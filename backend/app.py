import glob
import json
import os
import subprocess
import threading
import time
import uuid

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

from fastapi import (
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    File,
)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.stream_manager import StreamManager
from pipeline.video_processor import VideoProcessor
from alerts.mqtt_publisher import MQTTPublisher
from alerts.speaker_alert import SpeakerAlert
from health import get_system_health


Gst.init(None)

BASE_DIR = "/home/ksanju/ppe_system/deepstream_ppe_poc/backend"
UPLOAD_DIR = f"{BASE_DIR}/uploads"
OUTPUT_DIR = f"{BASE_DIR}/outputs"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

ALLOWED_VIDEO_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".webm",
}

app = FastAPI()

mqtt = MQTTPublisher(
    broker=os.getenv("MQTT_BROKER", "localhost"),
    topic=os.getenv("MQTT_TOPIC", "ppe/alerts"),
)

speaker = SpeakerAlert(
    cooldown_seconds=15
)

managers = {
    0: StreamManager(
        slot_id=0,
        mqtt=mqtt,
        speaker=speaker,
        base_dir=BASE_DIR,
    ),
    1: StreamManager(
        slot_id=1,
        mqtt=mqtt,
        speaker=speaker,
        base_dir=BASE_DIR,
    ),
}

for m in managers.values():
    os.makedirs(
        f"{BASE_DIR}/screenshots/slot{m.slot_id}",
        exist_ok=True,
    )

    os.makedirs(
        f"{BASE_DIR}/temp/gallery/slot{m.slot_id}",
        exist_ok=True,
    )


# ── Video upload job registry ──────────────────────────────────────────
# Live cameras and video-upload processing are mutually exclusive on this
# Jetson (shared decoder/encoder + GPU inference context) — starting one
# is blocked while the other is active.

video_jobs = {}
video_jobs_lock = threading.Lock()


def any_camera_running():
    return any(
        m.is_running()
        for m in managers.values()
    )


def any_job_running():
    with video_jobs_lock:
        return any(
            j.running
            for j in video_jobs.values()
        )


app.mount(
    "/screenshots",
    StaticFiles(
        directory=f"{BASE_DIR}/screenshots"
    ),
    name="screenshots",
)

app.mount(
    "/static",
    StaticFiles(
        directory=f"{BASE_DIR}/../frontend"
    ),
    name="static",
)


class StartRequest(BaseModel):
    device: str


def _run_glib_loop():
    loop = GLib.MainLoop()
    loop.run()


threading.Thread(
    target=_run_glib_loop,
    daemon=True,
).start()


@app.get("/")
def index():
    return FileResponse(
        f"{BASE_DIR}/../frontend/index.html"
    )


@app.get("/api/cameras")
def list_cameras():
    all_devices = sorted(
        glob.glob("/dev/video*")
    )

    def _is_capture(path):
        try:
            out = subprocess.check_output(
                [
                    "v4l2-ctl",
                    "-d",
                    path,
                    "--info",
                ],
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).decode()

            return "Video Capture" in out

        except Exception:
            return False

    devices = [
        d
        for d in all_devices
        if _is_capture(d)
    ]

    return {
        "cameras": devices
    }


# ── Video upload processing ────────────────────────────────────────────

@app.post("/api/upload")
async def upload_video(
    file: UploadFile = File(...)
):
    if any_camera_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "Stop live camera streams before uploading "
                "a video for processing."
            ),
        )

    original_name = os.path.basename(
        file.filename or "video.mp4"
    )

    extension = os.path.splitext(
        original_name
    )[1].lower()

    if extension not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported video format. "
                "Use MP4, AVI, MOV, MKV, or WEBM."
            ),
        )

    file_id = uuid.uuid4().hex

    saved_name = (
        f"{file_id}{extension}"
    )

    saved_path = os.path.join(
        UPLOAD_DIR,
        saved_name,
    )

    try:
        with open(saved_path, "wb") as output:
            while True:
                chunk = await file.read(
                    1024 * 1024
                )

                if not chunk:
                    break

                output.write(chunk)

    finally:
        await file.close()

    size_bytes = os.path.getsize(
        saved_path
    )

    job_id = file_id

    job = VideoProcessor(
        job_id=job_id,
        video_path=saved_path,
        mqtt=mqtt,
        speaker=speaker,
        base_dir=BASE_DIR,
    )

    with video_jobs_lock:
        video_jobs[job_id] = job

    try:
        job.start()

    except Exception as e:
        raise HTTPException(
            500,
            f"Failed to start processing: {e}",
        )

    return {
        "status": "processing",
        "job_id": job_id,
        "original_name": original_name,
        "saved_name": saved_name,
        "size_bytes": size_bytes,
    }


@app.websocket("/ws/process/{job_id}")
async def process_ws(
    websocket: WebSocket,
    job_id: str,
):
    await websocket.accept()

    job = video_jobs.get(job_id)

    if job is None:
        await websocket.send_text(
            json.dumps({
                "type": "error",
                "message": "Unknown job_id",
            })
        )

        await websocket.close()
        return

    try:
        while True:
            frame = job.get_latest_jpeg()

            if frame is not None:
                await websocket.send_bytes(
                    frame
                )

            stats = job.get_latest_stats()

            alerts = job.pop_new_alerts()

            progress = job.get_progress()

            await websocket.send_text(
                json.dumps({
                    "type": "stats",
                    "progress": progress,
                    "frame": job.frame_count,
                    "total": job.total_frames,
                    "persons": stats["persons"],
                    "violations": stats["violations"],
                    "fps": stats["fps"],
                    "alerts": alerts,
                })
            )

            if (
                job.done
                and progress >= 100
            ):
                break

            await asyncio_sleep(0.03)

    except WebSocketDisconnect:
        pass


# ── Annotated video download ───────────────────────────────────────────

@app.get("/api/download/{job_id}")
def download_annotated_video(
    job_id: str
):
    job = video_jobs.get(job_id)

    if job is None:
        raise HTTPException(
            404,
            "Unknown job_id",
        )

    output_path = (
        f"{OUTPUT_DIR}/"
        f"{job_id}_annotated.mp4"
    )

    if not job.done:
        raise HTTPException(
            409,
            (
                "Video is still processing. "
                "Please wait until it finishes."
            ),
        )

    if not os.path.isfile(output_path):
        raise HTTPException(
            404,
            (
                "Annotated video not found "
                "(processing may have failed)."
            ),
        )

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=(
            f"ppe_annotated_"
            f"{job_id[:8]}.mp4"
        ),
    )


# ── Live camera streams ────────────────────────────────────────────────

@app.post("/api/stream/{slot}/start")
def start_stream(
    slot: int,
    req: StartRequest,
):
    if slot not in managers:
        raise HTTPException(
            404,
            "Invalid slot",
        )

    if any_job_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "Stop video upload processing before "
                "starting a live camera."
            ),
        )

    try:
        managers[slot].start(
            req.device
        )

    except Exception as e:
        raise HTTPException(
            500,
            str(e),
        )

    return {
        "status": "started",
        "slot": slot,
        "device": req.device,
    }


@app.post("/api/stream/{slot}/stop")
def stop_stream(slot: int):
    if slot not in managers:
        raise HTTPException(
            404,
            "Invalid slot",
        )

    managers[slot].stop()

    return {
        "status": "stopped",
        "slot": slot,
    }


@app.get("/api/stream/{slot}/status")
def stream_status(slot: int):
    if slot not in managers:
        raise HTTPException(
            404,
            "Invalid slot",
        )

    m = managers[slot]

    return {
        "running": m.is_running(),
        "device": m.device,
    }


def _mjpeg_generator(slot: int):
    manager = managers[slot]

    while True:
        if not manager.is_running():
            time.sleep(0.1)
            continue

        frame = manager.get_latest_jpeg()

        if frame is None:
            time.sleep(0.02)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: "
            + str(len(frame)).encode()
            + b"\r\n"
            b"\r\n"
            + frame
            + b"\r\n"
        )

        time.sleep(0.03)


@app.get("/api/stream/{slot}/feed")
def stream_feed(slot: int):
    if slot not in managers:
        raise HTTPException(
            404,
            "Invalid slot",
        )

    return StreamingResponse(
        _mjpeg_generator(slot),
        media_type=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        ),
    )


@app.websocket("/ws/stream/{slot}")
async def stream_ws(
    websocket: WebSocket,
    slot: int,
):
    await websocket.accept()

    if slot not in managers:
        await websocket.send_text(
            json.dumps({
                "type": "error",
                "message": "Invalid slot",
            })
        )

        await websocket.close()
        return

    m = managers[slot]

    try:
        while True:
            if not m.is_running():
                await websocket.send_text(
                    json.dumps({
                        "type": "stats",
                        "persons": 0,
                        "violations": 0,
                        "fps": 0,
                        "alerts": [],
                    })
                )

                await asyncio_sleep(0.2)
                continue

            frame = m.get_latest_jpeg()

            if frame is not None:
                await websocket.send_bytes(
                    frame
                )

            stats = m.get_latest_stats()

            alerts = m.pop_new_alerts()

            await websocket.send_text(
                json.dumps({
                    "type": "stats",
                    **stats,
                    "alerts": alerts,
                })
            )

            await asyncio_sleep(0.03)

    except WebSocketDisconnect:
        pass


async def asyncio_sleep(seconds):
    import asyncio

    await asyncio.sleep(
        seconds
    )


# ── Aggregated (both cameras) REST endpoints ──────────────────────────

@app.get("/api/alerts")
def get_alerts():
    combined = []

    for slot, m in managers.items():
        for a in m.event_mgr.get_history():
            combined.append({
                **a,
                "camera": slot,
            })

    return sorted(
        combined,
        key=lambda x: x["timestamp"],
        reverse=True,
    )[:100]


@app.get("/api/stats")
def get_stats():
    total = {
        "persons": 0,
        "violations": 0,
        "total_alerts": 0,
        "fps": 0,
    }

    for m in managers.values():
        s = m.get_latest_stats()

        total["persons"] += (
            s["persons"]
        )

        total["violations"] += (
            s["violations"]
        )

        total["fps"] += (
            s["fps"]
        )

        total["total_alerts"] += len(
            m.event_mgr.history
        )

    return total


@app.get("/api/gallery")
def get_gallery():
    combined = []

    for slot, m in managers.items():
        for e in m.gallery.get_gallery():
            combined.append({
                **e,
                "camera": slot,
            })

    return sorted(
        combined,
        key=lambda x: x["timestamp"],
        reverse=True,
    )


@app.delete("/api/gallery")
def clear_gallery():
    for m in managers.values():
        m.gallery.clear()

    return {
        "status": "cleared"
    }


@app.get("/api/screenshots")
def get_screenshots():
    items = []

    # Live-camera screenshots
    for slot, m in managers.items():
        d = m.event_mgr.screenshot_dir

        if not os.path.isdir(d):
            continue

        for fname in sorted(
            os.listdir(d),
            reverse=True,
        ):
            if not fname.endswith(".jpg"):
                continue

            parts = (
                fname
                .replace(".jpg", "")
                .split("_", 2)
            )

            ts = (
                int(parts[0])
                if parts[0].isdigit()
                else 0
            )

            pid = (
                parts[1]
                if len(parts) > 1
                else "?"
            )

            vtype = (
                parts[2]
                if len(parts) > 2
                else "unknown"
            )

            items.append({
                "filename": fname,
                "url": (
                    f"/screenshots/"
                    f"slot{slot}/"
                    f"{fname}"
                ),
                "person_id": pid,
                "violation_type": vtype,
                "timestamp": ts,
                "camera": slot,
            })

    # Upload-processing screenshots
    with video_jobs_lock:
        job_ids = list(
            video_jobs.keys()
        )

    for job_id in job_ids:
        d = (
            f"{BASE_DIR}/screenshots/"
            f"upload_{job_id}"
        )

        if not os.path.isdir(d):
            continue

        for fname in sorted(
            os.listdir(d),
            reverse=True,
        ):
            if not fname.endswith(".jpg"):
                continue

            parts = (
                fname
                .replace(".jpg", "")
                .split("_", 2)
            )

            ts = (
                int(parts[0])
                if parts[0].isdigit()
                else 0
            )

            pid = (
                parts[1]
                if len(parts) > 1
                else "?"
            )

            vtype = (
                parts[2]
                if len(parts) > 2
                else "unknown"
            )

            items.append({
                "filename": fname,
                "url": (
                    f"/screenshots/"
                    f"upload_{job_id}/"
                    f"{fname}"
                ),
                "person_id": pid,
                "violation_type": vtype,
                "timestamp": ts,
                "camera": (
                    f"upload:"
                    f"{job_id[:8]}"
                ),
            })

    return sorted(
        items,
        key=lambda x: x["timestamp"],
        reverse=True,
    )


@app.delete("/api/screenshots")
def clear_screenshots():
    for slot, m in managers.items():
        d = m.event_mgr.screenshot_dir

        if os.path.isdir(d):
            for fname in os.listdir(d):
                if fname.endswith(".jpg"):
                    os.remove(
                        os.path.join(
                            d,
                            fname,
                        )
                    )

        m.event_mgr.history = []

    return {
        "deleted": True
    }


@app.get("/api/health")
def system_health():
    health = get_system_health()

    health["streams"] = {
        str(slot): {
            "running": manager.is_running(),
            "device": manager.device,
            "stats": manager.get_latest_stats(),
        }
        for slot, manager in managers.items()
    }

    health["active_streams"] = sum(
        1
        for manager in managers.values()
        if manager.is_running()
    )

    return health

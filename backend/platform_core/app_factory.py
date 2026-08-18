"""
platform_core/app_factory.py — Pro tier (DeepStream).

Same role as the Lite tier's app_factory.py: builds one fully
self-contained, single-solution FastAPI app. Each product
(apps/pro_ppe/main.py, apps/pro_driver/main.py, apps/pro_healthcare/main.py)
calls create_app(solution_class=...) and gets back a ready-to-run app.

Differences from the Lite tier factory, all driven by DeepStream's
execution model:
  - Gst.init(None) + a background GLib MainLoop are started once per
    process here (each product IS its own process, so no conflict).
  - Live camera and video upload are mutually exclusive on this
    hardware (shared decoder/encoder + GPU inference context) --
    any_camera_running()/any_job_running() guard this, preserved
    unchanged from the original app.py.
  - StreamManager/VideoProcessor own actual GStreamer pipelines, not
    Python capture threads -- stop() tears down the pipeline, not just
    joins a thread.
"""
import sys
import os as _os
_REPO_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import glob
import json
import os
import subprocess
import threading
import time
import uuid
import asyncio

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pipeline.stream_manager import StreamManager
from pipeline.video_processor import VideoProcessor
from framework.mqtt.mqtt_publisher import MQTTPublisher
from framework.alerts.speaker_alert import SpeakerAlert
from framework.database.data_manager import DataManager
from framework.device.health import get_system_health

Gst.init(None)

MAX_SLOTS = 2
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

_glib_loop_started = False


def _ensure_glib_loop():
    global _glib_loop_started
    if _glib_loop_started:
        return
    _glib_loop_started = True

    def _run():
        GLib.MainLoop().run()

    threading.Thread(target=_run, daemon=True).start()


def create_app(solution_class, base_dir, frontend_dir, app_title=None,
                mqtt_topic=None):
    """
    solution_class: a BaseSolution subclass, e.g. PPEIndustrialSolution
    base_dir: this product's own directory (screenshots/, temp/,
        uploads/, outputs/, data/ all live under here -- isolated per
        product, same principle as Lite tier's runtime_data/)
    frontend_dir: shared frontend/ folder (same manifest-driven UI as
        Lite tier, reused here unchanged since the route contract matches)
    """
    _ensure_glib_loop()

    solution_name = solution_class.name
    manifest = getattr(solution_class, "manifest", {})

    UPLOAD_DIR = os.path.join(base_dir, "uploads")
    OUTPUT_DIR = os.path.join(base_dir, "outputs")
    SCREENSHOTS_DIR = os.path.join(base_dir, "screenshots")
    DATA_DIR = os.path.join(base_dir, "data")

    for d in (UPLOAD_DIR, OUTPUT_DIR, SCREENSHOTS_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)

    DB_PATH = os.path.join(DATA_DIR, "platform.db")

    app = FastAPI(title=app_title or f"{solution_name} Pro")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    _data_manager = DataManager(db_path=DB_PATH)
    _mqtt = MQTTPublisher(
        broker=os.getenv("MQTT_BROKER", "localhost"),
        topic=mqtt_topic or f"{solution_name}/alerts",
        client_id=f"edge_ai_pro_{solution_name}",
    )
    _speaker = SpeakerAlert(cooldown_seconds=15)

    managers = {
        slot: StreamManager(
            slot_id=slot, solution=solution_class(),
            mqtt=_mqtt, speaker=_speaker, base_dir=base_dir,
        )
        for slot in range(MAX_SLOTS)
    }

    for m in managers.values():
        os.makedirs(f"{base_dir}/screenshots/{solution_name}/slot{m.slot_id}", exist_ok=True)
        os.makedirs(f"{base_dir}/temp/gallery/{solution_name}/slot{m.slot_id}", exist_ok=True)

    video_jobs = {}
    video_jobs_lock = threading.Lock()

    def any_camera_running():
        return any(m.is_running() for m in managers.values())

    def any_job_running():
        with video_jobs_lock:
            return any(j.running for j in video_jobs.values())

    class StartRequest(BaseModel):
        device: str

    # ── Cameras / manifest ───────────────────────────────────────────

    @app.get("/api/cameras")
    def list_cameras():
        all_devices = sorted(glob.glob("/dev/video*"))

        def _is_capture(path):
            try:
                out = subprocess.check_output(
                    ["v4l2-ctl", "-d", path, "--info"], stderr=subprocess.DEVNULL, timeout=2,
                ).decode()
                device_caps_section = out.split("Device Caps")[-1]
                return "Video Capture" in device_caps_section
            except Exception:
                return False

        return {"cameras": [d for d in all_devices if _is_capture(d)]}

    @app.get("/api/solutions")
    def get_solution_info():
        return {"available": [solution_name], "active": solution_name}

    @app.get("/api/solutions/manifest")
    def get_manifest():
        return {"active": solution_name, "manifest": manifest}

    # ── Video upload ──────────────────────────────────────────────────

    @app.post("/api/upload")
    async def upload_video(file: UploadFile = File(...)):
        if any_camera_running():
            raise HTTPException(409, "Stop live camera streams before uploading a video for processing.")

        original_name = os.path.basename(file.filename or "video.mp4")
        extension = os.path.splitext(original_name)[1].lower()
        if extension not in ALLOWED_VIDEO_EXTENSIONS:
            raise HTTPException(400, "Unsupported video format. Use MP4, AVI, MOV, MKV, or WEBM.")

        file_id = uuid.uuid4().hex
        saved_path = os.path.join(UPLOAD_DIR, f"{file_id}{extension}")

        try:
            with open(saved_path, "wb") as output:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        finally:
            await file.close()

        job_id = file_id
        job = VideoProcessor(
            job_id=job_id, video_path=saved_path, solution=solution_class(),
            mqtt=_mqtt, speaker=_speaker, base_dir=base_dir,
        )

        with video_jobs_lock:
            video_jobs[job_id] = job

        try:
            job.start()
        except Exception as e:
            raise HTTPException(500, f"Failed to start processing: {e}")

        return {"status": "processing", "job_id": job_id, "solution": solution_name}

    @app.websocket("/ws/process/{job_id}")
    async def process_ws(websocket: WebSocket, job_id: str):
        await websocket.accept()
        job = video_jobs.get(job_id)
        if job is None:
            await websocket.send_text(json.dumps({"type": "error", "message": "Unknown job_id"}))
            await websocket.close()
            return

        try:
            while True:
                frame = job.get_latest_jpeg()
                if frame is not None:
                    await websocket.send_bytes(frame)

                stats = job.get_latest_stats()
                alerts = job.pop_new_alerts()
                progress = job.get_progress()

                await websocket.send_text(json.dumps({
                    "type": "stats", "progress": progress,
                    "frame": job.frame_count, "total": job.total_frames,
                    "persons": stats["persons"], "violations": stats["violations"],
                    "fps": stats["fps"], "alerts": alerts,
                }))

                if job.done and progress >= 100:
                    break
                await asyncio.sleep(0.03)
        except WebSocketDisconnect:
            pass

    @app.get("/api/download/{job_id}")
    def download_annotated_video(job_id: str):
        job = video_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job_id")
        if not job.done:
            raise HTTPException(409, "Video is still processing. Please wait until it finishes.")
        if not os.path.isfile(job.output_path):
            raise HTTPException(404, "Annotated video not found (processing may have failed).")
        return FileResponse(job.output_path, media_type="video/mp4",
                             filename=f"annotated_{job_id[:8]}.mp4")

    # ── Live camera streams ───────────────────────────────────────────

    @app.post("/api/stream/{slot}/start")
    def start_stream(slot: int, req: StartRequest):
        if slot not in managers:
            raise HTTPException(404, "Invalid slot")
        if any_job_running():
            raise HTTPException(409, "Stop video upload processing before starting a live camera.")
        try:
            managers[slot].start(req.device)
        except Exception as e:
            raise HTTPException(500, str(e))
        return {"status": "started", "slot": slot, "device": req.device}

    @app.post("/api/stream/{slot}/stop")
    def stop_stream(slot: int):
        if slot not in managers:
            raise HTTPException(404, "Invalid slot")
        managers[slot].stop()
        return {"status": "stopped", "slot": slot}

    @app.get("/api/stream/{slot}/status")
    def stream_status(slot: int):
        if slot not in managers:
            raise HTTPException(404, "Invalid slot")
        m = managers[slot]
        return {"running": m.is_running(), "device": m.device}

    @app.websocket("/ws/stream/{slot}")
    async def stream_ws(websocket: WebSocket, slot: int):
        await websocket.accept()
        if slot not in managers:
            await websocket.send_text(json.dumps({"type": "error", "message": "Invalid slot"}))
            await websocket.close()
            return

        m = managers[slot]
        try:
            while True:
                if not m.is_running():
                    await websocket.send_text(json.dumps(
                        {"type": "stats", "persons": 0, "violations": 0, "fps": 0, "alerts": []}
                    ))
                    await asyncio.sleep(0.2)
                    continue

                frame = m.get_latest_jpeg()
                if frame is not None:
                    await websocket.send_bytes(frame)

                stats = m.get_latest_stats()
                alerts = m.pop_new_alerts()
                await websocket.send_text(json.dumps({"type": "stats", **stats, "alerts": alerts}))
                await asyncio.sleep(0.03)
        except WebSocketDisconnect:
            pass

    # ── Alerts / stats / health ───────────────────────────────────────

    @app.get("/api/alerts")
    def get_alerts():
        combined = []
        for slot, m in managers.items():
            for a in m.event_mgr.get_history():
                combined.append({**a, "camera": slot})
        return sorted(combined, key=lambda x: x["timestamp"], reverse=True)[:100]

    @app.get("/api/stats")
    def get_stats():
        total = {"persons": 0, "violations": 0, "total_alerts": 0, "fps": 0}
        for m in managers.values():
            s = m.get_latest_stats()
            total["persons"] += s["persons"]
            total["violations"] += s["violations"]
            total["fps"] += s["fps"]
            total["total_alerts"] += len(m.event_mgr.history)
        return total

    @app.get("/api/health")
    def system_health():
        health = get_system_health()
        health["streams"] = {
            str(slot): {"running": m.is_running(), "device": m.device, "stats": m.get_latest_stats()}
            for slot, m in managers.items()
        }
        health["active_streams"] = sum(1 for m in managers.values() if m.is_running())
        health["active_solution"] = solution_name
        health["tier"] = "pro"
        return health

    # ── Screenshots ───────────────────────────────────────────────────

    @app.get("/api/screenshots")
    def get_screenshots():
        items = []
        for slot, m in managers.items():
            d = m.event_mgr.screenshot_dir
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d), reverse=True):
                if not fname.endswith(".jpg"):
                    continue
                parts = fname.replace(".jpg", "").split("_", 2)
                ts = int(parts[0]) if parts[0].isdigit() else 0
                pid = parts[1] if len(parts) > 1 else "?"
                vtype = parts[2] if len(parts) > 2 else "unknown"
                items.append({
                    "filename": fname,
                    "url": f"/screenshots/{solution_name}/slot{slot}/{fname}",
                    "person_id": pid, "violation_type": vtype,
                    "timestamp": ts, "camera": slot,
                })

        with video_jobs_lock:
            job_ids = list(video_jobs.keys())

        for job_id in job_ids:
            d = f"{base_dir}/screenshots/{solution_name}/upload_{job_id}"
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d), reverse=True):
                if not fname.endswith(".jpg"):
                    continue
                parts = fname.replace(".jpg", "").split("_", 2)
                ts = int(parts[0]) if parts[0].isdigit() else 0
                pid = parts[1] if len(parts) > 1 else "?"
                vtype = parts[2] if len(parts) > 2 else "unknown"
                items.append({
                    "filename": fname,
                    "url": f"/screenshots/{solution_name}/upload_{job_id}/{fname}",
                    "person_id": pid, "violation_type": vtype,
                    "timestamp": ts, "camera": f"upload:{job_id[:8]}",
                })

        return sorted(items, key=lambda x: x["timestamp"], reverse=True)

    @app.delete("/api/screenshots")
    def clear_screenshots():
        for slot, m in managers.items():
            d = m.event_mgr.screenshot_dir
            if os.path.isdir(d):
                for fname in os.listdir(d):
                    if fname.endswith(".jpg"):
                        os.remove(os.path.join(d, fname))
            m.event_mgr.history = []
        return {"deleted": True}

    # ── Static frontend ───────────────────────────────────────────────

    app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))

    return app

"""
framework/api/routes.py — shared REST route definitions, extracted from
platform_core/app_factory.py. Routes are grouped into register_*
functions that each take (router, ctx) and attach their endpoints.
ctx bundles the per-product state (managers, solution_name, etc.) that
used to be captured via closures inside create_app().
"""
import glob
import subprocess
from fastapi import APIRouter


def register_camera_routes(router: APIRouter, ctx):
    @router.get("/api/cameras")
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

    @router.get("/api/solutions")
    def get_solution_info():
        return {"available": [ctx.solution_name], "active": ctx.solution_name}

    @router.get("/api/solutions/manifest")
    def get_manifest():
        return {"active": ctx.solution_name, "manifest": ctx.manifest}

def register_health_routes(router: APIRouter, ctx):
    from framework.device.health import get_system_health

    @router.get("/api/alerts")
    def get_alerts():
        combined = []
        for slot, m in ctx.managers.items():
            for a in m.event_mgr.get_history():
                combined.append({**a, "camera": slot})
        return sorted(combined, key=lambda x: x["timestamp"], reverse=True)[:100]

    @router.get("/api/stats")
    def get_stats():
        total = {"persons": 0, "violations": 0, "total_alerts": 0, "fps": 0}
        for m in ctx.managers.values():
            s = m.get_latest_stats()
            total["persons"] += s["persons"]
            total["violations"] += s["violations"]
            total["fps"] += s["fps"]
            total["total_alerts"] += len(m.event_mgr.history)
        return total

    @router.get("/api/health")
    def system_health():
        health = get_system_health()
        health["streams"] = {
            str(slot): {"running": m.is_running(), "device": m.device, "stats": m.get_latest_stats()}
            for slot, m in ctx.managers.items()
        }
        health["active_streams"] = sum(1 for m in ctx.managers.values() if m.is_running())
        health["active_solution"] = ctx.solution_name
        health["tier"] = "pro"
        return health

def register_screenshot_routes(router: APIRouter, ctx):
    import os

    @router.get("/api/screenshots")
    def get_screenshots():
        # Reads structured events from DataManager (populated by
        # StreamManager.log_event() at screenshot-save time) instead of
        # reverse-parsing filenames -- fixes the bug where a person_id
        # containing an underscore (e.g. "driver_seatbelt") broke the
        # old split("_", 2) parsing.
        if ctx.data_manager is None:
            return []

        rows = ctx.data_manager.get_recent_events(limit=200)
        items = []
        for row in rows:
            camera_slot = row["camera_slot"]
            fname = row["screenshot_path"]
            if not fname:
                continue

            if camera_slot.startswith("upload_"):
                url = f"/screenshots/{row['solution']}/{camera_slot}/{fname}"
                camera = f"upload:{camera_slot[len('upload_'):][:8]}"
            else:
                url = f"/screenshots/{row['solution']}/slot{camera_slot}/{fname}"
                camera = int(camera_slot) if camera_slot.isdigit() else camera_slot

            items.append({
                "filename": fname,
                "url": url,
                "person_id": row["person_id"],
                "violation_type": row["event_type"],
                "timestamp": row["timestamp"],
                "camera": camera,
            })

        return items

    @router.delete("/api/screenshots")
    def clear_screenshots():
        for slot, m in ctx.managers.items():
            d = m.event_mgr.screenshot_dir
            if os.path.isdir(d):
                for fname in os.listdir(d):
                    if fname.endswith(".jpg"):
                        os.remove(os.path.join(d, fname))
            m.event_mgr.history = []

        if ctx.data_manager is not None:
            ctx.data_manager.clear_screenshot_paths()

        return {"deleted": True}

def register_upload_routes(router: APIRouter, ctx):
    import os
    import json
    import uuid
    import asyncio
    from fastapi import HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
    from fastapi.responses import FileResponse
    from pipeline.video_processor import VideoProcessor
    from framework.configuration.config_loader import load_platform_config

    _platform_config = load_platform_config()
    ALLOWED_VIDEO_EXTENSIONS = set(_platform_config["platform"]["allowed_video_extensions"])


    @router.post("/api/upload")
    async def upload_video(file: UploadFile = File(...)):
        if ctx.any_camera_running():
            raise HTTPException(409, "Stop live camera streams before uploading a video for processing.")

        original_name = os.path.basename(file.filename or "video.mp4")
        extension = os.path.splitext(original_name)[1].lower()
        if extension not in ALLOWED_VIDEO_EXTENSIONS:
            raise HTTPException(400, "Unsupported video format. Use MP4, AVI, MOV, MKV, or WEBM.")

        file_id = uuid.uuid4().hex
        saved_path = os.path.join(ctx.UPLOAD_DIR, f"{file_id}{extension}")

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
            job_id=job_id, video_path=saved_path, solution=ctx.solution_class(),
            mqtt=ctx._mqtt, speaker=ctx._speaker, base_dir=ctx.base_dir, data_manager=ctx.data_manager,
        )

        with ctx.video_jobs_lock:
            ctx.video_jobs[job_id] = job

        try:
            job.start()
        except Exception as e:
            raise HTTPException(500, f"Failed to start processing: {e}")

        return {"status": "processing", "job_id": job_id, "solution": ctx.solution_name}

    @router.websocket("/ws/process/{job_id}")
    async def process_ws(websocket: WebSocket, job_id: str):
        await websocket.accept()
        job = ctx.video_jobs.get(job_id)
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

    @router.get("/api/download/{job_id}")
    def download_annotated_video(job_id: str):
        job = ctx.video_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown job_id")
        if not job.done:
            raise HTTPException(409, "Video is still processing. Please wait until it finishes.")
        if not os.path.isfile(job.output_path):
            raise HTTPException(404, "Annotated video not found (processing may have failed).")
        return FileResponse(job.output_path, media_type="video/mp4",
                             filename=f"annotated_{job_id[:8]}.mp4")

def register_stream_routes(router: APIRouter, ctx):
    import json
    import asyncio
    from fastapi import HTTPException, WebSocket, WebSocketDisconnect
    from pydantic import BaseModel

    class StartRequest(BaseModel):
        device: str
        source_type: str = "usb"  # "usb" or "rtsp" -- defaults to usb
                                   # for full backward compatibility with
                                   # every existing caller that only sends
                                   # "device" (see CameraFactory)
    @router.post("/api/stream/{slot}/start")
    def start_stream(slot: int, req: StartRequest):
        if slot not in ctx.managers:
            raise HTTPException(404, "Invalid slot")
        if ctx.any_job_running():
            raise HTTPException(409, "Stop video upload processing before starting a live camera.")
        if req.source_type not in ("usb", "rtsp"):
            raise HTTPException(400, f"Unknown source_type: {req.source_type!r}")
        try:
            ctx.managers[slot].start(req.device, source_type=req.source_type)
        except Exception as e:
            raise HTTPException(500, str(e))
        return {"status": "started", "slot": slot, "device": req.device, "source_type": req.source_type}

    @router.post("/api/stream/{slot}/stop")
    def stop_stream(slot: int):
        if slot not in ctx.managers:
            raise HTTPException(404, "Invalid slot")
        ctx.managers[slot].stop()
        return {"status": "stopped", "slot": slot}

    @router.get("/api/stream/{slot}/status")
    def stream_status(slot: int):
        if slot not in ctx.managers:
            raise HTTPException(404, "Invalid slot")
        m = ctx.managers[slot]
        return {"running": m.is_running(), "device": m.device}

    @router.websocket("/ws/stream/{slot}")
    async def stream_ws(websocket: WebSocket, slot: int):
        await websocket.accept()
        if slot not in ctx.managers:
            await websocket.send_text(json.dumps({"type": "error", "message": "Invalid slot"}))
            await websocket.close()
            return

        m = ctx.managers[slot]
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

def register_model_routes(router: APIRouter, ctx):
    from fastapi import HTTPException
    from pydantic import BaseModel
    from framework.model.model_manager import ModelManager, list_available_solutions

    class SolutionSwitchRequest(BaseModel):
        solution_name: str

    @router.get("/api/solutions/available")
    def get_available_solutions():
        return {"available": list_available_solutions()}

    @router.post("/api/camera/{slot}/solution")
    def switch_camera_solution(slot: int, req: SolutionSwitchRequest):
        if slot not in ctx.managers:
            raise HTTPException(404, "Invalid slot")
        try:
            solution_instance, _ = ModelManager.load(req.solution_name)
        except ValueError as e:
            raise HTTPException(400, str(e))

        ctx.managers[slot].swap_solution(solution_instance)

        return {
            "status": "switched",
            "slot": slot,
            "solution": req.solution_name,
            "running": ctx.managers[slot].is_running(),
        }

    @router.get("/api/camera/{slot}/manifest")
    def get_slot_manifest(slot: int):
        if slot not in ctx.managers:
            raise HTTPException(404, "Invalid slot")
        m = ctx.managers[slot]
        manifest = getattr(type(m.solution), "manifest", {})
        return {"slot": slot, "active": m.solution.name, "manifest": manifest}

    @router.get("/api/solutions/{name}/manifest")
    def get_solution_manifest_by_name(name: str):
        try:
            solution_instance, _ = ModelManager.load(name)
        except ValueError as e:
            raise HTTPException(404, str(e))
        manifest = getattr(type(solution_instance), "manifest", {})
        return {"name": name, "manifest": manifest}

def build_router(ctx) -> APIRouter:
    router = APIRouter()
    register_camera_routes(router, ctx)
    register_health_routes(router, ctx)
    register_screenshot_routes(router, ctx)
    register_upload_routes(router, ctx)
    register_stream_routes(router, ctx)
    register_model_routes(router, ctx)

    return router

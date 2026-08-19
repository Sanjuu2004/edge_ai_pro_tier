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

Route migration to framework/api/routes.py is in progress -- routes
are being moved out one group at a time. Currently migrated:
  - Cameras / manifest (/api/cameras, /api/solutions, /api/solutions/manifest)
  - Alerts / stats / health (/api/alerts, /api/stats, /api/health)
Still defined locally below: upload, live streams, screenshots,
static frontend.
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
from framework.api.routes import build_router
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

    # ── ctx: shared state passed to framework/api routes ──────────────
    # Replaces closures for routes that have been migrated to
    # framework/api/routes.py. As more route groups migrate, add
    # whatever they need onto ctx here.
    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.solution_name = solution_name
    ctx.manifest = manifest
    ctx.base_dir = base_dir
    ctx.solution_class = solution_class

    UPLOAD_DIR = os.path.join(base_dir, "uploads")
    OUTPUT_DIR = os.path.join(base_dir, "outputs")
    SCREENSHOTS_DIR = os.path.join(base_dir, "screenshots")
    DATA_DIR = os.path.join(base_dir, "data")

    for d in (UPLOAD_DIR, OUTPUT_DIR, SCREENSHOTS_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)
    ctx.UPLOAD_DIR = UPLOAD_DIR

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
    ctx._mqtt = _mqtt
    ctx._speaker = _speaker

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

    ctx.managers = managers

    video_jobs = {}
    video_jobs_lock = threading.Lock()
    ctx.video_jobs = video_jobs
    ctx.video_jobs_lock = video_jobs_lock

    def any_camera_running():
        return any(m.is_running() for m in managers.values())

    def any_job_running():
        with video_jobs_lock:
            return any(j.running for j in video_jobs.values())

    ctx.any_camera_running = any_camera_running
    ctx.any_job_running = any_job_running
    

    # ── Cameras / manifest / alerts / stats / health ──────────────────
    # Migrated to framework/api/routes.py — see build_router(ctx) below.
    app.include_router(build_router(ctx))


    # ── Static frontend ───────────────────────────────────────────────

    app.mount("/screenshots", StaticFiles(directory=SCREENSHOTS_DIR), name="screenshots")
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(frontend_dir, "index.html"))

    return app

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
        items = []
        for slot, m in ctx.managers.items():
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
                    "url": f"/screenshots/{ctx.solution_name}/slot{slot}/{fname}",
                    "person_id": pid, "violation_type": vtype,
                    "timestamp": ts, "camera": slot,
                })

        with ctx.video_jobs_lock:
            job_ids = list(ctx.video_jobs.keys())

        for job_id in job_ids:
            d = f"{ctx.base_dir}/screenshots/{ctx.solution_name}/upload_{job_id}"
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
                    "url": f"/screenshots/{ctx.solution_name}/upload_{job_id}/{fname}",
                    "person_id": pid, "violation_type": vtype,
                    "timestamp": ts, "camera": f"upload:{job_id[:8]}",
                })

        return sorted(items, key=lambda x: x["timestamp"], reverse=True)

    @router.delete("/api/screenshots")
    def clear_screenshots():
        for slot, m in ctx.managers.items():
            d = m.event_mgr.screenshot_dir
            if os.path.isdir(d):
                for fname in os.listdir(d):
                    if fname.endswith(".jpg"):
                        os.remove(os.path.join(d, fname))
            m.event_mgr.history = []
        return {"deleted": True}

def build_router(ctx) -> APIRouter:
    router = APIRouter()
    register_camera_routes(router, ctx)
    register_health_routes(router, ctx)
    register_screenshot_routes(router, ctx)
    return router

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


def build_router(ctx) -> APIRouter:
    router = APIRouter()
    register_camera_routes(router, ctx)
    return router

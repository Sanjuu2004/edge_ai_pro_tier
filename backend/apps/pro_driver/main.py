"""
apps/pro_driver/main.py — Pro tier (DeepStream), Driver Monitoring.
Run:
    uvicorn apps.pro_driver.main:app --host 0.0.0.0 --port 8104 --reload
"""
import os
from platform_core.app_factory import create_app
from applications.driver_monitoring.logic import DriverMonitoringSolution

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_DIR = os.path.join(BACKEND_DIR, "..", "framework", "dashboard")

app = create_app(
    solution_class=DriverMonitoringSolution,
    base_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_data"),
    frontend_dir=os.path.abspath(FRONTEND_DIR),
    app_title="Driver Monitoring (Pro)",
)

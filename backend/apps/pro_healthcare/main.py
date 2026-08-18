"""
apps/pro_healthcare/main.py — Pro tier (DeepStream), Healthcare Monitoring.
Run:
    uvicorn apps.pro_healthcare.main:app --host 0.0.0.0 --port 8105 --reload
"""
import os
from platform_core.app_factory import create_app
from solutions.healthcare_monitoring.logic import HealthcareMonitoringSolution

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_DIR = os.path.join(BACKEND_DIR, "..", "frontend")

app = create_app(
    solution_class=HealthcareMonitoringSolution,
    base_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_data"),
    frontend_dir=os.path.abspath(FRONTEND_DIR),
    app_title="Healthcare Monitoring (Pro)",
)

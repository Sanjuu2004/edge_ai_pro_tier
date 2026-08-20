"""
applications/healthcare/main.py — Pro tier (DeepStream), Healthcare Monitoring.
Standalone product, own port, own isolated base_dir.
Run (from repo root):
    uvicorn applications.healthcare.main:app --host 0.0.0.0 --port 8105 --reload
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from platform_core.app_factory import create_app
from applications.healthcare.logic import HealthcareMonitoringSolution

FRONTEND_DIR = os.path.join(REPO_ROOT, "framework", "dashboard")

# runtime_data intentionally stays at its original location -- data
# location is decoupled from code location so this restructure never
# risks moving or orphaning the real screenshots/DB/uploads.
BASE_DIR = os.path.join(REPO_ROOT, "backend", "apps", "pro_healthcare", "runtime_data")

app = create_app(
    solution_class=HealthcareMonitoringSolution,
    base_dir=BASE_DIR,
    frontend_dir=os.path.abspath(FRONTEND_DIR),
    app_title="Healthcare Monitoring (Pro)",
)

"""
applications/ppe_detection/main.py — Pro tier (DeepStream), PPE Industrial Safety.
Standalone product, own port, own isolated base_dir.
Run (from repo root):
    uvicorn applications.ppe_detection.main:app --host 0.0.0.0 --port 8103 --reload
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from platform_core.app_factory import create_app
from applications.ppe_detection.logic import PPEIndustrialSolution

FRONTEND_DIR = os.path.join(REPO_ROOT, "framework", "dashboard")

# runtime_data intentionally stays at its original location -- data
# location is decoupled from code location so this restructure never
# risks moving or orphaning the real screenshots/DB/uploads.
BASE_DIR = os.path.join(REPO_ROOT, "backend", "apps", "pro_ppe", "runtime_data")

app = create_app(
    solution_class=PPEIndustrialSolution,
    base_dir=BASE_DIR,
    frontend_dir=os.path.abspath(FRONTEND_DIR),
    app_title="PPE Industrial Safety (Pro)",
)

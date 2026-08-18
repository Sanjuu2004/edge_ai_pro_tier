"""
apps/pro_ppe/main.py — Pro tier (DeepStream), PPE Industrial Safety.
Standalone product, own port, own isolated base_dir.

Run:
    cd ~/ppe_system/deepstream_ppe_poc/backend
    uvicorn apps.pro_ppe.main:app --host 0.0.0.0 --port 8103 --reload
"""
import os
from platform_core.app_factory import create_app
from solutions.ppe_industrial.logic import PPEIndustrialSolution

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_DIR = os.path.join(BACKEND_DIR, "..", "frontend")

app = create_app(
    solution_class=PPEIndustrialSolution,
    base_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime_data"),
    frontend_dir=os.path.abspath(FRONTEND_DIR),
    app_title="PPE Industrial Safety (Pro)",
)

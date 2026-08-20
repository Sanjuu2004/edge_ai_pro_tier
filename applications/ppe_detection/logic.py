"""solutions/ppe_industrial/logic.py — Pro tier PPE, wrapping the
existing pipeline.ppe_logic.PPEAssociator / pipeline.smoother.TemporalSmoother
unchanged. No algorithm changes, only wrapped in the BaseSolution contract."""

import os
from framework.common.base_solution import BaseSolution
from pipeline.ppe_logic import PPEAssociator
from pipeline.smoother import TemporalSmoother

CONFIGS_DIR = "/home/ksanju/ppe_system/deepstream_ppe_poc/configs"


class PPEIndustrialSolution(BaseSolution):
    name = "ppe_industrial"
    requires_tracking = True
    class_id_to_name = {0: "helmet", 1: "person", 2: "vest"}
    pgie_config_path = f"{CONFIGS_DIR}/config_infer_primary_ppe.txt"

    manifest = {
        "icon": "🛡️",
        "name": "PPE Monitoring",
        "description": "Helmet & vest compliance detection",
        "sidebar_brand_html": '<span style="color:var(--gold-light);">PPE</span> Monitor',
        "model_badge": "YOLOv8 · TensorRT · DeepStream",
        "doc_title": "PPE Monitor",
        "violation_types": {
            "no_helmet_no_vest": {"label": "No Helmet + No Vest", "short_label": "NO HELMET+VEST", "icon": "🚫", "tone": "danger"},
            "no_helmet": {"label": "No Helmet", "short_label": "NO HELMET", "icon": "⛑️", "tone": "warn"},
            "no_vest": {"label": "No Vest", "short_label": "NO VEST", "icon": "🦺", "tone": "gold"},
        },
	"dashboard_labels": {
            "compliance_title": "PPE Compliance",
            "compliance_subtitle": "Current compliance estimate from active detections",
            "violations_detail": "Current PPE violations",
            "recent_subtitle": "Latest PPE events across all cameras",
            "empty_state_text": "New PPE events will appear here automatically.",
	},
    }

    def __init__(self):
        self.associator = PPEAssociator(iou_threshold=0.1)
        self.smoother = TemporalSmoother(buffer_size=7, min_violation_frames=40)

    def evaluate(self, tracked_persons: list, all_detections: list) -> dict:
        raw_results = self.associator.associate(tracked_persons, all_detections)

        merged_status = {}
        for p in tracked_persons:
            pid = p["track_id"]
            r = raw_results.get(pid)
            if r is None:
                continue
            merged_status[pid] = {**r, "occluded": p.get("occluded", False)}

        return self.smoother.update(merged_status)

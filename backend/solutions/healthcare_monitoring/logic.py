"""solutions/healthcare_monitoring/logic.py — Pro tier. Room occupancy,
same debounce pattern as the Lite tier's healthcare solution (standalone
copy, no cross-import)."""

import os
from solutions.base_solution import BaseSolution

BASE_DIR = "/home/ksanju/ppe_system/deepstream_ppe_poc/backend"


class OccupancyDebouncer:
    def __init__(self, max_occupancy, confirm_frames, clear_frames):
        self.max_occupancy = max_occupancy
        self.confirm_frames = confirm_frames
        self.clear_frames = clear_frames
        self._over_streak = 0
        self._under_streak = 0
        self._confirmed = False

    def update(self, current_count):
        if current_count > self.max_occupancy:
            self._over_streak += 1
            self._under_streak = 0
        else:
            self._under_streak += 1
            self._over_streak = 0
        if self._over_streak >= self.confirm_frames:
            self._confirmed = True
        if self._under_streak >= self.clear_frames:
            self._confirmed = False
        return self._confirmed


class HealthcareMonitoringSolution(BaseSolution):
    name = "healthcare_monitoring"
    requires_tracking = True
    class_id_to_name = {0: "person"}
    pgie_config_path = f"{BASE_DIR}/config/config_infer_primary_healthcare.txt"

    manifest = {
        "icon": "🏥",
        "name": "Healthcare Monitoring",
        "description": "Room occupancy detection (fall detection coming soon)",
        "sidebar_brand_html": '<span style="color:var(--gold-light);">Healthcare</span> Monitor',
        "model_badge": "YOLOv8 · TensorRT · DeepStream",
        "doc_title": "Healthcare Monitor",
        "violation_types": {
            "overcrowding": {"label": "Overcrowding", "short_label": "OVERCROWDED", "icon": "🚪", "tone": "warn"},
        },
        "dashboard_labels": {
            "compliance_title": "Room Compliance",
            "compliance_subtitle": "Current occupancy compliance estimate from active detections",
            "violations_detail": "Current occupancy alerts",
            "recent_subtitle": "Latest occupancy events across all cameras",
            "empty_state_text": "New occupancy events will appear here automatically.",
        },
    }

    def __init__(self, max_occupancy=1):
        self.occupancy = OccupancyDebouncer(max_occupancy=max_occupancy, confirm_frames=30, clear_frames=10)

    def evaluate(self, tracked_persons: list, all_detections: list) -> dict:
        smoothed = {}
        for person in tracked_persons:
            pid = person["track_id"]
            smoothed[pid] = {
                "track_id": pid, "bbox": person["bbox"],
                "violation": False, "violation_type": None,
                "occluded": person.get("occluded", False),
            }

        overcrowded = self.occupancy.update(len(tracked_persons))
        if overcrowded:
            if tracked_persons:
                x1 = min(p["bbox"][0] for p in tracked_persons)
                y1 = min(p["bbox"][1] for p in tracked_persons)
                x2 = max(p["bbox"][2] for p in tracked_persons)
                y2 = max(p["bbox"][3] for p in tracked_persons)
                room_bbox = [x1, y1, x2, y2]
            else:
                room_bbox = [0, 0, 100, 100]
            smoothed["room_occupancy"] = {
                "track_id": "room_occupancy", "bbox": room_bbox,
                "violation": True, "violation_type": "overcrowding",
                "occluded": False,
            }
        return smoothed

    def get_stats(self, smoothed: dict) -> dict:
        real_persons = {k: v for k, v in smoothed.items() if k != "room_occupancy"}
        violations = sum(1 for s in smoothed.values() if s.get("violation"))
        return {"persons": len(real_persons), "violations": violations}

"""solutions/driver_monitoring/logic.py — Pro tier. Same PERCLOS-based
drowsiness + presence-debounce logic as the Lite tier's driver_monitoring
solution (this is a separate, standalone copy for this project — not
imported from ppe_platform_lite)."""

import os
from collections import deque
from framework.common.base_solution import BaseSolution

BASE_DIR = "/home/ksanju/ppe_system/deepstream_ppe_poc/backend"


def _best_of(detections, cls_name):
    matches = [d for d in detections if d["class"] == cls_name]
    if not matches:
        return None
    return max(matches, key=lambda d: d["conf"])


class PerclosTracker:
    def __init__(self, window_frames=45, closed_ratio_threshold=0.7, min_observed_frames=20):
        self.window_frames = window_frames
        self.closed_ratio_threshold = closed_ratio_threshold
        self.min_observed_frames = min_observed_frames
        self._buf = deque(maxlen=window_frames)

    def update(self, open_eye_det, closed_eye_det):
        if closed_eye_det is None and open_eye_det is None:
            return None
        if closed_eye_det is not None and (open_eye_det is None or closed_eye_det["conf"] >= open_eye_det["conf"]):
            self._buf.append(1)
            latest_bbox = closed_eye_det["bbox"]
        else:
            self._buf.append(0)
            latest_bbox = open_eye_det["bbox"]
        if len(self._buf) < self.min_observed_frames:
            return {"drowsy": False, "bbox": latest_bbox}
        ratio = sum(self._buf) / len(self._buf)
        return {"drowsy": ratio >= self.closed_ratio_threshold, "bbox": latest_bbox}


class PresenceDebouncer:
    def __init__(self, confirm_frames, clear_frames, invert=False):
        self.confirm_frames = confirm_frames
        self.clear_frames = clear_frames
        self.invert = invert
        self._present_streak = 0
        self._absent_streak = 0
        self._confirmed = False
        self._last_bbox = None

    def update(self, detection):
        if detection is not None:
            self._present_streak += 1
            self._absent_streak = 0
            self._last_bbox = detection["bbox"]
        else:
            self._absent_streak += 1
            self._present_streak = 0

        if not self.invert:
            if self._present_streak >= self.confirm_frames:
                self._confirmed = True
            if self._absent_streak >= self.clear_frames:
                self._confirmed = False
        else:
            if self._absent_streak >= self.confirm_frames:
                self._confirmed = True
            if self._present_streak >= self.clear_frames:
                self._confirmed = False

        return {"confirmed": self._confirmed, "bbox": self._last_bbox}


class DriverMonitoringSolution(BaseSolution):
    name = "driver_monitoring"
    requires_tracking = False
    class_id_to_name = {0: "Open Eye", 1: "Closed Eye", 2: "Cigarette", 3: "Phone", 4: "Seatbelt"}
    pgie_config_path = f"{BASE_DIR}/config/config_infer_primary_driver.txt"

    manifest = {
        "icon": "🚗",
        "name": "Driver Monitoring",
        "description": "Drowsiness, phone, smoking & seatbelt detection",
        "sidebar_brand_html": '<span style="color:var(--gold-light);">Driver</span> Monitor',
        "model_badge": "YOLOv8 · TensorRT · DeepStream",
        "doc_title": "Driver Monitor",
        "violation_types": {
            "drowsy": {"label": "Drowsy", "short_label": "DROWSY", "icon": "😴", "tone": "danger"},
            "phone_usage": {"label": "Phone Usage", "short_label": "PHONE", "icon": "📱", "tone": "warn"},
            "smoking": {"label": "Smoking", "short_label": "SMOKING", "icon": "🚬", "tone": "warn"},
            "no_seatbelt": {"label": "No Seatbelt", "short_label": "NO BELT", "icon": "🔒", "tone": "gold"},
        },
        "dashboard_labels": {
            "compliance_title": "Safety Compliance",
            "compliance_subtitle": "Current driver safety estimate from active detections",
            "violations_detail": "Current safety violations",
            "recent_subtitle": "Latest safety events across all cameras",
            "empty_state_text": "New safety events will appear here automatically.",
        },
    }

    def __init__(self):
        self.perclos = PerclosTracker()
        self.phone = PresenceDebouncer(confirm_frames=15, clear_frames=5)
        self.cigarette = PresenceDebouncer(confirm_frames=15, clear_frames=5)
        self.seatbelt = PresenceDebouncer(confirm_frames=45, clear_frames=5, invert=True)
        self._frames_since_face_seen = 999999
        self._face_recency_window = 45
        self._fallback_bbox = [0, 0, 100, 100]

    def evaluate(self, tracked_persons: list, all_detections: list) -> dict:
        open_eye = _best_of(all_detections, "Open Eye")
        closed_eye = _best_of(all_detections, "Closed Eye")
        phone_det = _best_of(all_detections, "Phone")
        cigarette_det = _best_of(all_detections, "Cigarette")
        seatbelt_det = _best_of(all_detections, "Seatbelt")

        perclos_result = self.perclos.update(open_eye, closed_eye)
        phone_result = self.phone.update(phone_det)
        cigarette_result = self.cigarette.update(cigarette_det)

        if open_eye is not None or closed_eye is not None:
            self._frames_since_face_seen = 0
        else:
            self._frames_since_face_seen += 1
        face_recently_seen = self._frames_since_face_seen < self._face_recency_window

        if seatbelt_det is not None or face_recently_seen:
            seatbelt_result = self.seatbelt.update(seatbelt_det)
        else:
            seatbelt_result = {"confirmed": self.seatbelt._confirmed, "bbox": self.seatbelt._last_bbox}

        smoothed = {}
        if perclos_result is not None:
            smoothed["driver_eyes"] = {
                "track_id": "driver_eyes", "bbox": perclos_result["bbox"],
                "violation": perclos_result["drowsy"],
                "violation_type": "drowsy" if perclos_result["drowsy"] else None,
                "occluded": False,
            }
        if phone_result["bbox"] is not None or phone_result["confirmed"]:
            smoothed["driver_phone"] = {
                "track_id": "driver_phone", "bbox": phone_result["bbox"] or self._fallback_bbox,
                "violation": phone_result["confirmed"],
                "violation_type": "phone_usage" if phone_result["confirmed"] else None,
                "occluded": False,
            }
        if cigarette_result["bbox"] is not None or cigarette_result["confirmed"]:
            smoothed["driver_cigarette"] = {
                "track_id": "driver_cigarette", "bbox": cigarette_result["bbox"] or self._fallback_bbox,
                "violation": cigarette_result["confirmed"],
                "violation_type": "smoking" if cigarette_result["confirmed"] else None,
                "occluded": False,
            }
        if seatbelt_result["bbox"] is not None or seatbelt_result["confirmed"]:
            smoothed["driver_seatbelt"] = {
                "track_id": "driver_seatbelt", "bbox": seatbelt_result["bbox"] or self._fallback_bbox,
                "violation": seatbelt_result["confirmed"],
                "violation_type": "no_seatbelt" if seatbelt_result["confirmed"] else None,
                "occluded": False,
            }
        return smoothed

    def get_stats(self, smoothed: dict) -> dict:
        violations = sum(1 for s in smoothed.values() if s.get("violation"))
        return {"persons": 1, "violations": violations}

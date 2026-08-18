import time
import os
from collections import defaultdict

class EventManager:
    """
    One instance per camera slot - cooldowns/alerted_types are isolated
    per camera automatically. Screenshot saving is deferred (see
    save_screenshot) since the annotated JPEG isn't available until
    one pipeline stage later than where violations are computed.
    """
    def __init__(self, screenshot_dir, cooldown_seconds=30):
        self.cooldown = cooldown_seconds
        self.screenshot_dir = screenshot_dir
        os.makedirs(screenshot_dir, exist_ok=True)

        self.last_alert    = defaultdict(float)
        self.alerted_types = defaultdict(set)
        self.history        = []

    def process(self, smoothed_status: dict) -> list:
        alerts = []
        now = time.time()

        for pid, status in smoothed_status.items():
            if not status.get("violation", False):
                self.alerted_types[pid].clear()
                self.last_alert.pop(pid, None)
                continue

            vtype = status.get("violation_type")
            if not vtype:
                continue

            if vtype in self.alerted_types[pid]:
                continue

            if self.last_alert[pid] != 0:
                if now - self.last_alert[pid] < self.cooldown:
                    if not (
                        vtype == "no_helmet_no_vest"
                        and "no_helmet_no_vest" not in self.alerted_types[pid]
                    ):
                        continue

            self.last_alert[pid] = now
            self.alerted_types[pid].add(vtype)

            alert = {
                "person_id":      pid,
                "violation_type": vtype,
                "timestamp":      now,
                "bbox":           status["bbox"],
                "helmet_frames":  status.get("helmet_frames", 0),
                "vest_frames":    status.get("vest_frames", 0),
                "screenshot":     None,
            }

            alerts.append(alert)
            self.history.append(alert)

        return alerts

    def save_screenshot(self, alert: dict, jpeg_bytes: bytes):
        """Called shortly after process() once the annotated JPEG for
        this frame is available. Mutates alert['screenshot'] in place -
        since alert is the same object stored in self.history, the
        history entry updates automatically."""
        if jpeg_bytes is None:
            return
        fname = f"{int(alert['timestamp'])}_{alert['person_id']}_{alert['violation_type']}.jpg"
        fpath = os.path.join(self.screenshot_dir, fname)
        with open(fpath, "wb") as f:
            f.write(jpeg_bytes)
        alert["screenshot"] = fname

    def get_history(self):
        return sorted(self.history, key=lambda x: x["timestamp"], reverse=True)[:100]

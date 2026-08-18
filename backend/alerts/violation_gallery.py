import os
import base64
from collections import defaultdict

class ViolationGallery:
    """One instance per camera slot."""
    def __init__(self, save_dir, max_per_person=5):
        self.save_dir       = save_dir
        self.max_per_person = max_per_person
        self.gallery         = defaultdict(list)
        os.makedirs(save_dir, exist_ok=True)

    def capture(self, alert: dict, jpeg_bytes: bytes):
        if jpeg_bytes is None:
            return

        pid   = alert["person_id"]
        vtype = alert["violation_type"]
        ts    = int(alert["timestamp"])

        fname = f"{self.save_dir}/pid{pid}_{vtype}_{ts}.jpg"
        with open(fname, "wb") as f:
            f.write(jpeg_bytes)

        entry = {
            "person_id":      pid,
            "violation_type": vtype,
            "timestamp":      alert["timestamp"],
            "image_path":     fname,
        }
        self.gallery[pid].append(entry)

        if len(self.gallery[pid]) > self.max_per_person:
            old = self.gallery[pid].pop(0)
            if os.path.exists(old["image_path"]):
                os.remove(old["image_path"])

    def get_gallery(self):
        result = []
        for pid, entries in self.gallery.items():
            for e in entries:
                entry = dict(e)
                if os.path.exists(e["image_path"]):
                    with open(e["image_path"], "rb") as f:
                        entry["image_b64"] = base64.b64encode(f.read()).decode()
                else:
                    entry["image_b64"] = None
                result.append(entry)
        return sorted(result, key=lambda x: x["timestamp"], reverse=True)

    def clear(self):
        for pid, entries in self.gallery.items():
            for e in entries:
                if os.path.exists(e["image_path"]):
                    os.remove(e["image_path"])
        self.gallery.clear()

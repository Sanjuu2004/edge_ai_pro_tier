import threading
import subprocess
import time
import os
from collections import defaultdict

class SpeakerAlert:
    """
    Shared across all camera slots (one physical speaker).
    Cooldown key includes the camera slot so a recent alert on one
    camera can never suppress a genuine new alert on another camera.
    """
    def __init__(self, cooldown_seconds=15):
        self.cooldown  = cooldown_seconds
        self.last_play = defaultdict(float)
        self._lock     = threading.Lock()

        result = subprocess.run(["which", "espeak"], capture_output=True)
        self.has_espeak = result.returncode == 0

        if not self.has_espeak:
            print("[Speaker] espeak not found — installing...")
            os.system("sudo apt install espeak -y")
            self.has_espeak = True

        print("[Speaker] Audio alert ready")

    def alert(self, slot_id: int, person_id: int, violation_type: str):
        key = f"{slot_id}_{person_id}_{violation_type}"
        now = time.time()

        with self._lock:
            if now - self.last_play[key] < self.cooldown:
                return
            self.last_play[key] = now

        threading.Thread(
            target=self._play,
            args=(slot_id, person_id, violation_type),
            daemon=True
        ).start()

    def _play(self, slot_id: int, person_id: int, violation_type: str):
        messages = {
            "no_helmet":         f"Warning! Camera {slot_id}, person {person_id} is not wearing a helmet!",
            "no_vest":           f"Warning! Camera {slot_id}, person {person_id} is not wearing a safety vest!",
            "no_helmet_no_vest": f"Alert! Camera {slot_id}, person {person_id} has no helmet and no vest!",
        }
        msg = messages.get(violation_type, f"PPE violation detected on camera {slot_id}, person {person_id}")

        try:
            subprocess.run(
                ["espeak", "-v", "en", "-s", "140", "-a", "200", msg],
                timeout=10,
                capture_output=True
            )
        except Exception as e:
            print(f"[Speaker] Error: {e}")

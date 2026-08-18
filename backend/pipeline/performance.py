import threading
import time
import psutil
import subprocess


class SystemPerformance:

    def __init__(self):

        self.cpu = 0
        self.ram = 0
        self.gpu = 0
        self.temp = 0

        self.running = False

    def start(self):

        self.running = True

        threading.Thread(
            target=self._loop,
            daemon=True
        ).start()

    def stop(self):

        self.running = False

    def _loop(self):

        while self.running:

            self.cpu = psutil.cpu_percent()

            self.ram = psutil.virtual_memory().percent

            try:

                out = subprocess.check_output(
                    ["tegrastats", "--interval", "1000"],
                    stderr=subprocess.DEVNULL,
                    timeout=1
                ).decode()

                if "GR3D_FREQ" in out:

                    idx = out.find("GR3D_FREQ")

                    self.gpu = out[idx:].split("%")[0].split()[-1]

                if "CPU@" in out:

                    self.temp = out.split("CPU@")[1].split("C")[0]

            except Exception:

                pass

            time.sleep(1)

    def get(self):

        return {

            "cpu": self.cpu,

            "ram": self.ram,

            "gpu": self.gpu,

            "temp": self.temp

        }

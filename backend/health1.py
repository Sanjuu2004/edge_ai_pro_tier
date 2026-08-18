import os
import re
import time
import shutil
import subprocess
from typing import Dict, Any

import psutil


START_TIME = time.time()


def _read_text(path: str):
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _read_temperature(path: str):
    value = _read_text(path)

    if value is None:
        return None

    try:
        temperature = float(value)

        # Jetson thermal zones normally expose millidegrees Celsius.
        if temperature > 1000:
            temperature /= 1000.0

        return round(temperature, 1)

    except (TypeError, ValueError):
        return None


def get_temperatures() -> Dict[str, float]:
    temperatures = {}

    thermal_root = "/sys/class/thermal"

    if not os.path.isdir(thermal_root):
        return temperatures

    try:
        zones = sorted(
            name
            for name in os.listdir(thermal_root)
            if name.startswith("thermal_zone")
        )

        for zone in zones:
            zone_path = os.path.join(thermal_root, zone)

            sensor_type = _read_text(
                os.path.join(zone_path, "type")
            )

            temperature = _read_temperature(
                os.path.join(zone_path, "temp")
            )

            if sensor_type and temperature is not None:
                temperatures[sensor_type] = temperature

    except Exception:
        pass

    return temperatures


def get_primary_temperature(temperatures: Dict[str, float]):
    preferred_sensors = [
        "GPU-therm",
        "CPU-therm",
        "SOC0-therm",
        "SOC1-therm",
        "Tboard_tegra",
        "tj-therm",
    ]

    for sensor in preferred_sensors:
        if sensor in temperatures:
            return temperatures[sensor]

    if temperatures:
        return max(temperatures.values())

    return None


def get_gpu_usage():
    """
    Read one tegrastats sample.

    Returns GPU utilization as a percentage or None.
    """

    tegrastats = shutil.which("tegrastats")

    if tegrastats is None:
        return None

    process = None

    try:
        process = subprocess.Popen(
            [tegrastats, "--interval", "1000"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        line = process.stdout.readline().strip()

        match = re.search(r"GR3D_FREQ\s+(\d+)%", line)

        if match:
            return float(match.group(1))

    except Exception:
        pass

    finally:
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=1)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    return None


def get_system_health() -> Dict[str, Any]:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    temperatures = get_temperatures()
    primary_temperature = get_primary_temperature(temperatures)

    cpu_percent = psutil.cpu_percent(interval=None)
    gpu_percent = get_gpu_usage()

    uptime_seconds = int(time.time() - START_TIME)

    return {
        "status": "healthy",

        "cpu": {
            "percent": round(cpu_percent, 1),
            "cores": psutil.cpu_count(logical=True),
        },

        "gpu": {
            "percent": gpu_percent,
        },

        "memory": {
            "percent": round(memory.percent, 1),
            "used_gb": round(memory.used / (1024 ** 3), 2),
            "total_gb": round(memory.total / (1024 ** 3), 2),
        },

        "disk": {
            "percent": round(disk.percent, 1),
            "used_gb": round(disk.used / (1024 ** 3), 2),
            "total_gb": round(disk.total / (1024 ** 3), 2),
        },

        "temperature": {
            "primary_c": primary_temperature,
            "sensors": temperatures,
        },

        "uptime_seconds": uptime_seconds,
        "timestamp": time.time(),
    }

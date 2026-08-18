import os
import time
from pathlib import Path

import psutil


def _read_temperature():
    thermal_paths = [
        "/sys/devices/virtual/thermal/thermal_zone0/temp",
        "/sys/class/thermal/thermal_zone0/temp",
    ]

    for path in thermal_paths:
        try:
            value = Path(path).read_text().strip()
            temperature = float(value)

            if temperature > 1000:
                temperature /= 1000.0

            return round(temperature, 1)
        except Exception:
            continue

    return None


def _read_gpu_usage():
    """
    Jetson GPU utilization from sysfs.
    Different JetPack/L4T versions may expose different paths.
    """

    paths = [
        "/sys/devices/gpu.0/load",
        "/sys/devices/17000000.ga10b/load",
    ]

    for path in paths:
        try:
            raw = float(Path(path).read_text().strip())

            # Jetson GPU load is commonly reported from 0–1000.
            if raw > 100:
                raw /= 10.0

            return round(raw, 1)
        except Exception:
            continue

    return None


def _memory_info():
    memory = psutil.virtual_memory()

    return {
        "total_gb": round(memory.total / (1024 ** 3), 2),
        "used_gb": round(memory.used / (1024 ** 3), 2),
        "available_gb": round(memory.available / (1024 ** 3), 2),
        "percent": round(memory.percent, 1),
    }


def _disk_info():
    disk = psutil.disk_usage("/")

    return {
        "total_gb": round(disk.total / (1024 ** 3), 2),
        "used_gb": round(disk.used / (1024 ** 3), 2),
        "free_gb": round(disk.free / (1024 ** 3), 2),
        "percent": round(disk.percent, 1),
    }


def get_system_health():
    return {
        "timestamp": time.time(),
        "cpu_percent": round(
            psutil.cpu_percent(interval=None),
            1,
        ),
        "cpu_count": psutil.cpu_count(),
        "memory": _memory_info(),
        "disk": _disk_info(),
        "temperature_c": _read_temperature(),
        "gpu_percent": _read_gpu_usage(),
        "uptime_seconds": int(
            time.time() - psutil.boot_time()
        ),
    }

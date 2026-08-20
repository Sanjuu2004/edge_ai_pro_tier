"""
framework/configuration/config_loader.py — YAML-backed configuration
for the Pro tier. Replaces hardcoded Python constants (MAX_SLOTS,
MQTT broker, per-solution ports/titles, etc.) with values loaded from
configs/platform.yaml and configs/solutions/<name>.yaml.

Defensive by design: a missing file or missing key falls back to a
documented default rather than crashing, so a partial or malformed
YAML edit never takes a whole product down.
"""
import os
import yaml

CONFIGS_DIR = "/home/ksanju/ppe_system/deepstream_ppe_poc/configs"

_PLATFORM_DEFAULTS = {
    "platform": {
        "max_camera_slots": 2,
        "allowed_video_extensions": [".mp4", ".avi", ".mov", ".mkv", ".webm"],
    },
    "mqtt": {
        "broker": "localhost",
        "client_id_prefix": "edge_ai_pro",
    },
    "speaker": {
        "cooldown_seconds": 15,
    },
}


def _load_yaml(path, defaults):
    """Load a YAML file, falling back to defaults if missing/invalid."""
    if not os.path.isfile(path):
        return dict(defaults)
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        return dict(defaults)
    merged = dict(defaults)
    merged.update(data)
    return merged


def load_platform_config():
    """Returns the platform-wide config dict (mqtt, speaker, platform.*)."""
    path = os.path.join(CONFIGS_DIR, "platform.yaml")
    return _load_yaml(path, _PLATFORM_DEFAULTS)


def load_solution_config(solution_name, defaults=None):
    """Returns the per-solution config dict for the given solution name."""
    path = os.path.join(CONFIGS_DIR, "solutions", f"{solution_name}.yaml")
    return _load_yaml(path, defaults or {})

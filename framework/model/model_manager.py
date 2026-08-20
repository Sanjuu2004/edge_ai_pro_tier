"""
framework/model/model_manager.py — registry and loader for business
Solution classes (PPE, Driver, Healthcare, ...). Framework code has
zero hardcoded knowledge of any specific solution: which class backs
"driver_monitoring" is entirely data-driven from
configs/solutions/<name>.yaml (module + class keys), loaded here via
importlib. This is what makes ModelManager.load("driver_monitoring")
usable without framework/ ever importing applications/*.

Deliberately read-only / stateless in this first version: it resolves
a solution name to a fresh Solution instance + its pgie_config_path.
It does NOT yet touch any running GStreamer pipeline — that's a
separate, higher-risk step (StreamManager.swap_solution) once this
registry layer is verified in isolation.
"""
import glob
import importlib
import os

from framework.configuration.config_loader import load_solution_config

CONFIGS_DIR = "/home/ksanju/ppe_system/deepstream_ppe_poc/configs"


def list_available_solutions():
    """Solution names available, derived from configs/solutions/*.yaml filenames."""
    pattern = os.path.join(CONFIGS_DIR, "solutions", "*.yaml")
    return sorted(
        os.path.splitext(os.path.basename(p))[0]
        for p in glob.glob(pattern)
    )


class ModelManager:
    @staticmethod
    def load(solution_name):
        """
        Resolve a solution name to a fresh (solution_instance, pgie_config_path).
        Raises ValueError if the name isn't registered, or if its YAML
        is missing 'module'/'class' (e.g. an old-style config from
        before this feature existed).
        """
        cfg = load_solution_config(solution_name)
        if not cfg or "module" not in cfg or "class" not in cfg:
            raise ValueError(
                f"Solution '{solution_name}' not found or missing "
                f"module/class in configs/solutions/{solution_name}.yaml"
            )

        module = importlib.import_module(cfg["module"])
        solution_class = getattr(module, cfg["class"])
        solution_instance = solution_class()

        return solution_instance, solution_instance.pgie_config_path

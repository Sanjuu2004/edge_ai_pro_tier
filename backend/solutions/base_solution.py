"""
solutions/base_solution.py — Pro tier (DeepStream). Own copy for this
project; not imported from ppe_platform_lite, by design, so the two
tiers stay fully independent.

Same evaluate()/manifest contract as the Lite tier's BaseSolution,
deliberately — proven pattern, and it lets pipeline/callbacks.py stay
completely generic regardless of which solution is plugged in.
"""
from abc import ABC, abstractmethod


class BaseSolution(ABC):
    name: str = None
    requires_tracking: bool = True

    #: {class_id: class_name} — maps nvinfer's raw detected class index
    #: to a name, e.g. {0: "helmet", 1: "person", 2: "vest"}
    class_id_to_name: dict = {}

    #: Absolute path to this solution's config_infer_primary_*.txt
    pgie_config_path: str = None

    #: Same shape as Lite tier's manifest (icon/name/description/
    #: violation_types/etc) — will drive the shared frontend once wired.
    manifest: dict = {}

    @abstractmethod
    def evaluate(self, tracked_persons: list, all_detections: list) -> dict:
        """Same contract as Lite tier: returns smoothed status dict,
        id -> {bbox, violation, violation_type, occluded}."""
        raise NotImplementedError

    def get_stats(self, smoothed: dict) -> dict:
        persons = len(smoothed)
        violations = sum(1 for s in smoothed.values() if s.get("violation"))
        return {"persons": persons, "violations": violations}

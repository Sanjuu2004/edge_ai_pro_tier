"""
pipeline/callbacks.py — Pro tier, now solution-agnostic.

make_osd_probe(solution, ...) replaces the old PPE-hardcoded version:
CLASS_ID_TO_NAME, the associator/smoother calls, and the violation
label/color lookup all now come from whichever `solution` (a
BaseSolution instance) is passed in, instead of being fixed to PPE.

Person-tracking is conditional on solution.requires_tracking, same
branch Lite tier already uses — Driver Monitoring has no person class
at all, so tracking is skipped entirely for it.
"""
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

import pyds
from .tracker import ByteTracker

TONE_COLORS = {
    "danger": (1.0, 0.0, 0.0, 1.0),
    "warn": (1.0, 0.65, 0.0, 1.0),
    "gold": (1.0, 0.84, 0.0, 1.0),
    "success": (0.0, 1.0, 0.0, 1.0),
}
COLOR_OK = TONE_COLORS["success"]
COLOR_NEUTRAL = (0.7, 0.5, 0.15, 1.0)
COLOR_HELMET = (0.0, 0.4, 1.0, 1.0)
COLOR_VEST = (1.0, 0.65, 0.0, 1.0)


def _rect_to_xyxy(rect):
    return [rect.left, rect.top, rect.left + rect.width, rect.top + rect.height]


def _iou(a, b):
    x1 = max(a[0], b[0]); y1 = max(a[1], b[1])
    x2 = min(a[2], b[2]); y2 = min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter == 0:
        return 0.0
    a1 = (a[2] - a[0]) * (a[3] - a[1])
    a2 = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (a1 + a2 - inter)


def _class_nms(entries, iou_threshold=0.4):
    by_class = {}
    for e in entries:
        by_class.setdefault(e["class"], []).append(e)
    kept, suppressed = [], []
    for cls, dets in by_class.items():
        dets = sorted(dets, key=lambda x: x["conf"], reverse=True)
        killed = [False] * len(dets)
        for i in range(len(dets)):
            if killed[i]:
                continue
            kept.append(dets[i])
            for j in range(i + 1, len(dets)):
                if killed[j]:
                    continue
                if _iou(dets[i]["bbox"], dets[j]["bbox"]) > iou_threshold:
                    killed[j] = True
        for i, d in enumerate(dets):
            if killed[i]:
                suppressed.append(d)
    return kept, suppressed


def _hide_obj(obj_meta):
    obj_meta.rect_params.border_width = 0
    obj_meta.rect_params.has_bg_color = 0
    obj_meta.text_params.display_text = ""


def _set_rect_color(obj_meta, color):
    obj_meta.rect_params.border_color.set(*color)
    obj_meta.rect_params.border_width = 3
    obj_meta.rect_params.has_bg_color = 0


def _set_label(obj_meta, text, color):
    obj_meta.text_params.display_text = text
    obj_meta.text_params.x_offset = int(obj_meta.rect_params.left)
    obj_meta.text_params.y_offset = max(0, int(obj_meta.rect_params.top) - 25)
    obj_meta.text_params.font_params.font_name = "Serif"
    obj_meta.text_params.font_params.font_size = 9
    obj_meta.text_params.font_params.font_color.set(1.0, 1.0, 1.0, 1.0)
    obj_meta.text_params.set_bg_clr = 1
    obj_meta.text_params.text_bg_clr.set(*color)


def _add_fps_overlay(batch_meta, frame_meta, label_text, frame_width):
    display_meta = pyds.nvds_acquire_display_meta_from_pool(batch_meta)
    display_meta.num_labels = 1
    txt = display_meta.text_params[0]
    txt.display_text = label_text
    txt.x_offset = max(0, frame_width - 190)
    txt.y_offset = 10
    txt.font_params.font_size = 14
    txt.font_params.font_name = "Serif"
    txt.font_params.font_color.set(0.0, 1.0, 0.0, 1.0)
    txt.set_bg_clr = 1
    txt.text_bg_clr.set(0.0, 0.0, 0.0, 1.0)
    pyds.nvds_add_display_meta_to_frame(frame_meta, display_meta)


def _best_iou_match(target_bbox, candidates, used_indices, min_iou=0.25):
    best_i = None
    best_iou = min_iou
    for i, c in enumerate(candidates):
        if i in used_indices:
            continue
        score = _iou(target_bbox, c["bbox"])
        if score > best_iou:
            best_iou = score
            best_i = i
    if best_i is None:
        return None, None
    return best_i, candidates[best_i]["obj_meta"]


def _color_and_label_for(violation_type, is_violation, manifest_violation_types):
    if not is_violation or not violation_type:
        return COLOR_OK, "OK"
    entry = (manifest_violation_types or {}).get(violation_type)
    if not entry:
        return COLOR_NEUTRAL, violation_type.replace("_", " ").upper()
    color = TONE_COLORS.get(entry.get("tone"), COLOR_NEUTRAL)
    label = entry.get("short_label") or violation_type.replace("_", " ").upper()
    return color, label


def make_osd_probe(slot_id, metrics, solution, frame_width=640, on_result=None):
    """
    solution: a BaseSolution instance (PPEIndustrialSolution,
    DriverMonitoringSolution, or HealthcareMonitoringSolution) --
    provides class_id_to_name, requires_tracking, evaluate(), and
    manifest["violation_types"] for coloring/labeling.

    Per-slot state: each independent camera pipeline gets its own
    tracker (only actually used if solution.requires_tracking).
    """
    tracker = ByteTracker(max_lost=90, min_iou=0.15) if solution.requires_tracking else None
    class_id_to_name = solution.class_id_to_name
    violation_types = solution.manifest.get("violation_types", {})

    def osd_sink_pad_buffer_probe(pad, info, u_data):
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            return Gst.PadProbeReturn.OK

        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))

        l_frame = batch_meta.frame_meta_list
        while l_frame:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break

            metrics.update(0)

            raw_entries = []
            l_obj = frame_meta.obj_meta_list
            while l_obj:
                try:
                    obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
                except StopIteration:
                    break

                cls_name = class_id_to_name.get(obj_meta.class_id)
                if cls_name is not None:
                    raw_entries.append({
                        "class": cls_name,
                        "bbox": _rect_to_xyxy(obj_meta.rect_params),
                        "conf": float(obj_meta.confidence),
                        "obj_meta": obj_meta,
                    })

                try:
                    l_obj = l_obj.next
                except StopIteration:
                    break

            kept, suppressed = _class_nms(raw_entries, iou_threshold=0.4)
            for e in suppressed:
                _hide_obj(e["obj_meta"])

            detections_for_tracker = [
                {"class": e["class"], "bbox": e["bbox"], "conf": e["conf"]}
                for e in kept
            ]

            if solution.requires_tracking:
                tracked_persons = tracker.update(detections_for_tracker, None)
                raw_person_entries = [e for e in kept if e["class"] == "person"]
                non_person_kept = [e for e in kept if e["class"] != "person"]
            else:
                tracked_persons = []
                raw_person_entries = []
                non_person_kept = kept  # everything gets drawn generically below

            # Draw non-person / non-tracked detections with generic
            # coloring (helmet/vest blue-orange kept for PPE visual
            # familiarity; anything else uses the neutral tone).
            for e in non_person_kept:
                if e["class"] == "helmet":
                    color = COLOR_HELMET
                elif e["class"] == "vest":
                    color = COLOR_VEST
                else:
                    color = COLOR_NEUTRAL
                _set_rect_color(e["obj_meta"], color)
                _set_label(e["obj_meta"], e["class"], color)

            smoothed = solution.evaluate(tracked_persons, detections_for_tracker)

            if solution.requires_tracking:
                used_indices = set()
                for p in tracked_persons:
                    pid = p["track_id"]
                    s = smoothed.get(pid)
                    if s is None:
                        continue

                    idx, obj_meta = _best_iou_match(p["bbox"], raw_person_entries, used_indices)
                    if obj_meta is None:
                        continue
                    used_indices.add(idx)

                    color, label = _color_and_label_for(
                        s.get("violation_type"), s.get("violation", False), violation_types
                    )
                    if not s.get("violation"):
                        label = f"person #{pid} | OK"
                    _set_rect_color(obj_meta, color)
                    _set_label(obj_meta, label, color)

                for i, e in enumerate(raw_person_entries):
                    if i not in used_indices:
                        _set_rect_color(e["obj_meta"], COLOR_OK)
                        _set_label(e["obj_meta"], "person", COLOR_OK)

            if on_result is not None:
                try:
                    on_result(smoothed)
                except Exception as e:
                    print(f"[callbacks] on_result error: {e}")

            fps_label = f"CAM {slot_id} | FPS {metrics.fps(0)}"
            _add_fps_overlay(batch_meta, frame_meta, fps_label, frame_width)

            try:
                l_frame = l_frame.next
            except StopIteration:
                break

        return Gst.PadProbeReturn.OK

    return osd_sink_pad_buffer_probe

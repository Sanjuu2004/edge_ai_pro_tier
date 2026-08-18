import numpy as np

class PPEAssociator:
    def __init__(self, iou_threshold=0.1):
        self.iou_threshold = iou_threshold

    def associate(self, tracked_persons, all_detections):
        helmets = [d for d in all_detections if d['class'] == 'helmet']
        vests   = [d for d in all_detections if d['class'] == 'vest']
        results = {}

        helmet_owner = self._assign_ppe(helmets, tracked_persons, 'helmet')
        vest_owner   = self._assign_ppe(vests,   tracked_persons, 'vest')

        for person in tracked_persons:
            pid         = person['track_id']
            helmet_worn = pid in helmet_owner
            vest_worn   = pid in vest_owner

            results[pid] = {
                'track_id':       pid,
                'bbox':           person['bbox'],
                'helmet':         helmet_worn,
                'vest':           vest_worn,
                'violation':      not helmet_worn or not vest_worn,
                'violation_type': self._get_violation_type(helmet_worn, vest_worn)
            }
        return results

    def _get_head_percent(self, p_h: float) -> float:
        """
        Dynamically compute what fraction of person bbox height
        is just the head — no shoulders.

        Human head is ~13% of standing body height.
        For partial/sitting bodies the bbox is smaller so
        the head occupies a larger fraction of the box.

        p_h (pixels)  → head_pct
        > 400px       → 0.13  (full body visible, strict)
        200 – 400px   → 0.17  (half body, moderate)
        100 – 200px   → 0.22  (sitting / crouching)
        < 100px       → 0.28  (very small / far away)
        """
        if p_h > 400:
            return 0.13
        elif p_h > 200:
            # linear interpolation 0.13 → 0.17
            t = (p_h - 200) / 200        # 0 at 200px, 1 at 400px
            return 0.17 - t * 0.04       # 0.17 at 200px, 0.13 at 400px
        elif p_h > 100:
            # linear interpolation 0.17 → 0.22
            t = (p_h - 100) / 100
            return 0.22 - t * 0.05
        else:
            return 0.28

    def _assign_ppe(self, items, persons, item_type):
        owners = set()
        if not items or not persons:
            return owners

        for item in items:
            ix1, iy1, ix2, iy2 = item['bbox']
            i_cx = (ix1 + ix2) / 2
            i_cy = (iy1 + iy2) / 2
            i_w  = ix2 - ix1
            i_h  = iy2 - iy1

            best_pid   = None
            best_score = -1

            for person in persons:
                pid  = person['track_id']
                px1, py1, px2, py2 = person['bbox']
                p_h  = py2 - py1
                p_w  = px2 - px1
                p_cx = (px1 + px2) / 2
                p_cy = (py1 + py2) / 2

                # ── Score 1: IoU with full person bbox ──────────────
                iou_full = self._iou(item['bbox'], [px1, py1, px2, py2])

                # ── Score 2: item center inside person bbox ─────────
                in_person = (px1 - p_w*0.15 <= i_cx <= px2 + p_w*0.15 and
                             py1 - p_h*0.10 <= i_cy <= py2 + p_h*0.10)

                # ── Score 3: horizontal overlap ratio ───────────────
                h_overlap = max(0, min(ix2, px2) - max(ix1, px1))
                h_ratio   = h_overlap / max(p_w, 1)

                # ── Score 4: vertical position ───────────────────────
                if p_h > 0:
                    rel_y = (i_cy - py1) / p_h
                else:
                    rel_y = 0.5

                if item_type == 'helmet':
                    # ── Dynamic head region ──────────────────────────
                    # head_pct is based on actual pixel height of person
                    head_pct = self._get_head_percent(p_h)

                    # Absolute pixel boundary where head ends
                    head_bottom = py1 + p_h * head_pct

                    # Hard reject: helmet center below head region
                    # This ensures shoulders are never inside helmet zone
                    if i_cy > head_bottom:
                        continue

                    # Hard reject: helmet bottom way below head region
                    # Allow slight overlap (10% tolerance)
                    if iy2 > head_bottom + p_h * 0.10:
                        continue

                    # Hard reject: helmet top below person top (impossible)
                    if iy1 > py1 + p_h * 0.35:
                        continue

                    # v_score peaks when helmet is at very top of person
                    v_score = max(0, 1 - rel_y * (1.0 / head_pct))

                else:  # vest
                    v_score = 1.0 - abs(rel_y - 0.45) * 2.0
                    v_score = max(0, v_score)

                    if iy2 < py1 + p_h * 0.10:
                        continue
                    if i_cy < py1 or i_cy > py2 + p_h * 0.1:
                        continue

                # ── Score 5: size check ──────────────────────────────
                if item_type == 'helmet':
                    size_ok = 0.10 <= (i_w / max(p_w, 1)) <= 0.75
                else:
                    size_ok = 0.25 <= (i_w / max(p_w, 1)) <= 1.3

                # ── Score 6: proximity ────────────────────────────────
                dist       = ((i_cx-p_cx)**2 + (i_cy-p_cy)**2)**0.5
                norm_dist  = dist / max(p_h, 1)
                prox_score = max(0, 1 - norm_dist)

                # ── Combined score ───────────────────────────────────
                score = (iou_full   * 3.0 +
                         h_ratio    * 1.5 +
                         v_score    * 1.0 +
                         prox_score * 0.8 +
                         (0.5 if in_person else 0) +
                         (0.3 if size_ok   else 0))

                if score > best_score:
                    best_score = score
                    best_pid   = pid

            if best_pid is not None and best_score > 1.0:
                owners.add(best_pid)

        return owners

    def _iou(self, box1, box2):
        x1 = max(box1[0], box2[0]); y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2]); y2 = min(box1[3], box2[3])
        inter = max(0, x2-x1) * max(0, y2-y1)
        if inter == 0: return 0.0
        a1 = (box1[2]-box1[0]) * (box1[3]-box1[1])
        a2 = (box2[2]-box2[0]) * (box2[3]-box2[1])
        return inter / (a1+a2-inter)

    def _get_violation_type(self, helmet, vest):
        if not helmet and not vest: return "no_helmet_no_vest"
        if not helmet:              return "no_helmet"
        if not vest:                return "no_vest"
        return None

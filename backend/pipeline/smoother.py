from collections import defaultdict, deque

class TemporalSmoother:
    """
    Two-stage PPE smoother with occlusion awareness.

    Stage 1 — Majority vote (buffer_size frames)
    Stage 2 — Consecutive violation counter (min_violation_frames)

    Key improvements:
    - Occlusion freezes counter (not incremented)
    - reset() clears all state for new video session
    - Transfer counters when ID jumps detected
    """
    def __init__(self, buffer_size=7, min_violation_frames=40):
        self.buffer_size          = buffer_size
        self.min_violation_frames = min_violation_frames
        self._init_state()

    def _init_state(self):
        self.helmet_buf        = defaultdict(lambda: deque(maxlen=self.buffer_size))
        self.vest_buf          = defaultdict(lambda: deque(maxlen=self.buffer_size))
        self.violation_counter = defaultdict(lambda: defaultdict(int))
        self._prev_pids        = set()

    def reset(self):
        """Call when new video uploaded — clears all counters."""
        self._init_state()
        print("[Smoother] Reset — all counters cleared")

    def update(self, ppe_status: dict) -> dict:
        smoothed    = {}
        current_ids = set(ppe_status.keys())

        # Detect completely new IDs that appeared suddenly
        # (ByteTrack reassigned after long occlusion)
        # If a new ID appears and old ID disappeared in same frame,
        # inherit the old counter to prevent false immediate alerts
        new_ids  = current_ids - self._prev_pids
        gone_ids = self._prev_pids - current_ids

        # Transfer violation counters from gone IDs to new IDs
        # only if exactly 1 new and 1 gone (clear 1-to-1 reassignment)
        if len(new_ids) == 1 and len(gone_ids) == 1:
            new_pid  = list(new_ids)[0]
            gone_pid = list(gone_ids)[0]

            # Transfer buffers and counters
            if gone_pid in self.violation_counter:
                self.violation_counter[new_pid]['helmet'] = \
                    self.violation_counter[gone_pid]['helmet']
                self.violation_counter[new_pid]['vest'] = \
                    self.violation_counter[gone_pid]['vest']
                self.helmet_buf[new_pid] = deque(
                    self.helmet_buf[gone_pid],
                    maxlen=self.buffer_size
                )
                self.vest_buf[new_pid] = deque(
                    self.vest_buf[gone_pid],
                    maxlen=self.buffer_size
                )

        self._prev_pids = current_ids

        for pid, status in ppe_status.items():
            occluded = status.get('occluded', False)

            # Stage 1 — majority vote
            self.helmet_buf[pid].append(int(status['helmet']))
            self.vest_buf[pid].append(int(status['vest']))

            buf_len     = len(self.helmet_buf[pid])
            helmet_vote = sum(self.helmet_buf[pid]) >= buf_len / 2
            vest_vote   = sum(self.vest_buf[pid])   >= buf_len / 2

            # Stage 2 — consecutive counter
            # FREEZE when occluded — occlusion is not a violation
            if not occluded:
                if not helmet_vote:
                    self.violation_counter[pid]['helmet'] += 1
                else:
                    # Complying — reset immediately
                    self.violation_counter[pid]['helmet'] = 0

                if not vest_vote:
                    self.violation_counter[pid]['vest'] += 1
                else:
                    self.violation_counter[pid]['vest'] = 0
            # else: frozen — counters unchanged during occlusion

            helmet_confirmed = (
                self.violation_counter[pid]['helmet'] >= self.min_violation_frames
            )
            vest_confirmed = (
                self.violation_counter[pid]['vest'] >= self.min_violation_frames
            )

            smoothed[pid] = {
                **status,
                'helmet':           helmet_vote,
                'vest':             vest_vote,
                'helmet_confirmed': helmet_confirmed,
                'vest_confirmed':   vest_confirmed,
                'violation':        helmet_confirmed or vest_confirmed,
                'violation_type':   self._get_type(helmet_confirmed, vest_confirmed),
                'helmet_frames':    self.violation_counter[pid]['helmet'],
                'vest_frames':      self.violation_counter[pid]['vest'],
                'occluded':         occluded,
            }

        return smoothed

    def _get_type(self, h, v):
        if h and v:  return "no_helmet_no_vest"
        if h:        return "no_helmet"
        if v:        return "no_vest"
        return None

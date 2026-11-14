"""Price the distance from the centre of taps and ground holds."""

import math
from collections import deque

from .two_finger import target_xy


class NoteCenterPreference:
    def __init__(self, projection, free_m, items, repeat_ms):
        self.projection = projection
        self.free_m = free_m
        self.centres = {}
        self.repeat_notes = set()
        recent = deque()
        notes = sorted((item for item in items if item.kind in ("tap", "sky") and not item.is_red),
                       key=lambda item: item.start_ms)
        for item in notes:
            centre = self.centre(item)
            while recent and item.start_ms - recent[0][0] > repeat_ms:
                recent.popleft()
            for time, key, point in recent:
                if time < item.start_ms and math.dist(centre, point) <= 0.035:
                    self.repeat_notes.update((key, item.occupation_id))
            recent.append((item.start_ms, item.occupation_id, centre))

    def centre(self, item):
        key = item.occupation_id
        if key not in self.centres:
            self.centres[key] = target_xy(item, item.start_ms, self.projection)
        return self.centres[key]

    def scale(self, item):
        return 0.01 if item.occupation_id in self.repeat_notes else 1.0

    def measure(self, touch):
        if touch is None:
            return 0.0
        if "_note_center_cost" in touch.__dict__:
            return touch._note_center_cost
        jobs = {}
        for part in touch.parts:
            item = part.candidate.occupation
            if item.kind in ("tap", "sky", "hold"):
                jobs.setdefault(item.occupation_id, (item, []))[1].append(part)
        total = 0.0
        for item, parts in jobs.values():
            centre = self.centre(item)
            scale = self.scale(item)

            def penalty(point):
                return (max(0.0, math.dist(point, centre) - self.free_m) / 0.020) ** 2 * scale

            if item.kind == "hold":
                frames = {frame for part in parts for frame in
                          range(max(part.start, touch.down), min(part.stop, touch.up))}
                total += sum(penalty(touch.xy(frame)) for frame in sorted(frames)) / 60
            else:
                total += penalty(touch.first_xy)
        touch._note_center_cost = total
        return total

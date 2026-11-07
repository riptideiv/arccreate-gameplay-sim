"""Optional, throttled observation of planner work; never changes search decisions."""

import time


class ProgressReporter:
    def __init__(self, callback=None, interval_s=0.4):
        self.callback = callback
        self.interval_s = interval_s
        self.last_phase = None
        self.last_emit = float('-inf')
        self.last_position = None

    def __call__(self, phase, completed=None, total=None, **details):
        if self.callback is None:
            return
        now = time.monotonic()
        position = (phase, completed, total)
        boundary = (completed is not None and (completed == 0 or completed == total)
                    and position != self.last_position)
        if phase != self.last_phase or boundary or now - self.last_emit >= self.interval_s:
            self.last_phase, self.last_emit = phase, now
            self.last_position = position
            self.callback(dict(phase=phase, completed=completed, total=total, **details))

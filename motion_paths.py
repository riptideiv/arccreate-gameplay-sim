"""Contact-pinned 60 Hz finger paths with return/wait/approach idle motion."""

import math
import numpy as np

RATE = 60
TIP = 0.006


def home_position(side, lift):
    return np.array((-0.085 if side == "left" else 0.085, -0.068, TIP + lift))


def travel(a, b, frames, lift, max_speed):
    t = np.linspace(0, 1, frames + 1)
    eased = t**3 * (10 - 15 * t + 6 * t * t)
    for linear in (0, 0.25, 0.5, 0.75, 1):
        u = (1 - linear) * eased + linear * t
        path = a[None] + u[:, None] * (b - a)[None]
        path[:, 2] += (1 - linear) * min(lift, 0.001 * frames) * np.sin(np.pi * t) ** 2
        if (
            np.linalg.norm(np.diff(path, axis=0), axis=1).max(initial=0) * RATE
            <= max_speed + 0.0001
        ):
            return path
    return path


def positions(touches, side, last_frame, lift=0.008, max_speed=math.inf, idle_speed=1.0):
    home = home_position(side, lift)
    knots = {0: home, last_frame: home}
    for touch in touches:
        for frame, (x, y) in enumerate(touch["path_mm"], touch["down_frame"]):
            knots[frame] = np.array((x / 1000, y / 1000, TIP))
    keys = sorted(knots)
    # Long gaps get a real stationary dwell, with approaches scheduled backwards
    # from the next contact. Never divert to home if the detour cannot fit.
    for start, end in zip(keys, keys[1:]):
        gap = end - start
        if gap < RATE:
            continue
        a, b = knots[start], knots[end]
        distances = (np.linalg.norm(home - a), np.linalg.norm(b - home))
        dwell = 15
        cap = min(idle_speed, max_speed * 0.8)
        speed = min(0.30, cap)
        needed = 1.875 * sum(distances) * RATE / max(gap - dwell, 1)
        speed = max(speed, needed)
        if speed > cap:
            continue
        durations = [
            0 if d < 1e-9 else max(8, math.ceil(1.875 * d / speed * RATE))
            for d in distances
        ]
        if sum(durations) + dwell > gap:
            continue
        knots[start + durations[0]] = home
        knots[end - durations[1]] = home
    result = np.empty((last_frame + 1, 3))
    keys = sorted(knots)
    for start, end in zip(keys, keys[1:]):
        # Home dwells stay still; released taps lift even at identical XY.
        resting = np.array_equal(knots[start], home) and np.array_equal(knots[end], home)
        # Home is optional; its approach/departure must respect the idle cap.
        # Contact-to-contact transfers retain the main limit when necessary.
        home_leg = np.array_equal(knots[start], home) or np.array_equal(knots[end], home)
        needed = np.linalg.norm(knots[end] - knots[start]) * RATE / (end - start)
        cap = min(max_speed, max(idle_speed, needed)) if home_leg else max_speed
        result[start : end + 1] = travel(knots[start], knots[end], end - start,
                                       0 if resting else lift, cap)
    if not keys or last_frame == 0:
        result[0] = knots[0]
    return result

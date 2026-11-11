"""Contact-pinned travel shared by planning and motion generation."""

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
        path[:, 2] += (1 - linear) * min(lift, 0.001 * frames) * np.sin(np.pi * t)**2
        if np.linalg.norm(np.diff(path, axis=0), axis=1).max(initial=0) * RATE <= max_speed + 0.0001:
            return path
    return path


def span(a, b, frames, home, lift, max_speed, idle_speed):
    """Travel between pinned samples, returning to idle when the gap permits."""
    if not frames:
        return b[None]
    distances = (np.linalg.norm(home - a), np.linalg.norm(b - home))
    if frames >= RATE:
        dwell = 15
        cap = min(idle_speed, max_speed * 0.8)
        speed = max(min(0.30, cap), 1.875 * sum(distances) * RATE / (frames - dwell))
        if speed <= cap:
            out, back = (
                0 if distance < 1e-9 else max(8, math.ceil(1.875 * distance / speed * RATE))
                for distance in distances
            )
            if out + back + dwell <= frames:
                chunks = [travel(a, home, out, lift, min(max_speed, max(idle_speed, distances[0] * RATE / out)))[:-1]
                          if out else np.empty((0, 3))]
                chunks.append(np.repeat(home[None], frames - out - back, axis=0))
                chunks.append(travel(home, b, back, lift, min(max_speed, max(idle_speed, distances[1] * RATE / back)))
                              if back else home[None])
                return np.concatenate(chunks)
    resting = np.array_equal(a, home) and np.array_equal(b, home)
    cap = max_speed
    if np.array_equal(a, home) or np.array_equal(b, home):
        cap = min(max_speed, max(idle_speed, np.linalg.norm(b - a) * RATE / frames))
    return travel(a, b, frames, 0 if resting else lift, cap)


def positions(touches, side, last_frame, lift=0.008, max_speed=math.inf, idle_speed=1.0):
    home = home_position(side, lift)
    knots = {0: home, last_frame: home}
    for touch in touches:
        for frame, (x, y) in enumerate(touch["path_mm"], touch["down_frame"]):
            knots[frame] = np.array((x / 1000, y / 1000, TIP))
    keys = sorted(knots)
    result = np.empty((last_frame + 1, 3))
    result[keys[0]] = knots[keys[0]]
    for start, end in zip(keys, keys[1:]):
        result[start : end + 1] = span(knots[start], knots[end], end - start,
                                      home, lift, max_speed, idle_speed)
    return result


def settle_frame(last_contact, requested, endpoints, lift, idle_speed):
    retreat = max((np.linalg.norm(point - home_position(side, lift))
                   for side, point in endpoints), default=0)
    return max(last_contact, requested) + max(34, math.ceil(1.875 * retreat / idle_speed * RATE))

"""Anatomical digit labels and thumb poses for the experimental planner."""

import numpy as np

from arc_hand_order.planning.hand_travel import TIP, home_position, span, travel
from hand_pose import bases


def assign_digits(touches, tap_interval_ms, min_air_frames, max_speed):
    """Orient each connected set of interchangeable planner slots anatomically."""
    for side in ("left", "right"):
        side_touches = sorted((t for t in touches if t["hand"] == side), key=lambda t: t["down_frame"])
        parents = list(range(len(side_touches)))
        paired = []
        recent = []
        point_press = [any(p["kind"] in ("tap", "sky") for p in t["parts"]) for t in side_touches]
        window = max(min_air_frames, int(np.ceil(tap_interval_ms * .06)),
                     int(np.ceil(60 * .3 / max_speed)))

        def root(i):
            while parents[i] != i:
                parents[i] = parents[parents[i]]
                i = parents[i]
            return i

        for i, touch in enumerate(side_touches):
            down = touch["down_frame"]
            recent = [j for j in recent if side_touches[j]["up_frame"] + window > down]
            for j in recent:
                other = side_touches[j]
                if other["finger"] == touch["finger"]:
                    continue
                overlap = min(touch["up_frame"], other["up_frame"]) - down
                elapsed = down - other["up_frame"]
                distance = np.linalg.norm(np.subtract(touch["path_mm"][0], other["path_mm"][-1])) / 1000
                repeat = point_press[i] and point_press[j] and (
                    (down - other["down_frame"]) * 1000 < tap_interval_ms * 60)
                if overlap > 0 or repeat or elapsed < min_air_frames or (
                        distance * 60 > max_speed * (elapsed + 1) + 1e-6):
                    parents[root(i)] = root(j)
                if overlap > 0:
                    a = np.asarray(touch["path_mm"][:overlap])
                    b = np.asarray(other["path_mm"][down-other["down_frame"]:][:overlap])
                    delta = (a - b) * (1 if touch["finger"] == 1 else -1)
                    # Prefer the index farther up the glass and the thumb inward
                    # when the two simultaneous contacts are side by side.
                    inward = 1 if side == "left" else -1
                    paired.append((i, float(np.sum(delta[:, 1] - .25 * inward * delta[:, 0]))))
            recent.append(i)

        preferences = {}
        for i, score in paired:
            key = root(i)
            preferences[key] = preferences.get(key, 0.) + score
        flips = {}
        for i, touch in enumerate(side_touches):
            key = root(i)
            preferred = preferences.get(key, 0.)
            flip = flips.setdefault(key, preferred < 0 or (preferred == 0 and touch["finger"] == 2))
            if flip:
                touch["finger"] = 3 - touch["finger"]


def samples(touches):
    return {frame: np.array([x / 1000, y / 1000, TIP])
            for touch in touches
            for frame, (x, y) in enumerate(touch["path_mm"], touch["down_frame"])}


def index_paths(contacts, thumbs, sides, last_frame, lift, max_speed, idle_speed):
    """Anchor the hand with the thumb when its index is free."""
    paths = []
    fixed = []
    physical = np.zeros((last_frame + 1, 2), dtype=bool)
    for h, side in enumerate(sides):
        home = home_position(side, lift)
        anchors = {0: home}
        sign = 1 if side == "left" else -1
        thumb_only = {f: point - [sign * .028, -.046, -lift] for f, point in thumbs[side].items()}
        index = samples(contacts[side])
        physical[list(index), h] = True
        anchors.update(thumb_only)
        anchors.update(index)
        anchors.setdefault(last_frame, anchors[max(anchors)])
        path = np.empty((last_frame + 1, 3))
        frames = sorted(anchors)
        for a, b in zip(frames, frames[1:]):
            path[a:b+1] = span(anchors[a], anchors[b], b-a, home, lift, max_speed, idle_speed)
        paths.append(path)
        fixed.append(set(anchors) | {f for f, p in enumerate(path) if np.array_equal(p, home)})
    return paths, fixed, physical


def thumb_joints(paths, poses, thumbs, sides):
    """Return base, knuckle and pad positions in tablet space for each thumb."""
    result = {}
    for h, side in enumerate(sides):
        sign = 1 if side == "left" else -1
        origin = np.asarray(paths[h], dtype=float)
        axes = bases(np.asarray(poses)[:, h])
        base = np.array([sign * .003, -.040, .010])
        # The thumb points along the index at rest, clear of the glass.
        rest = np.array([sign * .014, -.009, .011])
        anchors = {0: rest}
        for frame, target in thumbs[side].items():
            anchors[frame] = axes[frame].T @ (target - origin[frame])
        last = max(anchors)
        end = len(origin) - 1
        anchors.setdefault(end, anchors[last] if end-last < 9 else rest)
        frames = sorted(anchors)
        tips = np.empty_like(origin)
        for a, b in zip(frames, frames[1:]):
            start, end = anchors[a], anchors[b]
            if b - a > 18:
                tips[a:a+10] = travel(start, rest, 9, 0, np.inf)
                tips[a+9:b-9] = rest
                tips[b-9:b+1] = travel(rest, end, 9, 0, np.inf)
            else:
                tips[a:b+1] = travel(start, end, b-a, 0, np.inf)
        bend = np.clip(np.linalg.norm(tips-rest, axis=-1) * .15, 0, .008)
        middle = (base + tips) / 2
        middle[:, 2] += bend
        local = np.stack((np.broadcast_to(base, tips.shape), middle, tips), axis=1)
        world = np.einsum("fij,fkj->fki", axes, local) + origin[:, None]
        # Pinned pad coordinates use the identical replay samples.
        for frame, target in thumbs[side].items():
            if np.linalg.norm(world[frame, 2] - target) > 1e-9:
                raise ValueError(f"{side.title()} thumb moved a scoring contact")
            world[frame, 2] = target
        result[side] = world
    return result

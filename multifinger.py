"""Anatomical digit labels and thumb poses for the experimental planner."""

from bisect import bisect_left

import numpy as np

from arc_hand_order.planning.hand_travel import TIP, home_position, span, travel
from hand_pose import NEUTRAL, bases

ORIGINAL_THUMB_BASE = np.array([.003, -.040, .010])
THUMB_BASE = np.array([.003, -.040, .005])
THUMB_REST_VECTOR = np.array([.014, -.009, .011]) - ORIGINAL_THUMB_BASE
THUMB_LENGTH_M = float(np.linalg.norm(THUMB_REST_VECTOR))
THUMB_LENGTH_RATIO = .60
THUMB_OPEN_DEG = 115
TABLET_TO_REACH = 1.95
HAND_REFERENCE_DEPTH_M = .1735


def hand_calibration(tablet_height_m):
    # Measure the distance between pad centers with both fingers on the glass.
    angle = np.deg2rad(THUMB_OPEN_DEG)
    direction = np.array([np.sin(angle), np.cos(angle)])

    def reach(length, scale, thumb_base=THUMB_BASE):
        base = thumb_base * scale
        in_plane = np.sqrt(max(0., length**2 - base[2]**2))
        return float(np.linalg.norm(base[:2] + in_plane * direction))

    original_reach = reach(THUMB_LENGTH_M, 1., ORIGINAL_THUMB_BASE)
    target_reach = tablet_height_m / TABLET_TO_REACH
    previous_scale = max(1., target_reach / original_reach)
    length = THUMB_LENGTH_M * previous_scale * THUMB_LENGTH_RATIO
    low, high = 1., length / THUMB_BASE[2]
    for _ in range(40):
        scale = (low + high) / 2
        if reach(length, scale) < target_reach:
            low = scale
        else:
            high = scale
    return {
        "thumb_open_deg": THUMB_OPEN_DEG,
        "original_thumb_length_mm": THUMB_LENGTH_M * 1000,
        "original_open_reach_mm": original_reach * 1000,
        "scale": high,
        "thumb_length_mm": length * 1000,
        "open_reach_mm": reach(length, high) * 1000,
        "tablet_height_mm": tablet_height_m * 1000,
    }


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


def samples(touches, scale=1.):
    return {frame: np.array([x / 1000, y / 1000, TIP * scale])
            for touch in touches
            for frame, (x, y) in enumerate(touch["path_mm"], touch["down_frame"])}


def index_paths(contacts, thumbs, sides, last_frame, lift, max_speed, idle_speed, *, scale, length):
    """Anchor the hand with the thumb when its index is free."""
    paths = []
    fixed = []
    physical = np.zeros((last_frame + 1, 2), dtype=bool)
    for h, side in enumerate(sides):
        home = home_position(side, lift) + [0, 0, TIP * (scale-1)]
        anchors = {0: home}
        sign = 1 if side == "left" else -1
        axis = bases(NEUTRAL[h])
        base = axis @ (THUMB_BASE * [sign, 1, 1] * scale)
        index_lift = np.clip(.85 * length - base[2], 0, lift)
        outward = axis[:2, 0] * sign
        outward /= np.linalg.norm(outward)
        thumb_offset = base + np.r_[outward * .45 * length, -index_lift - base[2]]
        index = samples(contacts[side], scale)
        physical[list(index), h] = True
        shared = sorted(index.keys() & thumbs[side].keys())
        for frame, point in thumbs[side].items():
            if frame in index:
                continue
            offset = thumb_offset
            insertion = bisect_left(shared, frame)
            nearby = shared[max(0, insertion-1):insertion+1]
            if nearby:
                chord = min(nearby, key=lambda other: abs(other-frame))
                # Approach the chord while the thumb is already down. The
                # free index can line up with its next contact before landing.
                blend = max(0., 1. - abs(chord-frame) / 9)
                offset = (1-blend)*thumb_offset + blend*(thumbs[side][chord] - index[chord])
            anchors[frame] = point - offset
        anchors.update(index)
        if len(anchors) > 1 and 0 not in index and 0 not in thumbs[side]:
            first = min(frame for frame in anchors if frame > 0)
            if np.linalg.norm(anchors[first] - anchors[0]) * 60 > max_speed * first:
                # The excerpt may open during an existing hold. Start at the
                # first contact if there is no time to travel from home.
                anchors[0] = anchors[first]
        anchors.setdefault(last_frame, anchors[max(anchors)])
        path = np.empty((last_frame + 1, 3))
        frames = sorted(anchors)
        for a, b in zip(frames, frames[1:]):
            path[a:b+1] = span(anchors[a], anchors[b], b-a, home, lift, max_speed, idle_speed)
        paths.append(path)
        fixed.append(set(anchors) | {f for f, p in enumerate(path) if np.array_equal(p, home)})
    return paths, fixed, physical


def thumb_pitch_ceiling(points, thumbs, sides, scale, length):
    """Find where a thumb contact needs a flatter wrist than the neutral pose."""
    ceiling = np.full(points.shape[:2], np.inf)
    for h, side in enumerate(sides):
        if not thumbs[side]:
            continue
        frames = list(thumbs[side])
        delta = np.array(list(thumbs[side].values())) - points[frames, h]
        sign = 1 if side == "left" else -1
        base = bases(NEUTRAL[h]) @ (THUMB_BASE * [sign, 1, 1] * scale)
        reach = np.hypot(np.linalg.norm(delta[:, :2], axis=1) - np.linalg.norm(base[:2]),
                         delta[:, 2] - base[2])
        ceiling[frames, h] = np.where(reach > length, 0., NEUTRAL[h, 0])
    return ceiling


def feasible_yaw(preferred, lower, upper, budget):
    """Find a reachable yaw trajectory within the supplied turn budget."""
    lower, upper = lower.copy(), upper.copy()
    for frame in range(len(preferred)-2, -1, -1):
        lower[frame] = max(lower[frame], lower[frame+1]-budget[frame])
        upper[frame] = min(upper[frame], upper[frame+1]+budget[frame])
    if np.any(lower > upper + 1e-7):
        return None
    yaw = preferred.copy()
    yaw[0] = np.clip(preferred[0], lower[0], upper[0])
    for frame, step in enumerate(budget, 1):
        lo = max(lower[frame], yaw[frame-1]-step)
        hi = min(upper[frame], yaw[frame-1]+step)
        yaw[frame] = np.clip(preferred[frame], lo, hi)
    return yaw


def smooth_thumb_yaw(yaw, preferred, lower, upper, budget, contact_frames):
    """Minimize angular acceleration while keeping the thumb reachable."""
    if len(yaw) < 3:
        return
    window = np.zeros(len(yaw), dtype=bool)
    for frame in contact_frames:
        window[max(0, frame-18):frame+19] = True
    before = np.r_[np.inf, budget]
    after = np.r_[budget, np.inf]
    previous = np.arange(len(yaw)) - 1
    following = np.minimum(previous + 2, len(yaw)-1)
    previous[0] = 0
    groups = [np.flatnonzero(window & (np.arange(len(yaw)) % 3 == phase)) for phase in range(3)]
    weight = .025
    diagonal = np.convolve(np.ones(len(yaw)-2), [1., 4., 1.]) + weight
    # The acceleration stencil couples at most two neighbors on each side.
    # Three disjoint groups let us optimize in bulk while respecting speeds.
    for _ in range(160):
        for frames in groups:
            accel = np.diff(yaw, n=2)
            gradient = np.pad(accel, (0, 2)) - 2*np.pad(accel, (1, 1)) + np.pad(accel, (2, 0))
            gradient += weight * (yaw - preferred)
            lo = np.maximum.reduce((lower[frames], yaw[previous[frames]]-before[frames],
                                    yaw[following[frames]]-after[frames]))
            hi = np.minimum.reduce((upper[frames], yaw[previous[frames]]+before[frames],
                                    yaw[following[frames]]+after[frames]))
            yaw[frames] = np.clip(yaw[frames] - gradient[frames] / diagonal[frames], lo, hi)


def fit_thumb_yaw(paths, poses, thumbs, sides, scale, angular_limit, max_speed, hand_radius, *, length):
    """Choose a smooth wrist yaw inside the fixed-length thumb's reach."""
    result = np.array(poses, dtype=float, copy=True)
    for h, side in enumerate(sides):
        if not thumbs[side]:
            continue
        origin = np.asarray(paths[h], dtype=float)
        pitch, yaw = result[:, h].T
        preferred = yaw.copy()
        base = THUMB_BASE * [(-1 if side == "right" else 1), 1, 1] * scale
        base_at_pitch = bases(np.column_stack((pitch, np.zeros(len(pitch))))) @ base
        lower = np.full(len(pitch), -np.inf)
        upper = np.full(len(pitch), np.inf)
        for frame, point in thumbs[side].items():
            delta = point - origin[frame]
            base_vec = base_at_pitch[frame]
            a, b = np.linalg.norm(delta[:2]), np.linalg.norm(base_vec[:2])
            cos_limit = (a*a + b*b + (delta[2]-base_vec[2])**2 - length**2) / max(2*a*b, 1e-12)
            if cos_limit > 1. + 1e-7:
                raise ValueError(f"{side.title()} thumb is out of reach at {frame/60:.3f} s")
            centre = np.degrees(np.arctan2(delta[1], delta[0]) - np.arctan2(base_vec[1], base_vec[0]))
            centre = preferred[frame] + (centre - preferred[frame] + 180) % 360 - 180
            limit = np.degrees(np.arccos(np.clip(cos_limit, -1, 1)))
            lower[frame], upper[frame] = centre-limit, centre+limit
        speeds = np.linalg.norm(np.diff(origin, axis=0), axis=-1) * 60
        rotation = np.minimum(angular_limit, np.degrees(np.maximum(0, max_speed-speeds)/hand_radius)) / 60
        budget = np.sqrt(np.maximum(0, rotation**2 - np.diff(pitch)**2))
        # Seed the refinement with an earlier, slower turn when that is
        # possible. Otherwise use the full turn budget to reach the contacts.
        comfort = feasible_yaw(preferred, lower, upper, np.minimum(budget, 2.5))
        if comfort is None:
            comfort = feasible_yaw(preferred, lower, upper, budget)
        if comfort is None:
            raise ValueError(f"{side.title()} wrist cannot turn fast enough to reach the thumb contacts")
        yaw[:] = comfort
        smooth_thumb_yaw(yaw, preferred, lower, upper, budget, thumbs[side])
    return result


def thumb_joints(paths, poses, thumbs, sides, *, scale, length):
    """Return base, knuckle and pad positions in tablet space for each thumb."""
    result = {}
    for h, side in enumerate(sides):
        sign = 1 if side == "left" else -1
        origin = np.asarray(paths[h], dtype=float)
        axes = bases(np.asarray(poses)[:, h])
        base = THUMB_BASE * [sign, 1, 1] * scale
        # The thumb points along the index at rest, clear of the glass.
        rest = base + THUMB_REST_VECTOR * [sign, 1, 1] * (length / THUMB_LENGTH_M)
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
        axis = tips - base
        distance = np.linalg.norm(axis, axis=-1)
        if distance.max() > length + 1e-7:
            raise ValueError(f"{side.title()} thumb cannot reach its target at a fixed length")
        direction = axis / np.maximum(distance[:, None], 1e-12)
        up = np.array([0., 0., 1.]) - direction[:, 2, None] * direction
        vertical = np.linalg.norm(up, axis=-1) < 1e-8
        up[vertical] = [sign, 0, 0]
        bend = np.sqrt(np.maximum(0, length**2 - distance**2)) / 2
        middle = (base + tips) / 2 + up / np.linalg.norm(up, axis=-1)[:, None] * bend[:, None]
        local = np.stack((np.broadcast_to(base, tips.shape), middle, tips), axis=1)
        world = np.einsum("fij,fkj->fki", axes, local) + origin[:, None]
        # Pinned pad coordinates use the identical replay samples.
        for frame, target in thumbs[side].items():
            if np.linalg.norm(world[frame, 2] - target) > 1e-9:
                raise ValueError(f"{side.title()} thumb moved a scoring contact")
            world[frame, 2] = target
        result[side] = world
    return result

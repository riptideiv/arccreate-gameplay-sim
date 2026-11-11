"""Offline, contact-preserving glove posing. Collision costs never alter a chart.

The search uses enclosing spheres for the glove's ellipsoids. Its fixed budget
returns the best pose corridor found, with residual overlaps reported, never a
request for the contact planner to skip notes.
"""

import math
import numpy as np

TIP = 0.006
# Centers and radii in meters, with the index pad centered on the hand origin.
HAND_PARTS = (
    ("index pad", (0, 0, 0), (TIP, TIP, TIP)),
    ("index", (0, -0.011, 0.003), (0.0063, 0.013, 0.0065)),
    ("index knuckle", (0, -0.022, 0.008), (0.007, 0.012, 0.008)),
    ("palm", (-0.012, -0.039, 0.011), (0.020, 0.021, 0.014)),
    ("folded middle", (-0.012, -0.025, 0.011), (0.007, 0.013, 0.010)),
    ("folded ring", (-0.022, -0.028, 0.010), (0.007, 0.012, 0.009)),
    ("folded pinky", (-0.030, -0.033, 0.008), (0.006, 0.010, 0.008)),
    ("tucked thumb", (0.003, -0.037, 0.004), (0.009, 0.012, 0.008)),
    ("cuff", (-0.012, -0.060, 0.011), (0.017, 0.013, 0.013)),
)


VERSION = "pivot-beam-arms-2"
NEUTRAL = np.array([[10.0, -18.0], [10.0, 18.0]])
# Seated player in tablet-local meters. The elbow pole points outward and down.
SHOULDERS = np.array([[-0.18, -0.38, 0.17], [0.18, -0.38, 0.17]])
ELBOW_POLES = np.array([[-0.7, 0.0, -1.0], [0.7, 0.0, -1.0]])
UPPER_ARM_LENGTH = 0.29
FOREARM_LENGTH = 0.26
ARM_RADIUS = 0.020
ARM_WEIGHT = 0.25


def bases(angles):
    p, y = np.moveaxis(np.radians(angles), -1, 0)
    cp, sp, cy, sy = np.cos(p), np.sin(p), np.cos(y), np.sin(y)
    out = np.zeros(angles.shape[:-1] + (3, 3))
    out[..., 0, 0] = cy
    out[..., 0, 1] = -sy * cp
    out[..., 0, 2] = -sy * sp
    out[..., 1, 0] = sy
    out[..., 1, 1] = cy * cp
    out[..., 1, 2] = cy * sp
    out[..., 2, 1] = -sp
    out[..., 2, 2] = cp
    return out


def proxies(angles, parts):
    """Pose ellipsoid centres; use their enclosing radii to certify clearance."""
    centres = np.array([p[1] for p in parts])
    radii = np.array([p[2] for p in parts])
    rot = bases(np.asarray(angles))
    return np.einsum("...ij,pj->...pi", rot, centres), np.sqrt(
        np.einsum("...j,pj->...p", rot[..., 2, :] ** 2, radii**2)
    )


def arm_elbows(wrists, forward, swivels=None):
    axis = wrists - SHOULDERS
    distance = np.linalg.norm(axis, axis=-1)
    axis /= np.maximum(distance[..., None], 1e-9)
    reach = np.clip(
        distance, abs(UPPER_ARM_LENGTH - FOREARM_LENGTH), UPPER_ARM_LENGTH + FOREARM_LENGTH
    )
    along = (UPPER_ARM_LENGTH**2 - FOREARM_LENGTH**2 + reach**2) / (2 * reach)
    away = np.sqrt(np.maximum(0, UPPER_ARM_LENGTH**2 - along**2))
    pole = ELBOW_POLES - (ELBOW_POLES * axis).sum(axis=-1)[..., None] * axis
    pole /= np.maximum(np.linalg.norm(pole, axis=-1, keepdims=True), 1e-9)
    # Swivel the elbow toward the hand's backward bearing. The outward/downward
    # bias limits the swivel and leaves pitch available for tapping.
    backward = -forward.copy()
    backward[..., 2] = 0
    backward /= np.maximum(np.linalg.norm(backward, axis=-1, keepdims=True), 1e-9)
    steering = backward - (backward * axis).sum(axis=-1)[..., None] * axis
    pole += 0.65 * steering
    pole /= np.maximum(np.linalg.norm(pole, axis=-1, keepdims=True), 1e-9)
    if swivels is not None:
        angle = np.radians(swivels)[..., None]
        pole = pole * np.cos(angle) + np.cross(axis, pole) * np.sin(angle)
    return SHOULDERS + axis * along[..., None] + pole * away[..., None]


def _point_segment(point, start, end):
    axis = end - start
    delta = point - start
    t = np.sum(delta * axis, axis=-1) / np.maximum(np.sum(axis**2, axis=-1), 1e-12)
    return np.linalg.norm(delta - np.clip(t, 0, 1)[..., None] * axis, axis=-1)


def _segment_gap(a, b, c, d):
    u, v, w = b - a, d - c, a - c
    uu, vv, uv, uw, vw = (
        np.sum(x * y, axis=-1) for x, y in ((u, u), (v, v), (u, v), (u, w), (v, w))
    )
    determinant = uu * vv - uv**2
    t = (uv * vw - vv * uw) / np.maximum(determinant, 1e-12)
    s = (uu * vw - uv * uw) / np.maximum(determinant, 1e-12)
    interior = (determinant > 1e-12) & (t >= 0) & (t <= 1) & (s >= 0) & (s <= 1)
    closest = w + t[..., None] * u - s[..., None] * v
    distance = np.where(interior, np.linalg.norm(closest, axis=-1), np.inf)
    return np.minimum.reduce([
        distance, _point_segment(a, c, d), _point_segment(b, c, d),
        _point_segment(c, a, b), _point_segment(d, a, b),
    ])


def arm_geometry(centres, forward, parts, swivels=None):
    cuffs = [next(i for i, part in enumerate(hand) if part[0] == "cuff") for hand in parts]
    wrists = np.stack(
        [hand[..., cuff, :] for hand, cuff in zip(centres, cuffs)], axis=-2
    )
    return wrists, arm_elbows(wrists, forward, swivels)


def arm_metrics(centres, forward, parts, swivels=None):
    """Arm clearance and wrist alignment for posed glove centres in tablet space."""
    wrists, elbows = arm_geometry(centres, forward, parts, swivels)
    forearm = wrists - elbows
    arm_distance = _segment_gap(
        elbows[..., 0, :], wrists[..., 0, :], elbows[..., 1, :], wrists[..., 1, :]
    )
    margin = arm_distance - 2 * ARM_RADIUS
    margin = np.minimum(margin, _segment_gap(
        SHOULDERS[0], elbows[..., 0, :], SHOULDERS[1], elbows[..., 1, :]
    ) - 2 * ARM_RADIUS)
    for h in range(2):
        shoulder, elbow, wrist = SHOULDERS[h], elbows[..., h, :], wrists[..., h, :]
        other = 1 - h
        margin = np.minimum(margin, _segment_gap(
            shoulder, elbow, elbows[..., other, :], wrists[..., other, :]
        ) - 2 * ARM_RADIUS)
        glove = centres[other]
        radius = np.array([max(part[2]) for part in parts[other]])
        margin = np.minimum(margin, (_point_segment(
            glove, elbow[..., None, :], wrist[..., None, :]
        ) - radius - ARM_RADIUS).min(axis=-1))
    forearm_xy = forearm[..., :2]
    forward_xy = forward[..., :2]
    yaw_cos = (forearm_xy * forward_xy).sum(axis=-1) / np.maximum(
        np.linalg.norm(forearm_xy, axis=-1) * np.linalg.norm(forward_xy, axis=-1), 1e-9
    )
    forearm_pitch = np.arctan2(forearm[..., 2], np.linalg.norm(forearm_xy, axis=-1))
    hand_pitch = np.arctan2(forward[..., 2], np.linalg.norm(forward_xy, axis=-1))
    flexion = forearm_pitch - hand_pitch
    reach = np.linalg.norm(wrists - SHOULDERS, axis=-1)
    return margin, np.clip(yaw_cos, -1, 1), flexion, reach


def arm_cost(centres, forward, parts, gap, swivels=None):
    margin, yaw_cos, flexion, reach = arm_metrics(centres, forward, parts, swivels)
    sideways_bend = 320 * (1 - yaw_cos) + 1400 * np.maximum(0, 0.86 - yaw_cos) ** 2
    # Allow downward flex for taps. Backward or excessive flex is less useful.
    pitch_bend = (
        400 * np.maximum(0, -flexion - math.radians(10)) ** 2
        + 400 * np.maximum(0, flexion - math.radians(35)) ** 2
    )
    return (
        np.clip((gap - margin) * 1000, 0, 18) ** 2
        + np.sum(sideways_bend + pitch_bend, axis=-1)
        + np.sum(np.maximum(0, reach - UPPER_ARM_LENGTH - FOREARM_LENGTH) ** 2, axis=-1) * 1e6
    )


def arm_diagnostics(points, poses, parts, swivels=None):
    """Sample the simplified arm rig at the same subframes as glove clearance."""
    overlaps = np.zeros(len(points) - 1, dtype=bool)
    min_margin, min_yaw, max_reach = math.inf, 1.0, 0.0
    for fraction in (0, 0.25, 0.5, 0.75):
        p = points[:-1] * (1 - fraction) + points[1:] * fraction
        q = poses[:-1] * (1 - fraction) + poses[1:] * fraction
        centres = [proxies(q[:, h], parts[h])[0] + p[:, h, None, :] for h in range(2)]
        swivel = None if swivels is None else swivels[:-1]*(1-fraction) + swivels[1:]*fraction
        margin, yaw, _, reach = arm_metrics(centres, bases(q)[..., 1], parts, swivel)
        overlaps |= margin < -1e-5
        min_margin = min(min_margin, margin.min(initial=math.inf))
        min_yaw = min(min_yaw, yaw.min(initial=1))
        max_reach = max(max_reach, reach.max(initial=0))
    return {
        "minimum_clearance_mm": float(min_margin * 1000),
        "overlap_intervals": int(overlaps.sum()),
        "maximum_wrist_yaw_deg": math.degrees(math.acos(np.clip(min_yaw, -1, 1))),
        "maximum_reach_mm": float(max_reach * 1000),
    }


def margins(points, angles, parts):
    a, za = proxies(angles[..., 0, :], parts[0])
    b, zb = proxies(angles[..., 1, :], parts[1])
    ra = np.array([max(p[2]) for p in parts[0]])
    rb = np.array([max(p[2]) for p in parts[1]])
    a = a + points[..., 0, None, :]
    b = b + points[..., 1, None, :]
    delta = a[..., :, None, :] - b[..., None, :, :]
    gap = (np.linalg.norm(delta, axis=-1) - ra[:, None] - rb[None, :]).min(
        axis=(-2, -1)
    )
    floor = np.minimum((a[..., 2] - za).min(axis=-1), (b[..., 2] - zb).min(axis=-1))
    return gap, floor


def _pose_penalty(margin, floor, gap, arm_weight, both_pinned):
    penalty = np.maximum(0, (gap - margin) * 1000) ** 2
    penalty += 4 * np.maximum(0, -floor * 1000 - 0.05) ** 2
    if arm_weight:
        # The arm placement is approximate. Visible glove and tablet
        # penetration should outweigh a small improvement to the arm fit.
        # Free hands can route around a collision later. With both fingers
        # pinned, rotation is the only way to clear a glove crossing.
        overlap_weight = 48 if both_pinned else 4
        penalty += overlap_weight * np.maximum(0, -margin * 1000 - 0.05) ** 2
        penalty += 12 * np.maximum(0, -floor * 1000 - 0.05) ** 2
    return penalty


def air_paths(
    points, active, max_speed, pad_radius=0.006, gap=0.001, poses=None, parts=None, idle_speed=math.inf,
    routes=None, swivels=None,
):
    """Lift only free portions of a hand path; preserve every contact sample."""
    result = points.copy()
    changes = 0
    runs = []
    for h in range(2):
        free = ~active[:, h]
        edges = np.diff(np.r_[False, free, False].astype(int))
        for first, last in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            a = max(0, first - 1)
            b = min(len(points) - 1, last)
            if b - a < (2 if routes else 3):
                continue
            runs.append((a, b, h))
    directions = np.array(
        [
            (x, y, z)
            for x, y, z in (
                (0, 0, 1),
                (1, 0, 0.6),
                (-1, 0, 0.6),
                (0, 1, 0.6),
                (0, -1, 0.6),
                (1, 1, 0.6),
                (-1, 1, 0.6),
                (1, -1, 0.6),
                (-1, -1, 0.6),
            )
        ],
        float,
    )
    if routes is not None:
        from crossing_motion import layer_margin, layers_at, sample_swivels
        directions = directions[:1]
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    heights = ((.005, .01, .015, .022, .03, .038, .045, .047, .049, .06, .08)
               if routes else (.015, .03, .045, .06, .08))
    offsets = np.vstack(
        (
            np.zeros((1, 3)),
            *[directions * height for height in heights],
        )
    )
    radius = (
        (max(np.linalg.norm(p[1]) + max(p[2]) for p in parts[0]) + 0.002)
        if parts
        else 0
    )

    def penalty_for(candidates, other, a, b, h, mandatory_layer):
        penalty = np.zeros(len(candidates))
        peak_layer_shortfall = np.zeros(len(candidates))
        fractions = np.arange(8)/8 if mandatory_layer else (0, 0.25, 0.5, 0.75)
        for fraction in fractions:
            here = candidates[:, :-1] * (1 - fraction) + candidates[:, 1:] * fraction
            there = other[:-1] * (1 - fraction) + other[1:] * fraction
            if poses is None:
                margin = np.linalg.norm(here - there, axis=-1) - (
                    2 * pad_radius + gap + 0.008
                )
            else:
                q = poses[a:b] * (1 - fraction) + poses[a + 1 : b + 1] * fraction
                ca, _ = proxies(q[:, h], parts[h])
                cb, _ = proxies(q[:, 1 - h], parts[1 - h])
                ra = np.array([max(p[2]) for p in parts[h]])
                rb = np.array([max(p[2]) for p in parts[1 - h]])
                margins = []
                for block in range(0, len(candidates), 8):
                    delta = (ca[:, :, None, :] - cb[:, None, :, :])[None] + (
                        here[block : block + 8] - there
                    )[:, :, None, None, :]
                    margins.append(
                        (
                            np.linalg.norm(delta, axis=-1) - ra[:, None] - rb[None, :]
                        ).min(axis=(-2, -1))
                        - gap
                    )
                margin = np.concatenate(margins)
                if routes:
                    frames = np.arange(a, b) + fraction
                    layers = layers_at(frames, routes)
                if routes and layers.any():
                    # The candidate axis broadcasts over the fixed other hand.
                    own = ca[None] + here[..., None, :]
                    peer = np.broadcast_to(cb[None] + there[None, :, None, :], own.shape)
                    centres = (own, peer) if h == 0 else (peer, own)
                    layer_gap = layer_margin(centres, q, parts, layers, sample_swivels(swivels, frames))
                    shortfall = np.maximum(0, gap-layer_gap)**2
                    penalty += 8e9 * np.sum(shortfall, axis=1)
                    peak_layer_shortfall = np.maximum(peak_layer_shortfall, shortfall.max(axis=1))
            penalty += np.sum(np.maximum(0, -margin) ** 2, axis=1) * 1e8
        penalty += 8e10 * peak_layer_shortfall
        penalty += np.sum((candidates - points[a : b + 1, h]) ** 2, axis=(1, 2)) * 0.01
        return penalty

    for _ in range(3):
        improved = False
        for a, b, h in runs:
            base = points[a : b + 1, h]
            other = result[a : b + 1, 1 - h]
            mandatory_layer = bool(routes and layers_at(np.arange(a, b+1), routes).any())
            if poses is None:
                if (
                    np.linalg.norm(result[a : b + 1, h] - other, axis=-1).min()
                    >= 2 * pad_radius + gap + 0.008
                ):
                    continue
            elif penalty_for(result[a : b + 1, h][None], other, a, b, h, mandatory_layer)[0] < 1e-7:
                continue
            t = np.linspace(0, 1, b - a + 1)
            from_start = np.arange(b - a + 1)
            to_end = from_start[::-1]
            ramp = np.minimum(1, np.minimum(from_start, to_end) / 4)
            ramp = ramp**3 * (10 - 15 * ramp + 6 * ramp**2)
            shapes = [np.sin(t * np.pi) ** 2, ramp]
            if mandatory_layer:
                # A release can sit immediately before a pass. Consider a
                # quick rise or descent, still bounded by the real speed cap.
                shapes.extend(np.minimum(1, np.minimum(from_start/a_rate, to_end/b_rate))
                              for a_rate, b_rate in ((1, 4), (4, 1), (2, 2)))
            shapes = np.stack(shapes)
            candidates = (
                base[None, None, :, :]
                + offsets[None, :, None, :] * shapes[:, None, :, None]
            ).reshape(-1, b - a + 1, 3)
            candidates = np.concatenate((result[a : b + 1, h][None], candidates))
            speed = np.linalg.norm(np.diff(candidates, axis=1), axis=-1) * 60
            # Optional avoidance must not spend the full contact-travel budget.
            # If the fixed contacts already demand faster travel, preserve that
            # baseline allowance rather than dropping a note to slow a detour.
            required = np.linalg.norm(np.diff(base, axis=0), axis=-1).max(initial=0) * 60
            free_cap = max_speed if mandatory_layer else min(max_speed, max(idle_speed, required))
            valid_translation = speed.max(axis=1) <= free_cap + 1e-5
            if poses is not None:
                speed += (
                    radius
                    * np.radians(
                        np.linalg.norm(np.diff(poses[a : b + 1, h], axis=0), axis=-1)
                        * 60
                    )[None, :]
                )
            valid = valid_translation & (speed.max(axis=1) <= max_speed + 1e-5)
            penalty = penalty_for(candidates, other, a, b, h, mandatory_layer)
            penalty[~valid] = np.inf
            choice = int(np.argmin(penalty))
            if choice and penalty[choice] < penalty[0] - 1e-8:
                result[a : b + 1, h] = candidates[choice]
                changes += 1
                improved = True
        if not improved:
            break
    result[active] = points[active]
    return result, changes


def solve(
    points,
    active,
    parts,
    max_speed=4.0,
    angular_limit=360.0,
    gap=0.001,
    beam=96,
    stride=6,
    idle_speed=math.inf,
    arm_weight=ARM_WEIGHT,
    routes=None,
):
    points = np.asarray(points, dtype=float)
    active = np.asarray(active, dtype=bool)
    both_pinned = active.all(axis=1)
    # Keep glove clearance dominant when the wrist turn budget is very tight.
    arm_weight *= min(1, angular_limit / 180)**2
    # Slow wrists need wider key intervals to traverse the angular catalogue;
    # the final 60 Hz interpolation still respects each frame's turn budget.
    stride = max(stride, math.ceil(30 * 60 / max(angular_limit, 1)))
    original = points.copy()
    count = len(points)
    swivels = None
    if routes:
        from crossing_motion import (
            CrossingMotionError, boundary_subframes, layer_margin, layers_at, sample_swivels,
            solve_swivels, verify,
        )
        neutral_poses = np.broadcast_to(NEUTRAL, (count, 2, 2))
        swivels = solve_swivels(points, neutral_poses, parts, routes, gap)
        points, lifts = air_paths(
            points, active, max_speed, gap=gap, poses=neutral_poses, parts=parts,
            idle_speed=idle_speed, routes=routes, swivels=swivels,
        )
        swivels = solve_swivels(points, neutral_poses, parts, routes, gap)
        neutral_report = diagnostics(points, neutral_poses, parts, gap)
        if not neutral_report["residual_proxy_overlap_samples"]:
            try:
                verify(points, neutral_poses, parts, routes, gap, swivels=swivels)
            except CrossingMotionError:
                pass  # The elbow and free-hand search could not avoid wrist compensation.
            else:
                return points, neutral_poses, {
                    **neutral_report, "solver": VERSION, "air_overpasses": lifts,
                    "elbow_swivel_deg": swivels.tolist(), "wrist_compensation_search": False,
                    "maximum_contact_drift_m": 0.,
                }
    else:
        points, lifts = air_paths(
            points, active, max_speed, gap=gap, idle_speed=idle_speed, routes=routes,
        )
    # Each state is a pair of pitch/yaw choices. The pad centre is the pivot.
    pitches = (10, 25, 40, 55, 70, 85) if angular_limit < 200 else (10, 30, 50, 70, 85)
    yaws = (
        sorted(set(range(-150, 151, 15)) | {-18, 18})
        if angular_limit < 200
        else (-150, -120, -90, -60, -30, -18, 0, 18, 30, 60, 90, 120, 150)
    )
    catalogue = np.array(
        [(p, y) for p in pitches for y in yaws],
        dtype=float,
    )
    left, right = np.meshgrid(
        np.arange(len(catalogue)), np.arange(len(catalogue)), indexing="ij"
    )
    states = np.stack((catalogue[left.ravel()], catalogue[right.ravel()]), axis=1)
    radius = max(np.linalg.norm(p[1]) + max(p[2]) for p in parts[0]) + 0.002
    speeds = np.linalg.norm(np.diff(points, axis=0), axis=-1) * 60
    budgets = (
        np.minimum(
            angular_limit, np.maximum(0, max_speed - speeds) * 180 / (np.pi * radius)
        )
        / 60
    )
    cumulative = np.vstack((np.zeros(2), np.cumsum(budgets, axis=0)))
    knots = np.unique(np.r_[np.arange(0, count, stride), count - 1])
    neutral = np.sum((states - NEUTRAL) ** 2, axis=(1, 2)) / 10000
    # Cache pose-dependent proxies; each endpoint query only translates them.
    ca, za = proxies(states[:, 0], parts[0])
    cb, zb = proxies(states[:, 1], parts[1])
    forward = bases(states)[..., 1]
    ra = np.array([max(p[2]) for p in parts[0]])
    rb = np.array([max(p[2]) for p in parts[1]])

    layers = layers_at(np.arange(count), routes) if routes else np.zeros(count, dtype=np.int8)
    if routes:
        boundaries = boundary_subframes(routes)
        boundaries = boundaries[boundaries < count - 1]
        boundary_layers = layers_at(boundaries, routes)
        boundaries = boundaries[boundary_layers != 0]
        boundary_layers = boundary_layers[boundary_layers != 0]

    def crossing_penalty(centres, angles, f):
        if not layers[f]:
            return 0
        margin = layer_margin(centres, angles, parts, layers[f], swivels[f])
        wrist_departure = np.sum((angles - NEUTRAL)**2, axis=(-2, -1))
        return 80 * np.maximum(0, (gap - margin) * 1000)**2 + .08 * wrist_departure

    def spatial(f):
        delta = (
            ca[:, :, None, :]
            - cb[:, None, :, :]
            + (points[f, 0] - points[f, 1])[None, None, None, :]
        )
        margin = (np.linalg.norm(delta, axis=-1) - ra[:, None] - rb[None, :]).min(
            axis=(1, 2)
        )
        floor = np.minimum(
            (ca[..., 2] + points[f, 0, 2] - za).min(axis=1),
            (cb[..., 2] + points[f, 1, 2] - zb).min(axis=1),
        )
        penalty = _pose_penalty(margin, floor, gap, arm_weight, both_pinned[f])
        centres = (ca + points[f, 0], cb + points[f, 1])
        if arm_weight:
            penalty += arm_weight * arm_cost(centres, forward, parts, gap, None if swivels is None else swivels[f])
        penalty += crossing_penalty(centres, states, f)
        return penalty

    cache = {}

    def cost(f):
        if f not in cache:
            cache[f] = spatial(f)
        return cache[f]

    def ahead(f):
        horizon = max(1, 360 / angular_limit)
        return sum(
            cost(min(count - 1, f + round(d * horizon))) * w
            for d, w in ((6, 0.4), (12, 0.3), (24, 0.15), (36, 0.05))
        )

    initial = cost(0) + neutral * 0.03
    chosen = np.argsort(initial + ahead(0), kind="stable")[:beam]
    previous = states[chosen]
    values = initial[chosen]
    history = [(chosen, np.full(len(chosen), -1, dtype=int))]
    for a, b in zip(knots, knots[1:]):
        cap = cumulative[b] - cumulative[a]
        distance = np.linalg.norm(
            states[None, :, :, :] - previous[:, None, :, :], axis=-1
        )
        feasible = np.all(distance <= cap[None, None, :] + 1e-6, axis=-1)
        edge = np.sum(distance**2, axis=-1) / 10000 * 0.2
        scores = values[:, None] + edge + cost(b)[None, :] + neutral[None, :] * 0.03
        scores[~feasible] = np.inf
        parents = np.argmin(scores, axis=0)
        best = scores[parents, np.arange(len(states))]
        # Keep diverse destination poses; evaluate interpolated geometry on the
        # candidates that can actually survive the beam, not every graph edge.
        shortlist = np.argsort(best + ahead(b), kind="stable")[
            : min(beam * 3, len(states))
        ]
        shortlist = shortlist[np.isfinite(best[shortlist])]
        if not len(shortlist):
            # Keeping the last orientation always satisfies a zero turn budget.
            raise RuntimeError("Pose graph lost its stationary transition")
        start = previous[parents[shortlist]]
        end = states[shortlist]
        max_penalty = np.zeros(len(shortlist))
        for f in range(a + 1, b):
            alpha = np.divide(
                cumulative[f] - cumulative[a], cap, out=np.zeros(2), where=cap > 1e-9
            )
            angles = start + (end - start) * alpha[None, :, None]
            m, floor = margins(points[f], angles, parts)
            penalty = _pose_penalty(m, floor, gap, arm_weight, both_pinned[f])
            if arm_weight or layers[f]:
                centres = [proxies(angles[:, h], parts[h])[0] + points[f, h] for h in range(2)]
                if arm_weight:
                    penalty += arm_weight * arm_cost(centres, bases(angles)[..., 1], parts, gap,
                                                 None if swivels is None else swivels[f])
                penalty += crossing_penalty(centres, angles, f)
            max_penalty = np.maximum(max_penalty, penalty)
        if routes:
            first, last = np.searchsorted(boundaries, (a, b))
            for frame, layer in zip(boundaries[first:last], boundary_layers[first:last]):
                f = int(frame)
                u = frame - f
                used = cumulative[f]*(1-u) + cumulative[f+1]*u - cumulative[a]
                alpha = np.divide(used, cap, out=np.zeros(2), where=cap > 1e-9)
                angles = start + (end - start) * alpha[None, :, None]
                point = points[f]*(1-u) + points[f+1]*u
                centres = [proxies(angles[:, h], parts[h])[0] + point[h] for h in range(2)]
                margin = layer_margin(centres, angles, parts, layer,
                                      sample_swivels(swivels, np.asarray(frame)))
                max_penalty = np.maximum(max_penalty, 80*np.maximum(0, (gap-margin)*1000)**2)
        adjusted = best[shortlist] + max_penalty
        order = np.argsort(adjusted + ahead(b)[shortlist], kind="stable")[:beam]
        chosen = shortlist[order]
        history.append((chosen, parents[chosen]))
        previous = states[chosen]
        values = adjusted[order]
    row = int(np.argmin(values))
    route = []
    for ids, parents in reversed(history):
        route.append(states[ids[row]])
        row = parents[row]
    route = np.array(route[::-1])
    poses = np.empty((count, 2, 2))
    for k, (a, b) in enumerate(zip(knots, knots[1:])):
        cap = cumulative[b] - cumulative[a]
        alpha = np.divide(
            cumulative[a : b + 1] - cumulative[a],
            cap,
            out=np.zeros((b - a + 1, 2)),
            where=cap > 1e-9,
        )
        poses[a : b + 1] = route[k] + (route[k + 1] - route[k]) * alpha[:, :, None]
    if routes:
        swivels = solve_swivels(points, poses, parts, routes, gap)
    points, body_lifts = air_paths(
        points, active, max_speed, gap=gap, poses=poses, parts=parts, idle_speed=idle_speed,
        routes=routes, swivels=swivels,
    )
    lifts += body_lifts
    if routes and body_lifts:
        swivels = solve_swivels(points, poses, parts, routes, gap)
    report = diagnostics(points, poses, parts, gap)
    report.update(
        solver=VERSION,
        air_overpasses=lifts,
        maximum_contact_drift_m=float(
            np.linalg.norm((points - original)[active], axis=-1).max(initial=0)
        ),
    )
    if swivels is not None:
        report.update(elbow_swivel_deg=swivels.tolist(), wrist_compensation_search=True)
    return points, poses, report


def diagnostics(points, poses, parts, gap):
    # Match the renderer's linear interpolation, including between-frame motion.
    minimum = math.inf
    floor_min = math.inf
    overlaps = set()
    shortfalls = set()
    pad_overlap = set()
    for t in (0, 0.25, 0.5, 0.75):
        p = points[:-1] * (1 - t) + points[1:] * t
        q = poses[:-1] * (1 - t) + poses[1:] * t
        margin, floor = margins(p, q, parts)
        minimum = min(minimum, float(margin.min(initial=math.inf)))
        floor_min = min(floor_min, float(floor.min(initial=math.inf)))
        overlaps.update(np.flatnonzero(margin < -0.00001).tolist())
        shortfalls.update(np.flatnonzero(margin < gap - 0.00001).tolist())
        pad_overlap.update(
            np.flatnonzero(np.linalg.norm(p[:, 0] - p[:, 1], axis=-1) < 0.012).tolist()
        )
    return {
        "minimum_clearance_mm": minimum * 1000,
        "minimum_tablet_clearance_mm": floor_min * 1000,
        "residual_proxy_overlap_samples": len(overlaps),
        "residual_pad_overlap_samples": len(pad_overlap),
        "preferred_clearance_shortfall_samples": len(shortfalls),
        "residual_times_s": [f / 60 for f in sorted(overlaps)[:30]],
        "collision_verification": False,
        "contact_preserving_pose_solver": True,
    }

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


VERSION = "pivot-beam-1"
NEUTRAL = np.array([[10.0, -18.0], [10.0, 18.0]])


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


def air_paths(
    points, active, max_speed, pad_radius=0.006, gap=0.001, poses=None, parts=None
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
            if b - a < 3:
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
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    offsets = np.vstack(
        (
            np.zeros((1, 3)),
            *[directions * height for height in (0.015, 0.03, 0.045, 0.06, 0.08)],
        )
    )
    radius = (
        (max(np.linalg.norm(p[1]) + max(p[2]) for p in parts[0]) + 0.002)
        if parts
        else 0
    )

    def penalty_for(candidates, other, a, b, h):
        penalty = np.zeros(len(candidates))
        for fraction in (0, 0.25, 0.5, 0.75):
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
            penalty += np.sum(np.maximum(0, -margin) ** 2, axis=1) * 1e8
        penalty += np.sum((candidates - points[a : b + 1, h]) ** 2, axis=(1, 2)) * 0.01
        return penalty

    for _ in range(3):
        improved = False
        for a, b, h in runs:
            base = points[a : b + 1, h]
            other = result[a : b + 1, 1 - h]
            if poses is None:
                if (
                    np.linalg.norm(result[a : b + 1, h] - other, axis=-1).min()
                    >= 2 * pad_radius + gap + 0.008
                ):
                    continue
            elif penalty_for(result[a : b + 1, h][None], other, a, b, h)[0] < 1e-7:
                continue
            t = np.linspace(0, 1, b - a + 1)
            ramp = np.minimum(
                1, np.minimum(np.arange(b - a + 1), np.arange(b - a, -1, -1)) / 4
            )
            ramp = ramp**3 * (10 - 15 * ramp + 6 * ramp**2)
            shapes = np.stack((np.sin(t * np.pi) ** 2, ramp))
            candidates = (
                base[None, None, :, :]
                + offsets[None, :, None, :] * shapes[:, None, :, None]
            ).reshape(-1, b - a + 1, 3)
            candidates = np.concatenate((result[a : b + 1, h][None], candidates))
            speed = np.linalg.norm(np.diff(candidates, axis=1), axis=-1) * 60
            if poses is not None:
                speed += (
                    radius
                    * np.radians(
                        np.linalg.norm(np.diff(poses[a : b + 1, h], axis=0), axis=-1)
                        * 60
                    )[None, :]
                )
            valid = speed.max(axis=1) <= max_speed + 1e-5
            penalty = penalty_for(candidates, other, a, b, h)
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
):
    points = np.asarray(points, dtype=float)
    active = np.asarray(active, dtype=bool)
    # Slow wrists need wider key intervals to traverse the angular catalogue;
    # the final 60 Hz interpolation still respects each frame's turn budget.
    stride = max(stride, math.ceil(30 * 60 / max(angular_limit, 1)))
    original = points.copy()
    points, lifts = air_paths(points, active, max_speed, gap=gap)
    count = len(points)
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
    ra = np.array([max(p[2]) for p in parts[0]])
    rb = np.array([max(p[2]) for p in parts[1]])

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
        return margin, floor

    cache = {}

    def cost(f):
        if f not in cache:
            m, floor = spatial(f)
            cache[f] = (
                np.maximum(0, (gap - m) * 1000) ** 2
                + 4 * np.maximum(0, -floor * 1000 - 0.05) ** 2
            )
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
            penalty = (
                np.maximum(0, (gap - m) * 1000) ** 2
                + 4 * np.maximum(0, -floor * 1000 - 0.05) ** 2
            )
            max_penalty = np.maximum(max_penalty, penalty)
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
    points, body_lifts = air_paths(
        points, active, max_speed, gap=gap, poses=poses, parts=parts
    )
    lifts += body_lifts
    report = diagnostics(points, poses, parts, gap)
    report.update(
        solver=VERSION,
        air_overpasses=lifts,
        maximum_contact_drift_m=float(
            np.linalg.norm((points - original)[active], axis=-1).max(initial=0)
        ),
    )
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

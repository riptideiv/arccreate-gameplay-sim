"""Global homotopy search for free-hand travel, with pinned contact samples.

The directed left-to-right line is unwrapped from the idle +X axis. A route
cannot erase a turn by lifting, returning home, or crossing the angle branch
cut. The DAG carries integer turns and chooses longer physical routes when
needed. Its edges never overlap, so each edge's other hand is the baseline.
"""

import math
from dataclasses import dataclass
import numpy as np

RATE = 60
POLE_RADIUS = 0.001
UNWIND_RADIUS = 0.018


class WindingError(ValueError):
    def __init__(self, frame, limit, reason="no feasible unwind route"):
        super().__init__(
            f"Arm winding has {reason} at {frame/RATE:.3f} s (limit ±{limit:g}°)."
        )
        self.failure = {
            "frame": int(frame),
            "reason": "arm winding",
            "detail": reason,
            "limit_deg": limit,
        }


def _angles(v):
    raw = np.arctan2(v[:, 1], v[:, 0])
    # The reference can be singular, but no accepted edge may go through it.
    for i in np.flatnonzero(np.linalg.norm(v, axis=1) < 1e-12):
        raw[i] = raw[i - 1] if i else 0
    return np.unwrap(raw)


def _edge_clear(v):
    a = v[:-1]
    delta = v[1:] - a
    den = np.sum(delta * delta, axis=1)
    t = np.divide(
        -np.sum(a * delta, axis=1), den, out=np.zeros(len(a)), where=den > 1e-24
    )
    closest = a + np.clip(t, 0, 1)[:, None] * delta
    clear = np.linalg.norm(closest, axis=1) > 1e-10
    # Coincident scoring targets may touch and bounce without changing order.
    # Keep the incoming bearing there; a passage to the opposite side remains
    # forbidden. In particular, coincidence never resets the accumulated turn.
    for i in np.flatnonzero(~clear):
        left = i
        while left >= 0 and np.linalg.norm(v[left]) <= 1e-10:
            left -= 1
        right = i + 1
        while right < len(v) and np.linalg.norm(v[right]) <= 1e-10:
            right += 1
        if left >= 0 and right < len(v):
            a0, b0 = v[left], v[right]
            clear[i] = np.dot(a0, b0) > 0
    return clear


def measure(points, limit_deg=260, anchor_offsets=None):
    points = np.asarray(points)
    anchors = points if anchor_offsets is None else points + anchor_offsets
    v = anchors[:, 1, :2] - anchors[:, 0, :2]
    angles = np.degrees(_angles(v))
    clear = _edge_clear(v)
    bad = np.flatnonzero((abs(angles) > limit_deg + 1e-5) | np.r_[False, ~clear])
    return {
        "limit_deg": limit_deg,
        "minimum_deg": float(angles.min(initial=0)),
        "maximum_deg": float(angles.max(initial=0)),
        "final_deg": float(angles[-1]),
        "first_invalid_frame": int(bad[0]) if len(bad) else None,
        "pole_crossings": int(np.sum(~clear)),
        "angles_deg": angles,
    }


@dataclass
class Route:
    start: int
    end: int
    hand: int
    path: np.ndarray
    turns: int
    low: float
    high: float
    cost: float
    extra_distance: float


def _orbit(a, b, delta, radius, u):
    """Tangent/arc/tangent around a finite disc, parameterised by arc length."""
    r0, r1 = np.linalg.norm(a), np.linalg.norm(b)
    theta = math.atan2(a[1], a[0])
    sign = 1 if delta >= 0 else -1
    if min(r0, r1) < radius:
        # Endpoints may be close, but the actual unwind must leave the disc.
        x = abs(r0 - radius)
        z = abs(r1 - radius)
        sweep = abs(delta)
        length = x + radius * sweep + z
        s = u * length
        out = np.empty((len(u), 2))
        for k, value in enumerate(s):
            if value < x and x > 1e-12:
                angle = theta
                r = r0 + (radius - r0) * value / x
            elif value < x + radius * sweep and sweep > 1e-12:
                angle = theta + sign * (value - x) / radius
                r = radius
            else:
                angle = theta + delta
                r = radius + (r1 - radius) * (value - x - radius * sweep) / max(
                    z, 1e-12
                )
            out[k] = (r * math.cos(angle), r * math.sin(angle))
        return out
    alpha0, alpha1 = math.acos(radius / r0), math.acos(radius / r1)
    sweep = abs(delta) - alpha0 - alpha1
    if sweep < 0:
        return None
    enter = theta + sign * alpha0
    leave = theta + delta - sign * alpha1
    p = radius * np.array([math.cos(enter), math.sin(enter)])
    q = radius * np.array([math.cos(leave), math.sin(leave)])
    x, z = math.dist(a, p), math.dist(q, b)
    length = x + radius * sweep + z
    s = u * length
    out = np.empty((len(u), 2))
    first = s < x
    last = s > x + radius * sweep
    middle = ~(first | last)
    out[first] = a + (p - a) * (s[first] / max(x, 1e-12))[:, None]
    ph = enter + sign * (s[middle] - x) / radius
    out[middle] = radius * np.stack((np.cos(ph), np.sin(ph)), axis=-1)
    out[last] = q + (b - q) * ((s[last] - x - radius * sweep) / max(z, 1e-12))[:, None]
    out[0] = a
    out[-1] = b
    return out


def route(
    points,
    active,
    *,
    limit_deg=260,
    max_speed=3,
    comfort_speed=0.25,
    distance_weight=5,
    lift=0.012,
    anchor_offsets=None,
    surface_speed=None,
    geometry_cost=None,
    idle_speed=1,
):
    """Shortest bounded-winding schedule over free-gap homotopy alternatives.

    At most two winding branches survive at a time for limits below 360°.
    Every route is checked at 60 Hz and along each interpolated line segment.
    """
    points = np.asarray(points, dtype=float)
    active = np.asarray(active, dtype=bool)
    if not math.isfinite(limit_deg) or not 0 < limit_deg < 360:
        raise ValueError(
            "Arm winding limit must be finite and between 0 and 360 degrees."
        )
    limit = math.radians(limit_deg)
    count = len(points)
    offsets = (
        np.zeros_like(points) if anchor_offsets is None else np.asarray(anchor_offsets)
    )
    anchors = points + offsets
    extra_speed = (
        np.zeros((count - 1, 2)) if surface_speed is None else np.asarray(surface_speed)
    )
    relative = anchors[:, 1, :2] - anchors[:, 0, :2]
    angles = _angles(relative)
    clear = _edge_clear(relative)
    speed = np.linalg.norm(np.diff(points, axis=0), axis=-1) * RATE
    frame_cost = (
        distance_weight * np.sum(speed, axis=1) / RATE
        + np.sum(np.maximum(0, speed - comfort_speed) ** 2, axis=1) / RATE
    )
    baseline_movement_cost = float(frame_cost.sum())
    if geometry_cost is not None:
        frame_cost += geometry_cost(points, None)[:-1] / RATE
    candidates = {}
    generated = 0
    for h in range(2):
        edges = np.diff(np.r_[False, ~active[:, h], False].astype(int))
        for first, last in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            a = max(0, first - 1)
            b = min(count - 1, last)
            if b - a < 2:
                continue
            theirs = anchors[a : b + 1, 1 - h]
            own = points[a : b + 1, h]
            own_anchors = anchors[a : b + 1, h]
            v0 = own_anchors[0, :2] - theirs[0, :2]
            v1 = own_anchors[-1, :2] - theirs[-1, :2]
            if min(np.linalg.norm(v0), np.linalg.norm(v1)) < POLE_RADIUS:
                continue
            short = math.atan2(v0[0] * v1[1] - v0[1] * v1[0], float(np.dot(v0, v1)))
            t = np.linspace(0, 1, b - a + 1)
            eased = t**3 * (10 - 15 * t + 6 * t * t)
            baseline_distance = np.linalg.norm(np.diff(own, axis=0), axis=1).sum()
            # Keep the cheapest physical route for each homotopy and angular
            # envelope; a narrower envelope can remain useful at another branch.
            local = {}
            for turns in (-1, 0, 1):
                delta = short + turns * 2 * math.pi
                for radius in (UNWIND_RADIUS, 0.024, 0.032, 0.045):
                    for linear in (0, 0.5, 1):
                        u = (1 - linear) * eased + linear * t
                        xy = _orbit(v0, v1, delta, radius, u)
                        if xy is None:
                            continue
                        for height in sorted(
                            set((lift, max(lift, 0.03), max(lift, 0.055)))
                        ):
                            path = own.copy()
                            path[:, :2] = theirs[:, :2] + xy - offsets[a : b + 1, h, :2]
                            path[:, 2] = np.maximum(
                                path[:, 2], 0.006 + height * np.sin(np.pi * t) ** 2
                            )
                            path[[0, -1]] = own[[0, -1]]
                            velocities = (
                                np.linalg.norm(np.diff(path, axis=0), axis=1) * RATE
                            )
                            if (velocities + extra_speed[a:b, h]).max(
                                initial=0
                            ) > max_speed + 1e-6:
                                continue
                            own_line = path[:, :2] + offsets[a : b + 1, h, :2]
                            v = (
                                (theirs[:, :2] - own_line)
                                if h == 0
                                else (own_line - theirs[:, :2])
                            )
                            if not _edge_clear(v).all():
                                continue
                            phi = _angles(v)
                            phi += angles[a] - phi[0]
                            branch = int(round((phi[-1] - angles[b]) / (2 * math.pi)))
                            if abs(phi[-1] - angles[b] - branch * 2 * math.pi) > 1e-6:
                                continue
                            if (
                                branch == 0
                                and velocities.max(initial=0)
                                > min(
                                    max_speed,
                                    max(idle_speed, speed[a:b, h].max(initial=0)),
                                )
                                + 1e-6
                            ):
                                continue
                            extra = (
                                np.linalg.norm(np.diff(path, axis=0), axis=1).sum()
                                - baseline_distance
                            )
                            cost = (
                                distance_weight * np.sum(velocities) / RATE
                                + np.sum(np.maximum(0, velocities - comfort_speed) ** 2)
                                / RATE
                            )
                            other_speed = speed[a:b, 1 - h]
                            cost += (
                                distance_weight * np.sum(other_speed) / RATE
                                + np.sum(
                                    np.maximum(0, other_speed - comfort_speed) ** 2
                                )
                                / RATE
                            )
                            if geometry_cost is not None:
                                pair = points[a : b + 1].copy()
                                pair[:, h] = path
                                cost += (
                                    float(np.sum(geometry_cost(pair, (a, b)))) / RATE
                                )
                            edge = Route(
                                a,
                                b,
                                h,
                                path,
                                branch,
                                float(phi.min()),
                                float(phi.max()),
                                float(cost),
                                float(extra),
                            )
                            key = (branch, round(edge.low, 5), round(edge.high, 5))
                            if key not in local or cost < local[key].cost:
                                local[key] = edge
            if local:
                candidates.setdefault(a, []).extend(local.values())
                generated += len(local)
    # Exact DAG search over these finite free-gap candidates. No modulo-state
    # merging: the same endpoints with a different turn count stay distinct.
    states = [{} for _ in range(count)]
    states[0][0] = (0.0, None)
    furthest = 0
    for f in range(count):
        for branch, (cost, node) in list(states[f].items()):
            offset = branch * 2 * math.pi
            if abs(angles[f] + offset) > limit + 1e-9:
                continue
            furthest = max(furthest, f)
            if (
                f + 1 < count
                and clear[f]
                and np.max(speed[f] + extra_speed[f]) <= max_speed + 1e-6
                and abs(angles[f + 1] + offset) <= limit + 1e-9
            ):
                total = cost
                if branch not in states[f + 1] or total < states[f + 1][branch][0]:
                    states[f + 1][branch] = (total, (node, None))
            for edge in candidates.get(f, ()):
                if (
                    edge.low + offset < -limit - 1e-9
                    or edge.high + offset > limit + 1e-9
                ):
                    continue
                new = branch + edge.turns
                total = (
                    cost
                    + max(
                        0.0, edge.cost - float(frame_cost[edge.start : edge.end].sum())
                    )
                    + 1e-8
                )
                if new not in states[edge.end] or total < states[edge.end][new][0]:
                    states[edge.end][new] = (total, (node, edge))
    if not states[-1]:
        raise WindingError(furthest + 1, limit_deg)
    cost, node = min(states[-1].values(), key=lambda item: item[0])
    chosen = []
    while node is not None:
        node, edge = node
        if edge is not None:
            chosen.append(edge)
    result = points.copy()
    events = []
    for edge in reversed(chosen):
        result[edge.start : edge.end + 1, edge.hand] = edge.path
        events.append(
            {
                "hand": ("left", "right")[edge.hand],
                "start_s": edge.start / RATE,
                "end_s": edge.end / RATE,
                "turns_relative_to_direct": edge.turns,
                "extra_distance_m": edge.extra_distance,
            }
        )
    np.testing.assert_array_equal(result[active], points[active])
    measured = measure(result, limit_deg, offsets)
    if measured["first_invalid_frame"] is not None:
        raise WindingError(
            measured["first_invalid_frame"], limit_deg, "an invalid routed edge"
        )
    velocities = np.linalg.norm(np.diff(result, axis=0), axis=-1) * RATE
    measured["extra_movement_cost"] = float(
        distance_weight * velocities.sum() / RATE
        + np.sum(np.maximum(0, velocities - comfort_speed) ** 2) / RATE
        - baseline_movement_cost
    )
    measured.pop("angles_deg")
    measured["extra_distance_m"] = float(sum(e.extra_distance for e in chosen))
    measured.update(
        enabled=True, unwinds=events, candidate_routes=generated, cost=float(cost)
    )
    return result, measured


def wrist_offsets(poses, parts):
    from hand_pose import proxies

    result = []
    for h in range(2):
        centres, _ = proxies(np.asarray(poses)[:, h], parts[h])
        result.append(
            centres[:, next(i for i, p in enumerate(parts[h]) if p[0] == "cuff")]
        )
    return np.stack(result, axis=1)


def clearance_cost(poses, parts, gap):
    """Cache posed geometry, including renderer-matching between-frame samples."""
    from hand_pose import proxies

    poses = np.asarray(poses)
    catalogue = []
    radii = [np.array([max(p[2]) for p in hand]) for hand in parts]
    for fraction in (0, 0.25, 0.5, 0.75):
        q = poses[:-1] * (1 - fraction) + poses[1:] * fraction
        centres = [proxies(q[:, h], parts[h])[0] for h in range(2)]
        catalogue.append((fraction, centres[1][:, None] - centres[0][:, :, None]))

    def cost(points, span):
        a, b = (0, len(points) - 1) if span is None else span
        penalties = np.zeros(len(points) - 1)
        for fraction, offsets in catalogue:
            p = points[:-1] * (1 - fraction) + points[1:] * fraction
            delta = offsets[a:b] + (p[:, 1] - p[:, 0])[:, None, None]
            margin = (
                np.linalg.norm(delta, axis=-1) - radii[0][:, None] - radii[1][None]
            ).min(axis=(1, 2))
            penalties = np.maximum(
                penalties, np.maximum(0, (gap - margin) * 1000) ** 2 * 4
            )
        return np.r_[penalties, 0.0]

    return cost


def solve(
    points,
    active,
    poses,
    parts,
    *,
    limit_deg=260,
    max_speed=3,
    lift=0.012,
    comfort_speed=0.25,
    gap=0.001,
    idle_speed=1,
):
    """Apply winding to actual arm-attachment positions after glove posing."""
    offsets = wrist_offsets(poses, parts)
    radius = max(np.linalg.norm(p[1]) + max(p[2]) for p in parts[0]) + 0.002
    angular = np.linalg.norm(np.diff(poses, axis=0), axis=-1) * RATE
    last = None
    geometry = clearance_cost(poses, parts, gap)
    for reserve in (0, 2, 5):
        try:
            result, report = route(
                points,
                active,
                limit_deg=limit_deg - reserve,
                max_speed=max_speed,
                lift=lift,
                comfort_speed=comfort_speed,
                anchor_offsets=offsets,
                surface_speed=radius * np.radians(angular),
                geometry_cost=geometry,
                idle_speed=idle_speed,
            )
            report.update(verify_wrists(result, poses, parts, limit_deg))
            report["solver_limit_deg"] = limit_deg - reserve
            return result, report
        except WindingError as error:
            last = error
    raise WindingError(
        last.failure["frame"], limit_deg, last.failure["detail"]
    ) from last


def verify_wrists(points, poses, parts, limit_deg=260, subdivisions=4):
    """Bound the true interpolated cuff curve, not just its 60 Hz endpoints.

    Rotation's chord error is bounded by r*(|pitch'|+|yaw'|)^2/8.
    Applying it to each subinterval certifies that no hidden branch crossing
    or limit excursion lies between the rendered samples.
    """
    points = np.asarray(points)
    poses = np.asarray(poses)
    count = len(points)
    t = np.arange((count - 1) * subdivisions + 1) / subdivisions
    i = np.minimum(t.astype(int), count - 2)
    u = (t - i)[:, None, None]
    p = points[i] * (1 - u) + points[i + 1] * u
    q = poses[i] * (1 - u) + poses[i + 1] * u
    anchors = p + wrist_offsets(q, parts)
    v = anchors[:, 1, :2] - anchors[:, 0, :2]
    theta = np.degrees(_angles(v))
    delta = v[1:] - v[:-1]
    den = np.sum(delta * delta, axis=1)
    at = np.divide(
        -np.sum(v[:-1] * delta, axis=1),
        den,
        out=np.zeros(len(delta)),
        where=den > 1e-24,
    )
    distance = np.linalg.norm(v[:-1] + np.clip(at, 0, 1)[:, None] * delta, axis=1)
    radius = np.array(
        [np.linalg.norm(next(p[1] for p in hand if p[0] == "cuff")) for hand in parts]
    )
    turn = np.sum(abs(np.radians(np.diff(poses, axis=0))), axis=-1)
    error = np.repeat(
        np.sum(radius * turn**2, axis=1) / (8 * subdivisions**2), subdivisions
    )
    uncertainty = np.degrees(
        np.arcsin(
            np.minimum(
                1,
                np.divide(
                    error,
                    distance,
                    out=np.full(len(error), 2.0),
                    where=distance > 1e-15,
                ),
            )
        )
    )
    low = np.minimum(theta[:-1], theta[1:]) - uncertainty
    high = np.maximum(theta[:-1], theta[1:]) + uncertainty
    bad = np.flatnonzero(
        (low < -limit_deg - 1e-5)
        | (high > limit_deg + 1e-5)
        | (distance <= error + 1e-12)
    )
    if len(bad):
        raise WindingError(
            (int(bad[0]) + 1) / subdivisions,
            limit_deg,
            "an interpolated cuff limit violation",
        )
    return {
        "limit_deg": limit_deg,
        "minimum_deg": float(low.min(initial=0)),
        "maximum_deg": float(high.max(initial=0)),
        "final_deg": float(theta[-1]),
        "anchor": "cuff",
        "interpolation_certified": True,
        "subdivisions": subdivisions,
    }

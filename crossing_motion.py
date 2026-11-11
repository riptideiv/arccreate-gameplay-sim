"""Check the planner's arm order against the posed arm links."""

import math

import numpy as np

from motion_paths import RATE


class CrossingMotionError(ValueError):
    def __init__(self, frame, detail):
        super().__init__(f"Arm crossing at {frame/RATE:.3f} s: {detail}.")
        self.failure = {"frame": int(frame), "reason": "arm crossing", "detail": detail}


def validate(points, instructions):
    routes = instructions["routes"]
    if instructions["last_frame"] != len(points) - 1:
        raise ValueError("Arm crossing instructions have the wrong frame range")
    previous_end = 0.
    for route in routes:
        start, end = route["start_frame"], route["end_frame"]
        if (route["top"] not in ("left", "right") or not math.isfinite(start)
                or not math.isfinite(end) or not previous_end <= start < end <= len(points)-1):
            raise ValueError("Invalid arm crossing instruction")
        previous_end = end
    # Replay the same event boundaries with the recorded choices. This also
    # catches plans that insert a new top hand without ever uncrossing.
    from arc_hand_order.planning.arm_crossings import SIDES, passes
    from motion_paths import home_position

    left, right = points.transpose(1, 0, 2)
    trace = (left, right)
    shift = 0
    if left[0, 0] - right[0, 0] > 1e-5:
        trace = tuple(np.vstack((home_position(side, 0), path))
                      for side, path in zip(SIDES, trace))
        shift = 1
    index, active = 0, None
    for _, frame, entering, tops in passes(*trace, 0, False):
        frame = max(0, frame - shift)
        if entering:
            if index >= len(routes) or active is not None:
                raise CrossingMotionError(frame, "missing crossing instruction")
            active = routes[index]
            if active["top"] not in tops or abs(active["start_frame"] - frame) > .015:
                raise CrossingMotionError(frame, "the crossing entry disagrees with the touch path")
        else:
            if (active is None or active["top"] not in tops
                    or abs(active["end_frame"] - frame) > .015):
                raise CrossingMotionError(frame, "the arms cannot change top hand before uncrossing")
            active = None
            index += 1
    if active is not None or index != len(routes):
        raise CrossingMotionError(len(points)-1, "the arm instructions contain an unmatched crossing")


def layers_at(frames, routes):
    layers = np.zeros_like(frames, dtype=np.int8)
    for route in routes:
        layers[(frames >= route["start_frame"]) & (frames < route["end_frame"])] = (
            1 if route["top"] == "left" else -1
        )
    return layers


def elbow_bias_at(frames, routes):
    """Give the planned upper arm time to raise its elbow before the pass."""
    bias = np.zeros((*frames.shape, 2))
    for route in routes:
        distance = np.maximum(route["start_frame"] - frames, frames - route["end_frame"])
        ramp = np.clip(1 - distance / 8, 0, 1)
        bias[..., 0 if route["top"] == "left" else 1] += ramp**2 * (3 - 2*ramp)
    return np.clip(bias, 0, 1)


def layer_margin(centres, angles, parts, layers, elbow_bias=None):
    """Signed clearance at projected intersections of the arm links."""
    from hand_pose import FOREARM_RADIUS, SHOULDERS, TIP, UPPER_ARM_RADIUS, arm_geometry, bases

    wrists, elbows = arm_geometry(centres, bases(angles)[..., 1], parts, elbow_bias)
    shoulders = np.broadcast_to(SHOULDERS, wrists.shape)
    pads = np.stack([hand[..., 0, :] for hand in centres], axis=-2)
    start = np.stack((shoulders, elbows, wrists), axis=-2)
    stop = np.stack((elbows, wrists, pads), axis=-2)
    # Each left arm segment against each right arm segment.
    a, b = start[..., 0, :, None, :], stop[..., 0, :, None, :]
    c, d = start[..., 1, None, :, :], stop[..., 1, None, :, :]
    u, v, w = b - a, d - c, c - a

    def cross(x, y):
        return x[..., 0] * y[..., 1] - x[..., 1] * y[..., 0]

    determinant = cross(u, v)
    parallel = abs(determinant) <= 1e-12
    denominator = np.where(parallel, 1, determinant)
    t, s = cross(w, v) / denominator, cross(w, u) / denominator
    intersects = ~parallel & (t >= 0) & (t <= 1) & (s >= 0) & (s <= 1)
    radii = np.array([UPPER_ARM_RADIUS, FOREARM_RADIUS, .017])
    taper = np.array([0., 0., .017 - TIP])
    order = np.asarray(layers)[..., None, None]

    def margin_at(t, s):
        vertical = (a[..., 2] + t * u[..., 2]) - (c[..., 2] + s * v[..., 2])
        radius = radii[:, None] - t * taper[:, None] + radii[None, :] - s * taper[None, :]
        return vertical * order - radius

    margin = np.where(intersects, margin_at(t, s), np.inf)
    if np.any(parallel & (order != 0)):
        # With collinear projections the worst height difference is at an
        # endpoint of the overlap, including vertical segments seen end-on.
        def project(p, start, direction):
            delta = (p - start)[..., :2]
            xy = direction[..., :2]
            den = np.sum(xy**2, axis=-1)
            at = np.clip(np.sum(delta * xy, axis=-1) / np.maximum(den, 1e-16), 0, 1)
            overlap = np.linalg.norm(delta - at[..., None] * xy, axis=-1) <= 1e-6
            return at, parallel & overlap

        for endpoint in (0, 1):
            s, overlaps = project(a + endpoint * u, c, v)
            margin = np.minimum(margin, np.where(overlaps, margin_at(endpoint, s), np.inf))
            t, overlaps = project(c + endpoint * v, a, u)
            margin = np.minimum(margin, np.where(overlaps, margin_at(t, endpoint), np.inf))
    return np.where(order != 0, margin, np.inf).min(axis=(-2, -1))


def verify(points, angles, parts, routes, gap, subdivisions=8):
    from hand_pose import proxies

    frames = np.arange((len(points)-1) * subdivisions + 1) / subdivisions
    layers = layers_at(frames, routes)
    frames, layers = frames[layers != 0], layers[layers != 0]
    i = np.minimum(frames.astype(int), len(points)-2)
    u = (frames - i)[:, None, None]
    p = points[i] * (1-u) + points[i+1] * u
    q = angles[i] * (1-u) + angles[i+1] * u
    centres = [proxies(q[:, h], parts[h])[0] + p[:, h, None, :] for h in range(2)]
    margin = layer_margin(centres, q, parts, layers, elbow_bias_at(frames, routes))
    bad = np.flatnonzero(margin < gap - 1e-5)
    if len(bad):
        k = bad[0]
        top = "left" if layers[k] == 1 else "right"
        raise CrossingMotionError(frames[k], f"cannot keep the {top} arm over with 3D clearance")
    minimum = margin.min(initial=np.inf)
    return {
        "routes": len(routes),
        "subdivisions": subdivisions,
        "minimum_arm_layer_clearance_mm": float(minimum*1000) if np.isfinite(minimum) else None,
        "checked_intersection_samples": int(np.isfinite(margin).sum()),
        "verified": True,
    }

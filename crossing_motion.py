"""Check the planner's arm order against the posed arm links."""

import math

import numpy as np

from motion_paths import RATE


class CrossingMotionError(ValueError):
    def __init__(self, frame, detail):
        super().__init__(f"Arm crossing at {frame/RATE:.3f} s: {detail}.")
        self.failure = {"frame": int(frame), "reason": "arm crossing", "detail": detail}


def validate(points, instructions, locked=None):
    routes = instructions["routes"]
    if instructions["last_frame"] != len(points) - 1:
        raise ValueError("Arm crossing instructions have the wrong frame range")
    last_frame = len(points) - 1
    previous_end = 0.
    for index, route in enumerate(routes):
        start, end = route["start_frame"], route["end_frame"]
        valid_end = (index == len(routes) - 1 and start <= last_frame if end is None
                     else math.isfinite(end) and start < end <= last_frame)
        if (route["top"] not in ("left", "right") or not math.isfinite(start)
                or start < previous_end or not valid_end):
            raise ValueError("Invalid arm crossing instruction")
        previous_end = end
    # Replay the same event boundaries with the recorded choices. This also
    # catches plans that insert a new top hand without ever uncrossing.
    from arc_hand_order.planning.arm_crossings import SIDES, locked_overpass, passes
    from motion_paths import home_position

    left, right = points.transpose(1, 0, 2)
    if locked is not None:
        for route in routes:
            start, end = route["start_frame"], route["end_frame"]
            if end is None:
                end = last_frame
            if locked_overpass(left, right, locked[:, SIDES.index(route["top"])], start, end):
                raise CrossingMotionError(start, f"the {route['top']} fingertip is locked down during an overpass")
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
            if (active is None or active["top"] not in tops or active["end_frame"] is None
                    or abs(active["end_frame"] - frame) > .015):
                raise CrossingMotionError(frame, "the arms cannot change top hand before uncrossing")
            active = None
            index += 1
    if active is not None and active["end_frame"] is None:
        index += 1
    if index != len(routes):
        raise CrossingMotionError(last_frame, "the arm instructions contain an unmatched crossing")


def layers_at(frames, routes):
    layers = np.zeros_like(frames, dtype=np.int8)
    for route in routes:
        end = route["end_frame"]
        within = frames >= route["start_frame"]
        if end is not None:
            within &= frames < end
        layers[within] = 1 if route["top"] == "left" else -1
    return layers


def _hand_over_margin(centres, radii, start, stop, arm_radius):
    """Signed clearance of the glove spheres above the lower arm capsules."""
    axis = (stop - start)[..., None, :, :]
    delta = centres[..., :, None, :] - start[..., None, :, :]
    size = np.asarray(radii)[..., None] + arm_radius
    u = axis[..., :2]
    w = delta[..., :2]
    length2 = np.sum(u*u, axis=-1)
    projection = np.sum(w*u, axis=-1) / np.maximum(length2, 1e-16)
    nearest = w - np.clip(projection, 0, 1)[..., None] * u
    overlaps = np.sum(nearest**2, axis=-1) < size**2

    # Use the highest point of the lower segment inside the projected
    # footprint to establish the side. Test 3D distance separately.
    offset = w - projection[..., None] * u
    half = np.sqrt(np.maximum(0, size**2 - np.sum(offset**2, axis=-1))
                   / np.maximum(length2, 1e-16))
    first, last = np.clip(projection-half, 0, 1), np.clip(projection+half, 0, 1)
    first, last = np.where(length2 < 1e-16, 0, first), np.where(length2 < 1e-16, 1, last)
    dz = axis[..., 2]
    height = delta[..., 2] - np.maximum(first*dz, last*dz)
    t = np.clip(np.sum(delta*axis, axis=-1) / np.maximum(np.sum(axis**2, axis=-1), 1e-16), 0, 1)
    clearance = np.linalg.norm(delta - t[..., None]*axis, axis=-1) - size
    return np.where(overlaps, np.minimum(height, clearance), np.inf).min(axis=(-2,-1))


def _link_margin(start, stop, layers, arm_radius, tip_radius):
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
    radii = np.array([arm_radius, arm_radius, .017])
    taper = np.array([0., 0., .017 - tip_radius])
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


def layer_clearances(centres, angles, parts, layers, swivels=None):
    from hand_pose import ARM_RADIUS, SHOULDERS, TIP, arm_geometry, bases

    wrists, elbows = arm_geometry(centres, bases(angles)[..., 1], parts, swivels)
    shoulders = np.broadcast_to(SHOULDERS, wrists.shape)
    pads = np.stack([hand[..., 0, :] for hand in centres], axis=-2)
    start = np.stack((shoulders, elbows, wrists), axis=-2)
    stop = np.stack((elbows, wrists, pads), axis=-2)
    arm = _link_margin(start, stop, layers, ARM_RADIUS, TIP)
    hand = np.full_like(arm, np.inf)
    if parts is not None:
        for h in range(2):
            top = np.asarray(layers) == (1 if h == 0 else -1)
            if top.any():
                lower = 1-h
                radii = [max(p[2]) for p in parts[h]]
                margin = _hand_over_margin(centres[h], radii, start[..., lower, :2, :],
                                           stop[..., lower, :2, :], ARM_RADIUS)
                hand = np.where(top, margin, hand)
        pad_delta = pads[..., 0, :] - pads[..., 1, :]
        together = np.sum(pad_delta[..., :2]**2, axis=-1) < (2*TIP)**2
        pad_margin = np.minimum(pad_delta[..., 2]*layers, np.linalg.norm(pad_delta, axis=-1) - 2*TIP)
        hand = np.minimum(hand, np.where(together & (np.asarray(layers) != 0), pad_margin, np.inf))
    return arm, hand


def layer_margin(centres, angles, parts, layers, swivels=None):
    return np.minimum(*layer_clearances(centres, angles, parts, layers, swivels))


def sample_swivels(swivels, frames):
    if swivels is None:
        return None
    i = np.minimum(frames.astype(int), len(swivels)-2)
    u = (frames - i)[..., None]
    return swivels[i] * (1-u) + swivels[i+1] * u


def boundary_subframes(routes):
    """Sample the entire frame containing each fractional crossing boundary."""
    bounds = {math.floor(route[key]) for route in routes
              for key in ("start_frame", "end_frame") if route[key] is not None}
    return np.array(sorted(frame + k/8 for frame in bounds for k in range(1, 8)))


def solve_swivels(points, poses, parts, routes, gap, *, progress=None):
    """Blend bounded corrections around fixed elbow poles across the take."""
    from hand_pose import arm_metrics, arm_posture_cost, bases, proxies

    stride, radius = 24, 32
    windows = []
    coverage = np.zeros(len(points))
    for centre in range(0, len(points)+stride, stride):
        start = max(0, centre-radius+1)
        stop = min(len(points), centre+radius)
        samples = np.arange(start, stop)
        u = 1 - abs(samples-centre)/radius
        weight = u*u*(3 - 2*u)
        windows.append((start, stop, samples, weight))
        coverage[start:stop] += weight
    coverage = np.maximum(1, coverage)
    degrees = np.linspace(-50, 50, 7)
    catalogue = np.array([(left, right) for left in degrees for right in degrees])
    swivels = np.zeros((len(points), 2))
    selected = np.zeros((len(windows), 2))
    boundaries = boundary_subframes(routes)
    indices = list(range(len(windows)))
    # Revisit the keys after their neighbours have been placed.
    sweep = indices + indices[::-1]
    if progress:
        progress("pose_elbows", 0, len(sweep))
    for completed, index in enumerate(sweep, 1):
        start, stop, samples, weight = windows[index]
        weight = weight / coverage[start:stop]
        swivels[start:stop] -= weight[:, None] * selected[index]
        boundary = boundaries[(start <= boundaries) & (boundaries < stop-1)]
        frames = np.unique(np.r_[np.arange(start*2, (stop-1)*2 + 1)/2, boundary])
        layers = layers_at(frames, routes)
        i = np.minimum(frames.astype(int), len(points)-2)
        u = (frames - i)[:, None, None]
        p = points[i] * (1-u) + points[i+1] * u
        q = poses[i] * (1-u) + poses[i+1] * u
        centres = [proxies(q[:, h], parts[h])[0] + p[:, h, None, :] for h in range(2)]
        w = np.interp(frames, samples, weight)
        current = sample_swivels(swivels, frames)
        candidates = current + w[None, :, None] * catalogue[:, None, :]
        shape = (len(catalogue), *centres[0].shape)
        expanded = tuple(np.broadcast_to(hand, shape) for hand in centres)
        margin = layer_margin(expanded, q, parts, layers, candidates)
        shortfall = np.maximum(0, gap-margin) * 1000
        cost = shortfall.max(axis=1)**2 * 300 + np.mean(shortfall**2, axis=1) * 100
        metrics = arm_metrics(expanded, bases(q)[..., 1], parts, candidates)
        # Keep the arms apart even when their projected touch order is uncrossed.
        overlap = np.maximum(0, gap-metrics[0]) * 1000
        cost += overlap.max(axis=1)**2 * 50 + np.mean(overlap**2, axis=1) * 100
        cost += arm_posture_cost(metrics, gap).mean(axis=1)
        cost += .03 * np.sum(catalogue**2, axis=1)
        chosen = catalogue[int(np.argmin(cost))]
        selected[index] = chosen
        swivels[start:stop] += weight[:, None] * chosen
        if progress:
            progress("pose_elbows", completed, len(sweep))
    return swivels


def verify(points, angles, parts, routes, gap, subdivisions=8, *, swivels=None, allow_clipping=False):
    from hand_pose import proxies

    frames = np.arange((len(points)-1) * subdivisions + 1) / subdivisions
    layers = layers_at(frames, routes)
    frames, layers = frames[layers != 0], layers[layers != 0]
    i = np.minimum(frames.astype(int), len(points)-2)
    u = (frames - i)[:, None, None]
    p = points[i] * (1-u) + points[i+1] * u
    q = angles[i] * (1-u) + angles[i+1] * u
    centres = [proxies(q[:, h], parts[h])[0] + p[:, h, None, :] for h in range(2)]
    arm, hand = layer_clearances(centres, q, parts, layers, sample_swivels(swivels, frames))
    margin = np.minimum(arm, hand)
    bad = np.flatnonzero(margin < gap - 1e-5)
    failure = None
    if len(bad):
        k = bad[0]
        top = "left" if layers[k] == 1 else "right"
        detail = (f"the {top} hand cannot clear the other arm from above"
                  if hand[k] < gap - 1e-5 else f"cannot keep the {top} arm over with 3D clearance")
        error = CrossingMotionError(frames[k], detail)
        if not allow_clipping:
            raise error
        failure = error.failure
    minimum = arm.min(initial=np.inf)
    return {
        "routes": len(routes),
        "subdivisions": subdivisions,
        "minimum_arm_layer_clearance_mm": float(minimum*1000) if np.isfinite(minimum) else None,
        "checked_intersection_samples": int(np.isfinite(arm).sum()),
        "checked_hand_over_samples": int(np.isfinite(hand).sum()),
        "minimum_hand_over_clearance_mm": float(hand.min()*1000) if np.isfinite(hand).any() else None,
        "verified": not len(bad),
        "clearance_shortfall_samples": int(len(bad)),
        "first_clearance_shortfall": failure,
    }

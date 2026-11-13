"""ArcCreate 1.2.68 input geometry, including screen grace and camera tilt.

The built-in camera tilt depends on previously highlighted arcs. Instead of
assuming a particular score history, catches are valid over its full [-.05,.05]
up-vector range. Native replay remains the final judge.
"""

import math

from . import rules


# Measured from JudgementService.Awake in the fixed 1148 x 802 exporter.
# These are screen fractions, independent of the physical tablet dimensions.
SCREEN_HALF_X = 135.315277 / 1148
SCREEN_HALF_Y = 172.058334 / 802
TILTS = (-0.05, -0.025, 0.0, 0.025, 0.05)
LANE_WIDTH = 425.0  # ArcCreate game units (100 per Unity world unit)
DEFAULT_CONTACT_OFFSET_X_PERCENT = 48
DEFAULT_CONTACT_OFFSET_Y_PERCENT = 24


def screen_hitbox(kind, width, height):
    """Native screen-space half-extents in metres: left/right, down, up."""
    arc_x, arc_y = SCREEN_HALF_X * width, SCREEN_HALF_Y * height
    if kind == "chain":
        return arc_x, arc_y, arc_y
    lane_x = arc_x * LANE_WIDTH / (2 * rules.HITBOX_X)
    lane_y = arc_y * LANE_WIDTH / (2 * rules.HITBOX_Y)
    if kind == "sky":
        return (lane_x * 2 * rules.SKY_TAP_X / LANE_WIDTH,
                lane_y * 2 * rules.SKY_TAP_DOWN / LANE_WIDTH,
                lane_y * 2 * rules.SKY_TAP_UP / LANE_WIDTH)
    return lane_x, lane_y, lane_y


class CameraEnvelope:
    def __init__(self, projection):
        self.width = projection.width_m
        self.height = projection.height_m
        c = projection.camera
        self.position = tuple(c.position)
        self.forward = tuple(c.forward)
        self.scale = self.height / (2 * math.tan(math.radians(c.fov_y_deg) / 2))
        self.bases = []
        for tilt in TILTS:
            # Reflection from ArcCreate's world X into the planner's world X.
            desired_up = (-tilt, 1 - tilt, 0.0)
            fx, fy, fz = self.forward
            ux, uy, uz = desired_up
            r = (fy * uz - fz * uy, fz * ux - fx * uz, fx * uy - fy * ux)
            length = math.sqrt(sum(x * x for x in r))
            r = tuple(x / length for x in r)
            up = (r[1] * fz - r[2] * fy, r[2] * fx - r[0] * fz, r[0] * fy - r[1] * fx)
            self.bases.append((r, up))
        self.projected = {}
        self.rectangles = {}

    def project(self, x, y, view=2):
        key = (x, y, view)
        if key not in self.projected:
            if len(self.projected) > 200000:
                self.projected.clear()
            p = (
                x / 100 - self.position[0],
                y / 100 - self.position[1],
                -self.position[2],
            )
            right, up = self.bases[view]
            z = sum(a * b for a, b in zip(p, self.forward))
            self.projected[key] = (
                sum(a * b for a, b in zip(p, right)) * self.scale / z,
                sum(a * b for a, b in zip(p, up)) * self.scale / z,
            )
        return self.projected[key]

    def unproject(self, xy, view):
        right, up = self.bases[view]
        ray = tuple(
            f + r * xy[0] / self.scale + u * xy[1] / self.scale
            for f, r, u in zip(self.forward, right, up)
        )
        distance = -self.position[2] / ray[2]
        return tuple(
            (p + distance * r) * 100 for p, r in zip(self.position[:2], ray[:2])
        )

    def contact_rectangle(self, kind, x, y, fractions):
        """Intersect the inner screen rectangles over the camera tilt envelope."""
        key = (kind, x, y, fractions)
        if key not in self.rectangles:
            if len(self.rectangles) >= 65536:
                self.rectangles.clear()
            centres = [self.project(x, y, view) for view in range(len(TILTS))]
            dx, down, up = screen_hitbox(kind, self.width, self.height)
            xs, ys = zip(*centres)
            left, right = _axis_limits(xs, dx, dx, fractions[0], self.width/2)
            bottom, top = _axis_limits(ys, down, up, fractions[1], self.height/2)
            self.rectangles[key] = (left, bottom, right, top)
        return self.rectangles[key]

    def overlaps(self, a, b, view=2):
        if abs(a[0] - b[0]) <= 190 and abs(a[1] - b[1]) <= 250:
            return True
        x, y = self.project(*a, view)
        u, v = self.project(*b, view)
        return (
            abs(x - u) <= SCREEN_HALF_X * self.width
            and abs(y - v) <= SCREEN_HALF_Y * self.height
        )


def _axis_limits(centres, below, above, fraction, screen_edge):
    low = max(-screen_edge, max(centres) - below * fraction)
    high = min(screen_edge, min(centres) + above * fraction)
    if low > high:
        # At small settings the inner windows can separate as the camera tilts.
        # Keep the nominal offset limit, and require a native hit in every view.
        centre = centres[len(TILTS)//2]
        low = max(-screen_edge, centre - below*fraction, max(centres) - below)
        high = min(screen_edge, centre + above*fraction, min(centres) + above)
    # Leave room for the replay's 0.01 mm rounding, including narrow limits.
    inset = min(.00002, max(0., (high - low)/4))
    return low + inset, high - inset


def camera_for(projection):
    camera = getattr(projection, "_native_camera_envelope", None)
    if camera is None:
        camera = CameraEnvelope(projection)
        object.__setattr__(projection, "_native_camera_envelope", camera)
    return camera


class InputPoint(tuple):
    """Nominal world point plus inverse projections for every camera view."""

    def __new__(cls, xy, projection):
        camera = camera_for(projection)
        samples = tuple(camera.unproject(xy, v) for v in range(len(TILTS)))
        point = super().__new__(cls, samples[2])
        point.samples, point.camera, point.device_xy = samples, camera, xy
        return point

    def in_contact_rectangle(self, kind, x, y, fractions):
        left, bottom, right, top = self.camera.contact_rectangle(kind, x, y, fractions)
        px, py = self.device_xy
        return left - 1e-9 <= px <= right + 1e-9 and bottom - 1e-9 <= py <= top + 1e-9

    def collides(self, x, y, *, certain=False, inset=0):
        results = []
        for v, (px, py) in enumerate(self.samples):
            if y <= 550:
                py = min(py, 550)
            hit = abs(px - x) <= 190 - inset and abs(py - y) <= 250 - inset
            if not hit:
                a, b = self.camera.project(px, py, v)
                u, w = self.camera.project(x, y, v)
                margin = 0.001 if inset else 0
                hit = (
                    abs(a - u) <= SCREEN_HALF_X * self.camera.width - margin
                    and abs(b - w) <= SCREEN_HALF_Y * self.camera.height - margin
                )
            if certain and not hit:
                return False
            if not certain and hit:
                return True
            results.append(hit)
        return all(results) if certain else any(results)


class ArcInputGeometry:
    def __init__(self, occupations, projection, offset):
        from .two_finger import frame_at_or_after, frame_ms

        self.projection, self.offset = projection, offset
        self.camera = camera_for(projection)
        self.regions = {}
        self.active_notes = {}
        self.guard_targets = {}
        self.notes = {
            (o.colour, id(n)): n
            for o in occupations
            if o.kind == "chain"
            for n in o.notes
        }
        for (colour, _), n in self.notes.items():
            for f in range(
                max(0, frame_at_or_after(n.start, offset)),
                frame_at_or_after(n.end + 1, offset),
            ):
                x, y = n.position(min(max(frame_ms(f, offset), n.start), n.end))
                self.regions.setdefault(f, []).append((colour, x, y))
                self.active_notes.setdefault(f, []).append((colour, n))
        grace_views = [set() for _ in TILTS]
        for f, regions in self.regions.items():
            pairs = [
                (a[1:], b[1:])
                for i, a in enumerate(regions)
                for b in regions[i + 1 :]
                if a[0] != b[0]
            ]
            for view, graced in enumerate(grace_views):
                if any(self.camera.overlaps(a, b, view) for a, b in pairs):
                    graced.update(range(f, f + 37))
        self.graced = {f: set(range(4)) for f in grace_views[2]}
        # Guard every possible grace exit, rather than retaining an assignment
        # that native input may have cleared under a different camera roll.
        exits = sorted(
            {f + 1 for frames in grace_views for f in frames if f + 1 not in frames}
        )
        self.guards = {}
        for exit in exits:
            for f in range(max(0, exit - 6), exit + 13):
                if f <= exit:
                    w = (f - (exit - 6)) / 6
                elif f <= exit + 3:
                    w = 1.0
                else:
                    w = (exit + 12 - f) / 9
                w = max(0.0, min(1.0, w))
                w = w * w * (3 - 2 * w)
                self.guards[f] = max(self.guards.get(f, 0.0), w)

    def guard_path(self, item, path, down, *, fixed_prefix=0):
        """Briefly favour the intended colour at grace exit; keep tick cheese elsewhere."""
        from .two_finger import frame_ms

        out = list(path)
        for k in range(fixed_prefix, len(out)):
            f = down + k
            weight = self.guards.get(f, 0.0)
            if not weight:
                continue
            key = (f, item.colour)
            if key not in self.guard_targets:
                now = frame_ms(f, self.offset)
                # Preserve active-note order, including nearest-target ties.
                self.guard_targets[key] = tuple(
                    self.projection.judgement_to_device(*n.position(now))
                    for colour, n in self.active_notes.get(f, ()) if colour == item.colour
                )
            targets = self.guard_targets[key]
            if not targets:
                continue
            # A colour can branch. Retain the branch nearest the selected path.
            x, y = out[k]
            a, b = min(targets, key=lambda p: math.dist(p, (x, y)))
            out[k] = (x + (a - x) * weight, y + (b - y) * weight)
        return tuple(out)


def geometry_for(occupations, projection, offset):
    key = tuple(
        sorted(
            {
                (o.colour, id(n))
                for o in occupations
                if o.kind == "chain"
                for n in o.notes
            }
        )
    )
    cache = getattr(projection, "_arc_input_geometries", None)
    if cache is None:
        cache = {}
        object.__setattr__(projection, "_arc_input_geometries", cache)
    if key not in cache:
        cache[key] = ArcInputGeometry(occupations, projection, offset)
    return cache[key]

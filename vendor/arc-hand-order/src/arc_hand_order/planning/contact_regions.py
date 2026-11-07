"""Conservative native screen hitboxes and bounded spatial alternatives.

Each rectangle is inside ArcCreate's screen-space acceptance region for every
sampled camera roll. Never use an axis-aligned envelope of a projected polygon.
"""

import math
from .native_input import camera_for, SCREEN_HALF_X, SCREEN_HALF_Y

MARGIN = 0.0015  # screen metres; includes the 0.01 mm JSON quantisation


def intersect(a, b):
    box = (max(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), min(a[3], b[3]))
    return box if box[0] <= box[1] and box[2] <= box[3] else None


def centre(box):
    return ((box[0] + box[1]) / 2, (box[2] + box[3]) / 2)


def nearest(box, p):
    return (min(box[1], max(box[0], p[0])), min(box[3], max(box[2], p[1])))


def screen_box(projection, kind, x, y):
    """A sufficient (not exhaustive) native hit region, including camera roll."""
    # ArcHitboxX/Y=190/250, ArcTapHitboxX/Down/Up=302/310/250.
    sx, sy = SCREEN_HALF_X * projection.width_m, SCREEN_HALF_Y * projection.height_m
    if kind in ("tap", "hold"):
        sx *= 425 / 380
        down = up = sy * 425 / 500
    elif kind == "sky":
        sx *= 302 / 190
        down, up = sy * 310 / 250, sy
    else:
        down = up = sy
    box = (
        -projection.width_m / 2 + MARGIN,
        projection.width_m / 2 - MARGIN,
        -projection.height_m / 2 + MARGIN,
        projection.height_m / 2 - MARGIN,
    )
    camera = camera_for(projection)
    for view in range(len(camera.bases)):
        px, py = camera.project(x, y, view)
        box = intersect(
            box,
            (px - sx + MARGIN, px + sx - MARGIN, py - down + MARGIN, py + up - MARGIN),
        )
        if box is None:
            break
    return box


def point_box(item, projection):
    from .two_finger import target_xy

    p = target_xy(item, item.start_ms, projection)
    if item.lane is None:
        return screen_box(projection, "sky", item.notes[0].x, item.notes[0].y)
    x = (-637.5, -212.5, 212.5, 637.5)[item.lane - 1]
    box = screen_box(projection, item.kind, x, 0)
    if box is None:
        return None
    # Stay in the visible lane too, avoiding unintended adjacent-lane presses.
    return intersect(box, (p[0] - 0.021, p[0] + 0.021, p[1] - 0.014, p[1] + 0.014))


def point_targets(item, projection, occupations=()):
    box = point_box(item, projection)
    if box is None:
        return ()
    from .two_finger import target_xy

    p = target_xy(item, item.start_ms, projection)
    nearby = [
        o
        for o in occupations
        if o.kind in ("tap", "sky", "hold") and abs(o.start_ms - item.start_ms) <= 150
    ]
    candidates = tuple(
        dict.fromkeys(nearest(box, (p[0] + dx, p[1])) for dx in (0, -0.018, 0.018))
    )
    return tuple(
        q for q in candidates if preserves_press_targets(nearby, projection, p, q)
    )


def common_tick_boxes(item, projection, offset):
    """Intersect unions of time-specific regions, retaining 48 useful branches.

    A tick may use any frame in its own window. An intersection is a witness
    that a stationary finger can catch all ticks; exact native catches and
    colour binding are still checked by the graph.
    """
    from .arc_scoring import source_note
    from .two_finger import frame_ms, frame_at_or_after

    cache = getattr(projection, "_common_tick_boxes", None)
    if cache is None:
        cache = {}
        object.__setattr__(projection, "_common_tick_boxes", cache)
    key = (
        item.occupation_id,
        tuple(
            (p.source_event_id, p.tick_index, p.contact_window)
            for p in item.score_points
        ),
        offset,
    )
    if key in cache:
        return cache[key]
    regions = [
        (
            -projection.width_m / 2,
            projection.width_m / 2,
            -projection.height_m / 2,
            projection.height_m / 2,
        )
    ]
    for point in item.score_points:
        n = source_note(item, point)
        first = max(0, frame_at_or_after(point.contact_window.earliest_ms, offset))
        last = frame_at_or_after(point.contact_window.latest_ms + 1, offset) - 1
        frames = (
            range(first, last + 1)
            if last - first <= 24
            else sorted({round(first + (last - first) * i / 24) for i in range(25)})
        )
        boxes = []
        for f in frames:
            x, y = n.position(min(max(frame_ms(f, offset), n.start), n.end))
            box = screen_box(projection, "chain", x, y)
            if box is not None and box not in boxes:
                boxes.append(box)
        merged = []
        for a in regions:
            for b in boxes:
                c = intersect(a, b)
                if c is not None:
                    merged.append(c)
        # Dominated boxes add no stationary solution. Prune largest first.
        kept = []
        for box in sorted(set(merged), key=lambda b: -(b[1] - b[0]) * (b[3] - b[2])):
            if not any(
                a[0] <= box[0] and a[1] >= box[1] and a[2] <= box[2] and a[3] >= box[3]
                for a in kept
            ):
                kept.append(box)
            if len(kept) == 48:
                break
        regions = kept
        if not regions:
            break
    cache[key] = tuple(regions)
    return cache[key]


def point_collides(item, projection, xy, view):
    """Native lane/sky screen OR world test, for press ambiguity audits."""
    camera = camera_for(projection)
    sx, sy = SCREEN_HALF_X * projection.width_m, SCREEN_HALF_Y * projection.height_m
    px, py = xy
    vx, vy = camera.unproject(xy, view)
    if item.lane is not None:
        x = (-637.5, -212.5, 212.5, 637.5)[item.lane - 1]
        cx, cy = camera.project(x, 0, view)
        screen = abs(px - cx) <= sx * 425 / 380 and abs(py - cy) <= sy * 425 / 500
        right, up = camera.bases[view]
        ray = tuple(
            f + r * px / camera.scale + u * py / camera.scale
            for f, r, u in zip(camera.forward, right, up)
        )
        if abs(ray[1]) < 1e-12:
            return screen
        distance = -camera.position[1] / ray[1]
        gx = (camera.position[0] + ray[0] * distance) * 100
        gz = (camera.position[2] + ray[2] * distance) * 100
        lane = 1 if gx < -425 else 2 if gx < 0 else 3 if gx < 425 else 4
        return screen or (-500 <= gz <= 5350 and lane == item.lane)
    n = item.notes[0]
    cx, cy = camera.project(n.x, n.y, view)
    screen = abs(px - cx) <= sx * 302 / 190 and -sy * 310 / 250 <= py - cy <= sy
    if n.y <= 550:
        vy = min(vy, 550)
    return screen or (abs(vx - n.x) <= 302 and -310 <= vy - n.y <= 250)


def preserves_press_targets(targets, projection, before, after):
    """Keep possible eligible notes and their timing/distance tie order."""
    from .two_finger import target_xy

    for view in range(5):
        old = [o for o in targets if point_collides(o, projection, before, view)]
        new = [o for o in targets if point_collides(o, projection, after, view)]
        if {o.occupation_id for o in old} != {o.occupation_id for o in new}:
            return False

        def order(hits, xy):
            return [
                o.occupation_id
                for o in sorted(
                    hits,
                    key=lambda o: (
                        o.start_ms,
                        math.dist(target_xy(o, o.start_ms, projection), xy),
                    ),
                )
            ]

        # Preserve every pair's order, not just the first winner: earlier
        # requests may already have been consumed by another press.
        if order(old, before) != order(new, after):
            return False

    return True

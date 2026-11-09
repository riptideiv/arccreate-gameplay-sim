"""ArcCreate 1.2.68 tick windows and low-motion paths through arc hitboxes.

An arc job needs a hit during its own request window, not continuous centreline
tracking. Native replay remains the authority for colour ownership and results.
"""

from dataclasses import replace
import math
import numpy as np
from .occupations import ScorePoint, TimeWindow

# A small inset keeps catches away from numerical hitbox boundaries.
HALF_X, HALF_Y = 182.0, 242.0


def trace_position(item, frame, offset, projection):
    from .two_finger import frame_ms, target_xy

    cache = getattr(projection, "_trace_positions", None)
    if cache is None:
        cache = {}
        object.__setattr__(projection, "_trace_positions", cache)
    key = (item.parent_occupation_id or item.occupation_id, frame, offset)
    if key in cache:
        return cache[key]
    # A split scoring job still carries the whole connected snake's notes.
    geometry = replace(item, native_scoring=False, parent_occupation_id=None)
    result = target_xy(geometry, frame_ms(frame, offset), projection)
    cache[key] = result
    return result


def trace_cells(items, offset):
    """Partition each snake's visual lifetime between its tick jobs.

    Each frame is charged once, independent of tick density. Untraced gaps,
    heads and tails count too, rather than rewarding two-frame tick taps.
    """
    from .two_finger import frame_at_or_after

    parents = {}
    for item in items:
        parents.setdefault(item.parent_occupation_id or item.occupation_id, {})[
            item.occupation_id
        ] = item
    cells = {}
    for siblings in parents.values():
        jobs = sorted(
            siblings.values(), key=lambda o: (o.first_tick.time_ms, o.occupation_id)
        )
        boundaries = [jobs[0].start_ms]
        boundaries.extend(
            (a.last_tick.time_ms + b.first_tick.time_ms) / 2
            for a, b in zip(jobs, jobs[1:])
        )
        boundaries.append(jobs[0].end_ms + 1)
        boundaries = [
            min(jobs[0].end_ms + 1, max(jobs[0].start_ms, t)) for t in boundaries
        ]
        for job, first, stop in zip(jobs, boundaries, boundaries[1:]):
            cells[job.occupation_id] = (
                max(0, frame_at_or_after(first, offset)),
                max(0, frame_at_or_after(stop, offset)),
            )
    return cells


class TracePreference:
    """Deviation-seconds of actual composed touches, cached across extensions."""

    def __init__(self, groups, projection, offset):
        items = {
            c.occupation.occupation_id: c.occupation
            for group in groups
            for c in group
            if c.occupation.kind == "chain"
        }
        self.cells = trace_cells(items.values(), offset)
        self.centres = {
            key: tuple(
                trace_position(item, f, offset, projection)
                for f in range(*self.cells[key])
            )
            for key, item in items.items()
        }
        self.values = {}

    def distance(self, touch):
        if touch is None or not any(
            p.candidate.occupation.occupation_id in self.cells for p in touch.parts
        ):
            return 0.0
        if "_trace_distance" not in touch.__dict__:
            prefix = touch.__dict__.get("_native_prefix")
            if prefix is not None and any(
                p.candidate.occupation.occupation_id in self.cells for p in prefix.parts
            ):
                value = self.distance(prefix)
                path = touch.path[len(prefix.path) - 1 :]
            else:
                value, path = 0.0, touch.path
            touch.__dict__["_trace_distance"] = value + sum(
                math.dist(a, b) for a, b in zip(path, path[1:])
            )
        return touch.__dict__["_trace_distance"]

    def deviation(self, touch):
        if touch is None:
            return 0.0
        if touch.id in self.values:
            return self.values[touch.id]
        jobs = {
            p.candidate.occupation.occupation_id for p in touch.parts
        } & self.cells.keys()
        prefix = touch.__dict__.get("_native_prefix")
        old_jobs = (
            ({p.candidate.occupation.occupation_id for p in prefix.parts} & jobs)
            if prefix
            else set()
        )
        value = self.deviation(prefix) if prefix else 0.0
        for job in jobs:
            start, stop = self.cells[job]
            if job not in old_jobs:
                value += (stop - start) / 60
            first = max(start, prefix.up if job in old_jobs else touch.down)
            for f in range(first, min(stop, touch.up)):
                distance = math.dist(touch.xy(f), self.centres[job][f - start])
                value -= max(0, 1 - distance / 0.020) / 60
        self.values[touch.id] = max(0.0, value)
        return self.values[touch.id]


def source_note(item, point=None):
    point = point or item.first_tick
    return item.notes[item.note_indices.index(point.note_index)]


def segmented_position(note, event, events):
    """ArcCreate judges against its 71/143 ms polyline, not the ideal curve."""
    raw = note.position
    following = (
        events.get(event.chain.next_event_id)
        if event is not None and event.chain and event.chain.next_event_id
        else None
    )
    end = following.start_time_ms if following is not None else note.end
    # Event times passed here are original chart times; note times are excerpt-local.
    if event is not None and following is not None:
        end = note.end + (following.start_time_ms - event.end_time_ms)
    final = (
        (850 * following.start_aff_xy[0] - 425, 100 + 450 * following.start_aff_xy[1])
        if following is not None
        else raw(note.end)
    )
    step = 71 if note.end - note.start < 1000 else 143

    def position(t):
        if t < note.start or t > end:
            return raw(t)
        a = note.start + int((t - note.start) // step) * step
        b = min(a + step, end)
        if b <= a:
            return final
        left = raw(a)
        right = final if b == end else raw(b)
        u = (t - a) / (b - a)
        return left[0] + u * (right[0] - left[0]), left[1] + u * (right[1] - left[1])

    return position


def native_occupations(
    occupations,
    early_groups=frozenset(),
    event_groups=None,
    density=1.0,
    chart_events=(),
):
    """Port Arc.RecalculateJudgeTimings / RequestJudgement; expiry is exclusive.

    Ground holds also use native request starts. Zero-BPM arcs have no ticks.
    """
    items = []
    event_groups = event_groups or {}
    events = {e.event_id: e for e in chart_events}
    source_items = {
        event: i
        for i, o in enumerate(occupations.items)
        for event in o.source_event_ids
        if o.kind == "chain"
    }
    roots = list(range(len(occupations.items)))

    def root(i):
        while roots[i] != i:
            i = roots[i]
        return i

    # Native chain topology includes zero-duration connectors. They have no
    # ticks, but they change first-tick timing and connect sustained fingers.
    for event, index in source_items.items():
        e = events.get(event)
        previous = e.chain.previous_event_id if e is not None and e.chain else None
        seen = set()
        while previous and previous not in seen:
            seen.add(previous)
            p = events.get(previous)
            if p is None or p.color != e.color:
                break
            if previous in source_items:
                roots[root(index)] = root(source_items[previous])
                break
            previous = p.chain.previous_event_id if p.chain else None
    merged = {}
    for i, o in enumerate(occupations.items):
        key = root(i)
        if key not in merged:
            merged[key] = o
        else:
            base = merged[key]
            merged[key] = replace(
                base,
                source_event_ids=base.source_event_ids + o.source_event_ids,
                note_indices=base.note_indices + o.note_indices,
                notes=base.notes + o.notes,
                start_ms=min(base.start_ms, o.start_ms),
                end_ms=max(base.end_ms, o.end_ms),
            )
    for item in merged.values():
        if item.kind == "hold":
            n = item.notes[0]
            interval = n.interval / density
            if not math.isfinite(interval):
                continue
            count = int((n.end - n.start) / interval)
            first = n.start if count <= 1 else n.start + interval
            count = max(1, count - 1)
            points = tuple(
                ScorePoint(
                    item.source_event_ids[0],
                    "hold",
                    item.note_indices[0],
                    k,
                    round(first + k * interval),
                    1,
                    TimeWindow(
                        round(first + k * interval),
                        min(
                            n.end - 1,
                            round(first + k * interval + 2 * min(interval, 2000)) - 1,
                        ),
                    ),
                )
                for k in range(count)
            )
            items.append(
                replace(
                    item,
                    score_points=points,
                    up_window=TimeWindow(
                        points[-1].contact_window.earliest_ms + 1, None
                    ),
                )
            )
            continue
        if item.kind != "chain":
            items.append(item)
            continue
        notes = []
        for event, n in zip(item.source_event_ids, item.notes, strict=True):
            e = events.get(event)
            predecessor = (
                (e.chain.previous_event_id is not None)
                if e is not None and e.chain
                else n.has_predecessor
            )
            notes.append(
                replace(
                    n,
                    has_predecessor=predecessor,
                    position=segmented_position(n, e, events),
                )
            )
        item = replace(item, notes=tuple(notes))
        points = []
        for event, index, note in zip(
            item.source_event_ids, item.note_indices, item.notes, strict=True
        ):
            if not math.isfinite(note.interval):
                continue
            interval = note.interval / density
            modifier = int(not note.has_predecessor)
            count = int((note.end - note.start) / interval)
            if count <= modifier:
                count = 1
                first = note.start + (note.end - note.start) // 2
            else:
                count -= modifier
                first = note.start + modifier * interval
            early = event_groups.get(event) in early_groups
            for k in range(count):
                start = round(note.start + k * interval) - (
                    100 if early and k == 0 else 0
                )
                expiry = round(first + (k + 2) * min(interval, 2000))
                points.append(
                    ScorePoint(
                        event,
                        "arc",
                        index,
                        k,
                        round(first + k * interval),
                        1,
                        TimeWindow(start, min(expiry - 1, note.end + 240)),
                        native_auto_ms=round(note.start + k * interval),
                        native_expiry_ms=expiry,
                        native_judge_ms=first + k * interval,
                    )
                )
        if not points:
            continue
        points.sort(key=lambda p: (p.time_ms, p.note_index, p.tick_index))
        items.append(
            replace(
                item,
                score_points=tuple(points),
                native_scoring=True,
                notes=tuple(
                    replace(n, interval=n.interval / density) for n in item.notes
                ),
                down_window=TimeWindow(
                    None, min(p.contact_window.latest_ms for p in points)
                ),
                up_window=TimeWindow(
                    max(p.contact_window.earliest_ms for p in points) + 1, None
                ),
            )
        )
    items.sort(key=lambda o: (o.start_ms, o.end_ms, o.occupation_id))
    return replace(
        occupations,
        items=tuple(items),
        source_to_occupation={
            e: o.occupation_id for o in items for e in o.source_event_ids
        },
    )


def world(projection, xy):
    cache = getattr(projection, "_scoring_world_cache", None)
    if cache is None:
        cache = {}
        object.__setattr__(projection, "_scoring_world_cache", cache)
    if xy not in cache:
        if len(cache) > 100000:
            cache.clear()
        from .native_input import InputPoint
        cache[xy] = InputPoint(xy, projection)
    return cache[xy]


def hit(note, xy, time_ms):
    t = min(max(time_ms, note.start), note.end)
    cache = getattr(note, "_scoring_positions", None)
    if cache is None:
        cache = {}
        object.__setattr__(note, "_scoring_positions", cache)
    if t not in cache:
        cache[t] = note.position(t)
    x, y = cache[t]
    if hasattr(xy, 'collides'):
        return xy.collides(x, y, certain=True, inset=8)
    return abs(xy[0] - x) <= HALF_X and abs(xy[1] - y) <= HALF_Y


def covers(item, path, down, offset, projection, points=None):
    """Actual path samples, never an averaged or time-collapsed swept hitbox."""
    from .two_finger import frame_ms, frame_at_or_after

    points = points or item.score_points
    result = []
    for point in points:
        note = source_note(item, point)
        first = max(down, frame_at_or_after(point.contact_window.earliest_ms, offset))
        last = min(
            down + len(path),
            frame_at_or_after(point.contact_window.latest_ms + 1, offset),
        )
        found = next(
            (
                f
                for f in range(first, last)
                if hit(note, world(projection, path[f - down]), frame_ms(f, offset))
            ),
            None,
        )
        if found is None:
            return None
        result.append(found)
    return result


def anchors(item, projection):
    samples = np.array(
        [
            n.position(n.start + (n.end - n.start) * f / 4)
            for n in item.notes
            for f in range(5)
        ]
    )
    centres = [
        tuple(samples.mean(axis=0)),
        tuple(np.median(samples, axis=0)),
        tuple((samples.min(axis=0) + samples.max(axis=0)) / 2),
    ]
    # Repeated wiggles share their mean; retain representative endpoints too.
    centres.extend(
        tuple(samples[i])
        for i in np.linspace(0, len(samples) - 1, min(10, len(samples)), dtype=int)
    )
    pool = []
    for world in centres:
        p = projection.judgement_to_device(*world)
        if (
            abs(p[0]) <= projection.width_m / 2
            and abs(p[1]) <= projection.height_m / 2
            and all(math.dist(p, q) > 0.0005 for q in pool)
        ):
            pool.append(p)
    return tuple(pool)


def profile(item, projection, max_speed, offset):
    """Bounded single-chain search; return a fully covered path or None.

    First try stable anchors. Otherwise visit tick regions with a small beam,
    joining catches by straight held-finger travel instead of tracing bends.
    """
    from .two_finger import frame_ms, frame_at_or_after

    pool = anchors(item, projection)
    first = max(
        0,
        frame_at_or_after(
            min(p.contact_window.earliest_ms for p in item.score_points), offset
        ),
    )
    last = (
        frame_at_or_after(
            max(p.contact_window.latest_ms for p in item.score_points) + 1, offset
        )
        - 1
    )
    frames = range(first, last + 1)
    times = [frame_ms(f, offset) for f in frames]
    # Each tick owns a source segment, even after that segment's visual end.
    valid = []
    for point in item.score_points:
        n = source_note(item, point)
        valid.append(
            [f for f, t in zip(frames, times) if point.contact_window.contains(t)]
        )

    def bound_start(p):
        xy = world(projection, p)
        return next(
            (
                f
                for f, t in zip(frames, times)
                if any(n.start <= t <= n.end and hit(n, xy, t) for n in item.notes)
            ),
            None,
        )

    for anchor in pool:
        xy = world(projection, anchor)
        bound = bound_start(anchor)
        if bound is None:
            continue
        caught = [
            next(
                (
                    f
                    for f in fs
                    if f >= bound
                    and hit(source_note(item, point), xy, frame_ms(f, offset))
                ),
                None,
            )
            for point, fs in zip(item.score_points, valid)
        ]
        if all(f is not None for f in caught):
            a = bound
            b = max(caught) + 2
            return a, tuple(anchor for _ in range(a, b)), tuple(caught)
    # (last frame, last point, catch waypoints, cost). Equal-time catches can
    # share a point. Non-monotone choices are not falsely credited.
    states = [(first - 1, None, (), 0.0)]
    for point, fs in zip(item.score_points, valid):
        n = source_note(item, point)
        next_states = {}
        for previous, p, route, cost in states:
            choices = set()
            # Keep the old point whenever the moving hitbox catches it.
            for anchor in (*pool, *((p,) if p is not None else ())):
                xy = world(projection, anchor)
                possible = [
                    f for f in fs if f >= previous and hit(n, xy, frame_ms(f, offset))
                ]
                if possible:
                    for f in (possible[0], possible[len(possible) // 2], possible[-1]):
                        choices.add((f, anchor))
            for f in fs[:: max(1, len(fs) // 5)]:
                if f >= previous:
                    choices.add(
                        (
                            f,
                            projection.judgement_to_device(
                                *n.position(
                                    min(max(frame_ms(f, offset), n.start), n.end)
                                )
                            ),
                        )
                    )
            for f, q in sorted(choices):
                if p is not None:
                    distance = math.dist(p, q)
                    dt = (f - previous) / 60
                    if (dt == 0 and distance > 1e-9) or (
                        dt > 0 and distance / dt > max_speed
                    ):
                        continue
                else:
                    if frame_ms(f, offset) > n.end:
                        continue
                    distance = 0
                value = cost + distance + distance**2 / max((f - previous) / 60, 1 / 60)
                key = (f, tuple(round(x, 5) for x in q))
                if key not in next_states or value < next_states[key][3]:
                    next_states[key] = (f, q, route + ((f, q),), value)
        if not next_states:
            return None
        states = sorted(next_states.values(), key=lambda s: (s[3], s[0]))[:12]
    for _, _, route, _ in states:
        a = route[0][0]
        b = route[-1][0] + 2
        path = []
        k = 0
        for f in range(a, b):
            while k + 1 < len(route) and route[k + 1][0] <= f:
                k += 1
            if k + 1 < len(route):
                x, p = route[k]
                y, q = route[k + 1]
                t = (f - x) / (y - x)
                path.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
            else:
                path.append(route[k][1])
        caught = covers(item, path, a, offset, projection)
        if caught is not None:
            return a, tuple(path), tuple(caught)
    return None

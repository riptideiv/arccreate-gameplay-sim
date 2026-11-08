"""Bounded two-hand contact refinement before motion/posing, never a visual offset.

Assignments and contact lifetimes remain fixed. Coordinate descent projects
continuous neighbour/clearance targets into conservative native hit regions;
accepted routes preserve colour-binding epochs and the original scoring claims.
"""

from dataclasses import replace
import math
import time
import numpy as np

from arc_hand_order.planning.contact_regions import (
    point_box,
    preserves_press_targets,
    screen_box,
    intersect,
    nearest,
)
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning.multi_finger import _Graph, Touch
from arc_hand_order.planning.two_finger import frame_ms, frame_at_or_after
from arc_hand_order.planning.arc_scoring import covers, source_note
from hand_pose import HAND_PARTS, NEUTRAL, proxies


def refine(result):
    started = time.monotonic()
    plan = result.plan
    if plan.multi.second_finger or plan.covered or plan.steady:
        return result, {
            "enabled": True,
            "changed_touches": 0,
            "reason": "unsupported multi-finger/covered shortcut",
        }
    projection = GameplayProjection(result.device)
    groups = tuple((c,) for c, _ in plan.choices)
    graph = _Graph(
        groups, projection, plan.options, plan.multi, occupations=result.occupations
    )
    rows = list(sorted(plan.touches, key=lambda ft: (ft.touch.down, ft.hand)))
    touches = [ft.touch for ft in rows]
    initial = list(touches)
    parts = [
        [
            (name, ((1 if h == 0 else -1) * x, y, z), r)
            for name, (x, y, z), r in HAND_PARTS
        ]
        for h in range(2)
    ]
    centres = [proxies(NEUTRAL[h], parts[h])[0] for h in range(2)]
    radii = [np.array([max(p[2]) for p in ph]) for ph in parts]
    serial = max((t.id for t in touches), default=0) + 1
    own = {
        i: [j for j, r in enumerate(rows) if r.hand == ft.hand]
        for i, ft in enumerate(rows)
    }
    neighbours = {}
    opponents = {}
    boxes = {}
    bindings = {}
    press_neighbours = {}
    for i, ft in enumerate(rows):
        group = own[i]
        k = group.index(i)
        neighbours[i] = group[max(0, k - 1) : k] + group[k + 1 : k + 2]
        t = ft.touch
        opponents[i] = [
            j
            for j, r in enumerate(rows)
            if r.hand != ft.hand
            and r.touch.down < t.up + 21
            and r.touch.up > t.down - 21
        ]
        bindings[i] = graph.bound_frames(t) if plan.options.scoring_arcs else {}
        press_neighbours[i] = [
            o
            for o in result.occupations.items
            if o.kind in ("tap", "sky", "hold") and abs(o.start_ms - t.down_ms) <= 150
        ]
        box = (-0.06, 0.06, -0.04, 0.04)
        for part in t.parts:
            o = part.candidate.occupation
            if o.kind in ("tap", "sky", "hold"):
                b = point_box(o, projection)
                if b is None:
                    box = None
                    break
                frames = (
                    range(max(t.down, part.start), min(t.up, part.stop))
                    if o.kind == "hold"
                    else (t.down,)
                )
                for f in frames:
                    x, y = t.xy(f)
                    box = intersect(box, (b[0] - x, b[1] - x, b[2] - y, b[3] - y))
                    if box is None:
                        break
            elif o.native_scoring:
                # Keep each tick's original time witness. The assignment search
                # separately explores other times / stationary common regions.
                for point in o.score_points:
                    a = max(
                        t.down,
                        frame_at_or_after(point.contact_window.earliest_ms, t.offset),
                    )
                    b = min(
                        t.up,
                        frame_at_or_after(point.contact_window.latest_ms + 1, t.offset),
                    )
                    from arc_hand_order.planning.arc_scoring import hit, world

                    f = next(
                        (
                            f
                            for f in range(a, b)
                            if hit(
                                source_note(o, point),
                                world(projection, t.xy(f)),
                                frame_ms(f, t.offset),
                            )
                        ),
                        None,
                    )
                    if f is None:
                        box = None
                        break
                    n = source_note(o, point)
                    x, y = n.position(min(max(frame_ms(f, t.offset), n.start), n.end))
                    b = screen_box(projection, "chain", x, y)
                    if b is None:
                        box = None
                        break
                    x, y = t.xy(f)
                    box = intersect(box, (b[0] - x, b[1] - x, b[2] - y, b[3] - y))
                    if box is None:
                        break
            else:
                box = None
            if box is None:
                break
        boxes[i] = box

    def objective(i, path):
        t = touches[i]
        h = 0 if rows[i].hand == "left" else 1
        p = np.asarray(path)
        total = 0.0
        for j in neighbours[i]:
            other = touches[j]
            a, b = (
                (p[-1], other.first_xy) if other.down >= t.up else (other.last_xy, p[0])
            )
            dt = max(
                1 / 60,
                (other.down - t.up if other.down >= t.up else t.down - other.up) / 60,
            )
            distance = math.dist(a, b)
            total += (
                5 * distance
                + max(0, distance / dt - plan.options.speed_comfort_m_s) ** 2
            )
        total += 5 * np.linalg.norm(np.diff(p, axis=0), axis=1).sum()
        if plan.options.acceleration_weight:
            from arc_hand_order.planning.trajectory import acceleration_energy, transfer_energy
            trial = replace(t, path=tuple(map(tuple,p)))
            energy = acceleration_energy(p)
            for j in neighbours[i]:
                a,b = (trial,touches[j]) if t.down < touches[j].down else (touches[j],trial)
                energy += transfer_energy(a,b,plan.options.speed_max_m_s)
            total += plan.options.acceleration_weight*energy
        for j in opponents[i]:
            other = touches[j]
            for f in np.unique(
                np.linspace(t.down, t.up - 1, min(8, len(p))).astype(int)
            ):
                idle = max(other.down - f, f - other.up + 1, 0)
                if idle >= 21:
                    continue
                q = np.asarray(other.xy(min(max(f, other.down), other.up - 1)))
                # Relaxed glove bodies, rather than distance between finger tips.
                a = centres[h].copy()
                a[:, :2] += p[f - t.down]
                b = centres[1 - h].copy()
                b[:, :2] += q
                margin = (
                    np.linalg.norm(a[:, None] - b[None], axis=-1)
                    - radii[h][:, None]
                    - radii[1 - h][None]
                )
                overlap = max(0, 0.001 - float(margin.min()))
                total += (1 - idle / 21) * (overlap / 0.01) ** 2
        if graph.trace_preference is not None:
            # Match the existing deviation preference exactly, including time
            # outside the contact, not a new centre-only constraint.
            nonlocal serial
            trial = Touch(
                serial, t.parts, t.bridges, t.down, t.up, tuple(map(tuple, p)), t.offset
            )
            serial += 1
            total += plan.options.arc_trace_weight * graph.trace_preference.deviation(
                trial
            )
        total += (
            0.15
            * float(np.mean(np.sum((p - np.asarray(initial[i].path)) ** 2, axis=1)))
            / 0.02**2
        )
        return total

    def valid(i, path):
        nonlocal serial
        old = touches[i]
        # Validate exactly the coordinates that will be serialized/replayed.
        path = tuple(
            (round(x * 1000, 2) / 1000, round(y * 1000, 2) / 1000) for x, y in path
        )
        new = Touch(serial, old.parts, old.bridges, old.down, old.up, path, old.offset)
        serial += 1
        if any(
            math.dist(a, b) * 60 > plan.options.speed_max_m_s + 1e-6
            for a, b in zip(path, path[1:])
        ):
            return None
        for j in neighbours[i]:
            a, b = (
                (new, touches[j]) if new.down < touches[j].down else (touches[j], new)
            )
            if graph.travel(a, b) is None:
                return None
            if graph.air_over(a, b) > graph.air_over(
                initial[i] if a is new else initial[j],
                initial[j] if b is touches[j] else initial[i],
            ):
                return None
        # Moving inside one note must not introduce another eligible press
        # target (including a skipped note), or invert a simultaneous tie.
        if not preserves_press_targets(
            press_neighbours[i], projection, initial[i].first_xy, new.first_xy
        ):
            return None
        if plan.options.scoring_arcs:
            # Equal epochs guarantee no new incidental colour capture or lift
            # lock, including notes that were skipped in contact assignment.
            if graph.bound_frames(new) != bindings[i]:
                return None
            if not graph.native_claims_valid(new):
                return None
        for part in new.parts:
            o = part.candidate.occupation
            if (
                o.native_scoring
                and covers(o, path, new.down, new.offset, projection) is None
            ):
                return None
        return new

    accepted = 0
    before = sum(objective(i, t.path) for i, t in enumerate(touches))
    for sweep in range(3):
        changes = 0
        for i, t in enumerate(touches):
            box = boxes[i]
            if box is None:
                continue
            p = np.asarray(initial[i].path)
            current = np.asarray(t.path)
            base_delta = current[0] - p[0]
            requests = [
                (0, 0),
                base_delta,
                ((box[0] + box[1]) / 2, (box[2] + box[3]) / 2),
            ]
            for j in neighbours[i]:
                other = touches[j]
                requests.append(
                    np.asarray(other.first_xy if other.down >= t.up else other.last_xy)
                    - (p[-1] if other.down >= t.up else p[0])
                )
            for j in opponents[i]:
                other = touches[j]
                f = min(max(t.down, other.down), other.up - 1)
                q = np.asarray(other.xy(f))
                # A relaxed pair should be able to rest beside one another.
                requests.append(
                    q
                    - p[0]
                    + np.array((-0.045 if rows[i].hand == "left" else 0.045, 0))
                )
            # Continuous projected coordinate descent, with decreasing trust
            # radii. No full hand-pose solve occurs inside this placement loop.
            best = objective(i, t.path)
            chosen = None
            for step in (0.012, 0.006, 0.002):
                requests.extend(
                    base_delta + np.array(v) * step
                    for v in ((1, 0), (-1, 0), (0, 1), (0, -1))
                )
                options = tuple(dict.fromkeys(nearest(box, q) for q in requests))
                for delta in options:
                    candidate = p + delta
                    value = objective(i, candidate)
                    if value >= best - 1e-7:
                        continue
                    new = valid(i, candidate)
                    if new is None:
                        continue
                    value = objective(i, new.path)
                    if value < best - 1e-7:
                        best = value
                        chosen = new
                        base_delta = np.asarray(new.first_xy) - p[0]
                requests = [base_delta]
            if chosen is not None:
                touches[i] = chosen
                changes += 1
                accepted += 1
        if not changes:
            break
    after = sum(objective(i, t.path) for i, t in enumerate(touches))
    changed = [i for i, t in enumerate(touches) if t.path != initial[i].path]
    # The local objective double-counts pair terms; the global audit is the
    # final acceptance criterion. Never publish a worse coordinated refinement.
    if after > before + 1e-6:
        touches = initial
        changed = []
        after = before
    out = replace(
        plan, touches=tuple(replace(ft, touch=t) for ft, t in zip(rows, touches))
    )
    report = {
        "enabled": True,
        "method": "native-region projected coordinate descent",
        "changed_touches": len(changed),
        "accepted_steps": accepted,
        "objective_before": before,
        "objective_after": after,
        "maximum_shift_mm": max(
            (
                math.dist(touches[i].first_xy, initial[i].first_xy) * 1000
                for i in changed
            ),
            default=0,
        ),
        "binding_epochs_preserved": True,
        "lifetimes_preserved": True,
        "elapsed_s": round(time.monotonic() - started, 3),
        "search_cost_scope": "assignment before refinement",
    }
    return replace(result, plan=out), report

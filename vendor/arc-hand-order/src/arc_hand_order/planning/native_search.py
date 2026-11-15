"""Optional native search adapter; Python remains the reference implementation.

Only preparation and reconstruction touch Python objects. Every transition and
the global beam reduction run on native-owned data with the interpreter detached.
"""

from dataclasses import asdict, fields, replace, dataclass
import importlib
import json
import math
import sys


@dataclass(frozen=True)
class NativeCheckpoint:
    items: tuple
    groups: tuple
    native: object
    blocked_ms: int
    states: int
    visited: int


class NativeUnsupported(RuntimeError):
    """A request cannot be represented without changing the reference search."""


_load_failure = None


def _load_extension():
    try:
        module = importlib.import_module("arc_hand_order_native")
    except (ImportError, OSError) as error:
        raise NativeUnsupported(
            f"native extension cannot be loaded: {error}"
        ) from error
    if not callable(getattr(module, "Search", None)):
        raise NativeUnsupported("native extension has an incompatible search API")
    identity = getattr(module, "implementation_id", None)
    if not callable(identity):
        raise NativeUnsupported(
            "native extension is missing its implementation identity; rebuild native-search"
        )
    identity = identity()
    if (
        not isinstance(identity, str)
        or not identity.startswith("arc-hand-order-native/")
        or identity.rsplit(";", 1)[-1] != "schema=6"
    ):
        raise NativeUnsupported(
            "native extension has an incompatible input schema; rebuild native-search"
        )
    return module


def availability():
    global _load_failure
    try:
        _load_extension()
    except NativeUnsupported as error:
        _load_failure = str(error)
        return False
    _load_failure = None
    return True


def unavailable_reason():
    return _load_failure or "native extension is unavailable"


def unsupported_reasons(occupations, projection, options, multi):
    reasons = []
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 13):
        reasons.append(
            "native numerical compatibility is validated for CPython 3.13 only"
        )
    if not options.scoring_arcs:
        reasons.append("native search currently requires score-mode arc judgement")
    for name in (
        "second_finger",
        "second_finger_again",
        "meeting_swaps",
        "covered_holds",
        "covered_arcs",
        "steady_arcs",
        "enveloped_arcs",
    ):
        if getattr(multi, name):
            reasons.append(f"{name} requires the Python reference backend")
    if multi.preference_weight:
        reasons.append("learned preference models require the Python reference backend")
    if options.handovers or options.swaps:
        reasons.append(
            "explicit handover/swap transforms require the Python reference backend"
        )
    # This hook exposes interned Python graph objects for diagnostics. A native
    # graph cannot masquerade as that exact object model.
    from . import multi_finger as mf

    if mf.PATH_TRACE is not None:
        reasons.append("PATH_TRACE requires Python graph objects")
    return reasons


def _prepared(occupations, projection, options, multi, reporter, checkpoint=None):
    from . import multi_finger as mf
    from .arc_scoring import source_note, trace_position
    from .two_finger import build_candidates, frame_at_or_after, frame_ms, target_xy
    from .native_geometry import prepare

    reporter("preparing")
    groups = build_candidates(
        occupations,
        projection,
        options,
        progress=reporter,
        prefix=checkpoint.groups if checkpoint else (),
    )
    mf.check_weights(options, multi)
    reporter("graph")
    graph = mf._Graph(groups, projection, options, multi, None, occupations)
    present = {c.occupation.occupation_id for g in groups for c in g}
    empty = iter(o for o in occupations.items if o.occupation_id not in present)
    items = [g[0].occupation if g else next(empty) for g in groups]
    candidates = [c for g in groups for c in g]
    indices = {id(c): k for k, c in enumerate(candidates)}
    item_indices = {id(o): i for i, o in enumerate(items)}
    records = []
    # Split arc jobs often retain the same whole-snake geometry. Share prepared
    # Python arrays while serializing; Rust owns its immutable copy afterwards.
    geometry_cache = {}
    raw_source_cache = {}
    for index, (o, group) in enumerate(zip(items, groups)):
        down = min(
            [
                frame_at_or_after(o.start_ms, graph.offset),
                *(c.down_frame for c in group),
            ]
        )
        stop = max(
            [
                down + 1,
                frame_at_or_after(o.end_ms + 1, graph.offset),
                *(c.up_frame for c in group),
            ]
        )
        geometry_key = (id(o), down, stop)
        if geometry_key not in geometry_cache:
            source = (
                source_note(o) if o.native_scoring and o.parent_occupation_id else None
            )
            raw_key = (id(source), down, stop) if source is not None else None
            raw = raw_source_cache.get(raw_key) if raw_key is not None else None
            if raw is None:
                points, centres = [], []
                for f in range(down, stop):
                    # Graph.point aliases split jobs by parent/frame. Leave
                    # its first-query choice to ordered native expansion.
                    point = (
                        graph.hold_point(o)
                        if o.kind == "hold"
                        else target_xy(o, frame_ms(f, graph.offset), projection)
                    )
                    r = o.region_at(frame_ms(f, graph.offset))
                    points.append(point)
                    centres.append((r.x or 0.0, r.y or 0.0))
                raw = points, centres
                if raw_key is not None:
                    raw_source_cache[raw_key] = raw
            points, centres = raw
            polygons = [
                graph.regions.polygon(o, frame_ms(f, graph.offset)) or []
                for f in range(down, stop)
            ]
            geometry_cache[geometry_key] = points, polygons, centres
        points, polygons, centres = geometry_cache[geometry_key]
        trace_start, trace = 0, ()
        if (
            graph.trace_preference is not None
            and o.occupation_id in graph.trace_preference.cells
        ):
            trace_start = graph.trace_preference.cells[o.occupation_id][0]
            trace = graph.trace_preference.centres[o.occupation_id]
        guides_start, guides = 0, ()
        if (
            graph.trace_preference is not None
            and getattr(options, "acceleration_weight", 0)
            and o.kind == "chain"
        ):
            from .trajectory import smooth_guides

            guides_start = max(0, frame_at_or_after(o.start_ms, graph.offset))
            guides = smooth_guides(
                o, guides_start, max(guides_start + 1, stop), graph.offset, projection
            )
        records.append(
            dict(
                kind=o.kind,
                oid=o.occupation_id,
                parent=o.parent_occupation_id,
                colour=o.colour if o.colour is not None else -1,
                start=o.start_ms,
                end=o.end_ms,
                first_tick=o.first_tick.time_ms,
                last_tick=o.last_tick.time_ms,
                score_weight=o.score_weight,
                red=o.is_red,
                forced=o.occupation_id in multi.forced_misses
                or bool(set(o.source_event_ids) & set(multi.forced_misses)),
                continuous=o.continuous_contact,
                native=o.native_scoring,
                source_end=source_note(o).end if o.kind == "chain" else o.end_ms,
                latest_down=o.down_window.latest_ms,
                earliest_down=o.down_window.earliest_ms,
                points_start=down,
                points=points,
                polygons=polygons,
                region_centres=centres,
                trace_points=[
                    trace_position(o, f, graph.offset, projection)
                    for f in range(down, stop)
                ]
                if o.kind == "chain"
                else points,
                centre=target_xy(o, o.start_ms, projection),
                note_center_scale=graph.note_center.scale(o) if graph.note_center is not None else 1.0,
                trace_start=trace_start,
                trace=trace,
                trace_hash=hash(o.occupation_id),
                trace_member=graph.trace_preference is not None
                and o.occupation_id in graph.trace_preference.cells,
                guides_start=guides_start,
                guides=guides,
                contested=o.occupation_id in graph.contested,
            )
        )
    prepared_candidates = []
    for c in candidates:
        if c.occupation.kind == "chain" and c.contact_path is None:
            raise NativeUnsupported(
                "score-mode candidates without explicit contact paths require Python"
            )
        t = graph.single(c)
        prepared_candidates.append(
            dict(
                id=c.id,
                item=item_indices[id(c.occupation)],
                down=c.down_frame,
                up=c.up_frame,
                path=graph._target_path(c),
                first=c.first_xy,
                held_to_end=c.held_to_end,
                held_release=graph.held_release(t),
                share_only=c.share_only,
                untouched=c.untouched_s,
                part_base=[
                    list(asdict(graph.part_cost(c, h)).values()) for h in mf.HANDS
                ],
                nominal=graph.nominal(t),
                fixed=(
                    mf.HANDS.index(graph.fixed[c.occupation.start_ms])
                    if c.occupation.kind in ("tap", "sky")
                    and c.occupation.start_ms in graph.fixed
                    else None
                ),
            )
        )
    result = dict(
        options=asdict(options),
        multi=asdict(multi),
        offset=graph.offset,
        items=records,
        candidates=prepared_candidates,
        groups=[[indices[id(c)] for c in g] for g in groups],
        trace_order=(
            [
                next(i for i, o in enumerate(items) if o.occupation_id == key)
                for key in graph.trace_preference.cells
            ]
            if graph.trace_preference is not None
            else []
        ),
        screen=graph.regions.screen,
        geometry=prepare(graph, items),
        wrist=None,
    )
    return result, items, candidates, groups, graph


def _cost(values):
    from .multi_finger import MultiCost
    from .two_finger import Cost

    rest = dict(
        zip(
            (f.name for f in fields(MultiCost)[1:]),
            list(values[9:35]) + list(values[38:40]) + [values[36]],
        )
    )
    rest["missed_ticks"] = int(rest["missed_ticks"])
    rest["missed_red_notes"] = int(rest["missed_red_notes"])
    return MultiCost(Cost(*values[:9]), **rest)


def _matches(touch, record, candidates):
    """Compare physical transitions, never implementation-specific touch IDs."""
    if touch.down != record["down"] or len(touch.path) != len(record["path"]):
        return False
    if tuple(touch.bridges) != tuple(map(tuple, record["bridges"])):
        return False
    actual = [(p.candidate.id, p.start, p.stop) for p in touch.parts]
    expected = [
        (candidates[p["candidate"]].id, p["start"], p["stop"]) for p in record["parts"]
    ]
    if actual != expected:
        return False
    if any(len(p) != 2 for p in record["path"]):
        return False
    if any(
        not (math.isfinite(a) and math.isfinite(b)) or abs(a - b) > 1e-12
        for p, q in zip(touch.path, record["path"])
        for a, b in zip(p, q)
    ):
        return False
    arrival = touch.__dict__.get("_acceleration_arrival")
    wanted = record["arrival"]
    if (arrival is None) != (wanted is None):
        return False
    if arrival is not None:
        if arrival[0] != wanted["up"] or len(arrival[1]) != len(wanted["path"]):
            return False
        if any(len(p) != 2 for p in wanted["path"]):
            return False
        if any(
            not (math.isfinite(a) and math.isfinite(b)) or abs(a - b) > 1e-12
            for p, q in zip(arrival[1], wanted["path"])
            for a, b in zip(p, q)
        ):
            return False
    return True


def _validate(result, graph, items, candidates, reporter):
    """Replay only the selected route through reference transition validation.

    This is linear in the chosen route, not another beam search. Native workers
    still select every frontier; reported costs and contacts come from the
    reference evaluator so an incorrect native price cannot pass quality gates.
    """
    from . import multi_finger as mf

    multi = graph.multi
    if len(result["steps"]) != len(items):
        raise RuntimeError("native search returned an incomplete or overlong route")
    # Discarded search branches can establish the reference's parent/frame
    # point cache before any selected transition uses it. Restore the native
    # ordered first-query values so route-only validation sees that same state.
    for parent, frame, xy in result.get("point_cache", ()):
        if (
            not isinstance(parent, str)
            or type(frame) is not int
            or len(xy) != 2
            or not all(math.isfinite(value) for value in xy)
        ):
            raise RuntimeError("native search returned invalid point-cache geometry")
        graph.points[(parent, frame)] = tuple(xy)
    state = (
        (-1,) * 4
        + ((0, 0) if multi.jack_weight else ())
        + ((-1, -1) if multi.arc_takeover_weight else ())
        + ((-1,) * 4 if multi.tick_contacts else ())
    )
    total = mf.MultiCost()
    current, done, skipped, step_costs = {}, [], [], []
    for index, step in enumerate(result["steps"]):
        if step["item"] != index:
            raise RuntimeError(
                f"native search returned an out-of-order item at {index}"
            )
        item = items[step["item"]]
        reporter(
            "validation", index, len(result["steps"]), chart_ms=item.first_tick.time_ms
        )
        if step["touch"] is None:
            if not (multi.allow_misses or item.is_red):
                raise RuntimeError(
                    f"native search returned an unauthorized miss at item {index}"
                )
            increment = mf.MultiCost(
                missed_ticks=item.score_weight, missed_red_notes=int(item.is_red)
            )
            skipped.append(item)
        else:
            if item.occupation_id in multi.forced_misses or set(
                item.source_event_ids
            ) & set(multi.forced_misses):
                raise RuntimeError(
                    f"native search returned a forced-miss hit at item {index}"
                )
            if step["hand"] not in (0, 1):
                raise RuntimeError(
                    f"native search returned an invalid hand at item {index}"
                )
            record = step["touch"]
            chosen = max(
                (candidates[p["candidate"]] for p in record["parts"]),
                key=lambda c: c.item_index,
            )
            if chosen.occupation is not item:
                raise RuntimeError(
                    f"native search returned the wrong candidate at item {index}"
                )
            hand = mf.HANDS[step["hand"]]
            match = None
            for next_state, increment, changes in graph.steps(state, chosen, hand):
                if len(changes) != 1:
                    continue
                slot, touch, kind = changes[0]
                if slot != 2 * step["hand"] or (kind == "extend") != step["extension"]:
                    continue
                if _matches(touch, record, candidates):
                    match = next_state, increment, changes
                    break
            if match is None:
                raise RuntimeError(
                    f"native transition failed Python validation at item {index}: {item.occupation_id}"
                )
            state, increment, changes = match
            for slot, touch, kind in changes:
                if kind == "new" and slot in current:
                    done.append((slot, current[slot]))
                current[slot] = touch
        total = total + increment
        step_costs.append((item.start_ms, increment))
    done.extend(current.items())
    touches = tuple(
        sorted(
            (mf.FingerTouch(t, mf.HANDS[slot // 2], slot % 2) for slot, t in done),
            key=mf.press_order,
        )
    )
    reporter("validation", len(result["steps"]), len(result["steps"]))
    return touches, total, tuple(step_costs), tuple(skipped)


def plan_items(
    occupations,
    projection,
    options,
    multi,
    progress=None,
    workers=1,
    *,
    keep_checkpoints=False,
    checkpoint=None,
):
    reasons = unsupported_reasons(occupations, projection, options, multi)
    if reasons:
        raise NativeUnsupported("; ".join(reasons))
    if not availability():
        raise NativeUnsupported(unavailable_reason())
    arc_hand_order_native = _load_extension()
    from . import multi_finger as mf
    from .progress import ProgressReporter

    reporter = ProgressReporter(progress)
    options = replace(options, audio_offset_ms=occupations.audio_offset_ms)
    data, items, candidates, groups, graph = _prepared(
        occupations, projection, options, multi, reporter, checkpoint
    )
    search = arc_hand_order_native.Search(
        json.dumps(data, allow_nan=False, separators=(",", ":")), workers
    )
    del data
    from collections import deque

    checkpoints = deque(maxlen=3)
    start = len(checkpoint.items) if checkpoint else 0
    if checkpoint:
        if tuple(items[:start]) != checkpoint.items:
            raise ValueError("The retry occupations do not match the beam checkpoint")
        search.resume(checkpoint.native)
    if checkpoint:
        reporter(
            "search",
            start,
            len(groups),
            chart_ms=items[start].first_tick.time_ms,
            states=checkpoint.states,
            visited=checkpoint.visited,
            states_completed=0,
            candidates=len(groups[start]),
            resumed_from=start,
            blocked_ms=checkpoint.blocked_ms,
        )
    last_checkpoint_ms = items[0].start_ms if items else 0
    prefix_end_ms = last_checkpoint_ms
    for k in range(start, len(groups)):
        group = groups[k]
        reporter(
            "search",
            k,
            len(groups),
            chart_ms=items[k].first_tick.time_ms,
            candidates=len(group),
        )
        try:
            completed, states, visited = search.advance()
        except ValueError as error:
            if "no candidate path" not in str(error):
                raise
            blocked_ms = (
                items[k].last_tick.time_ms
                if str(error) == "no candidate path through the final contacts"
                else (
                    items[k].first_tick.time_ms
                    if items[k].kind == "chain"
                    else items[k].start_ms
                )
            )
            resume = None
            for cut_ms, cut, native, state_count, work in reversed(checkpoints):
                if cut < len(items) and cut_ms <= blocked_ms - mf.RETRY_REWIND_MS:
                    resume = NativeCheckpoint(
                        tuple(items[:cut]),
                        groups[:cut],
                        native,
                        blocked_ms,
                        state_count,
                        work,
                    )
                    break
            raise mf.NoCandidatePath(str(error), resume) from error
        if keep_checkpoints:
            item_ms = (
                items[k].first_tick.time_ms
                if items[k].kind == "chain"
                else items[k].start_ms
            )
            prefix_end_ms = max(prefix_end_ms, item_ms)
            if prefix_end_ms >= last_checkpoint_ms + mf.RETRY_REWIND_MS:
                checkpoints.append(
                    (prefix_end_ms, k + 1, search.checkpoint(), states, visited)
                )
                last_checkpoint_ms = prefix_end_ms
        reporter(
            "search",
            completed,
            len(groups),
            chart_ms=items[k].last_tick.time_ms,
            states=states,
            states_completed=states,
            candidates=len(group),
            visited=visited,
        )
    reporter("finalizing")
    result = json.loads(search.result())
    touches, cost, step_costs, skipped = _validate(
        result, graph, items, candidates, reporter
    )
    arms = result.get("arms")
    crossings, travel, last = (), (), None
    if arms:
        import numpy as np
        from .arm_crossings import (
            ArmState,
            HandPath,
            FreeTravel,
            Crossing,
            CrossingGraph,
        )

        paths = tuple(
            HandPath(
                np.asarray(p["points"]),
                p["anchor"],
                np.asarray(p["locked"], dtype=bool),
                tuple(FreeTravel(**g) for g in p["travel"]),
            )
            for p in arms["paths"]
        )
        crossings = tuple(
            Crossing(**{**c, "top": mf.HANDS[c["top"]]}) for c in arms["routes"]
        )
        arm_state = ArmState(paths, crossings)
        # Check the finished route's contact locks and all upper-pad overlaps.
        from .arm_crossings import locked_overpass

        for c in crossings:
            if locked_overpass(
                paths[0].points,
                paths[1].points,
                paths[mf.HANDS.index(c.top)].locked,
                c.start_frame,
                c.end_frame if c.end_frame is not None else arm_state.through,
            ):
                raise RuntimeError("native arm route overlaps a locked upper contact")
        travel = CrossingGraph.travel_segments(arm_state)
        last = arm_state.through
        cost = replace(
            cost,
            base=replace(
                cost.base,
                crossing_seconds=arm_state.crossed_seconds,
                distance_m=cost.base.distance_m + arm_state.extra_distance_m,
            ),
        )
    native_cost = _cost(result["cost"])
    for field in fields(cost):
        actual = getattr(cost, field.name)
        expected = getattr(native_cost, field.name)
        pairs = (
            zip(asdict(actual).values(), asdict(expected).values())
            if field.name == "base"
            else [(actual, expected)]
        )
        if any(not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) for a, b in pairs):
            raise RuntimeError(
                f"native cost mismatch in {field.name}: {actual} != {expected}"
            )
    step_costs = tuple(
        (items[step["item"]].start_ms, _cost(step["increment"]))
        for step in result["steps"]
    )
    plan = mf.MultiPlan(
        touches,
        cost,
        options,
        multi,
        result["states_visited"],
        result["pruned_items"],
        step_costs=step_costs,
        skipped=skipped,
        arm_crossings=crossings,
        crossing_last_frame=last,
        crossing_travel=travel,
    )
    reporter("done", len(groups), len(groups), visited=plan.states_visited)
    return plan

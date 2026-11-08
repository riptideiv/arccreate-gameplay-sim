"""Plan an AFF window with one index per hand."""

import argparse
from dataclasses import replace
import json
import re
import time
from pathlib import Path

from arc_hand_order import api
from arc_hand_order.chart.arc_connections import connect_arcs
from arc_hand_order.planning.occupations import occupations_from_chart, TimeWindow


def split_arc_ticks(occupations):
    items = []
    for item in occupations.items:
        if item.kind != "chain":
            items.append(item)
            continue
        for point in item.score_points:
            token = f"arcTick:{point.source_event_id}:{point.tick_index}"
            items.append(replace(item, occupation_id=token, parent_occupation_id=item.occupation_id,
                                 source_event_ids=(point.source_event_id,), score_points=(point,),
                                 down_window=point.contact_window,
                                 up_window=TimeWindow(point.contact_window.earliest_ms+1,None)))
    return replace(occupations, items=tuple(sorted(items,key=lambda o:(o.first_tick.time_ms if o.kind=="chain" else o.start_ms,
                                                                    o.end_ms,o.occupation_id))))


def create_plan(chart_path, start_ms, end_ms, settings=None, prefer_same_hand=False,
                excluded=(), avoid_contacts=(), progress=None, *, cache_dir=None):
    """Library calls default to uncached. CLI/dashboard opt into completed reuse."""
    if cache_dir is None:
        return _create_plan(chart_path,start_ms,end_ms,settings,prefer_same_hand,excluded,avoid_contacts,progress)
    from plan_cache import request_key, read, write
    began=time.monotonic()
    excluded,avoid_contacts=tuple(excluded),tuple(avoid_contacts)
    key,request=request_key(chart_path,start_ms,end_ms,settings,prefer_same_hand,excluded,avoid_contacts)
    cached=read(cache_dir,key,request)
    if cached is not None:
        if progress:progress({'phase':'done','completed':1,'total':1,'cache_hit':True,
                              'attempt':0,'elapsed_s':round(time.monotonic()-began,3)})
        return cached
    result=_create_plan(chart_path,start_ms,end_ms,settings,prefer_same_hand,excluded,avoid_contacts,progress)
    write(cache_dir,key,request,result)
    return result


def _create_plan(chart_path, start_ms, end_ms, settings=None, prefer_same_hand=False, excluded=(), avoid_contacts=(), progress=None):
    if not 0 <= start_ms < end_ms:
        raise ValueError("Expected 0 <= start_ms < end_ms")
    settings = settings or {}
    began = time.monotonic()
    attempt = 1
    def report(event):
        if progress:
            progress({**event, 'attempt': attempt, 'elapsed_s': round(time.monotonic() - began, 1)})
    report({'phase': 'loading', 'completed': None, 'total': None})
    loaded = api.load_chart(chart_path)
    chart = loaded.chart.model_copy(deep=True)
    source_offset_ms = chart.audio_offset_ms
    # A negative source offset must not put visible excerpt contacts in negative
    # animation frames. Keep source audio timing separate from the sample grid.
    chart.audio_offset_ms = max(0, source_offset_ms)
    red_arcs = {e.event_id for e in chart.events if e.type == 'arc' and e.is_designant}
    red_taps = {e.event_id for e in chart.events if e.type == 'arctap' and e.parent_arc_id in red_arcs}
    enable_red = settings.get('enable_red_notes', True)
    target_red = enable_red and settings.get('target_all_red_notes', False)
    chart.events = [e for e in chart.events if (enable_red or e.event_id not in red_arcs)
                    and (target_red or e.event_id not in red_taps)]
    events = [
        event for event in chart.events
        if (event.type == "timing" and event.start_time_ms < end_ms)
        or (event.start_time_ms < end_ms and event.end_time_ms >= start_ms)
    ]
    parent_ids = {event.parent_arc_id for event in events if event.type == "arctap"}
    event_ids = {event.event_id for event in events}
    events.extend(event for event in chart.events
                  if event.event_id in parent_ids and event.event_id not in event_ids)
    for event in events:
        event.start_time_ms -= start_ms
        event.end_time_ms -= start_ms
    chart.events = events
    connect_arcs(chart.events, chart.timinggroups)
    excerpt = replace(loaded, parsed=replace(loaded.parsed, chart=chart))
    # Build ticks from the original note spans, then restrict the planning task
    # to this window. Truncating AFF notes first would change tick placement.
    occupations = occupations_from_chart(chart)
    scoring = settings.get('arc_path_mode','trace') == 'score'
    if scoring:
        from arc_hand_order.planning.arc_scoring import native_occupations
        early = {g.group_id for g in chart.timinggroups if 'earlyjudge' in g.raw_attributes}
        density=re.search(r'(?m)^TimingPoints?DensityFactor:([\d.]+)',Path(chart_path).read_text())
        density=float(density[1]) if density else 1.
        if density<=0:raise ValueError('TimingPointDensityFactor must be positive')
        occupations = native_occupations(occupations,early,{e.event_id:e.timinggroup_id for e in chart.events},density,loaded.chart.events)
    items = []
    duration = end_ms - start_ms
    for item in occupations.items:
        points = tuple(replace(point, contact_window=TimeWindow(
            max(0, point.contact_window.earliest_ms), min(duration - 1, point.contact_window.latest_ms)))
            for point in item.score_points if 0 <= point.time_ms < duration
            # Arc.ResetJudgeTo(start) uses inclusive ComboAt(start). A tick
            # exactly at the seek boundary is already consumed by native input.
            and not (scoring and item.kind == 'chain'
                     and point.native_judge_ms is not None and point.native_judge_ms <= 0))
        if not points:
            continue
        items.append(replace(item, start_ms=max(0, item.start_ms), end_ms=min(duration, item.end_ms),
                             score_points=points,
                             down_window=TimeWindow(0, min(p.contact_window.latest_ms for p in points))
                             if item.continuous_contact else points[0].contact_window,
                             up_window=TimeWindow(max(p.contact_window.earliest_ms for p in points) + 1, None)
                             if item.continuous_contact else item.up_window))
    occupations = replace(occupations, items=tuple(items))
    native_items = occupations.items
    multi = {"second_finger": False, "second_finger_again": False}
    if scoring:
        multi.update(tick_contacts=True, max_states=settings.get('search_width',256), meeting_swaps=False,
                     covered_holds=False, covered_arcs=False, steady_arcs=False, enveloped_arcs=False)
    planner = {
        'off_center_contacts': settings.get('off_center_contacts', True),
        'apart_weight': 20 if settings.get('off_center_contacts', True) else 10,
        'apart_margin_m': .036 if settings.get('off_center_contacts', True) else .03,
        'scoring_arcs': scoring,
        'prefer_arc_trace': settings.get('prefer_arc_trace', False),
        'arc_trace_weight': settings.get('arc_trace_weight', 1000),
        'acceleration_weight': settings.get('acceleration_weight', 0),
        "contact_exclusions": tuple((x['parent'],x['start_ms'],x['end_ms']) for x in avoid_contacts),
        "full_arc_hold": settings.get("full_arc_hold", False),
        "partial_hold_weight": settings.get("partial_hold_weight", 1000),
        "full_ground_hold": settings.get("full_ground_hold", True),
        "partial_ground_hold_weight": settings.get("partial_ground_hold_weight", 500),
    }
    if settings:
        planner["speed_max_m_s"] = settings.get("max_speed_m_s",3.0)
        planner["speed_comfort_m_s"] = min(settings.get("comfort_speed_m_s", 0.25),
                                          planner["speed_max_m_s"])
        planner["crossing_weight"] = settings.get("crossing_weight", 1)
        multi["accel_max_m_s2"] = settings.get("planner_accel_m_s2", 75)
        legacy_same_hand = settings.get("prefer_same_hand", prefer_same_hand)
        planner["repeat_weight"] = settings.get("repeat_weight", 0 if legacy_same_hand else 5)
        planner["repeat_comfort_s"] = settings.get("repeat_comfort_ms", 180) / 1000
        multi["repeat_comfort_s"] = planner["repeat_comfort_s"]
        multi["jack_weight"] = settings.get("jack_weight", 0 if legacy_same_hand else 20)
        multi["jack_gap_ms"] = settings.get("jack_gap_ms", 150)
        multi["jack_free"] = settings.get("jack_free", 4)
        planner["point_contact_frames"] = settings.get("point_contact_frames", 2)
        planner["min_air_frames"] = settings.get("min_air_frames", 1)
        planner["held_release_air_frames"] = settings.get("held_release_air_frames", 4)
    elif prefer_same_hand:
        multi["jack_weight"] = 0
        planner["repeat_weight"] = 0
    try:
        if excluded or avoid_contacts:
            raise ValueError("no candidate path: retrying verified motion")
        result = api.plan_chart(excerpt, multi=multi, planner=planner, occupation_set=occupations, progress=report)
    except ValueError as error:
        if "no candidate path" not in str(error):
            raise
        # Preserve scoring ticks before ergonomic costs. Covered-note shortcuts depend
        # on other contacts existing, so the skip-capable search plans them explicitly.
        occupations = split_arc_ticks(occupations)
        attempt = 2
        multi.update(allow_misses=settings.get("allow_misses",True), forced_misses=tuple(excluded), tick_contacts=True,
                     max_states=settings.get("search_width", 256), meeting_swaps=False,
                     covered_holds=False, covered_arcs=False, steady_arcs=False, enveloped_arcs=False)
        result = api.plan_chart(excerpt, multi=multi, planner=planner, occupation_set=occupations, progress=report)
    placement = None
    if planner['off_center_contacts']:
        report({'phase': 'placement', 'completed': None, 'total': None})
        from contact_placement import refine
        result, placement = refine(result)
    report({'phase': 'writing', 'completed': None, 'total': None})
    output = api.plan_to_dict(result)
    if placement is not None: output['contact_placement'] = placement
    from arc_hand_order.planning.trajectory import motion_metrics
    output['chart']['source_audio_offset_ms'] = source_offset_ms
    # A point at the excerpt boundary may have an early press in frame -1.
    # Keep only the visible portion of that contact in the playback timeline.
    for touch in output["touches"]:
        if touch["down_frame"] < 0:
            touch["path_mm"] = touch["path_mm"][-touch["down_frame"]:]
            touch["down_frame"] = 0
            touch["down_ms"] = -chart.audio_offset_ms
            for part in touch["parts"]:
                part["from_frame"] = max(0, part["from_frame"])
                part["from_ms"] = max(-chart.audio_offset_ms, part["from_ms"])
    output["misses"] = [{"events": list(o.source_event_ids), "kind": "red_sky" if o.is_red else "arc_tick" if o.parent_occupation_id else o.kind,
                         "tick_id": o.occupation_id if o.parent_occupation_id else None,
                         "parent_occupation_id": o.parent_occupation_id,
                         "start_ms": o.first_tick.time_ms if o.parent_occupation_id else o.start_ms,
                         "end_ms": o.last_tick.time_ms if o.parent_occupation_id else o.end_ms, "scoring_ticks": o.score_weight,
                         "reason": "motion limits" if set(o.source_event_ids) & set(excluded) or any(x['parent']==o.parent_occupation_id and
                             x['start_ms'] <= o.last_tick.contact_window.latest_ms and x['end_ms'] >= o.first_tick.contact_window.earliest_ms for x in avoid_contacts)
                         else "no feasible contact in the bounded search"}
                        for o in result.plan.skipped]
    parents = {}
    for touch in output['touches']:
        for part in touch['parts']:
            if part.get('parent_occupation_id'):
                parents.setdefault(part['parent_occupation_id'],set()).add(touch['id'])
    if scoring:
        kept={p['id'] for t in output['touches'] for part in t['parts'] for p in part.get('scoring_ticks',[])}
        by_id={e.event_id:e for e in chart.events}
        output['arc_tick_targets']=[]
        for o in native_items:
            if o.kind!='chain':continue
            for p in o.score_points:
                e=by_id[p.source_event_id]
                token=f'arcTick:{p.source_event_id}:{p.tick_index}'
                output['arc_tick_targets'].append({'id':token,'arc_start_ms':e.start_time_ms+start_ms,
                    'arc_end_ms':e.end_time_ms+start_ms,'color':e.color,'auto_ms':p.native_auto_ms+start_ms,
                    'time_ms':p.time_ms+start_ms,'x_start':e.start_aff_xy[0],'x_end':e.end_aff_xy[0],
                    'y_start':e.start_aff_xy[1],'y_end':e.end_aff_xy[1],'planned_hit':token in kept})
    output["planning"] = {"arc_path_mode": 'score' if scoring else 'trace',
                          "input_model": 'native-screen-world-camera-envelope' if scoring else 'legacy-trace',
                          "skipped_ticks": result.plan.cost.missed_ticks,
                          "red_notes_enabled": enable_red, "target_all_red_notes": target_red,
                          "skipped_red_notes": result.plan.cost.missed_red_notes,
                          "planned_red_notes": sum(o.is_red for o in native_items) - result.plan.cost.missed_red_notes,
                          "tick_contacts": result.plan.multi.tick_contacts,
                          "arc_reacquisitions": sum(len(ids)-1 for ids in parents.values()),
                          "contact_exclusions": list(avoid_contacts),
                          "pruned_items": result.plan.pruned_items,
                          "policy": ("score ticks first, then red catches, then movement cost; bounded candidate search" if target_red
                                     else "score ticks first, then movement cost; bounded candidate search")}
    output["preview"] = {
        "chart_start_ms": start_ms,
        "chart_end_ms": end_ms,
        "audio_start_ms": start_ms + source_offset_ms,
        "duration_ms": end_ms - start_ms,
    }
    output['acceleration'] = motion_metrics(output['touches'])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chart")
    parser.add_argument("start_ms", type=int)
    parser.add_argument("end_ms", type=int)
    parser.add_argument("output", type=Path)
    parser.add_argument("--prefer-same-hand", action="store_true")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--arc-path-mode", choices=('score','trace'), help='Default for new CLI plans: score; old settings files retain tracing')
    parser.add_argument("--exclude-event", action="append", default=[])
    parser.add_argument("--avoid-contacts", type=Path)
    parser.add_argument("--progress", action="store_true", help="Stream machine-readable planning progress to stdout")
    parser.add_argument('--no-plan-cache', action='store_true', help='Run the search without completed-plan disk reuse')
    parser.add_argument('--plan-cache-dir', type=Path, default=Path(__file__).resolve().parent/'output/contact-plans')
    args = parser.parse_args()
    try:
        settings = json.loads(args.settings.read_text()) if args.settings else {'arc_path_mode':'score'}
        if args.arc_path_mode:settings['arc_path_mode']=args.arc_path_mode
        avoid = json.loads(args.avoid_contacts.read_text()) if args.avoid_contacts else []
        def progress(event):
            print('PLANNER_PROGRESS ' + json.dumps(event), flush=True)
        output = create_plan(args.chart, args.start_ms, args.end_ms, settings, args.prefer_same_hand, args.exclude_event, avoid,
                             progress=progress if args.progress else None,
                             cache_dir=None if args.no_plan_cache else args.plan_cache_dir)
    except ValueError as error:
        parser.exit(1, f"error: {error}\n")
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(f"{args.output}: {len(output['touches'])} touches")


if __name__ == "__main__":
    main()

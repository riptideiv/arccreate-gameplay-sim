from dataclasses import replace
from itertools import product
import math
from pathlib import Path

import pytest

from arc_hand_order.chart.parser import parse_aff
from arc_hand_order.planning.occupations import occupations_from_chart
from arc_hand_order.planning.occupations import FingerId
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning.two_finger import (
    CandidateGraph, HandoverNode, PlannerOptions, build_candidates, frame_ms, handover_nodes,
    make_handover_candidate, plan_report, plan_two_finger, target_xy,
)
from arc_hand_order.schemas.device import DeviceProfile


@pytest.fixture
def projection():
    path = Path(__file__).resolve().parents[1] / "src/arc_hand_order/devices/ipad_10_9.json"
    return GameplayProjection(DeviceProfile.model_validate_json(path.read_text(encoding="utf-8")))


def test_finite_dp_matches_independent_exhaustive_cost(projection):
    chart = parse_aff("-\ntiming(0,120,4);\n(1000,1);(1080,4);(1200,2);(1240,3);"
                      "arc(1400,1700,.3,.7,s,.5,.5,0,none,false);").chart
    occupations = occupations_from_chart(chart)
    options = PlannerOptions(offsets_ms=(-33, 0, 33), speed_max_m_s=2)
    groups = build_candidates(occupations, projection, options)
    best, feasible = math.inf, 0
    for choices in product(*(tuple((c, hand) for c in group for hand in ("left", "right")) for group in groups)):
        if any(a.down_frame > b.down_frame for (a, _), (b, _) in zip(choices, choices[1:])):
            continue
        move, distance, valid = 0.0, 0.0, True
        for hand in ("left", "right"):
            mine = [c for c, h in choices if h == hand]
            for a, b in zip(mine, mine[1:]):
                if b.down_frame - a.up_frame < options.min_air_frames:
                    valid = False
                    break
                transfer = math.dist(a.last_xy, b.first_xy)
                distance += transfer
                speed = transfer / ((b.down_ms - a.up_ms) / 1000)
                if speed > options.speed_max_m_s:
                    valid = False
                    break
                move += max(0.0, speed - options.speed_comfort_m_s) ** 2
        if not valid:
            continue
        colour = sum(c.occupation.colour in (0, 1) and h != ("left" if c.occupation.colour == 0 else "right")
                     for c, h in choices)
        crossing = 0.0
        for left, hand in choices:
            if hand != "left":
                continue
            for right, other in choices:
                if other == "right":
                    crossing += sum(target_xy(left.occupation, frame_ms(f), projection)[0]
                                    > target_xy(right.occupation, frame_ms(f), projection)[0]
                                    for f in range(max(left.down_frame, right.down_frame), min(left.up_frame, right.up_frame))) / 60
        feasible += 1
        best = min(best, colour * options.colour_weight + crossing * options.crossing_weight
                   + move * options.movement_weight + distance * options.distance_weight)
    result = plan_two_finger(occupations, projection, options)
    assert feasible > 10
    assert result.cost.weighted(options) == pytest.approx(best, abs=1e-12)


def test_taps_cross_the_hands_only_while_nothing_weighs_where_the_other_hand_is(projection):
    # Two taps on the outer lanes, then the same two lanes one after the other.
    # All travel is below the comfort speed and the later taps do not overlap,
    # so without the apart term playing them with the hands crossed costs the
    # same as not crossing: nothing.
    chart = parse_aff("-\ntiming(0,120,4);\n(1000,1);(1000,4);(1400,4);(1500,1);").chart
    occupations = occupations_from_chart(chart)
    options = PlannerOptions(offsets_ms=(0,), apart_weight=10., distance_weight=0.)
    groups = build_candidates(occupations, projection, options)
    graph = CandidateGraph(groups, projection, options)

    def hands(pair, crossed):
        natural = ("left", "right") if pair[0][0].first_xy[0] < pair[1][0].first_xy[0] else ("right", "left")
        return natural[::-1] if crossed else natural

    def charge(assignment, weights):
        state, apart, total = (-1, -1), 0., 0.
        for group, hand in zip(groups, assignment):
            state, cost = graph.increment(state, group[0], hand)
            apart, total = apart+cost.apart, total+cost.weighted(weights)
        return apart, total

    straight = hands(groups[:2], False)+hands(groups[2:], False)
    crossed = hands(groups[:2], False)+hands(groups[2:], True)
    lane_gap = abs(groups[0][0].first_xy[0]-groups[1][0].first_xy[0])
    assert charge(straight, options) == (0., 0.)
    # The first hand to arrive lands where the other was 0.37 s ago, which
    # counts for the margin only; the second then starts a whole lane gap
    # beyond a hand that has only just lifted.
    first = (graph.nominal(groups[2][0].id)[0]-graph.nominal(groups[0][0].id)[1])/1000
    idle = (graph.nominal(groups[3][0].id)[0]-graph.nominal(groups[2][0].id)[1])/1000
    assert .3 < first < .4 and 0 < idle < .1
    assert charge(crossed, options)[0] == pytest.approx(
        (1-first/options.apart_memory_s)*options.apart_margin_m
        + (1-idle/options.apart_memory_s)*(lane_gap+options.apart_margin_m))
    assert charge(crossed, PlannerOptions(offsets_ms=(0,),distance_weight=0.))[1] == 0.
    plan = plan_two_finger(occupations, projection, options)
    assert tuple(hand for _, hand in plan.choices) == straight and plan.cost.apart == 0.


def test_a_quick_repeat_is_charged_by_the_note_times_not_by_the_chosen_frames(projection):
    chart = parse_aff("-\ntiming(0,120,4);\n(1000,2);(1150,2);").chart
    occupations = occupations_from_chart(chart)
    options = PlannerOptions(repeat_weight=1.)
    groups = build_candidates(occupations, projection, options)
    graph = CandidateGraph(groups, projection, options)
    charges = set()
    for a in groups[0]:
        for b in groups[1]:
            state, _ = graph.increment((-1, -1), a, "left")
            step = graph.increment(state, b, "left")
            if step is not None:
                charges.add(round(step[1].repeat, 12))
    assert len(groups[0]) > 1 and len(charges) == 1 and 0 < charges.pop() < 1
    plan = plan_two_finger(occupations, projection, options)
    assert {hand for _, hand in plan.choices} == {"left", "right"}


def test_two_near_taps_each_have_a_contact_of_their_own(projection):
    chart = parse_aff("-\ntiming(0,120,4);(1000,3);(1040,3);").chart
    plan = plan_two_finger(occupations_from_chart(chart), projection, PlannerOptions(offsets_ms=(0,)))
    contacts = [c for c, _ in plan.choices]
    assert len(contacts) == 2 and contacts[0].id != contacts[1].id
    assert all(c.up_frame > c.down_frame for c in contacts)


def test_the_audio_offset_sets_the_frame_grid(projection):
    chart = parse_aff("AudioOffset:-1256\n-\ntiming(0,120,4);"
                      "arc(2513,3513,.8,.8,s,.5,.5,0,none,false);").chart
    plan = plan_two_finger(occupations_from_chart(chart), projection)
    candidate = plan.choices[0][0]
    assert candidate.down_ms == round(candidate.down_frame * 1000 / 60) + 1256
    assert candidate.down_ms % 50 != 0  # offset-free grid would use multiples of 50 every third frame


def test_contact_sample_speed_is_a_hard_candidate_constraint(projection):
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,2000,0,1,s,.5,.5,0,none,false);").chart
    with pytest.raises(ValueError, match="no candidate path"):
        plan_two_finger(occupations_from_chart(chart), projection, PlannerOptions(speed_max_m_s=.001))


def test_strict_point_window_filters_actual_quantized_frames(projection):
    chart = parse_aff("AudioOffset:-1256\n-\ntiming(0,120,4);(3000,2);"
                      "arc(3500,3900,.5,.5,s,.5,.5,0,none,false);").chart
    occupations = occupations_from_chart(chart)
    options = PlannerOptions(offsets_ms=tuple(range(-25, 26)))
    raw = build_candidates(occupations, projection, options)
    strict = build_candidates(occupations, projection, replace(options, point_window_ms=25))
    assert any(abs(c.down_ms-3000) > 25 for c in raw[0])
    assert {c.down_ms for c in strict[0]} == {c.down_ms for c in raw[0] if abs(c.down_ms-3000) <= 25}
    # The new point target must not move the continuous arc's scoring span.
    assert [(c.down_frame, c.up_frame) for c in strict[1]] == [(c.down_frame, c.up_frame) for c in raw[1]]
    plan = plan_two_finger(occupations, projection, replace(options, point_window_ms=25))
    assert abs(next(c.down_ms for c, _ in plan.choices if c.occupation.kind == "tap") - 3000) <= 25


def test_observed_meeting_can_transfer_a_chain_after_other_colour_lifted(projection):
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,3000,.5,.5,s,.5,.5,0,none,false);"
                      "arc(1000,1400,.7,.7,s,.5,.5,1,none,false);").chart
    occupations = occupations_from_chart(chart)
    base = plan_two_finger(occupations, projection)
    node = handover_nodes(occupations, base)[0]
    candidate = make_handover_candidate(base, node, node.occupation_ids[0], projection)
    # The red contact ends before this hand catches the blue suffix: its
    # lateral transfer must also count in the explicit-handover evaluator.
    red = next(c for c, _ in candidate.choices if c.occupation.colour == 1)
    blue_suffix = next(c for c, h in candidate.choices if c.occupation.colour == 0 and h == "right")
    expected_distance = math.dist(red.last_xy, blue_suffix.first_xy)
    assert expected_distance > 0
    assert candidate.cost.distance_m == pytest.approx(expected_distance)
    segments = [s for s in candidate.contact_segments() if s.occupation_id == node.occupation_ids[0]]
    assert len(segments) == 2 and segments[0].finger != segments[1].finger
    assert segments[0].up_ms == segments[1].down_ms == node.chart_ms
    assert segments[0].lifecycle_id != segments[1].lifecycle_id
    assert candidate.target_at(segments[0].finger, node.chart_ms, projection) is None
    assert candidate.target_at(segments[1].finger, node.chart_ms, projection) == segments[1].target_at(node.chart_ms, projection)
    invalid = HandoverNode(132, 2200, node.arc_pairs, node.occupation_ids)
    with pytest.raises(ValueError, match="no two colours within the grace distance"):
        make_handover_candidate(base, invalid, node.occupation_ids[0], projection)
    assert "not asserted" in candidate.optimality_scope


def test_planned_reverse_catch_splits_the_chain_at_a_meeting_and_keeps_colours_swapped(projection):
    # Blue holds the centre; red meets it and leaves; a later red arc in the
    # swapped span goes to the left hand, the blue suffix to the right.
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,3000,.5,.5,s,.5,.5,0,none,false);"
                      "arc(1000,1400,.7,.7,s,.5,.5,1,none,false);"
                      "arc(2000,2600,.2,.2,s,.5,.5,1,none,false);").chart
    occupations = occupations_from_chart(chart)
    base = plan_two_finger(occupations, projection)
    node = handover_nodes(occupations, base)[0]
    options = PlannerOptions(handovers=(node.chart_ms,), reversed_spans=((node.chart_ms, 3200),))
    plan = plan_two_finger(occupations, projection, options)
    blue = [(s.finger.hand, s.down_ms, s.up_ms) for s in plan.contact_segments() if s.occupation.colour == 0]
    assert [hand for hand, _, _ in blue] == ["left", "right"] and blue[0][2] == blue[1][1]
    assert next(s.finger.hand for s in plan.contact_segments() if s.occupation.start_ms == 2000) == "left"
    assert plan.cost.colour == 0
    with pytest.raises(ValueError, match="meets another colour"):
        plan_two_finger(occupations, projection, PlannerOptions(handovers=(2200,)))
    # This meeting is right at the start of the blue contact: no room to catch early.
    with pytest.raises(ValueError, match="before the chain"):
        plan_two_finger(occupations, projection, replace(options, handover_lead_frames=2))


def test_reverse_catch_can_catch_first_and_let_go_after(projection):
    # Red comes in from the side and meets the blue centre arc midway.
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,3000,.5,.5,s,.5,.5,0,none,false);"
                      "arc(1600,2000,.9,.6,s,.5,.5,1,none,false);"
                      "arc(2400,2900,.2,.2,s,.5,.5,1,none,false);").chart
    occupations = occupations_from_chart(chart)
    nodes = handover_nodes(occupations, plan_two_finger(occupations, projection))
    node = nodes[len(nodes) // 2]
    # The swapped span begins before the meeting so that it holds the early catch.
    options = PlannerOptions(handovers=(node.chart_ms,), reversed_spans=((node.chart_ms - 100, 3200),),
                             handover_lead_frames=2)
    plan = plan_two_finger(occupations, projection, options)
    blue = [s for s in plan.contact_segments() if s.occupation.colour == 0]
    assert [s.finger.hand for s in blue] == ["left", "right"]
    assert blue[1].candidate.down_frame == blue[0].candidate.up_frame - 2
    assert plan.cost.colour == 0


def test_x_swap_lets_both_fingers_go_straight_on_where_two_chains_cross(projection):
    # Blue comes in from the right and red from the left; both turn back at
    # the centre node. Swapped, each finger keeps going the way it came.
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,1400,.9,.5,s,.5,.5,0,none,false);"
                      "arc(1400,1800,.5,.9,s,.5,.5,0,none,false);"
                      "arc(1000,1400,.1,.5,s,.5,.5,1,none,false);"
                      "arc(1400,1800,.5,.1,s,.5,.5,1,none,false);").chart
    occupations = occupations_from_chart(chart)
    options = PlannerOptions(swaps=(1400,), reversed_spans=((1400, 2000),))
    plan = plan_two_finger(occupations, projection, options)
    segments = {s.finger.hand: s for s in plan.contact_segments()}
    assert len(plan.choices) == 2 and plan.cost.colour == 0
    left, right = segments["left"], segments["right"]
    assert ">" in left.occupation_id and left.occupation.colour == 0
    assert left.target_at(1100, projection)[0] > 0 > left.target_at(1700, projection)[0]
    assert right.target_at(1100, projection)[0] < 0 < right.target_at(1700, projection)[0]
    for time in range(1380, 1430, 5):
        assert math.dist(left.target_at(time, projection), right.target_at(time, projection)) < .01
    with pytest.raises(ValueError, match="no two chains"):
        plan_two_finger(occupations, projection, PlannerOptions(swaps=(1200,)))


def _contacts(plan):
    return [(hand, c.occupation.kind, c.down_ms, c.up_ms, c) for c, hand in plan.choices]


def test_full_arc_hold_gives_a_note_at_the_arc_start_to_the_other_hand(projection):
    # A red arc and a tap start together; the tap's lane is under the arc's start.
    chart = parse_aff("-\ntiming(0,120,4);(1000,3);arc(1000,1600,.75,.75,s,.5,.5,1,none,false);").chart
    occupations = occupations_from_chart(chart)
    plan = plan_two_finger(occupations, projection, PlannerOptions(full_arc_hold=True, offsets_ms=(0,)))
    contacts = _contacts(plan)
    arc_hand, _, down, up, arc = next(row for row in contacts if row[1] == "chain")
    tap_hand = next(row[0] for row in contacts if row[1] == "tap")
    assert tap_hand != arc_hand
    assert arc.untouched_s == 0 and down <= 1000 + 17 and up > 1600


def test_full_arc_hold_gives_a_note_at_the_arc_end_to_the_other_hand(projection):
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,1600,.25,.5,s,.5,.5,0,none,false);"
                      "arc(1600,1600,.5,.5,s,0,0,0,none,true)[arctap(1600)];").chart
    occupations = occupations_from_chart(chart)
    plan = plan_two_finger(occupations, projection, PlannerOptions(full_arc_hold=True, offsets_ms=(0,)))
    contacts = _contacts(plan)
    arc_hand, _, _, up, _ = next(row for row in contacts if row[1] == "chain")
    sky_hand = next(row[0] for row in contacts if row[1] == "sky")
    assert sky_hand != arc_hand and up > 1600
    # The default policy lets the arc go at its last tick and needs no second hand for the note.
    default = plan_two_finger(occupations, projection, PlannerOptions(offsets_ms=(0,)))
    assert next(c.up_ms for c, _ in default.choices if c.occupation.kind == "chain") < 1600


def test_full_arc_hold_falls_back_to_a_charged_partial_hold_only_where_needed(projection):
    # Both colours are held while a tap comes near their end: two index fingers
    # cannot hold both arcs through it, so one arc lets go after its last tick.
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,2000,.25,.25,s,.5,.5,0,none,false);"
                      "arc(1000,2000,.75,.75,s,.5,.5,1,none,false);(1950,2);").chart
    occupations = occupations_from_chart(chart)
    plan = plan_two_finger(occupations, projection, PlannerOptions(full_arc_hold=True, offsets_ms=(0,)))
    partial = [c for c, _ in plan.choices if c.untouched_s > 0]
    assert len(partial) == 1
    assert plan.cost.untouched_arc_s == pytest.approx(partial[0].untouched_s)
    report = plan_report(plan, projection)
    assert report["arc_hold"]["partial_holds"][0]["untouched_ms"] > 0
    # Without the tap both arcs are held whole.
    whole = parse_aff("-\ntiming(0,120,4);arc(1000,2000,.25,.25,s,.5,.5,0,none,false);"
                      "arc(1000,2000,.75,.75,s,.5,.5,1,none,false);").chart
    plan = plan_two_finger(occupations_from_chart(whole), projection, PlannerOptions(full_arc_hold=True, offsets_ms=(0,)))
    assert all(c.untouched_s == 0 for c, _ in plan.choices) and plan.cost.untouched_arc_s == 0


def test_full_ground_hold_holds_to_the_end_and_gives_a_close_next_note_to_the_other_hand(projection):
    # A tap in the same lane 30 ms after the hold's end: no room for a visible lift on the holding hand.
    chart = parse_aff("-\ntiming(0,120,4);hold(1000,1500,2);(1530,2);").chart
    occupations = occupations_from_chart(chart)
    default = plan_two_finger(occupations, projection, PlannerOptions(offsets_ms=(0,)))
    assert next(c.up_ms for c, _ in default.choices if c.occupation.kind == "hold") < 1500
    options = PlannerOptions(offsets_ms=(0,), full_ground_hold=True, held_release_air_frames=4)
    plan = plan_two_finger(occupations, projection, options)
    hold_hand, _, _, up, hold = next(row for row in _contacts(plan) if row[1] == "hold")
    tap_hand = next(row[0] for row in _contacts(plan) if row[1] == "tap")
    assert hold.held_to_end and hold.untouched_s == 0 and up > 1500
    assert tap_hand != hold_hand
    report = plan_report(plan, projection)["arc_hold"]
    assert report["holds_held_to_end"] == 1 and report["held_release_air_frames"] == 4


def test_early_hold_spans_keep_the_tick_release(projection):
    chart = parse_aff("-\ntiming(0,120,4);hold(1000,1500,2);hold(3000,3500,3);").chart
    options = PlannerOptions(offsets_ms=(0,), full_ground_hold=True, early_hold_spans=((2900, 3100),))
    plan = plan_two_finger(occupations_from_chart(chart), projection, options)
    ups = {c.occupation.start_ms: c for c, _ in plan.choices}
    assert ups[1000].held_to_end and ups[1000].up_ms > 1500
    assert not ups[3000].held_to_end and ups[3000].up_ms < 3500 and ups[3000].untouched_s == 0
    assert plan_report(plan, projection)["arc_hold"]["holds_in_early_spans"] == 1


def test_a_hold_lets_go_early_rather_than_an_arc_being_caught_late(projection):
    # Both colours start 30 ms after the hold ends, so both hands are needed on the arcs.
    chart = parse_aff("-\ntiming(0,120,4);hold(1000,1300,2);arc(1330,1900,0,0,s,.5,.5,0,none,false);"
                      "arc(1330,1900,1,1,s,.5,.5,1,none,false);").chart
    options = PlannerOptions(offsets_ms=(0,), full_arc_hold=True, full_ground_hold=True, held_release_air_frames=4)
    plan = plan_two_finger(occupations_from_chart(chart), projection, options)
    chains = [c for c, _ in plan.choices if c.occupation.kind == "chain"]
    hold = next(c for c, _ in plan.choices if c.occupation.kind == "hold")
    assert all(c.untouched_s == 0 for c in chains) and plan.cost.untouched_arc_s == 0
    assert hold.untouched_s > 0 and hold.up_ms < 1300 and plan.cost.untouched_hold_s == pytest.approx(hold.untouched_s)


def test_held_release_options_are_validated():
    with pytest.raises(ValueError, match="held_release_air_frames"):
        PlannerOptions(held_release_air_frames=-1)
    with pytest.raises(ValueError, match="held_release_air_kinds"):
        PlannerOptions(held_release_air_kinds=("tap",))


def test_early_arc_spans_keep_the_tick_rule_for_arcs_starting_or_ending_there(projection):
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,1600,.25,.25,s,.5,.5,0,none,false);"
                      "arc(3000,3600,.75,.75,s,.5,.5,1,none,false);").chart
    occupations = occupations_from_chart(chart)
    held = plan_two_finger(occupations, projection, PlannerOptions(full_arc_hold=True, offsets_ms=(0,)))
    spans = plan_two_finger(occupations, projection,
                            PlannerOptions(full_arc_hold=True, offsets_ms=(0,), early_arc_spans=((3500, 3700),)))
    first, second = (sorted(p.choices, key=lambda c: c[0].down_ms) for p in (held, spans))
    assert first[0][0].up_ms > 1600 and second[0][0].up_ms > 1600          # outside the span: held to the end
    assert first[1][0].up_ms > 3600 and second[1][0].up_ms < 3600          # ending in the span: tick rule
    report = plan_report(spans, projection)["arc_hold"]
    assert [row["start_ms"] for row in report["chains_in_early_spans"]] == [3000]


def test_crossing_passes_are_reported_and_can_be_forbidden(projection):
    # The right hand holds a long arc sweeping right to left; the left hand taps on its far side, then
    # flies across it to a tap on the other side.
    chart = parse_aff("-\ntiming(0,120,4);arc(1000,2000,1,0,s,.5,.5,1,none,false);(1100,1);(1300,4);").chart
    occupations = occupations_from_chart(chart)
    options = PlannerOptions(full_arc_hold=True, offsets_ms=(0,))
    plan = plan_two_finger(occupations, projection, options)
    passes = plan_report(plan, projection)["crossing_passes"]["passes"]
    left = [p for p in passes if p["hand"] == "left"]
    assert left and left[0]["air_ms"] < 250
    # Here the chart leaves no other way, so the rule leaves no path at all.
    with pytest.raises(ValueError, match="no candidate path"):
        plan_two_finger(occupations, projection, replace(options, crossing_pass_s=.25))
    assert plan_report(plan, projection)["crossing_passes"]["minimum_s"] == 0.


def test_fixed_hands_send_a_point_note_to_the_named_hand(projection):
    chart = parse_aff("-\ntiming(0,120,4);(1000,1);(2000,4);").chart
    occupations = occupations_from_chart(chart)
    free = {c.occupation.start_ms: hand for c, hand in plan_two_finger(occupations, projection, PlannerOptions(offsets_ms=(0,))).choices}
    assert free == {1000: "left", 2000: "right"}
    fixed = plan_two_finger(occupations, projection, PlannerOptions(offsets_ms=(0,), fixed_hands=((1000, "right"),)))
    assert {c.occupation.start_ms: hand for c, hand in fixed.choices}[1000] == "right"
    with pytest.raises(ValueError, match="left or right"):
        PlannerOptions(fixed_hands=((1000, "middle"),))

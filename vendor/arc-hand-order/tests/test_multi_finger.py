from dataclasses import replace
import math
from pathlib import Path

import pytest

from arc_hand_order.chart.parser import parse_aff
from arc_hand_order.planning.multi_finger import (
    RECOMMENDED, MultiCost, MultiFingerOptions, Regions, multi_report, plan_multi_finger,
)
from arc_hand_order.planning.occupations import occupations_from_chart
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning.two_finger import PlannerOptions, plan_two_finger
from arc_hand_order.schemas.device import DeviceProfile

FULL = PlannerOptions(offsets_ms=(0,), full_arc_hold=True, full_ground_hold=True, held_release_air_frames=4)
EVERYTHING = MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True)


@pytest.fixture
def projection():
    path = Path(__file__).resolve().parents[1] / "src/arc_hand_order/devices/ipad_10_9.json"
    return GameplayProjection(DeviceProfile.model_validate_json(path.read_text(encoding="utf-8")))


def chart_of(body):
    return parse_aff("AudioOffset:0\n-\ntiming(0,120,4);" + body).chart


def plan(chart, projection, multi, options=FULL):
    return plan_multi_finger(occupations_from_chart(chart), projection, options, multi)


def test_with_every_option_off_the_plan_is_the_two_finger_plan(projection):
    chart = chart_of("(1000,1);(1250,4);hold(1500,2200,2);arc(1600,2400,0.75,1.00,s,1.00,0.50,1,none,false);"
                     "(2600,3);arc(2700,3000,0.25,0.00,s,0.00,0.50,0,none,false);")
    occupations = occupations_from_chart(chart)
    old = plan_two_finger(occupations, projection, FULL)
    new = plan_multi_finger(occupations, projection, FULL, MultiFingerOptions())
    assert sorted((c.id, h) for c, h in old.choices) == sorted((c.id, h) for c, h in new.choices)
    assert new.cost.weighted(new.options, new.multi) == old.cost.weighted(old.options)
    assert all(len(t.touch.parts) == 1 and t.slot == 0 for t in new.touches)


def test_a_dotted_arc_is_followed_without_lifting(projection):
    # Two blue pieces 50 ms apart at one spot: shorter than the 4-frame lift after a full hold.
    chart = chart_of("arc(1000,1500,0.25,0.25,s,0.00,0.00,0,none,false);arc(1550,2000,0.25,0.25,s,0.00,0.00,0,none,false);")
    lifted = plan(chart, projection, MultiFingerOptions())
    assert len(lifted.touches) == 2
    assert {t.hand for t in lifted.touches} == {"left", "right"}  # the second piece needs the other hand
    slid = plan(chart, projection, MultiFingerOptions(bridge_max_gap_ms=150))
    touch, = slid.touches
    assert touch.hand == "left" and len(touch.touch.parts) == 2 and len(touch.touch.bridges) == 1
    assert multi_report(slid)["bridges"] == 1


def test_a_hold_under_an_arc_shares_one_touch(projection):
    # Two holds and two arcs over them at once: four notes, two hands.
    chart = chart_of("hold(1000,2000,2);hold(1000,2000,3);arc(1000,2000,0.25,0.25,s,0.00,0.00,0,none,false);"
                     "arc(1000,2000,0.75,0.75,s,0.00,0.00,1,none,false);")
    with pytest.raises(ValueError, match="no candidate path"):
        plan(chart, projection, MultiFingerOptions())
    shared = plan(chart, projection, MultiFingerOptions(share=True))
    assert len(shared.touches) == 2
    assert all({p.candidate.occupation.kind for p in t.touch.parts} == {"chain", "hold"} for t in shared.touches)
    assert multi_report(shared)["shared_touches"] == 2


def test_four_lanes_held_at_once_take_two_fingers_on_each_hand(projection):
    chart = chart_of("hold(1000,2000,1);hold(1000,2000,2);hold(1000,2000,3);hold(1000,2000,4);")
    with pytest.raises(ValueError, match="no candidate path"):
        plan(chart, projection, MultiFingerOptions(share=True))
    both = plan(chart, projection, MultiFingerOptions(second_finger=True))
    assert sorted((t.hand, t.slot) for t in both.touches) == [("left", 0), ("left", 1), ("right", 0), ("right", 1)]
    for hand, lanes in (("left", {1, 2}), ("right", {3, 4})):
        assert {p.candidate.occupation.lane for t in both.touches if t.hand == hand for p in t.touch.parts} == lanes
    assert both.cost.second_touches == 2 and both.cost.side_by_side_s > 0


def test_a_second_finger_must_be_within_reach(projection):
    # Both outer lanes held while a sky note is played at the top centre: one hand needs two fingers.
    chart = chart_of("hold(1000,2000,1);hold(1000,2000,4);arc(1500,1500,0.50,0.50,s,1.00,1.00,0,none,true)[arctap(1500)];")
    with pytest.raises(ValueError, match="no candidate path"):
        plan(chart, projection, MultiFingerOptions(second_finger=True, pair_max_m=0.03))
    result = plan(chart, projection, MultiFingerOptions(second_finger=True))
    sky = next(ft for ft in result.touches if ft.touch.parts[0].candidate.occupation.kind == "sky")
    hold = next(ft for ft in result.touches if ft.hand == sky.hand and ft.slot != sky.slot)
    f = sky.touch.down
    assert 0.015 <= abs(complex(*sky.touch.xy(f)) - complex(*hold.touch.xy(f))) <= 0.10


def test_a_hold_passes_to_the_other_finger_so_the_first_can_press_again(projection):
    # As at the start of Vicious [ANTi] Heroism: each hand holds a centre lane with one finger and an arc
    # with another; the arc comes down over that lane, takes the hold, and the first finger presses
    # the outer lane.
    chart = chart_of(
        "hold(1000,3000,2);hold(1000,3000,3);"
        "arc(1000,2000,-0.25,0.25,s,1.00,0.00,0,none,false);arc(2000,3000,0.25,0.25,s,0.00,0.00,0,none,false);"
        "arc(1000,2000,1.25,0.75,s,1.00,0.00,1,none,false);arc(2000,3000,0.75,0.75,s,0.00,0.00,1,none,false);"
        "hold(2300,3000,1);hold(2300,3000,4);")
    with pytest.raises(ValueError, match="no candidate path"):
        plan(chart, projection, MultiFingerOptions(second_finger=True))
    result = plan(chart, projection, EVERYTHING)
    assert result.cost.takeovers == 2
    for hand, centre, outer in (("left", 2, 1), ("right", 3, 4)):
        mine = [t.touch for t in result.touches if t.hand == hand]
        giver = next(t for t in mine if t.parts[0].candidate.occupation.lane == centre)
        receiver = next(t for t in mine if t.parts[0].candidate.occupation.kind == "chain")
        assert giver.up < receiver.up
        assert any(p.candidate.occupation.lane == centre for p in receiver.parts)
        assert any(p.candidate.occupation.lane == outer for t in mine for p in t.parts)


def test_shared_points_keep_their_margin_inside_every_region(projection):
    chart = chart_of("hold(1000,2000,2);arc(1000,2000,0.25,0.25,s,0.00,0.00,0,none,false);"
                     "hold(1000,2000,3);arc(1000,2000,0.75,0.75,s,0.00,0.00,1,none,false);")
    result = plan(chart, projection, MultiFingerOptions(share=True, share_margin_m=0.004))
    regions = Regions(projection)
    for ft in result.touches:
        t = ft.touch
        for f in range(t.down, t.up):
            occupations = [p.candidate.occupation for p in t.parts if p.start <= f < p.stop]
            ms = round(f * 1000 / 60)
            assert regions.shared(occupations, ms, 0.0039)  # non-empty with (just under) the margin
            x, y = t.xy(f)
            for occupation in occupations:
                region = occupation.region_at(ms)
                gx, gz = projection.device_to_ground(x, y)
                jx, jy = projection.device_to_judgement(x, y)
                assert region.contains(gx, gz) if region.lane is not None else region.contains(jx, jy)


@pytest.mark.parametrize("bad", [{"bridge_max_gap_ms": -1}, {"bridge_max_gap_ms": 1.5}, {"share_margin_m": -0.001},
                                 {"pair_min_m": 0.2}, {"second_finger_weight": float("nan")}, {"max_states": -1},
                                 {"forearm_near_m": -0.01}, {"apart_near_weight": float("inf")},
                                 {"shoulder_y_m": 0.1}, {"trade_ms": 1.5}, {"share_jump_max_m": -0.01},
                                 {"accel_max_m_s2": -1.0}, {"accel_weight": float("nan")},
                                 {"repeat_comfort_s": -0.1}, {"crossing_depth_weight": -1.0}])
def test_options_are_validated(bad):
    with pytest.raises(ValueError):
        MultiFingerOptions(**bad)


def test_arcs_of_both_colours_on_one_spot_share_a_touch_in_grace(projection):
    # A red and a blue arc lie together over each centre lane, each over a hold.
    chart = chart_of("hold(1000,2000,2);hold(1000,2000,3);"
                     "arc(1000,2000,0.25,0.25,s,0.00,0.00,0,none,false);arc(1000,2000,0.25,0.25,s,0.00,0.00,1,none,false);"
                     "arc(1000,2000,0.75,0.75,s,0.00,0.00,0,none,false);arc(1000,2000,0.75,0.75,s,0.00,0.00,1,none,false);")
    result = plan(chart, projection, MultiFingerOptions(share=True))
    assert len(result.touches) == 2
    for ft in result.touches:
        colours = {p.candidate.occupation.colour for p in ft.touch.parts if p.candidate.occupation.kind == "chain"}
        assert colours == {0, 1}


def test_arcs_of_two_colours_apart_never_share_a_touch(projection):
    # 300 game units apart: both inside one hitbox width, but outside the grace distance.
    chart = chart_of("arc(1000,2000,0.40,0.40,s,0.00,0.00,0,none,false);arc(1000,2000,0.75,0.75,s,0.00,0.00,1,none,false);")
    result = plan(chart, projection, MultiFingerOptions(share=True))
    assert len(result.touches) == 2


def test_two_fingers_never_hold_one_arc_colour_apart_outside_grace(projection):
    # Two blue arcs at once, far apart: blue binds one touch and takes no other.
    chart = chart_of("arc(1000,2000,0.00,0.00,s,0.00,0.00,0,none,false);arc(1000,2000,1.00,1.00,s,0.00,0.00,0,none,false);")
    with pytest.raises(ValueError, match="no candidate path"):
        plan(chart, projection, MultiFingerOptions(share=True, second_finger=True))


def test_after_grace_a_colour_binds_the_first_touch_in_its_hitbox(projection):
    from arc_hand_order.planning.multi_finger import FingerTouch, MultiCost, MultiPlan, Touch, _taken
    from arc_hand_order.planning.two_finger import frame_at_or_after

    # Blue at x 0.25 throughout; red at 0.40 until 1500 (in grace with blue until 2000) and at 0.75 from 2200.
    chart = chart_of("arc(1000,3000,0.25,0.25,s,0.00,0.00,0,none,false);arc(1000,1500,0.40,0.40,s,0.00,0.00,1,none,false);"
                     "arc(2200,2600,0.75,0.75,s,0.00,0.00,1,none,false);")
    occupations = occupations_from_chart(chart)
    blue, red, red_again = (projection.judgement_to_device(850 * x - 425, 100) for x in (0.25, 0.40, 0.75))

    def frame(ms):
        return frame_at_or_after(ms, 0)

    # The red finger presses first and rests at 0.40, inside blue's hitbox, until 2100; the blue finger stays on blue.
    red_finger = Touch(1, (), (), frame(1000), frame(2700), (red,) * (frame(2100) - frame(1000))
                       + (red_again,) * (frame(2700) - frame(2100)), 0)
    blue_finger = Touch(2, (), (), frame(1000) + 1, frame(3000), (blue,) * (frame(3000) - frame(1000) - 1), 0)
    touches = (FingerTouch(red_finger, "right", 0), FingerTouch(blue_finger, "left", 0))
    taken = _taken(MultiPlan(touches, MultiCost(), FULL, MultiFingerOptions(), 0), occupations, projection)

    def counted(arc, a, b):
        return [(arc, f) in taken for f in range(frame(a), frame(b))]

    assert all(counted(0, 1017, 2100))  # in grace either finger counts, then blue binds the red finger in its hitbox
    assert not any(counted(0, 2117, 2700))  # the red finger has left: blue takes no other until it lifts
    assert all(counted(0, 2700, 3000))  # then blue binds the blue finger
    assert not any(counted(2, 2200, 2600))  # and red never takes the finger that holds blue


def test_a_sky_note_runs_on_into_the_arc_it_starts(projection):
    # The right hand is busy with a red arc, so one left finger taps the sky note and stays on the blue arc.
    chart = chart_of("arc(900,2100,1.00,1.00,s,0.50,0.50,1,none,false);"
                     "arc(1000,1000,0.25,0.25,s,1.00,1.00,0,none,true)[arctap(1000)];"
                     "arc(1000,2000,0.25,0.00,s,1.00,0.00,0,none,false);")
    result = plan(chart, projection, MultiFingerOptions(share=True))
    left, = [ft.touch for ft in result.touches if ft.hand == "left"]
    assert [p.candidate.occupation.kind for p in left.parts] == ["sky", "chain"]


def test_a_hold_finger_keeps_out_of_the_hitbox_of_an_arc_that_starts_over_it(projection):
    # Blue starts over the held lane and moves away. Pressed first, a hold finger inside the hitbox when blue is
    # free would take the colour, and the arc's own finger would be refused once the arc leaves the hold.
    chart = chart_of("hold(1000,3000,2);arc(1500,2500,0.25,1.00,s,0.00,0.00,0,none,false);")
    result = plan(chart, projection, EVERYTHING)
    hold, = [ft.touch for ft in result.touches if ft.touch.parts[0].candidate.occupation.kind == "hold"]
    arc, = [ft.touch for ft in result.touches if ft.touch is not hold]
    box = Regions(projection).polygon(arc.parts[0].candidate.occupation, 1500)
    xy = hold.xy(arc.down)
    assert xy[1] < min(y for _, y in box) - EVERYTHING.share_margin_m  # lower in the lane, below the ground arc


def test_two_arcs_that_cross_at_a_node_swap_fingers_there(projection):
    # Blue from the left and red from the right meet in the middle at 2000 ms and go on to the far sides. Taken
    # plainly the hands cross for two seconds; swapped at the meeting each finger turns back on its own side.
    chart = chart_of("arc(1000,2000,0.00,0.50,s,0.00,0.00,0,none,false);arc(2000,4000,0.50,1.00,s,0.00,0.00,0,none,false);"
                     "arc(1000,2000,1.00,0.50,s,0.00,0.00,1,none,false);arc(2000,4000,0.50,0.00,s,0.00,0.00,1,none,false);")
    plain = plan(chart, projection, MultiFingerOptions(share=True, second_finger=True))
    assert plain.cost.base.crossing_seconds > 1.5 and plain.cost.base.handover == 0
    swaps = MultiFingerOptions(share=True, second_finger=True, meeting_swaps=True)
    swapped = plan(chart, projection, swaps)
    assert swapped.cost.base.crossing_seconds == 0 and swapped.cost.base.handover == 1
    assert len(multi_report(swapped)["meeting_switches"]) == 2
    left, = [ft.touch for ft in swapped.touches if ft.hand == "left"]
    right, = [ft.touch for ft in swapped.touches if ft.hand == "right"]
    assert left.xy(left.up - 1)[0] < right.xy(right.up - 1)[0]
    assert swapped.cost.weighted(swapped.options, swaps) < plain.cost.weighted(plain.options, plain.multi)


def test_a_finger_whose_arc_ends_at_a_meeting_goes_on_along_the_other(projection):
    # Blue ends in the middle where red arrives from the right; red goes on to the left while a hold on lane 4
    # needs a finger. Plainly the right hand follows red left and the left hand reaches over for the hold; relayed,
    # the left finger goes on along red and the right one lets go in the grace and takes the hold.
    chart = chart_of("arc(1000,2000,0.00,0.50,s,0.00,0.00,0,none,false);arc(1000,2000,1.00,0.50,s,0.00,0.00,1,none,false);"
                     "arc(2000,4000,0.50,0.00,s,0.00,0.00,1,none,false);hold(2300,4000,4);")
    plain = plan(chart, projection, MultiFingerOptions(share=True, second_finger=True))
    assert plain.cost.base.crossing_seconds > 1.0
    swaps = MultiFingerOptions(share=True, second_finger=True, meeting_swaps=True)
    relayed = plan(chart, projection, swaps)
    # A relay, or an X at the last blue frame (red's finger takes one more frame of blue): the same move.
    assert relayed.cost.base.crossing_seconds == 0 and relayed.cost.base.handover == 1
    left, = [ft.touch for ft in relayed.touches if ft.hand == "left"]
    switched = left.parts[0].candidate.occupation
    assert (switched.first.colour, switched.second.colour) == (0, 1) and left.up_ms > 3900  # on blue, then red
    assert {p.candidate.occupation.kind for ft in relayed.touches if ft.hand == "right" for p in ft.touch.parts} == {"chain", "hold"}


# A hold on lane 2 under a conveyor: blue sits on lane 2 and leaves to the left, red slides in from lane 3 and
# stays. A pressed hold counts any touch in its lane, so the two arc fingers keep it between them.
CONVEYOR = ("hold(1001,4000,2);arc(1000,2300,0.25,0.25,s,0.00,0.00,0,none,false);"
            "arc(2300,2600,0.25,-0.50,s,0.00,0.00,0,none,false);arc(1000,2000,0.75,0.75,s,0.00,0.00,1,none,false);"
            "arc(2000,2600,0.75,0.25,s,0.00,0.00,1,none,false);arc(2600,4000,0.25,0.25,s,0.00,0.00,1,none,false);")
# The same with red staying on lane 3: nothing keeps lane 2 after blue leaves.
NO_RELAY = ("hold(1001,4000,2);arc(1000,2300,0.25,0.25,s,0.00,0.00,0,none,false);"
            "arc(2300,2600,0.25,-0.50,s,0.00,0.00,0,none,false);arc(1000,4000,0.75,0.75,s,0.00,0.00,1,none,false);")


def _serves_a_hold(plan):
    return any(p.candidate.occupation.kind == "hold" for ft in plan.touches for p in ft.touch.parts)


def test_a_hold_the_arc_fingers_keep_needs_no_touch_of_its_own(projection):
    from arc_hand_order.planning.multi_finger import coverable_holds

    chart = chart_of(CONVEYOR)
    hold, = coverable_holds(occupations_from_chart(chart), projection)
    assert (hold.lane, hold.start_ms, hold.end_ms) == (2, 1001, 4000)
    plain = plan(chart, projection, EVERYTHING)
    assert _serves_a_hold(plain) and not plain.covered
    covered = plan(chart, projection, MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True,
                                                         covered_holds=True))
    assert covered.covered == (hold,) and not _serves_a_hold(covered)
    assert len(covered.touches) == 2 < len(plain.touches)
    assert multi_report(covered)["covered_holds"][0]["start_ms"] == 1001


def test_a_hold_no_arc_keeps_to_its_end_is_not_covered(projection):
    from arc_hand_order.planning.multi_finger import coverable_holds

    chart = chart_of(NO_RELAY)
    assert coverable_holds(occupations_from_chart(chart), projection) == ()
    covered = plan(chart, projection, MultiFingerOptions(share=True, second_finger=True, covered_holds=True))
    assert not covered.covered and _serves_a_hold(covered)


# A stream of sky notes between a blue and a red arc that stay within the grace distance: either colour takes any
# touch in its hitbox and a lift binds nothing, so the stream's touches keep both arcs.
STREAM = ("arc(1000,2600,0.42,0.42,s,0.50,0.50,0,none,false);arc(1000,2600,0.58,0.58,s,0.50,0.50,1,none,false);"
          "arc(1000,2600,0.50,0.50,s,0.50,0.50,0,none,true)["
          + ",".join(f"arctap({t})" for t in range(1000, 2601, 100)) + "];")


def _fast_runs(plan, gap_ms=150):
    """Longest run of one hand's presses each less than gap_ms after the previous, per hand."""
    longest = {}
    for hand in ("left", "right"):
        downs = sorted(ft.touch.down_ms for ft in plan.touches if ft.hand == hand)
        run = best = min(1, len(downs))
        for a, b in zip(downs, downs[1:]):
            run = run + 1 if b - a < gap_ms else 1
            best = max(best, run)
        longest[hand] = best
    return longest


def test_a_sky_note_stream_keeps_the_arcs_beside_it_and_two_hands_share_the_stream(projection):
    from arc_hand_order.planning.multi_finger import coverable_arcs

    chart = chart_of(STREAM)
    assert sorted(o.colour for o in coverable_arcs(occupations_from_chart(chart), projection)) == [0, 1]
    plain = plan(chart, projection, EVERYTHING)
    assert max(_fast_runs(plain).values()) == 17  # one finger on both arcs, the other hand on the whole stream
    fixed = plan(chart, projection, MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True,
                                                       covered_arcs=True, jack_weight=20.0))
    assert sorted(o.colour for o in fixed.covered) == [0, 1]
    assert not any(p.candidate.occupation.kind == "chain" for ft in fixed.touches for p in ft.touch.parts)
    assert max(_fast_runs(fixed).values()) <= 4 and fixed.cost.jack_presses == 0
    assert len(multi_report(fixed)["covered_arcs"]) == 2


def test_arcs_apart_are_not_covered_by_a_stream(projection):
    from arc_hand_order.planning.multi_finger import coverable_arcs

    # Red moved out of the grace distance: a tapping finger would bind blue and hold it from the next finger.
    chart = chart_of(STREAM.replace("0.58,0.58,s,0.50,0.50,1", "1.00,1.00,s,0.50,0.50,1"))
    assert coverable_arcs(occupations_from_chart(chart), projection) == ()


def test_a_hand_that_cannot_pass_a_stream_on_pays_for_each_press_beyond_the_free_run(projection):
    # One hand holds a blue arc on the far left; the other has 17 taps on lane 4, 100 ms apart, to itself.
    chart = chart_of("arc(1000,2600,-0.25,-0.25,s,0.00,0.00,0,none,false);"
                     + "".join(f"({t},4);" for t in range(1000, 2601, 100)))
    jack = MultiFingerOptions(share=True, second_finger=True, jack_weight=20.0)
    charged = plan(chart, projection, jack)
    assert max(_fast_runs(charged).values()) == 17 and charged.cost.jack_presses == 17 - jack.jack_free
    free = plan(chart, projection, MultiFingerOptions(share=True, second_finger=True))
    assert free.cost.jack_presses == 0
    assert (charged.cost.weighted(charged.options, charged.multi) - free.cost.weighted(free.options, free.multi)
            == pytest.approx(20.0 * (17 - jack.jack_free)))


def test_a_stream_keeps_the_first_part_of_a_chain_and_a_finger_takes_the_rest(projection):
    from arc_hand_order.planning.multi_finger import arc_cover_splits

    # STREAM's arcs go on, joined, out of the grace distance and away from the notes: only their first parts are kept.
    chart = chart_of(STREAM + "arc(2600,3400,0.42,0.00,s,0.50,0.50,0,none,false);"
                     "arc(2600,3400,0.58,1.00,s,0.50,0.50,1,none,false);")
    splits = arc_cover_splits(occupations_from_chart(chart), projection)
    assert sorted((chain.colour, [(p.start_ms, p.end_ms) for p in covered], [(p.start_ms, p.end_ms) for p in held])
                  for chain, covered, held in splits) == [(0, [(1000, 2600)], [(2600, 3400)]),
                                                          (1, [(1000, 2600)], [(2600, 3400)])]
    split = plan(chart, projection, MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True,
                                                       covered_arcs=True, jack_weight=20.0))
    assert sorted((o.start_ms, o.end_ms) for o in split.covered) == [(1000, 2600), (1000, 2600)]
    served = [p.candidate.occupation for ft in split.touches for p in ft.touch.parts if p.candidate.occupation.kind == "chain"]
    assert sorted((o.colour, o.start_ms) for o in served) == [(0, 2600), (1, 2600)]


def _jitter(a, b, step=60, t0=1000, pieces=16):
    """A blue arc chart-drawn as pieces jumping between x a and x b, too far apart to join (0.1 or more)."""
    return "".join(f"arc({t0 + k * step},{t0 + (k + 1) * step},{x:.2f},{x:.2f},s,0.00,0.00,0,none,false);"
                   for k, x in enumerate([a, b] * (pieces // 2)))


def test_a_narrow_jitter_is_held_as_one_still_finger(projection):
    from arc_hand_order.planning.multi_finger import steady_runs

    chart = chart_of(_jitter(0.40, 0.60))
    occupations = occupations_from_chart(chart)
    assert sum(o.kind == "chain" for o in occupations.items) == 16  # every piece is a chain of its own
    run, = steady_runs(occupations, MultiFingerOptions(steady_arcs=True))
    assert len(run.pieces) == 16 and abs(run.region_at(1500).x) < 20  # still, between the two jumps
    plain = plan(chart, projection, EVERYTHING)
    steady = plan(chart, projection, MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True,
                                                        steady_arcs=True))
    assert len(steady.touches) == 1 and [s.occupation_id for s in steady.steady] == [run.occupation_id]
    assert multi_report(steady)["steady_arcs"][0]["pieces"] == 16
    assert steady.states_visited < plain.states_visited


def test_a_wide_jitter_is_held_as_two_rails_only_under_grace(projection):
    from arc_hand_order.planning.multi_finger import steady_runs

    # Alone, the colour stays bound to the first rail's finger and takes no second one: not offered.
    alone = _jitter(0.10, 0.90)
    assert steady_runs(occupations_from_chart(chart_of(alone)), MultiFingerOptions(steady_arcs=True)) == []
    # With a red jitter on the same points, both colours are in grace all along: two rails each.
    chart = chart_of(alone + alone.replace(",0,none,false);", ",1,none,false);"))
    rails = steady_runs(occupations_from_chart(chart), MultiFingerOptions(steady_arcs=True))
    assert sorted((r.colour, len(r.pieces)) for r in rails) == [(0, 8), (0, 8), (1, 8), (1, 8)]
    steady = plan(chart, projection, MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True,
                                                        steady_arcs=True))
    assert len(steady.steady) == 4 and len(steady.touches) <= 4


def _sky(t, x, y=1.0):
    return f"arc({t},{t},{x},{x},s,{y},{y},0,none,true)[arctap({t})];"


APART = replace(FULL, apart_weight=10.0, repeat_weight=5.0)


def test_crossings_are_told_apart_by_the_upper_forearm():
    from arc_hand_order.planning.multi_finger import crossing_kind

    m = MultiFingerOptions()
    # Right hand on a sky note 46 mm right, left hand on lane 4 below and further out, under the right forearm:
    # near. Right hand up on the left, left hand down on lane 3: clear.
    assert crossing_kind(m, (0.046, 0.010), "right", (0.076, -0.050)) == 1
    assert crossing_kind(m, (-0.032, 0.010), "right", (0.025, -0.050)) == 2
    assert crossing_kind(m, (0.038, 0.010), "left", (-0.024, 0.002)) == 0  # both on the top sky line


SKY_TAP_RUN = (_sky(1000, 1.0) + "(1087,2);" + _sky(1174, 0.0) + _sky(1261, 0.5) + "(1349,2);" + _sky(1436, 0.5)
           + _sky(1785, 0.854) + "(1785,4);" + "(1960,3);" + _sky(2047, 0.99) + "(2134,2);" + _sky(2309, 1.0)
           + "(2309,3);")  # sky notes and taps alternating across the track


def test_geometry_features_are_recorded_without_changing_the_plan(projection):
    chart = chart_of(SKY_TAP_RUN)
    off = plan(chart, projection, MultiFingerOptions(), APART)
    on = plan(chart, projection, MultiFingerOptions(geometry_features=True), APART)
    assert [(c.id, h) for c, h in off.choices] == [(c.id, h) for c, h in on.choices]
    assert on.cost.weighted(on.options, on.multi) == off.cost.weighted(off.options, off.multi)
    c = on.cost
    assert c.apart_level + c.apart_near + c.apart_clear == pytest.approx(c.base.apart)
    assert c.crossing_level_s + c.crossing_near_s + c.crossing_clear_s == pytest.approx(c.base.crossing_seconds)
    assert 0 <= c.repeat_far <= c.base.repeat
    total = MultiCost()
    for _, step in on.step_costs:
        total = total + step
    assert total.weighted(on.options, on.multi) == pytest.approx(on.cost.weighted(on.options, on.multi))


def test_a_crossed_double_under_the_forearm_goes_the_other_way_when_charged(projection):
    chart = chart_of(SKY_TAP_RUN)

    def double(p):
        return {c.occupation.kind: h for c, h in p.choices if c.occupation.start_ms == 1785}
    plain = plan(chart, projection, MultiFingerOptions(geometry_features=True), APART)
    assert double(plain) == {"sky": "right", "tap": "left"}
    assert plain.cost.apart_near > 0
    charged = plan(chart, projection, MultiFingerOptions(apart_near_weight=30.0), APART)
    assert double(charged) == {"sky": "left", "tap": "right"}


def test_an_extra_weight_may_not_take_a_cost_below_zero(projection):
    with pytest.raises(ValueError, match="below 0"):
        plan(chart_of(SKY_TAP_RUN), projection, MultiFingerOptions(apart_clear_weight=-20.0), APART)


def test_a_learned_preference_steers_hands_and_costs_minus_log_p(projection):
    from arc_hand_order.planning.multi_finger import preference_features

    # A model that only wants each hand on its own side of the screen.
    side = MultiFingerOptions(preference=(("my_side", 5.0),), preference_weight=10.0)
    chart = chart_of(_sky(1000, 0.25) + _sky(1300, 0.75) + _sky(1600, 0.3) + _sky(1900, 0.7))
    planned = plan(chart, projection, side, APART)
    assert [h for _, h in planned.choices] == ["left", "right", "left", "right"]
    # The charge is -log p of the chosen hand, here with one note and no history.
    one = plan(chart_of(_sky(1000, 0.25)), projection, side, APART)
    fl = preference_features(one.choices[0][0].first_xy[0] * 1000, 10, "sky", "left", None, None, ())
    fr = preference_features(one.choices[0][0].first_xy[0] * 1000, 10, "sky", "right", None, None, ())
    z = 5.0 * (fl["my_side"] - fr["my_side"])
    assert one.cost.preference == pytest.approx(math.log1p(math.exp(-z)))
    linear = plan(chart_of(_sky(1000, 0.25)), projection, replace(side, preference_linear=True), APART)
    assert linear.cost.preference == pytest.approx(-z)  # the left hand's score lead, as a negative cost
    with pytest.raises(ValueError):
        MultiFingerOptions(preference=(("no_such_feature", 1.0),))


def _arc_end_spot_passage():
    # A red arc ends top-left as taps run on lanes 2 and 3 and sky notes come back to its end spot.
    return parse_aff("AudioOffset:0\n-\ntiming(0,172,4);arc(1000,1175,0.25,0.25,s,1.00,1.00,1,none,false);(1000,3);"
                     "(1175,2);" + _sky(1262, 0.25) + "(1349,3);" + _sky(1436, 0.25) + _sky(1524, 1.0) + "(1698,3);"
                     + _sky(1785, 0.75) + "(1872,2);" + _sky(1960, 0.75)).chart


def test_the_arc_hand_keeps_its_end_spot_through_a_passage(projection):
    # Without the takeover cost the left hand takes the spot 87 ms after the arc and the hands trade roles; with it
    # the arc hand keeps the spot.
    chart = _arc_end_spot_passage()
    plain = plan(chart, projection, MultiFingerOptions(geometry_features=True), APART)
    assert plain.cost.arc_takeovers > 0
    kept = plan(chart, projection, MultiFingerOptions(arc_takeover_weight=20.0), APART)
    assert kept.cost.arc_takeovers == 0
    points = [h[0] for c, h in kept.choices if c.occupation.kind in ("tap", "sky")]
    assert "".join(points) == "llrlrllrlr"  # 47.789, 47.964 L; 48.051 R; 48.138 L; 48.225 R; 48.313 L ...


def test_the_recommended_options_keep_the_arc_hand_on_its_spot(projection):
    chart = _arc_end_spot_passage()
    result = plan(chart, projection, RECOMMENDED, APART)
    assert result.cost.arc_takeovers == 0


def _jitter_along_an_arc():
    # A blue jitter of 55 ms pieces, its odd pieces against a red arc.
    red = "arc(1000,1660,0.50,1.10,s,0.00,0.00,1,none,false);"
    pieces, last = [], None
    for k in range(12):
        a, b = 1000 + 55 * k, 1055 + 55 * k
        x0, x1 = ((0.55 + 0.012 * k, 0.56 + 0.012 * k) if k % 2 == 0
                  else (0.48 + 0.60 * (a - 1000) / 660, 0.48 + 0.60 * (b - 1000) / 660))
        if last is not None:
            pieces.append(f"arc({a},{a},{last:.2f},{x0:.2f},s,0.00,0.00,0,none,false);")
        pieces.append(f"arc({a},{b},{x0:.2f},{x1:.2f},s,0.00,0.00,0,none,false);")
        last = x1
    return parse_aff("AudioOffset:0\n-\ntiming(0,180,4);" + red + "".join(pieces)).chart


def test_the_finger_on_an_arc_keeps_the_jitter_pieces_against_it(projection):
    chart = _jitter_along_an_arc()
    plain = plan(chart, projection, replace(RECOMMENDED, enveloped_arcs=False, jump_slides=False), APART)
    assert any(ft.slot == 1 for ft in plain.touches)  # the jitter on two rails, a finger each
    kept = plan(chart, projection, RECOMMENDED, APART)
    assert kept.covered and all(o.kind == "chain" and o.colour == 0 for o in kept.covered)
    assert {ft.hand for ft in kept.touches} == {"left", "right"} and all(ft.slot == 0 for ft in kept.touches)


def test_a_shared_touch_does_not_jump_from_a_sky_note_into_an_arc(projection):
    # A sky note top right a frame before a lane-4 hold and a blue sky arc from the
    # top centre, after holds in lanes 2 and 3. The left finger could tap the sky note and be at the arc's hitbox
    # edge 40 mm away the next frame; the player taps it with the right index, the right thumb on the hold.
    chart = chart_of("hold(0,668,2);hold(0,668,3);hold(1000,1834,4);" + _sky(984, 1.0)
                     + "arc(1000,2000,0.50,-0.50,sisi,1.00,0.00,0,none,false);")
    # (Aiming at the drawn sky note rules that touch out as well.)
    jumpy = plan(chart, projection, replace(RECOMMENDED, share_jump_max_m=0.0, sky_aim_drawn=False), APART)
    assert [ft.hand for ft in jumpy.touches if ft.touch.down_ms > 900 and len(ft.touch.parts) == 2] == ["left"]
    fixed = plan(chart, projection, RECOMMENDED, APART)
    hands = {p.candidate.occupation.kind: ft.hand for ft in fixed.touches if ft.touch.down_ms > 900
             for p in ft.touch.parts}
    assert hands == {"sky": "right", "hold": "right", "chain": "left"}


def _largest_step(touch):
    return max((math.dist(a, b) for a, b in zip(touch.path, touch.path[1:])), default=0.0)


def test_a_finger_does_not_jump_from_a_sky_note_into_an_arc_beside_it(projection):
    # A sky note top right and a still blue arc at the top centre start together, the
    # right hand on a red arc. One left finger serves both, the sky note at its inner end; once it is tapped the point
    # went to the arc's centre the next frame, 22 mm. With the limit it glides there over frames, inside the hitbox.
    # Aiming at the drawn sky note (RECOMMENDED) leaves that note to the other hand (see
    # test_a_sky_note_is_tapped_where_it_is_drawn), so the glide is checked with sky_aim_drawn off.
    chart = chart_of("arc(900,1500,1.00,1.00,s,0.00,0.00,1,none,false);" + _sky(1000, 1.0)
                     + "arc(1000,1167,0.50,0.50,s,1.00,1.00,0,none,false);")
    edge = replace(RECOMMENDED, sky_aim_drawn=False)
    jumpy = plan(chart, projection, replace(edge, accel_max_m_s2=0.0), APART)
    shared, = [ft.touch for ft in jumpy.touches if len(ft.touch.parts) == 2]
    assert _largest_step(shared) > 0.020
    limited = plan(chart, projection, edge, APART)
    glide, = [ft.touch for ft in limited.touches if len(ft.touch.parts) == 2]
    assert [p.candidate.occupation.kind for p in glide.parts] == ["sky", "chain"]
    assert _largest_step(glide) < 0.015 and limited.cost.accel_over == 0
    assert glide.path[:2] == shared.path[:2] and glide.path[-1] == shared.path[-1]


def test_a_move_beyond_the_acceleration_limit_is_charged_and_left_to_the_other_hand(projection):
    # Top left, then top right 117 ms later: one hand would cross the screen in 83 ms of air.
    chart = chart_of(_sky(1000, 0.0) + _sky(1117, 1.0))
    limit = RECOMMENDED
    forced = replace(APART, fixed_hands=((1000, "left"), (1117, "left")))
    assert plan(chart, projection, replace(RECOMMENDED, accel_max_m_s2=0.0), forced).cost.accel_over == 0  # off
    charged = plan(chart, projection, limit, forced)
    assert charged.cost.accel_over == 1
    assert charged.cost.weighted(forced, limit) - charged.cost.base.weighted(forced) >= limit.accel_weight
    free = plan(chart, projection, limit, APART)
    assert [h for _, h in free.choices] == ["left", "right"] and free.cost.accel_over == 0


def test_a_hand_may_leave_a_hold_once_its_last_tick_is_settled(projection):
    # The left hand holds lane 1 to 1500 ms and taps lane 3 at 1600 ms, 100 mm away in 83 ms of air: too fast from the
    # planned end, but no contact is needed after the hold's last tick settles, so a player lets go earlier.
    # The same move from a tap is charged.
    limit = RECOMMENDED
    busy = "arc(900,1700,1.00,1.00,s,0.00,0.00,1,none,false);"
    held = plan(chart_of(busy + "hold(1000,1500,1);(1600,3);"), projection, limit, replace(APART, fixed_hands=((1600, "left"),)))
    assert [h for _, h in held.choices] == ["right", "left", "left"] and held.cost.accel_over == 0
    tapped = plan(chart_of(busy + "(1500,1);(1600,3);"), projection, limit,
                  replace(APART, fixed_hands=((1500, "left"), (1600, "left"))))
    assert tapped.cost.accel_over == 1


def test_a_hold_goes_to_the_hand_whose_index_is_right_above_it(projection):
    # A blue sky arc and a red ground arc run right, holds in lanes 2 and 3 under the blue one. Without the
    # side-by-side cost the right hand takes the lane-3 hold 70 mm beside its index on the red arc; players take it
    # with the left thumb, 60 mm under the index on the blue arc.
    chart = parse_aff("AudioOffset:0\n-\ntiming(0,180,4);arc(1000,3000,0.00,0.75,so,1.00,1.00,0,none,false);"
                      "arc(1000,3000,0.25,1.50,b,0.00,0.00,1,none,false);hold(1999,2500,2);hold(2667,3001,3);").chart

    def lane_3(result):
        return [ft.hand for ft in result.touches for p in ft.touch.parts
                if p.candidate.occupation.kind == "hold" and p.candidate.occupation.lane == 3]

    level = plan(chart, projection, replace(RECOMMENDED, side_by_side_weight=0.0), APART)
    assert lane_3(level) == ["right"] and level.cost.side_by_side_s > 0
    shaped = plan(chart, projection, RECOMMENDED, APART)
    assert lane_3(shaped) == ["left"] and shaped.cost.side_by_side_s == 0


def test_a_sky_note_is_tapped_where_it_is_drawn(projection):
    # A sky note top right starts with a still blue arc at the top centre, the right hand just off a red arc. One
    # left finger could serve both, tapping the sky note at its hitbox edge, 42 mm from the note drawn; aiming at the
    # note, the right hand taps it.
    chart = parse_aff("AudioOffset:0\n-\ntiming(0,180,4);arc(1000,1250,0.00,0.92,sisi,0.00,0.92,1,none,false);"
                      + _sky(1000, 1.0) + _sky(1083, 0.75) + _sky(1333, 1.0)
                      + "arc(1333,1500,0.50,0.50,s,1.00,1.00,0,none,false);(1500,4);").chart

    def at_1333(result):
        return {p.candidate.occupation.kind: (ft.hand, ft.touch.first_xy) for ft in result.touches
                if 1333 <= ft.touch.down_ms < 1400 for p in ft.touch.parts}

    edge = at_1333(plan(chart, projection, replace(RECOMMENDED, sky_aim_drawn=False), APART))
    assert edge["sky"][0] == edge["chain"][0] == "left"
    aimed = plan(chart, projection, replace(RECOMMENDED, sky_aim_drawn=True), APART)
    drawn = at_1333(aimed)
    assert drawn["sky"][0] == "right" and drawn["chain"][0] == "left"
    assert math.dist(edge["sky"][1], drawn["sky"][1]) > 0.04  # the shared touch was that far off the note


def test_a_second_finger_taps_its_spot_again_beside_the_hold(projection):
    # Each hand holds an outer lane and taps the lane beside it with its other finger; the right hand's lane-4 hold
    # ends as lane 3 comes again. Only two notes need contact then, so without the option a second finger is not
    # allowed and the left hand comes across; with it the right hand taps its spot again.
    chart = parse_aff("AudioOffset:0\n-\ntiming(0,180,4);hold(1000,2334,1);hold(1000,2500,4);"
                      "(2000,2);(2166,3);(2333,2);(2500,3);").chart

    def lane_3(result):
        return [(ft.hand, ft.slot) for ft in sorted(result.touches, key=lambda ft: ft.touch.down)
                for p in ft.touch.parts if p.candidate.occupation.kind == "tap" and p.candidate.occupation.lane == 3]

    plain = plan(chart, projection, replace(RECOMMENDED, second_finger_again=False), APART)
    assert lane_3(plain) == [("right", 1), ("left", 0)]
    again = plan(chart, projection, RECOMMENDED, APART)
    assert lane_3(again) == [("right", 1), ("right", 1)]
    assert again.cost.second_touches == plain.cost.second_touches - 1  # the repeat is not charged again


def test_a_deep_crossing_is_charged_by_its_depth(projection):
    # A blue arc on the right and a red arc on the left for a second: by colour the hands cross, 104 mm apart. Charged by
    # depth enough, each hand takes the arc on its own side instead, off its colour.
    chart = chart_of("arc(1000,2000,1.00,1.00,s,0.00,0.00,0,none,false);arc(1000,2000,0.00,0.00,s,0.00,0.00,1,none,false);")

    def colours(result):
        return {ft.hand: ft.touch.parts[0].candidate.occupation.colour for ft in result.touches}

    crossed = plan(chart, projection, replace(RECOMMENDED, crossing_depth_weight=100.0), APART)
    assert colours(crossed) == {"left": 0, "right": 1}
    assert crossed.cost.crossing_depth == pytest.approx(0.1, abs=0.005)  # about 1 s at 99 mm beyond the 5 mm margin
    sides = plan(chart, projection, replace(RECOMMENDED, crossing_depth_weight=400.0), APART)
    assert colours(sides) == {"left": 1, "right": 0}
    assert sides.cost.base.crossing_seconds == 0 and sides.cost.base.colour == 2


def test_a_finger_slides_on_after_an_arc_that_jumps_back(projection):
    # Red ground arcs of 166 ms, each starting far left of where the last
    # one ended, under sky notes. A bridge reaches 15 mm, so without jump slides the pieces are caught late (charged)
    # or go to other fingers; with them the right finger slides back after each jump, keeping the colour, and the left
    # hand taps the sky notes.
    chart = parse_aff("AudioOffset:0\n-\ntiming(0,180,4);arc(1000,1166,-0.50,0.38,s,0.00,0.00,1,none,false);"
                      "arc(1167,1333,-0.38,0.50,s,0.00,0.00,1,none,false);arc(1333,1499,-0.25,0.63,s,0.00,0.00,1,none,false);"
                      + _sky(1000, 0.25) + _sky(1167, 0.38) + _sky(1333, 0.50)).chart
    lifted = plan(chart, projection, replace(RECOMMENDED, jump_slides=False), APART)
    assert lifted.cost.base.untouched_arc_s > 0
    slid = plan(chart, projection, replace(RECOMMENDED, jump_slides=True), APART)
    arc, = [ft for ft in slid.touches if ft.touch.parts[0].candidate.occupation.kind == "chain"]
    assert arc.hand == "right" and len(arc.touch.parts) == 3 and len(arc.touch.bridges) == 2
    assert {ft.hand for ft in slid.touches if ft is not arc} == {"left"}
    assert slid.cost.base.untouched_arc_s == 0 and slid.cost.base.colour == 0
    assert _largest_step(arc.touch) < 0.02  # over frames, not in one jump

"""Spatial placement must change replay contacts, not just glove animation."""

from pathlib import Path
import sys
import math
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order import api
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning.contact_regions import (
    point_box,
    point_targets,
    common_tick_boxes,
    centre,
)
from arc_hand_order.planning.occupations import occupations_from_chart
from arc_hand_order.planning.arc_scoring import native_occupations, profile, covers
from contact_placement import refine
from plan_excerpt import create_plan


def chart(tmp_path, body):
    p = tmp_path / "placement.aff"
    p.write_text("AudioOffset:0\n-\ntiming(0,120,4);\n" + body)
    return p


def alternating(tmp_path, spatial=True, kind="tap"):
    times = [1000 + i * 100 for i in range(24)]
    body = "".join(f"({t},2);\n" for t in times)
    if kind == "sky":
        body = (
            "arc(0,4000,.5,.5,s,.5,.5,0,none,true)["
            + ",".join(f"arctap({t})" for t in times)
            + "];"
        )
    f = chart(tmp_path, body)
    r = api.plan_chart(
        api.load_chart(f),
        planner={
            "off_center_contacts": spatial,
            "offsets_ms": (0,),
            "scoring_arcs": True,
            "speed_max_m_s": 3,
            "apart_weight": 20 if spatial else 10,
            "apart_margin_m": 0.036,
            "fixed_hands": tuple(
                (t, "left" if i % 2 == 0 else "right") for i, t in enumerate(times)
            ),
        },
        multi={
            "second_finger": False,
            "second_finger_again": False,
            "tick_contacts": True,
            "meeting_swaps": False,
            "covered_holds": False,
            "covered_arcs": False,
            "steady_arcs": False,
            "enveloped_arcs": False,
            "max_states": 64,
        },
    )
    return r


def test_alternating_ground_stream_uses_stable_side_by_side_contacts(tmp_path):
    old = alternating(tmp_path, False)
    selected = alternating(tmp_path)
    new, report = refine(selected)
    assert len(new.plan.touches) == 24 and not new.plan.skipped
    assert [(x.hand, x.touch.down, x.touch.up) for x in new.plan.touches] == [
        (x.hand, x.touch.down, x.touch.up) for x in selected.plan.touches
    ]
    means = {
        h: np.mean([t.touch.first_xy for t in new.plan.touches if t.hand == h], axis=0)
        for h in ("left", "right")
    }
    assert means["right"][0] - means["left"][0] > 0.030
    assert len({t.touch.first_xy for t in old.plan.touches}) == 1
    assert (
        report["objective_after"] <= report["objective_before"]
        and report["binding_epochs_preserved"]
    )
    for ft in new.plan.touches:
        box = point_box(
            ft.touch.parts[0].candidate.occupation, GameplayProjection(new.device)
        )
        x, y = ft.touch.first_xy
        assert (
            box[0] - 1e-5 <= x <= box[1] + 1e-5 and box[2] - 1e-5 <= y <= box[3] + 1e-5
        )


def test_sky_stream_can_alternate_off_center_without_changing_lifetimes(tmp_path):
    r = alternating(tmp_path, kind="sky")
    new, report = refine(r)
    by_hand = {
        h: [t.touch.first_xy[0] for t in new.plan.touches if t.hand == h]
        for h in ("left", "right")
    }
    assert max(by_hand["left"]) < min(by_hand["right"]) - 0.025
    assert report["lifetimes_preserved"]


def test_native_boxes_are_safe_for_all_camera_roll_samples(tmp_path):
    from arc_hand_order.planning.native_input import (
        camera_for,
        SCREEN_HALF_X,
        SCREEN_HALF_Y,
    )

    p = GameplayProjection(api.load_device())
    f = chart(tmp_path, "(1000,2);")
    o = occupations_from_chart(api.load_chart(f).chart).items[0]
    for q in point_targets(o, p):
        for view in range(5):
            x, y = camera_for(p).project(-212.5, 0, view)
            assert abs(q[0] - x) < SCREEN_HALF_X * p.width_m * 425 / 380
            assert abs(q[1] - y) < SCREEN_HALF_Y * p.height_m * 425 / 500


def test_common_tick_regions_find_safe_stationary_off_center_anchor(tmp_path):
    body = "".join(
        f"arc({1000+i*120},{1120+i*120},{i%2*.1},{(1-i%2)*.1},s,.5,.5,0,none,false);"
        for i in range(8)
    )
    body += "arc(1960,2080,0,.4,s,.5,.5,0,none,false);arc(2080,2200,.4,0,s,.5,.5,0,none,false);"
    f = chart(tmp_path, body)
    o = native_occupations(occupations_from_chart(api.load_chart(f).chart)).items[0]
    p = GameplayProjection(api.load_device())
    boxes = common_tick_boxes(o, p, 0)
    assert boxes
    fitted = profile(o, p, 0.05, 0, spatial=True)
    assert fitted is not None
    down, path, _ = fitted
    assert len(set(path)) == 1 and covers(o, path, down, 0, p) is not None
    assert any(math.dist(path[0], centre(b)) < 1e-10 for b in boxes)


def test_dashboard_plan_emits_spatial_report_and_can_disable_feature(tmp_path):
    f = chart(tmp_path, "(1000,2);(1100,2);(1200,2);")
    settings = {
        "arc_path_mode": "score",
        "max_speed_m_s": 3,
        "jack_free": 1,
        "jack_gap_ms": 300,
        "jack_weight": 100,
    }
    new = create_plan(f, 0, 2000, settings)
    old = create_plan(f, 0, 2000, {**settings, "off_center_contacts": False})
    assert new["contact_placement"]["enabled"]
    assert "contact_placement" not in old
    assert new["planner_options"]["off_center_contacts"]
    assert len({tuple(t["path_mm"][0]) for t in new["touches"]}) > 1


def test_empty_safe_inset_keeps_the_original_centre_candidate(tmp_path, monkeypatch):
    from arc_hand_order.planning import contact_regions

    monkeypatch.setattr(contact_regions, "point_targets", lambda *args: ())
    result = alternating(tmp_path)
    assert len(result.plan.touches) == 24 and not result.plan.skipped
    assert len({ft.touch.first_xy for ft in result.plan.touches}) == 1


def test_spatial_candidates_recover_a_transfer_rejected_at_note_centres(tmp_path):
    import pytest

    f = chart(tmp_path, "(1000,1);(1150,4);")
    planner = {
        "offsets_ms": (0,),
        "point_contact_frames": 1,
        "speed_max_m_s": 0.9,
        "fixed_hands": ((1000, "left"), (1150, "left")),
    }
    multi = {
        "second_finger": False,
        "second_finger_again": False,
        "meeting_swaps": False,
        "covered_holds": False,
        "covered_arcs": False,
        "steady_arcs": False,
        "enveloped_arcs": False,
    }
    with pytest.raises(ValueError, match="no candidate path"):
        api.plan_chart(api.load_chart(f), planner=planner, multi=multi)
    r = api.plan_chart(
        api.load_chart(f), planner={**planner, "off_center_contacts": True}, multi=multi
    )
    assert len(r.plan.touches) == 2 and not r.plan.skipped


def test_spatial_candidates_do_not_invert_simultaneous_sky_note_ties(tmp_path):
    from arc_hand_order.planning.contact_regions import preserves_press_targets
    from arc_hand_order.planning.two_finger import target_xy

    f = chart(
        tmp_path,
        "arc(1000,1000,.45,.45,s,.5,.5,0,none,true)[arctap(1000)];"
        "arc(1000,1000,.55,.55,s,.5,.5,0,none,true)[arctap(1000)];",
    )
    items = occupations_from_chart(api.load_chart(f).chart).items
    projection = GameplayProjection(api.load_device())
    for o in items:
        original = target_xy(o, o.start_ms, projection)
        targets = point_targets(o, projection, items)
        assert targets
        assert all(
            preserves_press_targets(items, projection, original, q) for q in targets
        )

"""Release policy and the contact timeline shared with Blender."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arc_hand_order import api
from arc_hand_order.planning.multi_finger import _release
from arc_hand_order.planning.two_finger import frame_ms
from dashboard.server import Settings
from plan_excerpt import create_plan


@pytest.fixture
def snake(tmp_path):
    chart = tmp_path / "snake.aff"
    chart.write_text("AudioOffset:37\n-\ntiming(0,120,4);"
                     "arc(1000,2000,.25,.5,s,.5,.5,0,none,false);"
                     "arc(2000,2600,.5,.75,s,.5,.5,0,none,false);")
    return chart


@pytest.mark.parametrize("settings", [None, {"max_speed_m_s": 3},
                                      Settings(full_arc_hold=False, prefer_arc_trace=False).model_dump()])
def test_early_release_policy_releases_a_connected_snake_after_score_is_secured(snake, settings):
    output = create_plan(snake, 0, 3000, settings)
    assert output["planner_options"]["full_arc_hold"] is False
    touch, = output["touches"]
    assert touch["up_ms"] < 2600
    assert len(touch["path_mm"]) == touch["up_frame"] - touch["down_frame"]
    # Both connected segments' scoring windows still receive contact.
    from arc_hand_order.planning.occupations import occupations_from_chart
    occupation, = occupations_from_chart(api.load_chart(snake).chart).items
    for point in occupation.score_points:
        assert any(point.contact_window.contains(frame_ms(f, 37))
                   for f in range(touch["down_frame"], touch["up_frame"]))


def test_dashboard_controls_reach_both_hold_policies(snake):
    output = create_plan(snake, 0, 3000, {
        "max_speed_m_s": 3, "full_arc_hold": True, "partial_hold_weight": 321,
        "full_ground_hold": False, "partial_ground_hold_weight": 123,
    })
    options = output["planner_options"]
    assert options["full_arc_hold"] is True
    assert options["full_ground_hold"] is False
    assert options["partial_hold_weight"] == 321
    assert options["partial_ground_hold_weight"] == 123
    assert output["touches"][0]["up_ms"] > 2600


@pytest.mark.parametrize("note", [
    "arc(1000,2000,.25,.5,s,.5,.5,0,none,false);",
    "hold(1000,2000,2);",
])
@pytest.mark.parametrize("full", [False, True])
def test_acceleration_release_matches_the_exported_animation_contact(tmp_path, note, full):
    chart = tmp_path / "release.aff"
    chart.write_text("AudioOffset:37\n-\ntiming(0,120,4);" + note)
    result = api.plan_chart(api.load_chart(chart),
                            multi={"second_finger": False, "second_finger_again": False},
                            planner={"full_arc_hold": full, "full_ground_hold": full})
    exported = api.plan_to_dict(result)
    for ft, touch in zip(result.plan.touches, exported["touches"], strict=True):
        frame, xy = _release(ft.touch)
        assert frame == touch["up_frame"]
        assert frame_ms(frame, 37) == touch["up_ms"]
        assert [round(v * 1000, 2) for v in xy] == touch["path_mm"][-1]
        assert (touch["up_ms"] > 2000) is full

"""Regression cases captured from the native 40–100 s miss audit."""

from pathlib import Path
from types import SimpleNamespace as N
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order import api
from arc_hand_order.planning.native_input import (
    CameraEnvelope,
    ArcInputGeometry,
    InputPoint,
    TILTS,
    screen_hitbox,
)
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning.multi_finger import _Graph, Touch
from arc_hand_order.planning.occupations import occupations_from_chart
from arc_hand_order.planning.two_finger import PlannerOptions, build_candidates, frame_ms


def projection():
    return GameplayProjection(api.load_device())


@pytest.mark.parametrize("scoring", [False, True])
@pytest.mark.parametrize("fractions", [(.48, .24), (0., .24), (.48, 0.), (0., 0.)])
def test_all_note_types_offer_xy_offsets_inside_the_inner_hitbox(tmp_path, scoring, fractions):
    from arc_hand_order.planning.arc_scoring import native_occupations

    chart_file = tmp_path / "offsets.aff"
    chart_file.write_text("AudioOffset:0\n-\ntiming(0,120,4);(400,1);hold(650,950,4);"
                          "arc(1100,1450,.5,.5,s,.5,.5,0,none,false);"
                          "arc(1650,1650,.5,.5,s,1,1,0,none,true)[arctap(1650)];")
    chart = api.load_chart(chart_file).chart
    occupations = occupations_from_chart(chart)
    if scoring:
        occupations = native_occupations(occupations, set(), {e.event_id:e.timinggroup_id for e in chart.events},
                                         chart_events=chart.events)
    device = projection()
    camera = CameraEnvelope(device)
    options = PlannerOptions(contact_offset_fractions=fractions, scoring_arcs=scoring, full_ground_hold=True)
    for group in build_candidates(occupations, device, options):
        item = group[0].occupation
        for axis, fraction in enumerate(fractions):
            positions = {round(c.first_xy[axis], 8) for c in group}
            assert len(positions) >= 3 if fraction else len(positions) == 1
        dx, down, up = screen_hitbox(item.kind, device.width_m, device.height_m)
        for c in group:
            region = item.region_at(frame_ms(c.down_frame))
            x, y = ((camera.unproject(device.lane_center_to_device(region.lane), 2))
                    if region.lane else (region.x, region.y))
            for view in range(len(TILTS)):
                cx, cy = camera.project(x, y, view)
                px, py = c.first_xy
                x_limit, y_limit = fractions if view == 2 else (1., 1.)
                assert abs(px - cx) <= x_limit * dx + 1e-8
                assert -y_limit * down - 1e-8 <= py - cy <= y_limit * up + 1e-8


def test_workbench_passes_contact_axis_limits_to_the_planner(tmp_path):
    from dashboard.server import Settings
    from plan_excerpt import create_plan

    chart = tmp_path / "tap.aff"
    chart.write_text("AudioOffset:0\n-\ntiming(0,120,4);(1000,1);")
    defaults = Settings()
    assert (defaults.contact_offset_x_percent, defaults.contact_offset_y_percent) == (60, 30)
    settings = Settings(contact_offset_x_percent=0, contact_offset_y_percent=73)
    plan = create_plan(chart, 0, 2000, settings.model_dump())
    assert plan["planner_options"]["contact_offset_fractions"] == [0., .73]
    assert plan["misses"] == []


def test_native_screen_only_overlap_triggers_grace():
    # ArcCreate starts grace at 70.033 s despite >250 game units of Y separation.
    a, b = (381.7263, 119.467545), (406.254852, 431.199982)
    assert abs(a[1] - b[1]) > 250
    assert CameraEnvelope(projection()).overlaps(a, b)


def test_camera_envelope_rejects_the_previously_false_edge_catch():
    q = InputPoint((0.03416, -0.02436), projection())
    target = (436.792572, 511.0649)
    assert abs(q[0] - target[0]) < 182 and abs(q[1] - target[1]) < 242
    assert not q.collides(*target, certain=True, inset=8)
    # Independently observed native touch under camera roll at the same point.
    native = (248.335, 254.687317)
    for k in range(2):
        assert min(p[k] for p in q.samples) <= native[k] <= max(p[k] for p in q.samples)


def test_screen_grace_clears_a_held_fingers_old_colour_assignment():
    p = projection()

    def position(colour, time):
        if 500 <= time <= 600:
            return ((406.254852, 431.199982), (381.7263, 119.467545))[colour]
        return ((-350, 100), (350, 500))[colour]

    occupations = [
        N(
            kind="chain",
            colour=c,
            notes=(N(start=0, end=2000, position=lambda t, c=c: position(c, t)),),
        )
        for c in range(2)
    ]
    geometry = ArcInputGeometry(occupations, p, 0)
    graph = object.__new__(_Graph)
    graph.options = N(scoring_arcs=True)
    graph.projection = p
    graph.bindings = {}
    graph.graced = geometry.graced
    graph.native_frame_regions = geometry.regions
    path = tuple(
        p.judgement_to_device(*((-350, 100) if f < 48 else (350, 500)))
        for f in range(121)
    )
    touch = Touch(0, (), (), 0, len(path), path, 0)
    bindings = graph.bound_frames(touch)
    assert 10 in bindings[0]
    assert 80 in bindings[1] and 80 not in bindings[0]

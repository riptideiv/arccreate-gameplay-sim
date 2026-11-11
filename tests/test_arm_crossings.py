import json
import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from arc_hand_order.planning.arm_crossings import ArmState, HandPath, extend, order
from crossing_motion import CrossingMotionError, layer_margin, validate
from hand_pose import HAND_PARTS, proxies
from motion_paths import positions


def path(xs, y):
    return np.column_stack((np.asarray(xs)*.01, np.broadcast_to(y, len(xs)), np.full(len(xs), .006)))


def instructions(routes, last_frame):
    return {"last_frame": last_frame, "routes": [
        {"top": c.top, "start_frame": c.start_frame, "end_frame": c.end_frame} for c in routes
    ]}


def test_approach_above_requires_over_and_below_keeps_both_beam_keys():
    right = path([0, 0], 0)
    above = path([-1, 1], .01)
    below = path([-1, 1], -.01)
    assert [order(r) for r in extend((), above, right, 0)] == ["left"]
    histories = extend((), below, right, 0)
    states = [ArmState((HandPath(below, 0), HandPath(right, 0)), r) for r in histories]
    assert {order(r) for r in histories} == {"left", "right"}
    assert len({s.key for s in states}) == 2


def test_a_coincident_x_sample_does_not_allow_changing_top():
    left = path([-1, 1, 0, 1, -1], .01)
    right = path([0] * len(left), 0)
    history, = extend((), left, right, 0)
    crossing, = history
    assert (crossing.start_frame, crossing.end_frame, crossing.top) == (.5, 3.5, "left")
    original = instructions(history, 4)
    points = np.stack((left, right), axis=1)
    validate(points, original)
    invalid = instructions((replace(crossing, end_frame=2.),
                            replace(crossing, start_frame=2., top="right")), 4)
    with pytest.raises(CrossingMotionError):
        validate(points, invalid)


def test_uncross_and_recross_are_seen_between_scoring_ticks():
    left = path([-1, 1, -1, 1], [-.01, -.01, .01, .01])
    right = path([0] * len(left), 0)
    seeded = next(r for r in extend((), left[:2], right[:2], 0) if order(r) == "right")
    result, = extend(seeded, left, right, 1)
    assert [(r.top, r.start_frame, r.end_frame) for r in result] == [
        ("right", .5, 1.5), ("left", 2.5, None)
    ]


def test_legal_layer_order_can_still_allow_a_full_planar_circle():
    phi = np.linspace(np.pi, 3*np.pi, 361)
    left = np.column_stack((.05*np.cos(phi), .05*np.sin(phi), np.full(len(phi), .006)))
    right = np.zeros_like(left)
    history, = extend((), left, right, 0)
    assert len(history) == 1 and history[0].top == "left"
    assert order(history) == "uncrossed"
    assert np.ptp(np.unwrap(np.arctan2(left[:, 1], left[:, 0]))) == pytest.approx(2*np.pi)


def test_3d_check_resolves_layer_for_collinear_projected_arms(monkeypatch):
    import hand_pose

    monkeypatch.setattr(hand_pose, "SHOULDERS", np.array([[-.3, 0, .12], [.3, 0, 0]]))
    elbows = np.array([[-.15, 0, .12], [.15, 0, 0]])
    wrists = np.array([[.05, 0, .12], [-.05, 0, 0]])
    monkeypatch.setattr(hand_pose, "arm_geometry", lambda *args: (wrists, elbows))
    centres = (np.array([[.1, 0, .12]]), np.array([[-.1, 0, 0]]))
    poses = np.zeros((2, 2))
    assert layer_margin(centres, poses, None, 1) > 0
    assert layer_margin(centres, poses, None, -1) < 0


@pytest.mark.skipif(not os.getenv("ARCCREATE_ARGHENA_TAKE"), reason="requires the saved local Arghena take")
def test_arghena_118_119_cannot_flip_mid_crossing_and_can_swap(monkeypatch):
    import plan_excerpt

    take = Path(os.environ["ARCCREATE_ARGHENA_TAKE"])
    saved = json.loads((take/"plan.json").read_text())
    settings = json.loads((take/"settings.json").read_text())
    motion = json.loads((take/"motion.json").read_text())
    data = np.stack([motion["hands"][h] for h in ("left", "right")], axis=1)
    points = data[..., [0, 2, 1]] * [1, -1, 1] - [.405, .802, .708]
    assert (points[430:452, 0, 0] > points[430:452, 1, 0]).all()
    parts = [[(n, (sign*x, y, z), r) for n, (x, y, z), r in HAND_PARTS]
             for sign in (1, -1)]
    pose = data[[430, 451], :, -2:]
    centres = [proxies(pose[:, h], parts[h])[0] + points[[430, 451], h, None] for h in range(2)]
    assert (layer_margin(centres, pose, parts, np.array([-1, 1])) > 0).all()

    chart = Path(saved["chart"]["path"])
    start, end = [saved["preview"][f"chart_{k}_ms"] for k in ("start", "end")]
    normal = plan_excerpt.create_plan(chart, start, end, settings)
    instructions_ = normal["arm_crossings"]
    route = next(c for c in instructions_["routes"] if c["start_frame"] < 430 < 451 < c["end_frame"])
    assert route["top"] == "right"
    checked_path = np.stack([
        positions([t for t in normal["touches"] if t["hand"] == side], side,
                  instructions_["last_frame"], settings["lift_mm"]/1000,
                  settings["max_speed_m_s"], settings["idle_speed_m_s"])
        for side in ("left", "right")
    ], axis=1)
    validate(checked_path, instructions_)
    broken = {**instructions_, "routes": [
        piece for c in instructions_["routes"]
        for piece in ([{**c, "end_frame": 451.}, {**c, "start_frame": 451., "top": "left"}]
                      if c is route else [c])
    ]}
    with pytest.raises(CrossingMotionError):
        validate(checked_path, broken)

    plan_chart = plan_excerpt.api.plan_chart

    def swap_pair(chart, **kwargs):
        kwargs["planner"] = dict(kwargs["planner"], fixed_hands=((7593, "right"), (7594, "left")))
        return plan_chart(chart, **kwargs)

    monkeypatch.setattr(plan_excerpt.api, "plan_chart", swap_pair)
    swapped = plan_excerpt.create_plan(chart, start, end, settings)
    assert swapped["planning"]["skipped_ticks"] == 0
    assert [n["hands"] for n in swapped["notes"] if n["start_ms"] in (7593, 7594)] == [["right"], ["left"]]
    assert not any(c["start_frame"] <= 457 and c["end_frame"] >= 430
                   for c in swapped["arm_crossings"]["routes"])


@pytest.mark.parametrize("allow_misses", [True, False])
def test_infeasible_crossing_stops_without_reassigning_contacts(
    tmp_path, monkeypatch, allow_misses
):
    from dashboard import server
    import json

    monkeypatch.setattr(server, "RUNS", tmp_path)
    monkeypatch.setattr(server, "media_url", lambda path: "/media/" + path.name)
    d = tmp_path / "take"
    d.mkdir()
    q = server.RenderQueue()
    q.active = {"id": "take", "status": "running"}
    settings = server.Settings(end_s=1.5, allow_misses=allow_misses)
    plan = {
        "chart": {"audio_offset_ms": 0},
        "touches": [
            {
                "hand": "left",
                "down_frame": 0,
                "up_frame": 180,
                "parts": [
                    {
                        "kind": "chain",
                        "from_frame": 0,
                        "to_frame": 180,
                        "occupation_id": "arc-parent",
                    }
                ],
            }
        ],
    }
    calls = []

    def command(arguments, directory, *args, **kwargs):
        if "--motion-only" in arguments:
            calls.append(1)
            (d / "metrics.json").write_text(
                json.dumps({"failure": {"frame": 60, "reason": "arm crossing"}})
            )
            raise ValueError(
                "Arm crossing at 1.000 s: cannot keep the right arm over with 3D clearance."
            )
        (d / "plan.json").write_text(json.dumps(plan))

    monkeypatch.setattr(q, "command", command)
    q.work(settings)
    assert len(calls) == 1 and q.active["status"] == "failed"
    assert "cannot keep the right arm over" in q.active["error"]

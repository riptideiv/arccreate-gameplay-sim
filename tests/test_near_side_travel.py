from dataclasses import dataclass
import json
import os
from pathlib import Path

import numpy as np
import pytest

from arc_hand_order.planning.arm_crossings import CrossingGraph, SIDES
from crossing_motion import validate
from motion_paths import positions


@dataclass(frozen=True)
class Contact:
    down: int
    path: tuple
    up: int


def contact(frame, x, y):
    return Contact(frame, ((x, y),) * 2, frame+2)


def test_new_right_tap_can_reveal_a_near_side_detour_for_the_other_hand():
    graph = CrossingGraph(lift=.012, speed=1.5, idle_speed=1., requested=34)
    moves = (
        (1, contact(1, .06476, .01007)),
        (0, contact(6, .02529, -.05038)),
        (1, contact(11, 0., .01007)),
        (0, contact(17, 0., .01007)),
        (1, contact(20, -.02529, -.05038)),
        (0, contact(26, -.06476, .01007)),
        (1, contact(31, .06476, .01007)),
    )
    state = graph.initial
    for side, touch in moves[:-1]:
        paths = list(state.paths)
        paths[side] = graph.project(paths[side], touch, True, side)
        state = graph._advance(state, tuple(paths))[0]
    assert state.routes[-1].top == "right" and state.routes[-1].end_frame is None
    first_crossing = state.routes[-1]

    next_right = graph.project(state.paths[1], moves[-1][1], True, 1)
    assert not graph._advance(state, (state.paths[0], next_right))
    alternatives = graph.step(state, ((2, moves[-1][1], "new"),))
    resolved = next(a for a in alternatives if a.routes[-1].top == "right"
                    and a.routes[-1].end_frame is not None)
    assert resolved.routes[-1].start_frame == first_crossing.start_frame
    finished = graph.finish(resolved)[0]
    curve, = graph.travel_segments(finished)
    assert curve["hand"] == "left" and curve["start_frame"] == 18
    planned = json.loads(json.dumps([curve]))
    paths = []
    for h, side in enumerate(SIDES):
        touches = [{"down_frame": t.down, "path_mm": [[x*1000, y*1000] for x, y in t.path]}
                   for hand, t in moves if hand == h]
        rebuilt = positions(touches, side, finished.through, .012, 1.5, 1.,
                            hold_end=True, planned_travel=planned)
        np.testing.assert_allclose(rebuilt, finished.paths[h].points, atol=1e-12)
        locked = finished.paths[h].locked
        default = positions(touches, side, finished.through, .012, 1.5, 1., hold_end=True)
        np.testing.assert_array_equal(rebuilt[locked], default[locked])
        paths.append(rebuilt)
    start = curve["start_frame"]
    travel = paths[0][start:start + len(curve["path_mm"])]
    assert np.linalg.norm(np.diff(travel, axis=0), axis=1).max()*60 <= 1.5001
    instructions = {"last_frame": finished.through, "routes": [
        {"start_frame": c.start_frame, "end_frame": c.end_frame, "top": c.top}
        for c in finished.routes
    ]}
    validate(np.stack(paths, axis=1), instructions,
             locked=np.stack([p.locked for p in finished.paths], axis=1))
    exit_frame = resolved.routes[-1].end_frame
    assert np.interp(exit_frame, np.arange(len(paths[0])), paths[0][:, 1] - paths[1][:, 1]) < 0


def test_motion_rejects_a_detour_through_a_scoring_contact():
    touches = [{"down_frame": f, "path_mm": [[0, 0]]} for f in (0, 2, 4)]
    curve = {"hand": "left", "start_frame": 0, "path_mm": [[0, 0, 6]]*5}
    with pytest.raises(ValueError, match="locked"):
        positions(touches, "left", 4, max_speed=1.5, hold_end=True, planned_travel=[curve])


@pytest.mark.skipif(not os.getenv("ARCCREATE_ARGHENA_TAKE"), reason="requires the local Arghena chart")
def test_arghena_forced_alternating_triangles_keep_all_twelve_notes(monkeypatch):
    import plan_excerpt

    take = Path(os.environ["ARCCREATE_ARGHENA_TAKE"])
    original_plan = json.loads((take/"plan.json").read_text())
    settings = json.loads((take/"settings.json").read_text())
    settings["allow_misses"] = False
    times = (25, 106, 188, 269, 350, 431, 512, 593, 674, 755, 836, 917)
    original = plan_excerpt.api.plan_chart

    def alternating(*args, **kwargs):
        kwargs["planner"] = {**kwargs["planner"], "fixed_hands": tuple(zip(times, ("right", "left")*6))}
        return original(*args, **kwargs)

    monkeypatch.setattr(plan_excerpt.api, "plan_chart", alternating)
    plan = plan_excerpt.create_plan(Path(original_plan["chart"]["path"]), 131000, 131950, settings)
    assert [(n["start_ms"], n["hands"][0]) for n in plan["notes"]] == list(zip(times, ("right", "left")*6))
    assert plan["planning"]["skipped_ticks"] == 0
    assert plan["arm_crossings"]["travel"]

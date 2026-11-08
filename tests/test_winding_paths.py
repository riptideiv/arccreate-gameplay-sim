"""Arm topology is history, not just the current separation or wrist yaw."""

import numpy as np
import pytest
from winding_paths import route, measure, WindingError


def circular_points():
    theta = np.radians(np.linspace(0, 360, 361))
    points = np.zeros((361, 2, 3))
    points[:, :, 2] = 0.006
    points[:, 0, 0] = -0.05 * np.cos(theta)
    points[:, 0, 1] = -0.05 * np.sin(theta)
    return points


def test_full_turn_is_not_erased_by_returning_to_same_idle_geometry():
    points = circular_points()
    assert measure(points)["maximum_deg"] == pytest.approx(360)
    with pytest.raises(WindingError):
        route(points, np.ones((361, 2), bool), max_speed=3)


def test_ground_hold_release_allows_a_costly_unwind_with_every_contact_pinned():
    points = circular_points()
    active = np.ones((361, 2), bool)
    active[121:240, 0] = (
        False  # released after holding the first part; other hand remains down
    )
    solved, report = route(points, active, max_speed=1)
    np.testing.assert_array_equal(solved[active], points[active])
    assert max(abs(report["minimum_deg"]), abs(report["maximum_deg"])) <= 260
    assert report["final_deg"] == pytest.approx(0, abs=1e-7)
    assert any(e["turns_relative_to_direct"] == -1 for e in report["unwinds"])
    assert report["extra_distance_m"] > 0 and report["cost"] > 0
    assert np.linalg.norm(np.diff(solved, axis=0), axis=-1).max() * 60 <= 1.00001


def test_winding_follows_arm_attachment_offsets_not_only_touch_points():
    points = np.zeros((361, 2, 3))
    points[:, :, 2] = 0.006
    points[:, 0, 0] = -0.05
    offsets = circular_points() - points
    assert measure(points)["maximum_deg"] == 0
    with pytest.raises(WindingError):
        route(points, np.ones((361, 2), bool), anchor_offsets=offsets)


def test_coincidence_cannot_teleport_a_hand_to_the_other_side():
    p = np.zeros((3, 2, 3))
    p[:, :, 2] = 0.006
    p[:, 1, 0] = [0.02, 0, -0.02]
    with pytest.raises(WindingError):
        route(p, np.ones((3, 2), bool))
    # Touching the same scoring target then withdrawing on the same side does
    # not invent a turn or reset accumulated turns.
    p[:, 1, 0] = [0.02, 0, 0.02]
    assert measure(p)["pole_crossings"] == 0


def test_no_unwind_needed_keeps_idle_motion_exactly_unchanged():
    p = np.zeros((121, 2, 3))
    p[:, :, 2] = 0.02
    p[:, 0, 0] = -0.085
    p[:, 1, 0] = 0.085
    solved, report = route(p, np.zeros((121, 2), bool))
    np.testing.assert_array_equal(solved, p)
    assert not report["unwinds"]


def test_angular_surface_speed_is_part_of_the_hard_move_budget():
    points = circular_points()
    active = np.ones((361, 2), bool)
    active[121:240, 0] = False
    with pytest.raises(WindingError):
        route(points, active, max_speed=0.2, surface_speed=np.full((360, 2), 0.2))


def test_actual_cuff_interpolation_is_verified_after_routing():
    from winding_paths import solve, verify_wrists
    from hand_pose import HAND_PARTS, NEUTRAL

    points = circular_points()
    points[:, :, :2] *= 2.4
    poses = np.broadcast_to(NEUTRAL, (361, 2, 2)).copy()
    parts = [
        [(name, (sign * x, y, z), r) for name, (x, y, z), r in HAND_PARTS]
        for sign in (1, -1)
    ]
    with pytest.raises(WindingError):
        verify_wrists(points, poses, parts)
    active = np.ones((361, 2), bool)
    active[121:240, 0] = False
    solved, report = solve(points, active, poses, parts, max_speed=1)
    assert report["interpolation_certified"] and report["anchor"] == "cuff"
    assert max(abs(report["minimum_deg"]), abs(report["maximum_deg"])) <= 260
    np.testing.assert_array_equal(solved[active], points[active])


@pytest.mark.parametrize("allow_misses,attempts", [(True, 5), (False, 1)])
def test_infeasible_winding_does_not_loop_forever_or_publish_an_invalid_pose(
    tmp_path, monkeypatch, allow_misses, attempts
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
                json.dumps({"failure": {"frame": 60, "reason": "arm winding"}})
            )
            raise ValueError(
                "Arm winding has no feasible unwind route at 1.000 s (limit ±260°)."
            )
        (d / "plan.json").write_text(json.dumps(plan))

    monkeypatch.setattr(q, "command", command)
    q.work(settings)
    assert len(calls) == attempts and q.active["status"] == "failed"
    assert "winding limit remains enforced" in q.active["error"]


@pytest.mark.parametrize("limit", [0, 360, float("nan")])
def test_invalid_limits_cannot_disable_the_winding_constraint(limit):
    with pytest.raises(ValueError, match="winding limit"):
        route(circular_points(), np.ones((361, 2), bool), limit_deg=limit)

"""Pose changes must preserve contacts, clear crossings and remain bounded."""

from pathlib import Path
import sys
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hand_pose import (
    AVOIDANCE_ACCEL_M_S2, HAND_PARTS, NEUTRAL, acceleration_diagnostics,
    accelerations, air_paths, arm_cost, bases, clearance_ellipsoids, diagnostics,
    glove_gap, margins, proxies, solve,
)

PARTS = [
    [(name, (sign * x, y, z), r) for name, (x, y, z), r in HAND_PARTS]
    for sign in (1, -1)
]


def test_palm_and_cuff_clearance_ellipsoids_follow_the_hand_axes():
    indices, radii = clearance_ellipsoids(PARTS[0])
    assert [PARTS[0][i][0] for i in indices] == ["palm", "cuff"]
    np.testing.assert_allclose(radii, [[.0189, .0189, .0105], [.0153, .0153, .0085]])
    # Only the palm proxy is present here. A 30 mm separation clears
    # the flat faces, then intersects when the hands tilt edge-on.
    delta = np.array([[[0., 0., .03]]])
    flat = bases(np.array([0., 0.]))
    edge_on = bases(np.array([90., 0.]))
    palm = (radii[:1], radii[:1])
    assert glove_gap(delta, (flat, flat), palm) == pytest.approx(.009)
    assert glove_gap(delta, (edge_on, edge_on), palm) == pytest.approx(-.0078)
    # The original pad geometry still sets the tablet margin.
    points = np.array([[0, 0, .006], [0, 0, .006]])
    _, floor = margins(points, np.zeros((2, 2)), PARTS)
    assert floor == pytest.approx(0.)


def speed_bounds(points, poses):
    radius = max(np.linalg.norm(p[1]) + max(p[2]) for p in PARTS[0]) + 0.002
    angular = np.linalg.norm(np.diff(poses, axis=0), axis=-1) * 60
    return np.linalg.norm(np.diff(points, axis=0), axis=-1) * 60 + radius * np.radians(
        angular
    ), angular


@pytest.mark.parametrize("angular_limit", [360, 30])
def test_both_hands_pivot_around_fixed_contacts_to_clear_a_crossing(angular_limit):
    count = 121
    points = np.zeros((count, 2, 3))
    points[:, :, 2] = 0.006
    points[:, 0, 0] = np.linspace(-0.05, 0.05, count)
    points[:, 1, 0] = -points[:, 0, 0]
    points[:, 0, 1] = -0.0125
    points[:, 1, 1] = 0.0125
    active = np.ones((count, 2), bool)
    baseline = diagnostics(
        points, np.broadcast_to(NEUTRAL, (count, 2, 2)), PARTS, 0.001
    )
    assert baseline["residual_proxy_overlap_samples"] > 0
    events = []
    solved, poses, report = solve(
        points, active, PARTS, max_speed=1.6, angular_limit=angular_limit,
        progress=lambda phase, completed, total, **_: events.append((phase, completed, total)),
    )
    wrists = [(completed, total) for phase, completed, total in events if phase == "pose_wrists"]
    assert wrists and wrists == [(i, wrists[-1][1]) for i in range(wrists[-1][1] + 1)]
    np.testing.assert_array_equal(solved, points)
    assert report["maximum_contact_drift_m"] == 0
    assert report["residual_proxy_overlap_samples"] == 0
    assert np.max(np.abs(poses - NEUTRAL)) > 15
    speed, angular = speed_bounds(solved, poses)
    assert speed.max() <= 1.6001 and angular.max() <= angular_limit + 0.001


def test_free_hands_can_pass_over_each_other_without_moving_contact_endpoints():
    points = np.zeros((121, 2, 3))
    points[:, :, 2] = 0.006
    points[:, 0, 0] = np.linspace(-0.05, 0.05, 121)
    points[:, 1, 0] = -points[:, 0, 0]
    active = np.zeros((121, 2), bool)
    active[[0, -1]] = True
    solved, poses, report = solve(points, active, PARTS, max_speed=1.6)
    np.testing.assert_array_equal(solved[active], points[active])
    assert report["air_overpasses"] > 0
    assert report["residual_proxy_overlap_samples"] == 0
    speed, angular = speed_bounds(solved, poses)
    assert speed.max() <= 1.6001 and angular.max() <= 360.001


def test_impossible_coincident_pads_are_reported_without_failing_or_moving_touches():
    points = np.zeros((3, 2, 3))
    points[:, :, 2] = 0.006
    solved, poses, report = solve(points, np.ones((3, 2), bool), PARTS)
    np.testing.assert_array_equal(solved, points)
    assert report["residual_pad_overlap_samples"] > 0
    assert report["collision_verification"] is False
    assert report["maximum_contact_drift_m"] == 0


def test_arm_model_prefers_downward_tap_flex_to_sideways_bend():
    points = np.array([[-0.09, 0.02, 0.006], [0.09, 0.02, 0.006]])
    tap = NEUTRAL.copy()
    tap[0, 0] += 10
    sideways = NEUTRAL.copy()
    sideways[0, 1] += 10
    reversed_wrist = NEUTRAL.copy()
    reversed_wrist[0, 1] += 150

    def cost(angles):
        centres = [proxies(angles[h], PARTS[h])[0] + points[h] for h in range(2)]
        return arm_cost(centres, bases(angles), PARTS, 0.001)

    assert cost(NEUTRAL) <= cost(tap) < cost(sideways) < cost(reversed_wrist)



def test_acceleration_report_distinguishes_rotation_from_pinned_translation():
    count = 9
    points = np.broadcast_to([[-.09, .02, .006], [.09, .02, .006]], (count, 2, 3))
    poses = np.broadcast_to(NEUTRAL, (count, 2, 2)).copy()
    time = np.arange(count) / 60
    poses[:, 0, 1] += np.degrees(0.5 * 12 * time**2)

    report = acceleration_diagnostics(points, poses, PARTS, None)

    assert report["pad_m_s2"]["peak"] == 0
    assert report["wrist_rotation_rad_s2"]["peak"] == pytest.approx(12)
    assert report["wrist_joint_m_s2"]["peak"] > 0
    assert report["elbow_m_s2"]["peak"] > 0



def test_crossing_avoidance_is_smooth_and_second_pass_replaces_lift(monkeypatch):
    import crossing_motion

    # Model a crossing that would always benefit from raising the left pad.
    def clearance(centres, angles, parts, layers, swivels):
        return centres[0][..., 0, 2] - .2

    monkeypatch.setattr(crossing_motion, "layer_margin", clearance)
    count = 41
    points = np.broadcast_to([[-.09, .02, .006], [.09, .02, .006]], (count, 2, 3))
    poses = np.broadcast_to(NEUTRAL, (count, 2, 2))
    active = np.ones((count, 2), dtype=bool)
    active[1:-1, 0] = False
    routes = [{"start_frame": 0, "end_frame": None, "top": "left"}]

    def avoid(path):
        return air_paths(path, active, 3., poses=poses, parts=PARTS,
                         idle_speed=.7, routes=routes, reference=points)[0]

    first = avoid(points)
    second = avoid(first)
    np.testing.assert_array_equal(first[active], points[active])
    assert .006 < first[:, 0, 2].max() <= .086 + 1e-9
    np.testing.assert_array_equal(first, second)
    optional = np.pad((second-points)[:, 0], ((1, 1), (0, 0)))
    assert np.linalg.norm(accelerations(optional), axis=-1).max() <= AVOIDANCE_ACCEL_M_S2
    assert np.linalg.norm(np.diff(second[:, 0], axis=0), axis=-1).max() * 60 <= .70001

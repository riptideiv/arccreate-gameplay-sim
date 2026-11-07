"""Pose changes must preserve contacts, clear crossings and remain bounded."""

from pathlib import Path
import sys
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hand_pose import HAND_PARTS, NEUTRAL, diagnostics, solve

PARTS = [
    [(name, (sign * x, y, z), r) for name, (x, y, z), r in HAND_PARTS]
    for sign in (1, -1)
]


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
    solved, poses, report = solve(
        points, active, PARTS, max_speed=1.6, angular_limit=angular_limit
    )
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

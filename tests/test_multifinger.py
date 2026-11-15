from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hand_pose import NEUTRAL
from multifinger import (
    assign_digits, feasible_yaw, fit_thumb_yaw, hand_calibration, index_paths, samples, smooth_thumb_yaw, thumb_joints,
)


def touch(finger, x, y, down=4, up=16, kind="hold"):
    return dict(hand="left", finger=finger, down_frame=down, up_frame=up,
                path_mm=[(x, y)] * (up-down), parts=[dict(kind=kind)])


def test_upper_contact_becomes_index_and_lower_contact_becomes_thumb():
    lower, upper = touch(1, -30, -50), touch(2, -65, 10, down=10)
    assign_digits([lower, upper], 130, 1, 4)
    assert lower["finger"] == 2 and upper["finger"] == 1

    contacts = {"left": [upper], "right": []}
    calibration = hand_calibration(.1735)
    scale = calibration["scale"]
    length = calibration["thumb_length_mm"] / 1000
    previous_scale = 173.5 / (1.95 * calibration["original_open_reach_mm"])
    np.testing.assert_allclose(length * 1000, .6 * previous_scale * calibration["original_thumb_length_mm"])
    np.testing.assert_allclose(calibration["open_reach_mm"] * 1.95, 173.5)
    thumbs = {"left": samples([lower], scale), "right": {}}
    paths, _, active = index_paths(contacts, thumbs, ("left", "right"), 45, .012, 4, .7, scale=scale, length=length)
    poses = np.broadcast_to(NEUTRAL, (46, 2, 2))
    poses = fit_thumb_yaw(paths, poses, thumbs, ("left", "right"), scale, 360, 4, .08 * scale, length=length)
    joints = thumb_joints(paths, poses, thumbs, ("left", "right"), scale=scale, length=length)
    for frame, position in samples([upper], scale).items():
        assert active[frame, 0]
        np.testing.assert_allclose(paths[0][frame], position)
        np.testing.assert_allclose(joints["left"][frame, 2], thumbs["left"][frame])
    np.testing.assert_allclose(np.linalg.norm(np.diff(joints["left"], axis=1), axis=-1),
                               length / 2)
    # An inactive thumb rests straight along the side of the hand.
    a, b, c = joints["right"][0]
    np.testing.assert_allclose(b-a, c-b, atol=1e-10)


def test_relabeling_keeps_close_point_presses_on_different_digits():
    first = touch(1, -25, -40, down=4, up=5, kind="tap")
    second = touch(2, -30, -40, down=10, up=11, kind="tap")
    assign_digits([first, second], 130, 1, 4)
    assert first["finger"] != second["finger"]



def test_thumb_yaw_eases_a_turn_into_and_out_of_contact():
    preferred = np.zeros(100)
    lower = np.full_like(preferred, -np.inf)
    upper = np.full_like(preferred, np.inf)
    lower[45:55] = upper[45:55] = 36.
    budget = np.full(len(preferred) - 1, 6.)
    greedy = feasible_yaw(preferred, lower, upper, budget)
    greedy_acceleration = np.diff(greedy, n=2)
    yaw = feasible_yaw(preferred, lower, upper, np.minimum(budget, 2.5))

    smooth_thumb_yaw(yaw, preferred, lower, upper, budget, range(45, 55))

    np.testing.assert_allclose(yaw[45:55], 36.)
    assert np.abs(np.diff(yaw)).max() <= budget.max() + 1e-9
    acceleration = np.diff(yaw, n=2)
    assert np.abs(acceleration).max() < np.abs(greedy_acceleration).max()
    assert np.sum(acceleration**2) < np.sum(greedy_acceleration**2)
    np.testing.assert_array_equal(yaw[:27], 0.)
    np.testing.assert_array_equal(yaw[73:], 0.)

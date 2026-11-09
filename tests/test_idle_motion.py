from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motion_paths import positions, home_position


@pytest.mark.parametrize("side", ["left", "right"])
def test_long_idle_returns_to_own_side_and_waits_for_next_note(side):
    # The next contact is on the opposite side. Waiting there would hide the bug.
    xy = [55 if side == "left" else -55, -15]
    touches = [
        {"down_frame": 30, "path_mm": [xy, xy]},
        {"down_frame": 480, "path_mm": [xy, xy]},
    ]
    path = positions(touches, side, 600, max_speed=0.6)
    np.testing.assert_allclose(
        path[150:380], np.broadcast_to(home_position(side, 0.008), (230, 3))
    )
    np.testing.assert_allclose(
        path[[30, 31, 480, 481]], np.tile([xy[0] / 1000, xy[1] / 1000, 0.006], (4, 1))
    )
    assert np.linalg.norm(np.diff(path, axis=0), axis=1).max() * 60 <= 0.6001


def test_long_initial_wait_and_unused_hand_stay_home():
    home = home_position("right", 0.008)
    path = positions(
        [{"down_frame": 500, "path_mm": [[-50, 0], [-50, 0]]}],
        "right",
        620,
        max_speed=0.6,
    )
    np.testing.assert_allclose(path[:400], np.broadcast_to(home, (400, 3)))
    unused = positions([], "right", 620, max_speed=0.6)
    np.testing.assert_allclose(unused, np.broadcast_to(home, unused.shape))


def test_short_gap_keeps_a_direct_feasible_transfer():
    touches = [
        {"down_frame": 30, "path_mm": [[-50, -20], [-50, -20]]},
        {"down_frame": 37, "path_mm": [[-40, -20], [-40, -20]]},
    ]
    path = positions(touches, "left", 100, max_speed=0.6)
    assert path[34, 0] > -0.05
    assert np.linalg.norm(np.diff(path, axis=0), axis=1).max() * 60 <= 0.6001


def test_repeated_taps_at_same_position_lift_between_contacts():
    touches = [
        {"down_frame": 30, "path_mm": [[0, -20], [0, -20]]},
        {"down_frame": 43, "path_mm": [[0, -20], [0, -20]]},
    ]
    path = positions(touches, "left", 100, lift=0.008, max_speed=0.6)
    np.testing.assert_allclose(path[[30, 31, 43, 44], 2], 0.006)
    assert path[37, 2] == pytest.approx(0.014)
    assert np.linalg.norm(np.diff(path, axis=0), axis=1).max() * 60 <= 0.6001

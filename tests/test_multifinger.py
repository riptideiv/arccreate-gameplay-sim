from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hand_pose import NEUTRAL
from multifinger import assign_digits, index_paths, samples, thumb_joints


def touch(finger, x, y, down=4, up=16, kind="hold"):
    return dict(hand="left", finger=finger, down_frame=down, up_frame=up,
                path_mm=[(x, y)] * (up-down), parts=[dict(kind=kind)])


def test_upper_contact_becomes_index_and_lower_contact_becomes_thumb():
    lower, upper = touch(1, -30, -50), touch(2, -65, 10)
    assign_digits([lower, upper], 130, 1, 4)
    assert lower["finger"] == 2 and upper["finger"] == 1

    contacts = {"left": [upper], "right": []}
    thumbs = {"left": samples([lower]), "right": {}}
    paths, _, active = index_paths(contacts, thumbs, ("left", "right"), 45, .012, 4, .7)
    poses = np.broadcast_to(NEUTRAL, (46, 2, 2))
    joints = thumb_joints(paths, poses, thumbs, ("left", "right"))
    for frame, position in samples([upper]).items():
        assert active[frame, 0]
        np.testing.assert_allclose(paths[0][frame], position)
        np.testing.assert_allclose(joints["left"][frame, 2], thumbs["left"][frame])
    # An inactive thumb rests straight along the side of the hand.
    a, b, c = joints["right"][0]
    np.testing.assert_allclose(b-a, c-b, atol=1e-10)


def test_relabeling_keeps_close_point_presses_on_different_digits():
    first = touch(1, -25, -40, down=4, up=5, kind="tap")
    second = touch(2, -30, -40, down=10, up=11, kind="tap")
    assign_digits([first, second], 130, 1, 4)
    assert first["finger"] != second["finger"]

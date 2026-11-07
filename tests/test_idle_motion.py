from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motion_paths import positions, home_position
from hand_pose import air_paths


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


def test_return_to_side_respects_separate_idle_cap():
    touches=[{'down_frame':30,'path_mm':[[70,20],[70,20]]},
             {'down_frame':180,'path_mm':[[70,20],[70,20]]}]
    path=positions(touches,'left',300,max_speed=4,idle_speed=.2)
    # The optional return must not raise its speed to fit a full trip home.
    assert np.linalg.norm(np.diff(path[31:180],axis=0),axis=1).max()*60 <= .2001
    np.testing.assert_allclose(path[[30,31,180,181]],np.tile([.07,.02,.006],(4,1)))


def test_collision_detours_cannot_use_the_full_contact_speed_budget():
    points=np.zeros((13,2,3));points[:,:,2]=.006
    points[:,0,0]=np.linspace(-.05,.05,13)
    active=np.zeros((13,2),bool);active[[0,-1]]=True;active[:,1]=True
    solved,_=air_paths(points,active,max_speed=4,idle_speed=.7)
    np.testing.assert_array_equal(solved[active],points[active])
    assert np.linalg.norm(np.diff(solved[:,0],axis=0),axis=1).max()*60 <= .7001


def test_required_contact_transfer_can_exceed_idle_speed_without_added_detour():
    points=np.zeros((7,2,3));points[:,:,2]=.006
    points[:,0,0]=np.linspace(-.1,.1,7)
    active=np.zeros((7,2),bool);active[[0,-1]]=True;active[:,1]=True
    solved,_=air_paths(points,active,max_speed=4,idle_speed=.5)
    np.testing.assert_array_equal(solved[active],points[active])
    assert np.linalg.norm(np.diff(solved[:,0],axis=0),axis=1).max()*60 <= 2.0001


def test_requested_dashboard_defaults_and_idle_cache_separation(tmp_path):
    from dashboard.server import Settings
    from plan_cache import request_key
    settings=Settings()
    assert (settings.repeat_comfort_ms,settings.jack_gap_ms,settings.jack_free,settings.jack_weight)==(167,133,2,10)
    assert settings.scroll_speed==2.5 and settings.song_delay_ms==0
    assert settings.prefer_arc_trace and settings.full_arc_hold
    assert settings.camera_elevation_deg==70 and settings.idle_speed_m_s==1
    p=tmp_path/'chart.aff';p.write_text('AudioOffset:0\n-\n(1000,1);\n')
    a=request_key(p,0,2000,{'idle_speed_m_s':1},False,(),())[0]
    b=request_key(p,0,2000,{'idle_speed_m_s':.2},False,(),())[0]
    assert a==b

from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pose_cache as cache


@pytest.fixture
def pose_input():
    points=np.zeros((10,2,3));points[:,:,2]=.006
    active=np.zeros((10,2),bool);active[[0,-1]]=True
    angles=np.zeros((10,2,2));report={'verified':True,'solver':'test','idle_speed_m_s':1}
    return points,active,angles,report


def test_shared_key_ignores_view_media_and_planner_metadata_but_tracks_pose_inputs(pose_input):
    points,active,_,_=pose_input
    first=cache.request_key(points,active,{})
    assert first==cache.request_key(points,active,{'scroll_speed':2.5,'song_delay_ms':200,'hit_sounds':False,
        'camera_elevation_deg':70,'quality':'final','start_s':56.55,'chart':'different-id','search_width':2048})
    assert first==cache.request_key(points,active,cache.POSE_DEFAULTS)
    for name,value in {'max_speed_m_s':4,'angular_limit_deg_s':180,'lift_mm':6,'clearance_mm':2,'idle_speed_m_s':.5}.items():
        assert first!=cache.request_key(points,active,{name:value})
    moved=points.copy();moved[5,0,0]=.01
    assert first!=cache.request_key(moved,active,{})
    pins=active.copy();pins[5,0]=True
    assert first!=cache.request_key(points,pins,{})


def test_two_takes_share_validated_result_and_reject_tampering(tmp_path,pose_input):
    points,active,angles,report=pose_input
    key=cache.request_key(points,active,{'scroll_speed':2})
    assert cache.read(tmp_path,key,points,active) is None
    cache.write(tmp_path,key,points,angles,report,points,active)
    other_key=cache.request_key(points,active,{'scroll_speed':5.2,'camera_yaw_deg':20})
    result=cache.read(tmp_path,other_key,points,active)
    assert result is not None
    np.testing.assert_array_equal(result[0],points)
    np.testing.assert_array_equal(result[1],angles)
    assert result[2]==report
    path=tmp_path/(key+'.npz')
    with np.load(path,allow_pickle=False) as z:entry={k:z[k] for k in z.files}
    entry['points'][4,0,0]=.1
    np.savez(path,**entry)
    assert cache.read(tmp_path,key,points,active) is None
    assert not list(tmp_path.glob('*.tmp'))


def test_unverified_or_contact_altering_poses_are_never_cached(tmp_path,pose_input):
    points,active,angles,report=pose_input
    with pytest.raises(ValueError,match='unverified'):
        cache.write(tmp_path,'key',points,angles,{'verified':False},points,active)
    moved=points.copy();moved[0,0,0]=.1
    with pytest.raises(ValueError,match='contact-altering'):
        cache.write(tmp_path,'key',moved,angles,report,points,active)
    assert not list(tmp_path.iterdir())


def test_solver_code_changes_invalidate_shared_poses(tmp_path,pose_input):
    points,active,_,_=pose_input
    for name in ('animate.py','hand_pose.py','motion_paths.py','pose_cache.py'):
        (tmp_path/name).write_text('version one')
    key=cache.request_key(points,active,{},root=tmp_path)
    (tmp_path/'hand_pose.py').write_text('different finger geometry or solver')
    assert key!=cache.request_key(points,active,{},root=tmp_path)


def test_unavailable_optional_cache_does_not_break_render(tmp_path,pose_input):
    points,active,angles,report=pose_input
    path=tmp_path/'file';path.write_text('occupied')
    cache.write(path,'key',points,angles,report,points,active)

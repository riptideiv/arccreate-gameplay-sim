from pathlib import Path
import sys
from dataclasses import replace
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arc_hand_order.planning.wrist import HOME_XY,NEUTRAL,natural_angles,deviations,strain,WristEstimate
from hand_pose import HAND_PARTS,solve
from arc_hand_order.planning.two_finger import PlannerOptions
from arc_hand_order.planning.multi_finger import MultiCost,MultiFingerOptions


def test_body_relative_angles_match_idle_and_wrap_shortest_yaw():
    np.testing.assert_allclose(natural_angles(HOME_XY),NEUTRAL)
    a=NEUTRAL.copy();a[0,1]+=360
    assert np.max(np.abs(deviations(a,HOME_XY)))<1e-10
    moved=HOME_XY.copy();moved[0,0]+=.1
    assert natural_angles(moved)[0,1]<NEUTRAL[0,1]


def test_comfort_window_and_seconds_normalization():
    a=NEUTRAL.copy();a[0,1]+=120
    assert strain(a,HOME_XY,30)==pytest.approx(1)
    assert strain(a,HOME_XY,120)==0
    assert np.sum(strain(np.broadcast_to(a,(120,2,2)),HOME_XY,30))/60==pytest.approx(2)


def test_static_glove_proxy_is_symmetric_cached_and_uses_given_geometry():
    model=WristEstimate(HAND_PARTS)
    assert model.frame((-.05,0),(.05,0))==0
    a,b=(.01,0),(-.01,.01)
    value=model.frame(a,b);assert value>0
    assert model.frame((-b[0],b[1]),(-a[0],a[1]))==pytest.approx(value)
    before=model.query_count;assert model.frame(a,b)==value and model.query_count==before
    assert WristEstimate(HAND_PARTS,90).frame(a,b)<=value
    with pytest.raises(ValueError):WristEstimate(())


def test_depth_and_wrist_weights_add_independently():
    cost=MultiCost(crossing_depth=.09,wrist_twist_s=2)
    assert cost.weighted(PlannerOptions(wrist_twist_weight=3),MultiFingerOptions(crossing_depth_weight=100))==pytest.approx(15)
    assert (cost+cost).wrist_twist_s==4
    for name in ('wrist_twist_weight','wrist_twist_free_deg'):
        with pytest.raises(ValueError):PlannerOptions(**{name:float('nan')})
        with pytest.raises(ValueError):PlannerOptions(**{name:-1})


def test_depth_cost_changes_assignment_without_dropping_ticks(tmp_path):
    from plan_excerpt import create_plan
    f=tmp_path/'cross.aff';f.write_text('AudioOffset:0\n-\ntiming(0,120,4);\n'
        'arc(1000,2200,.9,.9,s,.5,.5,0,none,false);\n'
        'arc(1000,2200,.1,.1,s,.5,.5,1,none,false);')
    s={'arc_path_mode':'score','full_arc_hold':True,'search_width':64,'max_speed_m_s':3}
    before=create_plan(f,0,3000,{**s,'crossing_depth_weight':0})
    after=create_plan(f,0,3000,{**s,'crossing_depth_weight':2000})
    assert not before['misses'] and not after['misses']
    assert before['crossing_ergonomics']['depth_m_s']>0
    assert after['crossing_ergonomics']['depth_m_s']<before['crossing_ergonomics']['depth_m_s']*.1
    assert {(x['id'],x['planned_hit']) for x in before['arc_tick_targets']}=={(x['id'],x['planned_hit']) for x in after['arc_tick_targets']}


def test_actual_pose_solver_prefers_less_bent_wrists_without_moving_contacts():
    from arc_hand_order.planning.wrist import metrics
    parts=[[(name,(sign*x,y,z),radius) for name,(x,y,z),radius in HAND_PARTS] for sign in (1,-1)]
    points=np.zeros((31,2,3));points[:,:,2]=.006;points[:,0,0]=.08;points[:,1,0]=.16
    active=np.ones((31,2),bool)
    a,old,_=solve(points,active,parts,wrist_twist_weight=0,wrist_twist_free_deg=0)
    b,new,_=solve(points,active,parts,wrist_twist_weight=100,wrist_twist_free_deg=0)
    np.testing.assert_array_equal(a,points);np.testing.assert_array_equal(b,points)
    assert metrics(b,new,active,0)['active_strain_s']<metrics(a,old,active,0)['active_strain_s']


def test_wrist_estimate_changes_hand_choice_not_only_final_glove_pose(tmp_path):
    from plan_excerpt import create_plan
    f=tmp_path/'near-cross.aff';f.write_text('AudioOffset:0\n-\ntiming(0,120,4);\n'
        'arc(1000,2200,.55,.55,s,.5,.5,0,none,false);\n'
        'arc(1000,2200,.45,.45,s,.55,.55,1,none,false);')
    s={'arc_path_mode':'score','prefer_arc_trace':True,'arc_trace_weight':1000,'full_arc_hold':True,
        'max_speed_m_s':3,'search_width':64}
    before=create_plan(f,0,3000,{**s,'wrist_twist_weight':1})
    after=create_plan(f,0,3000,{**s,'wrist_twist_weight':10000})
    assert not before['misses'] and not after['misses']
    assert before['crossing_ergonomics']['estimated_wrist_strain_s']>0
    assert after['crossing_ergonomics']['estimated_wrist_strain_s']==0
    assert [(t['hand'],t['parts'][0]['colour']) for t in before['touches']]!=[(t['hand'],t['parts'][0]['colour']) for t in after['touches']]
    assert {(x['id'],x['planned_hit']) for x in before['arc_tick_targets']}=={(x['id'],x['planned_hit']) for x in after['arc_tick_targets']}

"""Acceleration effort, boundary accounting and genuine low-frequency guides."""
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arc_hand_order.planning.trajectory import acceleration_energy, touch_energy, smooth_path, transfer_energy
from arc_hand_order.planning.two_finger import PlannerOptions


def touch(path, down=0, prefix=None):
    t=SimpleNamespace(path=tuple(map(tuple,path)),down=down,up=down+len(path),
        first_xy=tuple(path[0]),last_xy=tuple(path[-1]))
    if prefix is not None: t._native_prefix=prefix
    return t


def test_acceleration_uses_metres_seconds_and_elapsed_time():
    for rate in (30,60,120):
        t=np.arange(rate+1)/rate
        p=np.column_stack((.5*3*t*t,np.zeros_like(t)))
        assert acceleration_energy(p,rate=rate,rest_ends=False)==pytest.approx(9/25*(1-1/rate))
    assert acceleration_energy([(0,0)]*100)==0
    assert acceleration_energy([(i*.01,0) for i in range(10)],rest_ends=False)<1e-20


def test_prefix_cache_counts_bends_at_the_seam_once_and_removes_old_rest():
    path=((0,0),(.01,0),(.02,0),(.02,.01),(.02,.02),(.03,.02))
    old=touch(path[:3]);new=touch(path[:4],prefix=old);last=touch(path,prefix=new)
    assert touch_energy(new)==pytest.approx(acceleration_energy(path[:4]))
    assert touch_energy(last)==pytest.approx(acceleration_energy(path))
    assert touch_energy(old)+(touch_energy(new)-touch_energy(old))+(touch_energy(last)-touch_energy(new))==pytest.approx(acceleration_energy(path))


def test_join_replaces_endpoint_rests_and_retains_incoming_velocity():
    a=touch(((0,0),(.01,0)),0);b=touch(((.02,0),(.03,0)),2)
    # With a one-frame flight these form one constant-velocity path.
    total=touch_energy(a)+touch_energy(b)+transfer_energy(a,b,3)
    assert total==pytest.approx(acceleration_energy(a.path+b.path))
    reversed_a=touch(((.02,0),(.01,0)),0)
    assert transfer_energy(reversed_a,b,3)>transfer_energy(a,b,3)


def test_smoothing_is_linear_time_natural_and_suppresses_fast_reversals():
    x=np.linspace(0,.12,200);line=tuple(zip(x,x*.3))
    assert np.allclose(smooth_path(line,.16),line,atol=1e-10)
    guide=tuple(zip(x,.02*np.sin(np.arange(200)*np.pi/3)))
    smooth=smooth_path(guide,.16)
    assert acceleration_energy(smooth)<acceleration_energy(guide)*.001
    assert np.ptp(np.array(smooth)[:,0])==pytest.approx(.12)


def test_penalty_validation_and_independent_weight():
    for value in (-1,float('nan'),float('inf')):
        with pytest.raises(ValueError):PlannerOptions(acceleration_weight=value)
    from arc_hand_order.planning.multi_finger import MultiCost,MultiFingerOptions
    c=MultiCost(acceleration_energy_s=2)
    assert c.weighted(PlannerOptions(acceleration_weight=3),MultiFingerOptions())==6
    assert (c+c).acceleration_energy_s==4


def test_same_endpoint_different_velocity_must_not_merge(tmp_path):
    from arc_hand_order import api
    from arc_hand_order.planning.multi_finger import _Graph
    from arc_hand_order.planning.projection import GameplayProjection
    from arc_hand_order.planning.occupations import occupations_from_chart
    from arc_hand_order.planning.two_finger import build_candidates
    from arc_hand_order.planning.multi_finger import MultiFingerOptions
    f=tmp_path/'tap.aff';f.write_text('AudioOffset:0\n-\n(100,1);\n')
    o=occupations_from_chart(api.load_chart(f).chart);projection=GameplayProjection(api.load_device())
    options=PlannerOptions(acceleration_weight=100)
    groups=build_candidates(o,projection,options);g=_Graph(groups,projection,options,MultiFingerOptions())
    a=g.single(groups[0][0]);b=g._intern(a.parts,a.bridges,((a.last_xy[0]+.01,a.last_xy[1]),a.last_xy))
    assert g.future(a.id,100)!=g.future(b.id,100)


def test_planner_keeps_regular_trace_but_does_not_chase_zigzag(tmp_path):
    from plan_excerpt import create_plan
    f=tmp_path/'smooth-and-teeth.aff'
    f.write_text('AudioOffset:0\n-\ntiming(0,120,4);\n'
        'arc(1000,2500,0,1,s,.5,.5,0,none,false);\n'+''.join(
        f'arc({3000+k*100},{3100+k*100},{k%2},{1-k%2},s,.5,.5,0,none,false);\n'
        for k in range(18)))
    settings={'arc_path_mode':'score','prefer_arc_trace':True,'arc_trace_weight':1000,
        'max_speed_m_s':3,'search_width':64,'full_arc_hold':True,'allow_misses':False}
    before=create_plan(f,0,5500,{**settings,'acceleration_weight':0})
    after=create_plan(f,0,5500,{**settings,'acceleration_weight':100})
    assert not before['misses'] and not after['misses']
    assert {(x['id'],x['planned_hit']) for x in before['arc_tick_targets']}=={(x['id'],x['planned_hit']) for x in after['arc_tick_targets']}
    def chains(plan,start):
        return [t for t in plan['touches'] if start <= t['down_ms'] < start+1000]
    def length(t):return np.linalg.norm(np.diff(np.array(t['path_mm']),axis=0),axis=1).sum()
    assert sum(map(length,chains(after,900))) >= .95*sum(map(length,chains(before,900)))
    assert sum(map(length,chains(after,2900))) < .1*sum(map(length,chains(before,2900)))
    assert after['acceleration']['held_rms_m_s2'] < before['acceleration']['held_rms_m_s2']*.1


def test_search_energy_equals_whole_final_trajectories_including_flights(tmp_path):
    from arc_hand_order import api
    from arc_hand_order.planning.occupations import occupations_from_chart
    from arc_hand_order.planning.arc_scoring import native_occupations
    from plan_excerpt import split_arc_ticks
    f=tmp_path/'joins.aff';f.write_text('AudioOffset:0\n-\ntiming(0,120,4);\n'
        '(500,1);arc(1000,2200,0,1,s,.3,.6,0,none,false);'
        'arc(2200,3000,1,.2,s,.6,.3,0,none,false);(3400,4);')
    loaded=api.load_chart(f)
    o=split_arc_ticks(native_occupations(occupations_from_chart(loaded.chart)))
    r=api.plan_chart(loaded,occupation_set=o,planner={'scoring_arcs':True,
        'prefer_arc_trace':True,'arc_trace_weight':1000,'acceleration_weight':100},
        multi={'second_finger':False,'second_finger_again':False,'tick_contacts':True,
            'meeting_swaps':False,'covered_holds':False,'covered_arcs':False,
            'steady_arcs':False,'enveloped_arcs':False,'max_states':64})
    total=0
    for side in ('left','right'):
        rows=sorted((ft.touch for ft in r.plan.touches if ft.hand==side),key=lambda t:t.down)
        previous=None
        for t in rows:
            total += acceleration_energy(t.path)+transfer_energy(previous,t,r.plan.options.speed_max_m_s)
            previous=t
    assert r.plan.cost.acceleration_energy_s==pytest.approx(total,abs=1e-8)

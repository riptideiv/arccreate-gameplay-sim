"""Partial arc recovery must retain scoring windows and native input locks."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order import api
from arc_hand_order.planning.occupations import occupations_from_chart
from plan_excerpt import create_plan, split_arc_ticks


def snake(tmp_path):
    chart = tmp_path / 'snake.aff'
    chart.write_text('AudioOffset:0\n-\ntiming(0,120,4);'
                     'arc(1000,5000,.5,.5,s,.5,.5,0,none,false);')
    occupation, = occupations_from_chart(api.load_chart(chart).chart).items
    return chart, occupation


def test_release_and_reacquire_keeps_both_ends_of_the_same_snake(tmp_path):
    chart, occupation = snake(tmp_path)
    avoid = [{'parent':occupation.occupation_id,'start_ms':2100,'end_ms':2400}]
    plan = create_plan(chart,0,5500,{'max_speed_m_s':4},avoid_contacts=avoid)
    assert plan['planning']['tick_contacts']
    assert plan['planning']['arc_reacquisitions'] == 1
    # The blocked middle loses one tick, not the snake's fifteen ticks.
    assert plan['planning']['skipped_ticks'] == 1
    assert [m['start_ms'] for m in plan['misses']] == [2250]
    first, second = plan['touches']
    assert first['up_ms'] < 2100 < 2400 < second['down_ms']
    # Native ArcCreate locks a lifted arc colour for min(4*tick interval,1s).
    assert second['down_ms'] > first['up_ms'] + 1000
    assert first['parts'][0]['scoring_ticks'][0]['time_ms'] == 1250
    assert second['parts'][-1]['scoring_ticks'][0]['time_ms'] == 4750
    assert all(t['finger']==1 for t in plan['touches'])


def test_strict_mode_rejects_a_gap_that_the_native_colour_lock_cannot_recover(tmp_path):
    chart, occupation = snake(tmp_path)
    with pytest.raises(ValueError,match='no candidate path'):
        create_plan(chart,0,5500,{'max_speed_m_s':4,'allow_misses':False},
                       avoid_contacts=[{'parent':occupation.occupation_id,'start_ms':2100,'end_ms':2130}])


@pytest.mark.parametrize('full_arc_hold',[False,True])
def test_cross_colour_grace_allows_a_quick_reacquisition(tmp_path,full_arc_hold):
    chart, occupation = snake(tmp_path)
    chart.write_text(chart.read_text()+'arc(2000,2500,.5,.5,s,.5,.5,1,none,false);')
    plan = create_plan(chart,0,5500,{'max_speed_m_s':4,'allow_misses':False,'full_arc_hold':full_arc_hold},
                       avoid_contacts=[{'parent':occupation.occupation_id,'start_ms':2100,'end_ms':2130}])
    assert not plan['misses']
    touches=[t for t in plan['touches'] if any(p.get('parent_occupation_id')==occupation.occupation_id for p in t['parts'])]
    first,second=touches
    assert first['up_ms'] < 2100 and second['down_ms'] > 2130
    assert second['down_ms'] < first['up_ms'] + 1000


def test_following_between_ticks_uses_the_arc_curve(tmp_path):
    chart = tmp_path/'curve.aff'
    chart.write_text('AudioOffset:0\n-\ntiming(0,120,4);'
                     'arc(1000,4000,.25,.75,si,.2,.8,0,none,false);')
    occupation,=occupations_from_chart(api.load_chart(chart).chart).items
    # A harmless exclusion outside the arc forces the granular path.
    plan=create_plan(chart,0,4500,{'max_speed_m_s':4,'allow_misses':False},
                     avoid_contacts=[{'parent':occupation.occupation_id,'start_ms':4100,'end_ms':4200}])
    assert not plan['misses']
    touch,=plan['touches']
    assert len(touch['parts']) == len(occupation.score_points)
    assert len(touch['path_mm']) == touch['up_frame']-touch['down_frame']
    assert len(set(tuple(xy) for xy in touch['path_mm'])) > 100


def test_busy_ground_hands_miss_only_the_arc_head_then_pick_up_its_tail(tmp_path):
    chart=tmp_path/'busy.aff'
    chart.write_text('AudioOffset:0\n-\ntiming(0,120,4);'
                     'hold(1000,3000,1);hold(1000,3000,4);'
                     'arc(1500,4500,.5,.5,s,1,1,0,none,false);')
    plan=create_plan(chart,0,5000,{'max_speed_m_s':4})
    assert [(m['kind'],m['start_ms']) for m in plan['misses']] == [('arc_tick',1750),('arc_tick',2000)]
    recovered=[p['scoring_ticks'][0]['time_ms'] for t in plan['touches'] for p in t['parts']
               if p.get('parent_occupation_id')]
    assert min(recovered)==2250 and max(recovered)==4250


def test_colour_lock_survives_an_intervening_ground_tap(tmp_path):
    chart, occupation=snake(tmp_path)
    chart.write_text(chart.read_text()+'(2200,1);')
    loaded=api.load_chart(chart)
    result=api.plan_chart(loaded,occupation_set=split_arc_ticks(occupations_from_chart(loaded.chart)),
        planner={'speed_max_m_s':4,'fixed_hands':((2200,'left'),),
                 'contact_exclusions':((occupation.occupation_id,2100,2400),)},
        multi={'allow_misses':True,'tick_contacts':True,'second_finger':False,'second_finger_again':False,
               'meeting_swaps':False,'covered_holds':False,'covered_arcs':False,'steady_arcs':False,
               'enveloped_arcs':False,'max_states':256})
    first,tap,second=api.plan_to_dict(result)['touches']
    assert first['hand']==tap['hand']==second['hand']=='left'
    assert tap['parts'][0]['kind']=='tap'
    assert first['up_ms'] < tap['down_ms'] < second['down_ms']
    assert second['down_ms'] > first['up_ms'] + 1000

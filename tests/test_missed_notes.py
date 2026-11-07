from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order import api
from dashboard.server import Settings
from plan_excerpt import create_plan


def chart(tmp_path, body):
    target = tmp_path / "test.aff"
    target.write_text("AudioOffset:0\n-\ntiming(0,120,4);" + body)
    return target


def test_impossible_contact_is_skipped_and_later_play_continues(tmp_path):
    source = chart(tmp_path, "hold(1000,2000,1);hold(1000,2000,4);"
                   "arc(1400,1400,.5,.5,s,1,1,0,none,true)[arctap(1400)];(2300,2);")
    with pytest.raises(ValueError, match="no candidate path"):
        create_plan(source, 0, 3000, {"max_speed_m_s":4,"allow_misses":False})
    output = create_plan(source, 0, 3000, {"max_speed_m_s":4,"allow_misses":True})
    assert output['planning']['skipped_ticks'] == 1
    assert output['misses'][0]['kind'] == 'sky'
    assert any(part['start_ms'] == 2300 for t in output['touches'] for part in t['parts'])
    assert all(t['finger'] == 1 for t in output['touches'])


def test_skip_search_does_not_trade_score_for_a_lower_movement_cost(tmp_path):
    source = chart(tmp_path, '(1000,1);(2000,4);')
    result = api.plan_chart(api.load_chart(source),
        planner={'movement_weight':1e12, 'distance_weight':1e12},
        multi={'allow_misses':True,'second_finger':False,'second_finger_again':False,
               'covered_holds':False,'covered_arcs':False,'steady_arcs':False,'enveloped_arcs':False,
               'meeting_swaps':False, 'max_states':256})
    assert result.plan.cost.missed_ticks == 0
    assert not result.plan.skipped


def test_an_arc_with_no_candidate_can_be_skipped(tmp_path):
    source = chart(tmp_path, 'arc(1000,2000,-2,2,s,1,1,0,none,false);(2500,2);')
    output = create_plan(source, 0, 3000, {'max_speed_m_s':.1,'allow_misses':True})
    assert any(m['kind']=='arc_tick' for m in output['misses'])
    assert output['touches']


def test_carry_in_and_carry_out_hold_ticks_are_preserved(tmp_path):
    source = chart(tmp_path, 'hold(1000,4000,1);')
    output = create_plan(source, 2000, 3000, {'max_speed_m_s':4})
    assert not output['misses']
    touch, = output['touches']
    assert touch['down_ms'] == 0
    assert 1000 <= touch['up_ms'] <= 1034
    assert output['preview']['duration_ms'] == 1000


def test_rest_window_and_full_song_length_are_allowed(tmp_path):
    source = chart(tmp_path, '(1000,1);')
    output = create_plan(source, 2000, 2500, {'max_speed_m_s':4})
    assert output['touches'] == []
    assert output['misses'] == []
    assert Settings(chart='bibideba',end_s=168).end_s == 168


def test_zero_air_gap_at_the_same_position_does_not_divide_by_zero(tmp_path):
    source = chart(tmp_path, '(1000,1);(1033,1);')
    result = api.plan_chart(api.load_chart(source),
        planner={'point_window_ms':0,'min_air_frames':0,'fixed_hands':((1000,'left'),(1033,'left'))},
        multi={'second_finger':False,'second_finger_again':False})
    assert len(result.plan.touches) == 2

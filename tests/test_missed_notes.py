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


def test_strict_tap_interval_controls_point_repeats(tmp_path):
    source = chart(tmp_path, '(1000,1);(1100,1);')
    loaded = api.load_chart(source)
    planner = {'point_window_ms': 0, 'fixed_hands': ((1000, 'left'), (1100, 'left'))}
    multi = {'second_finger': False, 'tap_interval_ms': 100}
    assert len(api.plan_chart(loaded, planner=planner, multi=multi).plan.touches) == 2
    with pytest.raises(ValueError, match='no candidate path'):
        api.plan_chart(loaded, planner=planner, multi={**multi, 'tap_interval_ms': 150})


@pytest.mark.parametrize("allow_misses", [True, False])
def test_split_search_can_recover_with_intact_arcs_and_far_timing(tmp_path, monkeypatch, allow_misses):
    source = chart(tmp_path, 'arc(100,600,.5,.5,s,1,1,0,none,false);'
                   '(1000,1);(1017,4);(1033,1);(1050,4);')
    plan_chart = api.plan_chart
    attempts = []

    def simulate_split_pruning(*args, **kwargs):
        items = kwargs['occupation_set'].items
        split_arc = next((item for item in items if item.parent_occupation_id), None)
        attempts.append((split_arc is not None, kwargs['multi'].get('allow_misses', False)))
        if split_arc:
            kwargs['multi'] = {**kwargs['multi'], 'forced_misses': (split_arc.occupation_id,)}
        return plan_chart(*args, **kwargs)

    monkeypatch.setattr(api, 'plan_chart', simulate_split_pruning)
    plan = create_plan(source, 0, 1500, {'arc_path_mode': 'score', 'max_speed_m_s': 4,
                                        'tap_interval_ms': 150, 'allow_misses': allow_misses})
    assert attempts == [(False, False), (True, allow_misses), (False, False)]
    assert plan['misses'] == []
    assert plan['planning']['planned_far_taps'] == 2


def test_far_timing_can_save_a_burst_at_the_strict_tap_interval(tmp_path):
    source = chart(tmp_path, '(1000,1);(1017,4);(1033,1);(1050,4);')
    plan = create_plan(source, 0, 1500, {'tap_interval_ms': 150, 'allow_misses': True})
    assert plan['planning']['skipped_ticks'] == 0
    assert plan['planning']['planned_far_taps'] == 2
    touches = {touch['id']: touch for touch in plan['touches']}
    for note in plan['notes']:
        touch, = (touches[identifier] for identifier in note['touches'])
        assert -100 <= touch['down_ms'] - note['start_ms'] < 100
    for hand in ('left', 'right'):
        a, b = sorted(t['down_frame'] for t in touches.values() if t['hand'] == hand)
        assert b - a >= 9


def test_contested_sky_presses_look_before_native_request_and_offer_dwell(tmp_path):
    from dataclasses import replace
    from arc_hand_order.planning.projection import GameplayProjection
    from arc_hand_order.planning.two_finger import PlannerOptions, build_candidates
    from plan_excerpt import point_windows

    source = chart(tmp_path,
        'arc(1000,1000,.5,.5,s,1,1,0,none,true)[arctap(1000)];'
        'arc(1087,1087,.5,.5,s,1,1,0,none,true)[arctap(1087)];'
        'arc(1174,1174,1,1,s,1,1,0,none,true)[arctap(1174)];')
    loaded = api.load_chart(source)
    from arc_hand_order.planning.occupations import occupations_from_chart
    occupations = point_windows(occupations_from_chart(loaded.chart), 2000, 100)
    projection = GameplayProjection(api.load_device(api.DEFAULT_DEVICE))
    options = PlannerOptions(scoring_arcs=True, full_point_window=True)
    ordinary = build_candidates(occupations, projection, options)
    protected = build_candidates(occupations, projection, replace(options, protect_sky_presses=True))
    assert max(c.down_ms for c in ordinary[0]) > 1000
    assert max(c.down_ms for c in protected[0]) < 1087 - 120
    assert {c.up_frame - c.down_frame for c in protected[0]} == set(range(2, 7))
    assert protected[1:] == ordinary[1:]

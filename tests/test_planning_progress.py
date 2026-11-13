"""Progress is streamed through the real CLI without changing the chosen plan."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order.planning import progress as progress_module
from dashboard import server
from plan_excerpt import create_plan


def chart(tmp_path, body='(1000,1);(1500,4);(2000,2);'):
    path = tmp_path / 'progress.aff'
    path.write_text('AudioOffset:0\n-\ntiming(0,120,4);' + body)
    return path


def test_reporting_does_not_change_contacts_or_search_cost(tmp_path):
    source = chart(tmp_path)
    events = []
    plain = create_plan(source, 0, 2500, {'arc_path_mode': 'score'})
    observed = create_plan(source, 0, 2500, {'arc_path_mode': 'score'}, progress=events.append)
    plain['stats'].pop('elapsed_s')
    observed['stats'].pop('elapsed_s')
    assert observed == plain
    assert events[0]['phase'] == 'loading'
    assert events[-1]['phase'] == 'writing'
    search = [e for e in events if e['phase'] == 'search']
    assert search[0]['completed'] == 0
    assert search[-1]['completed'] == search[-1]['total'] == 3
    assert all(e['attempt'] == 1 for e in events)


def test_retry_is_identified_and_counts_individual_scoring_ticks(tmp_path):
    source = chart(tmp_path, 'hold(1000,2000,1);hold(1000,2000,4);'
                   'arc(1400,1400,.5,.5,s,1,1,0,none,true)[arctap(1400)];(2300,2);')
    events = []
    result = create_plan(source, 0, 3000, {'max_speed_m_s': 4}, progress=events.append)
    assert result['planning']['skipped_ticks'] == 1
    assert {e['attempt'] for e in events} == {1, 2}
    assert events[-1]['attempt'] == 2
    assert [e for e in events if e['phase'] == 'search'][-1]['completed'] == 4


def test_far_retry_reuses_the_perfect_beam_and_retimes_the_lead_in(tmp_path, monkeypatch):
    source = chart(
        tmp_path, '(1000,1);arc(1800,2600,.5,.5,s,1,1,0,none,false);'
        '(3200,1);(4200,2);arc(4600,5400,.5,.5,s,1,1,1,none,false);'
        '(5200,3);(6000,1);(6017,4);(6033,1);(6050,4);(6700,4);',
    )
    events = []
    plan = create_plan(source, 0, 7200, {'arc_path_mode': 'score', 'max_speed_m_s': 4,
                                         'tap_interval_ms': 150, 'search_width': 128}, progress=events.append)
    resumed = next(event for event in events if event.get('resumed_from'))
    assert resumed['attempt'] == 2
    assert resumed['completed'] == resumed['resumed_from'] == 3
    assert resumed['chart_ms'] == 4200
    assert resumed['blocked_ms'] == 6033
    assert plan['misses'] == []
    assert plan['planning']['planned_far_taps'] == 2
    arc_parts = [part for touch in plan['touches'] for part in touch['parts'] if part['kind'] == 'chain']
    assert sum(part.get('parent_occupation_id') is None for part in arc_parts) == 1
    assert any(part.get('parent_occupation_id') for part in arc_parts)

    from arc_hand_order import api
    from arc_hand_order.planning.multi_finger import NoCandidatePath
    plan_chart = api.plan_chart

    def fail_local_retry(*args, **kwargs):
        if kwargs.get('checkpoint') is not None:
            raise NoCandidatePath('no candidate path from this checkpoint', None)
        return plan_chart(*args, **kwargs)

    monkeypatch.setattr(api, 'plan_chart', fail_local_retry)
    events.clear()
    fallback = create_plan(source, 0, 7200, {'arc_path_mode': 'score', 'max_speed_m_s': 4,
                                             'tap_interval_ms': 150, 'search_width': 128}, progress=events.append)
    retry_start = next(event for event in events if event['retry'] == 'split' and event['phase'] == 'search')
    assert retry_start['attempt'] == 3
    assert retry_start['completed'] == 0
    assert fallback['misses'] == []


def test_throttling_keeps_phase_boundaries_but_not_every_state(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(progress_module.time, 'monotonic', lambda: now[0])
    events = []
    report = progress_module.ProgressReporter(events.append)
    for state in range(256):
        report('search', 0, 100, states_completed=state)
    assert len(events) == 1
    now[0] = .5
    report('search', 0, 100, states_completed=256)
    assert len(events) == 2
    report('search', 100, 100)
    report('finalizing')
    assert [e['phase'] for e in events][-2:] == ['search', 'finalizing']


def test_cli_progress_reaches_dashboard_and_clears_for_next_stage(tmp_path, monkeypatch):
    source = chart(tmp_path)
    monkeypatch.setattr(server, 'RUNS', tmp_path / 'runs')
    queue = server.RenderQueue()
    directory = server.RUNS / 'test'
    directory.mkdir()
    queue.active = {'id': 'test', 'status': 'running'}
    snapshots = []
    original_update = queue.update
    def update(**changes):
        original_update(**changes)
        if changes.get('planning_progress'):
            snapshots.append(changes['planning_progress'])
    monkeypatch.setattr(queue, 'update', update)
    queue.command([sys.executable, server.ROOT / 'plan_excerpt.py', source, 0, 2500,
                   directory / 'plan.json', '--progress'], directory, 'Planning contacts', 3)
    assert snapshots[0]['phase'] == 'loading'
    assert snapshots[-1]['phase'] == 'writing'
    assert all(e['updated_at'] > 0 for e in snapshots)
    assert json.loads((directory / 'plan.json').read_text())['touches']
    assert 'PLANNER_PROGRESS ' in (directory / 'render.log').read_text()
    queue.command([sys.executable, '-c', 'pass'], directory, 'Next stage', 20)
    assert queue.active['planning_progress'] is None


def test_orientation_progress_reaches_dashboard_and_clears_for_next_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'RUNS', tmp_path)
    queue = server.RenderQueue()
    directory = tmp_path / 'test'
    directory.mkdir()
    queue.active = {'id': 'test', 'status': 'running'}
    event = {'phase': 'pose_wrists', 'completed': 4, 'total': 10, 'elapsed_s': 3}
    script = f"print('POSE_PROGRESS invalid'); print({('POSE_PROGRESS ' + json.dumps(event))!r})"
    queue.command([sys.executable, '-c', script], directory, 'Posing gloves around fixed touch points', 19)
    pose = json.loads((directory / 'run.json').read_text())['pose_progress']
    assert (pose['phase'], pose['completed'], pose['total'], pose['elapsed_s']) == ('pose_wrists', 4, 10, 3)
    assert pose['updated_at'] > 0
    assert queue.active['planning_progress'] is None
    queue.command([sys.executable, '-c', 'pass'], directory, 'Next stage', 35)
    assert queue.active['pose_progress'] is None

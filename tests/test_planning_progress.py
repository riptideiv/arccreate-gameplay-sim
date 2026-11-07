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
    # Runtime metadata belongs to each invocation, not the chosen contacts.
    for result in (plain, observed):
        result.get('contact_placement', {}).pop('elapsed_s', None)
    assert observed == plain
    assert events[0]['phase'] == 'loading'
    assert events[-1]['phase'] == 'writing'
    assert any(e['phase'] == 'placement' for e in events)
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

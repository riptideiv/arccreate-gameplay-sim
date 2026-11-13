"""The native rollout gate uses observed scores and provenance, not predictions alone."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'benchmarks'))
import replay_check
from replay_check import digest, object_digest, project_for_chart, report, write_json


def fixture(folder, *, hits=10, misses=0, score=10000000):
    folder.mkdir(exist_ok=True)
    project = folder / 'project'
    project.mkdir()
    (project / 'test.aff').write_text('AudioOffset:0\n-\n')
    (project / 'project.arcproj').write_text('lastOpenedChartPath: test.aff\n')
    write_json(folder / 'plan.json', {'arc_tick_targets': [], 'chart': {'path': '/chart/test.aff'},
                                    'device': {}, 'preview': {}})
    write_json(folder / 'replay.json', {'touches': []})
    request = {'plan_sha256': digest(folder / 'plan.json'), 'settings_sha256': object_digest({}),
               'staged_plan_sha256': digest(folder / 'plan.json'),
               'replay_sha256': digest(folder / 'replay.json'),
               'chart_sha256': digest(project / 'test.aff'),
               'project_sha256': digest(project / 'project.arcproj')}
    request['request_sha256'] = object_digest(request)
    write_json(folder / 'request.json', request)
    ledger = folder / 'gameplay.mp4.judgements.json'
    write_json(ledger, {'hits': hits, 'misses': misses, 'score': score})
    write_json(folder / 'receipt.json', {'request_sha256': request['request_sha256'], 'ledger_sha256': digest(ledger)})


def test_replay_gate_rejects_lower_native_score_despite_same_hit_count(tmp_path):
    fixture(tmp_path / 'baseline')
    fixture(tmp_path / 'native', score=9999999)
    result = report(tmp_path)
    assert not result['passed']
    assert result['reasons'] == ['native score decreased']


def test_replay_gate_accepts_observed_improvement(tmp_path):
    fixture(tmp_path / 'baseline', hits=9, misses=1, score=9000000)
    fixture(tmp_path / 'native')
    assert report(tmp_path)['passed']


def test_replay_gate_rejects_ledger_from_another_request(tmp_path):
    fixture(tmp_path / 'baseline')
    fixture(tmp_path / 'native')
    write_json(tmp_path / 'native/request.json', {'request_sha256': 'different-request'})
    with pytest.raises(ValueError, match='matching native replay receipt'):
        report(tmp_path)


@pytest.mark.parametrize('name', ['plan.json', 'replay.json', 'project/test.aff', 'project/project.arcproj'])
def test_replay_gate_rejects_changed_staged_inputs(tmp_path, name):
    fixture(tmp_path / 'baseline')
    fixture(tmp_path / 'native')
    path = tmp_path / 'native' / name
    path.write_text(path.read_text() + '\n')
    with pytest.raises(ValueError, match='Native replay input changed'):
        report(tmp_path)


def test_replay_report_rejects_different_requested_plan_or_settings(tmp_path):
    fixture(tmp_path / 'baseline')
    fixture(tmp_path / 'native')
    new_plan = tmp_path / 'different-final-plan.json'
    write_json(new_plan, {'touches': []})
    with pytest.raises(ValueError, match='requested native plan'):
        report(tmp_path, native=new_plan)
    with pytest.raises(ValueError, match='requested settings'):
        report(tmp_path, settings={'scroll_speed': 5})


def test_resolved_chart_recovers_only_matching_selected_catalog_project(tmp_path, monkeypatch):
    monkeypatch.setattr(replay_check, 'ROOT', tmp_path)
    raw = tmp_path / 'raw'
    raw.mkdir()
    chart = raw / '4.aff'
    chart.write_text('AudioOffset:0\n-\n')
    catalog_folder = tmp_path / 'charts/imported'
    catalog_folder.mkdir(parents=True)
    library = catalog_folder / 'selected'
    library.mkdir()
    (library / '4.aff').symlink_to(chart)
    project = library / 'project.arcproj'
    project.write_text('lastOpenedChartPath: 4.aff\n')
    write_json(catalog_folder / 'catalog.json', {'charts': {'chosen': {
        'directory': str(library.relative_to(tmp_path)), 'aff': '4.aff'}}})
    assert project_for_chart(chart, chart, {'chart': 'chosen'}) == project
    other = raw / '2.aff'
    other.write_text('AudioOffset:0\n-\n')
    with pytest.raises(ValueError, match='different chart'):
        project_for_chart(other, other, {'chart': 'chosen'})

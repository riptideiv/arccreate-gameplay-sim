from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'benchmarks'))
from support import PhaseTimings, compare_plans, executable_contacts_equal, fingerprint, normalized_plan, snapshot_git
from full_search import CASES, performance_assessment, report, verify_trial


@pytest.mark.parametrize('nested', [False, True])
def test_snapshot_copies_helpers_for_independent_and_historical_repositories(tmp_path, nested):
    repository = tmp_path / 'repository'
    repository.mkdir()
    root = repository / 'prototypes/ipad-rhythm' if nested else repository
    root.mkdir(parents=True, exist_ok=True)
    (root / 'plan_excerpt.py').write_text('import contact_placement\n')
    (root / 'contact_placement.py').write_text('VERSION = 1\n')
    subprocess.run(['git', 'init', '-q', str(repository)], check=True)
    subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'commit.gpgsign=false', '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'baseline'], check=True)
    (root / 'contact_placement.py').write_text('VERSION = 2\n')
    destination = tmp_path / 'baseline'
    snapshot_git(repository, 'HEAD', destination)
    assert (destination / 'plan_excerpt.py').is_file()
    assert (destination / 'contact_placement.py').read_text() == 'VERSION = 1\n'
    with pytest.raises(FileExistsError):
        snapshot_git(repository, 'HEAD', destination)


def plan():
    return {'stats': {'elapsed_s': 20, 'states_visited': 32},
            'chart': {'sha256': 'chart'}, 'device': {'profile_id': 'ipad'},
            'planner_options': {}, 'multi_finger_options': {}, 'frame_rate_hz': 60,
            'cost': {'components': {'missed_ticks': 1, 'missed_red_notes': 0}, 'weighted': 10.0},
            'touches': [{'down_frame': 0, 'up_frame': 2, 'hand': 'left', 'finger': 1,
                         'path_mm': [[0, 0], [1, 0]]}]}


def test_quality_comparison_only_ignores_elapsed_time_for_exact_equivalence():
    before, after = plan(), plan()
    before['contact_placement'] = {'elapsed_s': 1, 'moved': 3}
    after['contact_placement'] = {'elapsed_s': .1, 'moved': 3}
    after['stats']['elapsed_s'] = 2
    assert compare_plans(before, after)['exact']
    after['stats']['states_visited'] += 1
    result = compare_plans(before, after)
    assert not result['exact'] and result['quality_metrics_pass']
    assert not result['requires_native_replay_and_constraint_validation']
    assert before['stats']['elapsed_s'] == 20


@pytest.mark.parametrize('mutation', ['misses', 'weighted', 'invalid', 'settings'])
def test_quality_gate_rejects_regressions(mutation):
    before, after = plan(), plan()
    if mutation == 'misses':
        after['cost']['components']['missed_ticks'] = 2
    elif mutation == 'weighted':
        after['cost']['weighted'] = 10.001
    elif mutation == 'invalid':
        after['touches'][0]['path_mm'][0][0] = float('nan')
    else:
        after['planner_options']['speed_max_m_s'] = 100
    assert not compare_plans(before, after)['quality_metrics_pass']


def test_phase_accounting_keeps_retries_and_serialization_separate():
    phases = PhaseTimings(10)
    phases.transition(11, 1, 'loading')
    phases.transition(12, 1, 'search')
    phases.transition(20, 2, 'search')
    phases.transition(30, 2, 'serialization')
    values = {(x['attempt'], x['phase']): x['wall_s'] for x in phases.finish(32)}
    assert values == {(0, 'import'): 1, (1, 'loading'): 1, (1, 'search'): 8,
                      (2, 'search'): 10, (2, 'serialization'): 2}


def test_fingerprint_includes_root_helpers_and_rust_but_not_build_artifacts(tmp_path):
    (tmp_path / 'contact_placement.py').write_text('a')
    source = tmp_path / 'native-search/src'
    source.mkdir(parents=True)
    (source / 'lib.rs').write_text('a')
    first = fingerprint(tmp_path)
    target = tmp_path / 'native-search/target'
    target.mkdir()
    (target / 'scratch.rs').write_text('ignored')
    assert fingerprint(tmp_path) == first
    (source / 'lib.rs').write_text('b')
    assert fingerprint(tmp_path) != first


def write_trial(out, mode, result, backend='native'):
    target = out / f'{mode}-example-1.json'
    target.write_text(json.dumps(result))
    target.with_suffix('.timing.json').write_text(json.dumps({
        'wall_s': 1, 'cpu_s': 1.5, 'peak_rss_mib': 25, 'profiled': False,
        'disk_cache_enabled': False, 'backend_events': [{'search_backend': backend}],
        'native_modules': {'arc_hand_order_native': {'sha256': 'binary'}}}))


def test_report_rejects_fallback_and_worker_count_nondeterminism(tmp_path):
    write_trial(tmp_path, 'baseline', plan(), 'python')
    write_trial(tmp_path, 'native1', plan(), 'python')
    with pytest.raises(AssertionError, match='Fallback'):
        report(tmp_path, ['example'], ['baseline', 'native1'], 1)
    write_trial(tmp_path, 'native1', plan())
    altered = plan()
    altered['touches'][0]['hand'] = 'right'
    write_trial(tmp_path, 'native2', altered)
    with pytest.raises(AssertionError, match='worker counts'):
        report(tmp_path, ['example'], ['baseline', 'native1', 'native2'], 1)


def test_nonidentical_quality_metrics_do_not_approve_rollout(tmp_path):
    write_trial(tmp_path, 'baseline', plan(), 'python')
    altered = plan()
    altered['cost']['weighted'] = 9
    write_trial(tmp_path, 'native1', altered)
    result = report(tmp_path, ['example'], ['baseline', 'native1'], 1)
    assert result['quality_gate_pass'] and not result['default_rollout_approved']
    assert result['comparisons'][1]['quality_metrics_pass']


def test_changed_contacts_or_window_require_replay_but_not_reporting_metadata():
    before, after = plan(), plan()
    before['preview'] = {'chart_start_ms': 0, 'chart_end_ms': 3000}
    after['preview'] = {**before['preview'], 'waveform': [0, 1]}
    after['stats']['states_visited'] += 1
    assert executable_contacts_equal(before, after)
    assert not compare_plans(before, after)['requires_native_replay_and_constraint_validation']
    after['touches'][0]['path_mm'][0][0] = 1
    assert not executable_contacts_equal(before, after)
    assert compare_plans(before, after)['requires_native_replay_and_constraint_validation']
    after['touches'] = deepcopy(before['touches'])
    after['preview']['chart_end_ms'] += 1
    assert compare_plans(before, after)['requires_native_replay_and_constraint_validation']


def test_completed_trial_stops_immediately_on_scoring_regression(tmp_path):
    write_trial(tmp_path, 'baseline', plan(), 'python')
    worse = plan()
    worse['cost']['components']['missed_ticks'] += 1
    write_trial(tmp_path, 'native1', worse)
    with pytest.raises(AssertionError, match='quality regressed'):
        verify_trial(tmp_path, 'example', 'native1', 1)
    assert (tmp_path / 'native1-example-1.comparison.json').exists()


def test_performance_gate_uses_python_and_requires_repeatable_full_chart_gain():
    rows = [{'case': name, 'modes': {
        'python': {'median_s': 10, 'range_s': [9, 11], 'wall_trials_s': [9, 10, 11]},
        'native4': {'median_s': 8, 'range_s': [7, 9], 'wall_trials_s': [7, 8, 9]}}}
        for name in CASES]
    assert performance_assessment(rows, 3)['pass']
    assert not performance_assessment(rows, 2)['pass']
    assert not performance_assessment(rows[:1], 3)['pass']
    rows[0]['modes']['native4']['wall_trials_s'] = [10, 7, 8]
    assert not performance_assessment(rows, 3)['pass']  # Median alone is insufficient.


def test_excerpt_regression_threshold_combines_relative_and_absolute_allowances():
    def assess(python_seconds, native_seconds):
        modes = {key: {'median_s': value, 'range_s': [value, value],
                       'wall_trials_s': [value] * 3}
                 for key, value in [('python', python_seconds), ('native4', native_seconds)]}
        return performance_assessment([{'case': 'bibideba20', 'modes': modes}], 3)['checks'][0]
    assert assess(1, 1.25)['pass']
    assert not assess(1, 1.251)['pass']
    assert assess(10, 11)['pass']
    assert not assess(10, 11.001)['pass']


def test_legacy_harness_snapshots_head_and_runs_explicit_python(tmp_path):
    repository, out = tmp_path / 'repository', tmp_path / 'results'
    repository.mkdir()
    (repository / 'plan_excerpt.py').write_text('CURRENT_HEAD = True\n')
    (repository / 'contact_placement.py').write_text('HELPER = True\n')
    for name in ['charts/imported/bibideba/2.aff',
                 'vendor/arc-hand-order/src/arc_hand_order/devices/ipad_pro_11_m2.json']:
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{}')
    subprocess.run(['git', 'init', '-q', str(repository)], check=True)
    subprocess.run(['git', '-C', str(repository), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repository), '-c', 'commit.gpgsign=false',
                    '-c', 'user.name=Test', '-c', 'user.email=test@example.com',
                    'commit', '-qm', 'current'], check=True)
    driver = Path(__file__).resolve().parents[1] / 'benchmarks/run.py'
    command = [sys.executable, str(driver), '--workspace', str(repository), '--output', str(out)]
    subprocess.run(command + ['--stage', 'snapshot'], check=True, capture_output=True)
    assert (out / 'baseline-source/plan_excerpt.py').read_text() == 'CURRENT_HEAD = True\n'
    assert (out / 'baseline-source/contact_placement.py').is_file()
    # Replace only this test's worker; exercise actual driver argument dispatch.
    (out / 'worker.py').write_text(
        'import json,sys\nfrom pathlib import Path\n'
        'assert sys.argv[sys.argv.index("--search-backend")+1] == "python"\n'
        'target=Path(sys.argv[sys.argv.index("--output")+1])\n'
        'target.with_suffix(".timing.json").write_text(json.dumps({"backend":"python"}))\n')
    subprocess.run(command + ['--stage', 'baseline-first'], check=True, capture_output=True)
    assert len(list(out.glob('baseline-*-1.timing.json'))) == 3

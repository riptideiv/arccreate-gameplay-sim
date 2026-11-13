"""Current full-chart/backend benchmark matrix. Run --help for independent stages."""
import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

from support import compare_plans, fingerprint, normalized_plan, sha256, snapshot_git

HERE = Path(__file__).resolve().parent
MODES = {'baseline': ('python', 0), 'python': ('python', 0),
         **{f'native{n}': ('native', n) for n in (1, 2, 4, 8)}}
CASES = ('arghena', 'undying', 'bibideba20', 'bibideba65', 'bibideba90')


def performance_assessment(rows, trials):
    """Apply the recorded default-four-worker policy against optimized Python."""
    checks = []
    for row in rows:
        modes = row['modes']
        if not {'python', 'native4'} <= modes.keys():
            continue
        python, native = modes['python'], modes['native4']
        full_chart = row['case'] in ('arghena', 'undying')
        limit = max(python['median_s'] * .10, .25)
        if full_chart:
            passed = (native['median_s'] < python['median_s'] and
                      all(n < p for n, p in zip(native['wall_trials_s'], python['wall_trials_s'])))
        else:
            passed = native['median_s'] - python['median_s'] <= limit
        checks.append({'case': row['case'], 'full_chart': full_chart, 'pass': passed,
                       'native4_speedup_vs_python': python['median_s'] / native['median_s'],
                       'native4_minus_python_median_s': native['median_s'] - python['median_s'],
                       'excerpt_material_regression_limit_s': None if full_chart else limit,
                       'trial_ranges_overlap': not (native['range_s'][1] < python['range_s'][0] or
                                                   python['range_s'][1] < native['range_s'][0])})
    complete = trials >= 3 and set(CASES) <= {check['case'] for check in checks}
    return {'complete': complete, 'pass': complete and all(check['pass'] for check in checks),
            'reference': 'optimized Python', 'default_native_workers': 4, 'checks': checks,
            'note': 'Inspect trial ranges and independent correctness/replay evidence before rollout.'}


def prepare(root, out, revision):
    manifest_path = out / 'matrix-manifest.json'
    if manifest_path.exists():
        raise FileExistsError(f'Benchmark already prepared: {manifest_path}')
    baseline = out / 'baseline-source'
    if not baseline.exists():
        snapshot_git(root, revision, baseline)
    elif not (baseline / 'plan_excerpt.py').is_file():
        raise ValueError('Existing baseline-source is not a runnable snapshot')
    for name in ('worker.py', 'support.py'):
        shutil.copy2(HERE / name, out / name)
    catalog = json.loads((root / 'charts/imported/catalog.json').read_text())['charts']
    cases = {}
    for name in CASES:
        if name.startswith('bibideba'):
            settings = json.loads((HERE / 'fixtures/bibideba-settings.json').read_text())
            lo = int(name.removeprefix('bibideba'))
            settings.update(start_s=lo, end_s=lo + 10)
            chart = root / 'charts/imported/bibideba/2.aff'
        else:
            settings = json.loads((HERE / f'fixtures/{name}-full-settings.json').read_text())
            entry = catalog[settings['chart']]
            chart = root / entry['directory'] / entry.get('aff', '2.aff')
        config_path = out / f'{name}-settings.json'
        config_path.write_text(json.dumps(settings, indent=2) + '\n')
        cases[name] = {'chart': str(chart.resolve()), 'chart_sha256': sha256(chart),
                       'settings': str(config_path), 'settings_sha256': sha256(config_path),
                       'window_s': [settings['start_s'], settings['end_s']]}
    device = root / 'vendor/arc-hand-order/src/arc_hand_order/devices/ipad_pro_11_m2.json'
    manifest = {'baseline_revision_requested': revision, 'baseline': fingerprint(baseline),
                'device': str(device), 'device_sha256': sha256(device), 'cases': cases,
                'tools': {name: sha256(out / name) for name in ('worker.py', 'support.py')},
                'host': platform.platform(), 'machine': platform.machine(), 'python': sys.version,
                'dependencies': {name: importlib.metadata.version(name) for name in ('numpy', 'pydantic')},
                'timing_policy': 'fresh processes; serial trials with alternating mode order for remaining runs; disk cache disabled; profiles separate; resumed trials retain original timestamps and explicit reuse provenance'}
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    print('Prepared', manifest_path, flush=True)


def assert_inputs(out, manifest, case):
    assert fingerprint(out / 'baseline-source') == manifest['baseline'], 'Baseline changed'
    assert sha256(manifest['device']) == manifest['device_sha256'], 'Device changed'
    assert sha256(case['chart']) == case['chart_sha256'], 'Chart changed'
    assert sha256(case['settings']) == case['settings_sha256'], 'Settings changed'
    for name, digest in manifest['tools'].items():
        assert sha256(out / name) == digest, 'Benchmark tooling changed'
    for name, version in manifest.get('dependencies', {}).items():
        assert importlib.metadata.version(name) == version, 'Dependency changed'


def verify_trial(out, case_name, mode, number):
    """Stop on the first accuracy regression rather than finish an invalid matrix."""
    target = out / f'{mode}-{case_name}-{number}.json'
    reference_path = out / f'baseline-{case_name}-1.json'
    if not reference_path.exists():
        return
    actual = json.loads(target.read_text())
    comparison = compare_plans(json.loads(reference_path.read_text()), actual)
    target.with_suffix('.comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    if mode in ('baseline', 'python'):
        assert comparison['exact'], f'{mode}/{case_name}: Python plan changed'
    else:
        assert comparison['quality_metrics_pass'], f'{mode}/{case_name}: quality regressed: {comparison["reasons"]}'
        for other_mode in ('native1', 'native2', 'native4', 'native8'):
            for other_number in range(1, number + 1):
                other = out / f'{other_mode}-{case_name}-{other_number}.json'
                if other != target and other.with_suffix('.timing.json').exists():
                    assert normalized_plan(json.loads(other.read_text())) == normalized_plan(actual), f'{case_name}: native results changed across workers/trials'


def trial(root, out, manifest, case_name, mode, number, profiled=False):
    source = out / 'baseline-source' if mode == 'baseline' else root
    case = manifest['cases'][case_name]
    suffix = 'profile' if profiled else str(number)
    target = out / f'{mode}-{case_name}-{suffix}.json'
    assert_inputs(out, manifest, case)
    before = fingerprint(source)
    pin_path = out / f'{mode}-sources.json'
    if pin_path.exists():
        assert json.loads(pin_path.read_text()) == before, f'{mode} source changed across trials; use a new output directory'
    else:
        pin_path.write_text(json.dumps(before, indent=2) + '\n')
    if target.with_suffix('.timing.json').exists():
        if not profiled:
            verify_trial(out, case_name, mode, number)
        print('Already complete', target.name, flush=True)
        return
    backend, workers = MODES[mode]
    command = [sys.executable, str(out / 'worker.py'), '--source', str(source),
               '--chart', case['chart'], '--settings', case['settings'], '--output', str(target),
               '--device', manifest['device'], '--search-backend', backend, '--search-workers', str(workers)]
    if profiled:
        command += ['--profile', str(target.with_suffix('.prof'))]
    print('Starting', target.name, flush=True)
    with target.with_suffix('.log').open('w') as log:
        subprocess.run(command, cwd=source, stdout=log, stderr=subprocess.STDOUT, check=True,
                       env={**os.environ, 'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1'})
    try:
        assert before == fingerprint(source), 'Source changed during trial'
        assert_inputs(out, manifest, case)
    except BaseException:
        target.with_suffix('.timing.json').unlink(missing_ok=True)
        raise
    if not profiled:
        verify_trial(out, case_name, mode, number)
    print('Finished', target.name, flush=True)


def report(out, cases, modes, trials):
    if 'baseline' not in modes:
        raise ValueError('Reports require baseline mode')
    rows, comparisons, chronology, ready = [], [], [], True
    native_binary_identity = None
    for name in cases:
        reference = json.loads((out / f'baseline-{name}-1.json').read_text())
        native_reference = None
        row = {'case': name, 'modes': {}}
        for mode in modes:
            timings, first, binary_identity = [], None, None
            for n in range(1, trials + 1):
                stem = out / f'{mode}-{name}-{n}.json'
                actual = json.loads(stem.read_text())
                timing = json.loads(stem.with_suffix('.timing.json').read_text())
                assert not timing['profiled'] and not timing['disk_cache_enabled']
                normalized = normalized_plan(actual)
                if first is None:
                    first = normalized
                assert normalized == first, f'{mode}/{name}: nondeterministic trials'
                comparison = compare_plans(reference, actual)
                comparison.update(case=name, mode=mode, trial=n)
                if mode.startswith('native'):
                    assert timing['backend_events'] and all(e['search_backend'] == 'native' for e in timing['backend_events']), 'Fallback is not a native trial'
                    identity = timing['native_modules']
                    assert identity, 'Native trial has no binary fingerprint'
                    if native_binary_identity is None:
                        native_binary_identity = identity
                    assert identity == native_binary_identity, 'Native extension changed across cases or worker counts'
                    if binary_identity is None:
                        binary_identity = identity
                    assert identity == binary_identity, 'Native extension changed between trials'
                    if native_reference is None:
                        native_reference = normalized
                    assert normalized == native_reference, f'{name}: native worker counts changed the result'
                    if not comparison['quality_metrics_pass']:
                        ready = False
                    if comparison['requires_native_replay_and_constraint_validation']:
                        ready = False  # Native replay + constraints require independent evidence.
                elif not comparison['exact']:
                    raise AssertionError(f'{mode}/{name}: Python plan changed')
                comparisons.append(comparison)
                timings.append(timing)
                if 'finished_unix_s' in timing:
                    chronology.append({'case': name, 'mode': mode, 'trial': n,
                                       'started_unix_s': timing['finished_unix_s'] - timing['wall_s'],
                                       'finished_unix_s': timing['finished_unix_s']})
            phase_keys = sorted({(p['attempt'], p['phase']) for t in timings for p in t.get('phases', [])})
            row['modes'][mode] = {'median_s': statistics.median(t['wall_s'] for t in timings),
                'range_s': [min(t['wall_s'] for t in timings), max(t['wall_s'] for t in timings)],
                'wall_trials_s': [t['wall_s'] for t in timings],
                'cpu_median_s': statistics.median(t['cpu_s'] for t in timings),
                'cpu_trials_s': [t['cpu_s'] for t in timings],
                'peak_rss_mib': max(t['peak_rss_mib'] for t in timings),
                'peak_rss_trials_mib': [t['peak_rss_mib'] for t in timings],
                'phases_median_s': [{'attempt': a, 'phase': p, 'wall_s': statistics.median(
                    sum(v['wall_s'] for v in t.get('phases', []) if (v['attempt'], v['phase']) == (a, p))
                    for t in timings)} for a, p in phase_keys], 'trials': trials}
        baseline_seconds = row['modes']['baseline']['median_s']
        for values in row['modes'].values():
            values['speedup_vs_baseline'] = baseline_seconds / values['median_s']
            if 'python' in row['modes']:
                values['speedup_vs_python'] = row['modes']['python']['median_s'] / values['median_s']
        rows.append(row)
    result = {'rows': rows, 'comparisons': comparisons, 'all_results_exact': all(c['exact'] for c in comparisons),
              'quality_gate_pass': ready,
              'performance_gate': performance_assessment(rows, trials),
              'execution_chronology': sorted(chronology, key=lambda item: item['started_unix_s']),
              'default_rollout_approved': False,
              'rollout_note': 'Quality metrics alone do not approve rollout. Inspect speed, regressions and memory; changed plans require native replay and constraint validation.'}
    (out / 'matrix-report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if any(not comparison['quality_metrics_pass'] for comparison in comparisons):
        raise AssertionError('Quality metrics regressed; see matrix-report.json')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=HERE.parent)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=('prepare', 'run', 'profiles', 'report'), default='run')
    parser.add_argument('--baseline-revision', default='HEAD')
    parser.add_argument('--cases', default=','.join(CASES))
    parser.add_argument('--modes', default=','.join(MODES))
    parser.add_argument('--trials', type=int, default=3)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error('--trials must be positive')
    root, out = args.workspace.resolve(), args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cases, modes = args.cases.split(','), args.modes.split(',')
    if not set(cases) <= set(CASES) or not set(modes) <= set(MODES):
        parser.error(f'Cases: {CASES}; modes: {tuple(MODES)}')
    if args.stage == 'prepare':
        prepare(root, out, args.baseline_revision)
        return
    manifest = json.loads((out / 'matrix-manifest.json').read_text())
    if args.stage == 'profiles':
        for case in cases:
            for mode in modes:
                trial(root, out, manifest, case, mode, 0, profiled=True)
    elif args.stage == 'run':
        for n in range(1, args.trials + 1):
            for case in cases:
                # Rotate the order on alternating repetitions to reduce order bias.
                for mode in modes if n % 2 else modes[::-1]:
                    trial(root, out, manifest, case, mode, n)
        if 'baseline' in modes:
            report(out, cases, modes, args.trials)
        else:
            print('Selected trials complete; run --stage report with baseline and the comparison modes.')
    else:
        report(out, cases, modes, args.trials)


if __name__ == '__main__':
    main()

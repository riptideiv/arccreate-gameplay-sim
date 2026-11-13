"""One fresh-process trial; imports and JSON serialization are included in timing."""
import argparse
import cProfile
import hashlib
import inspect
import json
import os
import resource
import sys
import time
from functools import partial
from pathlib import Path

from support import PhaseTimings


def main():
    parser = argparse.ArgumentParser()
    for name in ('source', 'chart', 'settings', 'output', 'device'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--cache-dir', type=Path)
    parser.add_argument('--search-backend', choices=('python', 'native'))
    parser.add_argument('--search-workers', type=int, default=0)
    args = parser.parse_args()
    source = args.source.resolve()
    sys.path[:0] = [str(source), str(source / 'vendor/arc-hand-order/src')]
    started, cpu_started = time.perf_counter(), time.process_time()
    phases = PhaseTimings(started)
    from plan_excerpt import create_plan
    import arc_hand_order
    from arc_hand_order import api
    api.plan_chart = partial(api.plan_chart, device_path=args.device)
    assert Path(arc_hand_order.__file__).resolve().is_relative_to(source), arc_hand_order.__file__
    assert Path(inspect.getfile(create_plan)).resolve().is_relative_to(source)
    config = json.loads(args.settings.read_text())
    backend_events = []
    def progress(event):
        if 'search_backend' in event:
            backend_events.append(dict(event))
        # Backend diagnostics are not an execution phase and must not steal search time.
        if event.get('phase') != 'backend':
            phases.transition(time.perf_counter(), event.get('attempt', 1), event['phase'])
        print('PLANNER_PROGRESS ' + json.dumps(event), flush=True)
    def run():
        kwargs = {'progress': progress}
        if args.cache_dir:
            kwargs['cache_dir'] = args.cache_dir
        if args.search_backend:
            if 'search_backend' in inspect.signature(create_plan).parameters:
                kwargs.update(search_backend=args.search_backend, search_workers=args.search_workers)
            elif args.search_backend != 'python':
                raise RuntimeError('This snapshot does not implement native execution')
        result = create_plan(args.chart, round(config['start_s'] * 1000), round(config['end_s'] * 1000), config, **kwargs)
        if args.search_backend == 'native':
            if not backend_events or any(e['search_backend'] != 'native' for e in backend_events):
                raise RuntimeError('Native trial used fallback or emitted no backend evidence')
            if args.search_workers and any(e.get('search_workers') != args.search_workers for e in backend_events):
                raise RuntimeError('Native trial did not use the requested worker count')
        phases.transition(time.perf_counter(), phases.key[0], 'serialization')
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if args.profile:
        profiler = cProfile.Profile()
        try:
            profiler.runcall(run)
        finally:
            profiler.dump_stats(str(args.profile))
    else:
        run()
    finished = time.perf_counter()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 2 if sys.platform == 'darwin' else 1024)
    native_modules = {}
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if path and ('rhythm' in name or 'native' in name) and Path(path).suffix in ('.so', '.pyd', '.dylib'):
            native_modules[name] = {'path': path, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    if args.search_backend == 'native' and not native_modules:
        raise RuntimeError('Native trial loaded no fingerprintable native extension')
    metrics = {'wall_s': finished - started, 'cpu_s': time.process_time() - cpu_started,
               'peak_rss_mib': rss, 'finished_unix_s': time.time(), 'pid': os.getpid(), 'python': sys.version,
               'source': str(source), 'profiled': bool(args.profile), 'disk_cache_enabled': bool(args.cache_dir),
               'requested_backend': args.search_backend, 'requested_workers': args.search_workers,
               'backend_events': backend_events, 'native_modules': native_modules, 'phases': phases.finish(finished)}
    args.output.with_suffix('.timing.json').write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics), flush=True)


if __name__ == '__main__':
    main()

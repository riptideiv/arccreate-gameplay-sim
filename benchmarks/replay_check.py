"""用真实 ArcCreate 逐帧回放比较基线和原生搜索，不重新规划或运行 Blender。

prepare 仅准备私有谱面工程和输入；run 串行导出两份原生画面并检查判分。
现有 ArcCreate 适配器没有经过验证的无渲染判分入口，因此保留其 60 Hz
相机／输入更新顺序，不用 -nographics 或新的模拟判分替代它。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard.red_notes import render_chart
from scoring_report import compare_ticks


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def object_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def project_for_chart(chart, named_path, settings):
    project = Path(named_path).parent / 'project.arcproj'
    if project.is_file():
        return project
    # Benchmark plans deliberately store resolved AFF paths. Resolve only the
    # explicitly selected catalog entry, then prove it names the same chart.
    catalog = json.loads((ROOT / 'charts/imported/catalog.json').read_text())['charts']
    entry = catalog.get(settings.get('chart'))
    if entry is not None:
        folder = ROOT / entry['directory']
        catalog_chart = folder / entry.get('aff', '2.aff')
        if catalog_chart.resolve() != chart:
            raise ValueError('Selected catalog project belongs to a different chart')
        project = folder / 'project.arcproj'
        if project.is_file():
            return project
    raise FileNotFoundError(f'ArcCreate project missing for chart: {chart}')


def prepare_case(plan_path, folder, settings):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text())
    chart = Path(plan['chart']['path']).resolve()
    if digest(chart) != plan['chart']['sha256']:
        raise ValueError(f'Chart changed since planning: {chart}')
    project_source = project_for_chart(chart, plan['chart']['path'], settings)
    folder.mkdir(parents=True, exist_ok=True)
    private = folder / 'project'
    private.mkdir(exist_ok=True)
    for asset in project_source.parent.iterdir():
        if asset.name in (chart.name, 'project.arcproj', '.rendering'):
            continue
        target = private / asset.name
        if target.is_symlink() and target.resolve() != asset.resolve():
            target.unlink()
        if not target.exists() and not target.is_symlink():
            target.symlink_to(os.path.relpath(asset.resolve(), private))
    converted, _ = render_chart(chart.read_text(encoding='utf-8-sig'), plan['planning']['red_notes_enabled'])
    (private / chart.name).write_text(converted)
    project_text = re.sub(r'(?m)^\s*(?:lastOpenedChartPath|lastWorkingTiming):.*\n', '', project_source.read_text())
    project = private / 'project.arcproj'
    project.write_text('lastOpenedChartPath: ' + json.dumps(chart.name) + '\n' + project_text)
    replay = {
        'width_mm': plan['device']['screen_mm'][0], 'height_mm': plan['device']['screen_mm'][1],
        'scroll_speed': settings.get('scroll_speed', 3),
        'chart_start_ms': plan['preview']['chart_start_ms'],
        'audio_offset_ms': plan['chart']['audio_offset_ms'],
        'source_audio_offset_ms': plan['chart'].get('source_audio_offset_ms', plan['chart']['audio_offset_ms']),
        'touches': [{'id': t['id'], 'down_frame': t['down_frame'], 'up_frame': t['up_frame'],
                     'points': [{'x': x, 'y': y} for x, y in t['path_mm']]} for t in plan['touches']],
    }
    write_json(folder / 'plan.json', plan)
    write_json(folder / 'replay.json', replay)
    start = plan['preview']['audio_start_ms']
    # Include the judgement expiry tail even when the original audio ends.
    # Export.cs pads its in-memory AudioClip without changing the source asset.
    end = start + plan['preview']['duration_ms'] + 2000
    media = folder / 'gameplay.mp4'
    command = [str(ROOT / 'arc-create-export/export.sh'), str(project), str(media),
               str(start), str(end), str(folder / 'replay.json')]
    source_names = ('Export.cs', 'Replay.cs', 'RedNotes.cs', 'Patch.cs', 'export.sh')
    app = Path(os.environ.get('ARCCREATE_EXPORT_APP', str(ROOT / '.tools/ArcCreate/ArcCreate-media.app'))).resolve()
    managed = app / 'Contents/Resources/Data/Managed'
    request = {'plan_source': str(plan_path), 'plan_sha256': digest(plan_path),
               'staged_plan_sha256': digest(folder / 'plan.json'), 'settings_sha256': object_digest(settings),
               'replay_sha256': digest(folder / 'replay.json'), 'chart_sha256': digest(private / chart.name),
               'project_sha256': digest(project), 'window_audio_ms': [start, end], 'command': command,
               'runtime_app': str(app),
               'adapter_sources': {name: digest(ROOT / 'arc-create-export' / name) for name in source_names},
               'adapter_binary': {name: digest(managed / name) for name in
                                  ('ArcCreateOfflineExport.dll', 'ArcCreate.Gameplay.dll', 'ArcCreate.Compose.dll')}}
    request['request_sha256'] = object_digest(request)
    write_json(folder / 'request.json', request)
    return request


def verify_staged_request(folder, request):
    payload = {key: value for key, value in request.items() if key != 'request_sha256'}
    if object_digest(payload) != request['request_sha256']:
        raise ValueError('Native replay request metadata changed')
    plan = json.loads((folder / 'plan.json').read_text())
    files = {'staged_plan_sha256': folder / 'plan.json', 'replay_sha256': folder / 'replay.json',
             'project_sha256': folder / 'project/project.arcproj',
             'chart_sha256': folder / 'project' / Path(plan['chart']['path']).name}
    for field, path in files.items():
        if digest(path) != request[field]:
            raise ValueError(f'Native replay input changed: {path.name}')
    return plan


def run_case(folder):
    request = json.loads((folder / 'request.json').read_text())
    verify_staged_request(folder, request)
    receipt_path = folder / 'receipt.json'
    ledger_path = folder / 'gameplay.mp4.judgements.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    reusable = (receipt.get('request_sha256') == request['request_sha256'] and ledger_path.is_file()
                and receipt.get('ledger_sha256') == digest(ledger_path))
    if not reusable:
        for name, expected in request['adapter_sources'].items():
            if digest(ROOT / 'arc-create-export' / name) != expected:
                raise ValueError(f'Native replay adapter source changed: {name}')
        managed = Path(request['runtime_app']) / 'Contents/Resources/Data/Managed'
        for name, expected in request['adapter_binary'].items():
            if digest(managed / name) != expected:
                raise ValueError(f'Native replay adapter binary changed: {name}')
        with (folder / 'native.log').open('w') as log:
            subprocess.run(request['command'], stdout=log, stderr=subprocess.STDOUT, check=True, cwd=ROOT,
                           env={**os.environ, 'ARCCREATE_EXPORT_APP': request['runtime_app']})
        write_json(receipt_path, {'request_sha256': request['request_sha256'],
                                 'ledger_sha256': digest(ledger_path)})


def report(output, *, baseline=None, native=None, settings=None):
    cases = {}
    requests = {}
    plans = {}
    for kind in ('baseline', 'native'):
        folder = output / kind
        request = json.loads((folder / 'request.json').read_text())
        receipt = json.loads((folder / 'receipt.json').read_text())
        ledger_path = folder / 'gameplay.mp4.judgements.json'
        if (receipt.get('request_sha256') != request['request_sha256']
                or receipt.get('ledger_sha256') != digest(ledger_path)):
            raise ValueError(f'No matching native replay receipt for {kind}')
        plan = verify_staged_request(folder, request)
        supplied = baseline if kind == 'baseline' else native
        if supplied is not None and digest(supplied) != request['plan_sha256']:
            raise ValueError(f'Replay does not match the requested {kind} plan')
        if settings is not None and object_digest(settings) != request['settings_sha256']:
            raise ValueError('Replay does not match the requested settings')
        requests[kind], plans[kind] = request, plan
        ledger = json.loads(ledger_path.read_text())
        ticks = compare_ticks(plan, ledger)
        write_json(folder / 'arc-tick-check.json', ticks)
        cases[kind] = {'hits': ledger['hits'], 'misses': ledger['misses'], 'score': ledger['score'],
                       'red_hits': ledger.get('red_hits', 0), 'red_misses': ledger.get('red_misses', 0),
                       'predicted_arc_hits': ticks['predicted_hits'], 'confirmed_arc_hits': ticks['confirmed_hits'],
                       'unconfirmed_predictions': ticks['unconfirmed_predictions'],
                       'unjudged_arc_targets': [t['id'] for t in ticks['ticks'] if t['native_result'] == 'NotJudged'],
                       'request_sha256': json.loads((folder / 'request.json').read_text())['request_sha256']}
    for field in ('chart', 'device', 'preview'):
        if plans['baseline'][field] != plans['native'][field]:
            raise ValueError(f'Cannot compare different replay {field}')
    if requests['baseline']['chart_sha256'] != requests['native']['chart_sha256']:
        raise ValueError('Native replay used different rendered charts')
    before, after = cases['baseline'], cases['native']
    reasons = []
    for field in ('misses', 'red_misses'):
        if after[field] > before[field]:
            reasons.append(f'native {field} increased')
    for field in ('hits', 'red_hits', 'score'):
        if after[field] < before[field]:
            reasons.append(f'native {field} decreased')
    if before['hits'] + before['misses'] != after['hits'] + after['misses']:
        reasons.append('different native scoring-note totals')
    if before['red_hits'] + before['red_misses'] != after['red_hits'] + after['red_misses']:
        reasons.append('different native red-note totals')
    if after['unconfirmed_predictions']:
        reasons.append('native replay did not confirm every claimed arc tick')
    if before['unjudged_arc_targets'] or after['unjudged_arc_targets']:
        reasons.append('native replay did not observe every requested arc tick')
    result = {'passed': not reasons, 'reasons': reasons, 'engine': 'ArcCreate 1.2.68 real touch replay',
              'cases': cases}
    write_json(output / 'replay-report.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', choices=('prepare', 'run', 'report'), default='prepare')
    args = parser.parse_args()
    output = args.output.resolve()
    settings = json.loads(args.settings.read_text())
    if args.stage != 'report':
        requests = {kind: prepare_case(path, output / kind, settings)
                    for kind, path in (('baseline', args.baseline), ('native', args.native))}
        baseline = json.loads((output / 'baseline/plan.json').read_text())
        native = json.loads((output / 'native/plan.json').read_text())
        for field in ('chart', 'device', 'preview'):
            if baseline[field] != native[field]:
                raise ValueError(f'Cannot compare different replay {field}')
        write_json(output / 'replay-manifest.json', {'requests': requests, 'graphics_required': True,
                   'notes': 'Use the existing 60 Hz native exporter; no Blender or replanning.'})
        if args.stage == 'prepare':
            print(json.dumps({'prepared': str(output), 'commands': [r['command'] for r in requests.values()]}, indent=2))
            return
        for kind in ('baseline', 'native'):
            print(f'ArcCreate replay: {kind}', flush=True)
            run_case(output / kind)
    result = report(output, baseline=args.baseline, native=args.native, settings=settings)
    print(json.dumps(result, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

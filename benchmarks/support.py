"""Shared, side-effect-free benchmark checks."""
import copy
import hashlib
import io
import json
import math
import subprocess
import tarfile
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(source):
    source = Path(source)
    files = set(source.glob('*.py'))
    files.update((source / 'vendor/arc-hand-order/src').rglob('*.py'))
    for metadata in (source / 'pyproject.toml', source / 'vendor/arc-hand-order/pyproject.toml'):
        if metadata.exists():
            files.add(metadata)
    native = source / 'native-search'
    if native.exists():
        files.update(native.glob('*.toml'))
        files.update(native.glob('Cargo.lock'))
        files.update((native / 'src').rglob('*.rs'))
        files.update(native.glob('*.py'))
    hashes = {str(f.relative_to(source)): sha256(f) for f in sorted(files)}
    return {'files': hashes, 'sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def snapshot_git(workspace, revision, destination):
    """Archive the entire simulator, including root helper modules, from either Git layout."""
    workspace, destination = Path(workspace), Path(destination)
    if destination.exists():
        raise FileExistsError(f'Baseline already exists: {destination}')
    root = subprocess.check_output(['git', '-C', str(workspace), 'rev-parse', '--show-toplevel'], text=True).strip()
    names = subprocess.check_output(['git', '-C', root, 'ls-tree', '-r', '--name-only', revision], text=True).splitlines()
    if 'plan_excerpt.py' in names:
        tree = revision
    elif 'prototypes/ipad-rhythm/plan_excerpt.py' in names:
        tree = revision + ':prototypes/ipad-rhythm'
    else:
        raise ValueError(f'{revision} does not contain the simulator')
    payload = subprocess.check_output(['git', '-C', root, 'archive', tree])
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        archive.extractall(destination, filter='data')


def normalized_plan(plan):
    result = copy.deepcopy(plan)
    result['stats'].pop('elapsed_s', None)
    if 'contact_placement' in result:
        result['contact_placement'].pop('elapsed_s', None)
    return result


def executable_contacts_equal(reference, candidate):
    """Native replay consumes these inputs; cost/report rounding is not a new replay."""
    fields = ('chart', 'device', 'frame_rate_hz', 'touches')
    return (all(reference.get(name) == candidate.get(name) for name in fields)
            and all(reference.get('preview', {}).get(name) == candidate.get('preview', {}).get(name)
                    for name in ('chart_start_ms', 'chart_end_ms')))


def compare_plans(reference, candidate):
    """A quality comparison is a necessary gate, never proof of native judgement validity."""
    exact = normalized_plan(reference) == normalized_plan(candidate)
    reasons = []
    for name in ('missed_ticks', 'missed_red_notes', 'far_taps', 'non_max_taps'):
        before = reference['cost']['components'].get(name, 0)
        after = candidate['cost']['components'].get(name, 0)
        if not math.isfinite(after) or after > before:
            reasons.append(f'{name} increased')
    before, after = reference['cost']['weighted'], candidate['cost']['weighted']
    if not math.isfinite(after) or after > before + max(1e-9, abs(before) * 1e-12):
        reasons.append('weighted cost increased')
    for name in ('chart', 'device', 'planner_options', 'multi_finger_options', 'frame_rate_hz'):
        if candidate.get(name) != reference.get(name):
            reasons.append(f'{name} changed')
    for touch in candidate['touches']:
        path = touch['path_mm']
        if (touch['up_frame'] <= touch['down_frame'] or len(path) != touch['up_frame'] - touch['down_frame']
                or touch['hand'] not in ('left', 'right') or touch['finger'] != 1
                or any(len(xy) != 2 or any(not math.isfinite(v) for v in xy) for xy in path)):
            reasons.append('invalid contact structure')
            break
    executable_equal = executable_contacts_equal(reference, candidate)
    return {'exact': exact, 'executable_contacts_equal': executable_equal,
            'quality_metrics_pass': not reasons, 'reasons': reasons,
            'requires_native_replay_and_constraint_validation': not executable_equal}


class PhaseTimings:
    """Wall-time intervals between progress transitions; attempts remain distinct."""
    def __init__(self, started):
        self.last = started
        self.key = (0, 'import')
        self.values = {}

    def transition(self, now, attempt, phase):
        self.values[self.key] = self.values.get(self.key, 0.0) + now - self.last
        self.last, self.key = now, (attempt, phase)

    def finish(self, now):
        self.transition(now, *self.key)
        return [{'attempt': attempt, 'phase': phase, 'wall_s': seconds}
                for (attempt, phase), seconds in self.values.items()]

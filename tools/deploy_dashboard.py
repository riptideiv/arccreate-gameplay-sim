"""Test a simulator commit and deploy it to an isolated local launchd workbench."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, cwd=kwargs.pop("cwd", ROOT), check=True, **kwargs)


def output(*args):
    return run(*args, stdout=subprocess.PIPE).stdout


def fetch(base, path):
    return output('curl', '-fsS', '--max-time', '5', base.rstrip('/') + path)


def copy_files(source, destination, files):
    for relative in files:
        original = source / relative
        target = destination / relative
        if original.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
        elif target.is_file():
            target.unlink()


def check_health(url, stage, expected):
    bootstrap = json.loads(fetch(url, '/api/bootstrap'))
    if (not bootstrap['charts'] or bootstrap['defaults'] != expected['defaults']
            or bootstrap['defaults_revision'] != expected['defaults_revision']):
        raise RuntimeError('The dashboard returned unexpected defaults or an empty chart library.')
    for route, name in (('/', 'index.html'), ('/app.js', 'app.js')):
        if fetch(url, route) != (stage / 'dashboard' / name).read_bytes():
            raise RuntimeError(f'The dashboard returned a different file for {route}.')
    return bootstrap


def wait_for_health(url, stage, expected):
    for attempt in range(10):
        try:
            return check_health(url, stage, expected)
        except (subprocess.CalledProcessError, ValueError, RuntimeError, KeyError):
            if attempt == 9:
                raise
            time.sleep(1)


def deploy(config, *, native_wheel=None, checks="full"):
    plist = Path(config['launchd_plist']).expanduser().resolve()
    with plist.open('rb') as file:
        agent = plistlib.load(file)
    live = Path(agent['WorkingDirectory']).resolve()
    if (live == ROOT or (live / '.git').exists() or
            str(live / 'dashboard/server.py') not in agent['ProgramArguments']):
        raise ValueError('The LaunchAgent must point to an isolated dashboard release directory.')
    domain = f'gui/{os.getuid()}'
    job = f"{domain}/{agent['Label']}"
    loopback = config['loopback_url']
    tailnet = config['dashboard_url']
    if urlsplit(loopback).hostname != '127.0.0.1' or urlsplit(tailnet).scheme != 'https':
        raise ValueError('Configure a loopback backend and an HTTPS dashboard URL.')

    if output('git', 'status', '--porcelain', '--untracked-files=no'):
        raise RuntimeError('Commit tracked changes before testing and deploying.')
    revision = output('git', 'rev-parse', 'HEAD').decode().strip()
    python = ROOT / '.venv/bin/python'
    test_paths = ['vendor/arc-hand-order/tests', 'tests'] if checks == 'full' else [
        'tests/test_arm_crossings.py', 'tests/test_near_side_travel.py',
        'tests/test_search_memoization.py', 'tests/test_native_search.py',
        'tests/test_native_cancellation.py', 'tests/test_search_execution.py',
        'tests/test_plan_cache.py',
    ]
    run(python, '-m', 'pytest', '-q', *test_paths)
    run('node', '--test', 'tests/test_i18n.mjs', 'tests/test_preview_recorder.mjs')
    if (output('git', 'rev-parse', 'HEAD').decode().strip() != revision
            or output('git', 'status', '--porcelain', '--untracked-files=no')):
        raise RuntimeError('The source changed during testing.')
    expected = json.loads(output(
        python, '-c',
        'import json; from dashboard.server import DEFAULTS_REVISION, Settings; '
        'print(json.dumps(dict(defaults=Settings().model_dump(), defaults_revision=DEFAULTS_REVISION)))',
    ))

    state_dir = live / 'output'
    manifest = state_dir / 'dashboard-deployment.json'
    lock = state_dir / '.dashboard-deployment-lock'
    lock.mkdir()
    try:
        deploy_release(revision, live, manifest, job, domain, plist, loopback, tailnet, expected, native_wheel=native_wheel)
    finally:
        lock.rmdir()


def deploy_release(revision, live, manifest, job, domain, plist, loopback, tailnet, expected, *, native_wheel=None):
    files = set(output('git', 'ls-tree', '-rz', '--name-only', revision).decode().rstrip('\0').split('\0'))
    previous = set(json.loads(manifest.read_text())['files']) if manifest.is_file() else set()
    affected = sorted(files | previous)
    if any(not (live / name).resolve().is_relative_to(live) for name in affected):
        raise RuntimeError('A deployed source path resolves outside the isolated release directory.')
    with tempfile.TemporaryDirectory(dir=live.parent, prefix='.dashboard-release-') as temporary:
        temporary = Path(temporary)
        stage = temporary / 'release'
        backup = temporary / 'backup'
        stage.mkdir()
        backup.mkdir()
        archive = temporary / 'release.tar'
        run('git', 'archive', '--format=tar', '-o', archive, revision)
        run('tar', '-xf', archive, '-C', stage)
        copy_files(live, backup, affected)
        python = live / '.venv/bin/python'
        site = Path(output(python, '-c', 'import sysconfig; print(sysconfig.get_path("platlib"))').decode().strip())
        extension_backup = temporary / 'native-extension'
        if native_wheel is not None:
            native_wheel = Path(native_wheel).resolve(strict=True)
            extension_backup.mkdir()
            for entry in site.glob('arc_hand_order_native*'):
                if entry.is_dir():
                    shutil.copytree(entry, extension_backup / entry.name)
                else:
                    shutil.copy2(entry, extension_backup / entry.name)
        active = json.loads(fetch(loopback, '/api/state')).get('active')
        if active and active['status'] not in ('complete', 'failed', 'cancelled'):
            raise RuntimeError('A dashboard job is active. Deploy when it completes.')
        print(f'Deploying {revision[:12]}', flush=True)
        run('launchctl', 'bootout', job)
        try:
            copy_files(stage, live, affected)
            if native_wheel is not None:
                run('uv', 'pip', 'install', '--python', python, '--no-deps', '--reinstall', native_wheel)
                run(python, '-c', 'from arc_hand_order.planning.native_search import _load_extension; '
                    'print(_load_extension().implementation_id())', cwd=live)
            run('launchctl', 'bootstrap', domain, plist)
            local = wait_for_health(loopback, stage, expected)
        except (Exception, KeyboardInterrupt):
            print('Restoring the previous dashboard files.', flush=True)
            subprocess.run(['launchctl', 'bootout', job], check=False)
            copy_files(backup, live, affected)
            if native_wheel is not None:
                for entry in site.glob('arc_hand_order_native*'):
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                for entry in extension_backup.iterdir():
                    if entry.is_dir():
                        shutil.copytree(entry, site / entry.name)
                    else:
                        shutil.copy2(entry, site / entry.name)
            run('launchctl', 'bootstrap', domain, plist)
            raise

        manifest.write_text(json.dumps({'revision': revision, 'files': sorted(files)}, indent=2) + '\n')
        remote = wait_for_health(tailnet, stage, expected)
        if len(local['charts']) != len(remote['charts']):
            raise RuntimeError('The Tailscale dashboard returned a different chart library.')
        print(f'Deployed {revision[:12]} and verified loopback and Tailscale ({len(remote["charts"])} charts).')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='Path to the ignored local deployment JSON')
    parser.add_argument('--native-wheel', type=Path, help='Install a built native wheel with source rollback support')
    parser.add_argument('--checks', choices=('full', 'focused'), default='full', help='Use focused planner/motion checks for the native port')
    args = parser.parse_args()
    deploy(json.loads(args.config.read_text()), native_wheel=args.native_wheel, checks=args.checks)

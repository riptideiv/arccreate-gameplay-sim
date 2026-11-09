import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard import server


def take(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    monkeypatch.setattr(server, 'RUNS', tmp_path / 'output/runs')
    queue = server.RenderQueue()
    directory = server.RUNS / '20261106-000000-abcdef'
    directory.mkdir()
    queue.active = {'id':directory.name, 'status':'complete', 'live':{'scene':'scene.glb'},
                    'video':'gameplay.mp4', 'metrics':{'frame_count':30}}
    return queue, directory


def test_failed_optional_export_preserves_the_verified_live_preview(tmp_path, monkeypatch):
    queue, directory = take(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise ValueError('encoder failed')
    monkeypatch.setattr(queue, 'command', fail)
    queue.work_export(directory)
    saved = json.loads((directory / 'run.json').read_text())
    assert saved['status'] == 'complete'
    assert saved['live'] == {'scene':'scene.glb'}
    assert saved['video'] == 'gameplay.mp4'
    assert saved['export_error'] == 'encoder failed'


def test_optional_export_adds_a_download_without_replacing_the_live_clock(tmp_path, monkeypatch):
    queue, directory = take(tmp_path, monkeypatch)
    queue.active['export_error'] = 'previous failure'
    monkeypatch.setattr(queue, 'command', lambda *args, **kwargs: None)
    queue.work_export(directory)
    assert queue.active['status'] == 'complete'
    assert queue.active['video'] == 'gameplay.mp4'
    assert queue.active['export_video'].endswith('/preview.mp4')
    assert queue.active['export_error'] is None


def test_export_uses_the_current_camera_without_replacing_verified_motion(tmp_path, monkeypatch):
    queue, directory = take(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(queue, 'command', lambda arguments, *args: calls.append(arguments))
    config = server.Settings(camera_distance_m=.31).model_dump()
    queue.work_export(directory, config)
    assert len(calls) == 2
    assert calls[1][:2] == ['env', f'BLENDER_BIN={calls[0][0]}']
    saved, = directory.glob('export-settings-*.json')
    assert json.loads(saved.read_text())['camera_distance_m'] == .31
    assert queue.active['video'] == 'gameplay.mp4'
    assert 'preview-' in queue.active['export_video']


def test_cancelled_export_keeps_the_live_take_and_previous_download(tmp_path, monkeypatch):
    queue, directory = take(tmp_path, monkeypatch)
    queue.active['export_video'] = '/previous.mp4'
    def cancel(*args):
        raise InterruptedError('Render cancelled.')
    monkeypatch.setattr(queue,'command',cancel)
    queue.work_export(directory)
    assert queue.active['status'] == 'complete'
    assert queue.active['export_error'] is None
    assert queue.active['export_video'] == '/previous.mp4'
    assert queue.active['live'] == {'scene':'scene.glb'}


def test_existing_export_of_the_same_view_does_not_render_frames_again(tmp_path, monkeypatch):
    queue, directory = take(tmp_path, monkeypatch)
    (directory/'preview.mp4').write_bytes(b'x'*1001)
    def unexpected(*args):
        raise AssertionError('Cached video should not render again')
    monkeypatch.setattr(queue,'command',unexpected)
    queue.work_export(directory)
    assert queue.active['stage'] == 'MP4 ready'
    assert queue.active['export_error'] is None

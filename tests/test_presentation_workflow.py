import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard import server
from test_dashboard_export import take


def test_scene_update_reuses_motion_and_gameplay_and_invalidates_export(tmp_path,monkeypatch):
    queue,folder=take(tmp_path,monkeypatch)
    queue.active.update(live={'scene':'old.glb','motion':'motion.json'},export_video='old.mp4',export_view={'quality':'draft'})
    preserved={name:b'verified '+name.encode() for name in ('plan.json','motion.json','gameplay.mp4','judgements.json')}
    for name,data in preserved.items():(folder/name).write_bytes(data)
    result={'revision':'new-set','blend':'presentation-new.blend','glb':'presentation-new.glb'}
    (folder/'presentation-result.json').write_text(json.dumps(result))
    calls=[];monkeypatch.setattr(queue,'command',lambda args,*rest:calls.append(args))
    queue.work_scene(folder)
    assert len(calls)==1 and 'refresh_scene.py' in str(calls[0])
    assert queue.active['live']=={'scene':server.media_url(folder/result['glb']),'motion':'motion.json'}
    assert queue.active['export_video'] is None and queue.active['export_view'] is None
    assert queue.active['presentation']==result
    for name,data in preserved.items():assert (folder/name).read_bytes()==data


def test_failed_scene_update_keeps_previous_scene_and_video(tmp_path,monkeypatch):
    queue,folder=take(tmp_path,monkeypatch)
    queue.active['export_video']='previous.mp4'
    def fail(*args):raise ValueError('bad mesh')
    monkeypatch.setattr(queue,'command',fail)
    queue.work_scene(folder)
    assert queue.active['status']=='complete'
    assert queue.active['live']=={'scene':'scene.glb'}
    assert queue.active['export_video']=='previous.mp4'
    assert queue.active['scene_error']=='bad mesh'


def test_export_uses_updated_scene_snapshot_and_scene_sensitive_cache(tmp_path,monkeypatch):
    queue,folder=take(tmp_path,monkeypatch)
    queue.active['presentation']={'revision':'new-set','blend':'presentation-new.blend'}
    calls=[];monkeypatch.setattr(queue,'command',lambda args,*rest:calls.append(args))
    queue.work_export(folder,server.Settings().model_dump())
    assert folder/'presentation-new.blend' in calls[0]
    first=queue.active['export_video']
    queue.active['presentation']['revision']='newer-set'
    queue.work_export(folder,server.Settings().model_dump())
    assert queue.active['export_video']!=first

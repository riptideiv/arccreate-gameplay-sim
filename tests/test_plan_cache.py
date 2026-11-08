import hashlib,json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import plan_cache as cache
import plan_excerpt

@pytest.fixture
def chart(tmp_path):
 p=tmp_path/'chart.aff';p.write_text('AudioOffset:0\n-\n(100,1);\n');return p

def test_key_separates_every_planner_input_but_reuses_presentation_changes(chart,monkeypatch):
 def key(settings=None,**kwargs):return cache.request_key(chart,0,1000,settings or {},False,kwargs.get('excluded',()),kwargs.get('avoid',()))[0]
 original=key({'max_speed_m_s':4,'arc_path_mode':'score'})
 assert key({'max_speed_m_s':4,'arc_path_mode':'score','camera_yaw_deg':30,'quality':'final','lift_mm':20})==original
 for name,value in {'max_speed_m_s':3,'prefer_arc_trace':True,'full_arc_hold':True,'partial_hold_weight':999,'search_width':64,'point_contact_frames':3,'acceleration_weight':100,'note_center_weight':50,'note_center_free_mm':4,'future_planner_setting':1}.items():
  assert key({'max_speed_m_s':4,'arc_path_mode':'score',name:value})!=original
 assert key(excluded=['a'])!=key()
 assert key(avoid=[{'parent':'arc','start_ms':0,'end_ms':5}])!=key()
 monkeypatch.setattr(cache,'implementation_version',lambda:'changed-native-rules');assert key({'max_speed_m_s':4,'arc_path_mode':'score'})!=original

def test_validated_atomic_completed_reuse_and_corruption_recovery(chart,tmp_path,monkeypatch):
 calls=[];original=plan_excerpt._create_plan
 def compute(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
 monkeypatch.setattr(plan_excerpt,'_create_plan',compute)
 settings={'arc_path_mode':'score','max_speed_m_s':4,'search_width':32}
 first=plan_excerpt.create_plan(chart,0,1000,settings,cache_dir=tmp_path/'cache')
 events=[];second=plan_excerpt.create_plan(chart,0,1000,{**settings,'camera_elevation_deg':69},progress=events.append,cache_dir=tmp_path/'cache')
 assert first==second and len(calls)==1 and events[0]['cache_hit']
 entry,=(tmp_path/'cache').glob('*.json');saved=json.loads(entry.read_text());saved['plan']['touches'][0]['path_mm'][0][0]+=1;entry.write_text(json.dumps(saved))
 plan_excerpt.create_plan(chart,0,1000,settings,cache_dir=tmp_path/'cache');assert len(calls)==2
 assert not list((tmp_path/'cache').glob('*.tmp'))

def test_failed_or_cancelled_search_is_never_published(chart,tmp_path,monkeypatch):
 def cancel(*args,**kwargs):raise KeyboardInterrupt()
 monkeypatch.setattr(plan_excerpt,'_create_plan',cancel)
 with pytest.raises(KeyboardInterrupt):plan_excerpt.create_plan(chart,0,1000,cache_dir=tmp_path/'cache')
 assert not (tmp_path/'cache').exists()

def test_content_device_and_window_invalidate_key(chart,tmp_path,monkeypatch):
 from arc_hand_order import api
 args=(chart,0,1000,{},False,(),())
 a=cache.request_key(*args)[0];chart.write_text(chart.read_text()+'(200,2);\n');b=cache.request_key(*args)[0];assert a!=b
 device=tmp_path/'device.json';device.write_bytes(api.DEFAULT_DEVICE.read_bytes()+b'\n');monkeypatch.setattr(api,'DEFAULT_DEVICE',device)
 assert b!=cache.request_key(*args)[0]
 assert cache.request_key(chart,0,900,{},False,(),())[0]!=cache.request_key(*args)[0]

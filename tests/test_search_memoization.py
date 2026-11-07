from pathlib import Path
from types import SimpleNamespace
from arc_hand_order import api
from arc_hand_order.camera import CameraPose
from arc_hand_order.planning.multi_finger import Regions,_Graph,Touch,Part
from arc_hand_order.planning.occupations import ContactRegion
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning import arc_scoring


def test_fixed_projection_matrix_is_computed_once_without_changing_coordinates(monkeypatch):
 calls=[];original=CameraPose.view_projection
 def counted(self):calls.append(1);return original(self)
 monkeypatch.setattr(CameraPose,'view_projection',counted)
 projection=GameplayProjection(api.load_device())
 points=[(0.,1.,0.),(1.,2.,0.),(-1.,.5,0.)]
 actual=[projection._world_to_device(p) for p in points]
 assert len(calls)==1
 expected=[]
 for p in points:
  x,y=projection.camera.project(p,projection.width_m,projection.height_m)
  expected.append((float(x-projection.width_m/2),float(projection.height_m/2-y)))
 assert actual==expected
 assert not projection._view_projection.flags.writeable


def test_polygon_cache_clamps_time_isolates_geometry_and_returns_mutable_copies():
 projection=GameplayProjection(api.load_device());regions=Regions(projection);calls=[]
 def occupation(x):
  return SimpleNamespace(start_ms=0,end_ms=100,kind='chain',occupation_id='same-parent',
    region_at=lambda t:(calls.append(t) or ContactRegion('chain',x=x,y=100)))
 a,b=occupation(0),occupation(300)
 first=regions.polygon(a,200);first.clear()
 assert regions.polygon(a,999) and calls==[100]
 assert regions.polygon(a,100)!=regions.polygon(b,100)
 # Another physical device/projection must never see this cached polygon.
 device=api.load_device().model_copy(deep=True);device.screen.width_m # frozen profile: construct another existing device
 different=GameplayProjection(api.load_device(api.DEVICES/'ipad_10_9.json'))
 assert Regions(different).polygon(a,100)!=regions.polygon(a,100)


def graph():
 g=object.__new__(_Graph);g.options=SimpleNamespace(speed_max_m_s=1);g.offset=0;g.projection=object()
 g.native_geometry=SimpleNamespace(guard_path=lambda o,p,d,**kw:tuple(p))
 g.trace_preference=None;g.scoring_extensions={};g.extension_prefix_speed={};g._target_path=lambda c:c.contact_path
 g._intern=lambda parts,bridges,path:Touch(100,parts,bridges,0,len(path),path,0)
 old=Touch(0,(),(),0,2,((0,0),(.001,0)),0)
 c=SimpleNamespace(id=1,down_frame=3,up_frame=5,first_xy=(.002,0),contact_path=((.002,0),(.002,0)),occupation=object())
 return g,old,c


def test_extension_geometry_cached_without_losing_earlier_tick_windows(monkeypatch):
 g,old,c=graph();calls=[]
 def covers(o,path,down,offset,projection):
  calls.append(tuple(path));assert path[0]==old.path[0];return [0] # New tick may settle on the old prefix.
 monkeypatch.setattr(arc_scoring,'covers',covers)
 a=g._scoring_extensions(old,c);assert len(a)==2 and len(calls)==2
 assert g._scoring_extensions(old,c) is a and len(calls)==2
 assert all(t.path[:2]==old.path for t in a)


def test_extension_checks_prefix_at_its_exact_tolerance_and_new_boundary(monkeypatch):
 g,old,c=graph();calls=[]
 monkeypatch.setattr(arc_scoring,'covers',lambda *args:(calls.append(1) or None))
 bad=Touch(2,(),(),0,2,((0,0),(1.00002/60,0)),0)
 assert g._scoring_extensions(bad,c)==() and not calls
 c.first_xy=(.2,0);c.contact_path=((.2,0),(.2,0))
 assert g._scoring_extensions(old,c)==()
 count=len(calls);assert g._scoring_extensions(old,c)==() and len(calls)==count

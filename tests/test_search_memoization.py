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
 g=object.__new__(_Graph);g.options=SimpleNamespace(speed_max_m_s=1,contact_offset_fractions=(.48,.24));g.offset=0;g.projection=object()
 g.native_geometry=SimpleNamespace(guard_path=lambda o,p,d,**kw:tuple(p))
 g.trace_preference=None;g.scoring_extensions={};g.extension_prefix_speed={};g._target_path=lambda c:c.contact_path
 g._intern=lambda parts,bridges,path:Touch(100,parts,bridges,0,len(path),path,0)
 old=Touch(0,(),(),0,2,((0,0),(.001,0)),0)
 c=SimpleNamespace(id=1,down_frame=3,up_frame=5,first_xy=(.002,0),contact_path=((.002,0),(.002,0)),occupation=object())
 return g,old,c


def test_extension_geometry_cached_without_losing_earlier_tick_windows(monkeypatch):
 g,old,c=graph();calls=[]
 def covers(o,path,down,offset,projection,*,fractions):
  calls.append(tuple(path));assert fractions==(.48,.24) and path[0]==old.path[0];return [0] # New tick may settle on the old prefix.
 monkeypatch.setattr(arc_scoring,'covers',covers)
 a=g._scoring_extensions(old,c);assert len(a)==2 and len(calls)==2
 assert g._scoring_extensions(old,c) is a and len(calls)==2
 assert all(t.path[:2]==old.path for t in a)


def test_extension_checks_prefix_at_its_exact_tolerance_and_new_boundary(monkeypatch):
 g,old,c=graph();calls=[]
 monkeypatch.setattr(arc_scoring,'covers',lambda *args,**kwargs:(calls.append(1) or None))
 bad=Touch(2,(),(),0,2,((0,0),(1.00002/60,0)),0)
 assert g._scoring_extensions(bad,c)==() and not calls
 c.first_xy=(.2,0);c.contact_path=((.2,0),(.2,0))
 assert g._scoring_extensions(old,c)==()
 count=len(calls);assert g._scoring_extensions(old,c)==() and len(calls)==count


def test_lock_expiry_preserves_connected_spans_grace_and_frame_boundaries():
 from bisect import bisect_left
 import math
 from arc_hand_order.planning.two_finger import frame_ms
 g=object.__new__(_Graph);g.offset=7;g.lock_expiries={};g.lock_grace=[19,75]
 g.arc_notes_by_colour={0:((0,101,50.),(102,301,75.),(500,900,float('inf'))),1:((0,800,20.),),2:(),3:()}
 def reference(colour,up):
  now=frame_ms(up,g.offset);notes=g.arc_notes_by_colour[colour]
  active=[(a,b,i) for a,b,i in notes if a<=now<=b]
  if not active:return -1
  interval=max(i for _,_,i in active)
  duration=min(round(interval*4),1000) if math.isfinite(interval) else 1000
  end=max(b for _,b,_ in active)
  while True:
   following=[b for a,b,_ in notes if a<=end+1 and b>end]
   if not following:break
   end=max(following)
  unlock=min(now+duration,end);index=bisect_left(g.lock_grace,up)
  return min(unlock,frame_ms(g.lock_grace[index],g.offset)-1) if index<len(g.lock_grace) else unlock
 for colour in range(4):
  for up in range(-1,85):
   assert g.lock_expiry(colour,up)==reference(colour,up)
 # Repeated release queries do not scan the chart again.
 expected=g.lock_expiry(0,12)
 class NoScan:
  def __iter__(self):raise AssertionError('repeated lock scan')
 g.arc_notes_by_colour[0]=NoScan()
 assert g.lock_expiry(0,12)==expected


def test_native_validation_caches_both_success_and_failure(monkeypatch):
 g=object.__new__(_Graph);g.offset=0;g.projection=object();g.graced={};g.options=SimpleNamespace(contact_offset_fractions=None)
 g.bound_frames=lambda t:{0:{0,1}}
 point=SimpleNamespace(contact_window=SimpleNamespace(earliest_ms=0,latest_ms=16))
 o=SimpleNamespace(native_scoring=True,occupation_id='tick',score_points=(point,),colour=0)
 part=Part(SimpleNamespace(occupation=o),0,2)
 calls=[]
 monkeypatch.setattr(arc_scoring,'source_note',lambda *args:None)
 monkeypatch.setattr(arc_scoring,'world',lambda projection,xy:xy)
 monkeypatch.setattr(arc_scoring,'hit',lambda note,xy,now,**kwargs:(calls.append(xy) or xy[0]==1))
 for identity,x in enumerate((0,1)):
  touch=Touch(identity,(part,),(),0,2,((x,0),(x,0)),0)
  assert g.native_claims_valid(touch)==bool(x)
  count=len(calls)
  assert g.native_claims_valid(touch)==bool(x) and len(calls)==count


def test_pair_caches_preserve_symmetric_clashes_and_directional_steals():
 g=object.__new__(_Graph);g.offset=0;g.clash_pairs={};g.steal_pairs={}
 newer=Touch(1,(),(),2,4,((0,0),(0,0)),0)
 older=Touch(2,(),(),0,4,((0,0),)*4,0)
 third=Touch(3,(),(),1,4,((0,0),)*3,0)
 calls=[];bindings={1:{0:{2,3}},2:{},3:{0:{2}}}
 g.bound_frames=lambda t:(calls.append(t.id) or bindings[t.id])
 assert g.clash(newer,[third]);count=len(calls)
 assert g.clash(third,[newer]) and len(calls)==count
 assert not g.clash(newer,[older])
 g.arc_spans=lambda t:((0,object(),2,4),)
 g.regions=SimpleNamespace(polygon=lambda *args:[(-1,-1),(1,-1),(1,1),(-1,1)])
 assert g.steals(newer,[older]);count=len(calls)
 assert g.steals(newer,[older]) and len(calls)==count
 assert not g.steals(older,[newer])
 assert not g.steals(newer,[third]) # Earlier finger already bound to an arc.


def test_future_cache_retains_only_current_layer():
 g=object.__new__(_Graph);g.futures={(9,1):('old suffix',)};g.future_frame=1
 assert g.future(-1,2)==-1 and g.futures=={}
 g.futures[(9,2)]=('current suffix',)
 assert g.future(-1,2)==-1 and len(g.futures)==1
 assert g.future(-1,3)==-1 and g.futures=={}


def test_guard_target_cache_keeps_colour_order_and_projection_scope():
 from arc_hand_order.planning.native_input import ArcInputGeometry
 calls=[]
 g=object.__new__(ArcInputGeometry);g.offset=0;g.guards={3:1};g.guard_targets={}
 g.projection=SimpleNamespace(judgement_to_device=lambda x,y:(calls.append((x,y)) or (x,y)))
 g.active_notes={3:[(0,SimpleNamespace(position=lambda now:(-1,0))),
                    (0,SimpleNamespace(position=lambda now:(1,0))),
                    (1,SimpleNamespace(position=lambda now:(2,0)))]}
 # Equal distances retain active-note order. Different paths reuse targets,
 # not a previously chosen target or guarded output.
 assert g.guard_path(SimpleNamespace(colour=0),((0,0),),3)==((-1,0),)
 assert g.guard_path(SimpleNamespace(colour=0),((.9,0),),3)==((1,0),)
 assert calls==[(-1,0),(1,0)]
 assert g.guard_path(SimpleNamespace(colour=1),((0,0),),3)==((2,0),)

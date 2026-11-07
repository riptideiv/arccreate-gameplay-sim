"""Compare original and presentation snapshots, including exact hand geometry/keys."""
import hashlib,json,sys
from pathlib import Path
import bpy
import numpy as np

folder=Path(sys.argv[sys.argv.index('--')+1]).resolve()
run=json.loads((folder/'run.json').read_text())

def snapshot(path):
 bpy.ops.wm.open_mainfile(filepath=str(path));bpy.context.scene.frame_set(1)
 out={}
 for name in ['Left index target','Right index target','Tablet display | ArcCreate','Camera | Tablet play']:
  obj=bpy.data.objects[name];out[name]={'world':np.array(obj.matrix_world).tolist()}
  if obj.animation_data:
   action=obj.animation_data.action;bag=action.layers[0].strips[0].channelbag(obj.animation_data.action_slot)
   out[name]['keys']=[(c.data_path,c.array_index,[(tuple(k.co),k.interpolation) for k in c.keyframe_points]) for c in bag.fcurves]
 for obj in bpy.context.scene.objects:
  if obj.type=='MESH' and obj.parent and obj.parent.name in ('Left index target','Right index target'):
   verts=np.empty(len(obj.data.vertices)*3,dtype=np.float32);obj.data.vertices.foreach_get('co',verts)
   indices=np.empty(len(obj.data.loops),dtype=np.int32);obj.data.loops.foreach_get('vertex_index',indices)
   out[obj.name]={'geometry':hashlib.sha256(verts.tobytes()+indices.tobytes()).hexdigest(),'local_matrix':np.array(obj.matrix_local).tolist()}
 return out
before=snapshot(folder/'scene.blend');after=snapshot(folder/run['presentation']['blend'])
assert before.keys()==after.keys()
maximum=0
for name,values in before.items():
 for key,value in values.items():
  if key in ('world','local_matrix'):
   error=float(np.max(abs(np.array(value)-np.array(after[name][key]))));maximum=max(error,maximum);assert error<1e-6,(name,key,error)
  else:assert value==after[name][key],(name,key)
result={'verified':True,'maximum_matrix_error':maximum,'hand_meshes_and_animation_keys':'byte-identical','objects':list(before)}
(folder/'preserved-scene-check.json').write_text(json.dumps(result,indent=2));print(result)

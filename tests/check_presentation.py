"""Blender integration audit: interface geometry, native contacts and set clearance.
Run against a refreshed scene with -- TAKE_DIRECTORY. Writes presentation-check.json.
"""
import json,math,sys
from pathlib import Path
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

folder=Path(sys.argv[sys.argv.index('--')+1]);plan=json.loads((folder/'plan.json').read_text())
scene=bpy.context.scene;display=bpy.data.objects['Tablet display | ArcCreate']
w,h=np.array(plan['device']['screen_mm'])/1000
assert max(abs(display.dimensions[i]-[w,h][i]) for i in (0,1))<1e-7
assert (display.matrix_world.translation-Vector((.405,.802,.708))).length<1e-7
assert display.parent is None and all(bpy.data.objects[s+' index target'].parent is None for s in ('Left','Right'))
assert not bpy.data.objects.get('Desk') and not bpy.data.objects.get('Room')
uv=display.data.uv_layers.active
uv_error=max(abs(uv.data[loop.index].uv[i]-(display.data.vertices[loop.vertex_index].co[i]/[w,h][i]+.5)) for loop in display.data.loops for i in (0,1))
assert uv_error<1e-6
r=display['corner_radius_m'];clipped=0;contacts=0
for touch in plan['touches']:
 for x,y in touch['path_mm']:
  contacts+=1;dx=max(abs(x/1000)-(w/2-r),0);dy=max(abs(y/1000)-(h/2-r),0)
  clipped+=dx*dx+dy*dy>r*r+1e-10
assert clipped==0, f'{clipped} contacts hidden by display corners'

def geometry(obj):
 evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
 vertices=[evaluated.matrix_world@v.co for v in mesh.vertices]
 polys=[list(p.vertices) for p in mesh.polygons]
 evaluated.to_mesh_clear()
 return vertices,polys

def bounds(verts):
 v=np.array(verts);return v.min(axis=0),v.max(axis=0)

def overlap(a,b):return bool(np.all(a[0]<=b[1]) and np.all(b[0]<=a[1]))

props=[o for o in scene.objects if o.type=='MESH' and (o.name.startswith('Plant |') or o.name.startswith('Pen |'))]
static=[]
for obj in props:
 v,f=geometry(obj);static.append((obj.name,bounds(v),BVHTree.FromPolygons(v,f)))
intersections=[]
for obj in [o for o in scene.objects if o.name.startswith('Tablet')]:
 v,f=geometry(obj);a=bounds(v);tree=BVHTree.FromPolygons(v,f)
 for name,b,t in static:
  if overlap(a,b) and tree.overlap(t):intersections.append([obj.name,name])
assert not intersections,intersections
hand_objects=[o for o in scene.objects if o.type=='MESH' and o.parent and o.parent.name in ('Left index target','Right index target')]
local=[]
for obj in hand_objects:
 # Geometry is rigid; evaluate once, then transform its vertices per frame.
 e=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());m=e.to_mesh()
 local.append((obj,np.array([(*v.co,1) for v in m.vertices]),[list(p.vertices) for p in m.polygons]));e.to_mesh_clear()
minimum_table_clearance=1;candidate_checks=0
for sample in range(scene.frame_end*2-1):
 timeline=1+sample/2;scene.frame_set(math.floor(timeline),subframe=timeline%1)
 for obj,vertices,faces in local:
  world=vertices@np.array(obj.matrix_world).T;world=world[:,:3];a=bounds(world)
  minimum_table_clearance=min(minimum_table_clearance,float(a[0][2]-.6952))
  for name,b,tree in static:
   if overlap(a,b):
    candidate_checks+=1
    if BVHTree.FromPolygons(world.tolist(),faces).overlap(tree):intersections.append([sample/60,obj.name,name])
assert not intersections,intersections[:10]
assert minimum_table_clearance>0
result={'verified':True,'sampled_hand_poses':scene.frame_end*2-1,'actual_mesh_prop_intersections':intersections,
 'mesh_candidates':candidate_checks,'minimum_hand_to_pad_top_mm':minimum_table_clearance*1000,
 'display_uv_error':uv_error,'contact_samples':contacts,'contacts_hidden_by_corner_mask':clipped,
 'masked_display_area_percent':100*(4-math.pi)*r*r/(w*h)}
(folder/'presentation-check.json').write_text(json.dumps(result,indent=2));print(result)

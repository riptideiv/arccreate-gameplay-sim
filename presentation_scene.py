"""Shared Blender presentation set, used by new builds and saved-take refreshes.

All dimensions are metres. The gameplay plane and contact roots are interfaces:
never move them to arrange the set. No assets from the dafeiyu room are loaded.
"""
import hashlib
import math
from pathlib import Path
import struct
import zlib

import bpy
from mathutils import Matrix, Vector
import numpy as np

CENTER = Vector((.405, .802, .708))
DISPLAY = 'Tablet display | ArcCreate'
ROOTS = ('Left index target', 'Right index target')
COLLECTION = 'Presentation | Walnut studio'


def revision():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def material(name, color, roughness=.5, metal=0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    p = m.node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value = (*color, 1)
    p.inputs['Roughness'].default_value = roughness
    p.inputs['Metallic'].default_value = metal
    m.diffuse_color = (*color, 1)
    return m


def link(obj, collection):
    for c in list(obj.users_collection): c.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


def outline(width, height, radius, steps=12):
    points = []
    for cx, cy, start in ((width/2-radius,height/2-radius,0),
                          (-width/2+radius,height/2-radius,90),
                          (-width/2+radius,-height/2+radius,180),
                          (width/2-radius,-height/2+radius,270)):
        for i in range(steps+1):
            a = math.radians(start + i*90/steps)
            points.append((cx+radius*math.cos(a),cy+radius*math.sin(a)))
    return points


def rounded_mesh(name, width, height, radius, thickness=0):
    ring = outline(width,height,radius)
    n = len(ring)
    if thickness:
        verts = [(x,y,-thickness/2) for x,y in ring]+[(x,y,thickness/2) for x,y in ring]
        faces = [tuple(reversed(range(n))),tuple(range(n,2*n))]
        faces += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    else:
        verts = [(0,0,0)]+[(x,y,0) for x,y in ring]
        faces = [(0,i+1,(i+1)%n+1) for i in range(n)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts,[],faces)
    mesh.update()
    uv = mesh.uv_layers.new(name='UVMap')
    for loop in mesh.loops:
        p = mesh.vertices[loop.vertex_index].co
        uv.data[loop.index].uv = (p.x/width+.5,p.y/height+.5)
    return mesh


def slab(name, size, at, radius, mat, collection, bevel=.0004):
    obj = bpy.data.objects.new(name,rounded_mesh(name,*size[:2],radius,size[2]))
    collection.objects.link(obj)
    obj.location = at
    obj.data.materials.append(mat)
    if bevel:
        b = obj.modifiers.new('Soft edge glint','BEVEL'); b.width=bevel; b.segments=3
        obj.modifiers.new('Surface normals','WEIGHTED_NORMAL')
    return obj


def png(path, pixels):
    h,w,_ = pixels.shape
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    raw = b''.join(b'\0'+row.tobytes() for row in pixels)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw,6))+chunk(b'IEND',b''))


def walnut(directory):
    """Deterministic image grain, not Blender-only noise. PNGs are packed in GLB/blend."""
    directory = directory / revision()
    directory.mkdir(parents=True,exist_ok=True)
    base = directory/'walnut-color.png'; normal = directory/'walnut-normal.png'
    if not base.exists() or not normal.exists():
        y,x = np.mgrid[0:1024,0:1536].astype(float); x/=1536; y/=1024
        rng=np.random.default_rng(31)
        flow = y + .012*np.sin(x*9+np.sin(y*13)) + .004*np.sin(x*27+y*11)
        grain = np.sin(flow*620 + 2*np.sin(flow*44+x*4))
        pores = np.maximum(0,grain)**12
        broad = .5*np.sin(flow*58+x*2)+.3*np.sin(flow*117+x*3)
        fine = rng.normal(0,1,x.shape)
        value = 1+.13*broad-.18*pores+.018*fine
        rgb = np.stack([112*value,72*value,44*value],axis=-1)
        png(base,np.uint8(np.clip(rgb,0,255)))
        relief = .35*grain+.12*broad+.04*fine
        dy,dx = np.gradient(relief)
        normals = np.stack([-dx*.5,-dy*.5,np.ones_like(dx)],axis=-1)
        normals /= np.linalg.norm(normals,axis=-1,keepdims=True)
        png(normal,np.uint8(np.clip((normals*.5+.5)*255,0,255)))
    mat=material('Set | oiled walnut',(.26,.13,.065),.43)
    nodes=mat.node_tree.nodes; links=mat.node_tree.links; p=nodes.get('Principled BSDF')
    for path,kind in ((base,'color'),(normal,'normal')):
        tex=nodes.new('ShaderNodeTexImage');tex.image=bpy.data.images.load(str(path),check_existing=True);tex.image.pack()
        if kind=='color': links.new(tex.outputs['Color'],p.inputs['Base Color'])
        else:
            tex.image.colorspace_settings.name='Non-Color'
            n=nodes.new('ShaderNodeNormalMap');n.inputs['Strength'].default_value=.3
            links.new(tex.outputs['Color'],n.inputs['Color']);links.new(n.outputs['Normal'],p.inputs['Normal'])
    return mat


def tube(name, points, radius, mat, collection):
    curve=bpy.data.curves.new(name,'CURVE');curve.dimensions='3D';curve.bevel_depth=radius;curve.bevel_resolution=2
    spline=curve.splines.new('POLY');spline.points.add(len(points)-1)
    for p,xyz in zip(spline.points,points):p.co=(*xyz,1)
    obj=bpy.data.objects.new(name,curve);collection.objects.link(obj);obj.data.materials.append(mat)
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active=obj;obj.select_set(True)
    bpy.ops.object.convert(target='MESH');obj.select_set(False)
    return obj


def lathe(name, profile, mat, collection, at, segments=48):
    verts=[(r*math.cos(i*math.tau/segments),r*math.sin(i*math.tau/segments),z) for r,z in profile for i in range(segments)]
    faces=[]
    for j in range(len(profile)-1):
        for i in range(segments):
            a=j*segments+i;b=j*segments+(i+1)%segments
            faces.append((a,b,b+segments,a+segments))
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update()
    obj=bpy.data.objects.new(name,mesh);collection.objects.link(obj);obj.location=at;mesh.materials.append(mat)
    for f in mesh.polygons:f.use_smooth=True
    return obj


def plant(collection):
    at=CENTER+Vector((-.155,.130,-.0155))
    ceramic=material('Set | warm porcelain',(.66,.61,.51),.34)
    soil=material('Set | potting soil',(.035,.022,.013),.96)
    green=[material('Set | leaf sage',(.075,.20,.105),.47),material('Set | leaf olive',(.12,.265,.105),.49)]
    first=set(collection.objects)
    stem=material('Set | plant stems',(.09,.16,.045),.65)
    lathe('Plant | ceramic pot',[(0,0),(.017,0),(.019,.003),(.024,.038),(.024,.041),(.022,.043),(.020,.040),(.019,.006),(0,.006)],ceramic,collection,at)
    lathe('Plant | soil',[(0,.035),(.0208,.035)],soil,collection,at)
    for j in range(7):
        a=j*2.399;h=.053+j*.006
        end=at+Vector((math.cos(a)*.018,math.sin(a)*.018,h))
        tube('Plant | stem', [at+Vector((0,0,.034)),end],.00065,stem,collection)
        # Curved folded leaf with real thickness; varying side normals catch soft light.
        direction=Vector((math.cos(a),math.sin(a),.45)).normalized();across=Vector((-math.sin(a),math.cos(a),0))
        vertices=[]
        for k in range(9):
            t=k/8;centre=end+direction*(t*.033)+Vector((0,0,.009*math.sin(t*math.pi)))
            width=.009*math.sin(t*math.pi)**.7
            for side in (-1,0,1): vertices.append(tuple(centre+across*width*side+Vector((0,0,-abs(side)*.002*math.sin(t*math.pi)))))
        faces=[(k*3+s,k*3+s+1,(k+1)*3+s+1,(k+1)*3+s) for k in range(8) for s in range(2)]
        mesh=bpy.data.meshes.new('Leaf');mesh.from_pydata(vertices,[],faces);mesh.update()
        obj=bpy.data.objects.new('Plant | leaf',mesh);collection.objects.link(obj);mesh.materials.append(green[j%2])
        solid=obj.modifiers.new('Leaf thickness','SOLIDIFY');solid.thickness=.00025
        for f in mesh.polygons:f.use_smooth=True

    # Compact desk plant, with the base clear of the mat's rounded rear corner.
    for obj in set(collection.objects)-first:
        local = bool(obj.location.z)
        for vertex in obj.data.vertices:
            delta = vertex.co if local else vertex.co-at
            scaled = Vector((delta.x*.8,delta.y*.8,delta.z*.5))
            vertex.co = scaled if local else at+scaled


def pen(collection):
    metal=material('Set | brushed champagne metal',(.49,.43,.31),.27,.8)
    ink=material('Set | forest lacquer',(.025,.075,.069),.3,.25)
    at=CENTER+Vector((.153,.067,-.009))
    root=bpy.data.objects.new('Pen | assembly',None);collection.objects.link(root);root.location=at
    root.rotation_euler=(math.pi/2,0,math.radians(-12))
    for name,profile,mat in (
        ('Pen | barrel',[(0,-.062),(.0031,-.062),(.0038,-.052),(.0038,.042),(.0031,.048),(0,.048)],ink),
        ('Pen | nib',[(0,-.077),(.0005,-.076),(.0031,-.062),(0,-.062)],metal),
        ('Pen | clicker',[(0,.048),(.0031,.048),(.0031,.054),(.0025,.056),(0,.056)],metal)):
        obj=lathe(name,profile,mat,collection,(0,0,0),32);obj.parent=root
    clip=tube('Pen | pocket clip',[(.0034,0,.040),(.005,0,.037),(.005,0,.012),(.0043,0,.010)],.00065,metal,collection);clip.parent=root
    for z in (-.046,-.042,-.038):
        obj=lathe('Pen | grip ring',[(.00382,z),(.00385,z+.0005)],metal,collection,(0,0,0),32);obj.parent=root


def detach(obj):
    world=obj.matrix_world.copy()
    # Existing animated roots use parent * inverse == identity. Reject unknown
    # animated parent bases rather than silently changing the animation curves.
    if obj.parent and obj.animation_data:
        basis=obj.parent.matrix_world @ obj.matrix_parent_inverse
        if max(abs(basis[i][j]-Matrix.Identity(4)[i][j]) for i in range(4) for j in range(4))>1e-6:
            raise ValueError('Animated parent basis changed: bake world-space keys before porting '+obj.name)
    obj.parent=None;obj.matrix_world=world
    return world


def apply(directory):
    scene=bpy.context.scene;bpy.context.view_layer.update()
    proto=bpy.data.collections.get('90 Tablet rhythm prototype')
    if not proto:raise ValueError('Missing tablet/hand collection')
    before={o.name:o.matrix_world.copy() for o in proto.all_objects}
    for obj in list(proto.all_objects):
        if obj.parent and obj.parent.name not in before:detach(obj)
    keep=set(proto.all_objects)
    for obj in list(scene.objects):
        if obj not in keep:bpy.data.objects.remove(obj,do_unlink=True)
    for c in list(bpy.data.collections):
        if c!=proto and not c.objects and not c.children:bpy.data.collections.remove(c)
    collection=bpy.data.collections.new(COLLECTION);scene.collection.children.link(collection)
    display=bpy.data.objects[DISPLAY];w,h=display.dimensions[:2]
    for name,r in (('Tablet chassis',.012),('Tablet bezel',.011), (DISPLAY,.003)):
        obj=bpy.data.objects[name];dims=obj.dimensions.copy();old=obj.data
        mats=list(old.materials)
        obj.modifiers.clear();obj.data=rounded_mesh(name,dims.x,dims.y,r,dims.z if name!=DISPLAY else 0)
        for mat in mats:obj.data.materials.append(mat)
        obj['corner_radius_m']=r
        if name!=DISPLAY:
            b=obj.modifiers.new('Edge glint','BEVEL');b.width=.00035;b.segments=3
            obj.modifiers.new('Surface normals','WEIGHTED_NORMAL')
    wood=walnut(Path(directory)/'presentation-assets')
    slab('Set | walnut tabletop',(1.6,1.3,.027),CENTER+Vector((0,0,-.029)),.025,wood,collection,.002)
    fabric=material('Set | charcoal woven mat',(.036,.055,.061),.94)
    pad=slab('Set | mousepad',(.326,.247,.0025),CENTER+Vector((0,-.006,-.01425)),.018,fabric,collection,.0003)
    stitch=material('Set | subtle stitched edge',(.11,.14,.145),.95)
    ring=outline(.321,.242,.016)
    tube('Set | mousepad stitching',[(CENTER.x+x,CENTER.y-.006+y,.6952) for x,y in ring+[ring[0]]],.00022,stitch,collection)
    plant(collection);pen(collection)
    world=bpy.data.worlds.new('Presentation | soft studio');world.use_nodes=True
    world.node_tree.nodes['Background'].inputs[0].default_value=(.36,.40,.44,1)
    world.node_tree.nodes['Background'].inputs[1].default_value=.30;scene.world=world
    for name,offset,power,size,color in (
        ('Presentation | window key',(-.34,-.16,.55),8,.42,(1,.91,.79)),
        ('Presentation | broad fill',(.32,.16,.45),4,.38,(.82,.90,1))):
        data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size;data.color=color
        obj=bpy.data.objects.new(name,data);collection.objects.link(obj);obj.location=CENTER+Vector(offset)
        obj.rotation_euler=(CENTER-obj.location).to_track_quat('-Z','Y').to_euler()
    scene.view_settings.view_transform='AgX';scene.view_settings.look='AgX - Medium High Contrast';scene.view_settings.exposure=0
    scene['presentation_revision']=revision()
    bpy.context.view_layer.update()
    error=max(max(abs(o.matrix_world[i][j]-before[o.name][i][j]) for i in range(4) for j in range(4)) for o in proto.all_objects)
    if error>1e-6:raise ValueError(f'Presentation moved existing interfaces by {error}')
    return {'revision':revision(),'transform_error':error,'display_dimensions_m':[w,h],
            'display_corner_radius_m':.003,'source':'presentation_scene.py'}


def export_glb(path):
    display=bpy.data.objects[DISPLAY];mats=list(display.data.materials)
    placeholder=material('Tablet | live video placeholder',(1,1,1))
    display.data.materials.clear();display.data.materials.append(placeholder)
    try:
        bpy.ops.export_scene.gltf(filepath=str(Path(path).resolve()),export_format='GLB',export_animations=False,
                                  export_cameras=True,export_lights=False,export_yup=True,export_apply=True)
    finally:
        display.data.materials.clear()
        for mat in mats:display.data.materials.append(mat)

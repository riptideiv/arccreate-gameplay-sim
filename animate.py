"""Animate one rigid, pointing hand per side from arc-hand-order contact JSON."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import bpy
from mathutils import Euler, Vector


CENTER = Vector((0.405, 0.802, 0.708))
TIP = 0.006
SIDES = ("left", "right")
SAMPLE_RATE = 60
FPS = 30
# Shared geometry and pose model; the index-pad centre is the root pivot.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hand_pose import HAND_PARTS, VERSION as POSE_VERSION, solve as solve_hand_poses, diagnostics
from motion_paths import positions as finger_positions, home_position
import numpy as np


def material(name, color, roughness=0.4, metal=0.0, emission=0.0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    node = mat.node_tree.nodes.get("Principled BSDF")
    node.inputs["Base Color"].default_value = (*color, 1)
    node.inputs["Roughness"].default_value = roughness
    node.inputs["Metallic"].default_value = metal
    node.inputs["Emission Color"].default_value = (*color, 1)
    node.inputs["Emission Strength"].default_value = emission
    return mat


def move_to(obj, collection):
    for previous in list(obj.users_collection):
        previous.objects.unlink(obj)
    collection.objects.link(obj)


def box(name, dims, xyz, mat, collection, bevel=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1, location=xyz)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dims
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    move_to(obj, collection)
    if bevel:
        modifier = obj.modifiers.new("Rounded edges", "BEVEL")
        modifier.width = bevel
        modifier.segments = 5
        obj.modifiers.new("Weighted normals", "WEIGHTED_NORMAL")
    return obj


def sphere(name, xyz, radii, collection):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=16, location=xyz)
    obj = bpy.context.object
    obj.name = name
    obj.scale = radii
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    for face in obj.data.polygons:
        face.use_smooth = True
    move_to(obj, collection)
    return obj


def hand_mesh(side, collection, glove, cuff):
    root = bpy.data.objects.new(f"{side.title()} index target", None)
    collection.objects.link(root)
    root.empty_display_size = 0.008
    pieces = []
    for name, center, radii in hand_parts(side):
        piece = sphere(name, center, radii, collection)
        if name == "cuff":
            piece.data.materials.append(cuff)
            piece.parent = root
        else:
            pieces.append(piece)
    bpy.ops.object.select_all(action="DESELECT")
    for piece in pieces:
        piece.select_set(True)
    bpy.context.view_layer.objects.active = pieces[0]
    bpy.ops.object.join()
    hand = bpy.context.object
    bpy.context.scene.cursor.location = (0, 0, 0)
    bpy.ops.object.origin_set(type="ORIGIN_CURSOR")
    hand.name = f"{side.title()} pointing glove"
    modifier = hand.modifiers.new("Continuous glove surface", "REMESH")
    modifier.mode = "VOXEL"
    modifier.voxel_size = 0.00075
    modifier.use_smooth_shade = True
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    hand.data.materials.append(glove)
    hand.parent = root
    return root


def hand_parts(side):
    sign = 1 if side == "left" else -1
    return [(name, Vector((sign * xyz[0], xyz[1], xyz[2])), radii)
            for name, xyz, radii in HAND_PARTS]


def validate_plan(plan):
    if plan["format"] != "arc-hand-order.plan/1":
        raise ValueError("Expected an arc-hand-order.plan/1 file")
    hands = {side: [] for side in SIDES}
    for touch in plan["touches"]:
        start, end = touch["down_frame"], touch["up_frame"]
        if touch["finger"] != 1 or start < 0 or end <= start or end-start != len(touch["path_mm"]):
            raise ValueError(f"Invalid one-finger contact {touch['id']}")
        hands[touch["hand"]].append(touch)
    for side, touches in hands.items():
        touches.sort(key=lambda touch: touch["down_frame"])
        if any(a["up_frame"] > b["down_frame"] for a, b in zip(touches, touches[1:])):
            raise ValueError(f"Overlapping contacts for the {side} hand")
    if not plan["touches"] and not plan.get("misses") and not plan.get("preview", {}).get("duration_ms"):
        raise ValueError("The plan contains no contacts")
    return hands


def positions(touches, side, last_frame, lift=0.008, max_speed=math.inf):
    return [Vector(p) for p in finger_positions(touches, side, last_frame, lift, max_speed)]


def rotation(pitch, yaw):
    return Euler((math.radians(-pitch), 0, math.radians(yaw)), "XYZ")


HAND_RADIUS = max(Vector(center).length + max(radii) for _, center, radii in HAND_PARTS) + 0.002


def step_speed(a, b, before, after):
    translation = (b-a).length * SAMPLE_RATE
    angular = math.sqrt(sum((x-y)**2 for x, y in zip(before, after))) * SAMPLE_RATE
    return translation, angular, translation + HAND_RADIUS * math.radians(angular)


def motion_report(paths, poses, max_speed, angular_limit, gap):
    report = {"max_speed_m_s": max_speed, "angular_limit_deg_s": angular_limit,
              "minimum_clearance_mm": None, "collision_verification": False,
              "hands": {}, "sample_rate_hz": SAMPLE_RATE}
    for i, side in enumerate(SIDES):
        samples = [[0, 0, 0]]
        for frame in range(1, len(poses)):
            samples.append(list(step_speed(paths[i][frame-1], paths[i][frame],
                                           poses[frame-1][i], poses[frame][i])))
        peak = max(range(len(samples)), key=lambda f: samples[f][2])
        report["hands"][side] = {
            "peak_translation_m_s": max(s[0] for s in samples),
            "peak_angular_deg_s": max(s[1] for s in samples),
            "peak_surface_bound_m_s": samples[peak][2], "peak_time_s": peak / SAMPLE_RATE,
            "speed_samples_m_s": [round(s[2], 4) for s in samples],
        }
        if samples[peak][2] > max_speed + 0.0001:
            raise ValueError(f"{side.title()} glove needs {samples[peak][2]:.2f} m/s at "
                             f"{peak / SAMPLE_RATE:.2f} s. The move limit is {max_speed:.2f} m/s.")
        if report["hands"][side]["peak_angular_deg_s"] > angular_limit + 0.001:
            raise ValueError(f"{side.title()} wrist exceeds the {angular_limit:g}°/s turn limit.")
    report["verified"] = True
    return report


def pose_sequence(paths, active_samples, max_speed=1.6, angular_limit=360, gap=0.001):
    """Pose around pinned contacts; overlaps are diagnostics, never plan failures."""
    points = np.stack([np.array([tuple(p) for p in path]) for path in paths], axis=1)
    active = np.zeros(points.shape[:2], dtype=bool)
    for h, samples in enumerate(active_samples):
        active[list(samples), h] = True
    # Verify the supplied point paths before adding optional pose adjustments.
    neutral = [((10, -18), (10, 18)) for _ in paths[0]]
    base_report = motion_report(paths, neutral, max_speed, angular_limit, gap)
    parts = [hand_parts(s) for s in SIDES]
    try:
        solved, angles, posing = solve_hand_poses(points, active, parts, max_speed, angular_limit, gap)
        output = [[Vector(p) for p in solved[:, h]] for h in range(2)]
        poses = [tuple(tuple(q) for q in pair) for pair in angles]
        report = motion_report(output, poses, max_speed, angular_limit, gap)
    except (RuntimeError, ValueError) as error:
        # Pose fitting must never change score or restart contact planning.
        output, poses, report = paths, neutral, base_report
        posing = diagnostics(points, np.array(neutral), parts, gap)
        posing['pose_solver_fallback_reason'] = str(error)
        posing['solver'] = POSE_VERSION
    report.update(posing)
    return output, poses, report


def timeline_frame(sample, audio_offset_ms=0):
    return 1 + (sample / SAMPLE_RATE - audio_offset_ms / 1000) * FPS


def webgl_motion(paths, poses, plan, settings):
    """Exact Blender world transforms converted from Z-up to glTF Y-up."""
    hands = {}
    for index, side in enumerate(SIDES):
        samples = []
        for position, pose in zip(paths[index], poses):
            p = CENTER + position
            q = rotation(*pose[index]).to_quaternion()
            samples.append([round(v, 7) for v in (p.x, p.z, -p.y, q.x, q.z, -q.y, q.w, *pose[index])])
        hands[side] = samples
    return {"sample_rate_hz": SAMPLE_RATE, "audio_offset_ms": plan["chart"]["audio_offset_ms"],
            "duration_s": (len(poses)-1)/SAMPLE_RATE - plan["chart"]["audio_offset_ms"]/1000,
            "hands": hands, "screen_mm": plan["device"]["screen_mm"],
            "settings": settings, "misses": plan.get("misses", [])}


def linear(obj):
    action = obj.animation_data.action
    channels = action.layers[0].strips[0].channelbag(obj.animation_data.action_slot)
    for curve in channels.fcurves:
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"


def screen(plan, movie, frame_count, collection):
    width, height = (value / 1000 for value in plan["device"]["screen_mm"])
    case = material("Tablet | satin aluminum", (0.085, 0.13, 0.21), metal=0.65)
    rim = material("Tablet | black glass edge", (0.013, 0.021, 0.034), roughness=0.24)
    bezel_x, bezel_y = width + 0.013, height + 0.013
    box("Tablet chassis", (bezel_x, bezel_y, 0.010),
        CENTER + Vector((0, 0, -0.008)), case, collection, 0.004)
    box("Tablet bezel", (bezel_x-0.001, bezel_y-0.001, 0.004),
        CENTER + Vector((0, 0, -0.0034)), rim, collection, 0.0017)

    image = bpy.data.images.load(str(movie.resolve()))
    if image.source != "MOVIE" or image.frame_duration < frame_count:
        raise ValueError(f"Expected a gameplay video with at least {frame_count} frames")
    mat = bpy.data.materials.new("Tablet | ArcCreate gameplay")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.image_user.frame_start = 1
    texture.image_user.frame_duration = frame_count
    texture.image_user.use_auto_refresh = True
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 0.8
    output = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
    mat.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])

    mesh = bpy.data.meshes.new("Display surface")
    mesh.from_pydata([(-width/2, -height/2, 0), (width/2, -height/2, 0),
                      (width/2, height/2, 0), (-width/2, height/2, 0)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new()
    for loop, coordinate in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = coordinate
    display = bpy.data.objects.new("Tablet display | ArcCreate", mesh)
    collection.objects.link(display)
    display.location = CENTER
    mesh.materials.append(mat)
    return image


def camera(name, location, target, scale, collection, perspective=False, lens=32):
    data = bpy.data.cameras.new(name)
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = (Vector(target)-obj.location).to_track_quat("-Z", "Y").to_euler()
    data.type = "PERSP" if perspective else "ORTHO"
    data.lens = lens
    data.ortho_scale = scale
    data.clip_start = 0.01
    return obj


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("blend", type=Path)
    parser.add_argument("--gameplay", type=Path, help="30 fps ArcCreate clip starting at plan time zero")
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--motion", type=Path)
    parser.add_argument("--motion-only", action="store_true")
    parser.add_argument("--glb", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    plan = json.loads(args.plan.read_text())
    contacts = validate_plan(plan)
    settings = json.loads(args.settings.read_text()) if args.settings else {}
    max_speed = settings.get("max_speed_m_s", 1.6)
    angular_limit = settings.get("angular_limit_deg_s", 360)
    last_contact = max((t["up_frame"] for t in plan["touches"]), default=0)
    requested = math.ceil((plan.get("preview", {}).get("duration_ms", 0) + plan["chart"]["audio_offset_ms"]) * SAMPLE_RATE / 1000)
    lift = settings.get("lift_mm", 8)/1000
    retreat = max((np.linalg.norm(np.array((*contacts[side][-1]['path_mm'][-1], TIP*1000))/1000
                                 - home_position(side, lift)) for side in SIDES if contacts[side]), default=0)
    last_frame = max(last_contact, requested) + max(34, math.ceil(1.875*retreat/max_speed*SAMPLE_RATE))
    paths = [positions(contacts[side], side, last_frame, settings.get("lift_mm", 8)/1000, max_speed) for side in SIDES]
    active_samples = [{frame for t in contacts[side] for frame in range(t["down_frame"], t["up_frame"])}
                      for side in SIDES]
    # Stationary rests are fixed anchors too. Collision detours may change free
    # travel, but must not stretch a detour through an otherwise idle dwell.
    for h, side in enumerate(SIDES):
        home = home_position(side, lift)
        active_samples[h].update(f for f,p in enumerate(paths[h]) if np.linalg.norm(np.array(p)-home) < 1e-9)
    try:
        signature = hashlib.sha256(json.dumps([plan, settings], sort_keys=True).encode()
                                   + Path(__file__).read_bytes()
                                   + Path(__file__).with_name('hand_pose.py').read_bytes()
                                   + Path(__file__).with_name('motion_paths.py').read_bytes()).hexdigest()
        cache_path = args.motion.with_suffix('.poses.npz') if args.motion else None
        cached = None
        if cache_path and cache_path.exists():
            with np.load(cache_path, allow_pickle=False) as saved:
                if str(saved['signature']) == signature:
                    cached = (saved['points'].copy(), saved['angles'].copy(), json.loads(str(saved['report'])))
        if cached:
            points, poses, report = cached
            paths = [[Vector(p) for p in points[:, h]] for h in range(2)]
            print('Reused contact-preserving glove poses.')
        else:
            paths, poses, report = pose_sequence(paths, active_samples, max_speed, angular_limit,
                                                settings.get("clearance_mm", 1)/1000)
            if cache_path:
                points = np.stack([np.array([tuple(p) for p in path]) for path in paths], axis=1)
                np.savez(cache_path, signature=signature, points=points, angles=np.array(poses), report=json.dumps(report))
    except ValueError as error:
        if args.report:
            args.report.write_text(json.dumps({"verified": False, "error": str(error),
                                               "failure": getattr(error,"failure",None)}))
        raise
    report["contacts"] = len(plan["touches"])
    report["duration_s"] = (last_frame / SAMPLE_RATE) - plan["chart"]["audio_offset_ms"]/1000
    report["frame_count"] = math.ceil(report["duration_s"] * FPS)
    report["skipped_ticks"] = plan.get("planning", {}).get("skipped_ticks", 0)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2))
    if args.motion:
        args.motion.write_text(json.dumps(webgl_motion(paths, poses, plan, settings), separators=(",", ":")))
    if args.motion_only:
        print("Verified motion for WebGL preview.")
        return
    scene = bpy.context.scene
    collection = bpy.data.collections.new("90 Tablet rhythm prototype")
    scene.collection.children.link(collection)
    frame_count = math.ceil(timeline_frame(last_frame, plan["chart"]["audio_offset_ms"]))
    gameplay = screen(plan, args.gameplay, frame_count, collection)
    cuffs = [(0.045, 0.24, 0.44), (0.57, 0.14, 0.09)]
    gloves = [(0.31, 0.65, 0.90), (0.92, 0.55, 0.40)]
    for i, side in enumerate(SIDES):
        cuff = material(f"{side.title()} | cuff", cuffs[i], roughness=0.47)
        glove = material(f"{side.title()} | glove", gloves[i], roughness=0.39)
        root = hand_mesh(side, collection, glove, cuff)
        for sample, (position, pose) in enumerate(zip(paths[i], poses)):
            frame = timeline_frame(sample, plan["chart"]["audio_offset_ms"])
            root.location = CENTER + position
            root.rotation_euler = rotation(*pose[i])
            root.keyframe_insert("location", frame=frame)
            root.keyframe_insert("rotation_euler", frame=frame)
        linear(root)
    distance = settings.get("camera_distance_m", 0.29)
    elevation = math.radians(settings.get("camera_elevation_deg", 52))
    yaw = math.radians(settings.get("camera_yaw_deg", 0))
    target = CENTER + Vector((0, -0.008, 0.020))
    eye = target + Vector((distance*math.cos(elevation)*math.sin(yaw),
                           -distance*math.cos(elevation)*math.cos(yaw), distance*math.sin(elevation)))
    scene.camera = camera("Camera | Tablet play", eye, target, 0.406, collection,
                          perspective=True, lens=settings.get("camera_lens_mm", 30))
    camera("Camera | Tablet clearance", (0.104, 0.574, 0.880), CENTER, 0.405, collection)
    camera("Camera | Tablet overhead", (0.405, 0.802, 1.30), CENTER, 0.392, collection)
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = settings.get("width", 768)
    scene.render.resolution_y = settings.get("height", 576)
    scene.render.resolution_percentage = 100
    scene.eevee.taa_render_samples = 12
    scene.render.image_settings.file_format = "PNG"
    scene.render.fps = FPS
    scene.frame_start = 1
    scene.frame_end = frame_count
    scene.frame_set(1)
    scene.render.filepath = "//frames/hand-"
    for area in bpy.context.screen.areas if bpy.context.screen else ():
        if area.type == "VIEW_3D":
            area.spaces.active.region_3d.view_perspective = "CAMERA"
            area.spaces.active.shading.type = "MATERIAL"
    from presentation_scene import apply as apply_presentation, export_glb
    args.blend.parent.mkdir(parents=True, exist_ok=True)
    presentation = apply_presentation(args.blend.parent)
    (args.blend.parent / "presentation-result.json").write_text(json.dumps(presentation, indent=2))
    gameplay.filepath = "//" + os.path.relpath(args.gameplay.resolve(), args.blend.parent.resolve())
    bpy.ops.wm.save_as_mainfile(filepath=str(args.blend.resolve()), relative_remap=False)
    if args.glb:
        export_glb(args.glb)
    report["contacts"] = len(plan["touches"])
    report["duration_s"] = frame_count / FPS
    report["frame_count"] = frame_count
    if args.report:
        args.report.write_text(json.dumps(report, indent=2))
    print(f"Verified {len(plan['touches'])} contacts, "
          f"contact-pivot posing, {report.get('residual_proxy_overlap_samples', 0)} residual proxy overlaps, "
          f"{max_speed:g} m/s glove speed limit.")


if __name__ == "__main__":
    main()

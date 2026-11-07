"""Run in the saved Blender scene: inspect baked meshes and pinned contacts."""

import json
from pathlib import Path
import sys
import numpy as np
import bpy
from mathutils.bvhtree import BVHTree

folder = Path(sys.argv[sys.argv.index("--") + 1])
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hand_pose import HAND_PARTS, margins

motion = json.loads((folder / "motion.json").read_text())
plan = json.loads((folder / "plan.json").read_text())
poses = np.stack(
    [np.array(motion["hands"][s])[:, 7:9] for s in ("left", "right")], axis=1
)
points = np.stack(
    [np.array(motion["hands"][s])[:, :3] for s in ("left", "right")], axis=1
)
points = points[:, :, [0, 2, 1]] * np.array([1, -1, 1])
points -= np.array([0.405, 0.802, 0.708])
parts = [
    [(n, (sign * x, y, z), r) for n, (x, y, z), r in HAND_PARTS] for sign in (1, -1)
]
margin, _ = margins(points, poses, parts)
# Cover the full clip plus fractional frames at its tightest poses.
checks = {(float(f)) for f in range(0, len(points), 11)}
chosen = []
for f in np.argsort(margin):
    if all(abs(int(f) - g) > 4 for g in chosen):
        chosen.append(int(f))
    if len(chosen) == 20:
        break
for f in chosen:
    checks.update(f + t for t in (0, 0.25, 0.5, 0.75) if f + t <= len(points) - 1)
roots = [bpy.data.objects[s.title() + " index target"] for s in ("left", "right")]
geometry = []
for root in roots:
    entries = []
    for obj in root.children:
        if obj.type != "MESH":
            continue
        mesh = obj.data
        mesh.calc_loop_triangles()
        vertices = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", vertices)
        triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
        mesh.loop_triangles.foreach_get("vertices", triangles)
        entries.append((obj, vertices.reshape(-1, 3), triangles.reshape(-1, 3)))
    geometry.append(entries)
contact = {s: {} for s in ("left", "right")}
for touch in plan["touches"]:
    for f, xy in enumerate(touch["path_mm"], touch["down_frame"]):
        contact[touch["hand"]][f] = np.r_[np.array(xy) / 1000, 0.006]
intersections = []
drift = 0.0
floor = float("inf")
for sample in sorted(checks):
    timeline = 1 + sample / 2 - motion["audio_offset_ms"] * 30 / 1000
    bpy.context.scene.frame_set(int(timeline), subframe=timeline % 1)
    trees = []
    for h, side in enumerate(("left", "right")):
        vertices = []
        triangles = []
        offset = 0
        for obj, v, tri in geometry[h]:
            matrix = np.array(obj.matrix_world)
            world = v @ matrix[:3, :3].T + matrix[:3, 3]
            floor = min(floor, float(world[:, 2].min()) - 0.708)
            vertices.extend(world.tolist())
            triangles.extend((tri + offset).tolist())
            offset += len(v)
        trees.append(BVHTree.FromPolygons(vertices, triangles, all_triangles=True))
        f = int(sample)
        t = sample - f
        if f in contact[side] and (not t or f + 1 in contact[side]):
            expected = (
                contact[side][f]
                if not t
                else contact[side][f] * (1 - t) + contact[side][f + 1] * t
            )
            actual = np.array(roots[h].matrix_world.translation) - np.array(
                [0.405, 0.802, 0.708]
            )
            drift = max(drift, float(np.linalg.norm(actual - expected)))
    if trees[0].overlap(trees[1]):
        intersections.append(sample / 60)
report = {
    "checked_poses": len(checks),
    "mesh_intersection_poses": intersections,
    "maximum_contact_drift_m": drift,
    "minimum_mesh_height_above_glass_m": floor,
}
(folder / "mesh-check.json").write_text(json.dumps(report, indent=2))
print(report, flush=True)
assert not intersections, "Actual glove meshes intersect"
assert drift < 1e-6, "Baked touch pivot moved away from a contact"
assert floor > -0.0005, "Glove mesh penetrates the tablet"
# Representative real EEVEE output at a cross-hand arc and an air crossing.
scene = bpy.context.scene
scene.render.resolution_x = 640
scene.render.resolution_y = 480
scene.eevee.taa_render_samples = 12
for time in (4.5, 11.3, 18.3):
    if time * 60 >= len(points):
        continue
    scene.frame_set(int(1 + time * 30))
    scene.render.filepath = str((folder / f"pivot-{time:.1f}s.png").resolve())
    bpy.ops.render.render(write_still=True)

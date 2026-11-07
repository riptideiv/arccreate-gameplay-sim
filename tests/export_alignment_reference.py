"""Run in the saved Blender take to check the WebGL port against baked keys."""
import json
import math
from pathlib import Path
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view

folder = Path(sys.argv[sys.argv.index("--") + 1])
motion = json.loads((folder / "motion.json").read_text())
scene = bpy.context.scene
checks = []
length = len(motion["hands"]["left"])
for sample in sorted({0, 1, length - 1, *range(15, length - 1, 37)}):
    for fraction in (0, .25, .5, .75):
        if sample + fraction > length - 1:
            continue
        timeline = 1 + (sample + fraction) / 2 - motion["audio_offset_ms"] * 30 / 1000
        scene.frame_set(math.floor(timeline), subframe=timeline % 1)
        check = {"time": (sample + fraction) / 60 - motion["audio_offset_ms"] / 1000, "hands": {}}
        for side in ("left", "right"):
            root = bpy.data.objects[side.title() + " index target"]
            position, rotation, _ = root.matrix_world.decompose()
            ndc = world_to_camera_view(scene, scene.camera, position)
            check["hands"][side] = {"position": [position.x, position.z, -position.y],
                                    "quaternion": [rotation.x, rotation.z, -rotation.y, rotation.w],
                                    "screen": [ndc.x, ndc.y]}
        checks.append(check)
(folder / "alignment-reference.json").write_text(json.dumps({"settings": motion["settings"], "checks": checks}))
print("Exported", len(checks), "independent baked Blender alignment checks.")

"""Independent cuff-attachment audit in the actual saved Blender scene."""

import sys
import json
import math
from pathlib import Path
import bpy
import numpy as np

folder = Path(sys.argv[sys.argv.index("--") + 1])
motion = json.loads((folder / "motion.json").read_text())
scene = bpy.context.scene
length = len(motion["hands"]["left"])
vectors = []
cuffs = []
for side in ("Left", "Right"):
    root = bpy.data.objects[side + " index target"]
    cuffs.append(next(o for o in root.children if o.name.startswith("cuff")))
for quarter in range((length - 1) * 4 + 1):
    sample = quarter / 4
    frame = 1 + sample / 2 - motion["audio_offset_ms"] * 30 / 1000
    scene.frame_set(math.floor(frame), subframe=frame % 1)
    v = cuffs[1].matrix_world.translation - cuffs[0].matrix_world.translation
    vectors.append((v.x, v.y))
v = np.asarray(vectors)
angles = np.degrees(np.unwrap(np.arctan2(v[:, 1], v[:, 0])))
limit = motion["settings"].get("winding_limit_deg", 260)
assert np.max(abs(angles)) <= limit + 1e-3
report = {
    "verified": True,
    "checks": len(angles),
    "minimum_deg": float(angles.min()),
    "maximum_deg": float(angles.max()),
    "final_deg": float(angles[-1]),
    "limit_deg": limit,
    "anchor_objects": [o.name for o in cuffs],
}
(folder / "winding-blender-check.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))

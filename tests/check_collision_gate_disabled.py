"""Run in Blender: overlap must preserve touches, while speed checks still apply."""

from pathlib import Path
import runpy
from mathutils import Vector

namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "animate.py"))
paths = [[Vector((0, 0, 0)) for _ in range(3)] for _ in range(2)]
solved, poses, report = namespace["pose_sequence"](paths, [{0, 1, 2}, {0, 1, 2}])
assert report["verified"] and report["collision_verification"] is False
assert report["contact_preserving_pose_solver"]
assert report["residual_pad_overlap_samples"] > 0
assert (
    solved[0] == paths[0] and solved[1] == paths[1]
), "Overlapping contacts must not be moved or excluded"
contacts = [
    {"down_frame": 0, "path_mm": [[-85, -68]]},
    {"down_frame": 6, "path_mm": [[15, -68]]},
]
path = namespace["positions"](contacts, "left", 120, lift=0.006, max_speed=1.2)
assert max((b - a).length * 60 for a, b in zip(path, path[1:])) <= 1.2001
assert (path[0] - Vector((-0.085, -0.068, 0.006))).length < 1e-7
assert (path[6] - Vector((0.015, -0.068, 0.006))).length < 1e-7
fast = [[Vector((0, 0, 0)), Vector((1, 0, 0))] for _ in range(2)]
try:
    namespace["pose_sequence"](fast, [{0, 1}, {0, 1}], max_speed=4)
except ValueError as error:
    assert "move limit" in str(error)
else:
    raise AssertionError("Speed validation must remain enabled")
print(
    "PASS: collisions do not gate playback; contact positions and speed validation retained."
)

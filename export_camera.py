"""Apply the live camera settings to a copy of the baked scene for MP4 export."""
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Vector

settings_path, output = sys.argv[sys.argv.index('--') + 1:]
settings = json.loads(Path(settings_path).read_text())
target = Vector((.405, .802-.008, .708+.020))
distance = settings['camera_distance_m']
elevation, yaw = (math.radians(settings[key]) for key in ('camera_elevation_deg','camera_yaw_deg'))
eye = target + Vector((distance*math.cos(elevation)*math.sin(yaw),
                       -distance*math.cos(elevation)*math.cos(yaw), distance*math.sin(elevation)))
scene = bpy.context.scene
scene.camera.location = eye
scene.camera.rotation_euler = (target-eye).to_track_quat('-Z','Y').to_euler()
scene.camera.data.lens = settings['camera_lens_mm']
scene.render.resolution_x = 640 if settings['quality']=='draft' else 1024
scene.render.resolution_y = 480 if settings['quality']=='draft' else 768
bpy.ops.wm.save_as_mainfile(filepath=str(Path(output).resolve()))

"""Rebuild a completed take's presentation without touching motion or gameplay."""
import json
from pathlib import Path
import sys
import bpy

sys.path.insert(0,str(Path(__file__).resolve().parent))
from presentation_scene import apply, export_glb, revision

folder=Path(sys.argv[sys.argv.index('--')+1]).resolve()
identity=revision()
result=apply(folder)
blend=folder/f'presentation-{identity}.blend'
glb=folder/f'presentation-{identity}.glb'
bpy.ops.wm.save_as_mainfile(filepath=str(blend),relative_remap=True)
export_glb(glb)
result.update(blend=blend.name,glb=glb.name)
(folder/'presentation-result.json').write_text(json.dumps(result,indent=2))
print('Presentation scene ready:',identity)

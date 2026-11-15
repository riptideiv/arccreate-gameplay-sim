"""Validated, atomic completed-contact-plan cache. No posing or scene state."""
import hashlib
import json
import math
import os
from pathlib import Path
from uuid import uuid4

# These affect the downstream display/hand pose only. Unknown future settings
# remain in the key, so new planner inputs cannot silently reuse an old plan.
PRESENTATION_SETTINGS = frozenset(('chart','start_s','end_s','camera_distance_m','camera_elevation_deg',
    'camera_yaw_deg','camera_lens_mm','quality','width','height','clearance_mm','angular_limit_deg_s',
    'scroll_speed','song_delay_ms','hit_sounds','winding_limit_deg','allow_clipping'))
SCHEMA = 'rhythm-completed-plan/2'

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def implementation_version(root=None):
    root=Path(root or Path(__file__).resolve().parent)
    files=[root/'plan_excerpt.py',root/'plan_cache.py',root/'multifinger.py',
           *sorted((root/'vendor/arc-hand-order/src/arc_hand_order').rglob('*.py'))]
    native=root/'native-search'
    files += [p for name in ('Cargo.toml', 'Cargo.lock', 'pyproject.toml') if (p:=native/name).is_file()]
    files += sorted((native/'src').rglob('*.rs'))
    digest=hashlib.sha256()
    for f in files:
        digest.update(str(f.relative_to(root)).encode());digest.update(b'\0');digest.update(f.read_bytes())
    return digest.hexdigest()

def request_key(chart,start,end,settings,prefer_same_hand,excluded,avoid_contacts,*,search_backend='auto'):
    from plan_excerpt import device_path
    chart=Path(chart)
    request={'schema':SCHEMA,'chart_sha256':hashlib.sha256(chart.read_bytes()).hexdigest(),
        'chart_path':str(chart), 'device_sha256':hashlib.sha256(device_path(settings).read_bytes()).hexdigest(),
        'projection_version':implementation_version(),'window_ms':[start,end],
        'execution':execution_identity(search_backend),
        'settings':{k:v for k,v in (settings or {}).items() if k not in PRESENTATION_SETTINGS},
        'prefer_same_hand':prefer_same_hand,'excluded':sorted(set(excluded)),
        'avoid_contacts':list(avoid_contacts)}
    return hashlib.sha256(canonical(request)).hexdigest(),request

def valid_plan(plan,request):
    try:
        if plan['format']!='arc-hand-order.plan/1' or plan['chart']['sha256']!=request['chart_sha256']:return False
        if [plan['preview']['chart_start_ms'],plan['preview']['chart_end_ms']]!=request['window_ms']:return False
        multifinger = request['settings'].get('multifinger', False)
        for touch in plan['touches']:
            if touch['finger'] not in ((1, 2) if multifinger else (1,)) or touch['hand'] not in ('left','right'):return False
            if not 0<=touch['down_frame']<touch['up_frame'] or len(touch['path_mm'])!=touch['up_frame']-touch['down_frame']:return False
            if not all(len(xy)==2 and all(math.isfinite(v) for v in xy) for xy in touch['path_mm']):return False
        return all(key in plan for key in ('misses','cost','planning','device','stats')) and (
            multifinger or 'arm_crossings' in plan)
    except (KeyError,TypeError,ValueError):return False

def read(directory,key,request):
    try:
        entry=json.loads((Path(directory)/(key+'.json')).read_text())
        plan=entry['plan']
        if entry['schema']!=SCHEMA or entry['request']!=request:return None
        if hashlib.sha256(canonical(plan)).hexdigest()!=entry['plan_sha256']:return None
        return plan if valid_plan(plan,request) else None
    except (OSError,ValueError,KeyError,TypeError):return None

def write(directory,key,request,plan):
    if not valid_plan(plan,request):raise ValueError('Refusing to cache an incomplete contact plan')
    directory=Path(directory);temp=directory/(key+'.'+uuid4().hex+'.tmp')
    try:
        directory.mkdir(parents=True,exist_ok=True)
        entry={'schema':SCHEMA,'request':request,'plan_sha256':hashlib.sha256(canonical(plan)).hexdigest(),'plan':plan}
        with temp.open('wb') as stream:
            stream.write(canonical(entry));stream.flush();os.fsync(stream.fileno())
        os.replace(temp,directory/(key+'.json'))
    except OSError:
        pass # An unavailable optional cache must not make planning fail.
    finally:temp.unlink(missing_ok=True)


def execution_identity(backend):
    """Workers are deliberately absent: scheduling must not affect the plan."""
    from arc_hand_order.planning.execution import NATIVE_AUTO_ENABLED
    identity = {'requested_backend': backend, 'native_auto_enabled': NATIVE_AUTO_ENABLED}
    if backend == 'native' or (backend == 'auto' and NATIVE_AUTO_ENABLED):
        import importlib.util
        spec = importlib.util.find_spec('arc_hand_order_native')
        if spec is not None and spec.origin and Path(spec.origin).is_file():
            origin = Path(spec.origin)
            files = [origin]
            if spec.submodule_search_locations:
                files += sorted(p for directory in spec.submodule_search_locations
                                for p in Path(directory).rglob('*') if p.suffix in ('.so', '.pyd', '.dll'))
            digest = hashlib.sha256()
            for path in files:
                digest.update(path.name.encode()); digest.update(path.read_bytes())
            identity['native_binary_sha256'] = digest.hexdigest()
        else:
            identity['native_binary_sha256'] = None
    return identity

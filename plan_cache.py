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
    'camera_yaw_deg','camera_lens_mm','quality','width','height','lift_mm','clearance_mm','angular_limit_deg_s',
    'scroll_speed','song_delay_ms','hit_sounds','idle_speed_m_s'))
SCHEMA = 'rhythm-completed-plan/1'

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()

def implementation_version(root=None):
    root=Path(root or Path(__file__).resolve().parent)
    files=[root/'plan_excerpt.py',root/'plan_cache.py',*sorted((root/'vendor/arc-hand-order/src/arc_hand_order').rglob('*.py'))]
    digest=hashlib.sha256()
    for f in files:
        digest.update(str(f.relative_to(root)).encode());digest.update(b'\0');digest.update(f.read_bytes())
    return digest.hexdigest()

def request_key(chart,start,end,settings,prefer_same_hand,excluded,avoid_contacts):
    from arc_hand_order import api
    chart=Path(chart)
    request={'schema':SCHEMA,'chart_sha256':hashlib.sha256(chart.read_bytes()).hexdigest(),
        'chart_path':str(chart), 'device_sha256':hashlib.sha256(api.DEFAULT_DEVICE.read_bytes()).hexdigest(),
        'projection_version':implementation_version(),'window_ms':[start,end],
        'settings':{k:v for k,v in (settings or {}).items() if k not in PRESENTATION_SETTINGS},
        'prefer_same_hand':prefer_same_hand,'excluded':sorted(set(excluded)),
        'avoid_contacts':list(avoid_contacts)}
    return hashlib.sha256(canonical(request)).hexdigest(),request

def valid_plan(plan,request):
    try:
        if plan['format']!='arc-hand-order.plan/1' or plan['chart']['sha256']!=request['chart_sha256']:return False
        if [plan['preview']['chart_start_ms'],plan['preview']['chart_end_ms']]!=request['window_ms']:return False
        for touch in plan['touches']:
            if touch['finger']!=1 or touch['hand'] not in ('left','right'):return False
            if not 0<=touch['down_frame']<touch['up_frame'] or len(touch['path_mm'])!=touch['up_frame']-touch['down_frame']:return False
            if not all(len(xy)==2 and all(math.isfinite(v) for v in xy) for xy in touch['path_mm']):return False
        return all(key in plan for key in ('misses','cost','planning','device','stats'))
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

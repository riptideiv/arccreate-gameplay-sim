"""Sampled fingertip effort and whole-snake minimum-acceleration guides.

Coordinates are metres, time is seconds. Native judgement remains separate:
these are candidates, never an unvalidated post-animation visual filter.
"""
from functools import lru_cache
import math
import numpy as np

RATE = 60
ACCEL_REFERENCE = 5.0  # m/s²; fixed normalization, independent of the old flight threshold


def acceleration_energy(path, *, rate=RATE, rest_ends=True):
    p = np.asarray(path, dtype=float)
    if len(p) < 2:
        return 0.0
    if rest_ends:
        p = np.concatenate((p[:1], p, p[-1:]))
    a = np.diff(p, n=2, axis=0) * rate**2
    return float(np.sum(a*a) / (rate * ACCEL_REFERENCE**2))


def touch_energy(touch):
    if touch is None:
        return 0.0
    if '_acceleration_energy' not in touch.__dict__:
        prefix = touch.__dict__.get('_native_prefix')
        if prefix is not None and len(prefix.path) >= 2:
            # Remove the old terminal rest term and include both seam triples.
            terminal = math.dist(prefix.path[-1], prefix.path[-2])**2 * RATE**3 / ACCEL_REFERENCE**2
            tail = touch.path[len(prefix.path)-2:]
            touch.__dict__['_acceleration_energy'] = (touch_energy(prefix)-terminal
                + acceleration_energy(tail, rest_ends=False)
                + math.dist(touch.path[-1], touch.path[-2])**2 * RATE**3 / ACCEL_REFERENCE**2)
        else:
            touch.__dict__['_acceleration_energy'] = acceleration_energy(touch.path)
    return touch.__dict__['_acceleration_energy']


def transfer_energy(previous, current, speed_max):
    """Replace virtual endpoint rests by the sampled eased XY flight and its seams.

    Mirrors the playback's quintic/linear speed fallback in the screen plane.
    Lift/posing/idle detours are downstream and are not claimed as exact 3D costs.
    """
    if previous is None:
        return 0.0
    frames = current.down - (previous.up-1)
    if frames <= 0:
        return 0.0
    a, b = np.array(previous.last_xy), np.array(current.first_xy)
    t = np.linspace(0, 1, frames+1)
    eased = t**3 * (10-15*t+6*t*t)
    for linear in (0, .25, .5, .75, 1):
        u = (1-linear)*eased + linear*t
        flight = a + u[:, None]*(b-a)
        if np.linalg.norm(np.diff(flight, axis=0), axis=1).max(initial=0)*RATE <= speed_max+1e-4:
            break
    before = previous.path[-2] if len(previous.path)>1 else previous.last_xy
    after = current.path[1] if len(current.path)>1 else current.first_xy
    path = np.concatenate(([before], flight, [after]))
    rests = (math.dist(before,a)**2 + math.dist(b,after)**2)*RATE**3/ACCEL_REFERENCE**2
    return acceleration_energy(path, rest_ends=False)-rests


@lru_cache(maxsize=512)
def smooth_path(path, seconds):
    """O(n) pentadiagonal Cholesky: minimize ||p-guide||² + (seconds*60)^4 ||D²p||².

    Natural endpoints preserve straight lines; filtering the whole connected
    snake avoids restarting velocity at every scoring job. No SciPy dependency.
    """
    n = len(path)
    if n < 3:
        return tuple(path)
    w = (seconds*RATE)**4
    diag = np.ones(n)
    off1, off2 = np.zeros(n), np.zeros(n)
    for i in range(n-2):
        diag[i:i+3] += w*np.array((1,4,1))
        off1[i+1:i+3] -= 2*w
        off2[i+2] += w
    d, l1, l2 = np.zeros(n), np.zeros(n), np.zeros(n)
    rhs = np.asarray(path, dtype=float).copy()
    for i in range(n):
        if i>1:
            l2[i] = off2[i]/d[i-2]
        if i:
            l1[i] = (off1[i] - (l2[i]*l1[i-1] if i>1 else 0))/d[i-1]
        d[i] = math.sqrt(diag[i]-l1[i]**2-l2[i]**2)
        rhs[i] = (rhs[i] - (l1[i]*rhs[i-1] if i else 0) - (l2[i]*rhs[i-2] if i>1 else 0))/d[i]
    for i in range(n-1,-1,-1):
        rhs[i] = (rhs[i] - (l1[i+1]*rhs[i+1] if i+1<n else 0) - (l2[i+2]*rhs[i+2] if i+2<n else 0))/d[i]
    return tuple(map(tuple, rhs))


def smooth_guides(item, first, stop, offset, projection):
    from .arc_scoring import trace_position
    from .two_finger import frame_at_or_after
    cache = getattr(projection, '_smooth_guides', None)
    if cache is None:
        cache = {}
        object.__setattr__(projection, '_smooth_guides', cache)
    parent = item.parent_occupation_id or item.occupation_id
    key = (parent, offset)
    if key not in cache:
        begin = max(0, frame_at_or_after(item.start_ms, offset))
        end = max(begin+1, frame_at_or_after(item.end_ms+1, offset))
        centre = tuple(trace_position(item,f,offset,projection) for f in range(begin,end))
        cache[key] = (begin, tuple(smooth_path(centre,s) for s in (.04,.08,.16,.32)))
    begin, guides = cache[key]
    return tuple(tuple(g[min(max(f-begin,0),len(g)-1)] for f in range(first,stop)) for g in guides)


def motion_metrics(touches):
    acceleration = []
    energy = 0.0
    frames = 0
    for t in touches:
        p = np.asarray(t['path_mm'], dtype=float)/1000
        a = np.linalg.norm(np.diff(p,n=2,axis=0)*RATE**2,axis=1)
        acceleration.extend(a.tolist())
        energy += acceleration_energy(p)
        frames += len(a)
    return {'reference_m_s2': ACCEL_REFERENCE, 'held_peak_m_s2': max(acceleration,default=0),
        'held_rms_m_s2': math.sqrt(sum(x*x for x in acceleration)/max(1,frames)),
        'held_normalized_energy_s': energy,
        'scope': 'serialized fingertip XY; held RMS/peak exclude virtual endpoint rests'}

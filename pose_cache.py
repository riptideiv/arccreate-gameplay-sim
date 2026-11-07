"""Shared, validated cache of solved hand positions and wrist angles."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile

import numpy as np

SCHEMA = 'rhythm-hand-poses/1'
POSE_DEFAULTS = {'max_speed_m_s': 1.6, 'angular_limit_deg_s': 360,
                 'lift_mm': 8, 'clearance_mm': 1, 'idle_speed_m_s': 1}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def request_key(points, active, settings, root=None):
    root = Path(root or Path(__file__).resolve().parent)
    digest = hashlib.sha256(SCHEMA.encode())
    options = {k: float(settings.get(k, v)) for k, v in POSE_DEFAULTS.items()}
    digest.update(canonical(options).encode())
    for value, dtype in ((points, '<f8'), (active, '?')):
        array = np.asarray(value, dtype=dtype)
        digest.update(str(array.shape).encode()); digest.update(array.tobytes())
    # Conservative code invalidation includes all pose/path/geometry code. New
    # animation code can never silently load arrays from a different solver.
    for name in ('animate.py', 'hand_pose.py', 'motion_paths.py', 'pose_cache.py'):
        digest.update(name.encode()); digest.update((root / name).read_bytes())
    return digest.hexdigest()


def checksum(points, angles, report):
    digest = hashlib.sha256(canonical(report).encode())
    for value in (points, angles):
        array = np.asarray(value, dtype='<f8')
        digest.update(str(array.shape).encode()); digest.update(array.tobytes())
    return digest.hexdigest()


def valid(points, angles, report, base, active):
    return (isinstance(report, dict) and points.shape == base.shape and angles.shape == (*base.shape[:2], 2)
            and np.isfinite(points).all() and np.isfinite(angles).all()
            and report.get('verified') is True
            and np.allclose(points[active], base[active], rtol=0, atol=1e-9))


def read(directory, key, base, active):
    try:
        with np.load(Path(directory) / (key + '.npz'), allow_pickle=False) as entry:
            if str(entry['schema']) != SCHEMA or str(entry['signature']) != key:
                return None
            points, angles = entry['points'].copy(), entry['angles'].copy()
            report = json.loads(str(entry['report']))
            if str(entry['checksum']) != checksum(points, angles, report):
                return None
            if not valid(points, angles, report, base, active):
                return None
            return points, angles, report
    except (OSError, ValueError, KeyError, TypeError, EOFError, BadZipFile):
        return None


def write(directory, key, points, angles, report, base, active):
    if not valid(points, angles, report, base, active):
        raise ValueError('Refusing to cache unverified or contact-altering hand poses')
    directory = Path(directory)
    temporary = directory / (key + '.' + uuid4().hex + '.tmp')
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with temporary.open('wb') as out:
            np.savez(out, schema=SCHEMA, signature=key, points=points, angles=angles,
                     report=canonical(report), checksum=checksum(points, angles, report))
            out.flush(); os.fsync(out.fileno())
        os.replace(temporary, directory / (key + '.npz'))
    except OSError:
        pass  # An optional cache must not break a valid render.
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

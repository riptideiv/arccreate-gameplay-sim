"""Screen-space arm order carried between scoring ticks."""

from dataclasses import dataclass, replace
from functools import cached_property, lru_cache

import numpy as np

from .hand_travel import TIP, home_position, settle_frame, span

SIDES = ("left", "right")
EPSILON = 1e-5  # a touch with the same x does not reset the order


@dataclass(frozen=True)
class Crossing:
    start_frame: float
    end_frame: float | None
    top: str
    entered_at: int
    exited_at: int | None = None


def order(routes):
    return routes[-1].top if routes and routes[-1].end_frame is None else "uncrossed"


def prefix(routes, frame):
    keep = []
    for route in routes:
        if route.entered_at > frame:
            break
        if route.exited_at is None or route.exited_at > frame:
            keep.append(replace(route, end_frame=None, exited_at=None))
            break
        keep.append(route)
    return tuple(keep)


def passes(left, right, start, crossed):
    """Yield x-order changes and permitted top hands at the crossing plane.

    A projected path on the far side of the other touch (larger screen y)
    requires that hand to go over. If both hands pass, the far-side hand
    is one of the passers and must go over. Ties allow either route.
    """
    dx = right[start:, 0] - left[start:, 0]
    samples = np.flatnonzero(abs(dx[1:]) > EPSILON) + 1
    if not len(samples):
        return
    signs = dx[samples] < 0
    changes = samples[np.flatnonzero(signs != np.r_[crossed, signs[:-1]])]
    for i in changes:
        entering = dx[i] < 0
        delta = dx[i] - dx[i-1]
        u = float(np.clip(-dx[i-1] / delta, 0, 1)) if delta else 0.0
        i += start
        before, after = np.stack((left[i-1], right[i-1])), np.stack((left[i], right[i]))
        at = before + u * (after - before)
        toward = (after[:, 0] - before[:, 0]) * [1, -1] * (1 if entering else -1)
        above = int(at[1, 1] > at[0, 1])
        if abs(at[1, 1] - at[0, 1]) > EPSILON and toward[above] > EPSILON:
            tops = (SIDES[above],)
        else:
            preferred = int(at[1, 2] > at[0, 2])
            tops = (SIDES[preferred], SIDES[1-preferred])
        yield i, i - 1 + u, entering, tops


def locked_overpass(left, right, lock, start, end):
    """Detect overlap of the pad footprints while the upper pad is pinned."""
    first, last = int(np.ceil(start)), int(np.floor(end))
    # Restrict the distance check to the uncommitted part of this crossing.
    origin, stop = int(np.floor(start)), int(np.ceil(end)) + 1
    relative = left[origin:stop, :2] - right[origin:stop, :2]
    lock = lock[origin:stop]
    start, end = start-origin, end-origin
    first, last = first-origin, last-origin
    limit2 = (2*TIP - EPSILON)**2
    if np.any(lock[first:last+1] & (np.sum(relative[first:last+1]**2, axis=1) < limit2)):
        return True
    edges = np.arange(int(np.floor(start)), int(np.ceil(end)))
    edges = edges[lock[edges] & lock[edges+1]]
    if not len(edges):
        return False
    before = relative[edges]
    delta = relative[edges+1] - before
    u = -np.sum(before*delta, axis=1) / np.maximum(np.sum(delta**2, axis=1), 1e-16)
    u = np.clip(u, np.maximum(0, start-edges), np.minimum(1, end-edges))
    return bool(np.any(np.sum((before + u[:, None]*delta)**2, axis=1) < limit2))


def extend(routes, left, right, start, locks=None):
    """Process every crossing between two beam checkpoints."""
    options = (routes,)
    cursor = start

    def clear(history, end):
        top = order(history)
        return (locks is None or top == "uncrossed"
                or not locked_overpass(left, right, locks[SIDES.index(top)], cursor, end))

    for sample, frame, entering, tops in passes(left, right, start, order(routes) != "uncrossed"):
        if entering:
            options = tuple(history + (Crossing(frame, None, top, sample),)
                            for history in options for top in tops)
        else:
            # The arms must stay in their selected order through the exit.
            options = tuple(history[:-1] + (replace(history[-1], end_frame=frame, exited_at=sample),)
                            for history in options if order(history) in tops and clear(history, frame))
            if not options:
                break
            # Keep both orders for the just-completed crossing. Earlier
            # choices that arrive at the same topology have the same future.
            by_top = {}
            for history in options:
                by_top.setdefault(history[-1].top, history)
            options = tuple(by_top.values())
        cursor = frame
    return tuple(history for history in options if clear(history, len(left)-1))


@dataclass(frozen=True, eq=False)
class HandPath:
    points: np.ndarray
    anchor: int  # contact immediately before the current touch, or the home frame
    locked: np.ndarray | None = None


@dataclass(frozen=True)
class ArmState:
    paths: tuple[HandPath, HandPath]
    routes: tuple[Crossing, ...] = ()

    @property
    def through(self):
        return min(len(p.points) for p in self.paths) - 1

    @cached_property
    def key(self):
        # Keep the order at the common frontier and the geometry which later
        # contacts can extend or revise. Earlier finished crossings can merge.
        anchor = min(path.anchor for path in self.paths)
        pending = tuple((path.anchor, path.points[anchor:].tobytes(),
                         path.locked[anchor:].tobytes() if path.locked is not None else b"")
                        for path in self.paths)
        editable = tuple(c for c in self.routes if c.exited_at is None or c.exited_at > anchor)
        return order(self.routes), editable, pending


class CrossingGraph:
    def __init__(self, lift, speed, idle_speed, requested):
        self.lift = lift
        self.speed = speed
        self.idle_speed = min(speed, idle_speed)
        self.requested = requested
        self.homes = tuple(home_position(side, lift) for side in SIDES)
        self.initial = ArmState(tuple(HandPath(home[None], 0, np.zeros(1, dtype=bool)) for home in self.homes))
        self.project = lru_cache(maxsize=512)(self.project)

    def project(self, previous, touch, new, hand):
        # A newly assigned touch closes a free span. A modified touch can
        # replace its points all the way back to its first sample.
        anchor = len(previous.points) - 1 if new else previous.anchor
        down = max(touch.down, 0)
        if down < anchor:
            return None
        points = np.zeros((touch.up-down, 3))
        points[:, :2] = np.round(np.asarray(touch.path[down-touch.down:]) * 1000, 2) / 1000
        points[:, 2] = TIP
        gap = span(previous.points[anchor], points[0], down-anchor, self.homes[hand],
                   self.lift, self.speed, self.idle_speed)
        locked = np.r_[previous.locked[:anchor], np.zeros(len(gap)-1, dtype=bool),
                       np.ones(len(points), dtype=bool)]
        if down > anchor:
            locked[anchor] = previous.locked[anchor]
        return HandPath(np.concatenate((previous.points[:anchor], gap[:-1], points)), anchor, locked)

    def _advance(self, previous, paths):
        through = min(len(path.points) for path in paths) - 1
        shared = min(previous.through, through)
        changed = []
        for old, new in zip(previous.paths, paths):
            if old is not new:
                delta = np.any(old.points[:shared+1, :2] != new.points[:shared+1, :2], axis=1)
                diff = np.flatnonzero(delta | (old.locked[:shared+1] != new.locked[:shared+1]))
                if len(diff):
                    changed.append(int(diff[0]))
        last_fixed = min(changed) - 1 if changed else shared
        left, right = (path.points[:through+1] for path in paths)
        locks = tuple(path.locked[:through+1] for path in paths)
        if last_fixed < 0:
            # A touch can start on frame 0. Approach it from the neutral order.
            previous_point = tuple(home[None] for home in self.homes)
            left, right = (np.concatenate((home, points))
                           for home, points in zip(previous_point, (left, right)))
            routes = tuple(tuple(replace(c, start_frame=max(0., c.start_frame-1),
                                         end_frame=None if c.end_frame is None else c.end_frame-1,
                                         entered_at=c.entered_at-1,
                                         exited_at=None if c.exited_at is None else c.exited_at-1)
                                 for c in history)
                           for history in extend((), left, right, 0, tuple(np.r_[False, mask] for mask in locks)))
        else:
            routes = extend(prefix(previous.routes, last_fixed), left, right, last_fixed, locks)
        return tuple(ArmState(paths, history) for history in routes)

    def step(self, previous, changes):
        paths = list(previous.paths)
        for slot, touch, kind in changes:
            if touch.up <= 0:
                continue
            hand = slot // 2
            projected = self.project(paths[hand], touch, kind == "new", hand)
            if projected is None:
                return ()
            paths[hand] = projected
        return self._advance(previous, tuple(paths))

    def finish(self, state):
        last_contact = max(len(path.points) for path in state.paths)
        frame = settle_frame(last_contact, self.requested,
                             ((side, path.points[-1]) for side, path in zip(SIDES, state.paths)),
                             self.lift, self.idle_speed)
        paths = tuple(HandPath(np.concatenate((
            path.points[:-1], span(path.points[-1], home, frame-len(path.points)+1,
                                   home, self.lift, self.speed, self.idle_speed)
        )), path.anchor, np.r_[path.locked, np.zeros(frame+1-len(path.points), dtype=bool)])
            for path, home in zip(state.paths, self.homes))
        return self._advance(state, paths)

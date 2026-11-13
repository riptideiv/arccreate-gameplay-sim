"""Screen-space arm order carried between scoring ticks."""

from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from math import ceil

import numpy as np

from .hand_travel import RATE, TIP, home_position, span

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


def near_side_travel(direct, other, lock, swap, speed, hand, entering):
    """Find compact and wide curves past the near side of a moving peer."""
    samples = np.arange(len(direct), dtype=float)
    t = samples / samples[-1]
    line = direct[0] + (direct[-1] - direct[0]) * t[:, None]
    bases = [direct + blend*(line - direct) for blend in (0., .5, 1.)]
    # Slowing the lateral component can let a hand get around a pinned
    # fingertip before swapping sides. Y and Z remain independent.
    for progress in (t**1.5, t**2, 1-(1-t)**1.5):
        base = line.copy()
        base[:, 0] = direct[0, 0] + (direct[-1, 0]-direct[0, 0])*progress
        bases.append(base)

    best = [None, None]
    for base in bases:
        delta = (base[:len(other), 0] - other[:, 0]) * (-1 if hand == 0 else 1)
        edges = np.flatnonzero((delta[1:] < 0) != (delta[:-1] < 0))
        matching = [i for i in edges if (delta[i+1] < 0) == entering]
        if not matching:
            continue
        edge = min(matching, key=lambda i: abs(i + .5 - swap))

        def crossing_at(i):
            return i - delta[i] / (delta[i+1] - delta[i])

        crossing = crossing_at(edge)
        if not 0 < crossing < samples[-1]:
            continue
        fraction = crossing-edge
        if entering:
            adjacent = next((i for i in edges if i > edge), None)
            start, end = crossing, crossing_at(adjacent) if adjacent is not None else len(other)-1
        else:
            adjacent = next((i for i in reversed(edges) if i < edge), None)
            start, end = crossing_at(adjacent) if adjacent is not None else 0, crossing
        separation = ((base[edge, 1] - other[edge, 1]) * (1-fraction)
                      + (base[edge+1, 1] - other[edge+1, 1]) * fraction)
        ramp = np.clip(np.minimum(samples/crossing, (samples[-1]-samples)/(samples[-1]-crossing)), 0, 1)
        for power in (1.35, 1.08):
            bump = 1 - (1 - ramp)**power
            height_at_swap = bump[edge]*(1-fraction) + bump[edge+1]*fraction
            if height_at_swap <= EPSILON:
                continue
            for width, clearances in enumerate(((.010, .006, .002), (.020, .014))):
                for clearance in clearances:
                    depth = (separation + clearance) / height_at_swap
                    if depth <= 0:
                        continue
                    path = base.copy()
                    path[:, 1] -= depth*bump
                    path[[0, -1]] = direct[[0, -1]]
                    lengths = np.linalg.norm(np.diff(path, axis=0), axis=1)
                    if lengths.max()*RATE > speed + .0001:
                        continue
                    if locked_overpass(path[:len(other)], other, lock, start, end):
                        continue
                    rank = (-clearance, lengths.sum())
                    if best[width] is None or rank < best[width][0]:
                        best[width] = (rank, path)
                    break
    return tuple(choice[1] for choice in best if choice is not None)


@dataclass(frozen=True)
class FreeTravel:
    start: int
    end: int
    extra_distance_m: float = 0.
    curved: bool = False


@dataclass(frozen=True, eq=False)
class HandPath:
    points: np.ndarray
    anchor: int  # contact immediately before the current touch, or the home frame
    locked: np.ndarray | None = None
    travel: tuple[FreeTravel, ...] = ()


@dataclass(frozen=True)
class ArmState:
    paths: tuple[HandPath, HandPath]
    routes: tuple[Crossing, ...] = ()

    @property
    def through(self):
        return min(len(p.points) for p in self.paths) - 1

    @cached_property
    def crossed_seconds(self):
        """Integrate sampled crossing state through the common path frontier."""
        through = self.through
        if through == 0:
            return 0.0
        frames = 0.0
        for route in self.routes:
            first = ceil(route.start_frame)
            stop = through + 1 if route.end_frame is None else min(through + 1, ceil(route.end_frame))
            if first < stop:
                # Each interior sample contributes half of both adjoining intervals.
                frames += stop - first - 0.5 * ((first == 0) + (stop == through + 1))
        return frames / RATE

    @cached_property
    def extra_distance_m(self):
        return sum(gap.extra_distance_m for path in self.paths for gap in path.travel)

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
        travel = tuple(gap for gap in previous.travel if gap.end <= anchor)
        retained = next((gap for gap in previous.travel if gap.start == anchor
                         and gap.end == down and gap.curved
                         and np.array_equal(previous.points[down], points[0])), None)
        if retained is not None:
            gap = previous.points[anchor:down+1]
        else:
            gap = span(previous.points[anchor], points[0], down-anchor, self.homes[hand],
                       self.lift, self.speed, self.idle_speed)
        if down - anchor >= 2 and previous.locked[anchor]:
            travel += (retained if retained is not None else FreeTravel(anchor, down),)
        locked = np.r_[previous.locked[:anchor], np.zeros(len(gap)-1, dtype=bool),
                       np.ones(len(points), dtype=bool)]
        if down > anchor:
            locked[anchor] = previous.locked[anchor]
        return HandPath(np.concatenate((previous.points[:anchor], gap[:-1], points)), anchor, locked, travel)

    def _checkpoint(self, previous, paths):
        through = min(len(path.points) for path in paths) - 1
        shared = min(previous.through, through)
        changed = []
        for old, new in zip(previous.paths, paths):
            if old is not new:
                delta = np.any(old.points[:shared+1, :2] != new.points[:shared+1, :2], axis=1)
                diff = np.flatnonzero(delta | (old.locked[:shared+1] != new.locked[:shared+1]))
                if len(diff):
                    changed.append(int(diff[0]))
        return (min(changed) - 1 if changed else shared), through

    def _advance(self, previous, paths):
        last_fixed, through = self._checkpoint(previous, paths)
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

    def _near_side_paths(self, previous, paths):
        last_fixed, through = self._checkpoint(previous, paths)
        start = max(0, last_fixed)
        left, right = (path.points[:through+1] for path in paths)
        crossed = (left[0, 0] > right[0, 0] if last_fixed < 0
                   else order(prefix(previous.routes, last_fixed)) != "uncrossed")
        tried = set()
        for _, frame, entering, tops in passes(left, right, start, crossed):
            if len(tops) != 1:
                continue
            h = SIDES.index(tops[0])
            path, other = paths[h], paths[1-h]
            for gap in reversed(path.travel):
                a, b = gap.start, gap.end
                if a >= frame:
                    continue
                if b <= frame:
                    break
                key = (h, a, b)
                if key in tried or gap.curved or path.locked[a+1:b].any():
                    break
                tried.add(key)
                direct = path.points[a:b+1]
                if np.any(np.all(direct == self.homes[h], axis=1)):
                    break
                stop = min(b, through)+1
                direct_distance = np.linalg.norm(np.diff(direct, axis=0), axis=1).sum()
                for curved in near_side_travel(direct, other.points[a:stop], other.locked[a:stop],
                                               frame-a, self.speed, h, entering):
                    points = path.points.copy()
                    points[a:b+1] = curved
                    extra = np.linalg.norm(np.diff(curved, axis=0), axis=1).sum() - direct_distance
                    selected = replace(gap, extra_distance_m=max(float(extra), 0.), curved=True)
                    travel = tuple(selected if item == gap else item for item in path.travel)
                    result = list(paths)
                    result[h] = replace(path, points=points, travel=travel)
                    yield tuple(result)
                break

    def _routes(self, previous, paths):
        # A newly fixed gap can reveal a pass in either hand's pending travel.
        # Keep a bounded set of combinations when several passes appear at once.
        pending = [paths]
        seen = {()}
        result = []
        for candidate in pending:
            result.extend(self._advance(previous, candidate))
            if len(pending) >= 8:
                continue
            for alternative in self._near_side_paths(previous, candidate):
                key = tuple((h, gap.start, path.points[gap.start:gap.end+1].tobytes())
                            for h, path in enumerate(alternative) for gap in path.travel
                            if gap.curved)
                if key not in seen:
                    seen.add(key)
                    pending.append(alternative)
                    if len(pending) == 8:
                        break
        return tuple(result)

    @staticmethod
    def travel_segments(state):
        return tuple({"hand": side, "start_frame": gap.start,
                      "path_mm": (path.points[gap.start:gap.end+1] * 1000).tolist()}
                     for side, path in zip(SIDES, state.paths)
                     for gap in path.travel if gap.curved)

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
        return self._routes(previous, tuple(paths))

    def finish(self, state):
        """Extend each hand in place through the end of the excerpt."""
        frame = max(self.requested, *(len(path.points) for path in state.paths))
        paths = []
        for path in state.paths:
            padding = frame + 1 - len(path.points)
            points = np.concatenate((path.points, np.repeat(path.points[-1:], padding, axis=0)))
            locked = np.pad(path.locked, (0, padding))
            paths.append(replace(path, points=points, locked=locked))
        return self._routes(state, tuple(paths))

"""Arc connection topology as the chart loader of ArcCreate (https://github.com/Arcthesia/ArcCreate) builds it,
including branches and colour changes.

That loader walks an interval tree rather than a list sorted by note ID, and its sort is an unstable introsort. Both
orders decide same-colour ties, so they are reproduced here. This does not change touch sharing.
"""
from __future__ import annotations

from dataclasses import dataclass
import struct

from arc_hand_order.schemas.chart_ir import ArcChain, ArcEvent


def introsort_order(values, key):
    """Introsort order, as .NET's List.Sort gives it, including its equal-key swaps."""
    a = list(values)

    def swap(i, j):
        a[i], a[j] = a[j], a[i]

    def order(i, j):
        if i != j and key(a[i]) > key(a[j]):
            swap(i, j)

    def down_heap(i, n, lo):
        item = a[lo+i-1]
        while i <= n//2:
            child = 2*i
            if child < n and key(a[lo+child-1]) < key(a[lo+child]):
                child += 1
            if key(item) >= key(a[lo+child-1]):
                break
            a[lo+i-1] = a[lo+child-1]
            i = child
        a[lo+i-1] = item

    def intro(lo, hi, depth):
        while hi > lo:
            size = hi-lo+1
            if size <= 16:
                if size == 2:
                    order(lo, hi)
                elif size == 3:
                    order(lo, hi-1); order(lo, hi); order(hi-1, hi)
                else:
                    for i in range(lo, hi):
                        item, j = a[i+1], i
                        while j >= lo and key(item) < key(a[j]):
                            a[j+1] = a[j]
                            j -= 1
                        a[j+1] = item
                return
            if depth == 0:
                for i in range(size//2, 0, -1):
                    down_heap(i, size, lo)
                for i in range(size, 1, -1):
                    swap(lo, lo+i-1)
                    down_heap(1, i-1, lo)
                return
            depth -= 1
            mid = lo+(hi-lo)//2
            order(lo, mid); order(lo, hi); order(mid, hi)
            pivot = a[mid]
            swap(mid, hi-1)
            left, right = lo, hi-1
            while left < right:
                left += 1
                while key(a[left]) < key(pivot):
                    left += 1
                right -= 1
                while key(pivot) < key(a[right]):
                    right -= 1
                if left >= right:
                    break
                swap(left, right)
            swap(left, hi-1)
            intro(left+1, hi, depth)
            hi = left-1

    if len(a) > 1:
        intro(0, len(a)-1, 2*len(a).bit_length())
    return a


@dataclass
class _Tree:
    center: float
    items: list
    left: _Tree | None = None
    right: _Tree | None = None

    @classmethod
    def build(cls, arcs):
        center = 0.
        for arc in arcs:
            center += arc.start_time_ms/(2*len(arcs))
            center += arc.end_time_ms/(2*len(arcs))
        left, middle, right = [], [], []
        for arc in arcs:
            (left if arc.end_time_ms < center else right if arc.start_time_ms > center else middle).append(arc)
        if not middle:
            # The interval tree's rounding and empty-centre guard.
            return cls(center, introsort_order(arcs, lambda a: (a.start_time_ms, a.end_time_ms)))
        return cls(center, introsort_order(middle, lambda a: (a.start_time_ms, a.end_time_ms)),
                   cls.build(left) if left else None, cls.build(right) if right else None)

    def query(self, low, high):
        stack = [self]
        while stack:
            node = stack.pop()
            for arc in node.items:
                if arc.start_time_ms > high:
                    break
                if low <= arc.end_time_ms:
                    yield arc
            if node.left is not None and low < node.center:
                stack.append(node.left)
            if node.right is not None and high > node.center:
                stack.append(node.right)


def _float32(point):
    return struct.unpack('<ff', struct.pack('<ff', *point))


def is_geometrically_connected(first, second):
    return (first.event_id != second.event_id and abs(first.end_time_ms-second.start_time_ms) <= 1
            and _float32(first.end_aff_xy) == _float32(second.start_aff_xy)
            and first.is_guideline == second.is_guideline)


def connect_arcs(events, groups):
    """Populate every candidate edge and the selected directed links.

    Arctaps are left out of the loader's global sort. Timing groups load in
    order; only the current and earlier groups are visible when a group's arcs
    are linked.
    """
    arcs = [e for e in events if isinstance(e, ArcEvent)]
    by_start = {}
    for arc in arcs:
        arc.connected_event_ids = []
        arc.chain = ArcChain()
        by_start.setdefault(arc.start_time_ms, []).append(arc)
    edge_count = 0
    for first in arcs:
        for time in range(first.end_time_ms-1, first.end_time_ms+2):
            for second in by_start.get(time, []):
                if is_geometrically_connected(first, second):
                    first.connected_event_ids.append(second.event_id)
                    second.connected_event_ids.append(first.event_id)
                    edge_count += 1
    ordered = introsort_order([e for e in events if e.type != 'arctap'], lambda e: e.start_time_ms)
    loaded = []
    for group in groups:
        owned = [e for e in ordered if isinstance(e, ArcEvent) and e.timinggroup_id == group.group_id]
        loaded.append(_Tree.build(owned))
        for arc in owned:
            for tree in loaded:
                for previous in tree.query(arc.start_time_ms-1, arc.start_time_ms+1):
                    if is_geometrically_connected(previous, arc):
                        if arc.chain.previous_event_id is None or arc.color == previous.color:
                            arc.chain.previous_event_id = previous.event_id
                        if previous.chain.next_event_id is None or previous.color == arc.color:
                            previous.chain.next_event_id = arc.event_id
            for tree in loaded:
                for following in tree.query(arc.end_time_ms-1, arc.end_time_ms+1):
                    if is_geometrically_connected(arc, following):
                        if arc.chain.next_event_id is None or arc.color == following.color:
                            arc.chain.next_event_id = following.event_id
                        if following.chain.previous_event_id is None or following.color == arc.color:
                            following.chain.previous_event_id = arc.event_id
    for arc in arcs:
        arc.connected_event_ids = sorted(set(arc.connected_event_ids))
    return edge_count


def is_selected_continuation(first, second):
    """A reciprocal path can reuse contact; other branches remain separate."""
    if (second.event_id not in first.connected_event_ids
            or first.event_id not in second.connected_event_ids):
        return False
    if first.chain is not None and second.chain is not None:
        return (first.chain.next_event_id == second.event_id
                and second.chain.previous_event_id == first.event_id)
    # Without chosen links, arcs of one colour connect.
    return first.color == second.color

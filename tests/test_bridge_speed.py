"""A shared scoring contact can invalidate an already constructed bridge."""

from pathlib import Path
from types import SimpleNamespace as N
import math
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order.planning.multi_finger import _Graph


def test_shared_contact_does_not_allow_a_speed_spike_on_return_to_a_bridge():
    graph = object.__new__(_Graph)
    graph.options = N(speed_max_m_s=4, scoring_arcs=False)
    graph.multi = N(accel_max_m_s2=0, share_jump_max_m=0)
    graph._anchor = lambda live, f: live[0].occupation.points[f]
    graph.point = lambda occupation, f: occupation.points[f]
    graph._intern = lambda parts, bridges, path: path

    def part(i, start, stop, points):
        return N(
            start=start,
            stop=stop,
            candidate=N(id=i, occupation=N(kind="chain", points=points)),
        )

    parts = (
        part(0, 0, 2, {0: (0, 0), 1: (0, 0)}),
        part(1, 2, 4, {2: (0.05, 0), 3: (0.10, 0)}),
        part(2, 6, 7, {6: (0, 0)}),
    )
    # Individual live-frame moves are 3 m/s, but returning from the
    # intervening shared tick to the old bridge anchor jumps at 6 m/s.
    assert graph._compose(parts, ((2, 6),), None) is None
    graph.options = N(speed_max_m_s=7, scoring_arcs=False)
    path = graph._compose(parts, ((2, 6),), None)
    assert math.isclose(max(math.dist(a, b) * 60 for a, b in zip(path, path[1:])), 6)

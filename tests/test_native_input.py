"""Regression cases captured from the native 40–100 s miss audit."""

from pathlib import Path
from types import SimpleNamespace as N
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arc_hand_order import api
from arc_hand_order.planning.native_input import (
    CameraEnvelope,
    ArcInputGeometry,
    InputPoint,
)
from arc_hand_order.planning.projection import GameplayProjection
from arc_hand_order.planning.multi_finger import _Graph, Touch


def projection():
    return GameplayProjection(api.load_device())


def test_native_screen_only_overlap_triggers_grace():
    # ArcCreate starts grace at 70.033 s despite >250 game units of Y separation.
    a, b = (381.7263, 119.467545), (406.254852, 431.199982)
    assert abs(a[1] - b[1]) > 250
    assert CameraEnvelope(projection()).overlaps(a, b)


def test_camera_envelope_rejects_the_previously_false_edge_catch():
    q = InputPoint((0.03416, -0.02436), projection())
    target = (436.792572, 511.0649)
    assert abs(q[0] - target[0]) < 182 and abs(q[1] - target[1]) < 242
    assert not q.collides(*target, certain=True, inset=8)
    # Independently observed native touch under camera roll at the same point.
    native = (248.335, 254.687317)
    for k in range(2):
        assert min(p[k] for p in q.samples) <= native[k] <= max(p[k] for p in q.samples)


def test_screen_grace_clears_a_held_fingers_old_colour_assignment():
    p = projection()

    def position(colour, time):
        if 500 <= time <= 600:
            return ((406.254852, 431.199982), (381.7263, 119.467545))[colour]
        return ((-350, 100), (350, 500))[colour]

    occupations = [
        N(
            kind="chain",
            colour=c,
            notes=(N(start=0, end=2000, position=lambda t, c=c: position(c, t)),),
        )
        for c in range(2)
    ]
    geometry = ArcInputGeometry(occupations, p, 0)
    graph = object.__new__(_Graph)
    graph.options = N(scoring_arcs=True)
    graph.projection = p
    graph.bindings = {}
    graph.graced = geometry.graced
    graph.native_frame_regions = geometry.regions
    path = tuple(
        p.judgement_to_device(*((-350, 100) if f < 48 else (350, 500)))
        for f in range(121)
    )
    touch = Touch(0, (), (), 0, len(path), path, 0)
    bindings = graph.bound_frames(touch)
    assert 10 in bindings[0]
    assert 80 in bindings[1] and 80 not in bindings[0]

"""Named planner presets.

``BASE`` is the two-finger planner policy both presets build on: point presses within 25 ms of the note, every arc and
hold held from start to end, 4 air frames after a held contact, and the colour, crossing, movement, distance, apart
and repeat costs. Its audio offset does not matter: ``plan_multi_finger`` takes the chart's own.

``ON4`` and ``ON5`` are the multi-finger options on top of it:

* on4: bridges over dotted arcs, shared touches, second fingers, meeting swaps, covered holds and arcs, steady jitter
  arcs and the fast-run (jack) cost.
* on5 (``multi_finger.RECOMMENDED``): on4 plus the arc-end takeover cost, cheaper clear layered crossings, enveloped
  arc pieces, the 35 mm share jump, the 75 m/s^2 acceleration limit, the side-by-side cost, sky notes aimed where
  drawn, jump slides, the 0.18 s repeat gap and second-finger repeats.
"""
from __future__ import annotations

from .planning.multi_finger import RECOMMENDED, MultiFingerOptions
from .planning.two_finger import PlannerOptions

BASE = PlannerOptions(
    offsets_ms=tuple(range(-25, 26)), point_contact_frames=2, min_air_frames=1, speed_max_m_s=3.0,
    speed_comfort_m_s=0.5, colour_weight=20.0, crossing_weight=1.0, movement_weight=1.0, handover_weight=1.0,
    apart_weight=10.0, apart_margin_m=0.03, apart_memory_s=0.5, repeat_weight=5.0, repeat_comfort_s=0.25,
    point_window_ms=25, distance_weight=5.0, full_arc_hold=True, partial_hold_weight=1000.0, full_ground_hold=True,
    partial_ground_hold_weight=500.0, held_release_air_frames=4, held_release_air_kinds=("chain", "hold"),
    crossing_pass_s=0.0, crossing_pass_margin_m=0.005)

ON4 = MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True, meeting_swaps=True,
                         covered_holds=True, covered_arcs=True, steady_arcs=True, jack_weight=20.0)

ON5 = RECOMMENDED

PRESETS = {"on4": ON4, "on5": ON5}

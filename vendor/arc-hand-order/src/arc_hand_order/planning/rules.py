"""The contact rules the planner plans to, and a chart's playable notes in their units.

Positions are game units, 100 per world unit (``projection``): sky input on the vertical plane through the judgement
line, where aff (x, y) is at x = 850 * x - 425 and y = 100 + 450 * y; ground input on the track plane, x across the
track and z along it. Times are chart milliseconds. The values below are planning parameters.

* A tap takes a touch that begins in its lane within ``POINT_WINDOW_MS`` of its time. The lanes are split at x = -425,
  0 and 425, the outer two open to the side, for |z| < ``LANE_HALF_DEPTH``.
* A sky note takes a touch that begins inside its rectangle: ``SKY_TAP_X`` to either side, ``SKY_TAP_DOWN`` below and
  ``SKY_TAP_UP`` above, the touch's height first held to [``SKY_FLOOR``, ``SKY_INPUT_HEIGHT``].
* A hold is pressed by a touch that begins in its lane from ``HOLD_EARLY_MS`` before its start until its end, and is
  then kept by any touch in its lane.
* An arc is kept by a touch inside ``HITBOX_X`` by ``HITBOX_Y`` around its point that its colour takes. A colour
  holding no touch binds the first one, in press order, found inside the hitbox of one of its arcs and holding no
  other colour, and then takes only that touch until it lifts. While two arcs of different colours are closer than
  ``GRACE_DISTANCE``, and until ``GRACE_MS`` after the last such frame, both colours take any touch and hold none.
  Colour 3 takes any touch.
* Holds and arcs have ticks (``build_ticks``) every half beat, every beat from ``FULL_BEAT_BPM`` up. Contact settles a
  tick from half an interval before it; without contact the tick is missed min(2 intervals, ``MAX_EXPIRY_MS``)
  after it.
* Arcs whose ends meet within ``JOIN_MS``, less than ``JOIN_X`` apart in aff x and at one height, are joined.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Callable

from arc_hand_order.chart.interpolation import arc_position

POINT_WINDOW_MS = 50
LANE_HALF_DEPTH = 500.0
SKY_TAP_X, SKY_TAP_DOWN, SKY_TAP_UP = 302.0, 310.0, 250.0
SKY_FLOOR, SKY_INPUT_HEIGHT = 100.0, 550.0
HOLD_EARLY_MS = 100
HITBOX_X, HITBOX_Y = 190.0, 250.0
GRACE_DISTANCE, GRACE_MS = 200.0, 500
FULL_BEAT_BPM = 255.0
MAX_EXPIRY_MS = 500.0
JOIN_MS, JOIN_X = 9, 0.1


def game_xy(aff_xy) -> tuple[float, float]:
    return 850.0 * aff_xy[0] - 425.0, 100.0 + 450.0 * aff_xy[1]


def tick_interval(bpm: float) -> float:
    """Milliseconds between ticks at this BPM: half a beat, a whole beat from FULL_BEAT_BPM up. A zero BPM has no
    regular ticks (an infinite interval): its notes get the one at their middle."""
    bpm = abs(bpm)
    if not math.isfinite(bpm):
        raise ValueError("tick interval needs a finite BPM")
    if bpm == 0:
        return math.inf
    return 60000.0 / bpm / (1.0 if bpm >= FULL_BEAT_BPM else 2.0)


def build_ticks(start: int, end: int, interval: float, has_predecessor: bool, has_successor: bool) -> list[list[int]]:
    """[time, weight] of each tick: one every interval from the start, none at the start of an arc no other arc joins
    into, and one at the middle of a note too short for any. When an arc joins into another and an interval ends
    within 2 ms of its end, its last tick goes and the one before counts twice."""
    duration = end - start
    count = int(duration / interval)
    ticks = []
    for index in range(0 if has_predecessor else 1, count):
        time = int(index * interval + start)
        if time < end:
            ticks.append([time, 1])
    if not ticks and duration != 0:
        ticks.append([int(start + duration * 0.5), 1])
    if has_successor and len(ticks) >= 2 and count - 1 == int((duration - 2) / interval):
        ticks.pop()
        ticks[-1][1] = 2
    return ticks


def expiry_ms(interval: float) -> float:
    """How long after a tick contact can still settle it."""
    return min(interval * 2.0, MAX_EXPIRY_MS)


def touch_lane(x: float, z: float) -> int | None:
    """The lane a ground touch at (x, z) counts for; None beyond LANE_HALF_DEPTH."""
    if not -LANE_HALF_DEPTH < z < LANE_HALF_DEPTH:
        return None
    return 1 if x < -425.0 else 2 if x < 0.0 else 3 if x < 425.0 else 4


def sky_contact(touch_x: float, touch_y: float, note_x: float, note_y: float) -> bool:
    height = min(max(touch_y, SKY_FLOOR), SKY_INPUT_HEIGHT)
    return note_x - SKY_TAP_X < touch_x < note_x + SKY_TAP_X and note_y - SKY_TAP_DOWN < height < note_y + SKY_TAP_UP


def arc_contact(touch_x: float, touch_y: float, arc_x: float, arc_y: float) -> bool:
    return arc_x - HITBOX_X < touch_x < arc_x + HITBOX_X and arc_y - HITBOX_Y < touch_y < arc_y + HITBOX_Y


@dataclass
class Tap:
    time: int
    lane: int
    label: str = ""


@dataclass
class SkyTap:
    time: int
    x: float
    y: float
    label: str = ""


@dataclass
class Hold:
    start: int
    end: int
    lane: int
    interval: float
    label: str = ""
    ticks: list = field(default_factory=list)  # [time, weight]

    def __post_init__(self):
        self.ticks = build_ticks(self.start, self.end, self.interval, False, False)


@dataclass
class Arc:
    start: int
    end: int
    colour: int
    interval: float
    position: Callable[[float], tuple[float, float]]  # game units on the sky plane
    has_predecessor: bool = False
    has_successor: bool = False
    label: str = ""
    ticks: list = field(default_factory=list)  # [time, weight]

    def __post_init__(self):
        self.ticks = build_ticks(self.start, self.end, self.interval, self.has_predecessor, self.has_successor)


def playable_events(chart, events=None):
    """The chart's events outside noinput timing groups, in source order.

    Note indices and source event ids must both come from this one sequence: filtering only one of them would pair a
    note with a decorative event without any index error.
    """
    silent = {group.group_id for group in chart.timinggroups if "noinput" in group.raw_attributes}
    return [event for event in (chart.events if events is None else events)
            if event.timinggroup_id not in silent]


def solid_arc_events(chart, events=None):
    """Playable arcs that are not traces and have a length, in the order of ``notes_from_chart``'s arcs."""
    return [e for e in playable_events(chart, events)
            if e.type == "arc" and not e.is_guideline and e.end_time_ms != e.start_time_ms]


def _joined(first, second) -> bool:
    return (first is not second and abs(first.end_time_ms - second.start_time_ms) <= JOIN_MS
            and abs(first.end_aff_xy[0] - second.start_aff_xy[0]) < JOIN_X
            and first.end_aff_xy[1] == second.start_aff_xy[1])


def arc_connections(solid) -> tuple[tuple[int, int], ...]:
    """(earlier, later) indices of joined arcs. Colour is not part of the test: every join shapes the ticks, while
    planning keeps chains of one colour (``occupations``)."""
    return tuple((i, j) for i, first in enumerate(solid) for j, second in enumerate(solid) if _joined(first, second))


def notes_from_chart(chart):
    """(taps, sky notes, holds, arcs) of the chart's playable notes, in chart order.

    Notes in noinput timing groups, traces, and zero-length holds and arcs have none. Tick intervals use the BPM in
    force at the note's start in its timing group.
    """
    events = playable_events(chart)
    timings: dict = {}
    for event in events:
        if event.type == "timing":
            timings.setdefault(event.timinggroup_id, []).append(event)

    def interval(event) -> float:
        return tick_interval([t.bpm for t in timings[event.timinggroup_id] if t.start_time_ms <= event.start_time_ms][-1])

    by_id = {event.event_id: event for event in chart.events}
    solid = solid_arc_events(chart, events)
    connections = arc_connections(solid)
    predecessors = {j for i, j in connections}
    successors = {i for i, j in connections}
    taps = [Tap(e.start_time_ms, e.lane, f"({e.start_time_ms},{e.lane})") for e in events if e.type == "tap"]
    holds = [Hold(e.start_time_ms, e.end_time_ms, e.lane, interval(e), f"hold({e.start_time_ms},{e.end_time_ms},{e.lane})")
             for e in events if e.type == "hold" and e.end_time_ms != e.start_time_ms]
    sky = []
    for e in events:
        if e.type == "arctap":
            x, y = game_xy(arc_position(by_id[e.parent_arc_id], e.start_time_ms))
            sky.append(SkyTap(e.start_time_ms, x, y, f"arctap({e.start_time_ms})"))
    arcs = [Arc(
        start=e.start_time_ms, end=e.end_time_ms, colour=e.color, interval=interval(e),
        position=lambda time, e=e: game_xy(arc_position(e, time)),
        has_predecessor=i in predecessors, has_successor=i in successors,
        label=f"arc({e.start_time_ms},{e.end_time_ms},{e.start_aff_xy[0]:.2f},{e.end_aff_xy[0]:.2f},"
              f"{e.start_aff_xy[1]:.2f},{e.end_aff_xy[1]:.2f},{e.color})") for i, e in enumerate(solid)]
    return taps, sky, holds, arcs

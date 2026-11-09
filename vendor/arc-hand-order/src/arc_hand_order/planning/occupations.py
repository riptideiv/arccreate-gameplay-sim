"""Finger occupations: what each playable note needs from a finger under the contact rules (``rules``).

Times are integer chart milliseconds and positions are game units. One same-colour connected component is one chain,
even when a connection crosses colours; such joins still shape the ticks. Continuous single-finger contact is a
planning policy for chains and holds; a hold itself takes any touch in its lane.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Literal

from . import rules

Kind = Literal["chain", "hold", "tap", "sky"]


@dataclass(frozen=True)
class FingerId:
    hand: Literal["left", "right"]
    finger: str = "index"


@dataclass(frozen=True)
class FingerAssignment:
    occupation_id: str
    finger: FingerId
    down_ms: int
    up_ms: int


@dataclass(frozen=True)
class TimeWindow:
    """Inclusive integer window. None means no bound."""
    earliest_ms: int | None
    latest_ms: int | None

    def contains(self, time_ms: int) -> bool:
        return ((self.earliest_ms is None or self.earliest_ms <= time_ms)
                and (self.latest_ms is None or time_ms <= self.latest_ms))


@dataclass(frozen=True)
class ScorePoint:
    source_event_id: str
    note_kind: Literal["arc", "hold", "tap", "sky"]
    note_index: int
    tick_index: int | None
    time_ms: int
    weight: int
    contact_window: TimeWindow
    native_auto_ms: int | None = None
    native_expiry_ms: int | None = None
    native_judge_ms: float | None = None


@dataclass(frozen=True)
class ContactRegion:
    """Where a touch counts for a note at one moment (``rules``): a lane on the track plane, or a rectangle on the
    sky plane around the note."""
    kind: Kind
    x: float = 0.0
    y: float = 0.0
    lane: int | None = None

    @property
    def plane(self) -> str:
        return "ground" if self.lane is not None else "sky"

    @property
    def offsets(self) -> tuple[float, float, float, float] | None:
        if self.kind == "chain":
            return (-rules.HITBOX_X, rules.HITBOX_X, -rules.HITBOX_Y, rules.HITBOX_Y)
        if self.kind == "sky":
            return (-rules.SKY_TAP_X, rules.SKY_TAP_X, -rules.SKY_TAP_DOWN, rules.SKY_TAP_UP)
        return None

    def contains(self, x: float, y_or_z: float) -> bool:
        if self.lane is not None:
            return rules.touch_lane(x, y_or_z) == self.lane
        if self.kind == "sky":
            return rules.sky_contact(x, y_or_z, self.x, self.y)
        return rules.arc_contact(x, y_or_z, self.x, self.y)


@dataclass(frozen=True)
class Occupation:
    occupation_id: str
    kind: Kind
    source_event_ids: tuple[str, ...]
    note_indices: tuple[int, ...]  # into OccupationSet.taps, sky_taps, holds or arcs, by kind
    notes: tuple
    score_points: tuple[ScorePoint, ...]
    start_ms: int
    end_ms: int
    colour: int | None
    lane: int | None
    down_window: TimeWindow
    up_window: TimeWindow
    parent_occupation_id: str | None = None
    native_scoring: bool = False

    @property
    def requires_new_press(self) -> bool:
        return self.kind != "chain"

    @property
    def continuous_contact(self) -> bool:
        return self.kind in ("chain", "hold")

    @property
    def first_tick(self) -> ScorePoint:
        return self.score_points[0]

    @property
    def last_tick(self) -> ScorePoint:
        return self.score_points[-1]

    @property
    def score_weight(self) -> int:
        return sum(point.weight for point in self.score_points)

    def region_at(self, time_ms: float) -> ContactRegion:
        if self.lane is not None:
            return ContactRegion(self.kind, lane=self.lane)
        if self.kind == "sky":
            note = self.notes[0]
            return ContactRegion(self.kind, note.x, note.y)
        if self.native_scoring and self.parent_occupation_id:
            from .arc_scoring import source_note
            note = source_note(self)
            x, y = note.position(min(max(time_ms, note.start), note.end))
            return ContactRegion(self.kind, x, y)
        # A join can leave up to JOIN_MS between nodes. Keep every node and
        # choose its nearest endpoint inside such a gap.
        note = min(self.notes, key=lambda a: max(a.start - time_ms, time_ms - a.end, 0))
        x, y = note.position(min(max(time_ms, note.start), note.end))
        return ContactRegion(self.kind, x, y)

    def contact_span(self, *, early_settlement: bool = True, allow_expiry: bool = False,
                     point_contact_ms: int = 40) -> tuple[int, int]:
        """A candidate lifecycle, inside the occupation's own bounds.

        Default: start a chain at its first tick, press a hold at its start,
        and release after the last tick can settle half an interval early.
        Disabling early settlement keeps contact through the last tick time.
        Expiry permits a late chain/hold start at the first tick's deadline.
        Point notes have a configurable physical contact duration; only
        their press needs to be inside the point window.
        """
        if not self.continuous_contact:
            return self.start_ms, self.start_ms + point_contact_ms
        down = self.first_tick.time_ms if self.kind == "chain" else self.start_ms
        if allow_expiry:
            down = self.down_window.latest_ms
        last = (max(p.contact_window.earliest_ms for p in self.score_points)
                if early_settlement else self.last_tick.time_ms)
        return down, max(down + 1, last + 1)


@dataclass(frozen=True)
class OccupationSet:
    items: tuple[Occupation, ...]
    connections: tuple[tuple[int, int], ...]
    source_to_occupation: dict[str, str]
    taps: tuple
    sky_taps: tuple
    holds: tuple
    arcs: tuple
    audio_offset_ms: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(item.kind for item in self.items))

    @property
    def score_weights(self) -> dict[str, int]:
        return {kind: sum(item.score_weight for item in self.items if item.kind == kind)
                for kind in ("chain", "hold", "tap", "sky")}

    def grace_pairs_at(self, time_ms: float) -> tuple[tuple[int, int], ...]:
        """Pairs of active arcs of different colours within the grace distance: each renews the grace of both
        colours (``rules``)."""
        active = [i for i, arc in enumerate(self.arcs) if arc.start <= time_ms <= arc.end]
        return tuple((i, j) for n, i in enumerate(active) for j in active[n + 1:]
                     if self.arcs[i].colour != self.arcs[j].colour
                     and math.dist(self.arcs[i].position(time_ms), self.arcs[j].position(time_ms)) < rules.GRACE_DISTANCE)


def _tick_window(note, tick: int, hold: bool) -> TimeWindow:
    """The chart ms in which contact settles a tick, inside its note: from half an interval before it until its expiry.
    A note with no regular ticks (zero BPM) settles its one tick on any contact in its span."""
    earliest = note.start + int(hold) if note.interval == math.inf else math.ceil(tick - note.interval * 0.5)
    latest = math.ceil(tick + rules.expiry_ms(note.interval))
    return TimeWindow(max(note.start + int(hold), earliest), min(note.end - int(hold), latest))


def occupations_from_chart(chart) -> OccupationSet:
    """The occupations of a parsed chart's playable notes (``rules.notes_from_chart``).

    ``connections`` indexes ``arcs`` and includes different-colour joins.
    A ScorePoint's note index addresses the note list of its kind.
    """
    taps, sky, holds, arcs = rules.notes_from_chart(chart)
    events = rules.playable_events(chart)
    solid = rules.solid_arc_events(chart, events)
    connections = rules.arc_connections(solid)
    parent = list(range(len(solid)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i, j in connections:
        if arcs[i].colour == arcs[j].colour:
            parent[root(j)] = root(i)
    chains: dict[int, list[int]] = {}
    for i in sorted(range(len(arcs)), key=lambda i: (arcs[i].start, i)):
        chains.setdefault(root(i), []).append(i)

    items = []

    def add(kind, events, indices, notes):
        points = []
        for event, index, note in zip(events, indices, notes, strict=True):
            if kind in ("chain", "hold"):
                points.extend(ScorePoint(event.event_id, "arc" if kind == "chain" else "hold", index, k,
                                        time, weight, _tick_window(note, time, kind == "hold"))
                              for k, (time, weight) in enumerate(note.ticks))
            else:
                points.append(ScorePoint(event.event_id, kind, index, None, note.time, 1,
                                         TimeWindow(note.time - rules.POINT_WINDOW_MS, note.time + rules.POINT_WINDOW_MS)))
        points.sort(key=lambda p: (p.time_ms, p.note_index, p.tick_index or 0))
        first, last = points[0], points[-1]
        start, end = min(e.start_time_ms for e in events), max(e.end_time_ms for e in events)
        if kind == "chain":
            # A finger already down can take an arc: no fresh press and
            # no bound on how early that touch began.
            down = TimeWindow(None, min(p.contact_window.latest_ms for p in points))
        elif kind == "hold":
            down = TimeWindow(start - rules.HOLD_EARLY_MS + 1,
                              min(p.contact_window.latest_ms for p in points))
        else:
            down = first.contact_window
        up = (TimeWindow(max(p.contact_window.earliest_ms for p in points) + 1, None)
              if kind in ("chain", "hold") else TimeWindow(None, None))
        ids = tuple(e.event_id for e in events)
        items.append(Occupation(f"{kind}:{ids[0]}", kind, ids, tuple(indices), tuple(notes), tuple(points), start, end,
                                notes[0].colour if kind == "chain" else None,
                                notes[0].lane if kind in ("hold", "tap") else None, down, up))

    for indices in chains.values():
        add("chain", [solid[i] for i in indices], indices, [arcs[i] for i in indices])
    for kind, source_events, notes in (
        ("hold", [e for e in events if e.type == "hold" and e.end_time_ms != e.start_time_ms], holds),
        ("tap", [e for e in events if e.type == "tap"], taps),
        ("sky", [e for e in events if e.type == "arctap"], sky),
    ):
        for i, (event, note) in enumerate(zip(source_events, notes, strict=True)):
            add(kind, [event], [i], [note])
    items.sort(key=lambda item: (item.first_tick.time_ms if item.kind == "chain" else item.start_ms,
                               item.end_ms, item.occupation_id))
    return OccupationSet(tuple(items), connections,
                         {event_id: item.occupation_id for item in items for event_id in item.source_event_ids},
                         tuple(taps), tuple(sky), tuple(holds), tuple(arcs), chart.audio_offset_ms)

"""Exact finite-candidate DP for one finger per hand.

Optimality is confined to the candidate graph and its local constraints. The
state (last left candidate, last right candidate) is sufficient for these
costs and constraints; it does not hold colour binding or grace, which notes
a press could take, or settled ticks.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from functools import lru_cache
import math

from . import rules
from .occupations import FingerAssignment, FingerId, Occupation, OccupationSet
from .projection import GameplayProjection


def frame_ms(frame: int, audio_offset_ms: int = 0) -> int:
    return round(frame * 1000 / 60) - audio_offset_ms


def frame_at_or_after(ms: int, audio_offset_ms: int = 0) -> int:
    frame = math.ceil((ms + audio_offset_ms) * 60 / 1000)
    while frame_ms(frame - 1, audio_offset_ms) >= ms:
        frame -= 1
    return frame


@dataclass(frozen=True)
class PlannerOptions:
    audio_offset_ms: int = 0
    offsets_ms: tuple[int, ...] = (-33, 0, 33)
    point_contact_frames: int = 2  # 33 or 34 ms; two observable held frames
    min_air_frames: int = 1
    speed_max_m_s: float = 3.0
    speed_comfort_m_s: float = 0.5
    colour_weight: float = 20.0  # fixed cost per chain on the unusual hand
    crossing_weight: float = 1.0  # per second of sampled simultaneous contact
    movement_weight: float = 1.0  # squared speed above comfort
    handover_weight: float = 1.0  # fixed cost per chain transferred at a meeting
    # Optional separation and repeat preferences supplement the default
    # transfer-distance cost below. Speed comfort alone has a zero-cost plateau.
    apart_weight: float = 0.0  # per metre a contact starts beyond where the other hand has just been
    apart_margin_m: float = 0.03  # ... or nearer to that place than two fingers can be side by side
    apart_memory_s: float = 0.5  # the other hand's place stops counting after this long without a contact
    repeat_weight: float = 0.0  # a hand's next contact sooner than repeat_comfort_s after its last
    repeat_comfort_s: float = 0.25
    # A reverse catch, as players make where two colours meet: at each chart
    # time in ``handovers`` the chain held there is split into two contacts,
    # which cannot be on one finger, and inside each (start_ms, end_ms) of
    # ``reversed_spans`` blue belongs to the right hand and red to the left.
    handovers: tuple[int, ...] = ()
    reversed_spans: tuple[tuple[int, int], ...] = ()
    # Frames the catching finger is down before the other lifts: a player
    # catches first, then lets go.
    handover_lead_frames: int = 0
    # An X swap, where two colours cross at one node: at each chart time in
    # ``swaps`` both fingers stay down and go straight on, each onto the
    # other colour's next node. Grace at the meeting lets a held touch
    # change colour (``rules``).
    swaps: tuple[int, ...] = ()
    # Optional stricter point-note timing target, checked after frame rounding.
    # None keeps the occupation's window; 25 requests Max-PURE timing.
    point_window_ms: int | None = None
    distance_weight: float = 5.0  # cost per metre of straight-line transfer, even below comfort speed
    # Hold every arc chain from the frame it reaches the line
    # to the first frame after it ends, instead of from its first tick until
    # its last tick can settle. A hand holding an arc then cannot also play a
    # note during it. The tick-based head and tail stay available as fallback
    # candidates charged ``partial_hold_weight`` per second of arc left
    # untouched, so they are only chosen where a full hold has no path.
    full_arc_hold: bool = False
    partial_hold_weight: float = 1000.0
    # The same for ground holds: press at the hold's start and
    # release on the first frame after its end, instead of once its last tick
    # can settle. Holds starting inside one of ``early_hold_spans`` (chart ms,
    # inclusive) keep the tick rule: a run of holds too close together to be
    # held through, which players release early as well. The tick-rule
    # release stays a fallback, charged ``partial_ground_hold_weight`` per
    # second: less than an arc, so where both cannot be held through, the
    # hold lets go early rather than the arc being caught late.
    full_ground_hold: bool = False
    early_hold_spans: tuple[tuple[int, int], ...] = ()
    partial_ground_hold_weight: float = 500.0
    # With full_arc_hold: an arc chain whose start or end lies inside one of
    # ``early_arc_spans`` (chart ms, inclusive) keeps the tick rule (catch at
    # its first tick, release once its last tick can settle). For places where
    # holding to the end pins one hand across the other's path and the body
    # cannot get out of the way in time; such spans come from outside the planner.
    early_arc_spans: tuple[tuple[int, int], ...] = ()
    # Air frames a hand needs after a contact held to its end (an arc with
    # full_arc_hold, a hold with full_ground_hold, of a kind in
    # ``held_release_air_kinds``) before it presses again: room for a visible
    # lift. With less, the next note goes to the other hand, or the held
    # contact falls back to its charged early release. 0 disables the rule.
    held_release_air_frames: int = 0
    held_release_air_kinds: tuple[str, ...] = ("chain", "hold")
    # A crossing pass: a hand flies from one contact to its next while the
    # other hand is on the glass (or about to be), and the two swap sides on
    # the screen. One hand must go over the other and the other drop out of
    # the way, which the body cannot do quickly (arcs held to their end left
    # some passes 0.1-0.15 s, beyond the fingertip limits). Passes shorter
    # than ``crossing_pass_s`` are not allowed; the plan then releases a held
    # arc or hold early, catches one late, or changes hands, all charged.
    # 0 disables the rule. ``crossing_pass_margin_m``: the order counts as
    # swapped only if both ends are at least this far to either side.
    crossing_pass_s: float = 0.0
    crossing_pass_margin_m: float = 0.005
    # Point notes (tap, sky) starting at these chart ms go to the given hand:
    # decisions fixed from outside the planner, not a planning preference.
    fixed_hands: tuple[tuple[int, str], ...] = ()
    # Local physical-contact failures supplied by the glove verification pass:
    # (parent occupation id, first forbidden chart ms, last forbidden chart ms).
    contact_exclusions: tuple[tuple[str, int, int], ...] = ()
    scoring_arcs: bool = False
    prefer_arc_trace: bool = False
    arc_trace_weight: float = 1000.0

    def __post_init__(self):
        if not math.isfinite(self.arc_trace_weight) or self.arc_trace_weight < 0:
            raise ValueError("arc_trace_weight must be finite and nonnegative")
        if not math.isfinite(self.distance_weight) or self.distance_weight < 0:
            raise ValueError("distance_weight must be finite and nonnegative")
        if not math.isfinite(self.partial_hold_weight) or self.partial_hold_weight < 0:
            raise ValueError("partial_hold_weight must be finite and nonnegative")
        if not math.isfinite(self.partial_ground_hold_weight) or self.partial_ground_hold_weight < 0:
            raise ValueError("partial_ground_hold_weight must be finite and nonnegative")
        if type(self.held_release_air_frames) is not int or self.held_release_air_frames < 0:
            raise ValueError("held_release_air_frames must be a nonnegative integer")
        if not set(self.held_release_air_kinds) <= {"chain", "hold"}:
            raise ValueError("held_release_air_kinds are chain and/or hold")
        if not math.isfinite(self.crossing_pass_s) or self.crossing_pass_s < 0:
            raise ValueError("crossing_pass_s must be finite and nonnegative")
        if not math.isfinite(self.crossing_pass_margin_m) or self.crossing_pass_margin_m < 0:
            raise ValueError("crossing_pass_margin_m must be finite and nonnegative")
        if any(hand not in ("left", "right") for _, hand in self.fixed_hands):
            raise ValueError("fixed_hands assigns notes to left or right")

    def holds_to_end(self, occupation: Occupation) -> bool:
        """Whether this arc chain or ground hold is planned from its start to its end."""
        if occupation.parent_occupation_id:
            return False
        if occupation.kind == "chain":
            return self.full_arc_hold and not any(start <= edge <= end for start, end in self.early_arc_spans
                                                  for edge in (occupation.start_ms, occupation.end_ms))
        if occupation.kind == "hold":
            return self.full_ground_hold and not any(start <= occupation.start_ms <= end
                                                     for start, end in self.early_hold_spans)
        return False

    @classmethod
    def from_record(cls, record: dict):
        """Restore saved policy; records predating distance cost used zero."""
        return cls(**{"distance_weight": 0.0, **record})


@dataclass(frozen=True)
class Candidate:
    id: int
    item_index: int
    occupation: Occupation
    down_frame: int
    up_frame: int
    first_xy: tuple[float, float]
    last_xy: tuple[float, float]
    audio_offset_ms: int = 0
    max_contact_sample_speed_m_s: float = 0.0
    untouched_s: float = 0.0  # arc or hold time a full-hold fallback leaves without contact
    contact_path: tuple[tuple[float,float], ...] | None = None

    @property
    def held_to_end(self) -> bool:
        """An arc or hold contact that lasts to the first frame after the note's end."""
        return (self.occupation.kind in ("chain", "hold") and self.untouched_s == 0
                and self.up_frame == frame_at_or_after(self.occupation.end_ms + 1, self.audio_offset_ms))

    @property
    def down_ms(self):
        return frame_ms(self.down_frame, self.audio_offset_ms)

    @property
    def up_ms(self):
        return frame_ms(self.up_frame, self.audio_offset_ms)


@dataclass(frozen=True)
class Cost:
    colour: float = 0.0
    crossing_seconds: float = 0.0
    movement: float = 0.0
    handover: float = 0.0
    apart: float = 0.0
    repeat: float = 0.0
    distance_m: float = 0.0
    untouched_arc_s: float = 0.0
    untouched_hold_s: float = 0.0

    def __add__(self, other):
        return Cost(self.colour + other.colour, self.crossing_seconds + other.crossing_seconds,
                    self.movement + other.movement, self.handover + other.handover,
                    self.apart + other.apart, self.repeat + other.repeat,
                    self.distance_m + other.distance_m, self.untouched_arc_s + other.untouched_arc_s,
                    self.untouched_hold_s + other.untouched_hold_s)

    def weighted(self, options: PlannerOptions):
        return (self.colour * options.colour_weight + self.crossing_seconds * options.crossing_weight
                + self.movement * options.movement_weight + self.handover * options.handover_weight
                + self.apart * options.apart_weight + self.repeat * options.repeat_weight
                + self.distance_m * options.distance_weight
                + self.untouched_arc_s * options.partial_hold_weight
                + self.untouched_hold_s * options.partial_ground_hold_weight)


@dataclass(frozen=True)
class ContactSegment:
    """One contact: its finger, chart-time lifecycle and fixed device target."""
    lifecycle_id: int
    occupation_id: str
    finger: FingerId
    down_ms: int
    up_ms: int
    candidate: Candidate

    @property
    def occupation(self):
        return self.candidate.occupation

    def target_at(self, time_ms: int, projection: GameplayProjection):
        return target_xy(self.candidate.occupation, time_ms, projection)

    def region_at(self, time_ms: int):
        return self.occupation.region_at(time_ms)


@dataclass(frozen=True)
class HandoverNode:
    frame: int
    chart_ms: int
    arc_pairs: tuple[tuple[int, int], ...]
    occupation_ids: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    choices: tuple[tuple[Candidate, str], ...]
    cost: Cost
    options: PlannerOptions
    states_visited: int
    handover_nodes: tuple[HandoverNode, ...] = ()
    optimality_scope: str = "Exact over finite candidates in fixed occupation order, with local gap/speed constraints and additive costs. Colour binding, grace and pending-note history are not in the DP state."

    @property
    def assignments(self):
        return tuple(FingerAssignment(c.occupation.occupation_id, FingerId(hand), c.down_ms, c.up_ms)
                     for c, hand in self.choices)

    def contact_segments(self) -> tuple[ContactSegment, ...]:
        return tuple(ContactSegment(i, c.occupation.occupation_id, FingerId(hand), c.down_ms, c.up_ms, c)
                     for i, (c, hand) in enumerate(self.choices, 1))

    def target_at(self, finger: FingerId, time_ms: int, projection: GameplayProjection):
        """Fixed planned screen XY during contact; None in the air."""
        candidate = next((c for c, hand in self.choices if finger.finger == "index" and hand == finger.hand
                          and c.down_ms <= time_ms < c.up_ms), None)
        return None if candidate is None else target_xy(candidate.occupation, time_ms, projection)


def handover_nodes(occupations: OccupationSet, plan: Plan) -> tuple[HandoverNode, ...]:
    """First, middle and final observed frames of each meeting window.

    Proximity renews grace even if the other arc's ticks have already been
    settled and its finger lifted. A node is only a candidate time for a
    handover.
    """
    offset = occupations.audio_offset_ms
    by_arc = {i: item.occupation_id for item in occupations.items if item.kind == "chain" for i in item.note_indices}
    windows, window = [], []
    last = max((c.up_frame for c, _ in plan.choices), default=0)
    for frame in range(max(0, frame_at_or_after(0, offset)), last + 1):
        now = frame_ms(frame, offset)
        pairs = occupations.grace_pairs_at(now)
        active = {c.occupation.occupation_id for c, _ in plan.choices if c.down_frame < frame < c.up_frame}
        ids = tuple(sorted({by_arc[i] for pair in pairs for i in pair} & active))
        if pairs and ids:
            window.append(HandoverNode(frame, now, pairs, ids))
        elif window:
            windows.append(window)
            window = []
    if window:
        windows.append(window)
    return tuple(window[i] for window in windows for i in sorted({0, len(window) // 2, len(window) - 1}))


def _evaluate_contact_choices(choices, projection, options, handover_count):
    """Evaluate a contact candidate without asserting global DP optimality."""
    movement, crossing, distance = 0.0, 0.0, 0.0
    unusual = set()
    for c, hand in choices:
        if c.occupation.colour in (0, 1) and hand != ("left" if c.occupation.colour == 0 else "right"):
            unusual.add(c.occupation.occupation_id)
        if c.max_contact_sample_speed_m_s > options.speed_max_m_s:
            raise ValueError("handover candidate exceeds sampled contact speed limit")
    for hand in ("left", "right"):
        mine = [c for c, h in choices if h == hand]
        for a, b in zip(mine, mine[1:]):
            if b.down_frame - a.up_frame < options.min_air_frames:
                raise ValueError("handover candidate lacks an observable release/air frame")
            transfer = math.dist(a.last_xy, b.first_xy)
            distance += transfer
            speed = transfer / ((b.down_ms - a.up_ms) / 1000)
            if speed > options.speed_max_m_s:
                raise ValueError("handover candidate exceeds air-transfer speed limit")
            movement += max(0.0, speed - options.speed_comfort_m_s) ** 2
    for a, hand in choices:
        if hand == "left":
            for b, other in choices:
                if other == "right":
                    crossing += sum(target_xy(a.occupation, frame_ms(f, a.audio_offset_ms), projection)[0]
                                    > target_xy(b.occupation, frame_ms(f, b.audio_offset_ms), projection)[0]
                                    for f in range(max(a.down_frame, b.down_frame), min(a.up_frame, b.up_frame))) / 60
    return Cost(float(len(unusual)), crossing, movement, float(handover_count), distance_m=distance,
                untouched_arc_s=sum(c.untouched_s for c, _ in choices if c.occupation.kind != "hold"),
                untouched_hold_s=sum(c.untouched_s for c, _ in choices if c.occupation.kind == "hold"))


def make_handover_candidate(plan: Plan, node: HandoverNode, occupation_id: str,
                            projection: GameplayProjection) -> Plan:
    """Explicit local alternative; not a new globally optimal DP result.

    Split active chains at an observed meeting, switch hands for the suffix,
    and mirror assignments through the overlapping occupation cluster. This
    preserves non-overlap inside the cluster and returns to the base plan
    after it ends. Every boundary is rechecked.
    """
    if occupation_id not in node.occupation_ids:
        raise ValueError("requested chain is not active at this handover node")
    arcs = {i: arc for c, _ in plan.choices if c.occupation.kind == "chain"
            for i, arc in zip(c.occupation.note_indices, c.occupation.notes)}
    if not any(arcs[i].start <= node.chart_ms <= arcs[i].end and arcs[j].start <= node.chart_ms <= arcs[j].end
               and arcs[i].colour != arcs[j].colour
               and math.dist(arcs[i].position(node.chart_ms), arcs[j].position(node.chart_ms)) < rules.GRACE_DISTANCE
               for i, j in node.arc_pairs):
        raise ValueError("handover node has no two colours within the grace distance")
    chosen = next(c for c, _ in plan.choices if c.occupation.occupation_id == occupation_id
                  and c.down_frame < node.frame < c.up_frame)
    end = chosen.up_frame
    while True:
        extended = max([end, *(c.up_frame for c, _ in plan.choices if c.down_frame < end and c.up_frame > node.frame)])
        if extended == end:
            break
        end = extended
    choices, next_id, transfers = [], max(c.id for c, _ in plan.choices) + 1, 0
    for c, hand in plan.choices:
        opposite = "right" if hand == "left" else "left"
        if c.down_frame < node.frame < c.up_frame:
            if c.occupation.kind != "chain" or c.occupation.occupation_id not in node.occupation_ids:
                raise ValueError("active occupation cannot transfer at this meeting")
            prefix = replace(c, id=next_id, up_frame=node.frame,
                             last_xy=target_xy(c.occupation, frame_ms(node.frame - 1, c.audio_offset_ms), projection))
            suffix = replace(c, id=next_id + 1, down_frame=node.frame,
                             first_xy=target_xy(c.occupation, node.chart_ms, projection))
            next_id += 2
            choices.extend(((prefix, hand), (suffix, opposite)))
            transfers += 1
        elif node.frame <= c.down_frame < end:
            choices.append((c, opposite))
        else:
            choices.append((c, hand))
    choices.sort(key=lambda value: (value[0].down_frame, value[0].item_index, value[0].id))
    cost = _evaluate_contact_choices(choices, projection, plan.options, transfers)
    return Plan(tuple(choices), cost, plan.options, 0, (node,),
                "Explicit meeting-node alternative produced from the base DP plan by local hand mirroring. It is not asserted to be a globally optimal handover plan.")


def target_xy(occupation: Occupation, time_ms: int, projection: GameplayProjection):
    region = occupation.region_at(time_ms)
    if region.lane is not None:
        return projection.lane_center_to_device(region.lane)
    return projection.judgement_to_device(region.x, region.y)


def crossing_pass(previous: "Candidate", following: "Candidate", other: "Candidate | None", hand: str,
                  projection: GameplayProjection, margin_m: float = 0.005):
    """Whether one hand's flight from ``previous`` to ``following`` swaps sides with the other hand.

    The other hand is at its contact ``other`` while it lasts (planned point at that time), before it at
    its first point, after it at its last. The flight swaps sides when the left hand is left of the other
    at release and right of it at landing (or the reverse), each by at least ``margin_m``. Returns
    (swapped, air seconds)."""
    if other is None:
        return False, 0.0
    begin, end = previous.up_ms, following.down_ms

    def other_x(time_ms):
        if other.down_ms <= time_ms < other.up_ms:
            return target_xy(other.occupation, time_ms, projection)[0]
        return (other.first_xy if time_ms < other.down_ms else other.last_xy)[0]
    side = 1.0 if hand == "left" else -1.0  # positive: the usual side (left hand left of the right hand)
    before = side * (other_x(begin) - previous.last_xy[0])
    after = side * (other_x(end) - following.first_xy[0])
    swapped = min(abs(before), abs(after)) >= margin_m and (before > 0) != (after > 0)
    return swapped, (end - begin) / 1000


def build_candidates(occupations: OccupationSet, projection: GameplayProjection,
                     options: PlannerOptions, progress=None) -> tuple[tuple[Candidate, ...], ...]:
    """Finite graph; nominal ordering is retained even when ±33 ms overlaps."""
    if options.point_window_ms is not None and (
            type(options.point_window_ms) is not int or options.point_window_ms < 0):
        raise ValueError("point_window_ms must be a nonnegative integer or None")
    groups, next_id = [], 0
    offset = occupations.audio_offset_ms
    profiles = {}
    if options.scoring_arcs:
        if progress:
            progress('geometry')
        from .arc_scoring import profile, anchors, source_note, covers, hit, world, trace_cells, trace_position
        from .native_input import geometry_for
        native_geometry = geometry_for(occupations.items, projection, offset)
        cells = trace_cells([o for o in occupations.items if o.kind == 'chain'], offset)
        parents = {}
        for o in occupations.items:
            if o.kind == 'chain':
                key = o.parent_occupation_id or o.occupation_id
                if key not in parents: parents[key] = o
                else: parents[key] = replace(parents[key], score_points=parents[key].score_points + o.score_points)
        for index, (key,o) in enumerate(parents.items()):
            if progress:
                progress('arc_profiles', index, len(parents))
            o = replace(o, parent_occupation_id=None, score_points=tuple(sorted(set(o.score_points),key=lambda p:p.time_ms)))
            profiles[key] = (profile(o,projection,options.speed_max_m_s,offset), anchors(o,projection))
    for k, item in enumerate(occupations.items):
        if progress:
            progress('candidates', k, len(occupations.items), chart_ms=item.start_ms)
        if options.scoring_arcs and item.kind == 'chain':
            fitted,pool = profiles[item.parent_occupation_id or item.occupation_id]
            variants = []
            if not item.parent_occupation_id:
                if fitted is not None:
                    down,path,_ = fitted
                    variants.append((down,path))
                    if len(set(path))==1:
                        for dx in (-.012,.012):
                            shifted=tuple((x+dx,y) for x,y in path)
                            caught=covers(item,shifted,down,offset,projection)
                            if caught is not None and any(hit(n,world(projection,shifted[0]),frame_ms(down,offset))
                                    for n in item.notes if n.start<=frame_ms(down,offset)<=n.end):
                                variants.append((down,shifted))
                    if options.full_arc_hold:
                        head=max(0,frame_at_or_after(item.start_ms,offset))
                        end=max(down+len(path),frame_at_or_after(item.end_ms+1,offset))
                        variants.insert(0,(head,(path[0],)*(down-head)+path+(path[-1],)*(end-down-len(path))))
            else:
                point=item.first_tick;n=source_note(item)
                first=max(0,frame_at_or_after(point.contact_window.earliest_ms,offset))
                last=frame_at_or_after(point.contact_window.latest_ms+1,offset)-1
                nominal=frame_at_or_after(point.time_ms,offset)
                targets=list(pool[:2])
                targets.append(projection.judgement_to_device(*n.position(min(max(point.time_ms,n.start),n.end))))
                if fitted is not None:
                    f,path,_=fitted;targets.insert(0,path[min(max(0,nominal-f),len(path)-1)])
                targets=list(dict.fromkeys(targets))[:3]
                for q in targets:
                    xy=world(projection,q)
                    valid=[f for f in range(first,last+1) if hit(n,xy,frame_ms(f,offset))]
                    if not valid:continue
                    downs={valid[0],min(valid,key=lambda f:abs(f-nominal)),valid[-1]}
                    for down in sorted(downs):
                        variants.append((down,(q,q)))
                # Keep centre catches as alternatives. They must use the owning
                # segment at the chosen time, not its nominal tick position.
                for down in sorted({first,last,nominal,nominal-1,nominal+1}):
                    if not first<=down<=last:continue
                    path=tuple(projection.judgement_to_device(*n.position(min(max(frame_ms(f,offset),n.start),n.end))) for f in (down,down+1))
                    variants.append((down,path))
            if options.prefer_arc_trace and options.arc_trace_weight:
                first, stop = cells[item.occupation_id]
                if stop > first:
                    centre = tuple(trace_position(item, f, offset, projection) for f in range(first, stop))
                    variants.append((first, centre))
                    if fitted is not None:
                        guide_down, guide, _ = fitted
                        base = tuple(guide[min(max(f-guide_down, 0), len(guide)-1)] for f in range(first, stop))
                        for amount in (.25, .5, .75):
                            variants.append((first, tuple((x+(u-x)*amount, y+(v-y)*amount)
                                for (x,y),(u,v) in zip(base, centre))))
            candidates=[]
            for down,path in variants:
                path = native_geometry.guard_path(item,path,down)
                if any(abs(x)>projection.width_m/2 or abs(y)>projection.height_m/2 for x,y in path):continue
                up=down+len(path)
                if any(parent==(item.parent_occupation_id or item.occupation_id) and frame_ms(down,offset)<=stop and frame_ms(up-1,offset)>=start
                       for parent,start,stop in options.contact_exclusions):continue
                if covers(item,path,down,offset,projection) is None:continue
                speed=max((math.dist(a,b)*60 for a,b in zip(path,path[1:])),default=0)
                if speed>options.speed_max_m_s*1.00001:continue
                untouched=0
                if not item.parent_occupation_id:
                    untouched=max(0,frame_ms(down,offset)-item.start_ms)/1000+max(0,item.end_ms+1-frame_ms(up,offset))/1000
                if not options.full_arc_hold:untouched=0
                candidates.append(Candidate(next_id,k,item,down,up,path[0],path[-1],offset,speed,untouched,tuple(path)))
                next_id+=1
            groups.append(tuple(candidates));continue
        if item.parent_occupation_id:
            candidates = []
            point = item.first_tick
            first = frame_at_or_after(point.contact_window.earliest_ms, offset)
            last = frame_at_or_after(point.contact_window.latest_ms + 1, offset) - 1
            nominal = frame_at_or_after(point.time_ms, offset)
            # Sample the full early/late settlement window, not just the tick's
            # nominal time. Retiming lets a freed hand catch an existing arc.
            downs = {first, last, nominal, nominal-1, nominal+1}
            downs.update(round(first + (last-first)*part/4) for part in range(1, 4))
            original_first = min(t for note in item.notes for t, _ in note.ticks)
            original_last = max(t for note in item.notes for t, _ in note.ticks)
            if options.full_arc_hold and point.time_ms == original_first:
                downs.add(frame_at_or_after(item.start_ms, offset))
            for down in sorted(downs):
                if down < 0 or down > last:
                    continue
                up = max(down + 2, first + 1)
                ends = {up}
                if options.full_arc_hold and point.time_ms == original_last:
                    ends.add(max(up, frame_at_or_after(item.end_ms+1, offset)))
                for end in sorted(ends):
                    if not any(point.contact_window.contains(frame_ms(f, offset)) for f in range(down,end)):
                        continue
                    if any(parent == item.parent_occupation_id and frame_ms(down, offset) <= stop
                           and frame_ms(end-1, offset) >= start for parent,start,stop in options.contact_exclusions):
                        continue
                    points = [target_xy(item, frame_ms(f, offset), projection) for f in range(down,end)]
                    speed = max((math.dist(a,b) / ((frame_ms(f+1,offset)-frame_ms(f,offset))/1000)
                                 for f,(a,b) in enumerate(zip(points,points[1:]),down)),default=0)
                    if speed > options.speed_max_m_s:
                        continue
                    untouched = 0
                    if options.full_arc_hold:
                        if point.time_ms == original_first: untouched += max(0, frame_ms(down,offset)-item.start_ms)/1000
                        if point.time_ms == original_last: untouched += max(0, item.end_ms+1-frame_ms(end,offset))/1000
                    candidates.append(Candidate(next_id,k,item,down,end,points[0],points[-1],offset,speed,untouched))
                    next_id += 1
            groups.append(tuple(candidates))
            continue
        candidates = []
        if item.kind in ("chain", "hold") and options.holds_to_end(item):
            # From the first frame the arc is at the line, or the hold starts, to the first frame after it ends.
            down_frames = [frame_at_or_after(item.start_ms, offset)]
            up_frame = frame_at_or_after(item.end_ms + 1, offset)
        elif item.continuous_contact:
            desired_down, _ = item.contact_span()
            down_frames = [frame_at_or_after(desired_down, offset)]
            settled = max(p.contact_window.earliest_ms for p in item.score_points)
            up_frame = frame_at_or_after(settled, offset) + 1
        else:
            down_frames = sorted({round((item.start_ms + delta + offset) * 60 / 1000) for delta in options.offsets_ms})
        for down in down_frames:
            if not item.down_window.contains(frame_ms(down, offset)):
                continue
            if (not item.continuous_contact and options.point_window_ms is not None
                    and abs(frame_ms(down, offset) - item.start_ms) > options.point_window_ms):
                continue
            up = max(up_frame, down + 1) if item.continuous_contact else down + options.point_contact_frames
            points = [target_xy(item, frame_ms(f, offset), projection) for f in range(down, up)]
            speed = max((math.dist(a, b) / ((frame_ms(f + 1, offset) - frame_ms(f, offset)) / 1000)
                         for f, (a, b) in enumerate(zip(points, points[1:]), down)), default=0.0)
            if speed > options.speed_max_m_s:
                continue
            candidates.append(Candidate(next_id, k, item, down, up, points[0], points[-1], offset, speed))
            next_id += 1
        groups.append(tuple(candidates))
    if options.full_arc_hold and not any(o.parent_occupation_id for o in occupations.items):
        # A full hold presses before the chain's first tick, so process items in press order.
        order = sorted(range(len(groups)), key=lambda i: (min((c.down_frame for c in groups[i]), default=0), i))
        groups = [tuple(replace(c, item_index=n) for c in groups[i]) for n, i in enumerate(order)]
    for chart_ms in options.handovers:
        groups, next_id = _split_at_handover(groups, occupations, projection, chart_ms, next_id,
                                             options.handover_lead_frames, options if options.full_arc_hold else None)
    for chart_ms in options.swaps:
        groups = _swap_at_meeting(groups, occupations, chart_ms)
    if options.full_arc_hold or options.full_ground_hold:
        groups, next_id = _add_partial_holds(groups, projection, options, next_id)
    if progress:
        progress('candidates', len(occupations.items), len(occupations.items))
    return tuple(groups)


def _add_partial_holds(groups, projection, options, next_id):
    """Fallbacks for a fully held, unsplit chain or hold: tick-based head and/or tail.

    Each is charged for the arc or hold time it leaves untouched. Chains split
    at a handover or joined at a swap keep only their planned contacts. A hold
    is pressed at its start either way, so only its tail has a fallback.
    """
    out = []
    for group in groups:
        full = group[0] if len(group) == 1 else None
        item = full.occupation if full else None
        if (item is None or item.kind not in ("chain", "hold") or not options.holds_to_end(item)
                or ">" in item.occupation_id):
            out.append(group)
            continue
        offset = full.audio_offset_ms
        if (full.down_frame, full.up_frame) != (frame_at_or_after(item.start_ms, offset),
                                                frame_at_or_after(item.end_ms + 1, offset)):
            out.append(group)  # a handover prefix or suffix
            continue
        tick_down = max(frame_at_or_after(item.contact_span()[0], offset), full.down_frame)
        settled = max(p.contact_window.earliest_ms for p in item.score_points)
        settle_up = min(frame_at_or_after(settled, offset) + 1, full.up_frame)
        variants = [full]
        for down in sorted({full.down_frame, tick_down}):
            for up in sorted({full.up_frame, settle_up}):
                if (down, up) == (full.down_frame, full.up_frame) or up <= down:
                    continue
                if not item.down_window.contains(frame_ms(down, offset)):
                    continue
                points = [target_xy(item, frame_ms(f, offset), projection) for f in range(down, up)]
                speed = max((math.dist(a, b) / ((frame_ms(f + 1, offset) - frame_ms(f, offset)) / 1000)
                             for f, (a, b) in enumerate(zip(points, points[1:]), down)), default=0.0)
                if speed > options.speed_max_m_s:
                    continue
                untouched = (frame_ms(down, offset) - frame_ms(full.down_frame, offset)
                             + frame_ms(full.up_frame, offset) - frame_ms(up, offset)) / 1000
                variants.append(replace(full, id=next_id, down_frame=down, up_frame=up, first_xy=points[0],
                                        last_xy=points[-1], max_contact_sample_speed_m_s=speed, untouched_s=untouched))
                next_id += 1
        out.append(tuple(variants))
    return out, next_id


def _joined(first: Occupation, second: Occupation, chart_ms: int) -> Occupation:
    """One touch along ``first``'s nodes before ``chart_ms`` and ``second``'s after it."""
    before = [k for k, note in enumerate(first.notes) if note.start < chart_ms]
    after = [k for k, note in enumerate(second.notes) if note.end > chart_ms]
    if not before or not after:
        raise ValueError(f"the two chains have no nodes on both sides of {chart_ms} ms")
    if abs(first.notes[before[-1]].end - second.notes[after[0]].start) > 1:
        raise ValueError(f"the two chains do not change node together at {chart_ms} ms")
    indices = tuple(first.note_indices[k] for k in before) + tuple(second.note_indices[k] for k in after)
    points = tuple(p for p in first.score_points if p.note_index in indices[:len(before)]) + tuple(
        p for p in second.score_points if p.note_index in indices[len(before):])
    return replace(first, occupation_id=f"{first.occupation_id}>{second.occupation_id}",
                   source_event_ids=tuple(first.source_event_ids[k] for k in before)
                   + tuple(second.source_event_ids[k] for k in after),
                   note_indices=indices,
                   notes=tuple(first.notes[k] for k in before)
                   + tuple(second.notes[k] for k in after),
                   score_points=points, end_ms=second.end_ms, up_window=second.up_window)


def _swap_at_meeting(groups, occupations, chart_ms):
    """Two chains crossing at a node, held by fingers that go straight on.

    Both chains are held through ``chart_ms``, change node there and meet
    inside the grace distance. Each touch keeps its finger: its own
    chain up to the node, then the other chain. The DP then gives each
    joined touch to a hand like any chain, by the colour it starts with.
    """
    offset = occupations.audio_offset_ms
    frame = frame_at_or_after(chart_ms, offset)
    meeting = {i for pair in occupations.grace_pairs_at(frame_ms(frame, offset)) for i in pair}
    held = [k for k, group in enumerate(groups) if len(group) == 1 and group[0].occupation.kind == "chain"
            and group[0].down_frame < frame < group[0].up_frame
            and set(group[0].occupation.note_indices) & meeting]
    if len(held) != 2 or len({groups[k][0].occupation.colour for k in held}) != 2:
        raise ValueError(f"no two chains of different colours cross at {chart_ms} ms")
    a, b = (groups[k][0] for k in held)
    groups = list(groups)
    for k, mine, other in ((held[0], a, b), (held[1], b, a)):
        groups[k] = (replace(mine, occupation=_joined(mine.occupation, other.occupation, chart_ms),
                             up_frame=other.up_frame, last_xy=other.last_xy,
                             max_contact_sample_speed_m_s=max(mine.max_contact_sample_speed_m_s,
                                                              other.max_contact_sample_speed_m_s)),)
    return groups


def _split_at_handover(groups, occupations, projection, chart_ms, next_id, lead_frames=0, relay_options=None):
    """The chain held at ``chart_ms`` as two contacts that meet there.

    A colour changes finger only where it meets another colour, in grace;
    a time that is no such meeting is refused. The catching contact
    starts ``lead_frames`` before the other ends. With full arc holds both
    meeting chains can still be held there; see ``_relay_at_handover``.
    """
    offset = occupations.audio_offset_ms
    frame = frame_at_or_after(chart_ms, offset)
    meeting = {i for pair in occupations.grace_pairs_at(frame_ms(frame, offset)) for i in pair}
    held = [k for k, group in enumerate(groups) if len(group) == 1 and group[0].occupation.kind == "chain"
            and group[0].down_frame < frame < group[0].up_frame]
    held = [k for k in held if set(groups[k][0].occupation.note_indices) & meeting]
    if relay_options is not None and len(held) == 2:
        return _relay_at_handover(groups, held, projection, chart_ms, frame, relay_options), next_id
    if len(held) != 1:
        raise ValueError(f"no single chain meets another colour at {chart_ms} ms")
    k = held[0]
    whole = groups[k][0]
    prefix = replace(whole, up_frame=frame,
                     last_xy=target_xy(whole.occupation, frame_ms(frame - 1, offset), projection))
    start = frame - lead_frames
    if start <= whole.down_frame:
        raise ValueError(f"the catch at {chart_ms} ms would start before the chain")
    suffix = replace(whole, id=next_id, down_frame=start,
                     first_xy=target_xy(whole.occupation, frame_ms(start, offset), projection))
    groups = list(groups)
    groups[k] = (prefix,)
    # Keep the processing order by start: the suffix goes before the first
    # later contact that starts at or after the catch.
    at = next((i for i in range(k + 1, len(groups)) if min(c.down_frame for c in groups[i]) >= start), len(groups))
    groups.insert(at, (suffix,))
    groups = [tuple(replace(c, item_index=i) for c in group) for i, group in enumerate(groups)]
    return groups, next_id + 1


def _relay_at_handover(groups, held, projection, chart_ms, frame, options):
    """A reverse catch where both meeting chains are held whole.

    The finger on the chain that ends first rides it to its end and goes
    straight on along the other chain; that chain's own finger lets go on
    the same frame. Both happen inside the meeting's grace, which lets a
    held touch change colour and a release go without binding the colour.
    Every arc stays under a finger throughout.
    """
    ending, going = sorted((groups[k][0] for k in held), key=lambda c: c.occupation.end_ms)
    first, other = ending.occupation, going.occupation
    offset = ending.audio_offset_ms
    if first.colour == other.colour:
        raise ValueError(f"the chains meeting at {chart_ms} ms have one colour")
    if first.end_ms - chart_ms > rules.GRACE_MS:
        raise ValueError(f"the chain ending first at {chart_ms} ms ends {first.end_ms - chart_ms} ms after the meeting")
    relay = frame_at_or_after(first.end_ms + 1, offset)
    if not frame <= relay < going.up_frame:
        raise ValueError(f"no relay frame at {chart_ms} ms")
    after = [k for k, note in enumerate(other.notes) if note.end > first.end_ms]
    joined = replace(first, occupation_id=f"{first.occupation_id}>{other.occupation_id}",
                     source_event_ids=first.source_event_ids + tuple(other.source_event_ids[k] for k in after),
                     note_indices=first.note_indices + tuple(other.note_indices[k] for k in after),
                     notes=first.notes + tuple(other.notes[k] for k in after),
                     score_points=first.score_points + tuple(p for p in other.score_points if p.time_ms > first.end_ms),
                     end_ms=other.end_ms, up_window=other.up_window)
    points = [target_xy(joined, frame_ms(f, offset), projection) for f in range(ending.down_frame, going.up_frame)]
    speed = max((math.dist(a, b) / ((frame_ms(f + 1, offset) - frame_ms(f, offset)) / 1000)
                 for f, (a, b) in enumerate(zip(points, points[1:]), ending.down_frame)), default=0.0)
    if speed > options.speed_max_m_s:
        raise ValueError(f"the relay at {chart_ms} ms exceeds the contact speed limit")
    groups = list(groups)
    groups[held[0] if groups[held[0]][0] is ending else held[1]] = (
        replace(ending, occupation=joined, up_frame=going.up_frame, last_xy=points[-1],
                max_contact_sample_speed_m_s=speed),)
    groups[held[0] if groups[held[0]][0] is going else held[1]] = (
        replace(going, up_frame=relay, last_xy=target_xy(other, frame_ms(relay - 1, offset), projection)),)
    return groups


class CandidateGraph:
    def __init__(self, groups, projection, options):
        self.groups, self.projection, self.options = groups, projection, options
        self.candidates = {c.id: c for group in groups for c in group}
        self.travel = lru_cache(maxsize=None)(self.travel)
        self.crossing = lru_cache(maxsize=None)(self.crossing)
        self.apart = lru_cache(maxsize=None)(self.apart)
        self.repeat = lru_cache(maxsize=None)(self.repeat)
        self.distance = lru_cache(maxsize=None)(self.distance)
        self.too_quick_pass = lru_cache(maxsize=None)(self.too_quick_pass)
        self.fixed = dict(options.fixed_hands)

    def distance(self, previous: int, current: int):
        """Endpoint transfer lower bound; no invented home pose for a first contact."""
        if previous < 0:
            return 0.0
        return math.dist(self.candidates[previous].last_xy, self.candidates[current].first_xy)

    def travel(self, previous: int, current: int):
        if previous < 0:
            return 0.0
        a, b = self.candidates[previous], self.candidates[current]
        if b.down_frame - a.up_frame < self.options.min_air_frames:
            return None
        if (b.down_frame - a.up_frame < self.options.held_release_air_frames and a.held_to_end
                and a.occupation.kind in self.options.held_release_air_kinds and self.options.holds_to_end(a.occupation)):
            return None
        # Ended frame is observed separately. Use the last held sample for
        # distance, leaving the actual air interval for the movement bound.
        duration = (b.down_ms - a.up_ms) / 1000
        speed = self.distance(previous, current) / duration
        if speed > self.options.speed_max_m_s:
            return None
        return max(0.0, speed - self.options.speed_comfort_m_s) ** 2

    def too_quick_pass(self, previous: int, current: int, other: int, hand: str) -> bool:
        swapped, air_s = crossing_pass(self.candidates[previous], self.candidates[current], self.candidates[other],
                                       hand, self.projection, self.options.crossing_pass_margin_m)
        return swapped and air_s < self.options.crossing_pass_s

    def crossing(self, left: int, right: int):
        if left < 0 or right < 0:
            return 0.0
        a, b = self.candidates[left], self.candidates[right]
        begin, end = max(a.down_frame, b.down_frame), min(a.up_frame, b.up_frame)
        return sum(target_xy(a.occupation, frame_ms(f, a.audio_offset_ms), self.projection)[0]
                   > target_xy(b.occupation, frame_ms(f, b.audio_offset_ms), self.projection)[0]
                   for f in range(begin, end)) / 60

    def increment(self, state, candidate, hand):
        left, right = state
        previous = left if hand == "left" else right
        move = self.travel(previous, candidate.id)
        if move is None:
            return None
        other = right if hand == "left" else left
        if self.options.crossing_pass_s and previous >= 0 and other >= 0 and self.too_quick_pass(previous, candidate.id,
                                                                                               other, hand):
            return None
        if self.fixed and candidate.occupation.kind in ("tap", "sky") and \
                self.fixed.get(candidate.occupation.start_ms, hand) != hand:
            return None
        if max(left, right) >= 0:
            # The preceding processed item is one of these last candidates.
            most_recent = max((self.candidates[i] for i in state if i >= 0), key=lambda c: c.item_index)
            # Press order matters where a fresh press can take another note.
            # A full-hold fallback starts at its first tick, possibly after a
            # later-processed note on the other hand; each hand's own order
            # is still enforced by travel().
            relaxed = self.options.full_arc_hold and "chain" in (candidate.occupation.kind, most_recent.occupation.kind)
            if candidate.down_frame < most_recent.down_frame and not relaxed:
                return None
        new = (candidate.id, right) if hand == "left" else (left, candidate.id)
        usual = "left" if candidate.occupation.colour == 0 else "right"
        if any(start <= candidate.down_ms < end for start, end in self.options.reversed_spans):
            usual = "right" if usual == "left" else "left"
        colour = float(candidate.occupation.colour in (0, 1) and hand != usual)
        hold = candidate.occupation.kind == "hold"
        return new, Cost(colour, self.crossing(*new), move, 0.0,
                         self.apart(candidate.id, right if hand == "left" else left, hand),
                         self.repeat(previous, candidate.id), self.distance(previous, candidate.id),
                         0.0 if hold else candidate.untouched_s, candidate.untouched_s if hold else 0.0)

    def apart(self, current: int, other: int, hand: str):
        """Metres this contact starts on the far side of the other hand's place.

        The other hand's place is where it is touching now, or where it last
        touched, fading out over ``apart_memory_s`` of idleness: an idle hand
        is free to get out of the way, a busy one is not. The count starts
        ``apart_margin_m`` short of that place: two hands cannot take turns on
        one spot either.
        """
        if other < 0 or not self.options.apart_weight:
            return 0.0
        mine, theirs = self.candidates[current], self.candidates[other]
        idle = (self.nominal(current)[0] - self.nominal(other)[1]) / 1000
        if idle >= self.options.apart_memory_s:
            return 0.0
        if idle > 0:
            x, fresh = theirs.last_xy[0], 1 - idle / self.options.apart_memory_s
        else:
            held = min(max(mine.down_frame, theirs.down_frame), theirs.up_frame - 1)
            x, fresh = target_xy(theirs.occupation, frame_ms(held, theirs.audio_offset_ms), self.projection)[0], 1.0
        depth = mine.first_xy[0] - x if hand == "left" else x - mine.first_xy[0]
        return fresh * max(0.0, depth + self.options.apart_margin_m)

    def repeat(self, previous: int, current: int):
        if previous < 0 or not self.options.repeat_weight:
            return 0.0
        gap = (self.nominal(current)[0] - self.nominal(previous)[1]) / 1000
        return max(0.0, 1 - gap / self.options.repeat_comfort_s) ** 2

    def nominal(self, index: int):
        """Down and up time with a point note taken at its own time.

        The two terms above weigh which hand plays, not when: measured on the
        chosen frames they would pay the plan for shifting taps by a frame.
        """
        c = self.candidates[index]
        if c.occupation.continuous_contact:
            return c.down_ms, c.up_ms
        frame = round((c.occupation.start_ms + c.audio_offset_ms) * 60 / 1000)
        return frame_ms(frame, c.audio_offset_ms), frame_ms(frame + c.up_frame - c.down_frame, c.audio_offset_ms)


def plan_two_finger(occupations: OccupationSet, projection: GameplayProjection,
                    options: PlannerOptions = PlannerOptions()) -> Plan:
    options = replace(options, audio_offset_ms=occupations.audio_offset_ms)
    groups = build_candidates(occupations, projection, options)
    graph = CandidateGraph(groups, projection, options)
    states = {(-1, -1): (Cost(), None)}
    visited = 1
    for k, group in enumerate(groups):
        following = {}
        for state, (cost, node) in states.items():
            for candidate in group:
                for hand in ("left", "right"):
                    step = graph.increment(state, candidate, hand)
                    if step is None:
                        continue
                    new, increment = step
                    total = cost + increment
                    if new not in following or total.weighted(options) < following[new][0].weighted(options):
                        following[new] = (total, (node, candidate, hand))
        if not following:
            # Full holds reorder the groups by press time, so name the group's own item.
            item = group[0].occupation if group else occupations.items[k]
            raise ValueError(f"no candidate path at item {k}: {item.occupation_id} ({item.kind} at {item.start_ms} ms)")
        states = following
        visited += len(states)
    cost, node = min(states.values(), key=lambda value: value[0].weighted(options))
    choices = []
    while node is not None:
        node, candidate, hand = node
        choices.append((candidate, hand))
    return Plan(tuple(reversed(choices)), cost, options, visited)


def plan_report(plan: Plan, projection: GameplayProjection) -> dict:
    assignments, gaps, crossed = [], [], []
    for lifecycle_id, (candidate, hand) in enumerate(plan.choices, 1):
        assignments.append({"lifecycle_id": lifecycle_id, "occupation_id": candidate.occupation.occupation_id, "kind": candidate.occupation.kind,
                            "start_ms": candidate.occupation.start_ms, "end_ms": candidate.occupation.end_ms,
                            "colour": candidate.occupation.colour, "lane": candidate.occupation.lane,
                            "finger": {"hand": hand, "finger": "index"},
                            "down_ms": candidate.down_ms, "up_ms": candidate.up_ms,
                            "untouched_ms": round(candidate.untouched_s * 1000)})
    for hand in ("left", "right"):
        mine = [c for c, h in plan.choices if h == hand]
        for a, b in zip(mine, mine[1:]):
            gap = b.down_ms - a.up_ms
            gaps.append({"hand": hand, "previous": a.occupation.occupation_id, "next": b.occupation.occupation_id,
                         "gap_ms": gap, "next_down_ms": b.down_ms,
                         "speed_m_s": math.dist(a.last_xy, b.first_xy) / (gap / 1000)})
    passes = []
    for hand in ("left", "right"):
        mine = [c for c, h in plan.choices if h == hand]
        theirs = sorted((c for c, h in plan.choices if h != hand), key=lambda c: c.down_ms)
        for a, b in zip(mine, mine[1:]):
            # As in the DP: the other hand's contact pressed most recently before this landing.
            other = next((c for c in reversed(theirs) if c.down_ms <= b.down_ms), None)
            swapped, air_s = crossing_pass(a, b, other, hand, projection, plan.options.crossing_pass_margin_m)
            if swapped:
                passes.append({"hand": hand, "release_ms": a.up_ms, "land_ms": b.down_ms, "air_ms": round(1000 * air_s),
                               "previous": a.occupation.occupation_id, "next": b.occupation.occupation_id,
                               "other": other.occupation.occupation_id})
    end = max((c.up_ms for c, _ in plan.choices), default=0)
    open_frame = None
    offset = plan.options.audio_offset_ms
    for frame in range(max(0, frame_at_or_after(0, offset)), frame_at_or_after(end, offset) + 1):
        active = {h: c for c, h in plan.choices if c.down_frame <= frame < c.up_frame}
        cross = (len(active) == 2 and target_xy(active["left"].occupation, frame_ms(frame, offset), projection)[0]
                 > target_xy(active["right"].occupation, frame_ms(frame, offset), projection)[0])
        if cross and open_frame is None:
            open_frame = frame
        elif not cross and open_frame is not None:
            crossed.append({"start_ms": frame_ms(open_frame, offset), "end_ms": frame_ms(frame, offset)})
            open_frame = None
    chains = [c for c, _ in plan.choices if c.occupation.kind == "chain"]
    holds = [c for c, _ in plan.choices if c.occupation.kind == "hold"]
    arc_hold = {"full_arc_hold": plan.options.full_arc_hold, "chain_contacts": len(chains),
                "partial_holds": [{"occupation_id": c.occupation.occupation_id, "kind": c.occupation.kind, "hand": hand,
                                   "start_ms": c.occupation.start_ms, "end_ms": c.occupation.end_ms,
                                   "down_ms": c.down_ms, "up_ms": c.up_ms, "untouched_ms": round(c.untouched_s * 1000)}
                                  for c, hand in plan.choices if c.untouched_s > 0]}
    if plan.options.full_ground_hold:
        arc_hold.update(full_ground_hold=True, early_hold_spans=[list(s) for s in plan.options.early_hold_spans],
                        hold_contacts=len(holds), holds_held_to_end=sum(c.held_to_end for c in holds),
                        holds_in_early_spans=sum(not plan.options.holds_to_end(c.occupation) for c in holds))
    if plan.options.early_arc_spans:
        arc_hold.update(early_arc_spans=[list(s) for s in plan.options.early_arc_spans],
                        chains_in_early_spans=[{"occupation_id": c.occupation.occupation_id, "hand": hand,
                                                "start_ms": c.occupation.start_ms, "end_ms": c.occupation.end_ms,
                                                "down_ms": c.down_ms, "up_ms": c.up_ms}
                                               for c, hand in plan.choices if c.occupation.kind == "chain"
                                               and not plan.options.holds_to_end(c.occupation)])
    if plan.options.held_release_air_frames:
        arc_hold.update(held_release_air_frames=plan.options.held_release_air_frames,
                        held_release_air_kinds=list(plan.options.held_release_air_kinds))
    return {"assignments": assignments, "idle_intervals": gaps, "crossing_windows": crossed, "arc_hold": arc_hold,
            "crossing_passes": {"minimum_s": plan.options.crossing_pass_s,
                                "passes": sorted(passes, key=lambda p: p["release_ms"])},
            "idle_interval_distribution_ms": {"below_60": sum(g["gap_ms"] < 60 for g in gaps),
                                              "60_to_100": sum(60 <= g["gap_ms"] < 100 for g in gaps),
                                              "100_to_160": sum(100 <= g["gap_ms"] < 160 for g in gaps),
                                              "160_and_above": sum(g["gap_ms"] >= 160 for g in gaps)},
            "cost_components": asdict(plan.cost), "weighted_cost": plan.cost.weighted(plan.options),
            "cost_semantics": "Colour counts distinct chains with any segment on the unusual hand, once per chain. Handover counts chains actually transferred at the selected node. For the unsplit base DP, colour is simply its chain-start ownership penalty. distance_m sums straight-line transfers from each hand's last held point to its next first point, including comfortable-speed moves; first contacts have no assumed home pose. It is a planar lower bound, not a body path length. movement retains the squared excess-speed cost.",
            "options": asdict(plan.options), "states_visited": plan.states_visited,
            "max_contact_sample_speed_m_s": max((c.max_contact_sample_speed_m_s for c, _ in plan.choices), default=0.0),
            "handover_nodes": [asdict(node) for node in plan.handover_nodes],
            "speed_limit_scope": "Hard limit on adjacent 60fps contact samples and constant-speed air transfer between occupations. Continuous contact-path speed and body motion are not checked.",
            "optimality_scope": plan.optimality_scope,
            "not_implemented": ["colour-binding history in the DP state", "joint DP optimisation of meeting handover alternatives", "crossing upper/lower mode",
                                "body feasibility and visible lift", "timing reorder across occupation order", "continuous speed along contact paths"]}

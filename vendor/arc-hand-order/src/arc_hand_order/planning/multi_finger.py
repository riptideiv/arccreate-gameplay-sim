"""Hand-order DP with up to two fingers per hand, shared touches and dotted-arc bridges.

The same finite candidates, costs and constraints as ``two_finger.plan_two_finger``, over touches instead of
single contacts. A touch is one finger's press-to-lift lifecycle; it serves one or more candidates (parts):

* new: a fresh press for the candidate, on a hand's first slot when the hand is idle, or on its second slot
  while the other is down (``second_finger``; ``second_finger_again``: a repeat on that finger's spot beside a hold);
* attach (``share``): the candidate joins a touch that is down, when one point lies in every served note's
  region (``rules``), with ``share_margin_m`` to spare, on every frame. By the contact rules: no tap or sky note,
  one arc colour per touch except while the colours are within grace distance, and a hold only on the
  frame the touch begins (its press presses the hold); with ``sky_aim_drawn`` a sky note's region is its drawn width;
* bridge (``bridge_max_gap_ms``): a same-colour arc chain after a short gap, near where the previous chain of
  that touch ended: the finger stays down and slides, so there is no new press and no colour release; with
  ``jump_slides`` also a chain that starts where the arc jumps, slid to and caught late, the late head uncharged;
* takeover (``second_finger`` and ``share``): when both fingers are down, one hands the holds it is still on to
  the other (which must be in their lanes from then on) and lifts, so it can press the new candidate;
* meeting swap (``meeting_swaps``): where two arc chains held whole meet, the fingers may swap chains or relay one
  (see ``meeting_candidates``), as ``PlannerOptions.swaps`` and ``handovers`` make at given times only;
* covered hold (``covered_holds``): a pressed hold is kept by any touch in its lane, so a hold the arc fingers
  press and keep needs no touch of its own. Such holds (``coverable_holds``) are left out of the DP;
* covered arc (``covered_arcs``): the same for an arc whose colour stays in grace (another colour's arc within the
  grace distance) and whose hitbox the touches of tap and sky notes pass through on every tick (``coverable_arcs``),
  as a stream of sky notes between a blue and a red arc keeps both.

A hand's fast stream (``jack_weight``): a press less than ``jack_gap_ms`` after the same hand's previous one extends
the hand's run, kept in the DP state; presses beyond the first ``jack_free`` of a run are charged.
Acceleration (``accel_max_m_s2``): a hand's move in the air from one touch to its next, as a rest-to-rest minimum-jerk
move, should not need more peak acceleration than the limit; inside a touch, a jump composing makes (a note joining or
leaving it, the other finger coming or going) is spread over frames the same way, within the notes' regions. Each move
still beyond the limit costs ``accel_weight``.
Geometry features (``geometry_features`` or their extra weights): the crossing costs split by how the hands
cross (at one height; one above the other with the lower fingertip near or clear of the upper forearm), the
repeat cost by how far the hand moves, second-finger presses beside a held arc or hold, trades (a press where the
other hand has just lifted) and collisions of the two hands, each with an extra weight, as players weigh them
differently.

Learned preference (``preference``, ``preference_weight``): a tap or sky note pressed by one hand costs
preference_weight times -log p (or, ``preference_linear``, minus the score difference), p the probability a logistic
model gives that hand against the other, from what
the step knows (``preference_features``: the note, each hand's latest touch, the other notes of a double). The model
is fitted to recorded hand orders.

The second slot follows the hand shape: ``pair_min_m``..``pair_max_m`` apart, and when nearly level (|dy| below
``side_by_side_max_dy_m``) at most ``side_by_side_max_dx_m`` across. A finger on holds or arcs moves the least
distance inside its notes' regions (lane, hitbox) to stay within reach of the other finger and out of its way. With every option off the plan equals ``plan_two_finger``'s.
As there, optimality is over the finite candidates only.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field, fields, replace
from functools import cached_property, lru_cache
import math

import numpy as np

from arc_hand_order.camera import lane_x
from . import rules
from .occupations import ContactRegion, Occupation, OccupationSet, TimeWindow
from .projection import GAME_UNITS_PER_WORLD_UNIT, GameplayProjection
from .progress import ProgressReporter
from .two_finger import (Candidate, Cost, PlannerOptions, build_candidates, frame_at_or_after, frame_ms,
                         target_xy)

HANDS = ("left", "right")
LANE_X = {1: (-5000.0, -425.0), 2: (-425.0, 0.0), 3: (0.0, 425.0), 4: (425.0, 5000.0)}
LANE_HALF_DEPTH = 500.0
SKY_DRAWN_HALF_X = 217.5  # game units: half a sky note's drawn width (4.35 world units)
MIN_JERK_PEAK = 10 / math.sqrt(3)  # peak acceleration of a rest-to-rest minimum-jerk move of d in T, times d/T^2


def _min_jerk(s: float) -> float:
    """Share of a rest-to-rest minimum-jerk move done at time share ``s`` (0..1)."""
    return s * s * s * (10 - 15 * s + 6 * s * s)


@dataclass(frozen=True)
class MultiFingerOptions:
    allow_misses: bool = False
    forced_misses: tuple[str, ...] = ()
    tick_contacts: bool = False
    bridge_max_gap_ms: int = 0  # 0: off
    bridge_max_distance_m: float = 0.015
    # With bridges: a finger sliding on from an arc catches the next same-colour chain where it can, also where the arc
    # jumps (the chain starts as the last one ends, farther than a bridge reaches: as fast as speed_max_m_s and
    # accel_max_m_s2 let it, then following the arc), and the late head it misses is not charged: on the colour, it
    # cannot be there sooner, or it waits for an arc sweeping its way: a player follows a zigzag of such pieces with
    # one finger instead of lifting and catching each piece late. Chains with a gap between them are bridged only as
    # before (bridge_max_distance_m).
    jump_slides: bool = False
    share: bool = False
    share_margin_m: float = 0.004  # how far a shared point stays inside every served region
    share_weight: float = 2.0  # per candidate that joins a touch
    # Where the notes of a touch serving a tap or sky note change, its point moves at most this far in one frame (0:
    # only speed_max_m_s). A finger cannot tap a sky note and be 40 mm away, inside an arc's hitbox edge, one frame
    # later. Touches of arcs and holds alone are left alone: two arcs starting at one point and
    # splitting move it as fast as they part.
    share_jump_max_m: float = 0.0
    # A touch serving a sky note lands within the note's drawn width, not anywhere in its hitbox, which is 1.4 times
    # as wide: players aim at the note they see. One finger then no longer taps a sky note at the hitbox edge, far
    # from the drawn note, to stay on an arc beside it; players tap such sky notes with the other hand.
    sky_aim_drawn: bool = False
    second_finger: bool = False
    second_finger_weight: float = 10.0  # per touch on a hand that is already down
    # A second finger tapping a tap or sky note on the spot of the one it tapped last (the notes, not the touch points,
    # within bridge_max_distance_m; at most PlannerOptions.apart_memory_s before), during the arc or hold the hand's
    # other finger keeps, is not charged second_finger_weight again and needs no more notes at once than these two:
    # the hand is still set with two fingers (taps on one lane beside the hand's hold on the next).
    second_finger_again: bool = False
    second_finger_s_weight: float = 5.0  # per second two touches of one hand are down together
    # Extra, per second the two are nearly level (allowed like any other shape). Players often take such a pair with
    # the other hand: the hand whose index is on the sky arc right above takes the hold below with its thumb.
    side_by_side_weight: float = 0.0
    takeover_weight: float = 2.0
    # A finger pressed earlier sitting in an arc's hitbox when its colour is free to bind takes the colour (see
    # _Graph.steals); charged rather than forbidden, as some passages leave no other way.
    steal_weight: float = 300.0
    # Swaps and relays where two arc chains meet, charged PlannerOptions.handover_weight each (meeting_candidates).
    meeting_swaps: bool = False
    # Holds the arc fingers press and keep get no touch of their own (coverable_holds).
    covered_holds: bool = False
    # Arcs other touches keep under grace get no touch of their own (coverable_arcs).
    covered_arcs: bool = False
    # Runs of short jittering arc pieces are held as one, on a smoothed path (steady_runs).
    steady_arcs: bool = False
    # Short arc pieces the finger on a longer arc of another colour keeps under grace get no touch of their own
    # (enveloped_arcs; if one is not kept or the chart has no path with them left out, it is planned without any).
    enveloped_arcs: bool = False
    steady_max_piece_ms: int = 120
    steady_min_pieces: int = 3
    # A hand's presses less than jack_gap_ms after its previous press make a run; each press of a run after the
    # first jack_free costs jack_weight (0: off). One hand cannot keep up a fast stream for long; two alternate.
    jack_gap_ms: int = 150
    jack_free: int = 4
    jack_weight: float = 0.0
    # The most peak acceleration (m/s^2; 0: off) a finger may need, counted for a rest-to-rest minimum-jerk move
    # (MIN_JERK_PEAK d/T^2): a hand's move in the air between two touches (d over the air time T, from where _release
    # lets it go to where _arrival needs it; _Graph.air_over), and each jump composing makes inside a touch
    # (_Graph._ease). Players' moves between point notes rarely need more than about 60 m/s^2, and beyond 75 they
    # mostly use the other hand. Each move beyond it costs accel_weight: charged rather than forbidden, as a passage
    # may leave no other way (one hand's fast stream on two fingers, which is not modelled).
    accel_max_m_s2: float = 0.0
    accel_weight: float = 300.0
    # Per metre-second the hands are crossed beyond PlannerOptions.crossing_pass_margin_m (a left finger right of a
    # right finger while both are down), on top of the flat crossing_weight per second: a deep, long crossing is what
    # makes the body motion hard, and what players give the colours up for.
    crossing_depth_weight: float = 0.0
    # The gap after which a hand's next contact costs no repeat (0: PlannerOptions.repeat_comfort_s). Players play
    # again with one hand sooner than 0.25 s allows: most repeats on one spot 160-260 ms apart stay on one hand.
    repeat_comfort_s: float = 0.0
    # Geometry features, each with an extra weight (all 0: off). The first eight add to the weight of the cost they
    # are a part of (PlannerOptions apart, crossing, repeat; second_finger_weight) and may be negative down to minus it. A crossing (apart, crossing seconds) is
    # "level" when the two points are less than level_dy_m apart up the glass; otherwise the higher hand's forearm runs
    # from its fingertip toward its shoulder (shoulder_x_m either side of the centre, shoulder_y_m), and the crossing
    # is "near" when the lower fingertip is within forearm_near_m of that line across the glass, else "clear".
    # repeat_far: the repeat cost scaled by the hand's move (whole at repeat_far_m). second_held: a second-finger
    # press while the hand's other finger is on an arc or hold. trades: a press within trade_m of where the other hand
    # lifted less than trade_ms before. Collisions: each hand is a capsule on the glass from its fingertip
    # hand_length_m toward its shoulder, hand_radius_m thick; collision is the overlap (m) of the pressing hand with
    # the other hand's place at the press (fading as apart does), collision_s the overlap (m) times the seconds both
    # are down. geometry_features records them all even at weight 0; without it only the weighted ones are computed.
    geometry_features: bool = False
    level_dy_m: float = 0.02
    forearm_near_m: float = 0.02
    shoulder_x_m: float = 0.18
    shoulder_y_m: float = -0.38
    apart_level_weight: float = 0.0
    apart_near_weight: float = 0.0
    apart_clear_weight: float = 0.0
    crossing_level_weight: float = 0.0
    crossing_near_weight: float = 0.0
    crossing_clear_weight: float = 0.0
    repeat_far_m: float = 0.06
    repeat_far_weight: float = 0.0
    second_held_weight: float = 0.0
    trade_m: float = 0.025
    trade_ms: int = 150
    trade_weight: float = 0.0
    hand_length_m: float = 0.09
    hand_radius_m: float = 0.025
    collision_weight: float = 0.0
    collision_s_weight: float = 0.0
    # A hand pressing on the spot (within trade_m) where the other hand's arc or hold ended less than
    # arc_takeover_ms before, coming from further than arc_takeover_from_m: the hands swap the passage's roles,
    # where players keep the arc hand on its arc's end. Charged arc_takeover_weight. While the weight is set, each
    # hand's latest such arc or hold contact rides in the DP state until arc_takeover_ms after it ends, renewed
    # whenever that hand presses on the spot again, so a takeover one or two notes later counts too.
    arc_takeover_ms: int = 150
    # The window once the arc hand has pressed on its end spot again (it is still using the spot); 0: as above.
    arc_takeover_renewed_ms: int = 210
    arc_takeover_from_m: float = 0.04
    arc_takeover_weight: float = 0.0
    # Learned hand preference: (feature, weight) pairs of a logistic model over preference_features (the left hand's
    # features minus the right's give the log-odds of the left hand), charged preference_weight per -log p.
    preference: tuple[tuple[str, float], ...] = ()
    preference_weight: float = 0.0
    # Linear preference: charge minus the score difference itself instead of -log p (the score may then be fitted
    # jointly with the other costs); it can be negative.
    preference_linear: bool = False
    pair_min_m: float = 0.015
    pair_max_m: float = 0.10
    side_by_side_max_dy_m: float = 0.02
    side_by_side_max_dx_m: float = 0.075
    # Beam: at most this many DP states are kept after each item, the cheapest (0: no limit). Where it cuts,
    # the plan is no longer exact over the candidates; MultiPlan.pruned_items counts those items.
    max_states: int = 20000

    def __post_init__(self):
        if type(self.bridge_max_gap_ms) is not int or self.bridge_max_gap_ms < 0:
            raise ValueError("bridge_max_gap_ms must be a nonnegative integer")
        if type(self.max_states) is not int or self.max_states < 0:
            raise ValueError("max_states must be a nonnegative integer")
        if type(self.jack_gap_ms) is not int or self.jack_gap_ms < 0 or type(self.jack_free) is not int or self.jack_free < 1:
            raise ValueError("jack_gap_ms must be a nonnegative integer and jack_free a positive one")
        if not math.isfinite(self.jack_weight) or self.jack_weight < 0:
            raise ValueError("jack_weight must be finite and nonnegative")
        if (type(self.steady_max_piece_ms) is not int or self.steady_max_piece_ms < 1
                or type(self.steady_min_pieces) is not int or self.steady_min_pieces < 2):
            raise ValueError("steady_max_piece_ms must be a positive integer and steady_min_pieces at least 2")
        for name in ("bridge_max_distance_m", "share_margin_m", "share_weight", "share_jump_max_m", "second_finger_weight",
                     "second_finger_s_weight", "side_by_side_weight", "takeover_weight", "steal_weight", "pair_min_m", "pair_max_m",
                     "side_by_side_max_dy_m", "side_by_side_max_dx_m", "level_dy_m", "forearm_near_m", "shoulder_x_m",
                     "repeat_far_m", "trade_m", "hand_length_m", "hand_radius_m", "trade_weight",
                     "collision_weight", "collision_s_weight", "preference_weight", "arc_takeover_from_m",
                     "arc_takeover_weight", "accel_max_m_s2", "accel_weight", "repeat_comfort_s",
                     "crossing_depth_weight"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in EXTRA_WEIGHTS:
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        if (not math.isfinite(self.shoulder_y_m) or self.shoulder_y_m >= 0 or self.repeat_far_m <= 0
                or type(self.trade_ms) is not int or self.trade_ms < 0 or type(self.arc_takeover_ms) is not int
                or self.arc_takeover_ms <= 0 or type(self.arc_takeover_renewed_ms) is not int
                or self.arc_takeover_renewed_ms < 0):
            raise ValueError("shoulder_y_m must be negative, repeat_far_m positive and trade_ms a nonnegative integer")
        if self.pair_min_m > self.pair_max_m:
            raise ValueError("pair_min_m exceeds pair_max_m")
        if self.second_finger_weight + self.second_held_weight < 0:
            raise ValueError("second_held_weight takes the second-finger weight below 0")
        unknown = {name for name, _ in self.preference} - set(PREFERENCE_FEATURES)
        if unknown or not all(math.isfinite(w) for _, w in self.preference):
            raise ValueError(f"preference: unknown features {sorted(unknown)} or weights not finite")
        if self.preference_weight and not self.preference:
            raise ValueError("preference_weight needs a preference model")

    @cached_property
    def geometry(self) -> bool:
        """Whether any geometry feature is computed."""
        return self.geometry_features or any(getattr(self, name) for name in EXTRA_WEIGHTS)

    def computes(self, *weights: str) -> bool:
        """Whether the geometry feature charged by any of ``weights`` is computed: with geometry_features, or while
        one of them is set."""
        return self.geometry_features or any(getattr(self, name) for name in weights)


# Extra weights of the geometry features; the first eight may lower the weight they add to (check_weights,
# MultiFingerOptions.__post_init__ for second_held_weight).
EXTRA_WEIGHTS = ("apart_level_weight", "apart_near_weight", "apart_clear_weight", "crossing_level_weight",
                 "crossing_near_weight", "crossing_clear_weight", "repeat_far_weight", "second_held_weight",
                 "trade_weight", "collision_weight", "collision_s_weight", "arc_takeover_weight")


PREFERENCE_FEATURES = (
    "sky", "my_side", "mine_none", "mine_dist", "mine_dist_quick", "mine_quick", "mine_held", "mine_held_arc",
    "mine_same_spot", "mine_dy", "theirs_none", "theirs_dist", "theirs_held", "cross_level", "cross_near",
    "cross_clear", "cross_depth", "theirs_same_spot", "overlap", "double_order", "double_lower", "next1_dx",
    "next1_same", "next1_up", "next2_dx", "next2_up",
    # role keeping: what the press does to the hands' order and layers, and where an arc of this hand just ended
    "theirs_dy", "order_flip", "order_kept_crossed", "layer_flip", "layer_mine", "layer_theirs", "arc_end_dist",
    # interactions
    "double_layered_order", "cross_clear_held", "cross_level_quick", "next1_dx_quick", "flip_quick")


def preference_features(x, y, kind: str, hand: str, mine, theirs, double, ahead=()) -> dict:
    """Features of pressing a tap or sky note at glass point (x, y) mm with ``hand``. ``mine`` and ``theirs``:
    (x mm, y mm, still held, ms since it lifted, kind) of this hand's and the other hand's latest touch, or None;
    ``double``: (x, y) mm of the other notes of a double (within 25 ms); ``ahead``: (ms later, x, y) of the next
    one or two tap or sky notes, 25 to 400 ms after this one."""
    side = -1.0 if hand == "left" else 1.0
    f = {"sky": float(kind == "sky"), "my_side": side * x / 100}
    if mine is None:
        f["mine_none"] = 1.0
    else:
        mx, my, held, ago, mkind = mine
        d = math.dist((x, y), (mx, my)) / 100
        quick = math.exp(-ago / 150)
        f.update(mine_dist=d, mine_dist_quick=d * quick, mine_quick=quick, mine_held=float(held),
                 mine_held_arc=float(held) * (mkind == "chain"), mine_same_spot=float(d < 0.25) * quick,
                 mine_dy=abs(y - my) / 100 * quick, layer_mine=float(abs(y - my) < 20) * quick)
        if mkind == "chain" and not held and ago < 200:
            f["arc_end_dist"] = d * (1 - ago / 200)
    if theirs is None:
        f["theirs_none"] = 1.0
    else:
        tx, ty, held, ago, _ = theirs
        fresh = 1.0 if held else math.exp(-ago / 250)
        depth = (x - tx) if hand == "left" else (tx - x)
        kind_c = crossing_kind(_PREFERENCE_GEOMETRY, (x / 1000, y / 1000), hand, (tx / 1000, ty / 1000)) if depth > 5 else -1
        gap = math.dist((x, y), (tx, ty))
        f.update(theirs_dist=gap / 100 * fresh, theirs_held=float(held), cross_level=float(kind_c == 0) * fresh,
                 cross_near=float(kind_c == 1) * fresh, cross_clear=float(kind_c == 2) * fresh,
                 cross_depth=max(0.0, depth) / 100 * fresh, theirs_same_spot=float(gap < 25) * fresh,
                 overlap=hand_overlap(_PREFERENCE_GEOMETRY, (x / 1000, y / 1000), hand, (tx / 1000, ty / 1000)) * 20 * fresh,
                 theirs_dy=abs(y - ty) / 100 * fresh, layer_theirs=float(abs(y - ty) < 20) * fresh,
                 cross_clear_held=float(kind_c == 2) * float(held), cross_level_quick=float(kind_c == 0) * fresh)
        if mine is not None:
            mx, my, _, ago, _ = mine
            quick = math.exp(-ago / 150)
            before, after = mx - tx, x - tx  # this hand's side of the other hand, before and after the press
            if abs(before) > 5 and abs(after) > 5:
                if (before > 0) != (after > 0):
                    f["order_flip"] = fresh * quick
                elif (before > 0) == (hand == "left"):
                    f["order_kept_crossed"] = fresh * quick  # crossed already (as after an arc) and stays so
            vb, va = my - ty, y - ty
            if abs(vb) >= 20 and abs(va) >= 20 and (vb > 0) != (va > 0):
                f["layer_flip"] = fresh * quick
            f["flip_quick"] = f.get("order_flip", 0.0) + f.get("layer_flip", 0.0)
    if double:
        f["double_order"] = side * (sum(x > v for v, _ in double) - sum(x < v for v, _ in double))
        f["double_lower"] = float(any(y < w - 20 for _, w in double))
        f["double_layered_order"] = f["double_order"] * float(any(abs(y - w) >= 20 for _, w in double))
    for i, (later, nx, ny) in enumerate(ahead[:2], 1):
        w = math.exp(-later / 150)
        f[f"next{i}_dx"] = side * (nx - x) / 100 * w
        f[f"next{i}_up"] = (1.0 if ny > y + 20 else -1.0 if ny < y - 20 else 0.0) * w * side
        if i == 1:
            f["next1_same"] = float(math.dist((x, y), (nx, ny)) < 25) * w
            f["next1_dx_quick"] = f["next1_dx"] * f.get("mine_quick", 0.0)
    return f


def check_weights(options: PlannerOptions, multi: MultiFingerOptions):
    """Every cost a geometry feature is part of keeps a nonnegative weight."""
    for kind in ("level", "near", "clear"):
        if options.apart_weight + getattr(multi, f"apart_{kind}_weight") < 0:
            raise ValueError(f"apart_{kind}_weight takes the apart weight below 0")
        if options.crossing_weight + getattr(multi, f"crossing_{kind}_weight") < 0:
            raise ValueError(f"crossing_{kind}_weight takes the crossing weight below 0")
    if options.repeat_weight + multi.repeat_far_weight < 0:
        raise ValueError("repeat_far_weight takes the repeat weight below 0")


def crossing_kind(multi: MultiFingerOptions, p, hand: str, q) -> int:
    """How point p (glass, m) of ``hand`` and point q of the other hand cross: 0 level, 1 lower fingertip near the
    upper hand's forearm, 2 clear of it (MultiFingerOptions geometry features)."""
    if abs(p[1] - q[1]) < multi.level_dy_m:
        return 0
    upper, lower, upper_hand = (p, q, hand) if p[1] > q[1] else (q, p, "right" if hand == "left" else "left")
    sx = -multi.shoulder_x_m if upper_hand == "left" else multi.shoulder_x_m
    x_line = upper[0] + (sx - upper[0]) * (lower[1] - upper[1]) / (multi.shoulder_y_m - upper[1])
    return 1 if abs(x_line - lower[0]) < multi.forearm_near_m else 2


def hand_segment(multi: MultiFingerOptions, p, hand: str):
    """The hand on the glass: from fingertip p, hand_length_m toward the shoulder of ``hand``."""
    sx = -multi.shoulder_x_m if hand == "left" else multi.shoulder_x_m
    dx, dy = sx - p[0], multi.shoulder_y_m - p[1]
    scale = multi.hand_length_m / math.hypot(dx, dy)
    return p, (p[0] + dx * scale, p[1] + dy * scale)


def hand_overlap(multi: MultiFingerOptions, p, hand: str, q) -> float:
    """How far (m) the hand with its fingertip at p and the other hand at q overlap."""
    other = "right" if hand == "left" else "left"
    gap = _segment_distance(*hand_segment(multi, p, hand), *hand_segment(multi, q, other))
    return max(0.0, 2 * multi.hand_radius_m - gap)


_PREFERENCE_GEOMETRY = MultiFingerOptions()  # the crossing geometry the preference model was trained with

# The recommended options ("on5"): on4 (every planning feature and the jack cost) plus the arc-end takeover cost and
# cheaper clear layered crossings, the jitter pieces an arc of the other colour keeps, no jump from a sky note into an
# arc's hitbox edge, no move a finger cannot make, a hand's two fingers one above the other rather than level, a
# finger following an arc that jumps, quick repeats on one hand, and a second finger tapping its spot again beside the
# hand's hold. apart_clear_weight assumes PlannerOptions.apart_weight 10; repeat_comfort_s overrides the planner's
# 0.25 s.
RECOMMENDED = MultiFingerOptions(bridge_max_gap_ms=150, share=True, second_finger=True, meeting_swaps=True,
                                 covered_holds=True, covered_arcs=True, steady_arcs=True, jack_weight=20.0,
                                 arc_takeover_weight=5.0, apart_clear_weight=-8.0, enveloped_arcs=True,
                                 share_jump_max_m=0.035, accel_max_m_s2=75.0, side_by_side_weight=5.0,
                                 sky_aim_drawn=True, jump_slides=True, repeat_comfort_s=0.18, second_finger_again=True)


def _segment_distance(a0, a1, b0, b1) -> float:
    """Distance between segments a0-a1 and b0-b1 in the plane."""
    def point_segment(p, q0, q1):
        dx, dy = q1[0] - q0[0], q1[1] - q0[1]
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((p[0] - q0[0]) * dx + (p[1] - q0[1]) * dy) / length))
        return math.dist(p, (q0[0] + t * dx, q0[1] + t * dy))

    def side(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    if (side(a0, a1, b0) > 0) != (side(a0, a1, b1) > 0) and (side(b0, b1, a0) > 0) != (side(b0, b1, a1) > 0):
        return 0.0
    return min(point_segment(a0, b0, b1), point_segment(a1, b0, b1), point_segment(b0, a0, a1),
               point_segment(b1, a0, a1))


@dataclass(frozen=True)
class MultiCost:
    base: Cost = field(default_factory=Cost)
    shares: float = 0.0
    second_touches: float = 0.0
    second_s: float = 0.0
    side_by_side_s: float = 0.0
    takeovers: float = 0.0
    bridges: float = 0.0
    steals: float = 0.0
    jack_presses: float = 0.0  # presses beyond MultiFingerOptions.jack_free in a hand's fast run
    # Geometry features (MultiFingerOptions.geometry); the first seven split base.apart, base.crossing_seconds and
    # base.repeat by kind.
    apart_level: float = 0.0
    apart_near: float = 0.0
    apart_clear: float = 0.0
    crossing_level_s: float = 0.0
    crossing_near_s: float = 0.0
    crossing_clear_s: float = 0.0
    repeat_far: float = 0.0
    second_held: float = 0.0
    trades: float = 0.0
    collision: float = 0.0
    collision_s: float = 0.0
    arc_takeovers: float = 0.0
    preference: float = 0.0  # -log p of the hands chosen for tap and sky notes (MultiFingerOptions.preference)
    accel_over: float = 0.0  # moves needing more than MultiFingerOptions.accel_max_m_s2
    crossing_depth: float = 0.0  # metre-seconds crossed beyond the margin (MultiFingerOptions.crossing_depth_weight)
    missed_ticks: int = 0
    arc_trace_deviation_s: float = 0.0

    def __add__(self, other):
        return MultiCost(self.base + other.base, self.shares + other.shares,
                         self.second_touches + other.second_touches, self.second_s + other.second_s,
                         self.side_by_side_s + other.side_by_side_s, self.takeovers + other.takeovers,
                         self.bridges + other.bridges, self.steals + other.steals, self.jack_presses + other.jack_presses,
                         self.apart_level + other.apart_level, self.apart_near + other.apart_near,
                         self.apart_clear + other.apart_clear, self.crossing_level_s + other.crossing_level_s,
                         self.crossing_near_s + other.crossing_near_s, self.crossing_clear_s + other.crossing_clear_s,
                         self.repeat_far + other.repeat_far, self.second_held + other.second_held,
                         self.trades + other.trades, self.collision + other.collision,
                         self.collision_s + other.collision_s, self.arc_takeovers + other.arc_takeovers,
                         self.preference + other.preference, self.accel_over + other.accel_over,
                         self.crossing_depth + other.crossing_depth, self.missed_ticks + other.missed_ticks,
                         self.arc_trace_deviation_s + other.arc_trace_deviation_s)

    def weighted(self, options: PlannerOptions, multi: MultiFingerOptions):
        extra = (self.shares * multi.share_weight + self.second_touches * multi.second_finger_weight
                 + self.second_s * multi.second_finger_s_weight + self.side_by_side_s * multi.side_by_side_weight
                 + self.takeovers * multi.takeover_weight + self.steals * multi.steal_weight
                 + self.jack_presses * multi.jack_weight + self.preference * multi.preference_weight
                 + self.accel_over * multi.accel_weight + self.crossing_depth * multi.crossing_depth_weight)
        if multi.geometry:
            extra += (self.apart_level * multi.apart_level_weight + self.apart_near * multi.apart_near_weight
                      + self.apart_clear * multi.apart_clear_weight
                      + self.crossing_level_s * multi.crossing_level_weight
                      + self.crossing_near_s * multi.crossing_near_weight
                      + self.crossing_clear_s * multi.crossing_clear_weight + self.repeat_far * multi.repeat_far_weight
                      + self.second_held * multi.second_held_weight + self.trades * multi.trade_weight
                      + self.collision * multi.collision_weight + self.collision_s * multi.collision_s_weight
                      + self.arc_takeovers * multi.arc_takeover_weight)
        if options.scoring_arcs and options.prefer_arc_trace:
            extra += self.arc_trace_deviation_s * options.arc_trace_weight
        return self.base.weighted(options) + extra if extra else self.base.weighted(options)


@dataclass(frozen=True, eq=False)
class Part:
    """A candidate served by a touch on frames [start, stop)."""
    candidate: Candidate
    start: int
    stop: int


@dataclass(eq=False)
class Touch:
    id: int
    parts: tuple[Part, ...]
    bridges: tuple[tuple[int, int], ...]  # frames [a, b) held with no part: the slide between dotted arcs
    down: int
    up: int  # ended frame
    path: tuple[tuple[float, float], ...]  # frames down..up-1
    offset: int

    @property
    def first_xy(self):
        return self.path[0]

    @property
    def last_xy(self):
        return self.path[-1]

    @cached_property
    def down_ms(self):
        return frame_ms(self.down, self.offset)

    @cached_property
    def up_ms(self):
        return frame_ms(self.up, self.offset)

    def xy(self, frame: int):
        return self.path[frame - self.down]

    def active(self, frame: int) -> bool:
        return self.down <= frame < self.up

    @cached_property
    def latest(self) -> Candidate:
        """The candidate processed last (press-order rule)."""
        return max((p.candidate for p in self.parts), key=lambda c: c.item_index)

    @cached_property
    def end_part(self) -> Part:
        return max(self.parts, key=lambda p: (p.stop, p.candidate.item_index))

    @cached_property
    def single(self) -> Candidate | None:
        return self.parts[0].candidate if len(self.parts) == 1 and not self.bridges else None


@dataclass(frozen=True)
class FingerTouch:
    """A touch of the final plan: its hand, slot (0 first, 1 second finger) and lifecycle."""
    touch: Touch
    hand: str
    slot: int


@dataclass(frozen=True)
class MultiPlan:
    touches: tuple[FingerTouch, ...]
    cost: MultiCost
    options: PlannerOptions
    multi: MultiFingerOptions
    states_visited: int
    pruned_items: int = 0  # items after which the state beam (max_states) dropped states
    covered: tuple[Occupation, ...] = ()  # holds planned without a touch: other touches press and keep them
    steady: tuple[Occupation, ...] = ()  # runs of short arc pieces planned as one, on a smoothed path
    step_costs: tuple = ()  # (chart ms of the item, MultiCost increment) of each DP step on the chosen path
    skipped: tuple[Occupation, ...] = ()

    @property
    def choices(self) -> tuple[tuple[Candidate, str], ...]:
        """Every served candidate with its hand, in down order (as ``Plan.choices``)."""
        seen, parts = set(), []
        for t in sorted(self.touches, key=lambda t: t.touch.down):
            for p in t.touch.parts:
                if p.candidate.id not in seen:
                    seen.add(p.candidate.id)
                    parts.append((p.candidate, t.hand))
        return tuple(sorted(parts, key=lambda ch: (ch[0].down_frame, ch[0].item_index)))


# ---------------------------------------------------------------- regions


def _clip(poly, a, b, c):
    """Part of convex ``poly`` with a*x + b*y + c >= 0."""
    out = []
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        fp, fq = a * p[0] + b * p[1] + c, a * q[0] + b * q[1] + c
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def _inner(poly, region, margin):
    """``poly`` clipped to convex ``region`` shrunk by ``margin``."""
    area = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(region, region[1:] + region[:1]))
    sign = 1.0 if area > 0 else -1.0
    for p, q in zip(region, region[1:] + region[:1]):
        nx, ny = -(q[1] - p[1]) * sign, (q[0] - p[0]) * sign
        length = math.hypot(nx, ny)
        if length == 0:
            continue
        nx, ny = nx / length, ny / length
        poly = _clip(poly, nx, ny, -(nx * p[0] + ny * p[1]) - margin)
        if len(poly) < 3:
            return []
    return poly


def _nearest(poly, point):
    """Nearest point of convex ``poly`` to ``point`` (the point itself when inside)."""
    inside = True
    area = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(poly, poly[1:] + poly[:1]))
    for p, q in zip(poly, poly[1:] + poly[:1]):
        cross = (q[0] - p[0]) * (point[1] - p[1]) - (q[1] - p[1]) * (point[0] - p[0])
        if cross * area < 0:
            inside = False
            break
    if inside:
        return point
    best, best_d = None, math.inf
    for p, q in zip(poly, poly[1:] + poly[:1]):
        dx, dy = q[0] - p[0], q[1] - p[1]
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((point[0] - p[0]) * dx + (point[1] - p[1]) * dy) / length2))
        c = (p[0] + t * dx, p[1] + t * dy)
        d = math.dist(c, point)
        if d < best_d:
            best, best_d = c, d
    return best


class Regions:
    """The notes' regions (``rules``) as convex device polygons, at the planning projection; a sky note's is
    ``sky_half_x`` game units to either side (rules.SKY_TAP_X, or SKY_DRAWN_HALF_X for its drawn width)."""

    def __init__(self, projection: GameplayProjection, sky_half_x: float = rules.SKY_TAP_X):
        self.projection = projection
        self.sky_half_x = sky_half_x
        w, h = projection.width_m, projection.height_m
        self.screen = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        self.lanes = {}
        for lane, (x0, x1) in LANE_X.items():
            corners = [projection.ground_to_device(x, z) for x, z in
                       ((x0, -LANE_HALF_DEPTH), (x1, -LANE_HALF_DEPTH), (x1, LANE_HALF_DEPTH), (x0, LANE_HALF_DEPTH))]
            self.lanes[lane] = _inner(list(self.screen), corners, 0.0)

    def polygon(self, occupation, time_ms):
        region = occupation.region_at(min(max(time_ms, occupation.start_ms), occupation.end_ms))
        if region.lane is not None:
            return self.lanes[region.lane]
        if occupation.kind == "sky":
            # Heights are clamped to [SKY_FLOOR, SKY_INPUT_HEIGHT] first: a bound the clamp always meets is open
            # (taken to the bottom or top of the screen, about -400 and 950 on the judgement plane).
            low = region.y - rules.SKY_TAP_DOWN if region.y - rules.SKY_TAP_DOWN >= rules.SKY_FLOOR else -390.0
            high = region.y + rules.SKY_TAP_UP if region.y + rules.SKY_TAP_UP <= rules.SKY_INPUT_HEIGHT else 940.0
            box = self.projection.judgement_box_to_device((region.x - self.sky_half_x, low), (region.x + self.sky_half_x, high))
            return list(box)
        if occupation.kind != "chain":
            return None
        box = self.projection.judgement_box_to_device((region.x - rules.HITBOX_X, region.y - rules.HITBOX_Y),
                                                      (region.x + rules.HITBOX_X, region.y + rules.HITBOX_Y))
        return list(box)

    def shared(self, occupations, time_ms, margin):
        poly = list(self.screen)
        for occupation in occupations:
            region = self.polygon(occupation, time_ms)
            if region is None:
                return []
            poly = _inner(poly, region, margin)
            if not poly:
                return []
        return poly


# ---------------------------------------------------------------- meetings


@dataclass(frozen=True)
class _Switched(Occupation):
    """Arc chain ``first`` before ``switch_ms`` and ``second`` from then on: a finger going on along the other chain
    where the two meet (see ``meeting_candidates``)."""
    first: Occupation | None = None
    second: Occupation | None = None
    switch_ms: int = 0

    def region_at(self, time_ms: float):
        return (self.first if time_ms < self.switch_ms else self.second).region_at(time_ms)


def _end_colour(o):
    return o.second.colour if isinstance(o, _Switched) else o.colour


@dataclass(frozen=True)
class Meetings:
    mates: dict  # candidate id -> the candidate of the other chain it needs
    bases: dict  # candidate id -> its group's chain, for every chain candidate of a group with a meeting
    partners: dict  # chain -> chains it meets
    group_of: dict  # chain -> group index


def meeting_candidates(groups, projection: GameplayProjection, options: PlannerOptions):
    """Alternatives where two arc chains held whole meet, at every meeting.

    A meeting is a run of frames on which two chains of different colours are within the grace distance. Each such
    frame starts a grace of GRACE_MS for both colours (``rules``), in which a held touch may change colour and a
    release binds nothing. At the frame of the run where they are closest, the two fingers may swap chains,
    each going on along the other (an X). And if one chain ends before the grace does, its finger may go on along
    the other chain, whose own finger lets go there (a relay). Each alternative is a candidate in its chain's group,
    mated to the other chain's candidate it needs: the DP takes both or neither. Returns (groups, Meetings).
    """
    offset = options.audio_offset_ms
    reach = math.ceil((600 if options.scoring_arcs else rules.GRACE_MS) * 60 / 1000) + 1  # frames a grace lasts, as _Graph._grace_frames
    whole = [(k, c) for k, group in enumerate(groups) for c in group
             if c.occupation.kind == "chain" and c.occupation.colour != 3 and c.held_to_end
             and options.holds_to_end(c.occupation) and ">" not in c.occupation.occupation_id
             and c.down_frame == frame_at_or_after(c.occupation.start_ms, offset)]
    next_id = max((c.id for group in groups for c in group), default=-1) + 1
    extra = [[] for _ in groups]
    mates, partners, group_of = {}, {}, {}

    def switched(x, y, frame):
        ox, oy, at = x.occupation, y.occupation, frame_ms(frame, offset)
        values = {f.name: getattr(ox, f.name) for f in fields(Occupation)}
        values.update(occupation_id=f"{ox.occupation_id}>{oy.occupation_id}@{at}",
                      source_event_ids=ox.source_event_ids + oy.source_event_ids,
                      note_indices=ox.note_indices + oy.note_indices, notes=ox.notes + oy.notes,
                      score_points=tuple(p for p in ox.score_points if p.time_ms < at)
                      + tuple(p for p in oy.score_points if p.time_ms >= at),
                      end_ms=oy.end_ms, up_window=oy.up_window)
        o = _Switched(**values, first=ox, second=oy, switch_ms=at)
        points = [target_xy(o, frame_ms(f, offset), projection) for f in range(x.down_frame, y.up_frame)]
        speed = max((math.dist(a, b) / ((frame_ms(f + 1, offset) - frame_ms(f, offset)) / 1000)
                     for f, (a, b) in enumerate(zip(points, points[1:]), x.down_frame)), default=0.0)
        return Candidate(0, x.item_index, o, x.down_frame, y.up_frame, points[0], points[-1], offset, speed)

    def cut(y, frame):
        return replace(y, up_frame=frame, last_xy=target_xy(y.occupation, frame_ms(frame - 1, offset), projection))

    def pair(kx, cx, ky, cy):
        nonlocal next_id
        if max(cx.max_contact_sample_speed_m_s, cy.max_contact_sample_speed_m_s) > options.speed_max_m_s:
            return
        cx, cy = replace(cx, id=next_id), replace(cy, id=next_id + 1)
        next_id += 2
        extra[kx].append(cx)
        extra[ky].append(cy)
        mates[cx.id], mates[cy.id] = cy.id, cx.id
        x, y = groups[kx][0].occupation.occupation_id, groups[ky][0].occupation.occupation_id
        partners.setdefault(x, set()).add(y)
        partners.setdefault(y, set()).add(x)
        group_of[x], group_of[y] = kx, ky

    for n, (ka, a) in enumerate(whole):
        for kb, b in whole[n + 1:]:
            if a.occupation.colour == b.occupation.colour:
                continue
            run = []
            for f in range(max(a.down_frame, b.down_frame), min(a.up_frame, b.up_frame) + 1):
                if f < min(a.up_frame, b.up_frame):
                    now = frame_ms(f, offset)
                    pa, pb = a.occupation.region_at(now), b.occupation.region_at(now)
                    distance = math.dist((pa.x, pa.y), (pb.x, pb.y))
                    if distance < rules.GRACE_DISTANCE:
                        run.append((distance, f))
                        continue
                if not run:
                    continue
                closest, last = min(run)[1], run[-1][1]
                if max(a.down_frame, b.down_frame) < closest < min(a.up_frame, b.up_frame):
                    pair(ka, switched(a, b, closest), kb, switched(b, a, closest))
                (ke, e), (kg, g) = sorted(((ka, a), (kb, b)), key=lambda kc: kc[1].up_frame)
                if e.up_frame < g.up_frame and run[0][1] < e.up_frame <= last + reach - 2:
                    pair(ke, switched(e, g, e.up_frame), kg, cut(g, e.up_frame))
                run = []
    bases = {c.id: group[0].occupation.occupation_id for k, group in enumerate(groups)
             if group and group[0].occupation.occupation_id in group_of for c in (*group, *extra[k])}
    return tuple(tuple(group) + tuple(extra[k]) for k, group in enumerate(groups)), Meetings(mates, bases, partners, group_of)


# ---------------------------------------------------------------- planner


class _Graph:
    def __init__(self, groups, projection, options: PlannerOptions, multi: MultiFingerOptions,
                 meetings: Meetings | None = None, occupations: OccupationSet | None = None):
        self.projection, self.options, self.multi = projection, options, multi
        # Missed arcs still exist in the game and can clear another colour's
        # lock through grace, even when they have no reachable candidates.
        self.arc_occupations = (tuple(replace(o,native_scoring=False) for o in occupations.items if o.kind == "chain")
                                if multi.tick_contacts and occupations is not None else
                                tuple(c.occupation for g in groups for c in g if c.occupation.kind == "chain"))
        self.meetings = meetings
        self.regions = (Regions(projection, SKY_DRAWN_HALF_X if multi.sky_aim_drawn else rules.SKY_TAP_X)
                        if (multi.share or multi.second_finger or options.scoring_arcs) else None)
        self.offset = options.audio_offset_ms
        self.fixed = dict(options.fixed_hands)
        self.touches: list[Touch] = []
        self.interned: dict = {}
        self.targets = {}
        self.trace_preference = None
        if options.scoring_arcs and options.prefer_arc_trace and options.arc_trace_weight:
            from .arc_scoring import TracePreference
            self.trace_preference = TracePreference(groups, projection, self.offset)
        self.points = {}  # (occupation id, frame) -> planned target
        self.polygons = {}  # (occupation ids, frame) -> shared region with the margin
        self.composed = {}
        self.futures = {}
        self.dense_cache = {}
        self.bindings = {}
        self.native_frame_regions = {}
        if options.scoring_arcs:
            from .native_input import geometry_for
            self.native_geometry = geometry_for(self.arc_occupations, projection, self.offset)
            self.native_frame_regions = self.native_geometry.regions
            self.graced = self.native_geometry.graced
        else:
            self.graced = self._grace_frames(groups) if (multi.share or multi.second_finger) else None
        self.bind_events = self._bind_events(groups) if self.graced is not None else None
        self.hold_points = {}
        self.busy = []
        if multi.second_finger:
            spans = []
            for group in groups:
                for o in {c.occupation.occupation_id: c.occupation for c in group
                          if not isinstance(c.occupation, _Switched)}.values():
                    if o.continuous_contact:
                        spans.append((frame_at_or_after(o.start_ms, self.offset), frame_at_or_after(o.end_ms + 1, self.offset)))
                    else:
                        spans.append((frame_at_or_after(o.start_ms - 25, self.offset),
                                      frame_at_or_after(o.start_ms + 26, self.offset)))
            self.busy = [0] * (max((b for _, b in spans), default=0) + 1)
            for a, b in spans:
                for f in range(max(0, a), b):
                    self.busy[f] += 1
        self.takeovers = {}
        # State index of each hand's latest arc or hold contact (arc_takeover_weight), after the slots and runs.
        # Only contested ones ride there: a tap or sky note starts on their end spot within arc_takeover_ms.
        self.own = (4 + (2 if multi.jack_weight else 0)) if multi.arc_takeover_weight else None
        self.lock_index = 4 + (2 if multi.jack_weight else 0) + (2 if multi.arc_takeover_weight else 0) if multi.tick_contacts else None
        # ArcCreate starts grace for every colour when any differently coloured
        # pair intersects. Grace clears an existing lock permanently.
        self.lock_grace = sorted(self.graced if self.graced is not None else self._grace_frames(groups)) if multi.tick_contacts else []
        self.arc_notes = {(o.colour,n.start,n.end,n.interval) for o in self.arc_occupations for n in o.notes}
        self.contested = self._contested(groups) if self.own is not None else frozenset()
        # The geometry features computed (MultiFingerOptions.computes); kinds: crossing seconds by kind, collision_s.
        self.wants_apart = multi.computes("apart_level_weight", "apart_near_weight", "apart_clear_weight")
        self.wants_kinds = multi.computes("crossing_level_weight", "crossing_near_weight", "crossing_clear_weight",
                                          "collision_s_weight")
        self.wants_collision = multi.computes("collision_weight")
        self.wants_repeat_far = multi.computes("repeat_far_weight")
        self.wants_second_held = multi.computes("second_held_weight")
        self.wants_trades = multi.computes("trade_weight")
        self.wants_takeovers = multi.computes("arc_takeover_weight")
        self.pref_weights = dict(multi.preference)
        self.doubles = {}  # item index -> (x, y) mm of the items starting within 25 ms (preference_features)
        self.ahead = {}  # item index -> (ms later, x, y) of the next two point notes 25-400 ms on
        if multi.preference_weight:
            self.preference_context(groups)
        self.crossing = lru_cache(maxsize=None)(self.crossing)
        self.crossing_depth = lru_cache(maxsize=None)(self.crossing_depth)
        self.crossing_kinds = lru_cache(maxsize=None)(self.crossing_kinds)
        self.overlap_cost = lru_cache(maxsize=None)(self.overlap_cost)

    def preference_context(self, groups):
        """Fill ``doubles`` and ``ahead`` for preference_features."""
        starts = sorted((g[0].occupation.start_ms, g[0].item_index, g[0].first_xy, g[0].occupation.kind)
                        for g in groups if g)
        for a in starts:
            self.doubles[a[1]] = tuple((b[2][0] * 1000, b[2][1] * 1000) for b in starts
                                       if b[1] != a[1] and abs(b[0] - a[0]) <= 25)
            self.ahead[a[1]] = tuple((b[0] - a[0], b[2][0] * 1000, b[2][1] * 1000) for b in starts
                                     if b[3] in ("tap", "sky") and 25 < b[0] - a[0] < 400)[:2]

    def _grace_frames(self, groups):
        """Frame -> arc colours in grace: two arcs of different colours within the grace distance on that frame or
        in the GRACE_MS before (every such frame renews the grace of both colours)."""
        chains = list({o.parent_occupation_id or o.occupation_id: o for o in self.arc_occupations
                       if not isinstance(o, _Switched)}.values())
        graced = {}
        reach = math.ceil((600 if self.options.scoring_arcs else rules.GRACE_MS) * 60 / 1000) + 1
        for i, a in enumerate(chains):
            for b in chains[i + 1:]:
                if a.colour == b.colour or b.start_ms > a.end_ms or a.start_ms > b.end_ms:
                    continue
                for f in range(frame_at_or_after(max(a.start_ms, b.start_ms), self.offset),
                               frame_at_or_after(min(a.end_ms, b.end_ms) + 1, self.offset)):
                    now = frame_ms(f, self.offset)
                    pa, pb = a.region_at(now), b.region_at(now)
                    near = (abs(pa.x-pb.x)<=190 and abs(pa.y-pb.y)<=250) if self.options.scoring_arcs else math.dist((pa.x,pa.y),(pb.x,pb.y))<rules.GRACE_DISTANCE
                    if near:
                        for g in range(f, f + reach):
                            graced.setdefault(g, set()).update(range(4) if self.options.scoring_arcs else (a.colour,b.colour))
        return graced

    def _bind_events(self, groups):
        """(frame, chain) on which the chain's colour may be free to bind: from the chain's start to its first
        tick (its own finger may press only then), and around each end of its colour's grace while it runs.
        The colour then binds the first-pressed touch inside the hitbox (see ``steals``)."""
        chains, latest = {}, {}
        for g in groups:
            for c in g:
                o = c.occupation
                if o.kind == "chain" and o.colour != 3 and not isinstance(o, _Switched):  # colour 3 never binds
                    key = o.parent_occupation_id or o.occupation_id
                    chains[key] = replace(o,native_scoring=False) if self.options.scoring_arcs else o
                    latest[key] = max(latest.get(key, c.down_frame), c.down_frame)
        events = set()
        for oid, o in chains.items():
            a, b = frame_at_or_after(o.start_ms, self.offset), frame_at_or_after(o.end_ms + 1, self.offset)
            events.update((f, oid) for f in range(a, max(a, min(b - 1, latest[oid])) + 1))
            for f in range(a + 1, b):
                if o.colour in self.graced.get(f - 1, ()) and o.colour not in self.graced.get(f, ()):
                    events.update((g, oid) for g in range(f - 1, min(b, f + 3)))
        return sorted(((f, chains[oid]) for f, oid in events), key=lambda e: (e[0], e[1].occupation_id))

    def hold_point(self, o):
        """Where a finger holds ``o``: the lane centre on the judgement line, unless the hitbox of an arc whose
        colour may bind while the hold lasts (``_bind_events``) covers it. A finger pressed before the arc's
        own finger and inside the hitbox would take the colour, so the hold is then held at the nearest point
        of the lane's centre line clear of every such hitbox by the share margin (lower in the lane, below a
        ground arc), or at the centre if the lane has none."""
        found = self.hold_points.get(o.occupation_id)
        if found is None:
            found = target_xy(o, o.start_ms, self.projection)
            down, up = frame_at_or_after(o.start_ms, self.offset), frame_at_or_after(o.end_ms + 1, self.offset)
            boxes = [self.regions.polygon(chain, frame_ms(f, self.offset)) for f, chain in self.bind_events if down < f < up]
            margin = self.multi.share_margin_m

            def clear(q):
                for box in boxes:
                    n = _nearest(box, q)
                    if n == q or math.dist(n, q) < margin:
                        return False
                return True

            if not clear(found):
                lane = self.regions.shared([o], o.start_ms, margin)
                x = GAME_UNITS_PER_WORLD_UNIT * lane_x(o.lane)
                for z in sorted(range(-500, 501, 5), key=lambda z: (abs(z), z)):
                    q = self.projection.ground_to_device(x, float(z))
                    if lane and _nearest(lane, q) == q and clear(q):
                        found = q
                        break
            self.hold_points[o.occupation_id] = found
        return found

    # touches -----------------------------------------------------------

    # States whose touches differ only in what later moves cannot read are merged (the cheaper kept). Later
    # moves read a touch from the next press on (its unfinished parts, path, bridges), its end (time, point,
    # last note), the last processed note (press order), and holds it let go of within the takeover reach. Not
    # the crossing-pass rule, which looks back to the other hand's earlier release: with it on, nothing merges.
    TAKEOVER_REACH = 25

    def _contested(self, groups):
        """Occupation ids of arcs and holds whose end spot a tap or sky note lands on (within trade_m) less than
        arc_takeover_ms after they end."""
        ends = [(g[0].occupation.end_ms, g[0].last_xy, g[0].occupation.occupation_id) for g in groups
                if g and g[0].occupation.kind in ("chain", "hold")]
        points = sorted((g[0].occupation.start_ms, g[0].first_xy) for g in groups
                        if g and g[0].occupation.kind in ("tap", "sky"))
        starts = [t for t, _ in points]
        out = set()
        for end, xy, oid in ends:
            for k in range(bisect_left(starts, end), bisect_right(starts, end + self.multi.arc_takeover_ms)):
                if math.dist(points[k][1], xy) < self.multi.trade_m + 0.01:
                    out.add(oid)
                    break
        return frozenset(out)

    def tail_key(self, state, f0: int):
        """The state after the slots as later moves read it: fast-run lengths, and each hand's held contact while it
        can still be taken over (its id, else -1)."""
        locks = tuple(value if value >= frame_ms(f0,self.offset) else -1
                      for value in state[self.lock_index:]) if self.lock_index is not None else ()
        if self.own is None:
            return state[4:self.lock_index] + locks if self.lock_index is not None else state[4:]
        now = frame_ms(f0, self.offset)
        owned = tuple((self.touches[i].up, self.touches[i].last_xy, self.takeover_window(self.touches[i]))
                      if i >= 0 and self.touches[i].up_ms + self.takeover_window(self.touches[i]) > now else -1
                      for i in state[self.own:self.own + 2])
        return state[4:self.own] + owned + locks

    def takeover_window(self, touch: Touch) -> int:
        """How long after ``touch`` (a contested arc or hold, or its hand's later press on the spot) a takeover counts."""
        found = touch.__dict__.get("_window")
        if found is None:
            renewed = not any(p.candidate.occupation.kind in ("chain", "hold") for p in touch.parts)
            found = touch.__dict__["_window"] = ((self.multi.arc_takeover_renewed_ms or self.multi.arc_takeover_ms)
                                                 if renewed else self.multi.arc_takeover_ms)
        return found

    def is_contested(self, touch: Touch) -> bool:
        found = touch.__dict__.get("_contested")
        if found is None:
            found = touch.__dict__["_contested"] = any(p.candidate.occupation.occupation_id in self.contested
                                                       for p in touch.parts)
        return found

    def _own(self, slots, h, now_ms):
        """Keep hand h's latest arc or hold contact in its state entry, and drop entries too old to be taken over."""
        if self.own is None:
            return
        held = [self.touches[i] for i in slots[h:h + 2] if i >= 0 and self.is_contested(self.touches[i])]
        if held:
            slots[self.own + h // 2] = max(held, key=lambda t: t.up).id
        elif slots[self.own + h // 2] >= 0:
            i = slots[self.own + h // 2]  # pressing on its own spot again renews it
            latest = max((self.touches[j] for j in slots[h:h + 2] if j >= 0), key=lambda t: t.down, default=None)
            if (latest is not None and latest.id != i and latest.down_ms >= self.touches[i].up_ms - 1
                    and math.dist(latest.first_xy, self.touches[i].last_xy) < self.multi.trade_m):
                slots[self.own + h // 2] = latest.id
        for j in (0, 1):
            i = slots[self.own + j]
            if i >= 0 and self.touches[i].up_ms + self.takeover_window(self.touches[i]) <= now_ms:
                slots[self.own + j] = -1

    def owned_by_other(self, state, h):
        """The other hand's arc or hold contact in the state (arc_takeover_weight), or None."""
        if self.own is None:
            return None
        i = state[self.own + 1 - h // 2]
        return self.touches[i] if i >= 0 else None

    def future(self, touch_id: int, f0: int):
        """What later moves can read of a touch, when no later press comes before frame ``f0``."""
        if touch_id < 0:
            return -1
        key = (touch_id, f0)
        found = self.futures.get(key)
        if found is None:
            t = self.touches[touch_id]
            end, latest = t.end_part, t.latest
            c = end.candidate
            found = (tuple((p.candidate.id, p.start, p.stop) for p in t.parts if p.stop > f0
                           or (p.candidate.occupation.kind == "hold" and p.stop > f0 - self.TAKEOVER_REACH)),
                     t.up, t.last_xy, c.occupation.occupation_id, c.up_frame, end.stop, c.held_to_end,
                     c.down_frame if not c.occupation.continuous_contact else None,
                     latest.down_frame, latest.item_index, latest.occupation.kind,
                     t.down if t.down >= f0 else None, t.path[max(0, f0 - t.down):] if t.up > f0 else None,
                     tuple(b for b in t.bridges if b[1] > f0))
            self.futures[key] = found
        return found

    def _target_path(self, c: Candidate):
        path = self.targets.get(c.id)
        if path is None:
            path = c.contact_path if c.contact_path is not None else tuple(self.point(c.occupation, f) for f in range(c.down_frame, c.up_frame))
            self.targets[c.id] = path
        return path

    def _intern(self, parts, bridges, path):
        key = (tuple((p.candidate.id, p.start, p.stop) for p in parts), bridges, path)
        touch = self.interned.get(key)
        if touch is None:
            down = min(min(p.start for p in parts), min((a for a, _ in bridges), default=math.inf))
            touch = Touch(len(self.touches), parts, bridges, down, down + len(path), path, self.offset)
            self.touches.append(touch)
            self.interned[key] = touch
        return touch

    def single(self, c: Candidate) -> Touch:
        return self._intern((Part(c, c.down_frame, c.up_frame),), (), self._target_path(c))

    def point(self, occupation, frame):
        key = (occupation.parent_occupation_id or occupation.occupation_id, frame)
        xy = self.points.get(key)
        if xy is None:
            if occupation.kind == "hold" and self.bind_events is not None:
                xy = self.points[key] = self.hold_point(occupation)
            else:
                xy = self.points[key] = target_xy(occupation, frame_ms(frame, self.offset), self.projection)
        return xy

    def shared(self, candidates, frame):
        key = (tuple(sorted(c.occupation.occupation_id for c in candidates)), frame)
        poly = self.polygons.get(key)
        if poly is None:
            poly = self.polygons[key] = self.regions.shared([c.occupation for c in candidates], frame_ms(frame, self.offset),
                                                            self.multi.share_margin_m)
        return poly

    def compose(self, parts, bridges=(), partner: Touch | None = None):
        """Touch serving ``parts`` (and sliding over ``bridges``); None if no point serves them all."""
        parts = tuple(sorted(parts, key=lambda p: (p.start, p.candidate.item_index)))
        key = (tuple((p.candidate.id, p.start, p.stop) for p in parts), tuple(bridges), None if partner is None else partner.id)
        if key in self.composed:
            return self.composed[key]
        touch = self.composed[key] = self._compose(parts, tuple(bridges), partner)
        return touch

    def _compose(self, parts, bridges, partner):
        if len(parts) == 1 and not bridges and (parts[0].start, parts[0].stop) == (
                parts[0].candidate.down_frame, parts[0].candidate.up_frame) and not self._needs_reach(parts, partner):
            return self.single(parts[0].candidate)
        down = min(p.start for p in parts)
        up = max(p.stop for p in parts)
        path, previous, served = [], None, None
        kinds = {p.candidate.id: p.candidate.occupation.kind for p in parts}
        lives = []  # the candidates served on each frame (none on a bridge), for _ease
        for f in range(down, up):
            live = [p.candidate for p in parts if p.start <= f < p.stop]
            lives.append(tuple(live))
            if not live:
                span = next(((a, b) for a, b in bridges if a <= f < b), None)
                if span is None:
                    return None
                after = next(p for p in sorted(parts, key=lambda p: p.start) if p.start >= span[1])
                a_xy = path[span[0] - 1 - down]
                b_xy = self.point(after.candidate.occupation, span[1])
                s = (f - span[0] + 1) / (span[1] - span[0] + 1)
                path.append((a_xy[0] + s * (b_xy[0] - a_xy[0]), a_xy[1] + s * (b_xy[1] - a_xy[1])))
                previous, served = path[-1], None  # a bridge has its own distance rule
                continue
            xy = self._anchor(live, f)
            if xy is None:
                return None
            if partner is not None and partner.active(f):
                xy = self._reach(xy, live, f, partner.xy(f), previous)
                if xy is None:
                    return None
            if previous is not None and math.dist(previous, xy) * 60 > self.options.speed_max_m_s * 1.0001:
                return None
            now_served = {c.id for c in live}
            if (self.multi.share_jump_max_m and served is not None and now_served != served
                    and any(kinds[i] in ("tap", "sky") for i in now_served | served)
                    and math.dist(previous, xy) > self.multi.share_jump_max_m):
                return None
            path.append(xy)
            previous, served = xy, now_served
        over = 0
        if self.multi.accel_max_m_s2 and self.regions is not None:
            path, over = self._ease(path, lives, down, partner)
        # Bridge frames bypass the per-live-frame check above. A shared tick
        # can move their anchor, making a formerly valid bridge too fast.
        if any(math.dist(a,b)*60 > self.options.speed_max_m_s*1.0001 for a,b in zip(path,path[1:])):
            return None
        if self.options.scoring_arcs:
            from .arc_scoring import covers
            if any(covers(p.candidate.occupation,path,down,self.offset,self.projection) is None
                   for p in parts if p.candidate.occupation.native_scoring):return None
        touch = self._intern(parts, tuple(bridges), tuple(path))
        if over:
            touch.__dict__["_over"] = over
        return touch

    def _anchor(self, live, frame):
        """Where composing puts the point for the ``live`` candidates on ``frame`` before the other finger's reach: the
        note's planned point, or for several the nearest point of their shared region (with the margin) to the arc's
        (to the first note's if none is an arc); None if they share no point."""
        if self.options.scoring_arcs:
            mandatory=[c for c in live if not c.occupation.native_scoring]
            if not mandatory:
                c=max(live,key=lambda c:c.down_frame)
                if c.contact_path is not None and c.down_frame<=frame<c.up_frame:
                    return c.contact_path[frame-c.down_frame]
            else:
                poly=self.shared(live,frame)
                if poly:
                    arc=next((c for c in live if c.occupation.native_scoring),None)
                    preferred=arc.contact_path[frame-arc.down_frame] if arc is not None and arc.contact_path is not None else self.point(mandatory[0].occupation,frame)
                    return _nearest(poly,preferred)
                live=mandatory
        if len(live) == 1:
            return self.point(live[0].occupation, frame)
        poly = self.shared(live, frame)
        if not poly:
            return None
        chain = next((c for c in live if c.occupation.kind == "chain"), live[0])
        return _nearest(poly, self.point(chain.occupation, frame))

    def _ease(self, path, lives, down, partner):
        """``path`` (a point per frame from ``down``) with the jumps composing makes spread out for accel_max_m_s2,
        and how many jumps could not be.

        A jump is a frame on which the point moves unlike the notes live on both frames: a note joins the touch or
        leaves it, or the hand's other finger comes or goes (its reach). A finger cannot make it between two frames,
        so the change of offset becomes a rest-to-rest minimum-jerk move over the fewest frames within the limit:
        ahead of the jump (the finger moves before a note joins, or before the other finger needs room), or, where
        that would take the point out of the regions of the notes then live, after it (a note has left). Each moved
        point stays inside the regions of the notes live on its frame (with share_margin_m), within the hand shape and
        under the speed limit; a jump neither way can spread is left as it is and counted."""
        path, over = list(path), 0
        for k in range(1, len(path)):
            before, now = lives[k - 1], lives[k]
            f = down + k
            reach = partner is not None and partner.active(f) != partner.active(f - 1)
            ids = {c.id for c in before}
            if not before or not now or ([c.id for c in now] == [c.id for c in before] and not reach):
                continue
            kept = [c for c in now if c.id in ids]
            motion = (0.0, 0.0)
            if kept:
                a0, a1 = self._anchor(kept, f - 1), self._anchor(kept, f)
                if a0 is not None and a1 is not None:  # (composing found a shared point on both frames)
                    motion = (a1[0] - a0[0], a1[1] - a0[1])
            delta = (path[k][0] - path[k - 1][0] - motion[0], path[k][1] - path[k - 1][1] - motion[1])
            frames = math.ceil(math.sqrt(MIN_JERK_PEAK * math.hypot(*delta) / self.multi.accel_max_m_s2) * 60 - 1e-9)
            if frames <= 1:
                continue
            ahead = {j: _min_jerk((j - k + frames) / frames) for j in range(max(0, k - frames + 1), k)}
            after = {j: _min_jerk((j - k + 1) / frames) - 1 for j in range(k, min(len(path), k + frames - 1))}
            for shares in (ahead, after):
                trial = list(path)
                for j, s in shares.items():
                    trial[j] = (path[j][0] + s * delta[0], path[j][1] + s * delta[1])
                if self._eased_ok(trial, shares, lives, down, partner):
                    path = trial
                    break
            else:
                over += 1
        return path, over

    def _eased_ok(self, path, moved, lives, down, partner):
        """Whether the ``moved`` frames of ``path`` keep inside the regions of their live notes (with the margin), the
        hand shape and the speed limit."""
        for j in moved:
            if not lives[j]:
                return False  # a bridge: the slide has its own rule
            p = path[j]
            poly = self.shared(lives[j], down + j)
            if not poly or math.dist(_nearest(poly, p), p) > 1e-6:
                return False
            if partner is not None and partner.active(down + j) and not self.shape_ok(p, partner.xy(down + j)):
                return False
            for i in (j, j + 1):
                if 0 < i < len(path) and math.dist(path[i - 1], path[i]) * 60 > self.options.speed_max_m_s * 1.0001:
                    return False
        return True

    def _needs_reach(self, parts, partner):
        if partner is None:
            return False
        c = parts[0].candidate
        path = self._target_path(c)
        return any(partner.active(f) and not self.shape_ok(path[f - c.down_frame], partner.xy(f))
                   for f in range(c.down_frame, c.up_frame))

    def shape_ok(self, a, b):
        """Whether two fingers of one hand can be at device points a and b."""
        dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
        return (self.multi.pair_min_m <= math.hypot(dx, dy) <= self.multi.pair_max_m
                and (dy >= self.multi.side_by_side_max_dy_m or dx <= self.multi.side_by_side_max_dx_m))

    REACH_STEP_M = 0.004

    def _reach(self, xy, live, frame, partner_xy, previous=None):
        """A point of the served notes' regions (lane, hitbox, sky rectangle) that gives a hand shape with the
        partner: ``xy`` when it does, else where the finger already was (``previous``) when that still does,
        else the nearest such point to it on a 4 mm grid of the region. None if the region has none."""
        if self.shape_ok(xy, partner_xy):
            return xy
        poly = self.shared(live, frame)
        if not poly:
            return None
        if previous is not None and _nearest(poly, previous) == previous and self.shape_ok(previous, partner_xy):
            return previous
        anchor = xy if previous is None else previous
        xs, ys = [q[0] for q in poly], [q[1] for q in poly]
        step = self.REACH_STEP_M
        best, best_d = None, math.inf
        y = min(ys)
        while y <= max(ys):
            x = min(xs)
            while x <= max(xs):
                q = (x, y)
                d = math.dist(q, anchor)
                if d < best_d and self.shape_ok(q, partner_xy) and _nearest(poly, q) == q:
                    best, best_d = q, d
                x += step
            y += step
        return best

    # costs and constraints (as two_finger.CandidateGraph, on touches) ----

    def held_release(self, touch: Touch) -> bool:
        part = touch.end_part
        c = part.candidate
        return (part.stop == c.up_frame and c.held_to_end and c.occupation.kind in self.options.held_release_air_kinds
                and self.options.holds_to_end(c.occupation))

    def travel(self, previous: Touch | None, current: Touch):
        if previous is None:
            return 0.0
        if current.down - previous.up < self.options.min_air_frames:
            return None
        if current.down - previous.up < self.options.held_release_air_frames and self.held_release(previous):
            return None
        duration = (current.down_ms - previous.up_ms) / 1000
        distance = math.dist(previous.last_xy, current.first_xy)
        if duration <= 0:
            return 0.0 if duration == 0 and distance < 1e-9 else None
        speed = distance / duration
        if speed > self.options.speed_max_m_s:
            return None
        return max(0.0, speed - self.options.speed_comfort_m_s) ** 2

    def air_over(self, previous: Touch | None, current: Touch) -> float:
        """1.0 if the hand's move in the air from ``previous`` to ``current`` needs more peak acceleration than
        accel_max_m_s2 (as a rest-to-rest minimum-jerk move over the air time, from where _release lets it go to where
        _arrival needs it), else 0.0."""
        if not self.multi.accel_max_m_s2 or previous is None:
            return 0.0
        leave, a = _release(previous)
        land, b = _arrival(current)
        duration = (frame_ms(land, self.offset) - frame_ms(leave, self.offset)) / 1000
        if duration <= 0:
            return float(math.dist(a, b) > 1e-9)
        need = MIN_JERK_PEAK * math.dist(a, b) / duration ** 2
        return float(need > self.multi.accel_max_m_s2)

    def jumps_over(self, touch: Touch | None) -> int:
        """Jumps inside ``touch`` that _ease could not spread out within accel_max_m_s2."""
        return 0 if touch is None else touch.__dict__.get("_over", 0)

    def too_quick_pass(self, previous: Touch, current: Touch, other: Touch, hand: str) -> bool:
        begin, end = previous.up, current.down

        def other_x(frame):
            if other.down <= frame < other.up:
                return other.xy(frame)[0]
            return (other.first_xy if frame < other.down else other.last_xy)[0]
        side = 1.0 if hand == "left" else -1.0
        before = side * (other_x(begin) - previous.last_xy[0])
        after = side * (other_x(end) - current.first_xy[0])
        margin = self.options.crossing_pass_margin_m
        swapped = min(abs(before), abs(after)) >= margin and (before > 0) != (after > 0)
        return swapped and (current.down_ms - previous.up_ms) / 1000 < self.options.crossing_pass_s

    def crossing(self, left: int, right: int):
        a, b = self.touches[left], self.touches[right]
        begin, end = max(a.down, b.down), min(a.up, b.up)
        return sum(a.xy(f)[0] > b.xy(f)[0] for f in range(begin, end)) / 60

    def crossing_depth(self, left: int, right: int):
        """Metre-seconds the left touch is right of the right one by more than crossing_pass_margin_m."""
        a, b = self.touches[left], self.touches[right]
        margin = self.options.crossing_pass_margin_m
        return sum(max(0.0, a.xy(f)[0] - b.xy(f)[0] - margin) for f in range(max(a.down, b.down), min(a.up, b.up))) / 60

    def crossing_kinds(self, left: int, right: int):
        """``crossing`` seconds split (level, near, clear) by ``kind``, and the hands' overlap times seconds."""
        a, b = self.touches[left], self.touches[right]
        out = [0.0, 0.0, 0.0, 0.0]
        for f in range(max(a.down, b.down), min(a.up, b.up)):
            p, q = a.xy(f), b.xy(f)
            if p[0] > q[0]:
                out[self.kind(p, "left", q)] += 1 / 60
            out[3] += self.overlap(p, "left", q) / 60
        return tuple(out)

    def kind(self, p, hand: str, q) -> int:
        return crossing_kind(self.multi, p, hand, q)

    def overlap(self, p, hand: str, q) -> float:
        return hand_overlap(self.multi, p, hand, q)

    def nominal(self, touch: Touch):
        found = touch.__dict__.get("_nominal")
        if found is None:
            found = touch.__dict__["_nominal"] = self._nominal(touch)
        return found

    def _nominal(self, touch: Touch):
        c = touch.single
        if c is None or c.occupation.continuous_contact:
            return touch.down_ms, touch.up_ms
        frame = round((c.occupation.start_ms + c.audio_offset_ms) * 60 / 1000)
        return frame_ms(frame, c.audio_offset_ms), frame_ms(frame + c.up_frame - c.down_frame, c.audio_offset_ms)

    def preference(self, c: Candidate, new: Touch, mine, theirs, hand: str) -> float:
        """-log p(hand) for pressing tap or sky candidate ``c`` as ``new`` (MultiFingerOptions.preference)."""
        if not self.multi.preference_weight or c.occupation.kind not in ("tap", "sky"):
            return 0.0
        own = max((t for t in mine if t is not None), key=lambda t: t.down, default=None)
        other = max(theirs, key=lambda t: t.down, default=None)
        here = self._preference_score(c, new, hand, own, other)
        there = self._preference_score(c, new, "right" if hand == "left" else "left", other, own)
        z = there - here
        if self.multi.preference_linear:
            return z
        return z + math.log1p(math.exp(-z)) if z > 0 else math.log1p(math.exp(z))

    def _preference_score(self, c, new, hand, own, other):
        f = self.preference_vector(c, new, hand, own, other)
        return sum(self.pref_weights.get(k, 0.0) * v for k, v in f.items())

    def preference_vector(self, c, new, hand, own, other) -> dict:
        """preference_features of pressing candidate c as ``new`` with ``hand``, own and other the two hands'
        latest touches (needs preference_context)."""
        def state(t):
            if t is None:
                return None
            held = t.up > new.down
            x, y = t.xy(min(new.down, t.up - 1)) if held else t.last_xy
            return x * 1000, y * 1000, held, 0.0 if held else new.down_ms - t.up_ms, t.parts[0].candidate.occupation.kind
        x, y = new.first_xy
        return preference_features(x * 1000, y * 1000, c.occupation.kind, hand, state(own), state(other),
                                   self.doubles.get(c.item_index, ()), self.ahead.get(c.item_index, ()))

    def their_place(self, mine: Touch, theirs: Touch | None):
        """Where the other hand is or just was as ``mine`` presses, and how much it still counts: 1, fading to 0 over
        apart_memory_s of its idleness; None once it no longer does."""
        if theirs is None:
            return None
        idle = (self.nominal(mine)[0] - self.nominal(theirs)[1]) / 1000
        if idle >= self.options.apart_memory_s:
            return None
        if idle > 0:
            return theirs.last_xy, 1 - idle / self.options.apart_memory_s
        held = min(max(mine.down, theirs.down), theirs.up - 1)
        return theirs.xy(held), 1.0

    def apart(self, mine: Touch, theirs: Touch | None, hand: str):
        if not self.options.apart_weight:
            return 0.0
        found = self.their_place(mine, theirs)
        if found is None:
            return 0.0
        (x, y), fresh = found
        depth = mine.first_xy[0] - x if hand == "left" else x - mine.first_xy[0]
        return fresh * max(0.0, depth + self.options.apart_margin_m)

    def features(self, hand, previous, new: Touch, other: Touch | None, apart: float, partner=None,
                 owned: Touch | None = None) -> dict:
        """MultiCost geometry fields of pressing ``new`` (MultiFingerOptions.geometry) after the hand's ``previous``
        touch, with the other hand's latest touch ``other``, its arc or hold contact from the state ``owned`` (else
        ``other`` stands for it) and this press's apart cost."""
        out = {}
        found = self.their_place(new, other) if (apart and self.wants_apart) or self.wants_collision else None
        if found is not None:
            (x, y), fresh = found
            if apart and self.wants_apart:
                out[("apart_level", "apart_near", "apart_clear")[self.kind(new.first_xy, hand, (x, y))]] = apart
            hit = self.overlap(new.first_xy, hand, (x, y)) if self.wants_collision else 0.0
            if hit:
                out["collision"] = fresh * hit
        if self.wants_repeat_far and previous is not None and self.options.repeat_weight:
            rep = self.repeat(previous, new)
            if rep:
                out["repeat_far"] = rep * min(1.0, math.dist(previous.last_xy, new.first_xy) / self.multi.repeat_far_m)
        if (self.wants_second_held and partner is not None
                and any(p.candidate.occupation.kind in ("hold", "chain") for p in partner.parts)):
            out["second_held"] = 1.0
        if (self.wants_trades and other is not None and 0 <= new.down_ms - other.up_ms < self.multi.trade_ms
                and math.dist(new.first_xy, other.last_xy) < self.multi.trade_m):
            out["trades"] = 1.0
        if not self.wants_takeovers:
            return out
        if self.own is not None:
            held = owned  # a contested arc or hold end, or the owner's later press on the same spot
        else:
            held = other if other is not None and any(p.candidate.occupation.kind in ("chain", "hold")
                                                      for p in other.parts) else None
        if (held is not None and 0 <= new.down_ms - held.up_ms < (self.takeover_window(held) if self.own is not None
                                                                     else self.multi.arc_takeover_ms)
                and math.dist(new.first_xy, held.last_xy) < self.multi.trade_m
                and (previous is None or math.dist(previous.last_xy, held.last_xy) > self.multi.arc_takeover_from_m)):
            out["arc_takeovers"] = 1.0
        return out

    def repeat(self, previous: Touch | None, current: Touch):
        if previous is None or not self.options.repeat_weight:
            return 0.0
        gap = (self.nominal(current)[0] - self.nominal(previous)[1]) / 1000
        return max(0.0, 1 - gap / (self.multi.repeat_comfort_s or self.options.repeat_comfort_s)) ** 2

    def _run(self, state, h, mine, new: Touch):
        """The hand's fast-run length once ``new`` is pressed (state[4 + h // 2] before it), and 1.0 if this press
        is charged: it comes less than jack_gap_ms after the hand's previous press, beyond the first jack_free."""
        previous = max((t.down_ms for t in mine if t is not None), default=None)
        free = self.multi.jack_free
        fast = previous is not None and new.down_ms - previous < self.multi.jack_gap_ms
        run = min(state[4 + h // 2] + 1, free + 1) if fast else 1
        return run, float(run > free)

    def overlap_cost(self, a: int, b: int):
        """(seconds, side-by-side seconds) two touches of one hand are down together; None if the shape fails."""
        ta, tb = self.touches[a], self.touches[b]
        begin, end = max(ta.down, tb.down), min(ta.up, tb.up)
        seconds = side = 0
        for f in range(begin, end):
            (ax, ay), (bx, by) = ta.xy(f), tb.xy(f)
            if not self.shape_ok((ax, ay), (bx, by)):
                return None
            side += abs(ay - by) < self.multi.side_by_side_max_dy_m
            seconds += 1
        return seconds / 60, side / 60

    def colour(self, c: Candidate, hand: str):
        usual = "left" if c.occupation.colour == 0 else "right"
        if any(start <= c.down_ms < end for start, end in self.options.reversed_spans):
            usual = "right" if usual == "left" else "left"
        return float(c.occupation.colour in (0, 1) and hand != usual)

    def part_cost(self, c: Candidate, hand: str):
        hold = c.occupation.kind == "hold"
        handover = 0.5 if self.meetings is not None and c.id in self.meetings.mates else 0.0  # half of each pair
        return Cost(self.colour(c, hand), 0.0, 0.0, handover, 0.0, 0.0, 0.0,
                    0.0 if hold else c.untouched_s, c.untouched_s if hold else 0.0)

    def mates_ok(self, state, c: Candidate) -> bool:
        """A meeting alternative only with its mate, a chain taken plainly only with partners taken plainly: the
        partner processed first is down in ``state`` (they meet after both are down)."""
        m = self.meetings
        base = m.bases[c.id]
        served = {}
        for i in state[:4]:
            if i >= 0:
                for p in self.touches[i].parts:
                    other = m.bases.get(p.candidate.id)
                    if other in m.partners[base]:
                        served[other] = p.candidate.id
        mate = m.mates.get(c.id)
        if mate is not None:
            partner = m.bases[mate]
            return served[partner] == mate if partner in served else m.group_of[partner] > m.group_of[base]
        return not any(m.bases.get(m.mates.get(i)) == base for i in served.values())

    # transitions --------------------------------------------------------

    def steps(self, state, c: Candidate, hand: str):
        """(new state, cost increment, changes) for each way ``hand`` can take candidate ``c``."""
        h = 0 if hand == "left" else 2
        mine = [self.touches[i] if i >= 0 else None for i in state[h:h + 2]]
        theirs = [self.touches[i] for i in (state[2:4] if hand == "left" else state[0:2]) if i >= 0]
        d = c.down_frame
        active = [k for k, t in enumerate(mine) if t is not None and t.active(d)]
        if self.fixed and c.occupation.kind in ("tap", "sky") and self.fixed.get(c.occupation.start_ms, hand) != hand:
            return
        if self.meetings is not None and c.id in self.meetings.bases and not self.mates_ok(state, c):
            return
        latest = [self.touches[i].latest for i in state[:4] if i >= 0]
        most_recent = max(latest, key=lambda x: x.item_index) if latest else None

        def press_order_ok():
            if most_recent is None:
                return True
            relaxed = (c.occupation.parent_occupation_id or most_recent.occupation.parent_occupation_id or
                       (self.options.full_arc_hold and "chain" in (c.occupation.kind, most_recent.occupation.kind)))
            return not (c.down_frame < most_recent.down_frame and not relaxed)

        # Continue a scoring arc by waiting or travelling between catches.
        # No bend between catches is a physical fingertip constraint.
        if self.options.scoring_arcs and c.occupation.parent_occupation_id:
            from .arc_scoring import covers
            for k,t in enumerate(mine):
                if t is None or c.down_frame<t.down: continue
                if t.end_part.candidate.occupation.parent_occupation_id != c.occupation.parent_occupation_id:continue
                end=max(t.up,c.up_frame)
                paths=[t.path+(t.last_xy,)*(end-t.up)]
                if c.down_frame>=t.up:
                    span=c.down_frame-t.up+1
                    a,b=t.last_xy,c.first_xy
                    slide=tuple((a[0]+(b[0]-a[0])*(j+1)/span,a[1]+(b[1]-a[1])*(j+1)/span) for j in range(span))
                    paths.append(t.path+slide+self._target_path(c)[1:])
                    if self.trace_preference is not None:
                        from .arc_scoring import trace_position
                        centre = tuple(trace_position(c.occupation, f, self.offset, self.projection)
                                       for f in range(t.up, end))
                        straight = paths[-1][len(t.path):]
                        for amount in (.25, .5, .75, 1):
                            tail = tuple((x+(u-x)*amount, y+(v-y)*amount)
                                         for (x,y),(u,v) in zip(straight, centre))
                            paths.append(t.path+tail)
                for path in paths:
                    path = self.native_geometry.guard_path(c.occupation,path,t.down,fixed_prefix=len(t.path))
                    if len(path)!=end-t.down:continue
                    if any(math.dist(a,b)*60>self.options.speed_max_m_s*1.00001 for a,b in zip(path,path[1:])):continue
                    if covers(c.occupation,path,t.down,self.offset,self.projection) is None:continue
                    bridges=t.bridges+(((t.up,c.down_frame),) if c.down_frame>t.up else ())
                    new=self._intern(t.parts+(Part(c,c.down_frame,c.up_frame),),bridges,path)
                    step=self._finish(state,h,k,mine,theirs,new,t,hand,self.part_cost(c,hand),bridge=True)
                    if step is not None:yield step
        # Continue following the actual arc between scoring ticks, without a new
        # press. A fresh contact remains an alternative when release is useful.
        if c.occupation.parent_occupation_id and not self.options.scoring_arcs:
            for k,t in enumerate(mine):
                if t is None or c.down_frame < t.down:
                    continue
                parent = c.occupation.parent_occupation_id
                if t.end_part.candidate.occupation.parent_occupation_id != parent:
                    continue
                end = max(t.up,c.up_frame)
                if any(p == parent and t.down_ms <= stop and frame_ms(end-1,self.offset) >= start
                       for p,start,stop in self.options.contact_exclusions):
                    continue
                path = t.path + tuple(self.point(c.occupation,f) for f in range(t.up,end))
                if any(math.dist(a,b) / ((frame_ms(f+1,self.offset)-frame_ms(f,self.offset))/1000) > self.options.speed_max_m_s
                       for f,(a,b) in enumerate(zip(path,path[1:]),t.down)):
                    continue
                bridges = t.bridges + (((t.up,c.down_frame),) if c.down_frame > t.up else ())
                new = self._intern(t.parts+(Part(c,c.down_frame,c.up_frame),), bridges, path)
                step = self._finish(state,h,k,mine,theirs,new,t,hand,self.part_cost(c,hand),bridge=True)
                if step is not None:
                    yield step
        # Bridges: a same-colour chain after a short gap, from where the slot's touch let go.
        bridged = set()
        if self.multi.bridge_max_gap_ms and c.occupation.kind == "chain" and not c.occupation.parent_occupation_id and press_order_ok():
            for k, t in enumerate(mine):
                if t is None or t.active(d) or not self._bridgeable(t, c):
                    continue
                new = self.bridge(t, c)
                distance = math.dist(t.last_xy, c.first_xy)
                gap_s = (d - t.up + 1) / 60
                part = self.part_cost(c, hand)
                untouched = part.untouched_arc_s
                slide = self._jump_slide(t, c)
                if slide is not None:  # the finger slides to an arc that jumped
                    frames, distance = slide[1], math.dist(t.last_xy, slide[0][slide[1] - 1])
                    gap_s = frames / 60
                if self.multi.jump_slides:  # sliding on, the finger catches the arc where it can: no charge for the head
                    untouched -= max(0, frame_ms(d, self.offset) - frame_ms(
                        frame_at_or_after(c.occupation.start_ms, self.offset), self.offset)) / 1000
                base = Cost(part.colour, 0.0, max(0.0, distance / gap_s - self.options.speed_comfort_m_s) ** 2,
                            part.handover, 0.0, 0.0, distance, max(0.0, untouched), part.untouched_hold_s)
                step = self._finish(state, h, k, mine, theirs, new, t, hand, base, bridge=True)
                if step is not None:
                    bridged.add(k)  # a finger that can slide on does not lift and press again
                    yield step
        # A fresh press.
        if not active:
            if self.options.scoring_arcs and c.occupation.parent_occupation_id:
                from .arc_scoring import source_note
                if c.down_ms > source_note(c.occupation).end: return
            k = 0
            recent = max((t for t in mine if t is not None), key=lambda t: t.up, default=None)
            if (recent is not None and mine.index(recent) in bridged) or not press_order_ok():
                pass
            else:
                step = self._new(state, h, k, mine, theirs, recent, c, hand, partner=None)
                if step is not None:
                    yield step
        elif len(active) == 1 and self.multi.second_finger and press_order_ok():
            k = 1 - active[0]
            held, last = mine[active[0]], mine[k]
            again = (self.multi.second_finger_again and c.occupation.kind in ("tap", "sky") and last is not None
                     and last.down > held.down and all(p.candidate.occupation.kind in ("tap", "sky") for p in last.parts)
                     and any(p.candidate.occupation.kind in ("chain", "hold") for p in held.parts)
                     and frame_ms(d, self.offset) - last.down_ms <= 1000 * self.options.apart_memory_s
                     and any(math.dist(self._target_path(p.candidate)[0], self._target_path(c)[0])
                             <= self.multi.bridge_max_distance_m for p in last.parts))
            if (again or self.dense(c)) and k not in bridged and self._within_reach(c, held):
                step = self._new(state, h, k, mine, theirs, last, c, hand, partner=held, again=again)
                if step is not None:
                    yield step
        # Joining a touch that is down.
        if self.multi.share and c.occupation.kind in ("chain", "hold"):
            for k in [k for k in (0, 1) if mine[k] is not None and mine[k].active(d)]:
                t = mine[k]
                if c.occupation.parent_occupation_id and t.end_part.candidate.occupation.parent_occupation_id == c.occupation.parent_occupation_id:
                    continue
                if not self._may_join(t, c) or not self._may_share(t, c):
                    continue
                partner = mine[1 - k] if mine[1 - k] is not None else None
                new = self.compose(t.parts + (Part(c, c.down_frame, c.up_frame),), t.bridges, partner)
                if new is None:
                    continue
                step = self._finish(state, h, k, mine, theirs, new, t, hand, self.part_cost(c, hand), share=True)
                if step is not None:
                    yield step
        # Takeover: both fingers down; one passes its holds to the other and presses c.
        if self.multi.second_finger and self.multi.share and len(active) == 2 and press_order_ok():
            for giver_slot in (0, 1):
                step = self._takeover(state, h, giver_slot, mine, theirs, c, hand)
                if step is not None:
                    yield step

    def bridge(self, t: Touch, c: Candidate) -> Touch:
        """``t`` sliding on from where it let go to chain ``c`` and holding it (paths appended, not recomputed)."""
        key = ("bridge", t.id, c.id)
        touch = self.composed.get(key)
        if touch is None:
            d = c.down_frame
            jump = self._jump_slide(t, c)
            if jump is not None:
                slide = jump[0]
            else:
                a, b = t.last_xy, self._target_path(c)[0]
                slide = tuple((a[0] + (f - t.up + 1) / (d - t.up + 1) * (b[0] - a[0]),
                               a[1] + (f - t.up + 1) / (d - t.up + 1) * (b[1] - a[1])) for f in range(t.up, d))
            path = t.path + slide + self._target_path(c)
            touch = Touch(len(self.touches), t.parts + (Part(c, d, c.up_frame),), t.bridges + ((t.up, d),), t.down,
                          t.down + len(path), path, self.offset)
            if self.jumps_over(t):
                touch.__dict__["_over"] = self.jumps_over(t)  # the path it carries on
            self.touches.append(touch)
            self.composed[key] = touch
        return touch

    def bound_frames(self, touch: Touch):
        """Arc colour -> frames (outside that colour's grace) the touch holds it. It binds on its first frame on an
        arc of the colour and keeps it until it lifts, even sliding between dotted pieces or serving only a hold;
        grace releases it, and after grace it binds again only on a frame it serves an arc of that colour."""
        found = self.bindings.get(touch.id)
        if found is None and self.options.scoring_arcs:
            from .arc_scoring import world
            found={};owners=set()
            begin=touch.down
            prefix=touch.__dict__.get('_native_prefix')
            if prefix is not None:
                found={col:set(fs) for col,fs in self.bound_frames(prefix).items()}
                owners={col for col,fs in found.items() if prefix.up-1 in fs}
                begin=prefix.up
            for f in range(begin,touch.up):
                if self.graced.get(f):owners=set();continue
                if not owners:
                    point=world(self.projection,touch.xy(f))
                    owners={
                            colour for colour,ax,ay in self.native_frame_regions.get(f,())
                            if point.collides(ax,ay)}
                for colour in owners:found.setdefault(colour,set()).add(f)
            self.bindings[touch.id]=found
            return found
        if found is None:
            found = {}
            spans = self.arc_spans(touch)
            for colour in {s[0] for s in spans}:
                mine = [(a, b) for col, _, a, b in spans if col == colour]
                graced = self.graced
                frames, bound = set(), False
                for f in range(min(a for a, _ in mine), touch.up):
                    if colour in graced.get(f, ()):
                        bound = False
                        continue
                    if not bound and any(a <= f < b for a, b in mine):
                        if self.options.scoring_arcs:
                            from .arc_scoring import hit,world
                            xy=world(self.projection,touch.xy(f))
                            now=frame_ms(f,self.offset)
                            notes={id(n):n for col,o,a,b in spans if col==colour for n in o.notes}.values()
                            bound=any(n.start<=now<=n.end and hit(n,xy,now) for n in notes)
                        else:bound = True
                    if bound:
                        frames.add(f)
                found[colour] = frames
            self.bindings[touch.id] = found
        return found

    def arc_spans(self, touch: Touch):
        return self._spans(touch.parts)

    def _spans(self, parts):
        """(colour, chain, start, stop) of each part on an arc; a switched part is two, split at its switch."""
        spans = []
        for p in parts:
            o = p.candidate.occupation
            if o.kind != "chain":
                continue
            if isinstance(o, _Switched):
                f = frame_at_or_after(o.switch_ms, self.offset)
                spans += [(o.first.colour, o.first, p.start, min(p.stop, f)), (o.second.colour, o.second, max(p.start, f), p.stop)]
            else:
                spans.append((o.colour, o, p.start, p.stop))
        return [s for s in spans if s[2] < s[3]]

    def clash(self, touch: Touch, others) -> bool:
        """Whether ``touch`` and another touch hold one arc colour on the same frame: a colour binds one touch and
        takes no other."""
        mine = self.bound_frames(touch)
        if not mine:
            return False
        for other in others:
            for colour, frames in self.bound_frames(other).items():
                if colour in mine and not mine[colour].isdisjoint(frames):
                    return True
        return False

    def steals(self, touch: Touch, others) -> bool:
        """Whether a finger pressed before ``touch`` sits in the hitbox of an arc ``touch`` is to take, on a frame
        the colour is free to bind: the first frame of each stretch ``touch`` holds the colour (its first frame on
        it, or after grace). Touches are taken in press order and the first one inside the hitbox binds the colour:
        that finger keeps it, and once it lifts the colour takes no other finger for a while."""
        spans = self.arc_spans(touch)
        for colour, frames in self.bound_frames(touch).items():
            for f in sorted(g for g in frames if g - 1 not in frames):
                span = next((s for s in spans if s[0] == colour and s[2] <= f < s[3]), None)
                if span is None:
                    continue
                region = self.regions.polygon(span[1], frame_ms(f, self.offset))
                for other in others:
                    # bound on this frame: to this colour it is a clash (refused before), and a touch bound to
                    # another colour is not taken. A colour it takes only later does not protect it.
                    if (other.down >= touch.down or not other.active(f)
                            or any(f in frames for frames in self.bound_frames(other).values())):
                        continue
                    xy = other.xy(f)
                    if _nearest(region, xy) == xy:
                        return True
        return False

    def dense(self, c: Candidate) -> bool:
        """Whether more than two notes need contact at once somewhere in ``c``'s frames (holds and arcs from start
        to end, point notes within 25 ms): only there may a hand use a second finger. A chart property, so the
        order in which simultaneous notes are processed does not matter."""
        found = self.dense_cache.get(c.id)
        if found is None:
            found = self.dense_cache[c.id] = max(self.busy[max(0, c.down_frame):max(0, c.up_frame)], default=0) > 2
        return found

    def _within_reach(self, c: Candidate, partner: Touch) -> bool:
        """Quick test before composing a second finger: at its first, middle and last frames ``c``'s target is
        no farther from the partner than a hand spans plus the most a finger can move inside a note's region."""
        path = self._target_path(c)
        for f in (c.down_frame, (c.down_frame + c.up_frame - 1) // 2, c.up_frame - 1):
            if partner.active(f) and math.dist(path[f - c.down_frame], partner.xy(f)) > self.multi.pair_max_m + 0.06:
                return False
        return True

    def _may_share(self, t: Touch, c: Candidate) -> bool:
        """Quick test before composing a shared touch: a shared point exists at three frames of the overlap."""
        end = min(c.up_frame, max(p.stop for p in t.parts))
        for f in (c.down_frame, (c.down_frame + end - 1) // 2, end - 1):
            live = [p.candidate for p in t.parts if p.start <= f < p.stop]
            if live and not self.shared(live + [c], f):
                return False
        return True

    def _bridgeable(self, t: Touch, c: Candidate):
        last = t.end_part
        a = last.candidate
        if a.occupation.kind != "chain" or _end_colour(a.occupation) != c.occupation.colour or last.stop != t.up:
            return False
        gap = c.occupation.start_ms - a.occupation.end_ms
        if self._jump_slide(t, c) is not None:
            return True
        if not 0 < gap <= self.multi.bridge_max_gap_ms or c.down_frame < t.up:
            return False
        if math.dist(t.last_xy, c.first_xy) > self.multi.bridge_max_distance_m:
            return False
        frames = max(1, c.down_frame - t.up + 1)
        return math.dist(t.last_xy, c.first_xy) * 60 / frames <= self.options.speed_max_m_s

    def _jump_slide(self, t: Touch, c: Candidate):
        """(path over frames t.up .. c.down_frame - 1, frames the slide itself takes) for ``t`` sliding on to chain
        ``c``, the late-head candidate of a same-colour chain that starts as t's chain ends (no frame between them)
        farther than a bridge reaches (jump_slides); None if it is not one or the finger cannot get there in time.
        The slide is a rest-to-rest minimum-jerk move onto c's point within the speed and acceleration limits, then
        the finger follows c's point to c's first frame."""
        if not self.multi.jump_slides:
            return None
        key = ("jump", t.id, c.id)
        if key in self.composed:
            return self.composed[key]
        found = self.composed[key] = None
        last, d = t.end_part.candidate, c.down_frame
        start = frame_at_or_after(c.occupation.start_ms, self.offset)
        frames = d - t.up
        gap = c.occupation.start_ms - last.occupation.end_ms
        if (gap < 0 or start > t.up or frames < 1 or d <= start
                or (0 < gap and math.dist(t.last_xy, c.first_xy) <= self.multi.bridge_max_distance_m)):
            return None  # (a near chain after a gap is an ordinary bridge)
        a = t.last_xy
        for n in range(1, frames + 1):
            b = self.point(c.occupation, t.up + n - 1)
            distance, seconds = math.dist(a, b), n / 60
            if (1.875 * distance / seconds <= self.options.speed_max_m_s
                    and (not self.multi.accel_max_m_s2
                         or MIN_JERK_PEAK * distance / seconds ** 2 <= self.multi.accel_max_m_s2)):
                path = tuple((a[0] + _min_jerk(j / n) * (b[0] - a[0]), a[1] + _min_jerk(j / n) * (b[1] - a[1]))
                             for j in range(1, n + 1))
                path += tuple(self.point(c.occupation, f) for f in range(t.up + n, d))
                found = self.composed[key] = (path, n)
                break
        return found

    def _may_join(self, t: Touch, c: Candidate):
        kinds = {p.candidate.occupation.kind for p in t.parts}
        if c.down_frame >= t.up:
            return False
        if kinds & {"tap", "sky"}:
            # Only an arc, straight on from the point note the touch began with (its press took that note).
            return c.occupation.kind == "chain" and len(t.parts) == 1
        if c.occupation.kind == "chain":
            # Another colour only where grace lets both colours take any touch: the two arcs stay within
            # its distance on every frame they share the touch (a switched chain by the colour of each stretch).
            for colour, chain, start, stop in self._spans((Part(c, c.down_frame, c.up_frame),)):
                for other, o, a, b in self.arc_spans(t):
                    if other == colour:
                        continue
                    for f in range(max(start, a), min(stop, b)):
                        now = frame_ms(f, self.offset)
                        p, q = o.region_at(now), chain.region_at(now)
                        if math.dist((p.x, p.y), (q.x, q.y)) >= rules.GRACE_DISTANCE:
                            return False
        if c.occupation.kind == "hold":
            # The touch's own begin must press the hold, and one touch counts for one lane.
            if t.down != c.down_frame or any(p.candidate.occupation.kind == "hold" for p in t.parts):
                return False
        return True

    def _new(self, state, h, k, mine, theirs, previous, c, hand, partner, again=False):
        new = self.compose((Part(c, c.down_frame, c.up_frame),), (), partner)
        if new is None:
            return None
        move = self.travel(previous, new)
        if move is None:
            return None
        other = max(theirs, key=lambda t: t.down) if theirs else None
        if (self.options.crossing_pass_s and previous is not None and other is not None
                and self.too_quick_pass(previous, new, other, hand)):
            return None
        part = self.part_cost(c, hand)
        base = Cost(part.colour, 0.0, move, part.handover, self.apart(new, other, hand), self.repeat(previous, new),
                    0.0 if previous is None else math.dist(previous.last_xy, new.first_xy),
                    part.untouched_arc_s, part.untouched_hold_s)
        features = (self.features(hand, previous, new, other, base.apart, partner, self.owned_by_other(state, h))
                    if self.multi.geometry else {})
        if self.multi.preference_weight:
            features["preference"] = self.preference(c, new, mine, theirs, hand)
        if self.multi.accel_max_m_s2:
            features["accel_over"] = self.air_over(previous, new)
        moved = None
        if (partner is not None and not partner.bridges and all(p.candidate.occupation.kind in ("hold", "chain") for p in partner.parts)
                and self.overlap_cost(new.id, partner.id) is None):
            # A finger down on a hold or an arc slides within its region toward the new one, as far as needed.
            moved = self.compose(partner.parts, (), new)
            if moved is None or moved is partner:
                return None
        return self._finish(state, h, k, mine, theirs, new, None, hand, base, second=partner is not None,
                            partner_new=moved, features=features, second_free=again)

    def _crossing_delta(self, hand, theirs, new, old, measure=None):
        measure = measure or self.crossing
        total = 0.0
        for t in theirs:
            total += measure(*((new.id, t.id) if hand == "left" else (t.id, new.id)))
            if old is not None:
                total -= measure(*((old.id, t.id) if hand == "left" else (t.id, old.id)))
        return total

    def _depth_delta(self, hand, theirs, changes, into: dict):
        """Add the change in crossing depth of each (new, old) touch in ``changes`` to ``into`` (crossing_depth_weight)."""
        if self.multi.crossing_depth_weight:
            depth = sum(self._crossing_delta(hand, theirs, new, old, self.crossing_depth) for new, old in changes)
            if depth:
                into["crossing_depth"] = into.get("crossing_depth", 0.0) + depth

    def _kinds_delta(self, hand, theirs, new, old, into: dict):
        """Add the change in ``crossing_kinds`` (crossing seconds by kind, collision seconds) to MultiCost fields."""
        names = ("crossing_level_s", "crossing_near_s", "crossing_clear_s", "collision_s")
        for t in theirs:
            for touch, sign in ((new, 1), (old, -1)):
                if touch is not None:
                    pair = (touch.id, t.id) if hand == "left" else (t.id, touch.id)
                    for name, v in zip(names, self.crossing_kinds(*pair)):
                        if v:
                            into[name] = into.get(name, 0.0) + sign * v

    def _pair_delta(self, new, old, partner):
        """Change in (seconds, side-by-side seconds) of two fingers down; None if the hand shape fails."""
        if partner is None or max(new.down, partner.down) >= min(new.up, partner.up):
            now = (0.0, 0.0)
        elif not self.multi.second_finger:
            return None
        else:
            now = self.overlap_cost(new.id, partner.id)
            if now is None:
                return None
        if old is not None and partner is not None and max(old.down, partner.down) < min(old.up, partner.up):
            was = self.overlap_cost(old.id, partner.id) or (0.0, 0.0)
            now = (now[0] - was[0], now[1] - was[1])
        return now

    def native_claims_valid(self,new):
        from .arc_scoring import source_note,world,hit
        validated=new.__dict__.setdefault('_native_validated',set())
        bindings=self.bound_frames(new)
        for part in new.parts:
            o=part.candidate.occupation
            if not o.native_scoring or o.occupation_id in validated:continue
            for point in o.score_points:
                n=source_note(o,point)
                first=max(new.down,frame_at_or_after(point.contact_window.earliest_ms,self.offset))
                last=min(new.up,frame_at_or_after(point.contact_window.latest_ms+1,self.offset))
                if not any((self.graced.get(f) or (f in bindings.get(o.colour,()) and sum(f in fs for fs in bindings.values())==1))
                           and hit(n,world(self.projection,new.xy(f)),frame_ms(f,self.offset)) for f in range(first,last)):
                    return False
            validated.add(o.occupation_id)
        return True

    def _finish(self, state, h, k, mine, theirs, new, old, hand, base, *, share=False, second=False, bridge=False,
                partner_new=None, features=None, second_free=False):
        """State and cost increment for slot k of hand h becoming ``new`` (an extension of ``old`` if given);
        ``partner_new`` replaces the other slot's touch (the same notes, moved within reach); ``second_free``: a
        second-finger press not charged as one (second_finger_again)."""
        partner = mine[1 - k] if partner_new is None else partner_new
        if self.options.contact_exclusions:
            parents = {p.candidate.occupation.parent_occupation_id or p.candidate.occupation.occupation_id
                       for p in new.parts if p.candidate.occupation.kind == "chain"}
            if any(parent in parents and new.down_ms <= stop and frame_ms(new.up-1,self.offset) >= start
                   for parent,start,stop in self.options.contact_exclusions):
                return None
        if self.lock_index is not None and old is None:
            for part in new.parts:
                o = part.candidate.occupation
                if o.kind == "chain" and o.colour in range(4):
                    if state[self.lock_index+o.colour] >= new.down_ms:
                        return None
        if self.options.scoring_arcs:
            if old is not None and new.down==old.down and new.path[:len(old.path)]==old.path:
                new.__dict__.setdefault('_native_prefix',old)
                new.__dict__.setdefault('_native_validated',set()).update(old.__dict__.get('_native_validated',()))
            if not self.native_claims_valid(new):return None
        steal = 0.0
        if self.graced is not None:
            others = [t for t in (partner, *theirs) if t is not None]
            if self.clash(new, others):
                return None
            steal = float(self.steals(new, others))
        pair = self._pair_delta(new, old, partner)
        if pair is None:
            return None
        crossing = self._crossing_delta(hand, theirs, new, old)
        if partner_new is not None:
            crossing += self._crossing_delta(hand, theirs, partner_new, mine[1 - k])
        features = dict(features or {})
        trace_distance = 0.0
        if self.trace_preference is not None:
            deviation = self.trace_preference.deviation
            distance = self.trace_preference.distance
            trace_distance = distance(new) - distance(old)
            features['arc_trace_deviation_s'] = deviation(new) - deviation(old)
            if partner_new is not None:
                features['arc_trace_deviation_s'] += deviation(partner_new) - deviation(mine[1-k])
                trace_distance += distance(partner_new) - distance(mine[1-k])
        if self.wants_kinds:
            self._kinds_delta(hand, theirs, new, old, features)
            if partner_new is not None:
                self._kinds_delta(hand, theirs, partner_new, mine[1 - k], features)
        self._depth_delta(hand, theirs, ((new, old),) + (((partner_new, mine[1 - k]),) if partner_new is not None else ()),
                          features)
        if self.multi.accel_max_m_s2:
            jumps = self.jumps_over(new) - self.jumps_over(old)
            if partner_new is not None:
                jumps += self.jumps_over(partner_new) - self.jumps_over(mine[1 - k])
            if jumps:
                features["accel_over"] = features.get("accel_over", 0.0) + jumps
        base = Cost(base.colour, crossing, base.movement, base.handover,
                    base.apart, base.repeat, base.distance_m + trace_distance, base.untouched_arc_s, base.untouched_hold_s)
        slots = list(state)
        jack = 0.0
        if old is None and self.multi.jack_weight:
            slots[4 + h // 2], jack = self._run(state, h, mine, new)
        increment = MultiCost(base, float(share), float(second and not second_free), pair[0], pair[1], 0.0,
                              float(bridge), steal, jack, **features)
        slots[h + k] = new.id
        changes = ((h + k, new, "new" if old is None else "extend"),)
        if partner_new is not None:
            slots[h + 1 - k] = partner_new.id
            changes = ((h + 1 - k, partner_new, "extend"),) + changes
        elif old is None and not second and mine[1 - k] is not None and not mine[1 - k].active(new.down):
            slots[h + 1 - k] = -1  # an idle hand starts again from one finger
        self._own(slots, h, new.down_ms)
        if self.lock_index is not None:
            for colour in (set(self.bound_frames(new)) if self.options.scoring_arcs else {p.candidate.occupation.colour for p in new.parts if p.candidate.occupation.kind == "chain"}):
                if colour not in range(4): continue
                active = [(a,b,interval) for col,a,b,interval in self.arc_notes if col == colour and a <= new.up_ms <= b]
                unlock = -1
                if active:
                    interval = max(interval for _,_,interval in active)
                    duration = min(round(interval*4),1000) if math.isfinite(interval) else 1000
                    # ArcCreate clears this lock at the first frame with no arc
                    # of the colour in range, even before its timer expires.
                    end = max(b for _,b,_ in active)
                    while True:
                        following = [b for col,a,b,_ in self.arc_notes if col == colour and a <= end+1 and b > end]
                        if not following: break
                        end = max(following)
                    unlock = min(new.up_ms+duration,end)
                    index = bisect_left(self.lock_grace,new.up)
                    if index < len(self.lock_grace):
                        unlock = min(unlock,frame_ms(self.lock_grace[index],self.offset)-1)
                slots[self.lock_index+colour] = max(slots[self.lock_index+colour],unlock)
        return tuple(slots), increment, changes

    TAKEOVER_LEADS = (1, 2, 3, 4, 6, 8, 12, 18, 24)  # air frames tried before the new press

    def _takeover(self, state, h, giver_slot, mine, theirs, c, hand):
        giver, receiver = mine[giver_slot], mine[1 - giver_slot]
        if any(p.candidate.occupation.kind in ("tap", "sky") for p in receiver.parts):
            return None
        steps = [self._takeover_at(state, h, giver_slot, giver, receiver, theirs, c, hand, c.down_frame - lead)
                 for lead in self.TAKEOVER_LEADS if lead >= self.options.min_air_frames]
        steps = [step for step in steps if step is not None]
        # The cheapest release time: a longer handover can save a hurried move to the new note.
        return min(steps, key=lambda step: step[1].weighted(self.options, self.multi), default=None)

    def _takeover_at(self, state, h, giver_slot, giver, receiver, theirs, c, hand, release):
        key = (giver.id, receiver.id, c.id, release)
        if key not in self.takeovers:
            self.takeovers[key] = self._takeover_touches(giver, receiver, c, release)
        found = self.takeovers[key]
        if found is None:
            return None
        giver_new, receiver_new, new, move, after_old, after_new, before = found
        other = max(theirs, key=lambda t: t.down) if theirs else None
        part = self.part_cost(c, hand)
        if self.clash(receiver_new, theirs) or self.clash(new, theirs):
            return None
        steal = float(self.steals(receiver_new, theirs) or self.steals(new, [*theirs, receiver_new]))
        crossing = (self._crossing_delta(hand, theirs, new, None) + self._crossing_delta(hand, theirs, receiver_new, receiver)
                    + self._crossing_delta(hand, theirs, giver_new, giver))
        base = Cost(part.colour, crossing, move, part.handover, self.apart(new, other, hand), self.repeat(giver_new, new),
                    math.dist(giver_new.last_xy, new.first_xy), part.untouched_arc_s, part.untouched_hold_s)
        features = {}
        if self.trace_preference is not None:
            deviation = self.trace_preference.deviation
            features['arc_trace_deviation_s'] = (deviation(new) + deviation(receiver_new) - deviation(receiver)
                                                  + deviation(giver_new) - deviation(giver))
            distance = self.trace_preference.distance
            base = replace(base, distance_m=base.distance_m + distance(new) + distance(receiver_new)
                           - distance(receiver) + distance(giver_new) - distance(giver))
        if self.multi.preference_weight:
            features["preference"] = self.preference(c, new, (giver, receiver), theirs, hand)
        if self.multi.geometry:
            features.update(self.features(hand, giver_new, new, other, base.apart, receiver_new,
                                          self.owned_by_other(state, h)))
        if self.wants_kinds:
            for touch, was in ((new, None), (receiver_new, receiver), (giver_new, giver)):
                self._kinds_delta(hand, theirs, touch, was, features)
        self._depth_delta(hand, theirs, ((new, None), (receiver_new, receiver), (giver_new, giver)), features)
        if self.multi.accel_max_m_s2:
            over = (self.air_over(giver_new, new) + self.jumps_over(new) + self.jumps_over(receiver_new)
                    - self.jumps_over(receiver) + self.jumps_over(giver_new) - self.jumps_over(giver))
            if over:
                features["accel_over"] = over
        slots = list(state)
        jack = 0.0
        if self.multi.jack_weight:
            slots[4 + h // 2], jack = self._run(state, h, (giver, receiver), new)
        slots[h + giver_slot] = new.id
        slots[h + 1 - giver_slot] = receiver_new.id
        self._own(slots, h, new.down_ms)
        increment = MultiCost(base, 0.0, 1.0, after_old[0] + after_new[0] - before[0],
                              after_old[1] + after_new[1] - before[1], 1.0, 0.0, steal, jack, **features)
        changes = ((h + giver_slot, giver_new, "extend"), (h + 1 - giver_slot, receiver_new, "extend"),
                   (h + giver_slot, new, "new"))
        return tuple(slots), increment, changes

    def _takeover_touches(self, giver, receiver, c, release):
        """The giver cut at ``release``, the receiver with the giver's holds, and the new press; or None."""
        if release <= giver.down:
            return None
        moving = [p for p in giver.parts if p.stop > release]
        if not moving or any(p.candidate.occupation.kind != "hold" for p in moving):
            return None
        if any(p.candidate.occupation.kind == "hold" for p in receiver.parts if p.stop > release - 2):
            return None  # one lane per touch
        kept = tuple(Part(p.candidate, p.start, min(p.stop, release)) for p in giver.parts if p.start < release)
        handed = tuple(Part(p.candidate, max(p.start, release - 2), p.stop) for p in moving)
        receiver_new = self.compose(receiver.parts + handed, receiver.bridges, None)
        if receiver_new is None:
            return None
        giver_new = self.compose(kept, tuple(b for b in giver.bridges if b[1] <= release), receiver_new)
        if giver_new is None or giver_new.up != release:
            return None
        # The giver's slot presses c next: the same checks as a fresh second-finger press.
        new = self.compose((Part(c, c.down_frame, c.up_frame),), (), receiver_new)
        if new is None:
            return None
        move = self.travel(giver_new, new)
        if move is None:
            return None
        before = self.overlap_cost(giver.id, receiver.id) or (0.0, 0.0)
        after_old = self.overlap_cost(giver_new.id, receiver_new.id)
        after_new = self.overlap_cost(new.id, receiver_new.id)
        if after_old is None or after_new is None:
            return None
        if self.clash(receiver_new, [giver_new, new]) or self.clash(new, [giver_new]):
            return None
        return giver_new, receiver_new, new, move, after_old, after_new, before


def _release(touch: Touch):
    """Actual first frame off the glass and last contact point.

    Early release must be selected as a tick-based candidate, so the acceleration
    calculation, exported path and animation all use the same release. Giving a
    full hold hypothetical extra air time hides an infeasible animated move.
    """
    return touch.up, touch.last_xy


def _arrival(touch: Touch):
    """(frame, point) by which ``touch`` must be on the glass for the acceleration limit: its press, except that an arc
    or hold caught at its start may be caught as late as its first tick allows (the occupation's down_window), as
    players do after a far note. Point notes pressed with it keep the press."""
    frame = touch.up - 1
    for p in touch.parts:
        if p.start != touch.down:
            continue
        c = p.candidate
        latest = c.occupation.down_window.latest_ms
        if c.occupation.continuous_contact and p.start == c.down_frame and latest is not None:
            frame = min(frame, frame_at_or_after(latest + 1, touch.offset) - 1)
        else:
            frame = touch.down
    frame = max(touch.down, frame)
    return frame, touch.xy(frame)


def press_order(ft: FingerTouch):
    """Lifecycle order of the plan's touches (the order in which colours take touches): by press frame, and on one
    frame the touch that begins on an arc first, as a player catching an arc lands that finger a moment earlier. A
    tap or sky finger inside the arc's hitbox would otherwise take the colour and hold it from the arc's finger."""
    first = min(ft.touch.parts, key=lambda p: (p.start, p.candidate.item_index)).candidate
    return ft.touch.down, first.occupation.kind != "chain", ft.hand, ft.slot


def _lane_at(projection: GameplayProjection, xy) -> int | None:
    try:
        return rules.touch_lane(*projection.device_to_ground(*xy))
    except ValueError:  # above the horizon
        return None


def coverable_holds(occupations: OccupationSet, projection: GameplayProjection) -> tuple[Occupation, ...]:
    """Holds the arc chains' fingers can press and keep, at the chains' planned points (``target_xy``).

    A hold qualifies when a chain begins in its lane on a frame that presses it (start - HOLD_EARLY_MS < now <
    end), and each tick's contact window has a frame inside the hold with some chain's point in its lane: a hold
    binds no finger (``rules``).
    """
    offset = occupations.audio_offset_ms
    chains = [o for o in occupations.items if o.kind == "chain"]

    def inside(chain, frame, lane):
        t = frame_ms(frame, offset)
        return chain.start_ms <= t <= chain.end_ms and _lane_at(projection, target_xy(chain, t, projection)) == lane

    out = []
    for hold in (o for o in occupations.items if o.kind == "hold"):
        near = [c for c in chains if c.start_ms < hold.end_ms and c.end_ms > hold.start_ms]
        presses = [frame_at_or_after(c.start_ms, offset) for c in near]
        if not any(hold.start_ms - rules.HOLD_EARLY_MS < frame_ms(f, offset) < hold.end_ms and inside(c, f, hold.lane)
                   for c, f in zip(near, presses)):
            continue

        def kept(point):
            window = point.contact_window
            frame = frame_at_or_after(max(window.earliest_ms, hold.start_ms + 1), offset)
            while frame_ms(frame, offset) <= min(window.latest_ms, hold.end_ms - 1):
                if any(inside(c, frame, hold.lane) for c in near):
                    return True
                frame += 1
            return False

        if all(kept(p) for p in hold.score_points):
            out.append(hold)
    return tuple(out)


def _chain_part(chain: Occupation, keep) -> Occupation:
    """The arcs ``keep`` (consecutive, in chain order) of an arc chain as an occupation of its own."""
    rows = [row for row in zip(chain.note_indices, chain.source_event_ids, chain.notes) if row[0] in keep]
    points = tuple(p for p in chain.score_points if p.note_index in keep)
    return replace(chain, occupation_id=f"{chain.occupation_id}#{rows[0][0]}-{rows[-1][0]}",
                   source_event_ids=tuple(r[1] for r in rows), note_indices=tuple(r[0] for r in rows),
                   notes=tuple(r[2] for r in rows), score_points=points,
                   start_ms=min(r[2].start for r in rows), end_ms=max(r[2].end for r in rows),
                   down_window=TimeWindow(None, min(p.contact_window.latest_ms for p in points)),
                   up_window=TimeWindow(max(p.contact_window.earliest_ms for p in points) + 1, None))


def _beside_other_colour(arcs, chain, now) -> bool:
    """Whether an arc of ``chain`` live at ``now`` has a playable arc of another colour within the grace distance
    then, which keeps the chain's colour in grace."""
    mine = [arcs[e] for e in chain.note_indices if arcs[e].start <= now <= arcs[e].end]
    return bool(mine) and any(other.colour != a.colour and other.start <= now <= other.end
                              and math.dist(a.position(now), other.position(now)) < rules.GRACE_DISTANCE
                              for a in mine for other in arcs)


def enveloped_arcs(occupations: OccupationSet, projection: GameplayProjection,
                   multi: MultiFingerOptions) -> tuple[Occupation, ...]:
    """Short arc pieces the finger on a longer arc of another colour keeps: the outer half of a jitter drawn along
    an arc of the other colour, the arc its envelope (a player holds the envelope with one hand and the jitter's
    inner half with the other, a finger each).

    A piece qualifies when it is an arc chain at most ``steady_max_piece_ms`` long, an arc of another colour is
    within the grace distance on every frame of it (so its colour stays in grace: any touch in the hitbox counts,
    a lift binds nothing), and each of its ticks' contact windows has a frame of the piece where the planned point
    (``target_xy``) of an arc chain of another colour longer than ``steady_max_piece_ms`` is inside its hitbox.
    """
    offset = occupations.audio_offset_ms
    arcs = occupations.arcs
    chains = [o for o in occupations.items if o.kind == "chain" and o.colour in (0, 1) and not isinstance(o, _Steady)]
    longer = [c for c in chains if c.end_ms - c.start_ms > multi.steady_max_piece_ms]
    out = []
    for piece in chains:
        if piece.end_ms - piece.start_ms > multi.steady_max_piece_ms or not piece.score_points:
            continue
        near = [c for c in longer if c.colour != piece.colour and c.start_ms <= piece.end_ms and c.end_ms >= piece.start_ms]
        frames = range(frame_at_or_after(piece.start_ms, offset), frame_at_or_after(piece.end_ms + 1, offset))
        if not near or not all(_beside_other_colour(arcs, piece, frame_ms(f, offset)) for f in frames):
            continue

        def kept(point):
            window = point.contact_window
            frame = frame_at_or_after(max(window.earliest_ms, piece.start_ms), offset)
            while frame_ms(frame, offset) <= min(window.latest_ms, piece.end_ms):
                now = frame_ms(frame, offset)
                if any(c.start_ms <= now <= c.end_ms and piece.region_at(now).contains(
                        *projection.device_to_judgement(*target_xy(c, now, projection))) for c in near):
                    return True
                frame += 1
            return False

        if all(kept(p) for p in piece.score_points):
            out.append(piece)
    return tuple(out)


def coverable_arcs(occupations: OccupationSet, projection: GameplayProjection,
                   options: PlannerOptions = PlannerOptions()) -> tuple[Occupation, ...]:
    """The arc chains, or leading or trailing parts of them, that touches of tap and sky notes can keep
    (``arc_cover_splits``)."""
    return tuple(part for _, covered, _ in arc_cover_splits(occupations, projection, options) for part in covered)


def arc_cover_splits(occupations: OccupationSet, projection: GameplayProjection,
                     options: PlannerOptions = PlannerOptions()):
    """(chain, covered parts, held parts) for each arc chain the touches of tap and sky notes can keep, wholly or
    over a run of its arcs at its start or end. The covered parts need no touch of their own; the held part
    between them (``_chain_part``) is planned as a chain of its own.

    An arc qualifies when on every frame of it another playable arc of a different colour is within the grace
    distance, so its colour stays in grace (any touch in the hitbox counts, a lift binds nothing), and each of its
    ticks' contact windows holds a point note's touch inside the hitbox whichever press frame the planner gives that
    note (its ``offsets_ms``; ``point_contact_frames`` frames down), at the notes' planned points (``target_xy``). A
    covered part must have a tick.
    """
    offset = occupations.audio_offset_ms
    arcs = occupations.arcs
    notes = []  # (first possible chart ms, judgement x, y, contact ms of each possible press) of point notes
    for o in occupations.items:
        if o.kind in ("tap", "sky"):
            downs = sorted({round((o.start_ms + delta + offset) * 60 / 1000) for delta in options.offsets_ms})
            x, y = projection.device_to_judgement(*target_xy(o, o.start_ms, projection))
            presses = [[frame_ms(f, offset) for f in range(d, d + options.point_contact_frames)] for d in downs]
            notes.append((presses[0][0], x, y, presses))
    notes.sort(key=lambda n: n[0])
    firsts = [n[0] for n in notes]

    def graced(chain, now):
        return _beside_other_colour(arcs, chain, now)

    def kept(chain, point):
        window = point.contact_window
        near = notes[bisect_left(firsts, window.earliest_ms - 100):bisect_right(firsts, window.latest_ms)]
        return any(all(any(window.earliest_ms <= t <= window.latest_ms and chain.region_at(t).contains(x, y)
                           for t in press) for press in presses)
                   for _, x, y, presses in near)

    def coverable(chain, e):
        frames = range(frame_at_or_after(arcs[e].start, offset), frame_at_or_after(arcs[e].end + 1, offset))
        return (all(kept(chain, p) for p in chain.score_points if p.note_index == e)
                and all(graced(chain, frame_ms(f, offset)) for f in frames))

    def ticked(indices, chain):
        return any(p.note_index in indices for p in chain.score_points)

    out = []
    for chain in (o for o in occupations.items if o.kind == "chain" and o.colour != 3 and not isinstance(o, _Steady)):
        ok = [coverable(chain, e) for e in chain.note_indices]
        if all(ok):
            out.append((chain, (chain,), ()))
            continue
        head = next(n for n, v in enumerate(ok) if not v)
        tail = len(ok) - next(n for n, v in enumerate(reversed(ok)) if not v)
        runs = [chain.note_indices[:head], chain.note_indices[head:tail], chain.note_indices[tail:]]
        covered = tuple(_chain_part(chain, run) for run in (runs[0], runs[2]) if run and ticked(run, chain))
        if covered:
            held = (_chain_part(chain, runs[1]),) if ticked(runs[1], chain) else ()
            out.append((chain, covered, held))
    return out


@dataclass(frozen=True)
class _Steady(Occupation):
    """A run of short arc pieces of one colour held as one, its finger on a smoothed path (``steady_runs``)."""
    pieces: tuple[str, ...] = ()  # occupation ids of the pieces
    path: tuple[tuple[int, float, float], ...] = ()  # (chart ms, judgement x, y) per frame

    def region_at(self, time_ms: float):
        times = [p[0] for p in self.path]
        k = min(max(bisect_left(times, time_ms), 1), len(times) - 1)
        (t0, x0, y0), (t1, x1, y1) = self.path[k - 1], self.path[k]
        u = min(max((time_ms - t0) / (t1 - t0), 0.0), 1.0) if t1 > t0 else 0.0
        return ContactRegion("chain", x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)


STEADY_MARGIN = 0.8  # the smoothed point must sit inside this fraction of each piece's hitbox
STEADY_WINDOWS = (8, 4, 2)  # smoothing widths tried, in pieces, widest first


def steady_runs(occupations: OccupationSet, multi: MultiFingerOptions) -> list[_Steady]:
    """Runs of short arc pieces a finger can hold still through, each as one occupation (``_Steady``).

    A run is at least ``steady_min_pieces`` arc chains of one colour, each at most ``steady_max_piece_ms`` long,
    each starting within a frame of the last one's end: an arc chart-drawn as a jitter of separate pieces, whose
    jumps a finger following the arc centre would have to make. A player holds the finger still or moving
    smoothly instead: every tick needs a touch inside the piece's hitbox on one frame of its contact window. The
    path is the arc position averaged over the widest of ``STEADY_WINDOWS`` pieces that keeps every tick: some
    frame of its window, inside its piece, with the path within ``STEADY_MARGIN`` of the hitbox. A run too wide for
    that (a zigzag between two lines drifting apart) is held as two rails, a finger each, if each rail can be and
    the colour is in grace all along (another colour's arc within the grace distance): otherwise the colour stays
    bound to the first finger and takes no second one. A run neither keeps is left as it is.
    """
    offset = occupations.audio_offset_ms
    arcs = occupations.arcs
    out = []

    def graced(run):
        """Whether the run's colour is in grace on every frame of it: some arc of that colour within the grace
        distance of an arc of another colour on a frame at most GRACE_MS before."""
        colour, last = run[0].colour, None
        start = frame_at_or_after(run[0].start_ms - rules.GRACE_MS, offset)
        for f in range(start, frame_at_or_after(run[-1].end_ms + 1, offset)):
            now = frame_ms(f, offset)
            live = [a for a in arcs if a.start <= now <= a.end]
            if any(a.colour == colour and b.colour != colour
                   and math.dist(a.position(now), b.position(now)) < rules.GRACE_DISTANCE for a in live for b in live):
                last = now
            if now >= run[0].start_ms and (last is None or now - last > rules.GRACE_MS):
                return False
        return True

    def merge(run):
        frames = range(frame_at_or_after(run[0].start_ms, offset), frame_at_or_after(run[-1].end_ms + 1, offset))
        times = [frame_ms(f, offset) for f in frames]
        owner = [min(run, key=lambda c: max(c.start_ms - t, t - c.end_ms, 0)) for t in times]
        raw = np.array([(r.x, r.y) for r in (o.region_at(t) for o, t in zip(owner, times))])
        piece_ms = float(np.median([c.end_ms - c.start_ms for c in run]))
        for width in STEADY_WINDOWS:
            half = max(1, round(width * piece_ms / 2 * 60 / 1000))
            smooth = np.array([raw[max(0, k - half):k + half + 1].mean(axis=0) for k in range(len(raw))])

            def inside(c, k):
                region = c.region_at(times[k])
                return (abs(smooth[k, 0] - region.x) < rules.HITBOX_X * STEADY_MARGIN
                        and abs(smooth[k, 1] - region.y) < rules.HITBOX_Y * STEADY_MARGIN)

            if all(any(c.start_ms <= t <= c.end_ms and p.contact_window.earliest_ms <= t <= p.contact_window.latest_ms
                       and inside(c, k) for k, t in enumerate(times))
                   for c in run for p in c.score_points):
                values = {f.name: getattr(run[0], f.name) for f in fields(Occupation)}
                points = tuple(sorted((p for c in run for p in c.score_points), key=lambda p: p.time_ms))
                values.update(occupation_id=f"{run[0].occupation_id}~{len(run)}",
                              source_event_ids=sum((c.source_event_ids for c in run), ()),
                              note_indices=sum((c.note_indices for c in run), ()),
                              notes=sum((c.notes for c in run), ()), score_points=points,
                              start_ms=run[0].start_ms, end_ms=run[-1].end_ms,
                              down_window=TimeWindow(None, min(p.contact_window.latest_ms for p in points)),
                              up_window=TimeWindow(max(p.contact_window.earliest_ms for p in points) + 1, None))
                return _Steady(**values, pieces=tuple(c.occupation_id for c in run),
                               path=tuple((t, float(x), float(y)) for t, (x, y) in zip(times, smooth)))
        return None

    def rails(run):
        """The run's pieces on two rails, each piece to the rail whose last piece is nearer (a zigzag between two
        lines that drift apart); a rail starts where a piece is beyond the hitbox from the first."""
        out = []
        for c in run:
            mid = c.region_at((c.start_ms + c.end_ms) / 2)
            near = min(out, key=lambda r: abs(r[1] - mid.x), default=None)
            if near is None or (len(out) < 2 and abs(near[1] - mid.x) > rules.HITBOX_X):
                out.append([[c], mid.x])
            else:
                near[0].append(c)
                near[1] = mid.x
        return [r[0] for r in out]

    chains = sorted((o for o in occupations.items if o.kind == "chain" and o.colour != 3), key=lambda o: o.start_ms)
    for colour in sorted({c.colour for c in chains}):
        run = []
        for c in [c for c in chains if c.colour == colour] + [None]:
            short = c is not None and c.end_ms - c.start_ms <= multi.steady_max_piece_ms
            if short and (not run or c.start_ms - run[-1].end_ms <= 1000 / 60):
                run.append(c)
                continue
            if len(run) >= multi.steady_min_pieces:
                merged = merge(run)
                if merged is not None:
                    out.append(merged)
                else:
                    # Too wide for one still finger: one per rail, if each rail can be held, under grace.
                    parts = rails(run)
                    parts = ([merge(r) for r in parts] if len(parts) == 2 and all(len(r) >= 2 for r in parts)
                             and graced(run) else [None])
                    if all(parts):
                        out.extend(parts)
            run = [c] if short else []
    return out


def _taken(plan: MultiPlan, occupations: OccupationSet, projection: GameplayProjection) -> set[tuple[int, int]]:
    """(arc index, frame) of each frame on which a touch counts for the arc by the contact rules (``rules``): it is
    inside the arc's hitbox and the arc's colour takes it.

    A colour in grace (two arcs of different colours within GRACE_DISTANCE, then GRACE_MS more) takes any touch and
    holds none, and grace frees the touch it held; colour 3 always takes any touch. Otherwise a colour takes only the
    touch it holds. A colour that holds none binds the first touch, in press order, found inside the hitbox of one of
    its arcs that holds no other colour, and keeps it until it lifts. So a finger that rests in another colour's
    hitbox when that colour's grace ends takes the colour from the finger meant for it, and loses its own."""
    offset = plan.options.audio_offset_ms
    arcs = occupations.arcs
    live: dict = {}  # frame -> indices of the arcs on it
    for i, a in enumerate(arcs):
        for f in range(frame_at_or_after(a.start, offset), frame_at_or_after(a.end + 1, offset)):
            live.setdefault(f, []).append(i)
    down: dict = {}  # frame -> (press-order index, judgement x, y) of each touch down on it
    for k, ft in enumerate(plan.touches):
        t = ft.touch
        for f in range(t.down, t.up):
            if f in live:
                down.setdefault(f, []).append((k, *projection.device_to_judgement(*t.xy(f))))
    held, graced, out = {}, {}, set()  # colour -> touch it holds; colour -> chart ms of its latest grace
    for f in sorted(live):
        now, here, touching = frame_ms(f, offset), live[f], down.get(f, [])
        points = {i: arcs[i].position(now) for i in here}
        for n, i in enumerate(here):
            for j in here[n + 1:]:
                if arcs[i].colour != arcs[j].colour and math.dist(points[i], points[j]) < rules.GRACE_DISTANCE:
                    graced[arcs[i].colour] = graced[arcs[j].colour] = now
        anyone = {c for c, ms in graced.items() if now - ms <= rules.GRACE_MS} | {3}
        present = {k for k, _, _ in touching}
        held = {c: k for c, k in held.items() if k in present and c not in anyone}
        holding = set(held.values())
        for k, x, y in touching:
            for i in here:
                c = arcs[i].colour
                if not rules.arc_contact(x, y, *points[i]):
                    continue
                if c in anyone or held.get(c) == k:
                    out.add((i, f))
                elif c not in held and k not in holding:
                    held[c] = k
                    holding.add(k)
                    out.add((i, f))
    return out


def _unkept(plan: MultiPlan, occupations: OccupationSet, projection: GameplayProjection) -> set[str]:
    """Occupation ids of the plan's covered notes and steady runs its touches do not keep by the contact rules
    (``rules``): a tick with no frame of its contact window on which a touch counts for it (an arc's: ``_taken``,
    colour binding included; a hold's: a touch down in its lane), or a covered hold no touch presses (begins in its
    lane from HOLD_EARLY_MS before its start until its end)."""
    offset = plan.options.audio_offset_ms
    touches = [ft.touch for ft in plan.touches]
    taken = _taken(plan, occupations, projection)
    seen: dict = {}

    def lanes(frame):
        """The ground lane of each touch down on ``frame``."""
        if frame not in seen:
            seen[frame] = [_lane_at(projection, t.xy(frame)) for t in touches if t.down <= frame < t.up]
        return seen[frame]

    def ticked(point, counts, first_frame=None):
        window = point.contact_window
        frame = frame_at_or_after(window.earliest_ms, offset)
        if first_frame is not None:
            frame = max(frame, first_frame)
        while frame_ms(frame, offset) <= window.latest_ms:
            if counts(frame):
                return True
            frame += 1
        return False

    out = set()
    for o in plan.covered + plan.steady:
        if o.kind == "hold":
            hold = occupations.holds[o.note_indices[0]]
            presses = [t.down for t in touches if hold.start - rules.HOLD_EARLY_MS < frame_ms(t.down, offset) < hold.end
                       and _lane_at(projection, t.xy(t.down)) == hold.lane]
            if not presses or not all(ticked(p, lambda f: hold.lane in lanes(f), min(presses)) for p in o.score_points):
                out.add(o.occupation_id)
        elif not all(ticked(p, lambda f, i=p.note_index: (i, f) in taken) for p in o.score_points):
            out.add(o.occupation_id)
    return out


def plan_multi_finger(occupations: OccupationSet, projection: GameplayProjection,
                      options: PlannerOptions = PlannerOptions(),
                      multi: MultiFingerOptions = MultiFingerOptions(), progress=None) -> MultiPlan:
    reporter = ProgressReporter(progress)
    if not (multi.covered_holds or multi.covered_arcs or multi.steady_arcs or multi.enveloped_arcs):
        return _plan_items(occupations, projection, options, multi, reporter)
    enveloped = enveloped_arcs(occupations, projection, multi) if multi.enveloped_arcs else ()
    plan = _plan_covered(occupations, projection, options, multi, enveloped, reporter)
    if plan is None:  # a piece left to another colour's finger was not kept: plan the chart without any
        plan = _plan_covered(occupations, projection, options, multi, (), reporter)
    return plan


def _plan_covered(occupations, projection, options, multi, enveloped, progress=None) -> MultiPlan | None:
    """plan_multi_finger with the ``enveloped`` pieces left out; None if the plan's touches do not keep one of them
    (``_unkept``) or the chart then has no path."""
    def order(items):
        return tuple(sorted(items, key=lambda o: (o.first_tick.time_ms if o.kind == "chain" else o.start_ms, o.end_ms,
                                                  o.occupation_id)))

    left = {o.occupation_id for o in enveloped}
    own = replace(occupations, items=tuple(o for o in occupations.items if o.occupation_id not in left))
    steady = steady_runs(own, multi) if multi.steady_arcs else []
    merged = {p for s in steady for p in s.pieces}
    base = replace(occupations, items=order([o for o in own.items if o.occupation_id not in merged] + steady))
    holds = list(coverable_holds(base, projection)) if multi.covered_holds else []
    splits = list(arc_cover_splits(base, projection, options)) if multi.covered_arcs else []
    while True:
        gone = ({o.occupation_id for o in holds} | {chain.occupation_id for chain, _, _ in splits}
                | {p for s in steady for p in s.pieces} | left)
        kept = [o for o in occupations.items if o.occupation_id not in gone] + [p for _, _, held in splits for p in held]
        kept += [s for s in steady if s.occupation_id not in gone]
        covered = tuple(holds) + tuple(p for _, parts, _ in splits for p in parts) + tuple(enveloped)
        try:
            planned = _plan_items(replace(occupations, items=order(kept)), projection, options, multi, progress)
        except ValueError as error:
            # No path through a steady run: its pieces go back (one named in the error, else every run).
            blocked = [s for s in steady if s.occupation_id in str(error)] or steady
            if not blocked:
                if left:
                    return None
                raise
            ids = {s.occupation_id for s in blocked}
            steady = [s for s in steady if s.occupation_id not in ids]
            continue
        plan = replace(planned, covered=covered, steady=tuple(steady))
        unkept = _unkept(plan, occupations, projection)
        if unkept & left:
            return None
        if not unkept:
            return plan
        # A part that is not kept gives its whole chain back; a steady run, its pieces.
        holds = [o for o in holds if o.occupation_id not in unkept]
        splits = [s for s in splits if not any(p.occupation_id in unkept for p in s[1])]
        steady = [s for s in steady if s.occupation_id not in unkept]


# Analysis hook: when a list, each DP run appends its graph, candidate groups and chosen path, as (state before,
# candidate, hand, cost increment, changes) per step.
PATH_TRACE: list | None = None


def _plan_items(occupations: OccupationSet, projection: GameplayProjection, options: PlannerOptions,
                multi: MultiFingerOptions, progress=None) -> MultiPlan:
    progress = progress or ProgressReporter()
    progress('preparing')
    options = replace(options, audio_offset_ms=occupations.audio_offset_ms)
    groups = build_candidates(occupations, projection, options, progress=progress)
    meetings = None
    if multi.meeting_swaps:
        groups, meetings = meeting_candidates(groups, projection, options)
    check_weights(options, multi)
    progress('graph')
    graph = _Graph(groups, projection, options, multi, meetings, occupations)
    # Four slots (left 1, 2, right 1, 2: touch ids), then with jack_weight each hand's fast-run length.
    states = {(-1, -1, -1, -1) + ((0, 0) if multi.jack_weight else ()) + ((-1, -1) if multi.arc_takeover_weight else ()) + ((-1,)*4 if multi.tick_contacts else ()):
              (MultiCost(), None)}
    visited, pruned = 1, 0
    def rank(cost):
        return (cost.missed_ticks, cost.weighted(options, multi))
    present = {c.occupation.occupation_id for group in groups for c in group}
    empty_items = iter(o for o in occupations.items if o.occupation_id not in present)
    earliest = [min((c.down_frame for c in group),default=math.inf) for group in groups]
    if multi.tick_contacts:
        for index in range(len(earliest)-2,-1,-1): earliest[index] = min(earliest[index],earliest[index+1])
    for k, group in enumerate(groups):
        following = {}
        f0 = int(earliest[k]) if math.isfinite(earliest[k]) else 0
        item = group[0].occupation if group else next(empty_items)
        for state_index, (state, (cost, node)) in enumerate(states.items()):
            progress('search', k, len(groups), chart_ms=item.first_tick.time_ms,
                     states=len(states), states_completed=state_index,
                     candidates=len(group), visited=visited)
            if multi.allow_misses:
                increment = MultiCost(missed_ticks=item.score_weight)
                total = cost + increment
                key = state if options.crossing_pass_s else (tuple(graph.future(i, f0) for i in state[:4]) + graph.tail_key(state, f0))
                weight = rank(total)
                if key not in following or weight < following[key][1]:
                    following[key] = (total, weight, (node, (), increment, item.start_ms,
                                                     state, None, item), state)
            for candidate in (() if item.occupation_id in multi.forced_misses or set(item.source_event_ids) & set(multi.forced_misses) else group):
                for hand in HANDS:
                    for new, increment, changes in graph.steps(state, candidate, hand):
                        total = cost + increment
                        weight = rank(total)
                        key = new if options.crossing_pass_s else (tuple(graph.future(i, f0) for i in new[:4])
                                                                   + graph.tail_key(new, f0))
                        if key not in following or weight < following[key][1]:
                            following[key] = (total, weight, (node, changes, increment, candidate.occupation.start_ms,
                                                              state, candidate, hand), new)
        if not following:
            item = group[0].occupation if group else occupations.items[k]
            raise ValueError(f"no candidate path at item {k}: {item.occupation_id} ({item.kind} at {item.start_ms} ms)")
        kept = list(following.values())
        if multi.max_states and len(kept) > multi.max_states:
            kept = sorted(kept, key=lambda v: v[1])[:multi.max_states]
            pruned += 1
        states = {new: (total, node) for total, _, node, new in kept}
        visited += len(states)
        progress('search', k + 1, len(groups), chart_ms=item.last_tick.time_ms,
                 states=len(states), states_completed=len(states), candidates=len(group), visited=visited)
    progress('finalizing')
    final_state, (cost, node) = min(states.items(), key=lambda kv: rank(kv[1][0]))
    steps, step_costs, path, skipped = [], [], [], []
    while node is not None:
        node, changes, increment, start_ms, before, candidate, hand = node
        steps.append(changes)
        step_costs.append((start_ms, increment))
        if candidate is None:
            skipped.append(hand)
        else:
            path.append((before, candidate, hand, increment, changes))
    if PATH_TRACE is not None:
        PATH_TRACE.append({"graph": graph, "groups": groups, "path": path[::-1], "options": options})
    current, done = {}, []
    for changes in reversed(steps):
        for slot, touch, kind in changes:
            if kind == "new" and slot in current:
                done.append((slot, current[slot]))
            current[slot] = touch
    done.extend(current.items())
    touches = tuple(sorted((FingerTouch(t, HANDS[slot // 2], slot % 2) for slot, t in done),
                           key=press_order))
    result = MultiPlan(touches, cost, options, multi, visited, pruned, step_costs=tuple(reversed(step_costs)),
                       skipped=tuple(reversed(skipped)))
    progress('done', len(groups), len(groups), visited=visited)
    return result


# ---------------------------------------------------------------- report


def multi_report(plan: MultiPlan) -> dict:
    """What the extended moves were used for."""
    touches = []
    for n, ft in enumerate(plan.touches, 1):
        t = ft.touch
        touches.append({"touch": n, "hand": ft.hand, "slot": ft.slot, "down_ms": t.down_ms, "up_ms": t.up_ms,
                        "parts": [{"occupation_id": p.candidate.occupation.occupation_id, "kind": p.candidate.occupation.kind,
                                   "from_ms": frame_ms(p.start, t.offset), "to_ms": frame_ms(p.stop, t.offset)}
                                  for p in t.parts],
                        "bridges_ms": [[frame_ms(a, t.offset), frame_ms(b, t.offset)] for a, b in t.bridges]})
    shared = [r for r in touches if len(r["parts"]) > 1 and not r["bridges_ms"]]
    second = [r for r in touches if r["slot"] == 1]
    return {"touches": len(touches), "contacts": len(plan.choices),
            "shared_touches": len([r for r in touches if len({p["occupation_id"] for p in r["parts"]}) > 1
                                   and any(a["from_ms"] < b["to_ms"] and b["from_ms"] < a["to_ms"]
                                           for a in r["parts"] for b in r["parts"] if a is not b)]),
            "bridges": sum(len(r["bridges_ms"]) for r in touches),
            "meeting_switches": sorted({p.candidate.occupation.occupation_id for ft in plan.touches for p in ft.touch.parts
                                        if isinstance(p.candidate.occupation, _Switched)}),
            "covered_holds": [{"occupation_id": o.occupation_id, "lane": o.lane, "start_ms": o.start_ms, "end_ms": o.end_ms}
                              for o in plan.covered if o.kind == "hold"],
            "covered_arcs": [{"occupation_id": o.occupation_id, "colour": o.colour, "start_ms": o.start_ms, "end_ms": o.end_ms}
                             for o in plan.covered if o.kind == "chain"],
            "steady_arcs": [{"occupation_id": o.occupation_id, "colour": o.colour, "start_ms": o.start_ms, "end_ms": o.end_ms,
                             "pieces": len(o.pieces)} for o in plan.steady],
            "second_finger_touches": len(second),
            "second_finger_by_hand": {h: sum(r["hand"] == h for r in second) for h in HANDS},
            "cost_components": {**asdict(plan.cost.base), **{k: v for k, v in asdict(plan.cost).items() if k != "base"}},
            "weighted_cost": plan.cost.weighted(plan.options, plan.multi),
            "options": asdict(plan.multi), "states_visited": plan.states_visited, "pruned_items": plan.pruned_items,
            "detail": [r for r in touches if r in shared or r["slot"] == 1 or r["bridges_ms"]]}

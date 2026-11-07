"""Plan a chart's hand order with a preset and turn the result into the plan JSON (docs/FORMAT.md).

    from arc_hand_order import api
    chart = api.load_chart("2.aff")
    result = api.plan_chart(chart, preset="on5")
    api.write_plan(result, "plan.json")
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import hashlib
import json
import math
from pathlib import Path
import time

from .chart.parser import parse_aff
from .planning import multi_finger as mf
from .planning.occupations import OccupationSet, occupations_from_chart
from .planning.projection import GameplayProjection
from .planning.two_finger import PlannerOptions, frame_ms
from .presets import BASE, PRESETS
from .schemas.device import DeviceProfile

FORMAT = "arc-hand-order.plan/1"
DEVICES = Path(__file__).resolve().parent / "devices"
DEFAULT_DEVICE = DEVICES / "ipad_pro_11_m2.json"


@dataclass(frozen=True)
class LoadedChart:
    path: str
    sha256: str
    parsed: object  # chart.parser.ParsedChart

    @property
    def chart(self):
        return self.parsed.chart


@dataclass(frozen=True)
class PlanResult:
    chart: LoadedChart
    device_path: str
    device: DeviceProfile
    preset: str
    occupations: OccupationSet
    plan: mf.MultiPlan
    elapsed_s: float


def load_chart(path) -> LoadedChart:
    """Read an .aff file (UTF-8, a BOM allowed) and parse it; raises ValueError if the parser blocks it."""
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    parsed = parse_aff(raw.decode("utf-8-sig"), str(path), digest)
    if parsed.chart is None:
        problems = "; ".join(f"{d.code}: {d.message}" for d in parsed.diagnostics[:5])
        raise ValueError(f"{path}: the chart could not be parsed ({problems})")
    return LoadedChart(str(path), digest, parsed)


def load_device(path=DEFAULT_DEVICE) -> DeviceProfile:
    return DeviceProfile.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _tuples(value):
    return tuple(_tuples(v) for v in value) if isinstance(value, (list, tuple)) else value


def _typed(cls, overrides: dict) -> dict:
    """``overrides`` (name -> value, or its text as given on the command line) in the field types of ``cls``."""
    types = {f.name: str(f.type) for f in fields(cls)}
    out = {}
    for name, value in overrides.items():
        if name not in types:
            raise ValueError(f"{cls.__name__} has no option {name!r}")
        kind = types[name].replace(" | None", "")
        if isinstance(value, str) and kind != "str":
            if value.lower() in ("none", "null") and "None" in types[name]:
                value = None
            elif kind == "bool":
                if value.lower() not in ("0", "1", "false", "true", "no", "yes", "off", "on"):
                    raise ValueError(f"{name}: {value!r} is not a boolean")
                value = value.lower() in ("1", "true", "yes", "on")
            elif kind == "int":
                value = int(value)
            elif kind == "float":
                value = float(value)
            elif kind.startswith("tuple"):
                value = json.loads(value)
        out[name] = _tuples(value)
    return out


def options_for(preset: str = "on5", multi: dict | None = None, planner: dict | None = None):
    """(PlannerOptions, MultiFingerOptions) of a preset with field overrides."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; known: {', '.join(PRESETS)}")
    return (replace(BASE, **_typed(PlannerOptions, planner or {})),
            replace(PRESETS[preset], **_typed(mf.MultiFingerOptions, multi or {})))


def plan_chart(chart: LoadedChart, device_path=DEFAULT_DEVICE, preset: str = "on5", multi: dict | None = None,
               planner: dict | None = None, occupation_set: OccupationSet | None = None,
               progress=None) -> PlanResult:
    """Plan ``chart`` with the preset and overrides."""
    device = load_device(device_path)
    projection = GameplayProjection(device)
    options, multi_options = options_for(preset, multi, planner)
    began = time.perf_counter()
    occupations = occupation_set if occupation_set is not None else occupations_from_chart(chart.chart)
    plan = mf.plan_multi_finger(occupations, projection, options, multi_options, progress=progress)
    return PlanResult(chart, str(device_path), device, preset, occupations, plan,
                      round(time.perf_counter() - began, 1))


def _json(value):
    """``value`` with tuples as lists and non-finite floats as strings."""
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def _occupation(o) -> dict:
    out = {"kind": o.kind, "occupation_id": o.occupation_id, "events": list(o.source_event_ids),
           "start_ms": o.start_ms, "end_ms": o.end_ms, "colour": o.colour, "lane": o.lane}
    if o.is_red:
        out['non_scoring_red'] = True
    if o.parent_occupation_id or o.native_scoring:
        out.update(parent_occupation_id=o.parent_occupation_id,
                   scoring_ticks=[{"id": f"arcTick:{p.source_event_id}:{p.tick_index}", "time_ms": p.time_ms, "weight": p.weight,
                                   "earliest_ms": p.contact_window.earliest_ms, "latest_ms": p.contact_window.latest_ms,
                                   "native_auto_ms":p.native_auto_ms,"native_expiry_ms":p.native_expiry_ms}
                                  for p in o.score_points])
    if isinstance(o, mf._Switched):
        out["switch"] = {"at_ms": o.switch_ms, "before": _occupation(o.first), "after": _occupation(o.second)}
    if isinstance(o, mf._Steady):
        out["steady_pieces"] = list(o.pieces)
    return out


def _served(part: dict, events: dict) -> list:
    """The events a part serves (FORMAT.md, "notes"): a chain held across a meeting serves the first chain's events
    that begin before the switch and the second chain's that end after it (an arc ending at the switch is the first
    hand's alone); a chain let go early (the finger that hands it over at a relay) only those that begin before it
    lets go."""
    if "switch" in part:
        at = part["switch"]["at_ms"]
        return ([e for e in part["switch"]["before"]["events"] if events[e].start_time_ms < at]
                + [e for e in part["switch"]["after"]["events"] if events[e].end_time_ms > at])
    if part["kind"] == "chain":
        return [e for e in part["events"] if events[e].start_time_ms < part["to_ms"]]
    return part["events"]


def plan_to_dict(result: PlanResult) -> dict:
    """The plan JSON (FORMAT): see docs/FORMAT.md."""
    plan = result.plan
    events = {e.event_id: e for e in result.chart.chart.events}
    notes = {}
    for o in result.occupations.items:
        for e in o.source_event_ids:
            event = events[e]
            notes.setdefault(e, {"event_id": e, "type": event.type, "start_ms": event.start_time_ms,
                                 "end_ms": event.end_time_ms, "lane": getattr(event, "lane", None),
                                 "colour": getattr(event, "color", None), "hands": set(), "touches": []})
    touches = []
    for n, ft in enumerate(sorted(plan.touches, key=lambda ft: (ft.touch.down, ft.hand, ft.slot)), 1):
        t = ft.touch
        parts = []
        for p in t.parts:
            part = _occupation(p.candidate.occupation)
            part.update(from_frame=p.start, to_frame=p.stop, from_ms=frame_ms(p.start, t.offset),
                        to_ms=frame_ms(p.stop, t.offset))
            part["served_events"] = _served(part, events)
            parts.append(part)
            for e in part["served_events"]:
                notes[e]["hands"].add(ft.hand)
                if n not in notes[e]["touches"]:
                    notes[e]["touches"].append(n)
        touches.append({"id": n, "hand": ft.hand, "finger": ft.slot + 1, "down_frame": t.down, "up_frame": t.up,
                        "down_ms": t.down_ms, "up_ms": t.up_ms,
                        "path_mm": [[round(1000 * x, 2), round(1000 * y, 2)] for x, y in t.path],
                        "parts": parts, "bridges": [[a, b] for a, b in t.bridges]})
    covered = [_occupation(o) for o in plan.covered]
    for o in covered:
        for e in o["events"]:
            notes[e]["hands"].add("covered")
    report = mf.multi_report(plan)
    return _json({
        "format": FORMAT,
        "chart": {"path": result.chart.path, "sha256": result.chart.sha256,
                  "audio_offset_ms": plan.options.audio_offset_ms},
        "device": {"path": result.device_path, "profile_id": result.device.profile_id,
                   "screen_mm": [round(1000 * result.device.screen.width_m, 2),
                                 round(1000 * result.device.screen.height_m, 2)]},
        "preset": result.preset,
        "planner_options": asdict(plan.options),
        "multi_finger_options": asdict(plan.multi),
        "frame_rate_hz": 60,
        "touches": touches,
        "covered": covered,
        "notes": [{**note, "hands": sorted(note["hands"])}
                  for note in sorted(notes.values(), key=lambda n: (n["start_ms"], n["end_ms"], n["event_id"]))],
        "cost": {"weighted": report["weighted_cost"], "components": report["cost_components"]},
        "stats": {"touches": report["touches"], "second_finger_touches": report["second_finger_touches"],
                  "shared_touches": report["shared_touches"], "bridges": report["bridges"],
                  "meeting_switches": report["meeting_switches"], "steady_arcs": len(plan.steady),
                  "states_visited": plan.states_visited, "pruned_items": plan.pruned_items,
                  "elapsed_s": result.elapsed_s},
    })


def write_plan(result: PlanResult, path) -> dict:
    data = plan_to_dict(result)
    Path(path).write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return data

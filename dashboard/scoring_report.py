"""Compare planner claims with observed, source-segment-specific native ticks."""

from collections import defaultdict


def key(tick):
    return tuple(
        tick[x] for x in ("arc_start_ms", "arc_end_ms", "color", "auto_ms")
    ) + tuple(round(tick[x], 5) for x in ("x_start", "x_end", "y_start", "y_end"))


def compare_ticks(plan, judgements):
    native = defaultdict(list)
    for tick in judgements.get("arc_ticks", []):
        native[key(tick)].append(tick)
    rows = []
    for target in plan.get("arc_tick_targets", []):
        records = native[key(target)]
        record = records.pop(0) if records else None
        hit = record is not None and record["result"] not in (
            "MissLate",
            "MissEarly",
            "NoScore",
        )
        rows.append(
            {
                "id": target["id"],
                "time_ms": target["time_ms"],
                "planned_hit": target["planned_hit"],
                "native_result": record["result"] if record else "NotJudged",
                "native_hit": hit,
                "settled_ms": record["chart_ms"] if record else None,
            }
        )
    claimed = [x for x in rows if x["planned_hit"]]
    return {
        "engine": "ArcCreate 1.2.68 native request receivers",
        "predicted_hits": len(claimed),
        "confirmed_hits": sum(x["native_hit"] for x in claimed),
        "unconfirmed_predictions": [x for x in claimed if not x["native_hit"]],
        "incidental_hits": sum(x["native_hit"] and not x["planned_hit"] for x in rows),
        "ticks": rows,
    }

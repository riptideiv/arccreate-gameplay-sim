"""Command line: plan a chart's hand order, or list a preset's options.

    python -m arc_hand_order plan CHART.aff [--out plan.json] [--preset on5] [--device DEVICE.json]
                                            [--set NAME=VALUE ...] [--planner-set NAME=VALUE ...]
    python -m arc_hand_order options [--preset on5]

``plan`` writes the plan JSON (docs/FORMAT.md; default: next to the chart, <chart>.<preset>.plan.json) and prints a
one-line summary. Exit status 0 when the plan was made; 1 when the chart or an option is invalid or the planner finds
no path.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import api
from .presets import PRESETS


def _pairs(values) -> dict:
    out = {}
    for value in values or ():
        name, sep, text = value.partition("=")
        if not sep:
            raise ValueError(f"{value!r}: expected NAME=VALUE")
        out[name.strip()] = text
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m arc_hand_order", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="plan a chart and write the plan JSON")
    plan.add_argument("chart", type=Path, help="the .aff chart")
    plan.add_argument("--out", type=Path, help="plan JSON to write (default: <chart>.<preset>.plan.json)")
    plan.add_argument("--preset", choices=list(PRESETS), default="on5")
    plan.add_argument("--device", type=Path, default=api.DEFAULT_DEVICE, help="device profile JSON")
    plan.add_argument("--set", action="append", metavar="NAME=VALUE", help="a MultiFingerOptions field")
    plan.add_argument("--planner-set", action="append", metavar="NAME=VALUE", help="a PlannerOptions field")
    options = commands.add_parser("options", help="print a preset's options as JSON")
    options.add_argument("--preset", choices=list(PRESETS), default="on5")
    args = parser.parse_args(argv)

    if args.command == "options":
        planner, multi = api.options_for(args.preset)
        print(json.dumps({"preset": args.preset, "planner_options": api._json(asdict(planner)),
                          "multi_finger_options": api._json(asdict(multi))}, indent=1))
        return 0
    try:
        chart = api.load_chart(args.chart)
        result = api.plan_chart(chart, args.device, args.preset, _pairs(args.set), _pairs(args.planner_set))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    out = args.out or args.chart.with_name(f"{args.chart.stem}.{args.preset}.plan.json")
    data = api.write_plan(result, out)
    print(f"{out}: {data['stats']['touches']} touches, {len(data['covered'])} covered notes, "
          f"cost {data['cost']['weighted']:.1f}, {data['stats']['elapsed_s']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Native integration audit; requires the locally imported Bibideba assets.

Run: .venv/bin/python tests/check_off_center_native.py --native
Outputs are ignored QA artifacts, never imported into the chart catalogue.
"""

import argparse
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from arc_hand_order import api  # noqa: E402
from contact_placement import refine  # noqa: E402
from plan_excerpt import create_plan  # noqa: E402
from scoring_report import compare_ticks  # noqa: E402


def write(folder, plan, start, end):
    (folder / "plan.json").write_text(json.dumps(plan, indent=2))
    replay = {
        "width_mm": plan["device"]["screen_mm"][0],
        "height_mm": plan["device"]["screen_mm"][1],
        "scroll_speed": 2.5,
        "chart_start_ms": start,
        "audio_offset_ms": plan["chart"]["audio_offset_ms"],
        "source_audio_offset_ms": plan["chart"].get("source_audio_offset_ms", 0),
        "touches": [
            {
                "id": t["id"],
                "down_frame": t["down_frame"],
                "up_frame": t["up_frame"],
                "points": [{"x": x, "y": y} for x, y in t["path_mm"]],
            }
            for t in plan["touches"]
        ],
    }
    (folder / "replay.json").write_text(json.dumps(replay))
    return [start, end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "output/off-center-native"
    )
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    assets = ROOT / "charts/imported/bibideba"
    fixture = runpy.run_path(str(ROOT / "tests/test_contact_placement.py"))[
        "alternating"
    ]
    summary = []
    for kind in ("tap", "sky", "bibideba"):
        pair = []
        for spatial in (False, True):
            name = f"{kind}-" + ("regions" if spatial else "centres")
            folder = args.output / name
            folder.mkdir(exist_ok=True)
            if kind == "bibideba":
                settings = {
                    "arc_path_mode": "score",
                    "off_center_contacts": spatial,
                    "max_speed_m_s": 3,
                    "search_width": 64,
                    "allow_misses": True,
                    "prefer_arc_trace": False,
                    "full_arc_hold": False,
                    "repeat_comfort_ms": 167,
                    "jack_gap_ms": 133,
                    "jack_free": 2,
                    "jack_weight": 10,
                }
                plan = create_plan(assets / "2.aff", 65000, 75000, settings)
                project = assets / "project.arcproj"
                window = write(folder, plan, 65000, 75500)
            else:
                result = fixture(folder, spatial, kind)
                placement = None
                if spatial:
                    result, placement = refine(result)
                plan = api.plan_to_dict(result)
                if placement:
                    plan["contact_placement"] = placement
                for asset in ("base.ogg", "BIBBIDIBACover.png"):
                    link = folder / asset
                    if not link.exists():
                        link.symlink_to(assets / asset)
                project = folder / "project.arcproj"
                project.write_text(
                    (assets / "project.arcproj")
                    .read_text()
                    .replace("2.aff", "placement.aff")
                )
                # Include a generous native tail; require every synthetic note
                # to have actually been judged, rather than only zero misses.
                window = write(folder, plan, 0, 8000)
            record = {
                "case": name,
                "planned_misses": plan.get("planning", {}).get("skipped_ticks", 0),
                "placement": plan.get("contact_placement"),
            }
            digest = hashlib.sha256(
                (folder / "replay.json").read_bytes()
                + str(window).encode()
                + (
                    project.parent
                    / ("placement.aff" if kind != "bibideba" else "2.aff")
                ).read_bytes()
            ).hexdigest()
            record["request_sha256"] = digest
            if args.native:
                media = folder / "gameplay.mp4"
                receipt = folder / "native-check.json"
                saved = json.loads(receipt.read_text()) if receipt.exists() else {}
                if not media.exists() or saved.get("request_sha256") != digest:
                    with (folder / "native.log").open("w") as log:
                        subprocess.run(
                            [
                                str(ROOT / "arc-create-export/export.sh"),
                                str(project),
                                str(media),
                                *map(str, window),
                                str(folder / "replay.json"),
                            ],
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            check=True,
                        )
                ledger = json.loads(Path(str(media) + ".judgements.json").read_text())
                ticks = compare_ticks(plan, ledger)
                (folder / "arc-tick-check.json").write_text(json.dumps(ticks, indent=2))
                assert not ticks["unconfirmed_predictions"], ticks[
                    "unconfirmed_predictions"
                ]
                if kind != "bibideba":
                    assert ledger["hits"] == 24 and ledger["misses"] == 0
                record.update(
                    hits=ledger["hits"],
                    misses=ledger["misses"],
                    score=ledger["score"],
                    planned_ticks=ticks["predicted_hits"],
                    confirmed_ticks=ticks["confirmed_hits"],
                    native_miss_times_ms=[
                        j["chart_ms"]
                        for j in ledger["judgements"]
                        if j["result"].startswith("Miss")
                    ],
                )
                receipt.write_text(json.dumps(record, indent=2))
            pair.append(record)
            summary.append(record)
            print(json.dumps(record), flush=True)
        if args.native:
            assert [(x["hits"], x["misses"], x["score"]) for x in pair[:1]] == [
                (x["hits"], x["misses"], x["score"]) for x in pair[1:]
            ]
    (args.output / "verification.json").write_text(
        json.dumps({"passed": True, "native": args.native, "cases": summary}, indent=2)
    )


if __name__ == "__main__":
    main()

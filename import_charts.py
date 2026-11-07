"""Import extracted Arcaea song folders without modifying source charts/assets.

Run with .venv/bin/python import_charts.py charts/imported/COLLECTION.
Each difficulty gets an ArcCreate project; assets link to the local source pack.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

from arc_hand_order.api import load_chart
from dashboard.chart_library import BUILTINS, chart_paths

ROOT = Path(__file__).resolve().parent
DIFFICULTIES = ("Past", "Present", "Future", "Beyond", "Eternal")
COLORS = ("#2B7E98FF", "#6B9955FF", "#482B54FF", "#A3324AFF", "#7863A8FF")


def relative_asset(folder, name):
    path = (folder / name).resolve()
    if not path.is_relative_to(folder.resolve()) or not path.is_file():
        raise ValueError(f"Missing or invalid asset: {folder / name}")
    return path


def choose_assets(folder, difficulty):
    level = str(difficulty["ratingClass"])
    audio = relative_asset(folder, f"{level}.ogg" if difficulty.get("audioOverride") else "base.ogg")
    jacket_stem = level if difficulty.get("jacketOverride") else "base"
    jacket = next((folder / name for name in (f"1080_{jacket_stem}.jpg", f"{jacket_stem}.jpg",
                  f"1080_{jacket_stem}_256.jpg") if (folder / name).is_file()), None)
    if jacket is None:
        raise ValueError(f"Missing jacket for {folder.name} difficulty {level}")
    return audio, jacket.resolve()


def audio_duration(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True).strip())


def project_text(aff, audio, jacket, song, difficulty, title):
    # JSON strings are also YAML quoted scalars (including Unicode and newlines).
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    level = int(difficulty["ratingClass"])
    name = DIFFICULTIES[level] if 0 <= level < len(DIFFICULTIES) else f"Difficulty {level}"
    rating = str(difficulty.get("rating", "?")) + ("+" if difficulty.get("ratingPlus") else "")
    fields = {"chartPath": aff, "audioPath": audio, "jacketPath": jacket,
              "baseBpm": song.get("bpm_base", 120), "bpmText": song.get("bpm", "120"),
              "title": title, "composer": difficulty.get("artist", song.get("artist", "")),
              "charter": difficulty.get("chartDesigner", ""), "alias": "",
              "illustrator": difficulty.get("jacketDesigner", ""),
              "difficulty": f"{name} {rating}", "difficultyColor": COLORS[level] if 0 <= level < len(COLORS) else COLORS[2]}
    lines = [f"lastOpenedChartPath: {quote(aff)}", "charts:"]
    for index, (key, value) in enumerate(fields.items()):
        lines.append(f"{'- ' if index == 0 else '  '}{key}: {value if key == 'baseBpm' else quote(value)}")
    lines.extend(["  syncBaseBpm: true", "  skin:", "    particle: ''"])
    return "\n".join(lines) + "\n"


def import_collection(collection, root=ROOT):
    root, collection = root.resolve(), collection.resolve()
    base = (root / "charts/imported").resolve()
    if not collection.is_relative_to(base):
        raise ValueError("Copy/extract the collection under charts/imported before importing")
    catalog_path = base / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {"version": 1, "charts": {}}
    entries = {}
    durations = {}
    errors = []
    songs = sorted(collection.glob("*/songlist"))
    if not songs:
        raise ValueError("No song folders found")
    for songlist in songs:
        folder = songlist.parent
        metadata = json.loads(songlist.read_text())
        if len(metadata["songs"]) != 1:
            raise ValueError(f"Expected one song in {songlist}")
        song = metadata["songs"][0]
        levels = {str(d["ratingClass"]): d for d in song["difficulties"]}
        for source in sorted(folder.glob("*.aff")):
            try:
                difficulty = levels[source.stem]
                level = int(difficulty["ratingClass"])
                title = difficulty.get("title_localized", song.get("title_localized", {})).get("en", song["id"])
                name = DIFFICULTIES[level] if 0 <= level < len(DIFFICULTIES) else f"Difficulty {level}"
                rating = str(difficulty.get("rating", "?")) + ("+" if difficulty.get("ratingPlus") else "")
                key = f"{collection.name}--{folder.name}--{source.stem}"
                if key in BUILTINS or not re.fullmatch(r"[A-Za-z0-9_.+\-]+", key):
                    raise ValueError(f"Invalid/duplicate chart ID: {key}")
                audio, jacket = choose_assets(folder, difficulty)
                if audio not in durations:
                    durations[audio] = audio_duration(audio)
                text = source.read_text(encoding="utf-8-sig")
                offset = re.search(r"(?m)^AudioOffset\s*:\s*(-?\d+)", text)
                offset_ms = int(offset[1]) if offset else 0
                warnings, reason, last_ms = {}, None, 0
                try:
                    loaded = load_chart(source)
                    warnings = dict(Counter(d.code for d in loaded.parsed.diagnostics if d.severity != "info"))
                    last_ms = max((e.end_time_ms for e in loaded.chart.events if e.type in ("tap", "hold", "arc", "arctap")), default=0)
                except ValueError as error:
                    reason = ("Uses unsupported 'designant' arc type; requires newer chart-format support."
                              if re.search(r",\s*designant\s*\)", text) else str(error).split("could not be parsed", 1)[-1].strip())
                duration = math.ceil(max(durations[audio] - offset_ms / 1000, last_ms / 1000, .5) * 1000) / 1000
                directory = base / "library" / key
                directory.mkdir(parents=True, exist_ok=True)
                # Include custom SFX and any scene assets. Relative links are self-contained
                # within the copied library and avoid duplicating the pack per difficulty.
                for asset in folder.iterdir():
                    if not asset.is_file():
                        continue
                    target = directory / asset.name
                    expected = os.path.relpath(asset, directory)
                    if target.is_symlink() and os.readlink(target) == expected:
                        continue
                    if target.exists() or target.is_symlink():
                        raise ValueError(f"Refusing to overwrite {target}")
                    target.symlink_to(expected)
                (directory / "project.arcproj").write_text(project_text(source.name, audio.name, jacket.name, song, difficulty, title))
                entry = {"title": f"{title} · {name} {rating}", "song_title": title,
                         "artist": difficulty.get("artist", song.get("artist", "")), "difficulty": f"{name} {rating}",
                         "group": song.get("title_localized", {}).get("en", song["id"]), "collection": collection.name,
                         "directory": directory.relative_to(root).as_posix(), "aff": source.name,
                         "duration": duration, "start_s": max(0, -offset_ms / 1000),
                         "end_s": min(duration, max(0, -offset_ms / 1000) + 6.65),
                         "audio_offset_ms": offset_ms, "audio_start_ms": 0,
                         "source": source.relative_to(root).as_posix(),
                         "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                         "available": reason is None, "unavailable_reason": reason, "warnings": warnings}
                chart_paths(root, key, entry)
                entries[key] = entry
            except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
                errors.append({"file": str(source.relative_to(root)), "error": str(error)})
    if errors:
        # Do not publish a partially converted catalog as a successful bulk import.
        raise ValueError(json.dumps(errors, indent=2))
    catalog["charts"] = {key: value for key, value in catalog["charts"].items() if value.get("collection") != collection.name}
    catalog["charts"].update(entries)
    temporary = catalog_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(catalog_path)
    result = {"collection": collection.name, "songs": len(songs), "charts": len(entries),
              "available": sum(e["available"] for e in entries.values()),
              "unavailable": [{"id": key, "reason": e["unavailable_reason"]} for key, e in entries.items() if not e["available"]]}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=Path)
    args = parser.parse_args()
    print(json.dumps(import_collection(args.collection), ensure_ascii=False, indent=2))

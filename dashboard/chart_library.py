"""Catalog shared by the dashboard and the local song-folder importer."""
import json
from pathlib import Path


BUILTINS = {
    "ex-otogibanashi": {"title": "Ex-Otogibanashi", "artist": "ryo (supercell)", "duration": 109.894,
                       "start_s": 0, "end_s": 6.65, "audio_start_ms": 0},
    "bibideba": {"title": "BIBBIDIBA", "artist": "Hoshimachi Suisei / Tsumiki", "duration": 168,
                "start_s": 3.5, "end_s": 6.5, "audio_start_ms": 3500},
}


def chart_paths(root, key, entry):
    base = (root / "charts/imported").resolve()
    directory = (root / entry.get("directory", f"charts/imported/{key}")).resolve()
    aff = directory / entry.get("aff", "2.aff")
    project = directory / "project.arcproj"
    if not directory.is_relative_to(base) or not aff.resolve().is_relative_to(base):
        raise ValueError("Chart assets must stay inside charts/imported")
    return directory, aff, project


def load_catalog(root):
    charts = {key: dict(value) for key, value in BUILTINS.items()}
    path = root / "charts/imported/catalog.json"
    if path.exists():
        for key, entry in json.loads(path.read_text())["charts"].items():
            if key in charts:
                raise ValueError(f"Duplicate chart ID: {key}")
            directory, aff, project = chart_paths(root, key, entry)
            if not all(p.is_file() for p in (aff, project)):
                raise ValueError(f"Imported chart assets missing: {key}; rerun import_charts.py")
            charts[key] = entry
    return charts

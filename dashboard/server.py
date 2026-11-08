"""Local render workbench. Run with the prototype's virtual environment."""

import argparse
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from typing import Literal

if __package__:
    from .chart_library import load_catalog, chart_paths
    from .red_notes import prepare_project
else:
    from chart_library import load_catalog, chart_paths
    from red_notes import prepare_project


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "output/runs"
BLENDER = Path(os.environ.get("BLENDER_BIN") or shutil.which("blender") or ROOT / ".tools/Blender.app/Contents/MacOS/Blender")
CHARTS = load_catalog(ROOT)
DEFAULTS_REVISION = '20261107-crossing-wrist-costs'
STARTUP_PREFERENCES = ('repeat_comfort_ms', 'jack_gap_ms', 'jack_free', 'jack_weight',
                       'scroll_speed', 'song_delay_ms', 'prefer_arc_trace', 'full_arc_hold',
                       'camera_elevation_deg', 'idle_speed_m_s', 'winding_limit_deg', 'acceleration_weight',
                       'note_center_weight', 'note_center_free_mm','crossing_depth_weight','wrist_twist_weight','wrist_twist_free_deg')


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    chart: str = "ex-otogibanashi"
    start_s: float = Field(default=0, ge=0)
    end_s: float = Field(default=6.65, gt=0)
    max_speed_m_s: float = Field(default=1.6, ge=0.1, le=4)
    idle_speed_m_s: float = Field(default=1, ge=0.05, le=4)
    angular_limit_deg_s: float = Field(default=360, ge=30, le=1440)
    winding_limit_deg: float = Field(default=260, ge=90, le=355)
    lift_mm: float = Field(default=6, ge=0, le=30)
    clearance_mm: float = Field(default=1, ge=0.5, le=8)
    comfort_speed_m_s: float = Field(default=0.25, ge=0.1, le=2)
    crossing_weight: float = Field(default=1, ge=0, le=30)
    crossing_depth_weight: float = Field(default=100, ge=0, le=100000)
    wrist_twist_weight: float = Field(default=10, ge=0, le=10000)
    wrist_twist_free_deg: float = Field(default=30, ge=0, le=90)
    planner_accel_m_s2: float = Field(default=75, ge=1, le=200)
    acceleration_weight: float = Field(default=100, ge=0, le=10000)
    repeat_comfort_ms: float = Field(default=167, ge=1, le=1000)
    repeat_weight: float = Field(default=5, ge=0, le=100)
    jack_gap_ms: int = Field(default=133, ge=0, le=1000)
    jack_free: int = Field(default=2, ge=1, le=32)
    jack_weight: float = Field(default=10, ge=0, le=100)
    point_contact_frames: int = Field(default=2, ge=1, le=12)
    min_air_frames: int = Field(default=1, ge=0, le=30)
    held_release_air_frames: int = Field(default=4, ge=0, le=30)
    arc_path_mode: Literal["score", "trace"] = "score"
    off_center_contacts: bool = True
    note_center_weight: float = Field(default=50, ge=0, le=10000)
    note_center_free_mm: float = Field(default=5, ge=0, le=15)
    prefer_arc_trace: bool = True
    arc_trace_weight: float = Field(default=1000, ge=0, le=10000)
    full_arc_hold: bool = True
    partial_hold_weight: float = Field(default=1000, ge=0, le=10000)
    full_ground_hold: bool = True
    partial_ground_hold_weight: float = Field(default=500, ge=0, le=10000)
    allow_misses: bool = True
    enable_red_notes: bool = True
    target_all_red_notes: bool = False
    search_width: int = Field(default=256, ge=32, le=2048)
    camera_distance_m: float = Field(default=0.29, ge=0.22, le=0.5)
    camera_elevation_deg: float = Field(default=70, ge=25, le=80)
    camera_yaw_deg: float = Field(default=0, ge=-35, le=35)
    camera_lens_mm: float = Field(default=30, ge=20, le=55)
    quality: Literal["draft", "final"] = "draft"
    scroll_speed: float = Field(default=2.5, ge=1, le=10)
    song_delay_ms: int = Field(default=0, ge=-2000, le=2000)
    hit_sounds: bool = True

    @model_validator(mode="after")
    def time_range(self):
        if self.chart not in CHARTS:
            raise ValueError("Choose a chart from the library.")
        if CHARTS[self.chart].get("available") is False:
            raise ValueError(CHARTS[self.chart]["unavailable_reason"])
        if self.end_s <= self.start_s or self.end_s > CHARTS[self.chart]["duration"]:
            raise ValueError("Choose a window inside the chart, with its end after its start.")
        if self.end_s-self.start_s < 0.5:
            raise ValueError("Preview windows must be at least 0.5 seconds long.")
        return self


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def media_url(path):
    return "/media/" + path.relative_to(ROOT / "output").as_posix()


class RenderQueue:
    def __init__(self):
        RUNS.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.cancelled = threading.Event()

    def state(self):
        with self.lock:
            history = []
            for path in sorted(RUNS.glob("*/run.json"), reverse=True)[:30]:
                try:
                    run = json.loads(path.read_text())
                    if run["status"] in ("complete", "failed", "cancelled"):
                        history.append(run)
                except (OSError, ValueError):
                    continue
            return {"active": self.active.copy() if self.active else None, "history": history}

    def update(self, **changes):
        with self.lock:
            self.active.update(changes)
            write_json(RUNS / self.active["id"] / "run.json", self.active)

    def submit(self, settings):
        with self.lock:
            if self.active and self.active["status"] == "running":
                raise RuntimeError("A render is already running.")
            self.cancelled.clear()
            run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
            run = {"id": run_id, "status": "running", "stage": "Planning contacts",
                   "progress": 0, "created": time.time(), "settings": settings.model_dump(),
                   "defaults_revision": DEFAULTS_REVISION}
            (RUNS / run_id).mkdir()
            write_json(RUNS / run_id / "run.json", run)
            self.active = run
            threading.Thread(target=self.work, args=(settings,), daemon=True).start()
            return run.copy()

    def cancel(self, run_id):
        with self.lock:
            if not self.active or self.active["id"] != run_id or self.active["status"] != "running":
                raise ValueError("This render is no longer running.")
            self.cancelled.set()
            if self.process and self.process.poll() is None:
                try:
                    os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

    def refresh_scene(self, run_id):
        with self.lock:
            if self.active and self.active["status"] == "running":
                raise RuntimeError("A build or export is already running.")
            if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{6}", run_id):
                raise ValueError("Unknown take.")
            directory = RUNS / run_id
            if not (directory / "run.json").is_file():
                raise ValueError("Unknown take.")
            if not (directory / "run.json").is_file():
                raise ValueError("Unknown take.")
            run = json.loads((directory / "run.json").read_text())
            if run["status"] != "complete" or not run.get("live"):
                raise ValueError("Choose a completed live preview.")
            self.cancelled.clear()
            self.active = run
            self.update(status="running", stage="Updating presentation only", progress=10, scene_only=True, scene_error=None)
            threading.Thread(target=self.work_scene, args=(directory,), daemon=True).start()
            return self.active.copy()

    def work_scene(self, directory):
        try:
            source = directory / self.active.get("presentation", {}).get("blend", "scene.blend")
            self.command([BLENDER, "--factory-startup", "-b", source, "--python-exit-code", "1",
                          "-P", ROOT / "refresh_scene.py", "--", directory],
                         directory, "Building walnut presentation set", 30)
            result = json.loads((directory / "presentation-result.json").read_text())
            self.update(status="complete", stage="Presentation ready", progress=100, scene_only=False,
                        presentation=result, live={**self.active["live"], "scene":media_url(directory / result["glb"])},
                        blend=media_url(directory / result["blend"]), export_video=None, export_view=None,
                        export_error=None, scene_error=None)
        except InterruptedError:
            self.update(status="complete", stage="Scene update cancelled · previous scene ready", progress=100, scene_only=False)
        except Exception as error:
            self.update(status="complete", stage="Previous scene ready", progress=100, scene_only=False, scene_error=str(error))

    def add_ending(self, run_id):
        with self.lock:
            if self.active and self.active["status"] == "running":
                raise RuntimeError("A build or export is already running.")
            if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{6}", run_id):
                raise ValueError("Unknown take.")
            directory = RUNS / run_id
            run = json.loads((directory / "run.json").read_text())
            if run["status"] != "complete" or not run.get("live"):
                raise ValueError("Choose a completed live preview.")
            self.cancelled.clear()
            self.active = run
            self.update(status="running", stage="Rendering results ending only", progress=20, ending_only=True, ending_error=None)
            threading.Thread(target=self.work_ending, args=(directory,), daemon=True).start()
            return self.active.copy()

    def work_ending(self, directory):
        try:
            self.command([ROOT / '.venv/bin/python', ROOT / 'dashboard/ending.py', directory], directory, "Rendering results ending only", 30)
            lines = (directory / 'render.log').read_text().splitlines()
            result = json.loads(next(line.removeprefix('ENDING_RESULT ') for line in reversed(lines) if line.startswith('ENDING_RESULT ')))
            self.update(status="complete", stage="Results ending ready", progress=100, ending_only=False, ending=result, video=result['video'], export_video=None, export_view=None)
        except Exception as error:
            self.update(status="complete", stage="Live preview ready", ending_only=False, ending_error=str(error))

    def export_video(self, run_id, view=None):
        with self.lock:
            if self.active and self.active["status"] == "running":
                raise RuntimeError("A build or export is already running.")
            if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{6}", run_id):
                raise ValueError("Unknown take.")
            directory = RUNS / run_id
            if not (directory / "run.json").is_file():
                raise ValueError("Unknown take.")
            run = json.loads((directory / "run.json").read_text())
            if run["status"] != "complete" or not run.get("live"):
                raise ValueError("Choose a completed live preview.")
            view = view or {}
            if set(view) - {"camera_distance_m", "camera_elevation_deg", "camera_yaw_deg", "camera_lens_mm", "quality"}:
                raise ValueError("Only camera and display detail can change during MP4 export.")
            config = Settings.model_validate({**run["settings"], **view}).model_dump()
            self.cancelled.clear()
            self.active = run
            self.update(status="running", stage="Exporting 3D MP4", progress=40, export_only=True, export_error=None)
            threading.Thread(target=self.work_export, args=(directory, config), daemon=True).start()
            return self.active.copy()

    def work_export(self, directory, config=None):
        try:
            source = directory / self.active.get("presentation", {}).get("blend", "scene.blend")
            scene, output = source, directory / "preview.mp4"
            if config:
                identity = hashlib.sha256(json.dumps([config, self.active.get("presentation", {}).get("revision")], sort_keys=True).encode()).hexdigest()[:12]
                settings_path = directory / f"export-settings-{identity}.json"
                scene, output = directory / f"scene-export-{identity}.blend", directory / f"preview-{identity}.mp4"
                write_json(settings_path, config)
                self.command([BLENDER, "--factory-startup", "-b", source, "--python-exit-code", "1",
                              "-P", ROOT / "export_camera.py", "--", settings_path, scene], directory, "Preparing export camera", 25)
            if not output.exists() or output.stat().st_size <= 1000:
                self.command([ROOT / "render-preview.sh", scene, directory / "gameplay.mp4",
                              output], directory, "Exporting 3D MP4", 40,
                             self.active["metrics"]["frame_count"])
            self.update(status="complete", stage="MP4 ready", progress=100,
                        export_video=media_url(output), export_only=False, export_error=None,
                        export_view={key: config[key] for key in ("camera_distance_m", "camera_elevation_deg", "camera_yaw_deg", "camera_lens_mm", "quality")} if config else None)
        except InterruptedError:
            self.update(status="complete", stage="Export cancelled · live preview ready", progress=100,
                        export_only=False, export_error=None)
        except Exception as error:
            # The verified live take remains usable even if its optional export stops.
            self.update(status="complete", stage="Live preview ready", progress=100,
                        export_only=False, export_error=str(error))

    def command(self, arguments, directory, stage, progress, frame_count=None):
        self.update(stage=stage, progress=progress, planning_progress=None)
        log_path = directory / "render.log"
        with log_path.open("a") as log:
            process = subprocess.Popen([str(a) for a in arguments], cwd=ROOT, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, start_new_session=True)
            with self.lock:
                self.process = process
                if self.cancelled.is_set():
                    os.killpg(process.pid, signal.SIGTERM)
            for line in process.stdout:
                log.write(line)
                log.flush()
                if line.startswith('Reused contact-preserving glove poses from shared cache.'):
                    self.update(stage='Reusing cached hand pivots', progress=progress)
                if line.startswith('PLANNER_PROGRESS '):
                    try:
                        self.planner_update(json.loads(line.removeprefix('PLANNER_PROGRESS ')))
                    except (ValueError, TypeError, KeyError):
                        pass  # A malformed observation must not abort an otherwise valid plan.
                if frame_count and (match := re.search(r"(?:Rendering frame |Saved: ['\"].*frame-)(\d+)", line)):
                    self.update(progress=min(98, 40 + 58*int(match[1])/frame_count))
            code = process.wait()
            with self.lock:
                self.process = None
            if self.cancelled.is_set():
                raise InterruptedError("Render cancelled.")
            if code:
                report = directory / "metrics.json"
                if report.exists():
                    details = json.loads(report.read_text())
                    if details.get("error"):
                        raise ValueError(details["error"])
                lines = log_path.read_text().splitlines()
                errors = [line for line in lines if line.startswith(("error:", "ValueError:", "RuntimeError:", "Exception:"))]
                if errors and "no candidate path" in errors[-1]:
                    match = re.search(r"\((\w+) at (\d+) ms\)", errors[-1])
                    note = "this window"
                    if match:
                        seconds = self.active["settings"]["start_s"] + int(match[2])/1000
                        note = f"a {match[1]} note at {seconds:.2f} s"
                    raise ValueError(f"The planner could not find a one-index path for {note}. "
                                     "Enable missed notes, adjust the timing limits, or choose a different window.")
                raise ValueError(errors[-1] if errors else "The render failed. Details are in the run log.")

    def planner_update(self, event):
        """Store observed work counts separately from the overall build-stage bar."""
        phase = event['phase']
        ranges = {'loading': (3, 3), 'preparing': (3, 3), 'geometry': (3, 4),
                  'arc_profiles': (4, 6), 'candidates': (6, 9), 'graph': (9, 10),
                  'search': (10, 19), 'finalizing': (19, 19), 'placement': (19, 19), 'done': (19, 19), 'writing': (19, 19)}
        low, high = ranges[phase]
        completed, total = event.get('completed'), event.get('total')
        fraction = max(0, min(1, completed / total)) if total else 0
        self.update(progress=low + (high-low)*fraction,
                    planning_progress={**event, 'updated_at': time.time()})

    def work(self, settings):
        directory = RUNS / self.active["id"]
        try:
            config = settings.model_dump()
            config.update(width=640 if settings.quality == "draft" else 1024,
                          height=480 if settings.quality == "draft" else 768)
            write_json(directory / "settings.json", config)
            plan_path = directory / "plan.json"
            chart, aff, project_path = chart_paths(ROOT, settings.chart, CHARTS[settings.chart])
            excluded = set()
            avoid_contacts = []
            winding_retries = 0
            while True:
                (directory / "metrics.json").unlink(missing_ok=True)
                arguments = [ROOT / ".venv/bin/python", ROOT / "plan_excerpt.py", aff,
                             round(settings.start_s*1000), round(settings.end_s*1000), plan_path,
                             "--settings", directory / "settings.json", "--progress"]
                for event in sorted(excluded):
                    arguments.extend(["--exclude-event", event])
                if avoid_contacts:
                    write_json(directory / "avoid-contacts.json", avoid_contacts)
                    arguments.extend(["--avoid-contacts", directory / "avoid-contacts.json"])
                self.command(arguments, directory, "Planning contacts and missed notes", 3)
                plan = json.loads(plan_path.read_text())
                try:
                    self.command([BLENDER, "--factory-startup", "-b", "--python-exit-code", "1", "-P", ROOT / "animate.py", "--",
                                  plan_path, directory / "scene.blend", "--motion-only",
                                  "--motion", directory / "motion.json", "--settings", directory / "settings.json",
                                  "--report", directory / "metrics.json"], directory, "Posing gloves around fixed touch points", 15)
                    break
                except ValueError as error:
                    where = re.search(r"at ([\d.]+) s", str(error))
                    if not where or not plan["touches"]:
                        raise
                    details = json.loads((directory / "metrics.json").read_text()).get('failure') or {}
                    winding_failure=details.get('reason')=='arm winding'
                    if winding_failure:
                        winding_retries+=1
                        if not settings.allow_misses or winding_retries>4:
                            raise ValueError(f"{error} No safe arm route was found; the winding limit remains enforced.")
                    frame = details.get('frame',float(where[1]) * 60)
                    near = sorted(plan["touches"], key=lambda t: max(t["down_frame"]-frame, frame-t["up_frame"], 0))
                    candidates = [t for t in near if t['down_frame'] <= frame+2 and t['up_frame'] >= frame-2
                                  and any(p['kind']=='chain' for p in t['parts'])]
                    if details.get('hands'):
                        candidates.sort(key=lambda t:t['hand'] not in details['hands'])
                    if candidates:
                        touch = candidates[0]
                        part = min((p for p in touch['parts'] if p['kind']=='chain'),
                                   key=lambda p:max(p['from_frame']-frame,frame-p['to_frame'],0))
                        parent = part.get('parent_occupation_id') or part['occupation_id']
                        at = round(frame*1000/60) - plan['chart']['audio_offset_ms']
                        padding=180 if winding_failure else 34
                        start,end = max(0,at-padding),at+padding
                        prior = next((x for x in avoid_contacts if x['parent']==parent and x['start_ms'] <= end+100 and x['end_ms'] >= start-100),None)
                        if prior:
                            prior['start_ms'] = max(0,min(start,prior['start_ms']-padding))
                            prior['end_ms'] = max(end,prior['end_ms']+padding)
                        else:
                            avoid_contacts.append({'parent':parent,'start_ms':start,'end_ms':end,
                                                   'reason':details.get('reason','motion limits')})
                    else:
                        if not settings.allow_misses: raise
                        events = set(e for part in near[0]["parts"] for e in part["served_events"])
                        if not events - excluded: raise
                        excluded.update(events)
                    with (directory / "render.log").open("a") as log:
                        log.write(f"Retrying with {len(avoid_contacts)} local arc-release windows and {len(excluded)} other excluded events; {details.get('reason','motion limits')} at {frame/60:.3f} s.\n")
            report = json.loads((directory / "metrics.json").read_text())
            frames = report["frame_count"]
            audio_start = plan["preview"]["audio_start_ms"]
            replay = {"width_mm": plan["device"]["screen_mm"][0], "height_mm": plan["device"]["screen_mm"][1],
                      "scroll_speed": settings.scroll_speed,
                      "chart_start_ms": plan["preview"]["chart_start_ms"],
                      "audio_offset_ms": plan["chart"]["audio_offset_ms"],
                      "source_audio_offset_ms": plan["chart"].get("source_audio_offset_ms", plan["chart"]["audio_offset_ms"]),
                      "touches": [{"id": t["id"],
                                   "down_frame": t["down_frame"], "up_frame": t["up_frame"],
                                   "points": [{"x": x, "y": y} for x, y in t["path_mm"]]} for t in plan["touches"]]}
            write_json(directory / "replay.json", replay)
            game_chart, game_aff, game_project = prepare_project(chart, aff, project_path, directory, settings.enable_red_notes)
            digest = hashlib.sha256((directory / "replay.json").read_bytes() +
                                    str((audio_start, frames)).encode() + game_aff.read_bytes())
            project = re.sub(r"(?m)^\s*lastWorkingTiming:.*\n", "", game_project.read_text())
            digest.update(project.encode())
            for asset in sorted(game_chart.rglob("*")):
                if ".rendering" not in asset.parts and asset.suffix.lower() in (".ogg", ".mp3", ".wav", ".flac", ".png", ".jpg", ".jpeg", ".lua"):
                    digest.update(asset.relative_to(game_chart).as_posix().encode())
                    digest.update(asset.read_bytes())
            for source in ("Export.cs", "Replay.cs", "RedNotes.cs", "Patch.cs", "export.sh"):
                digest.update((ROOT / "arc-create-export" / source).read_bytes())
            cache = ROOT / "output/gameplay/replays"
            cache.mkdir(parents=True, exist_ok=True)
            cached = cache / (digest.hexdigest() + ".mp4")
            clip = directory / "gameplay.mp4"
            if not all(Path(str(cached) + suffix).exists() for suffix in ('', '.judgements.json', '.base.mp4', '.music.wav', '.hits.wav', '.sounds.json')):
                self.command([ROOT / "arc-create-export/export.sh", game_project, cached,
                              audio_start, audio_start+math.ceil((frames+2)*1000/30), directory / "replay.json"],
                             directory, "ArcCreate judging the two-hand replay", 35)
            mix_config = {key: config[key] for key in ('song_delay_ms', 'hit_sounds')}
            identity = hashlib.sha256(json.dumps([digest.hexdigest(), mix_config], sort_keys=True).encode() +
                                      (ROOT / 'dashboard/media.py').read_bytes()).hexdigest()
            mixed = cache / 'mixes' / (identity + '.mp4')
            if not mixed.exists():
                self.command([ROOT / '.venv/bin/python', ROOT / 'dashboard/media.py', cached, mixed,
                              '--settings', directory / 'settings.json'], directory, 'Mixing song and actual hit sounds', 75)
            clip.symlink_to(os.path.relpath(mixed, directory))
            stem = directory / 'hit-sounds.wav'
            stem.symlink_to(os.path.relpath(Path(str(cached) + '.hits.wav'), directory))
            stems = {'hits': media_url(stem)}
            sound_events = json.loads(Path(str(cached) + '.sounds.json').read_text())
            sound_events.pop('events', None)
            shutil.copyfile(str(cached) + ".judgements.json", directory / "judgements.json")
            self.command([BLENDER, "--factory-startup", "-b", "--python-exit-code", "1", "-P", ROOT / "animate.py", "--",
                          plan_path, directory / "scene.blend", "--gameplay", clip,
                          "--settings", directory / "settings.json", "--report", directory / "metrics.json",
                          "--motion", directory / "motion.json", "--glb", directory / "scene.glb"],
                         directory, "Preparing the live 3D scene", 80)
            if self.cancelled.is_set():
                raise InterruptedError("Render cancelled.")
            report = json.loads((directory / "metrics.json").read_text())
            judgments = json.loads((directory / "judgements.json").read_text())
            tick_validation = None
            if plan.get('arc_tick_targets'):
                from scoring_report import compare_ticks
                check = compare_ticks(plan,judgments)
                write_json(directory/'arc-tick-check.json',check)
                tick_validation = {'predicted_hits':check['predicted_hits'],'confirmed_hits':check['confirmed_hits'],
                    'unconfirmed':len(check['unconfirmed_predictions']),'incidental_hits':check['incidental_hits'],'report':media_url(directory/'arc-tick-check.json')}
            self.update(status="complete", stage="Ready to play", progress=100, metrics=report,
                        live={"scene": media_url(directory / "scene.glb"), "motion": media_url(directory / "motion.json")},
                        presentation=json.loads((directory / "presentation-result.json").read_text()),
                        judgements={k: v for k, v in judgments.items() if k not in ("judgements", "red_judgements", "arc_ticks")},
                        judgement_timeline=media_url(directory / "judgements.json"),
                        planned_misses=plan["misses"], video=media_url(clip),
                        audio_stems=stems, sound_events=sound_events,
                        planning=plan["planning"], acceleration=plan.get("acceleration"), note_centering=plan.get("note_centering"), crossing_ergonomics=plan.get("crossing_ergonomics"), arc_tick_validation=tick_validation,
                        blend=media_url(directory / "scene.blend"),
                        log=media_url(directory / "render.log"))
        except InterruptedError as error:
            self.update(status="cancelled", stage=str(error), progress=0)
        except Exception as error:
            self.update(status="failed", stage="Settings need adjustment", error=str(error),
                        log=media_url(directory / "render.log"))


QUEUE = RenderQueue()


class Handler(BaseHTTPRequestHandler):
    def json(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/bootstrap":
            self.json({"charts": CHARTS, "defaults": Settings().model_dump(),
                       "defaults_revision": DEFAULTS_REVISION, "startup_preferences": STARTUP_PREFERENCES,
                       "presentation_revision":hashlib.sha256((ROOT / "presentation_scene.py").read_bytes()).hexdigest()[:12], **QUEUE.state()})
        elif path == "/api/state":
            self.json(QUEUE.state())
        elif path.startswith("/media/"):
            target = (ROOT / "output" / unquote(path[7:])).resolve()
            if target.is_relative_to((ROOT / "output").resolve()) and target.is_file():
                self.file(target)
            else:
                self.send_error(404)
        elif path.startswith("/vendor/"):
            target = (Path(__file__).parent / path[1:]).resolve()
            if target.parent == (Path(__file__).parent / "vendor").resolve() and target.suffix == ".js":
                self.file(target)
            else:
                self.send_error(404)
        elif path in ("/", "/app.js", "/style.css", "/live-preview.js", "/chart-range.js", "/preview-recorder.js", "/i18n.js", "/i18n-messages.js"):
            self.file(Path(__file__).parent / ("index.html" if path == "/" else path[1:]))
        else:
            self.send_error(404)

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.json({"error": "Requests must come from this dashboard."}, 403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 16384:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(size))
            path = urlparse(self.path).path
            if path == "/api/render":
                self.json(QUEUE.submit(Settings.model_validate(data)), 202)
            elif path == "/api/cancel":
                QUEUE.cancel(data["id"])
                self.json({"cancelled": True})
            elif path == "/api/presentation":
                self.json(QUEUE.refresh_scene(data["id"]), 202)
            elif path == "/api/ending":
                self.json(QUEUE.add_ending(data["id"]), 202)
            elif path == "/api/export":
                self.json(QUEUE.export_video(data["id"], data.get("view")), 202)
            else:
                self.send_error(404)
        except ValidationError as error:
            self.json({"error": "; ".join(".".join(map(str, e["loc"])) + " " + e["msg"]
                                         for e in error.errors())}, 400)
        except RuntimeError as error:
            self.json({"error": str(error)}, 409)
        except (ValueError, KeyError) as error:
            self.json({"error": str(error)}, 400)

    def file(self, path):
        if not path.is_file():
            return self.send_error(404)
        size = path.stat().st_size
        start, end = 0, size-1
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or not any(match.groups()):
                return self.send_error(416)
            a, b = match.groups()
            if a:
                start, end = int(a), min(int(b), end) if b else end
            else:
                start = max(0, size-int(b))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(206 if requested else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end-start+1))
        self.end_headers()
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = end-start+1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, message, *args):
        if not self.path.startswith("/api/state"):
            super().log_message(message, *args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", options.port), Handler)
    print(f"Rhythm workbench is ready at http://127.0.0.1:{options.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if QUEUE.active and QUEUE.active["status"] == "running":
            QUEUE.cancel(QUEUE.active["id"])
        server.server_close()

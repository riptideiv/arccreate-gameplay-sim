import { RhythmPreview } from './live-preview.js';
import { ChartRange } from './chart-range.js';
import { PreviewRecorder } from './preview-recorder.js';

const form = document.querySelector("#settings");
const video = document.querySelector("#video");
const renderButton = document.querySelector("#render");
const fields = [...form.elements].filter(field => field.name);
const byName = name => fields.find(field => field.name === name);
const $ = id => document.getElementById(id);
let bootstrap, selected, lastHistory = "", lastTerminal = "", currentJob;
let pollTimer;
let exportSubmitting = false;
let sceneSubmitting = false;
const chartRange = new ChartRange(form, () => markDirty());
let live, recorder, deferredTake, webglDownload, webglResult;
try { live = new RhythmPreview($("live-scene"), video, $("live-performance")); }
catch (error) { $("live-performance").textContent = "Live 3D unavailable: " + error.message; }

recorder = new PreviewRecorder(live, video, state => {
  $('webgl-export-hint').textContent = state.message;
  $('webgl-export-progress').hidden = !state.active;
  $('webgl-export-progress').value = state.progress;
});
function lockRecording(locked) {
  form.inert = locked;
  for (const element of document.querySelectorAll('#live-transport button, #live-transport input, #playback-speed, #history button')) element.disabled = locked;
  $('cancel-webgl').hidden = !locked;
  $('webgl-export-progress').hidden = !locked;
  setRenderDisabled(currentJob?.status === 'running');
  updateExportControls();
}
$('cancel-webgl').addEventListener('click', () => recorder.cancel());
$('export-webgl').addEventListener('click', async () => {
  if (!selected?.live || recorder.active) return;
  $('error').hidden = true;
  const savedSpeed = $('playback-speed').value;
  try {
    // record() starts synchronously inside this user gesture, then awaits media.
    const task = recorder.record(); $('playback-speed').value = '1'; lockRecording(true);
    const result = await task;
    if (webglDownload) URL.revokeObjectURL(webglDownload);
    webglDownload = URL.createObjectURL(result.blob);
    webglResult = {width:result.width, height:result.height, id:selected.id, scene:selected.live.scene, view:JSON.stringify(Object.fromEntries([...cameraFields].map(name => [name, settings()[name]])))};
    $('download-webgl').href = webglDownload;
    Object.assign($('download-webgl').dataset, {mimeType:result.mimeType,width:result.width,height:result.height,wallSeconds:result.wallSeconds,durationSeconds:result.duration});
    $('download-webgl').download = `${selected.settings.chart}-${selected.settings.start_s}-${selected.settings.end_s}-preview-${result.width}x${result.height}.${result.extension}`;
    $('download-webgl').textContent = `Download ${result.extension.toUpperCase()}`;
    $('download-webgl').hidden = false;
    $('webgl-export-hint').textContent = `Video ready · ${result.width} × ${result.height} · up to 30 fps with game audio.`;
  } catch (error) {
    $('webgl-export-hint').textContent = error.message;
    if (!error.message.startsWith('Export cancelled')) showError(error.message);
  } finally {
    $('playback-speed').value = savedSpeed;
    lockRecording(false);
    if (deferredTake) { const pending = deferredTake; deferredTake = null; showTake(...pending); }
  }
});

const cameraFields = new Set(['camera_distance_m', 'camera_elevation_deg', 'camera_yaw_deg', 'camera_lens_mm', 'quality']);
const clock = value => Math.floor(value / 60) + ':' + String(Math.floor(value % 60)).padStart(2, '0');
$("play-preview").addEventListener('click', async () => {
  try { if (video.paused) { await recorder.resumeAudio(); await video.play(); } else video.pause(); }
  catch (error) { showError('Could not play the preview: ' + error.message); }
});
$("seek-preview").addEventListener('input', event => { video.currentTime = Number(event.target.value); live?.pose(video.currentTime); });
function updateTransport() {
  $("play-preview").textContent = video.paused ? 'Play' : 'Pause';
  $("play-preview").setAttribute('aria-label', video.paused ? 'Play preview' : 'Pause preview');
  const duration = Number.isFinite(video.duration) ? video.duration : 0;
  $("seek-preview").max = duration || 1;
  $("seek-preview").value = video.currentTime;
  $("preview-time").textContent = clock(video.currentTime) + ' / ' + clock(duration);
}
for (const event of ['timeupdate', 'loadedmetadata', 'play', 'pause', 'seeked']) video.addEventListener(event, updateTransport);

function setRenderDisabled(disabled) {
  renderButton.disabled = disabled || !!recorder?.active;
  $("mobile-render").disabled = disabled || !!recorder?.active;
}

for (const field of fields.filter(field => field.type === "number")) {
  field.inputMode = field.step === "1" ? "numeric" : "decimal";
}

function normalizeRun(run) {
  if (!run) return run;
  const saved = run.settings;
  const oldRepeat = saved.prefer_same_hand ? 0 : 5;
  const oldJack = saved.prefer_same_hand ? 0 : 20;
  return { ...run, legacyArcTrace: !("prefer_arc_trace" in saved), legacyArcPaths: !("arc_path_mode" in saved), legacyReleasePolicy: !("full_arc_hold" in saved),
    settings: { ...bootstrap.defaults, ...saved,
    arc_path_mode: saved.arc_path_mode ?? run.planning?.arc_path_mode ?? 'trace',
    prefer_arc_trace: saved.prefer_arc_trace ?? false,
    full_arc_hold: saved.full_arc_hold ?? true,
    full_ground_hold: saved.full_ground_hold ?? true,
    repeat_weight: saved.repeat_weight ?? oldRepeat,
    jack_weight: saved.jack_weight ?? oldJack } };
}

function updateReleasePolicy() {
  const traceAvailable = byName('arc_path_mode').value === 'score';
  byName('prefer_arc_trace').disabled = !traceAvailable;
  byName('arc_trace_weight').disabled = !traceAvailable || !byName('prefer_arc_trace').checked;
  const fullArc = byName("full_arc_hold").checked;
  byName("partial_hold_weight").disabled = !fullArc;
  byName("partial_ground_hold_weight").disabled = !byName("full_ground_hold").checked;
  $("arc-release-summary").textContent = fullArc
    ? "Arcs prefer full contact; tick-based early release is available at a cost."
    : "Arcs release once their final scoring tick is secured, before the visual tail ends.";
}

function updateClickInterval() {
  $("repeat-disabled").hidden = Number(byName("repeat_weight").value) !== 0;
  $("jack-disabled").hidden = Number(byName("jack_weight").value) !== 0;
  const down = byName("point_contact_frames");
  const air = byName("min_air_frames");
  if (!down.value || !air.value || !down.validity.valid || !air.validity.valid) {
    $("click-interval").textContent = "";
    return;
  }
  const frames = Number(down.value) + Number(air.value);
  $("click-interval").textContent = "Point taps require at least " + (frames * 1000 / 60).toFixed(1) +
    " ms between presses, at most " + (60 / frames).toFixed(1) + " taps/s per hand before movement is considered.";
}

function settings() {
  return Object.fromEntries(fields.map(field => [field.name, field.type === "checkbox" ? field.checked :
    field.type === "number" ? Number(field.value) : field.value]));
}

function fill(values) {
  for (const field of fields) {
    if (!(field.name in values)) continue;
    if (field.type === "checkbox") field.checked = values[field.name];
    else field.value = values[field.name];
  }
  syncSliders();
  updateChartCompatibility();
  updateRedNotes();
  updateClickInterval();
  updateReleasePolicy();
  live?.setCamera(settings());
  markDirty();
}

function updateRedNotes() {
  $('target-red').disabled = !byName('enable_red_notes').checked;
}

function populateCharts() {
  const select = byName('chart');
  select.replaceChildren();
  const groups = new Map();
  const entries = Object.entries(bootstrap.charts);
  for (const [key, chart] of entries) {
    const groupName = chart.group || 'Original projects';
    let group = groups.get(groupName);
    if (!group) {
      group = document.createElement('optgroup'); group.label = groupName;
      groups.set(groupName, group); select.append(group);
    }
    const option = document.createElement('option');
    option.value = key;
    option.textContent = chart.title + (chart.available === false ? ' · unavailable' : '');
    option.disabled = chart.available === false;
    if (chart.unavailable_reason) option.title = chart.unavailable_reason;
    group.append(option);
  }
  const unavailable = entries.filter(([, chart]) => chart.available === false).length;
  $('chart-library-info').textContent = `${entries.length} charts · grouped by song and difficulty` +
    (unavailable ? ` · ${unavailable} require newer chart-format support.` : '.');
}

function updateChartCompatibility() {
  const chart = bootstrap?.charts[byName('chart').value];
  const warnings = Object.entries(chart?.warnings || {});
  $('chart-compatibility').hidden = !warnings.length && !chart?.unavailable_reason;
  $('chart-compatibility').textContent = chart?.unavailable_reason || (warnings.length ?
    'Compatibility notice: this chart uses features the planner may not fully model (' +
    warnings.map(([code]) => code.replace(/^unsupported_/, '').replaceAll('_', ' ')).join(', ') + ').' : '');
}

function syncSliders() {
  for (const slider of form.querySelectorAll("[data-for]")) slider.value = byName(slider.dataset.for).value;
  if (bootstrap) chartRange.sync(bootstrap.charts[byName('chart').value].duration);
}

function sameSettings(a, b) {
  return fields.every(field => (selected?.live && cameraFields.has(field.name)) || a[field.name] === b[field.name]);
}

function markDirty() {
  if (!selected) return;
  const dirty = !sameSettings(settings(), selected.settings);
  $("preview-tag").textContent = dirty ? "Settings changed" : selected.metrics.contact_preserving_pose_solver ? "Contacts preserved" : selected.metrics.collision_verification === false ? "Speed verified" : "Motion verified";
  $("preview-tag").className = "tag " + (dirty ? "dirty" : "ready");
  updateExportControls();
}

function updateExportControls() {
  if (!selected) return;
  const current = settings();
  const running = currentJob?.status === 'running' || exportSubmitting || sceneSubmitting || recorder?.active;
  const currentSet = selected.presentation?.revision === bootstrap.presentation_revision;
  $('presentation-set').hidden = !selected.live;
  $('refresh-scene').disabled = running || currentSet;
  $('refresh-scene').textContent = sceneSubmitting || (running && currentJob?.scene_only) ? 'Updating…' : currentSet ? 'Set up to date' : 'Update presentation';
  $('presentation-hint').textContent = currentSet ? 'Walnut tabletop · woven mat · plant · ballpoint pen.' : 'Update this saved take’s scene. Reuses its hands, contact plan and gameplay; clears the old MP4 download.';
  const dirty = !sameSettings(current, selected.settings);
  const invalidCamera = [...cameraFields].some(name => !byName(name).validity.valid || byName(name).value === '');
  const saved = selected.export_video || (!selected.live ? selected.video : null);
  $('webgl-export').hidden = !selected.live;
  if (!recorder?.supported()) $('webgl-export-hint').textContent = 'This browser cannot record a canvas with audio. Use Blender MP4 export below.';
  $('export-webgl').disabled = !!recorder?.active || !recorder?.supported() || !live?.enabled || invalidCamera;
  $('download-webgl').hidden = !webglResult || webglResult.id !== selected.id || webglResult.scene !== selected.live?.scene;
  if (webglResult && webglResult.id === selected.id && !recorder.active) $('webgl-export-hint').textContent = webglResult.view !== JSON.stringify(Object.fromEntries([...cameraFields].map(name => [name, current[name]]))) ? 'The saved preview video uses an earlier view. Export again to record the displayed view.' : `Video ready · ${webglResult.width} × ${webglResult.height} · up to 30 fps with game audio.`;
  $('video-export').hidden = false;
  $('export-video').hidden = !selected.live;
  $('export-video').disabled = running || dirty || invalidCamera;
  const ownExport = currentJob?.export_only && currentJob.id === selected.id;
  $('export-video').textContent = exportSubmitting ? 'Starting export…' : running && ownExport ? 'Exporting…' : 'Export Blender MP4';
  $('download-export').hidden = !saved;
  if (saved) $('download-export').href = saved;
  const oldCamera = selected.export_view && [...cameraFields].some(name => current[name] !== selected.export_view[name]);
  const dimensions = current.quality === 'final' ? '1024 × 768' : '640 × 480';
  let message = !selected.live ? 'The saved 3D video is ready to download.'
    : exportSubmitting ? 'Starting your video export…'
    : running && ownExport ? (currentJob.stage + ' · ' + Math.round(currentJob.progress) + '%')
    : running ? 'The renderer is busy. Export will be available when it finishes.'
    : dirty ? 'Build the preview to include changed chart or hand settings in the export.'
    : invalidCamera ? 'Enter valid camera settings before exporting.'
    : oldCamera ? 'The saved MP4 uses an earlier camera. Export again to use this view.'
    : `Hands, tablet and presentation set with audio · MP4 · 30 fps · ${dimensions}. Uses the current camera.`;
  if ($('export-hint').textContent !== message) $('export-hint').textContent = message;
}

for (const slider of form.querySelectorAll("[data-for]")) {
    slider.addEventListener("input", () => { byName(slider.dataset.for).value = slider.value; live?.setCamera(settings()); markDirty(); });
}
form.addEventListener("input", event => {
  updateRedNotes();
  if (event.target.name) syncSliders();
  updateClickInterval();
  updateReleasePolicy();
  live?.setCamera(settings());
  if (event.target.name === 'quality') live?.resize();
  markDirty();
});
byName("chart").addEventListener("change", () => {
  const chart = bootstrap.charts[byName("chart").value];
  byName("start_s").value = chart.start_s;
  byName("end_s").value = chart.end_s;
  chartRange.sync(chart.duration);
  updateChartCompatibility();
  markDirty();
});
$("reset").addEventListener("click", () => fill(bootstrap.defaults));

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The local renderer could not complete the request.");
  return data;
}

function showError(message, log) {
  $("error").hidden = false;
  $("error-message").textContent = message;
  $("error-log").hidden = !log;
  if (log) $("error-log").href = log;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  $("error").hidden = true;
  setRenderDisabled(true);
  try {
    if (selected?.live && sameSettings(settings(), selected.settings)) {
      live?.setCamera(settings()); setRenderDisabled(false); return;
    }
    const run = await api("/api/render", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings())
    });
    displayJob(normalizeRun(run));
    if (matchMedia("(max-width: 700px)").matches) $("preview").scrollIntoView({ block: "start" });
    schedulePoll(300);
  } catch (error) {
    showError(error.message);
    setRenderDisabled(false);
  }
});
$("cancel").addEventListener("click", async () => {
  if (!currentJob) return;
  $("cancel").disabled = true;
  try {
    await api("/api/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: currentJob.id })
    });
    $("job-stage").textContent = "Stopping render…";
  } catch (error) { showError(error.message); }
});
$("playback-speed").addEventListener("change", () => { video.playbackRate = Number($("playback-speed").value); });
$("export-video").addEventListener('click', async () => {
  if (!selected?.live || $('export-video').disabled) return;
  const current = settings();
  const view = Object.fromEntries([...cameraFields].map(name => [name,current[name]]));
  exportSubmitting = true;
  updateExportControls();
  try { displayJob(normalizeRun(await api('/api/export', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:selected.id,view})}))); schedulePoll(300); }
  catch (error) { showError(error.message); }
  finally { exportSubmitting = false; updateExportControls(); }
});
$('refresh-scene').addEventListener('click', async () => {
  if (!selected?.live || $('refresh-scene').disabled) return;
  sceneSubmitting = true; updateExportControls();
  try {
    displayJob(normalizeRun(await api('/api/presentation', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id:selected.id})})));
    schedulePoll(300);
  } catch (error) { showError(error.message); }
  finally { sceneSubmitting = false; updateExportControls(); }
});
video.addEventListener("timeupdate", () => {
  if (!selected?.metrics) return;
  const x = 35 + Math.min(1, video.currentTime / selected.metrics.duration_s) * 745;
  $("playhead").setAttribute("x1", x);
  $("playhead").setAttribute("x2", x);
});

function displayJob(run) {
  currentJob = run;
  const running = run?.status === "running";
  setRenderDisabled(running);
  updateExportControls();
  $("job").hidden = !running;
  if (!run) return;
  if (running) {
    $("cancel").disabled = false;
    $("job-stage").textContent = run.stage;
    $("progress").value = run.progress;
    const planning = run.planning_progress;
    $("planning-status").hidden = !planning;
    if (planning) {
      const labels = {loading: 'Reading chart', preparing: 'Preparing touchpoint search',
        geometry: 'Checking arc input regions', arc_profiles: 'Preparing snake paths',
        candidates: 'Generating touchpoint candidates', graph: 'Preparing search constraints',
        search: 'Searching touchpoint plans', finalizing: 'Reconstructing hand contacts',
        done: 'Touchpoint search complete', writing: 'Saving contact plan'};
      const known = planning.total > 0;
      const percent = known ? Math.min(100, 100 * planning.completed / planning.total) : null;
      $("planning-label").textContent = (labels[planning.phase] || planning.phase)
        + (known ? ' · ' + percent.toFixed(1) + '% · ' + planning.completed.toLocaleString() + ' / '
          + planning.total.toLocaleString() + (planning.phase === 'arc_profiles' ? ' snakes' : ' items') : '')
        + (planning.attempt > 1 ? ' · retry with individual scoring ticks' : '');
      if (known) $("planning-progress").value = percent;
      else $("planning-progress").removeAttribute('value');
      const elapsed = Math.max(0, Math.floor((planning.elapsed_s || 0)
        + Math.max(0, Date.now()/1000 - planning.updated_at)));
      const details = [Math.floor(elapsed/60) + ':' + String(elapsed%60).padStart(2, '0') + ' elapsed'];
      if (planning.chart_ms != null) details.push('chart ' + (run.settings.start_s + planning.chart_ms/1000).toFixed(2) + ' s');
      if (planning.states != null) details.push(planning.states.toLocaleString() + ' alternatives retained');
      if (planning.phase === 'search') details.push('Progress counts processed items; remaining items can take different amounts of time.');
      $("planning-detail").textContent = details.join(' · ');
    }
    const chart = bootstrap.charts[run.settings.chart];
    $("job-detail").textContent = chart.title + " · " + run.settings.start_s + "–" + run.settings.end_s +
      " s. The previous take stays available while this renders.";
  } else if (run.id + run.status + (run.export_video || '') + (run.export_error || '') + (run.live?.scene || '') + (run.scene_error || '') !== lastTerminal) {
    lastTerminal = run.id + run.status + (run.export_video || '') + (run.export_error || '') + (run.live?.scene || '') + (run.scene_error || '');
    if (run.status === "complete") {
      if (selected?.id === run.id && run.live?.scene !== selected.live?.scene) {
        showTake(run);
      } else if (selected?.id === run.id && (run.export_video || run.export_error)) {
        selected = run;
        updateExportControls();
      } else if (!selected || run.created >= selected.created) showTake(run);
      if (run.scene_error) showError('The scene update stopped: ' + run.scene_error + ' The previous scene remains available.');
      else if (run.export_error) showError('The MP4 export stopped: ' + run.export_error + ' The live preview remains available.');
      else $("error").hidden = true;
    } else if (run.status === "failed") {
      showError(run.error, run.log);
    }
  }
}

function showTake(run, restoreControls = false) {
  if (recorder?.active) { deferredTake = [run, restoreControls]; return; }
  selected = run;
  $("error").hidden = true;
  if (restoreControls) fill(run.settings);
  const chart = bootstrap.charts[run.settings.chart];
  $("preview-title").textContent = chart.title;
  $("preview-description").textContent = run.settings.start_s + "–" + run.settings.end_s +
    " s · " + (run.live ? "live 3D · ArcCreate touch replay" : "saved MP4 · original autoplay");
  video.pause();
  video.src = run.video;
  video.controls = !run.live;
  document.querySelector('.player').classList.toggle('live', !!run.live);
  $("live-transport").hidden = !run.live;
  $("export-video").hidden = !run.live;
  if (run.live && !live) showError('This browser cannot start WebGL. Try Safari or Chrome with WebGL enabled.');
  live?.load(run).then(() => { if (selected?.id === run.id && run.live) { live.setCamera(settings()); updateExportControls(); } })
    .catch(error => showError('Live preview could not load: ' + error.message));
  $("judgement-report").hidden = !run.judgements;
  $("arc-tick-summary").hidden = !run.arc_tick_validation;
  $("arc-tick-report").hidden = !run.arc_tick_validation;
  if (run.arc_tick_validation) {
    const check=run.arc_tick_validation;
    $("arc-tick-summary").textContent = check.confirmed_hits + ' / ' + check.predicted_hits + ' planned arc catches confirmed by native replay'
      + (check.unconfirmed ? ' · ' + check.unconfirmed + ' unconfirmed; see report.' : '.')
      + (check.incidental_hits ? ' Also caught ' + check.incidental_hits + ' additional arc ticks.' : '');
    $("arc-tick-report").href=check.report;
  }
  if (run.judgements) {
    const j = run.judgements;
    $('hit-audio-report').hidden = !run.audio_stems?.hits;
    if (run.audio_stems?.hits) {
      $('hit-audio-download').href = run.audio_stems.hits;
      $('hit-audio-counts').textContent = `${run.sound_events.block_hits} block clicks · ${run.sound_events.arc_tick_hits} snake tick clicks from actual catches.`;
    }
    $("judgement-summary").textContent = j.hits + ' hits · ' + j.misses + ' misses · score ' + j.score.toLocaleString()
      + ((j.red_hits || j.red_misses) ? ` · red: ${j.red_hits || 0} caught / ${j.red_misses || 0} missed (no score)` : '')
      + (run.planning?.arc_reacquisitions ? ' · ' + run.planning.arc_reacquisitions + ' planned arc reacquisitions' : '');
    $("skipped-targets").replaceChildren();
    for (const miss of run.planned_misses || []) {
      const li = document.createElement('li'), button = document.createElement('button');
      button.type = 'button';
      button.textContent = (run.settings.start_s + miss.start_ms / 1000).toFixed(2) + ' s · ' +
        (miss.kind === 'red_sky' ? 'red sky note · no score' : (miss.kind === 'arc_tick' ? 'arc tick' : miss.kind) + ' · ' + miss.scoring_ticks + ' scoring tick' + (miss.scoring_ticks === 1 ? '' : 's'));
      button.title = miss.reason || '';
      button.addEventListener('click', () => { video.currentTime = Math.max(0, miss.start_ms / 1000 - 1); $("preview").scrollIntoView({block:'start'}); });
      li.append(button); $("skipped-targets").append(li);
    }
    if (!run.planned_misses?.length) { const li = document.createElement('li'); li.textContent = 'No targets deliberately skipped by the planner.'; $("skipped-targets").append(li); }
  }
  video.playbackRate = Number($("playback-speed").value);
  $("empty").hidden = true;
  $("analysis").hidden = false;
  const metrics = run.metrics;
  const hands = Object.values(metrics.hands);
  $("peak-speed").textContent = Math.max(...hands.map(hand => hand.peak_surface_bound_m_s)).toFixed(2) + " m/s";
  $("speed-limit").textContent = "Limit " + metrics.max_speed_m_s.toFixed(2) + " m/s";
  $("peak-turn").textContent = Math.max(...hands.map(hand => hand.peak_angular_deg_s)).toFixed(0) + "°/s";
  $("turn-limit").textContent = "Limit " + metrics.angular_limit_deg_s + "°/s";
  const collisionsOff = metrics.collision_verification === false;
  const fitted = metrics.contact_preserving_pose_solver;
  $("gap-label").textContent = fitted ? "Closest glove gap" : collisionsOff ? "Glove collision check" : "Closest hand gap";
  $("gap").textContent = fitted || !collisionsOff ? metrics.minimum_clearance_mm.toFixed(1) + " mm" : "Disabled";
  $("gap-note").textContent = fitted ? (metrics.minimum_clearance_mm < 0 ? "Residual overlap · playback continues" : "Contact pivots · quarter-step samples") : collisionsOff ? "Touch points preserved" : "Sampled at quarter steps";
  $("contacts").textContent = metrics.contacts;
  $("blend-link").href = run.blend;
  $("video-link").href = run.video;
  $("video-link").textContent = 'Download gameplay only';
  $("video-link").hidden = !run.live;
  $("run-log").href = run.log;
  const scale = metrics.max_speed_m_s * 1.17;
  const y = value => 130 - value / scale * 110;
  for (const side of ["left", "right"]) {
    const points = metrics.hands[side].speed_samples_m_s;
    const path = points.map((speed, index) => (index ? "L" : "M") +
      (35 + index / Math.max(1, points.length - 1) * 745).toFixed(1) + "," + y(speed).toFixed(1)).join(" ");
    $(side + "-line").setAttribute("d", path);
  }
  $("limit-line").setAttribute("d", "M35," + y(metrics.max_speed_m_s) + "H785");
  $("axis-limit").textContent = metrics.max_speed_m_s.toFixed(1) + " m/s";
  $("axis-end").textContent = metrics.duration_s.toFixed(1) + " s";
  $("playhead").setAttribute("x1", 35);
  $("playhead").setAttribute("x2", 35);
  markDirty();
  lastHistory = "";
}

function history(runs) {
  const identity = JSON.stringify(runs.map(run => [run.id, run.status])) + selected?.id;
  if (identity === lastHistory) return;
  lastHistory = identity;
  const container = $("history");
  const takes = runs.filter(run => run.status === "complete");
  $("take-count").textContent = takes.length ? takes.length + " saved locally" : "Saved locally";
  if (!takes.length) return;
  container.replaceChildren();
  for (const run of takes.slice(0, 8)) {
    const button = document.createElement("button");
    button.type = "button";
    button.disabled = !!recorder?.active;
    button.className = "take" + (run.id === selected?.id ? " selected" : "");
    button.setAttribute("aria-pressed", String(run.id === selected?.id));
    const mark = document.createElement("span");
    mark.className = "take-mark";
    mark.textContent = "▷";
    const info = document.createElement("span");
    info.className = "take-info";
    const title = document.createElement("strong");
    title.textContent = bootstrap.charts[run.settings.chart].title;
    const description = document.createElement("small");
    description.textContent = run.settings.start_s + "–" + run.settings.end_s + " s · " +
      run.settings.max_speed_m_s + " m/s · lift " + run.settings.lift_mm + " mm";
    info.append(title, description);
    const timestamp = document.createElement("span");
    timestamp.className = "take-time";
    timestamp.textContent = new Date(run.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    button.append(mark, info, timestamp);
    button.addEventListener("click", () => { showTake(run, true); history(runs); });
    container.append(button);
  }
}

function schedulePoll(delay) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, delay);
}

async function poll() {
  try {
    const state = await api("/api/state");
    displayJob(normalizeRun(state.active));
    history(state.history.map(normalizeRun));
    schedulePoll(state.active?.status === "running" ? 1000 : 3000);
  } catch (error) {
    document.querySelector(".connection").textContent = "Renderer disconnected";
    schedulePoll(3000);
  }
}

async function start() {
  try {
    bootstrap = await api("/api/bootstrap");
    populateCharts();
    bootstrap.history = bootstrap.history.map(normalizeRun);
    fill(bootstrap.defaults);
    const latest = bootstrap.history.find(run => run.status === "complete");
    if (latest) {
      showTake(latest, true);
      // Keep old takes faithful when restored, but start new work with early arc release.
      if (latest.legacyReleasePolicy || latest.legacyArcPaths || latest.legacyArcTrace) fill({ ...latest.settings,
        prefer_arc_trace: latest.legacyArcTrace ? bootstrap.defaults.prefer_arc_trace : latest.settings.prefer_arc_trace,
        full_arc_hold: latest.legacyReleasePolicy ? bootstrap.defaults.full_arc_hold : latest.settings.full_arc_hold,
        arc_path_mode: latest.legacyArcPaths ? bootstrap.defaults.arc_path_mode : latest.settings.arc_path_mode });
    }
    displayJob(normalizeRun(bootstrap.active));
    history(bootstrap.history);
    schedulePoll(1000);
  } catch (error) {
    showError(error.message);
  }
}
start();


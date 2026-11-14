import { setText, setAttributeText, initI18n } from './i18n.js';
import { RhythmPreview } from './live-preview.js';
import { ChartRange } from './chart-range.js';
import { PreviewRecorder } from './preview-recorder.js';

initI18n();

const form = document.querySelector("#settings");
const video = document.querySelector("#video");
const renderButton = document.querySelector("#render");
const fields = [...form.elements].filter(field => field.name);
const byName = name => fields.find(field => field.name === name);
const $ = id => document.getElementById(id);
let bootstrap, selected, lastHistory = "", lastTerminal = "", currentJob;
let pollTimer;
let generationSubmitting = false;
let lastQueue = "";
const cancelling = new Set();
let exportSubmitting = false;
let sceneSubmitting = false;
const chartRange = new ChartRange(form, () => markDirty());
let live, recorder, deferredTake, webglDownload, webglResult;
try { live = new RhythmPreview($("live-scene"), video, $("live-performance")); }
catch (error) { setText($("live-performance"), "Live 3D unavailable: " + error.message); }

recorder = new PreviewRecorder(live, video, state => {
  setText($('webgl-export-hint'), state.message);
  $('webgl-export-progress').hidden = !state.active;
  $('webgl-export-progress').value = state.progress;
});
function lockRecording(locked) {
  form.inert = locked;
  $('export-fps').disabled = locked;
  for (const element of document.querySelectorAll('#live-transport button, #live-transport input, #playback-speed, #history button')) element.disabled = locked;
  $('cancel-webgl').hidden = !locked;
  $('webgl-export-progress').hidden = !locked;
  setRenderDisabled();
  updateExportControls();
}
$('cancel-webgl').addEventListener('click', () => recorder.cancel());
let endingCapture = false;
$('export-ending').addEventListener('click', () => { endingCapture = true; $('export-webgl').click(); });
$('export-webgl').addEventListener('click', async () => {
  if (!selected?.live || recorder.active) return;
  $('error').hidden = true;
  const savedSpeed = $('playback-speed').value;
  try {
    // record() starts synchronously inside this user gesture, then awaits media.
    const startTime = endingCapture ? selected.ending.start_s : 0; endingCapture = false;
    const task = recorder.record({fps: Number($('export-fps').value), startTime}); $('playback-speed').value = '1'; lockRecording(true);
    const result = await task;
    if (webglDownload) URL.revokeObjectURL(webglDownload);
    webglDownload = URL.createObjectURL(result.blob);
    webglResult = {width:result.width, height:result.height, fps:result.fps, startTime:result.startTime, media:selected.video, id:selected.id, scene:selected.live.scene, view:JSON.stringify(Object.fromEntries([...cameraFields].map(name => [name, settings()[name]])))};
    $('download-webgl').href = webglDownload;
    Object.assign($('download-webgl').dataset, {mimeType:result.mimeType,width:result.width,height:result.height,fps:result.fps,startTime:result.startTime,wallSeconds:result.wallSeconds,durationSeconds:result.duration});
    $('download-webgl').download = `${selected.settings.chart}-${selected.settings.start_s}-${selected.settings.end_s}-${result.startTime ? "ending-" + result.startTime : "preview"}-${result.width}x${result.height}-${result.fps}fps.${result.extension}`;
    setText($('download-webgl'), `Download ${result.extension.toUpperCase()}`);
    $('download-webgl').hidden = false;
    setText($('webgl-export-hint'), `Video ready · ${result.width} × ${result.height} · ${result.fps} fps target with game audio.`);
  } catch (error) {
    setText($('webgl-export-hint'), error.message);
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
  setText($("play-preview"), video.paused ? 'Play' : 'Pause');
  setAttributeText($("play-preview"), 'aria-label', video.paused ? 'Play preview' : 'Pause preview');
  const duration = Number.isFinite(video.duration) ? video.duration : 0;
  $("seek-preview").max = duration || 1;
  $("seek-preview").value = video.currentTime;
  setText($("preview-time"), clock(video.currentTime) + ' / ' + clock(duration));
}
for (const event of ['timeupdate', 'loadedmetadata', 'play', 'pause', 'seeked']) video.addEventListener(event, updateTransport);

function setRenderDisabled() {
  renderButton.disabled = $("mobile-render").disabled = generationSubmitting || !!recorder?.active;
  const busy = currentJob?.status === "running";
  setText($("render-label"), busy ? "Add to generation queue" : "Build live preview");
  setText($("mobile-render"), busy ? "Queue build" : "Generate");
}

for (const field of fields.filter(field => field.type === "number")) {
  field.inputMode = field.step === "1" ? "numeric" : "decimal";
}

function normalizeRun(run) {
  if (!run) return run;
  const saved = run.settings;
  const oldRepeat = saved.prefer_same_hand ? 0 : 5;
  const oldJack = saved.prefer_same_hand ? 0 : 20;
  return { ...run, savedSettings: saved, legacyArcTrace: !("prefer_arc_trace" in saved), legacyArcPaths: !("arc_path_mode" in saved), legacyReleasePolicy: !("full_arc_hold" in saved),
    legacyContactOffsets: !("contact_offset_x_percent" in saved) || !("contact_offset_y_percent" in saved),
    settings: { ...bootstrap.defaults, ...saved,
    arc_path_mode: saved.arc_path_mode ?? run.planning?.arc_path_mode ?? 'trace',
    prefer_arc_trace: saved.prefer_arc_trace ?? false,
    note_center_weight: saved.note_center_weight ?? 0,
    note_center_free_mm: saved.note_center_free_mm ?? 5,
    full_arc_hold: saved.full_arc_hold ?? true,
    full_ground_hold: saved.full_ground_hold ?? true,
    allow_clipping: saved.allow_clipping ?? false,
    tap_interval_ms: saved.tap_interval_ms ?? 0,
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
  setText($("arc-release-summary"), fullArc
    ? "Arcs prefer full contact; tick-based early release is available at a cost."
    : "Arcs release once their final scoring tick is secured, before the visual tail ends.");
}

function updateClickInterval() {
  $("repeat-disabled").hidden = Number(byName("repeat_weight").value) !== 0;
  $("jack-disabled").hidden = Number(byName("jack_weight").value) !== 0;
  const down = byName("point_contact_frames");
  const air = byName("min_air_frames");
  const interval = byName("tap_interval_ms");
  if (!down.value || !air.value || !interval.value || !down.validity.valid || !air.validity.valid || !interval.validity.valid) {
    setText($("click-interval"), "");
    return;
  }
  const frames = Math.max(Number(down.value) + Number(air.value), Math.ceil(Number(interval.value) * 60 / 1000));
  setText($("click-interval"), "Point taps require at least " + (frames * 1000 / 60).toFixed(1) +
    " ms between presses, at most " + (60 / frames).toFixed(1) + " taps/s per hand before movement is considered.");
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
      group = document.createElement('optgroup');
      if (chart.group) group.label = chart.group;
      else setAttributeText(group, 'label', groupName);
      groups.set(groupName, group); select.append(group);
    }
    const option = document.createElement('option');
    option.value = key;
    if (chart.available === false) setText(option, chart.title + ' · unavailable');
    else option.textContent = chart.title;
    option.disabled = chart.available === false;
    if (chart.unavailable_reason) setAttributeText(option, 'title', chart.unavailable_reason);
    group.append(option);
  }
  const unavailable = entries.filter(([, chart]) => chart.available === false).length;
  setText($('chart-library-info'), `${entries.length} charts · grouped by song and difficulty` +
    (unavailable ? ` · ${unavailable} require newer chart-format support.` : '.'));
}

function updateChartCompatibility() {
  const chart = bootstrap?.charts[byName('chart').value];
  const warnings = Object.entries(chart?.warnings || {});
  $('chart-compatibility').hidden = !warnings.length && !chart?.unavailable_reason;
  setText($('chart-compatibility'), chart?.unavailable_reason || (warnings.length ?
    'Compatibility notice: this chart uses features the planner may not fully model (' +
    warnings.map(([code]) => code.replace(/^unsupported_/, '').replaceAll('_', ' ')).join(', ') + ').' : ''));
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
  setText($("preview-tag"), dirty ? "Settings changed" : selected.metrics.contact_preserving_pose_solver ? "Contacts preserved" : selected.metrics.collision_verification === false ? "Speed verified" : "Motion verified");
  $("preview-tag").className = "tag " + (dirty ? "dirty" : "ready");
  updateExportControls();
}

function updateExportControls() {
  if (!selected) return;
  const current = settings();
  const running = currentJob?.status === 'running' || exportSubmitting || sceneSubmitting || recorder?.active;
  const crossings = selected.metrics?.arm_crossings;
  const crossingStatus = !crossings ? 'Generate a new preview to apply planned arm crossings to this take.'
    : crossings.verified ? `Verified arm crossing routes: ${crossings.routes}.`
    : `Clipping allowed · ${crossings.clearance_shortfall_samples} samples below arm clearance.`;
  setText($('crossing-status'), crossingStatus);
  const currentSet = selected.presentation?.revision === bootstrap.presentation_revision;
  $('presentation-set').hidden = !selected.live;
  $('refresh-scene').disabled = running || currentSet;
  setText($('refresh-scene'), sceneSubmitting || (running && currentJob?.scene_only) ? 'Updating…' : currentSet ? 'Set up to date' : 'Update presentation');
  setText($('presentation-hint'), currentSet ? 'Walnut tabletop · woven mat · plant · ballpoint pen.' : 'Update this saved take’s scene. Reuses its hands, contact plan and gameplay; clears the old MP4 download.');
  const dirty = !sameSettings(current, selected.settings);
  const invalidCamera = [...cameraFields].some(name => !byName(name).validity.valid || byName(name).value === '');
  const saved = selected.export_video || (!selected.live ? selected.video : null);
  $('webgl-export').hidden = !selected.live;
  $('add-ending').hidden = selected.ending?.flow === 'arccreate-native-v1';
  setText($('add-ending'), selected.ending ? 'Update results flow' : 'Add results ending');
  $('add-ending').disabled = running || selected.settings.start_s !== 0;
  $('export-ending').hidden = !selected.ending;
  $('export-ending').disabled = !!recorder?.active || !live?.enabled;
  if (!recorder?.supported()) setText($('webgl-export-hint'), 'This browser cannot record a canvas with audio. Use Blender MP4 export below.');
  $('export-webgl').disabled = !!recorder?.active || !recorder?.supported() || !live?.enabled || invalidCamera;
  $('download-webgl').hidden = !webglResult || webglResult.id !== selected.id || webglResult.scene !== selected.live?.scene || webglResult.media !== selected.video;
  if (webglResult && webglResult.id === selected.id && webglResult.media === selected.video && !recorder.active) setText($('webgl-export-hint'), webglResult.view !== JSON.stringify(Object.fromEntries([...cameraFields].map(name => [name, current[name]]))) ? 'The saved preview video uses an earlier view. Export again to record the displayed view.' : `Video ready · ${webglResult.width} × ${webglResult.height} · ${webglResult.fps} fps target with game audio.`);
  $('video-export').hidden = false;
  $('export-video').hidden = !selected.live;
  $('export-video').disabled = running || dirty || invalidCamera || !!selected.ending;
  const ownExport = currentJob?.export_only && currentJob.id === selected.id;
  setText($('export-video'), exportSubmitting ? 'Starting export…' : running && ownExport ? 'Exporting…' : 'Export Blender MP4');
  $('download-export').hidden = !saved;
  if (saved) $('download-export').href = saved;
  const oldCamera = selected.export_view && [...cameraFields].some(name => current[name] !== selected.export_view[name]);
  const dimensions = current.quality === 'final' ? '1024 × 768' : '640 × 480';
  let message = selected.ending ? 'Results endings export through the WebGL preview.' : !selected.live ? 'The saved 3D video is ready to download.'
    : exportSubmitting ? 'Starting your video export…'
    : running && ownExport ? (currentJob.stage + ' · ' + Math.round(currentJob.progress) + '%')
    : running ? 'The renderer is busy. Export will be available when it finishes.'
    : dirty ? 'Build the preview to include changed chart or hand settings in the export.'
    : invalidCamera ? 'Enter valid camera settings before exporting.'
    : oldCamera ? 'The saved MP4 uses an earlier camera. Export again to use this view.'
    : `Hands, tablet and presentation set with audio · MP4 · 30 fps · ${dimensions}. Uses the current camera.`;
  setText($('export-hint'), message);
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
  setText($("error-message"), message);
  $("error-log").hidden = !log;
  if (log) $("error-log").href = log;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (generationSubmitting || recorder?.active) return;
  $("error").hidden = true;
  generationSubmitting = true;
  setRenderDisabled();
  try {
    const run = await api("/api/render", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings())
    });
    if (run.status === "running") displayJob(normalizeRun(run));
    setText($("queue-count"), run.status === "queued" ? "Added to queue" : "Generation started");
    if (matchMedia("(max-width: 700px)").matches) $("generation").scrollIntoView({ block: "start" });
    schedulePoll(100);
  } catch (error) {
    showError(error.message);
  } finally {
    generationSubmitting = false;
    setRenderDisabled();
  }
});

async function cancelRun(id, button, queuedOnly = false) {
  if (cancelling.has(id)) return;
  cancelling.add(id);
  button.disabled = true;
  try {
    await api("/api/cancel", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id, queued_only: queuedOnly })
    });
    if (currentJob?.id === id) setText($("job-stage"), "Stopping render…");
    schedulePoll(100);
  } catch (error) {
    showError(error.message);
  } finally {
    cancelling.delete(id);
    button.disabled = false;
  }
}
$("cancel").addEventListener("click", () => {
  if (currentJob) cancelRun(currentJob.id, $("cancel"));
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
$('add-ending').addEventListener('click', async () => {
  try { displayJob(normalizeRun(await api('/api/ending', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:selected.id})}))); schedulePoll(300); }
  catch(error) { showError(error.message); }
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
  setRenderDisabled();
  updateExportControls();
  $("job").hidden = !running;
  if (!run) return;
  if (running) {
    $("cancel").disabled = cancelling.has(run.id);
    setText($("job-stage"), run.stage);
    $("progress").value = run.progress;
    const pose = run.pose_progress;
    const planning = pose || run.planning_progress;
    $("planning-status").hidden = !planning;
    if (planning) {
      const labels = {backend: 'Preparing touchpoint search', loading: 'Reading chart', preparing: 'Preparing touchpoint search',
        geometry: 'Checking arc input regions', arc_profiles: 'Preparing snake paths',
        candidates: 'Generating touchpoint candidates', graph: 'Preparing search constraints',
        validation: 'Verifying contact plan', search: 'Searching touchpoint plans', finalizing: 'Reconstructing hand contacts',
        done: 'Touchpoint search complete', writing: 'Saving contact plan',
        pose_preparing: 'Preparing hand poses', pose_elbows: 'Adjusting elbows',
        pose_travel: 'Checking clearance along hand paths', pose_wrists: 'Orienting hands',
        pose_validation: 'Verifying hand motion', pose_done: 'Hand orientation complete'};
      const known = planning.total > 0;
      const percent = known ? Math.min(100, 100 * planning.completed / planning.total) : null;
      const retry = {checkpoint: 'resuming near the failure with FAR timing',
        checkpoint_misses: 'resuming near the failure with FAR timing and misses',
        sky_requests: 'retry with overlapping sky notes',
        split: 'retry with individual scoring ticks', intact: 'retry with intact arcs and FAR timing'};
      const retryLabel = retry[planning.retry];
      const units = {arc_profiles: 'snakes', pose_elbows: 'windows',
        pose_travel: 'travel spans', pose_wrists: 'pose intervals'};
      setAttributeText($("planning-progress"), 'aria-label',
        pose ? 'Hand orientation progress' : 'Touchpoint planning progress');
      setText($("planning-label"), (labels[planning.phase] || planning.phase)
        + (known ? ' · ' + percent.toFixed(1) + '%'
          + (planning.phase === 'pose_done' ? '' : ' · ' + planning.completed.toLocaleString() + ' / '
            + planning.total.toLocaleString() + ' ' + (units[planning.phase] || 'items')) : '')
        + (retryLabel ? ' · ' + retryLabel : ''));
      if (known) $("planning-progress").value = percent;
      else $("planning-progress").removeAttribute('value');
      const elapsed = Math.max(0, Math.floor((planning.elapsed_s || 0)
        + Math.max(0, Date.now()/1000 - planning.updated_at)));
      const details = [Math.floor(elapsed/60) + ':' + String(elapsed%60).padStart(2, '0') + ' elapsed'];
      if (planning.chart_ms != null) details.push('chart ' + (run.settings.start_s + planning.chart_ms/1000).toFixed(2) + ' s');
      if (planning.states != null) details.push(planning.states.toLocaleString() + ' alternatives retained');
      if (planning.phase === 'search') details.push('Progress counts processed items; remaining items can take different amounts of time.');
      setText($("planning-detail"), details.join(' · '));
    }
    const chart = bootstrap.charts[run.settings.chart];
    setText($("job-detail"), chart.title + " · " + run.settings.start_s + "–" + run.settings.end_s +
      " s. The previous take stays available while this renders.");
  } else if (run.id + run.status + (run.export_video || '') + (run.export_error || '') + (run.live?.scene || '') + (run.scene_error || '') + (run.video || '') + (run.ending_error || '') !== lastTerminal) {
    lastTerminal = run.id + run.status + (run.export_video || '') + (run.export_error || '') + (run.live?.scene || '') + (run.scene_error || '') + (run.video || '') + (run.ending_error || '');
    if (run.status === "complete") {
      if (selected?.id === run.id && run.live?.scene !== selected.live?.scene) {
        showTake(run);
      } else if (selected?.id === run.id && (run.export_video || run.export_error)) {
        selected = run;
        updateExportControls();
      } else if (!selected || run.created >= selected.created) showTake(run);
      if (run.ending_error) showError(run.ending_error);
      else if (run.scene_error) showError('The scene update stopped: ' + run.scene_error + ' The previous scene remains available.');
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
  setText($("preview-description"), run.settings.start_s + "–" + run.settings.end_s +
    " s · " + (run.live ? "live 3D · ArcCreate touch replay" : "saved MP4 · original autoplay"));
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
    setText($("arc-tick-summary"), check.confirmed_hits + ' / ' + check.predicted_hits + ' planned arc catches confirmed by native replay'
      + (check.unconfirmed ? ' · ' + check.unconfirmed + ' unconfirmed; see report.' : '.')
      + (check.incidental_hits ? ' Also caught ' + check.incidental_hits + ' additional arc ticks.' : ''));
    $("arc-tick-report").href=check.report;
  }
  if (run.judgements) {
    const j = run.judgements;
    $('hit-audio-report').hidden = !run.audio_stems?.hits;
    if (run.audio_stems?.hits) {
      $('hit-audio-download').href = run.audio_stems.hits;
      setText($('hit-audio-counts'), `${run.sound_events.block_hits} block clicks · ${run.sound_events.arc_tick_hits} snake tick clicks from actual catches.`);
    }
    setText($("judgement-summary"), j.hits + ' hits · ' + j.misses + ' misses · score ' + j.score.toLocaleString()
      + ((j.red_hits || j.red_misses) ? ` · red: ${j.red_hits || 0} caught / ${j.red_misses || 0} missed (no score)` : '')
      + (run.planning?.arc_reacquisitions ? ' · ' + run.planning.arc_reacquisitions + ' planned arc reacquisitions' : ''));
    $("skipped-targets").replaceChildren();
    for (const miss of run.planned_misses || []) {
      const li = document.createElement('li'), button = document.createElement('button');
      button.type = 'button';
      setText(button, (run.settings.start_s + miss.start_ms / 1000).toFixed(2) + ' s · ' +
        (miss.kind === 'red_sky' ? 'red sky note · no score' : (miss.kind === 'arc_tick' ? 'arc tick' : miss.kind) + ' · ' + miss.scoring_ticks + ' scoring tick' + (miss.scoring_ticks === 1 ? '' : 's')));
      setAttributeText(button, 'title', miss.reason || '');
      button.addEventListener('click', () => { video.currentTime = Math.max(0, miss.start_ms / 1000 - 1); $("preview").scrollIntoView({block:'start'}); });
      li.append(button); $("skipped-targets").append(li);
    }
    if (!run.planned_misses?.length) { const li = document.createElement('li'); setText(li, 'No targets deliberately skipped by the planner.'); $("skipped-targets").append(li); }
  }
  video.playbackRate = Number($("playback-speed").value);
  $("empty").hidden = true;
  $("analysis").hidden = false;
  const metrics = run.metrics;
  const hands = Object.values(metrics.hands);
  setText($("peak-speed"), Math.max(...hands.map(hand => hand.peak_surface_bound_m_s)).toFixed(2) + " m/s");
  setText($("speed-limit"), "Limit " + metrics.max_speed_m_s.toFixed(2) + " m/s");
  setText($("peak-turn"), Math.max(...hands.map(hand => hand.peak_angular_deg_s)).toFixed(0) + "°/s");
  setText($("turn-limit"), "Limit " + metrics.angular_limit_deg_s + "°/s");
  const collisionsOff = metrics.collision_verification === false;
  const fitted = metrics.contact_preserving_pose_solver;
  setText($("gap-label"), fitted ? "Closest glove gap" : collisionsOff ? "Glove collision check" : "Closest hand gap");
  setText($("gap"), fitted || !collisionsOff ? metrics.minimum_clearance_mm.toFixed(1) + " mm" : "Disabled");
  setText($("gap-note"), fitted ? (metrics.minimum_clearance_mm < 0 ? "Residual overlap · playback continues" : "Contact pivots · quarter-step samples") : collisionsOff ? "Touch points preserved" : "Sampled at quarter steps");
  setText($("contacts"), metrics.contacts);
  $("blend-link").href = run.blend;
  $("video-link").href = run.video;
  setText($("video-link"), 'Download gameplay only');
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
  setText($("axis-limit"), metrics.max_speed_m_s.toFixed(1) + " m/s");
  setText($("axis-end"), metrics.duration_s.toFixed(1) + " s");
  $("playhead").setAttribute("x1", 35);
  $("playhead").setAttribute("x2", 35);
  markDirty();
  lastHistory = "";
}

const cardSettings = [
  ["Planning and timing", [
    ["search_width", "Search width"], ["tap_interval_ms", "Minimum tap interval", "ms"],
    ["repeat_comfort_ms", "Comfortable repeat gap", "ms"], ["repeat_weight", "Fast-repeat cost"],
    ["jack_gap_ms", "Fast-run gap", "ms"], ["jack_free", "Unpenalized taps per run"], ["jack_weight", "Extra cost per tap"],
    ["point_contact_frames", "Tap duration", "frames"], ["min_air_frames", "Minimum release gap", "frames"],
    ["held_release_air_frames", "Release gap after a hold or arc", "frames"],
    ["contact_offset_x_percent", "Horizontal offset limit", "%"], ["contact_offset_y_percent", "Vertical offset limit", "%"],
    ["note_center_weight", "Note center cost"], ["note_center_free_mm", "Free center radius", "mm"],
    ["allow_misses", "Allow misses"], ["enable_red_notes", "Enable red notes"], ["target_all_red_notes", "Target every red note"],
  ]],
  ["Arcs and holds", [
    ["arc_path_mode", "Arc path mode"], ["prefer_arc_trace", "Prefer tracing arc geometry"], ["arc_trace_weight", "Geometry deviation cost"],
    ["full_arc_hold", "Prefer holding arcs to the visual end"], ["partial_hold_weight", "Early arc-release cost"],
    ["full_ground_hold", "Prefer holding ground holds to the visual end"], ["partial_ground_hold_weight", "Early ground-release cost"],
  ]],
  ["Motion and playback", [
    ["max_speed_m_s", "Max move speed", "m/s"], ["idle_speed_m_s", "Idle / detour speed", "m/s"],
    ["comfort_speed_m_s", "Comfort speed", "m/s"], ["planner_accel_m_s2", "Acceleration preference", "m/s²"],
    ["crossing_weight", "Crossing cost per second"], ["angular_limit_deg_s", "Max wrist turn", "°/s"],
    ["lift_mm", "Finger lift", "mm"], ["clearance_mm", "Preferred hand clearance", "mm"], ["allow_clipping", "Allow 3D clipping"],
    ["scroll_speed", "Chart scroll speed", "×"], ["song_delay_ms", "Song audio delay", "ms"], ["hit_sounds", "Hit sounds"],
    ["quality", "Preview quality"], ["camera_elevation_deg", "View angle", "°"],
    ["camera_yaw_deg", "Horizontal angle", "°"], ["camera_distance_m", "Distance", "m"], ["camera_lens_mm", "Lens", "mm"],
  ]],
];
const cardValues = {score: "Score-aware", trace: "Trace", draft: "Draft", final: "High"};

function cardText(tag, value, className) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  setText(element, value);
  return element;
}

function runCard(run, listName, status, action) {
  const saved = run.savedSettings || run.settings;
  const card = document.createElement("details");
  card.className = "take-card" + (run.id === selected?.id ? " selected" : "");
  card.name = listName;
  card.dataset.runId = run.id;
  const summary = document.createElement("summary");
  summary.className = "take";
  const info = document.createElement("span");
  info.className = "take-info";
  const title = document.createElement("strong");
  title.textContent = bootstrap.charts[saved.chart]?.title || saved.chart;
  const quick = [saved.start_s + "–" + saved.end_s + " s"];
  if (saved.search_width != null) quick.push("Width " + saved.search_width);
  if (saved.tap_interval_ms != null) quick.push("Tap ≥ " + saved.tap_interval_ms + " ms");
  if (saved.max_speed_m_s != null) quick.push(saved.max_speed_m_s + " m/s");
  info.append(title, cardText("small", quick.join(" · ")));
  const meta = document.createElement("span");
  meta.className = "take-meta";
  if (status) meta.append(cardText("span", status, "take-status"));
  else meta.append(cardText("time", new Date(run.created * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  }), "take-time"));
  const chevron = document.createElement("span");
  chevron.className = "take-chevron";
  chevron.setAttribute("aria-hidden", "true");
  summary.append(info, meta, chevron);
  card.append(summary);

  const details = document.createElement("div");
  details.className = "take-details";
  const toolbar = document.createElement("div");
  toolbar.className = "take-actions";
  if (action) toolbar.append(action);
  if (run.judgements) toolbar.append(cardText("span",
    run.judgements.hits + " hits · " + run.judgements.misses + " misses", "take-time"));
  if (status) {
    const formatTime = seconds => new Date(seconds * 1000).toLocaleTimeString([], {
      hour: "2-digit", minute: "2-digit",
    });
    toolbar.append(cardText("time", "Added " + formatTime(run.created), "take-time"));
    if (run.started) toolbar.append(cardText("time", "Started " + formatTime(run.started), "take-time"));
  }
  if (run.source_revision || run.metrics?.solver) {
    toolbar.append(cardText("span", run.source_revision || run.metrics.solver, "take-id"));
  }
  toolbar.append(cardText("span", run.id, "take-id"));
  details.append(toolbar);
  if (run.status === "running") details.append($("job"));
  const groups = document.createElement("div");
  groups.className = "take-settings";
  for (const [heading, settings] of cardSettings) {
    const section = document.createElement("div");
    const values = document.createElement("dl");
    for (const [key, label, unit] of settings) {
      const value = saved[key];
      if (value == null) continue;
      values.append(cardText("dt", label), cardText("dd",
        typeof value === "boolean" ? (value ? "On" : "Off") : unit ? value + " " + unit : cardValues[value] ?? value));
    }
    if (values.childElementCount) section.append(cardText("h3", heading), values);
    if (section.childElementCount) groups.append(section);
  }
  details.append(groups);
  card.append(details);
  return card;
}

function replaceCards(container, cards, defaultOpen) {
  const open = container.querySelector(".take-card[open]")?.dataset.runId;
  const focus = container.contains(document.activeElement) ? document.activeElement.closest(".take-card")?.dataset.runId : null;
  container.replaceChildren(...cards);
  const expanded = cards.find(card => card.dataset.runId === open) ||
    (defaultOpen ? cards.find(card => card.dataset.runId === defaultOpen) : null);
  if (expanded) expanded.open = true;
  if (focus) cards.find(card => card.dataset.runId === focus)?.querySelector("summary")?.focus();
}

function history(runs) {
  const takes = runs.filter(run => run.status === "complete" || run.status === "failed");
  const identity = JSON.stringify(takes.map(run => [run.id, run.status])) + selected?.id;
  if (identity === lastHistory) return;
  lastHistory = identity;
  const container = $("history");
  const savedCount = takes.filter(run => run.status === "complete").length;
  setText($("take-count"), savedCount ? savedCount + " saved locally" : "Saved locally");
  const cards = takes.slice(0, 8).map(run => {
    if (run.status === "failed") {
      const card = runCard(run, "recent-takes", "Generation failed");
      const details = card.querySelector(".take-details");
      if (run.error) details.prepend(cardText("p", run.error, "hint"));
      if (run.log) {
        const log = cardText("a", "View build log");
        log.href = run.log;
        log.target = "_blank";
        log.rel = "noopener";
        details.querySelector(".take-actions").prepend(log);
      }
      return card;
    }
    const play = cardText("button", "Open replay", "take-action");
    play.type = "button";
    play.disabled = !!recorder?.active;
    play.addEventListener("click", () => {
      showTake(run, true);
      history(runs);
    });
    return runCard(run, "recent-takes", null, play);
  });
  replaceCards(container, cards);
  if (!cards.length) container.append(cardText("p", "Completed previews will appear here for comparison.", "hint"));
}

function generationQueue(state) {
  const waiting = state.queue || [];
  const active = state.active?.status === "running" ? state.active : null;
  const identity = JSON.stringify([active?.id, active?.kind, waiting.map(run => run.id)]);
  const container = $("queue");
  if (identity !== lastQueue) {
    const previousActive = container.querySelector(".take-card[data-active]")?.dataset.runId;
    lastQueue = identity;
    const job = $("job");
    if (!active) $("generation").append(job);
    const cards = waiting.map((run, index) => {
      const remove = cardText("button", "Remove from queue", "take-action");
      remove.type = "button";
      remove.addEventListener("click", () => cancelRun(run.id, remove, true));
      return runCard(run, "generation-jobs", "Waiting · Position " + (index + 1), remove);
    });
    if (active) {
      const card = runCard(active, "generation-jobs", active.stage);
      card.dataset.active = "";
      cards.unshift(card);
    }
    replaceCards(container, cards, active?.id !== previousActive && active?.id);
    if (!cards.length) container.append(cardText("p", "No generation tasks queued.", "hint"));
  }
  setText($("queue-count"), waiting.length + " waiting" + (active ? " · 1 running" : ""));
  if (active) {
    const status = container.querySelector(".take-card .take-status");
    setText(status, active.stage + " · " + Math.round(active.progress || 0) + "%");
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
    generationQueue(state);
    history(state.history.map(normalizeRun));
    schedulePoll(state.active?.status === "running" ? 1000 : 3000);
  } catch (error) {
    setText(document.querySelector(".connection"), "Renderer disconnected");
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
      // Apply newly requested preferences once when migrating older takes.
      // Explicit saved-take selection still restores its historical settings.
      if (latest.defaults_revision !== bootstrap.defaults_revision) fill({ ...latest.settings,
        ...Object.fromEntries(bootstrap.startup_preferences.map(name => [name, bootstrap.defaults[name]])) });
    }
    displayJob(normalizeRun(bootstrap.active));
    generationQueue(bootstrap);
    history(bootstrap.history);
    schedulePoll(1000);
  } catch (error) {
    showError(error.message);
  }
}
start();


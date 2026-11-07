// Record the actual WebGL canvas and the gameplay element's audio. No screen or
// microphone capture, alternate renderer, contact planning or ArcCreate run.
export function recordingFormat(Recorder = globalThis.MediaRecorder) {
  if (!Recorder) return null;
  return ['video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4',
    'video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm']
    .find(type => Recorder.isTypeSupported(type)) || null;
}

function mediaReady(video, type, action, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); video.removeEventListener(type, done); video.removeEventListener('error', failed); signal.removeEventListener('abort', aborted); };
    const done = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error('The gameplay video could not be decoded.')); };
    const aborted = () => { cleanup(); reject(new Error('Export cancelled.')); };
    const timer = setTimeout(() => { cleanup(); reject(new Error('Timed out waiting for the gameplay video.')); }, 20000);
    video.addEventListener(type, done, {once:true}); video.addEventListener('error', failed, {once:true});
    signal.addEventListener('abort', aborted, {once:true});
    if (signal.aborted) aborted(); else action();
  });
}

export class PreviewRecorder {
  constructor(preview, video, changed) {
    Object.assign(this, {preview, video, changed, active:false});
  }

  supported() {
    return !!(recordingFormat() && this.preview?.canvas.captureStream && (globalThis.AudioContext || globalThis.webkitAudioContext));
  }

  async resumeAudio() { if (this.audio) await this.audio.context.resume(); }

  audioGraph() {
    if (!this.audio) {
      const context = new (globalThis.AudioContext || globalThis.webkitAudioContext)();
      const source = context.createMediaElementSource(this.video);
      const destination = context.createMediaStreamDestination();
      source.connect(context.destination); // Keep ordinary preview sound working.
      source.connect(destination);
      this.audio = {context, source, destination};
    }
    return this.audio;
  }

  cancel(message = 'Export cancelled.') {
    this.cancelReason = message;
    this.abort?.abort();
    if (this.recorder?.state !== 'inactive') this.recorder?.stop();
  }

  async record() {
    if (this.active) throw new Error('A preview export is already running.');
    if (!this.supported()) throw new Error('This browser cannot record the preview with audio. Use the Blender MP4 export below.');
    const {video, preview} = this;
    if (!preview.enabled || !Number.isFinite(video.duration) || video.duration <= 0) throw new Error('Wait for the live preview to finish loading.');
    const began = performance.now();
    const saved = {time:video.currentTime, rate:video.playbackRate, paused:video.paused, muted:video.muted, volume:video.volume};
    this.active = true; this.cancelReason = null; this.abort = new AbortController();
    const signal = this.abort.signal;
    let stream, watchdog, wakeLock, onVisibility, onProgress, end;
    this.changed({active:true, progress:0, message:'Preparing the preview…'});
    try {
      // Create/resume inside the user's click gesture, before waiting for seeks.
      const audio = this.audioGraph(); await audio.context.resume();
      video.pause(); video.playbackRate = 1; video.muted = false; video.volume = 1;
      if (video.readyState < 2) await mediaReady(video, 'loadeddata', () => video.load(), signal);
      if (video.currentTime !== 0) await mediaReady(video, 'seeked', () => { video.currentTime = 0; }, signal);
      if (signal.aborted) throw new Error(this.cancelReason);
      preview.beginCapture(); // Lock encoded dimensions even if the phone rotates.
      const canvasStream = preview.canvas.captureStream(30);
      stream = new MediaStream([...canvasStream.getVideoTracks(), ...audio.destination.stream.getAudioTracks().map(track => track.clone())]);
      const mimeType = recordingFormat();
      const recorder = this.recorder = new MediaRecorder(stream, {mimeType,
        videoBitsPerSecond:Math.max(2000000, Math.min(12000000, preview.canvas.width*preview.canvas.height*30*.18)), audioBitsPerSecond:192000});
      const chunks = [];
      let failure;
      const stopped = new Promise((resolve, reject) => {
        recorder.addEventListener('dataavailable', e => { if (e.data.size) chunks.push(e.data); });
        recorder.addEventListener('error', e => { failure = e.error || new Error('The browser video encoder stopped.'); reject(failure); }, {once:true});
        recorder.addEventListener('stop', resolve, {once:true});
      });
      // Attach a rejection handler immediately, including while play() is pending.
      stopped.catch(() => {});
      end = () => { preview.renderAt(video.currentTime); if (recorder.state !== 'inactive') recorder.stop(); };
      video.addEventListener('ended', end, {once:true});
      signal.addEventListener('abort', end, {once:true});
      onVisibility = () => { if (document.hidden) this.cancel('Export stopped because the page was hidden. Keep it visible and try again.'); };
      document.addEventListener('visibilitychange', onVisibility);
      onProgress = () => this.changed({active:true, progress:Math.min(99,100*video.currentTime/video.duration), message:`Recording preview · ${Math.floor(video.currentTime)} / ${Math.ceil(video.duration)} s`});
      video.addEventListener('timeupdate', onProgress);
      let previousTime = video.currentTime, advancedAt = performance.now();
      watchdog = setInterval(() => {
        if (video.currentTime !== previousTime) { previousTime = video.currentTime; advancedAt = performance.now(); }
        else if (performance.now()-advancedAt > 15000) this.cancel('Playback stalled during export. Try again after the video finishes buffering.');
      }, 1000);
      try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* A visible tab still works without a wake lock. */ }
      if (signal.aborted) throw new Error(this.cancelReason);
      recorder.start(250);
      preview.renderAt(0);
      await video.play();
      await stopped;
      video.removeEventListener('ended', end); signal.removeEventListener('abort', end);
      if (failure) throw failure;
      if (signal.aborted) throw new Error(this.cancelReason);
      const type = recorder.mimeType || mimeType;
      const blob = new Blob(chunks, {type});
      if (!blob.size) throw new Error('The browser returned an empty recording.');
      const result = {blob, extension:type.startsWith('video/mp4') ? 'mp4' : 'webm', mimeType:type,
        width:preview.canvas.width, height:preview.canvas.height, duration:video.duration, wallSeconds:(performance.now()-began)/1000};
      this.changed({active:false, progress:100, message:'Preview video ready to download.'});
      return result;
    } finally {
      clearInterval(watchdog);
      if (end) { video.removeEventListener('ended', end); signal.removeEventListener('abort', end); }
      if (onVisibility) document.removeEventListener('visibilitychange', onVisibility);
      if (onProgress) video.removeEventListener('timeupdate', onProgress);
      if (this.recorder?.state !== 'inactive') this.recorder?.stop();
      stream?.getTracks().forEach(track => track.stop());
      await wakeLock?.release().catch(() => {});
      this.recorder = null; this.active = false; preview.captureLock = false;
      video.pause(); video.playbackRate = saved.rate; video.muted = saved.muted; video.volume = saved.volume;
      video.currentTime = Math.min(saved.time, video.duration);
      preview.resize(); preview.needsRender = true;
      if (!saved.paused) video.play().catch(() => {});
    }
  }
}

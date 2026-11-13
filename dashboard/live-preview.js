import { setText } from './i18n.js';
import * as THREE from './vendor/three.module.js';
import { GLTFLoader } from './vendor/GLTFLoader.js';

// The asset and samples share Blender's exact Z-up -> glTF Y-up conversion.
// Video media time is the sole clock, including seek, pause and slow playback.
export class RhythmPreview {
  constructor(canvas, video, performanceLabel) {
    this.canvas = canvas;
    this.video = video;
    this.performanceLabel = performanceLabel;
    this.generation = 0;
    this.camera = new THREE.PerspectiveCamera(48, 4 / 3, .01, 100);
    this.nativeEuler = new THREE.Euler(0, 0, 0, 'ZYX');
    this.nativeQ = new THREE.Quaternion();
    this.settings = {};
    this.lastSecond = performance.now();
    this.frames = 0;
    this.frameTimes = [];
    this.needsRender = true;
    for (const event of ['seeked', 'loadeddata', 'playing']) video.addEventListener(event, () => { this.needsRender = true; });
    video.addEventListener('play', () => { this.lastSecond = performance.now(); this.frames = 0; this.frameTimes = []; this.previousRenderAt = null; });
    video.addEventListener('pause', () => { if (this.enabled) setText(this.performanceLabel, 'Live 3D · paused'); });
    this.renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: false});
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;
    this.renderer.setClearColor(0xb8b5ae);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.VSMShadowMap;
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  resize() {
    if (this.captureLock) return;
    const box = this.canvas.parentElement.getBoundingClientRect();
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, this.settings.quality === 'final' ? 2 : 1));
    this.renderer.setSize(Math.max(1, box.width), Math.max(1, box.height), false);
    this.camera.aspect = box.width / Math.max(1, box.height);
    this.setCamera(this.settings);
  }

  setCamera(settings) {
    this.needsRender = true;
    this.settings = settings;
    const elevation = THREE.MathUtils.degToRad(settings.camera_elevation_deg ?? 52);
    const yaw = THREE.MathUtils.degToRad(settings.camera_yaw_deg ?? 0);
    const distance = settings.camera_distance_m ?? .29;
    const target = [.405, .802 - .008, .708 + .020];
    const eye = [target[0] + distance * Math.cos(elevation) * Math.sin(yaw),
      target[1] - distance * Math.cos(elevation) * Math.cos(yaw), target[2] + distance * Math.sin(elevation)];
    this.camera.position.set(eye[0], eye[2], -eye[1]);
    this.camera.lookAt(target[0], target[2], -target[1]);
    this.camera.fov = THREE.MathUtils.radToDeg(2 * Math.atan(36 / (2 * (settings.camera_lens_mm ?? 30) * this.camera.aspect)));
    this.camera.updateProjectionMatrix();
  }

  disposeScene(scene = this.scene) {
    if (!scene) return;
    scene.traverse(object => {
      object.geometry?.dispose();
      object.shadow?.map?.dispose();
      object.shadow?.mapPass?.dispose();
      for (const material of [].concat(object.material || [])) {
        for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
        material.dispose();
      }
    });
    if (scene === this.scene) this.scene = null;
  }

  async load(run) {
    const generation = ++this.generation;
    this.enabled = false;
    this.canvas.hidden = true;
    if (!run.live) { this.disposeScene(); setText(this.performanceLabel, 'Saved 3D video · original autoplay'); return; }
    setText(this.performanceLabel, 'Loading live 3D…');
    const [gltf, response] = await Promise.all([
      new GLTFLoader().loadAsync(run.live.scene), fetch(run.live.motion)
    ]);
    if (!response.ok) throw new Error('Could not load verified hand motion.');
    const motion = await response.json();
    if (generation !== this.generation) { this.disposeScene(gltf.scene); return; }
    this.disposeScene();
    this.scene = gltf.scene;
    this.motion = motion;
    this.roots = {};
    let screen;
    this.scene.traverse(object => {
      const name = object.name.replaceAll('_', ' ');
      for (const side of ['left', 'right']) if (name.toLowerCase() === side + ' index target') this.roots[side] = object;
      if (object.isMesh && name.startsWith('Tablet display')) screen = object;
    });
    if (!screen || !this.roots.left || !this.roots.right) throw new Error('The exported scene is missing the tablet or hand pivots.');
    this.scene.updateMatrixWorld(true);
    const size = new THREE.Box3().setFromObject(screen).getSize(new THREE.Vector3());
    const error = Math.max(Math.abs(size.x - motion.screen_mm[0] / 1000), Math.abs(size.z - motion.screen_mm[1] / 1000));
    if (error > .00001) throw new Error('Tablet dimensions do not match the contact plan.');
    const texture = this.videoTexture = new THREE.VideoTexture(this.video);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.flipY = false; // glTF UVs already use the video image's top-left convention.
    screen.material = new THREE.MeshBasicMaterial({map: texture, toneMapped: false});
    // Same set geometry/material maps as Blender. Area lights are represented
    // by a broad hemispheric fill and a shadowed key in the realtime renderer.
    this.scene.traverse(object => {
      if (object.isLight) object.visible = false;
      if (object.isMesh) {
        object.castShadow = object !== screen;
        object.receiveShadow = object.name.startsWith('Set') || object.name.startsWith('Plant');
      }
    });
    this.scene.add(new THREE.HemisphereLight(0xf0f3f6, 0x8d8270, 1.8));
    const key = new THREE.DirectionalLight(0xffecd5, 1.9);
    key.position.set(.065, 1.258, -.642);
    key.target.position.set(.405,.708,-.802);
    key.castShadow = true;
    key.shadow.mapSize.set(1024,1024);
    Object.assign(key.shadow.camera,{left:-.30,right:.30,top:.30,bottom:-.30,near:.1,far:1.5});
    key.shadow.radius = 4;
    key.shadow.blurSamples = 8;
    key.shadow.bias = -.00015;
    key.shadow.normalBias = .00025;
    this.scene.add(key,key.target);
    this.settings = run.settings;
    this.enabled = true;
    this.canvas.hidden = false;
    this.resize();
    this.pose(this.video.currentTime);
    setText(this.performanceLabel, 'Live 3D');
    this.canvas.dataset.alignmentErrorM = String(error);
    this.canvas.dataset.sceneRevision = run.presentation?.revision || 'legacy';
    this.canvas.dataset.triangles = String(this.renderer.info.render.triangles);
  }

  pose(time) {
    if (!this.motion || !this.roots) return;
    const sample = Math.max(0, (time + this.motion.audio_offset_ms / 1000) * this.motion.sample_rate_hz);
    for (const side of ['left', 'right']) {
      const values = this.motion.hands[side];
      const a = Math.min(values.length - 1, Math.floor(sample));
      const b = Math.min(values.length - 1, a + 1);
      const t = Math.min(1, sample - a), before = values[a], after = values[b];
      const root = this.roots[side];
      root.position.set(...before.slice(0, 3).map((v, i) => THREE.MathUtils.lerp(v, after[i], t)));
      // Blender keys interpolate pitch/yaw linearly, not quaternion slerp.
      const pitch = THREE.MathUtils.lerp(before[7], after[7], t);
      const yaw = THREE.MathUtils.lerp(before[8], after[8], t);
      // Blender XYZ uses the multiplication order Three calls ZYX.
      this.nativeQ.setFromEuler(this.nativeEuler.set(-THREE.MathUtils.degToRad(pitch), 0, THREE.MathUtils.degToRad(yaw), 'ZYX'));
      root.quaternion.set(this.nativeQ.x, this.nativeQ.z, -this.nativeQ.y, this.nativeQ.w);
    }
    this.canvas.dataset.mediaTime = String(time);
  }

  beginCapture() {
    this.resize(); // Flush a pending ResizeObserver after a viewport change.
    this.captureLock = true;
    // H.264 encoders can crop odd pixel widths. Use a full 4:3 frame with
    // even dimensions, rendered by this same canvas/camera, then restore it.
    const width = Math.max(8, Math.floor(this.canvas.width / 8) * 8);
    this.renderer.setPixelRatio(1);
    this.renderer.setSize(width, width * 3 / 4, false);
  }

  renderAt(time) {
    this.pose(time);
    if (this.videoTexture) this.videoTexture.needsUpdate = true;
    this.renderer.render(this.scene, this.camera);
  }

  loop(now) {
    if (this.enabled && !document.hidden && this.video.readyState >= 2 &&
        (!this.video.paused || this.needsRender || this.lastMediaTime !== this.video.currentTime)) {
      if (!this.video.paused && this.previousRenderAt) this.frameTimes.push(now-this.previousRenderAt);
      if (this.frameTimes.length > 600) this.frameTimes.shift();
      this.previousRenderAt = this.video.paused ? null : now;
      this.pose(this.video.currentTime);
      this.renderer.render(this.scene, this.camera);
      this.canvas.dataset.triangles = String(this.renderer.info.render.triangles);
      this.canvas.dataset.drawCalls = String(this.renderer.info.render.calls);
      this.needsRender = false;
      this.lastMediaTime = this.video.currentTime;
      if (!this.video.paused) this.frames++;
      if (now - this.lastSecond >= 1000) {
        if (this.frameTimes.length) {
          const sorted = [...this.frameTimes].sort((a,b) => a-b);
          this.canvas.dataset.movingFps = String(1000*this.frameTimes.length/this.frameTimes.reduce((a,b)=>a+b,0));
          this.canvas.dataset.frameP95Ms = String(sorted[Math.floor(sorted.length*.95)]);
        }
        setText(this.performanceLabel, this.video.paused ? 'Live 3D · paused' :
          'Live 3D · ' + Math.round(this.frames * 1000 / (now - this.lastSecond)) + ' fps');
        this.frames = 0; this.lastSecond = now;
      }
    }
    requestAnimationFrame(this.loop);
  }
}

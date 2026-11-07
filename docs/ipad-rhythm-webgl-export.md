# WebGL preview video export

The primary **Export preview video** action records exactly the existing Three renderer's scene, camera, materials, lights and hand animation. It captures the game's existing audio through a persistent Web Audio media-element source, adds that audio track to the canvas stream, and encodes with MediaRecorder. No Blender frames, screen capture, microphone, contact search or ArcCreate rendering are involved.

The browser prefers supported MP4 codecs, then falls back to WebM. See the platform references for [canvas capture](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/captureStream), [media-element audio routing](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/createMediaElementSource) and [runtime codec detection](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static).

Recording starts at the displayed take's beginning and runs at 1× regardless of inspection speed. It takes approximately the duration of the clip. Current canvas detail controls resolution. Capture uses the same canvas in an even 4:3 size, then restores its regular responsive resolution. UI inputs and take changes are locked/deferred during recording; the original time, speed, muted/volume values and paused/playing state are restored. Repeated exports reuse the audio graph. Cancellation or a hidden/stalled page discards the incomplete capture and keeps a previous download available. Finished videos must be downloaded before closing/reloading the page.

The expandable Blender export retains its original API and saved-take MP4 behavior. It remains useful for browsers without recording support or offline EEVEE output; it is not the primary matching-view export.

## Verification

On the Apple M2 Max development Mac, the 65–75 s saved take was recorded and downloaded through the actual dashboard. The first desktop file contained H.264 video at 30 fps and AAC audio. Audio waveform correlation against the source gameplay was 0.986 with approximately 53 ms of common capture startup delay. The final aligned desktop export produced a full 856×642 H.264/AAC file in 10.83 s, with 0.984 audio correlation and about 49 ms common startup delay. The visual frame was inspected and retains the live presentation and native gameplay. Real-time capture is not a frame-exact offline encoder; foreground render/encoder latency and occasional frame pacing variation remain device dependent.

An odd 859 px canvas was initially encoded at 858 px by H.264. The final implementation renders a complete even 4:3 canvas before capture, rather than accepting that hidden crop. The phone-sized 375×812 check produced a 320×240 H.264/AAC MP4 in **10.83 s** for a **10.77 s** source clip. Cancellation preserved the previous MP4 URL and restored 5.65 s, paused, at the original 0.5× speed. Desktop recording and resized/mobile recording both use the same source and controller. This is browser testing on the Mac, not a physical iPhone/Safari benchmark.

Evidence is under `output/webgl-export-qa/`: downloaded desktop/mobile MP4s, exported frames, audio-check JSON and dashboard screenshots. The controller's Node regression test covers successful recording, audio graph reuse, cancellation and restoration of preview state. Existing independent Blender/WebGL alignment checks remain valid because the pose mapping and scene are unchanged.

```sh
cd /path/to/ipad-rhythm
node tests/test_preview_recorder.mjs
node tests/check_webgl_alignment.mjs output/runs/20261107-000550-9a4498
```

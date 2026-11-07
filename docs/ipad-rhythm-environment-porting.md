# iPad rhythm simulator: environment porting guide

This is the working guide for moving the simulator into another set or renderer. Update the experience log after each port. The first presentation port replaces the dafeiyu room with a small walnut tabletop set; the room project remains separate.

## Ownership and build paths

This folder is the independent simulator repository root.

| Source/artifact | Responsibility |
| --- | --- |
| `plan_excerpt.py`, `plan.json` | Native touch coordinates, ownership and timing. Environment changes must not replan them. |
| `hand_pose.py`, `motion_paths.py`, `motion.json` | Glove shape/posing and sampled motion. Keep these intact for a presentation change. |
| `animate.py` | Builds a new take and bakes the supplied animation. Starts from Blender factory startup, without the room blend. |
| `presentation_scene.py` | **One shared source of presentation geometry**: rounded tablet, tabletop, mat, plant, pen, image materials and Blender lights. |
| `refresh_scene.py` | Upgrades an existing baked take to the current set. Reuses its hand meshes, animation, cameras and movie material. |
| `dashboard/live-preview.js` | Loads the generated GLB and exact motion; substitutes a video material on the named display. Supplies realtime lighting. Do not reconstruct props here. |
| `export_camera.py`, `render-preview.sh` | Copy the take's selected Blender snapshot, apply current camera/detail, render EEVEE frames, mux the existing gameplay audio. |
| `output/runs/<id>/run.json` | Selected scene snapshot, motion URL, presentation revision and last MP4. This metadata, not the newest loose file, controls a saved take. |

Original room assets, including `outputs/whale-room-final.blend`, are not modified or loaded by new simulator builds. A saved old take retains its original `scene.blend` and `scene.glb`; a refresh publishes versioned `presentation-<revision>.blend/.glb` alongside them. Old exports remain on disk, but the stale dashboard download is cleared only after a successful scene update.

## Stable scene contract

Units are metres, Blender is Z-up. glTF/Three is Y-up: `(x,y,z) → (x,z,-y)`. Apply the equivalent quaternion conversion too; do not move the screen to compensate for a conversion error.

- Display world centre: **(0.405, 0.802, 0.708)**. The display is horizontal, with local X/Y equal to native touch X/Y.
- Current active area: **0.2297 × 0.1605 m**. Read `plan.device.screen_mm` for another device; avoid a second hardcoded size in a renderer.
- Display name: **`Tablet display | ArcCreate`**. The preview accepts its glTF name with spaces normalized from underscores.
- Animated roots: **`Left index target`**, **`Right index target`**. Each index pad centre is the pivot. Keep the hand children, mesh shape and local transforms.
- Retain **`Tablet chassis`**, **`Tablet bezel`**, **`Camera | Tablet play`** and the prototype collection **`90 Tablet rhythm prototype`**.
- Screen UVs are spatially affine: `u=x/width+0.5`, `v=y/height+0.5`. A rounded outline adds perimeter vertices and centre triangles without squeezing the image into the visible polygon. glTF already converts UV conventions; the Three `VideoTexture` uses `flipY=false`.
- Media time is the sole live animation clock. Sample index is `(mediaTime + audio_offset_ms/1000)*60`. Match Blender's **linear pitch/yaw interpolation**, rather than replacing it with quaternion slerp.
- Keep the camera target **(0.405, 0.794, 0.728)**, distance/elevation/yaw and the 36 mm sensor/lens projection shared with `export_camera.py`. Live canvas and export use a 4:3 image. Moving props is the solution to framing, not an unrequested camera change.

### Parenting: inspect before deleting

The old `Desk` was itself parented to `Room`. The tablet and hand roots were children of `Desk`, with a parent-inverse matrix. Deleting the desk first would invalidate the pose interface.

1. Evaluate the scene, record world matrices, parents and animation data.
2. Preserve the prototype collection and all descendants. Detach roots with `world = obj.matrix_world.copy(); obj.parent=None; obj.matrix_world=world`.
3. For animated roots, inspect `parent.matrix_world @ matrix_parent_inverse`. In these snapshots that basis is identity, so the existing location/Euler keys already describe world coordinates and remain valid. `detach()` rejects an unexpected nonidentity animated basis. A different environment must bake sampled world transforms before removing such a parent.
4. Remove only surroundings in the generated snapshot. Never edit the room asset to isolate a presentation.
5. Compare all retained world matrices and run the baked-animation alignment checks. Checking only frame 1 is insufficient.

## Rounded tablet geometry

A cube bevel cannot produce a 12 mm footprint radius on a 10 mm thick chassis: thickness constrains the bevel. The shared builder extrudes an actual rounded-rectangle perimeter, with a separate small edge glint bevel. Chassis radius is 12 mm; bezel radius 11 mm. The display radius is 3 mm, independent of thickness, with unchanged width/height, centre and affine UV mapping.

The display mask removes about **0.021%** of the rectangular image, at the four extreme corners. Validate native contact samples against the rounded outline and inspect the score UI/corners in real footage. Do not shrink the active image or reproject touches to hide a masking problem.

## Materials and lighting across renderers

Use glTF-supported Principled base color, metallic, roughness and normal inputs. Blender Noise/Voronoi nodes are not an export format. The walnut material generates deterministic color and tangent-normal PNGs, connects image nodes to Principled inputs, and packs both into the blend and GLB. Asset directories include the presentation revision to avoid silently reusing an older texture after a generator change. There are no external downloads or room texture dependencies.

Blender's area lights do not have a punctual glTF equivalent. The live preview deliberately replaces lights with a hemispheric fill and a shadowed warm directional key. It uses ACES tone mapping; Blender uses AgX. Material identity and geometry match, while highlight/soft-shadow response is approximate. Inspect both paths; a pleasing Blender still does not establish that WebGL is correct.

Realtime shadows use a 1024² VSM map with a small blur. Keep the shadow camera tight around the set and use a small normal bias to avoid contact acne. The video screen remains an unlit, untone-mapped video material in WebGL; avoid lighting/shadow changes that recolor gameplay.

## Refreshing saved takes and invalidating caches

Normal dashboard flow: choose a completed live take → **Update presentation** → inspect → **Export preview video** → **Download MP4/WebM** (or expand Blender MP4 export for EEVEE). All new builds use the new set automatically.

`POST /api/presentation {"id":"<take-id>"}` runs Blender once against the take's selected snapshot. It never invokes the contact planner or ArcCreate. It publishes the new scene paths only after successful generation, clears `export_video`/`export_view`, and leaves prior usable artifacts on cancellation/failure. Scene errors are separate from contact/score validation.

The presentation revision is the shared builder's SHA-256 prefix. GLB filenames are versioned so browser caches and saved-take references cannot mistake the new set for the old room. MP4 cache identities include both the current view settings and the selected presentation revision; export opens `run.presentation.blend`, falling back to `scene.blend` for an unrefreshed take. Do not assume replacing one GLB also updates a saved Blender snapshot or MP4.

When changing the environment again, check the refreshed metadata and actual exported frame. Keep a hash manifest of `plan.json`, `motion.json`, `gameplay.mp4` and `judgements.json` before/after. Geometry-only work must preserve those bytes.

## Verification recipe

From the simulator repository root, substitute the real take folder and versioned blend:

```sh
BLENDER="${BLENDER_BIN:-.tools/Blender.app/Contents/MacOS/Blender}"
TAKE=output/runs/20261107-000550-9a4498
SCENE="$TAKE/presentation-50aa44fcc9c4.blend"
"$BLENDER" --factory-startup -b "$SCENE" --python-exit-code 1 -P tests/check_presentation.py -- "$TAKE"
"$BLENDER" --factory-startup -b "$SCENE" --python-exit-code 1 -P tests/export_alignment_reference.py -- "$TAKE"
node tests/check_webgl_alignment.mjs "$TAKE"
"$BLENDER" --factory-startup -b --python-exit-code 1 -P tests/check_preserved_scene.py -- "$TAKE"
.venv/bin/python -m pytest tests/test_dashboard_export.py tests/test_presentation_workflow.py -q
```

The original alignment tests remain unchanged. The presentation audit checks display bounds/UVs, native contact corner masking, removal of the room/desk, detached roots, tablet/prop triangle intersections, and actual glove/cuff meshes versus the props over the animation at 60 Hz. It also checks clearance above the pad. These are finite checks on the inspected takes; future charts/changed hand shapes need their own envelope audit. They are not a universal proof against every possible future pose.

Inspect the live browser at the default camera, close cross-hand poses, elevated and left/right views, a wide lens, and a phone-sized viewport. At aggressive zoom/oblique settings edge props may be cropped; retain the user's camera rather than silently reframing. Verify no prop obstructs the display. Check real video frames as well as stream metadata/audio. Do not substitute an autoplay-only screenshot for live WebGL evidence.

For moving performance, record the host, viewport, quality, canvas dimensions, GLB bytes, moving FPS and p95 frame interval. The canvas exposes these measurements as `data-*` attributes. Measure while video/motion play; an idle renderer's redraw rate is meaningless. Phone-sized desktop testing does not establish physical-phone GPU performance.

## Deployment and environment differences

- On this Mac, Blender 5.2.3 LTS is selected explicitly by `BLENDER_BIN` in the launchd service, and the Python environment is the prototype's `.venv`. Check APIs in the installed runtime rather than assuming another Blender version's EEVEE/animation behavior.
- `dashboard/server.py` currently resolves the workspace Blender path; `render-preview.sh` accepts `BLENDER_BIN`. For another OS, centralize/override the server binary path too. Keep camera/projection and ffmpeg mux settings consistent.
- Native ArcCreate is only necessary to produce new gameplay; a presentation port needs the completed take's files and Blender/Node/ffmpeg.
- Server is supervised by the existing `com.dafeiyu.rhythm-workbench` launchd service and bound to loopback:8765; Tailscale Serve provides the phone URL. Inspect `/api/state` before restarting. Never restart a live planner/export merely to load UI changes. During this port the user's earlier build was already cancelled through the dashboard before the service was reloaded.
- Preserve exact case-sensitive names, path portability, relative movie paths, packed material images, 30 fps export, and 60 Hz motion sampling when moving to Linux/another engine. Test HTTP byte-range downloads and mobile video seeking. Linux/headless Blender may need an explicit graphics backend; verify actual rendering instead of inferring GPU support from a config flag.
- Keep old room files and prior snapshots. Future presentation ports should change the shared set builder and refresh scenes, not fork the renderer's prop implementation.

## Experience log — 2026-11-07

- Inspected the real parent hierarchy before detaching. Retained interface transforms differed by at most 5.96e-8 in the first old-room migration; repeat presentation updates were exact at the matrix comparison tolerance.
- Replaced thickness-limited beveled boxes with rounded perimeter meshes; affine display UVs and contact mapping stayed intact.
- A plant scaling pass initially translated meshes already authored in world coordinates a second time. Live and Blender inspection caught the floating/cropped foliage. Fixed local-versus-world scaling and reduced/repositioned the plant for the existing camera. Treat mesh coordinates and object transforms separately.
- Adjusted wood/light response in both engines after inspecting actual output. Shader-node success alone did not establish portable appearance.
- Reused BIBBIDIBA 65–75 s, including its closest proxy hand approach around media 9.317 s. Scene refresh preserved plan, motion, gameplay and judgement SHA-256 hashes. Existing 76/76 native arc-catch results remain valid.
- Initial final-scene audit: 637 sampled poses, 883 contact samples, zero clipped contacts, zero prop/tablet or prop/hand mesh intersections, at least 12.82 mm hand-to-pad-top clearance. Blender/Three: 77 subframe alignment checks; maximum position error 6.82e-8 m, rotation error 1.89e-7 rad, projected-coordinate error 3.69e-7.
- Mobile-sized live preview: 375×812 override (360 px layout with scrollbar), Draft canvas 324×243, about 120 fps/p95 9.2 ms on the development Mac (Apple M2 Max), while a real EEVEE video export was running. This is desktop GPU evidence, not a physical-phone benchmark. GLB about 4.5 MB; 67 draws / 145,206 reported triangles including shadow passes.
- Normal dashboard MP4 export succeeded with the new scene: H.264 640×480 at 30 fps, 10.633 s video and AAC audio. Inspected an ordinary catch and the close cross-hand frame. Evidence is in `output/presentation-qa/` and the saved take's `presentation-check.json`/`alignment-check.json`.

- Extended validation to the latest 120–150 s take: 1,837 sampled poses, 1,975 contact samples, zero prop intersections/clipped contacts, and 209 passing Blender/WebGL checks. Across both takes: 2,474 pose samples and 286 alignment checks. The original test suite plus workflow additions passes **142 tests**.
- Compared original and refreshed snapshots directly: hand mesh coordinates/topology and animation keys are byte-identical; retained matrices differ by at most 5.96e-8. Curve conversion now explicitly deselects other objects first so a prop conversion cannot accidentally apply a selected hand's modifiers.
- A factory-startup smoke build using the copied 65–75 s plan and existing movie completed without loading the room or rerunning the planner/ArcCreate. Its active contacts and wrist rotations match the saved motion. Recomputing motion from scratch changed one historical cached airborne lift by up to 4 mm; the saved-take refresh deliberately retains the original baked curves and motion bytes instead. This is why environment ports should use scene refresh, rather than the animation builder.

Final published presentation revision: `50aa44fcc9c4`. The verified downloadable video is `output/runs/20261107-000550-9a4498/preview-2e27af8e3808.mp4`; its stream audit is `output/presentation-qa/export-streams.json`. Live screenshots are `webgl-default.png`, `webgl-cross-hand.png`, and `mobile.png` in the QA folder. Tailscale byte-range downloads of the GLB and final MP4 returned HTTP 206.

## WebGL recording port — 2026-11-07

The primary dashboard export now captures the existing WebGL canvas via `captureStream(30)` and mixes the existing gameplay element through Web Audio into the same MediaRecorder stream. It does not construct another scene or rerender the gameplay. Export is real time at 1×, with the current live camera, materials and lights. The original preview state is restored afterward. Keep the page visible; hiding it cancels the export rather than delivering a throttled recording.

MP4 support is detected at runtime, with WebM fallback. A canvas width aligned to 8 pixels with a 4:3 even height avoids an observed H.264 encoder crop of an odd-width canvas. ResizeObserver updates are flushed before capture and then locked until cleanup. The renderer still uses its existing camera and source scene. Audio graphs are created once per media element and reused; only cloned recording tracks are stopped so subsequent playback/export keeps sound. No microphone or screen-sharing permission is needed.

Browser-local downloads expire when the page closes. The Blender export remains an explicit offline option with persistent server-side take artifacts. For another browser/OS, inspect a real file with ffprobe and listen/check the audio; feature detection alone does not establish correct encoding. Real-time capture can add short startup/encoder latency and depends on the foreground device's ability to render. Desktop viewport emulation is not a physical-phone test.

Contact-plan caching is now independent of presentation and hand posing: chart/device/rule version, planner settings, window and exclusions determine the key. Changing this presentation module, the physical viewing camera or export detail does not invalidate contact search. A device/projection/scoring-rule change does. See [planner optimization and cache verification](ipad-rhythm-planner-optimization.md) and [the Git/branch workflow](rhythm-git-workflow.md) before moving this build into another environment.

## Repository-boundary correction

The simulator now owns an independent `.git` and `docs/` at this folder. The parent room repository ignores it. Chair/room changes are outside this Git history; see [repository separation](repository-separation.md). Blender is configured via `BLENDER_BIN`/PATH or a local `.tools/Blender.app`, replacing the source code’s old assumption about the parent workspace layout. The launchd service records the existing shared Blender installation explicitly. Runtime/media paths and the Tailscale URL did not move. The packaged benchmark baseline still works when `--workspace` points directly to this repository.

## Chart-library import — 2026-11-07

Run `.venv/bin/python import_charts.py charts/imported/10plus-import-novedio` after copying the extracted collection. The importer produces one ArcCreate project per difficulty and `charts/imported/catalog.json`; the dashboard merges this local catalog with its original two chart IDs. Generated projects use relative asset links into the copied collection. Preserve both `library/` and the extracted collection, including symlinks, when moving the library; alternatively regenerate projects on the target. No source file is edited. ffprobe is needed for audio duration, including difficulty-specific audio overrides. The importer replaces its collection's catalog entries atomically and is repeatable.

The full import contains 268 difficulties across 77 songs, plus the two original dashboard projects. Eight Designant/Lament Rain charts use a `designant` arc token unsupported by the installed planner and ArcCreate readers; keep them visibly unavailable instead of coercing that token to a Boolean or omitting arcs. Forty other charts have parser feature warnings, which the selector displays. Parsing does not establish support for every scoring/visual gimmick. Inventory, import counts and rejected-chart reasons are recorded under `charts/manifests/`.

Two workflow issues surfaced during end-to-end validation. Build must send **all** form settings, including chart ID and start/end; camera-only payloads silently fall back to the original default chart. Blender preparation must use `-b` so its process exits after the script; the scene builder must tolerate `bpy.context.screen` being absent in background mode. Check for an active job before reloading the dashboard catalog.

Verified all 268 source/project AFF hashes against the original inventory, generated 268 projects, and passed 73 project tests. The live dashboard listed 270 chart options and eight unavailable entries. A real Abstruse Dilemma Past 5 build at 3–5 s used `0.aff`, completed contact planning, Blender preparation, ArcCreate replay and GLB export, and played in WebGL without browser errors. All 15 predicted arc catches were confirmed by native replay. The gameplay file has H.264 video and AAC audio; its 2.733 s duration includes the existing release/judgement tail. Evidence: `output/runs/20261107-033350-6b7eb9/` and `output/chart-import-dashboard.jpg`.

## Designant red-note compatibility — 2026-11-07

`designant` is a red trace with non-scoring sky taps. ChartIR preserves it explicitly as `ArcEvent.is_designant`; the trace is a guideline, not a scoring hold/arc, and its sky-tap occupations have zero scoring weight. The dashboard has `enable_red_notes=true` and `target_all_red_notes=false` defaults. Disabled means remove these traces/taps from the temporary render chart. Enabled without targeting means show them but plan no extra contacts; incidental native catches still register. Targeting adds red sky-tap occupations and ranks plans by scoring ticks lost, then red notes missed, then movement cost. Red targets are always skippable, even with ordinary misses disabled. This is a bounded best-effort search, not a guarantee of catching every red note.

The installed ArcCreate 1.2.68 parser does not understand the token. `dashboard/red_notes.py` prepares a per-take compatibility project using relative links to the copied assets. It translates designant to native traces with a private SFX marker, preserving the original chart, timing-group scope, and tap positions. `arc-create-export/RedNotes.cs` recognizes that marker: rendering tints traces/sky taps red, reloads the ordinary sky-tap mesh/texture, and keeps native touch requests, expiry, particles and sounds. Only red score processing and combo normalization are excluded. Red verdicts have separate `red_hits`, `red_misses`, and `red_judgements` in the replay JSON. Regular scoring behaviour remains native.

Run `./arc-create-export/setup.sh` to build the compatibility exporter. It now writes `.tools/ArcCreate/ArcCreate-red-notes.app` separately from the older exporter, so installing it does not mutate an app already rendering a user run. Both setup and export accept `ARCCREATE_EXPORT_APP` to override this destination. Source build and Mono.Cecil prerequisites are unchanged. The gameplay cache includes `RedNotes.cs` and the converted chart bytes; planner cache fingerprints include typed red-note semantics and both controls. Old completed takes retain their original footage/settings. Reload the dashboard only when idle, and refresh the browser for its new controls/catalog.

Native verification in `output/red-note-qa/` used two scoring taps and one red sky tap. Red hit, red miss, and red disabled each yielded **10,000,002 score, combo 2, two regular hits and zero regular misses**. Their separate red counts were 1/0, 0/1, and 0/0. A decoded frame visibly shows a red sky tap and red trace. A real Designant Past 7 excerpt at 77.5–80.5 s completed the full planner/Blender/ArcCreate/GLB pipeline: five red targets planned, five native red hits, zero skipped scoring ticks. Its one later red miss and two later ordinary misses are the existing judgement tail after the selected cutoff. Saved demonstration: `output/red-note-qa/runs/20261107-034319-055a2e/`, also linked into normal saved takes. Browser controls, enable/disable dependency and WebGL loading were checked without changing the user's tab. Screenshot: `output/red-note-qa/dashboard.jpg`. The project and vendored planner suite passed **160 tests**; the recorder checks passed too.

The same validation exposed a source-audio/sample-grid issue on Arghena (`AudioOffset:-1297`). Negative source offsets previously put early visible excerpt contacts into negative animation frames, where clipping created empty contacts and cache validation failed. New plans retain `source_audio_offset_ms` for footage/audio and use a nonnegative `audio_offset_ms` sample grid. Replay input follows the grid, while hit-sound mixing uses the source offset. The exact failed 135–165 s Arghena settings now generate 92 contacts and pass completed-plan validation. Do not conflate these two offsets when porting playback or audio mixing.

References for the AFF distinction: [community AFF model](https://github.com/AuRousKiNN/aff) and [ArcaeaChartRender element model](https://github.com/Arcaea-Infinity/ArcaeaChartRender/blob/master/ArcaeaChartRender/element.py). The native adapter is based on the installed [ArcCreate source](https://github.com/Arcthesia/ArcCreate), with local touch-replay tests establishing this implementation's scoring isolation.

## Scroll speed and audio mixing — 2026-11-07

The dashboard's `scroll_speed` uses ArcCreate's 1–10 scale (default 5). The adapter sets `Settings.DropRate` to `round(scroll_speed*30)` for export and restores the previous value afterward. This is the native approach-speed control, not `PlaybackSpeed`: note timestamps, judgement windows, hand contacts, song tempo and preview playback rate remain unchanged. Chart-authored timing-group/scene speed overrides still apply normally.

`song_delay_ms` ranges from −2000 to +2000 ms, default 0; positive means the song plays later. It is independent of the chart's source `AudioOffset`, planner sample-grid offset and the browser's playback-rate control. `hit_sounds` is one toggle covering both block hits and successful snake scoring ticks (default true). There are no separate block/snake toggles.

Stock ArcCreate AudioRenderer schedules effects from chart contents. Our replay path disables that autoplay effect mix and records native successful callbacks instead. Ground taps and hold heads use the native tap callback; sky taps use `PlayArcTapHitsound` only when `isFromJudgement` is true (including caught red notes). Native arc-head highlight sounds are excluded from the tick track. Each successful native scoring-arc request records a click at its actual settlement time; expired/Miss/NoScore requests do not. Standard ArcCreate clips and custom sky-tap SFX are retained. Native chord sound deduplication is retained for block clicks. `.sounds.json` records the actual block/tick event times separately for audit, while one combined `.hits.wav` is offered to users. Turning hit sounds off changes the mix, not judgement or the recorded WAV.

The new exporter is built by `./arc-create-export/setup.sh` into `.tools/ArcCreate/ArcCreate-media.app`, independently of the older running exporter. `ARCCREATE_EXPORT_APP` overrides this destination for setup and export. Each cached replay retains a silent 30 fps `.base.mp4`, combined `.hits.wav`, song `.music.wav`, native `.judgements.json` and `.sounds.json`. Song PCM includes 2000 ms before and after the selected video interval, preserving song continuity when shifting an excerpt; only the actual song edges are padded with silence. Song extraction preserves the source sample rate, leaving high-quality resampling to ffmpeg. Hit PCM uses 48 kHz stereo. The preview mix is AAC with the H.264 video stream copied unchanged. `dashboard/media.py` trims song PCM at `(2000-song_delay_ms)` and leaves hit PCM at time zero, so a positive delay shifts only the song later. Mix identity includes the controls and mixer implementation; scroll speed is included in the native replay cache identity. These three media settings are excluded from contact-plan cache keys and pose signatures.

Validation: 166 project/vendor tests passed, including synthetic tone checks for both delay signs, muted clicks, fixed hit timing, unchanged H.264 packet hashes and completed-plan reuse across all three controls. Real Designant native exports at speeds 5 and 8 had exactly identical judgement JSON and sound event timestamps, but visibly different approach geometry. Both captured 5 block clicks and 15 snake tick clicks; an otherwise identical all-missed input produced zero sound events and an entirely zero-valued WAV. Evidence is in `output/media-qa/verification.json` and the speed5/speed8/missed artifacts.

A full dashboard-pipeline QA take with speed 8, song delay +200 ms and hit sounds on completed at `output/media-qa/runs/20261107-040442-ff99a5/` and is linked into normal saved takes. WebGL recording from that take produced H.264/AAC, 856×642, 3.804 s including encoder startup; its source gameplay is 3.733 s. Decoded recorded audio correlated 0.967 with the source mix after a measured 49 ms encoder/startup alignment, consistent with the existing real-time recording limitation. Artifacts: `output/media-qa/webgl-preview.mp4` and `dashboard.jpg`. Browser inputs restored the saved 8/+200/true values, showed the combined WAV download and actual-hit counts, and loaded the GLB without browser errors. The existing user MP4 export was allowed to finish before the idle service was reloaded.

## Idle/detour pacing and default migration — 2026-11-07

A saved Arghena take exposed fast motion added after contact planning. Always compare `motion_paths.positions` with the final fitted paths before attributing a fling to return-to-home logic: this case had short gaps without home dwells, but the collision pass added roughly 12 cm detours under the full 4 m/s main budget. The new `idle_speed_m_s` control (default 1) caps optional home movement and both avoidance passes. A necessary faster contact-to-contact baseline remains allowed; it must not be slowed by moving/skipping a fixed touch. For long gaps, do not accelerate an optional home excursion to fit; allocate terminal return time using the idle limit. Rotation remains subject to the existing wrist and full-glove speed checks.

Idle speed belongs in pose signatures, not contact-plan cache keys. Existing take snapshots do not update automatically: the original full 56.55–118.378 s plan and movie were reused to regenerate a new motion/scene take `20261107-044336-ca9335`. Contact positions, all plan bytes and 3,746 motion samples were preserved. The affected transfer fell from 3.46 to 1.19 m/s. Residual collision diagnostics remain possible; this patch does not reintroduce failure/replan gates. The detailed cause, native input alternative, boundary misses and measured overlap tradeoff are documented in [the Arghena investigation](arghena-end-miss-and-idle-motion.md).

New requested defaults are repeat comfort 167 ms, fast-run gap 133 ms, two free presses, fast-run cost 10, scroll 2.5, song delay 0, prefer arc geometry/visual-end holding on, elevation 70°. `DEFAULTS_REVISION` and `STARTUP_PREFERENCES` apply these once when resuming pre-migration takes. Explicitly selecting a historical take still restores its old settings; subsequent new takes carry the current revision and retain custom tuning. Reset uses the full defaults. Distinguish a migrated form's pending preferences from the media of a historical take (the UI shows Settings changed).

Validation: 170 project/vendor tests pass; browser verification checked the new control, migrated values, GLB loading and the affected interval. The corrected saved take reuses the original ArcCreate footage; it preserves the original missed note for a fair motion comparison. Native diagnostic replay separately demonstrates that the 114.485 s lane-3 miss is avoidable with an earlier release/repress; a general contact-planner repair is not part of this motion change.

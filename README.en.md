**[简体中文](README.md) · English**

# iPad rhythm prototype

Two chunky pointing gloves play on a rounded tablet in an independent walnut tabletop presentation set, with a charcoal mousepad, ceramic plant pot and ballpoint pen. Each hand has one playable index finger. ArcCreate renders the perspective track, sky input, arcs, effects, and HUD from the supplied charts. The live Three.js preview uses Blender-exported meshes and verified 60 Hz hand motion, with ArcCreate touch-replay footage on the tablet.

Earlier offline exports (retained for comparison):

| Chart | Full contact plan | Hand animation and gameplay |
| --- | --- | --- |
| BIBBIDIBA | The strict planner reported no candidate path. | 35 contacts from the 3.50–11.75 s chart window in `output/bibideba-excerpt.blend` and `output/bibideba-excerpt.mp4` |
| Ex-Otogibanashi | `output/ex-otogibanashi.plan.json`, 586 contacts | 40 contacts from the 0–6.65 s chart window in `output/ex-otogibanashi-excerpt.blend` and `output/ex-otogibanashi-excerpt.mp4` |

The packages and their extracted projects are under `charts/imported/`. The Blender files use relative paths to their ArcCreate footage under `output/gameplay/`. Keep that folder next to the scenes when moving them. The MP4s include ArcCreate's source audio and hit sounds.

The local chart selector also contains all 268 difficulties from the 77-song `10plus-import-novedio` collection, grouped by song. See [chart import and compatibility](charts/README.md) for regeneration and source assets. Charts with known feature limitations show a notice when selected. Designant/Lament Rain red notes are supported: **Enable red notes** controls visibility (on by default); **Target every red note** adds them to the planner (off by default). Red catches are optimized after scoring notes and before movement cost. Unreachable red notes can still be skipped; they never count as lost scoring ticks or break native combo, and have separate replay hit/miss counts.

## Tune in the dashboard

See [calculation and media cache layers](docs/cache-layers.md) for exactly what a settings change reuses. Hand pivots now share a validated cache across separate takes; a scroll-speed change reuses contacts and hand motion, while ArcCreate footage and the new scene snapshot still need the appropriate update.

**Scroll and audio** contains the chart scroll-speed control (ArcCreate scale 1–10, default 2.5), song audio delay (−2000 to +2000 ms, default 0), and one **Hit sounds** toggle (on by default). Scroll speed changes note approach distance, with note times and tempo unchanged. Positive audio delay makes the song play later relative to the video; negative plays it earlier. Hand contacts, gameplay timing and hit clicks stay fixed. Build to apply these controls; contact planning is cached independently, and audio changes reuse cached native footage.

Current dashboard preferences: comfortable repeat gap **167 ms**, fast-run gap **133 ms**, **2** free fast-run presses, extra fast-run cost **10**, prefer arc geometry and visual-end holding **on**, camera elevation **70°**. Startup migrates these preferences once for older saved takes; selecting a saved take explicitly still restores its historical settings, and subsequent takes retain their own tuning. Reset controls applies the complete current defaults.

**Idle / detour speed** defaults to **1 m/s**, independently of the main move-speed limit. It bounds optional returns to the hand's own side and avoidance detours. If a full rest excursion cannot fit at that pace, the hand stays nearer its contacts. Required note-to-note transfers may exceed this idle limit under the main move limit, but optional avoidance cannot accelerate them further. Contact times and points remain fixed. See the [Arghena miss investigation](docs/arghena-end-miss-and-idle-motion.md).

The single hit-sound toggle covers ground/sky block hits, hold heads and successful snake scoring ticks. The ArcCreate replay adapter captures actual native catches rather than scheduling autoplay clicks. Misses stay silent. New takes provide **Download hit sounds (WAV)** under ArcCreate results, plus counts of block and snake clicks. The WAV remains available when the mix's hit-sound toggle is off. The delayed song and selected hit sounds are included in both the live preview and its WebGL/Blender video exports. Older saved takes keep their original media until rebuilt.

This folder is its own Git repository; the parent dafeiyu room repository ignores it. Simulator code, documentation, charts and runtime output belong here.

Run `./dashboard.sh`, then open [Rhythm workbench](http://127.0.0.1:8765). The dashboard uses the two copied chart packages and runs the planner, ArcCreate, and Blender on this Mac. It binds to loopback and saves each take under `output/runs/`.

For phone access, connect Tailscale on the phone and open [the tailnet dashboard](https://macbook-pro---riptideiv.tail57056e.ts.net/). Tailscale Serve proxies HTTPS to the loopback server, using the tailnet's existing access policy. Enable it with `tailscale serve --bg --https=443 http://127.0.0.1:8765`; disable this endpoint with `tailscale serve --https=443 off`. Serve persists in the background, but the dashboard process and Mac must remain running. An idle-sleep assertion is currently tied to the dashboard process for remote use.

The current Mac login runs the dashboard as the launchd job `com.dafeiyu.rhythm-workbench`, with `KeepAlive` and `caffeinate`, independently of the agent's terminal session. Its generated definition is `output/workbench.launchd.plist`, and output/errors go to `output/dashboard-server.log`. Inspect it with `launchctl print gui/$(id -u)/com.dafeiyu.rhythm-workbench`; stop it with `launchctl bootout gui/$(id -u)/com.dafeiyu.rhythm-workbench`. After logging out or rebooting, load this session job again with `launchctl bootstrap gui/$(id -u) "$PWD/output/workbench.launchd.plist"` from this directory. Do not start another server on port 8765 while that job is loaded.

On phones, the preview comes first. The sticky Preview / Settings / Build bar jumps between the video and controls and submits the current settings from anywhere on the page. Inputs use 16 px text with decimal or numeric keyboards; controls have larger touch targets, and the video plays inline. Build submission returns to the preview and progress display.

Choose any window inside the song, including the full song, then press **Build live preview**. The old 20-second cap is removed. Hand settings require a new plan; camera sliders update the existing 3D preview immediately. Draft uses a device-pixel ratio capped at 1; High caps it at 2. Completed takes retain their settings, touch plan, motion samples, GLB, Blender scene, gameplay footage, actual judgements, log, and measured speeds. **Export MP4** separately runs the slower EEVEE image-sequence render using the current live camera. Each view gets its own export file and preserves the verified source scene. Cancellation stops the current process group and leaves previous takes available.

The shared chart-range track has independently draggable start/end handles, synchronized exact numeric fields, and a **Full chart** shortcut. It preserves the chart's exact end time (including Ex-Otogibanashi's 109.894 s duration) and a minimum 0.5 s window. Arrow keys adjust a focused handle by 0.1 s, Shift+arrows by 1 s, Page Up/Down by 5 s, and Home/End to the allowed edge. Track taps and numeric entry provide alternatives to dragging.

**Export preview video** records the actual WebGL canvas with the gameplay element's audio. It replays the displayed take from the beginning at 1×, using the current live camera, lighting and preview detail. Recording takes approximately the clip's duration; it does not run Blender, replan contacts, or render ArcCreate again. Keep the page visible. Controls are locked during capture, and the original paused/playing state, position and playback speed are restored afterward. **Cancel export** preserves any previous completed download. The recording requests up to 30 fps and uses even 4:3 dimensions near the live canvas resolution so H.264 cannot silently crop an odd pixel column. MP4/H.264/AAC is preferred when the browser supports it; WebM is the fallback. **Download MP4/WebM** saves the file locally; download before closing/reloading the page. The download is browser-local, not a new saved take.

The expandable **Blender MP4 export** remains available for offline EEVEE rendering (640 × 480 Draft / 1024 × 768 High), with separate saved downloads. Its lighting can differ from the WebGL view. **Download gameplay only** remains separate under the motion report. See [WebGL export verification](docs/ipad-rhythm-webgl-export.md) for the measured output and browser/API details.

**Presentation set** is directly below playback. New builds use the independent set automatically. For an older saved take, press **Update presentation** to regenerate only its Blender/GLB snapshots, preserving the exact hand meshes, baked motion, plan, native judgements and gameplay footage. A successful update clears the stale MP4 download; export again for the new set. Cancellation/failure leaves the previous scene usable. Versioned scene paths and scene-aware MP4 cache identities prevent reuse of the old room export. The latest 120–150 s take and the 65–75 s cross-hand verification take have been updated.

See [the environment-porting guide](docs/ipad-rhythm-environment-porting.md) for hierarchy/coordinate contracts, material portability, snapshot regeneration, cache handling, repeatable validation and experience from this port. Original room assets are preserved; the simulator no longer loads `whale-room-final.blend`.

Move speed is now checked on the animated glove after airborne detours and wrist rotation. A conservative bound combines translation with the speed of the farthest point turning around the index pad. Turn speed has its own hard limit. Active index pads stay on their chart targets. Glove overlap is handled by contact-preserving posing; it never blocks a build. The planner's acceleration control remains a preference. Playback speed in the video player is only for inspection.

The earlier animation measured approximately 2.8 m/s translation and 3,240°/s wrist turns. Those airborne and rotational changes had bypassed the contact planner's 3 m/s limit. The new default Ex-Otogibanashi take uses a 1.6 m/s cap and smooth 6 mm lifts. Its measured peak is 1.57 m/s. A lower limit may require additional missed notes. With Allow missed notes disabled, infeasible windows still fail explicitly.

The **Click timing** group exposes the original planner's same-hand repeat preferences. Comfortable repeat gap maps to both effective `repeat_comfort_s` fields, with a 167 ms default, and Fast-repeat cost maps to `repeat_weight`, default 5. Fast-run gap, Unpenalized taps per run, and Extra cost per tap map to `jack_gap_ms`, `jack_free`, and `jack_weight`, with defaults 133 ms, 2, and 10. These add costs rather than forbidding fast taps. The old Prefer same-hand repeats shortcut zeroed both costs. Historical takes load those zeros explicitly.

Under **Hard timing limits**, Tap duration, Minimum release gap, and Release gap after a hold or arc map to `point_contact_frames`, `min_air_frames`, and `held_release_air_frames`. Their defaults are 2, 1, and 4 frames at 60 Hz. Point taps therefore have a theoretical minimum press-to-press interval of 50 ms before movement is considered. New renders use the visible values. Reset controls restores the upstream repeat defaults.

Under **Arc paths and release**, **Score-aware** is the default for new dashboard work. The planner can wait inside a valid hitbox or cut across a zigzag instead of tracing every bend. It fits stationary anchors first, then short connecting paths, and checks catches on the actual 60 Hz input samples. Tick timing, asymmetric request/expiry windows, segmented hitbox geometry, zero-duration connectors, density factors, colour ownership and cross-colour grace follow the local ArcCreate 1.2.68 implementation. A later segment cannot substitute for the specific segment that owns a tick. **Trace** retains the earlier centre-following planner for comparison; saved takes without this setting retain their old behaviour.

`planning/native_input.py` adds the native world-or-screen overlap rule and screen-space hitboxes calibrated from the fixed 1148 × 802 exporter. Scoring checks inverse-project each contact across the built-in camera tilt range (up-vector X from −0.05 to +0.05), rather than assuming an unchanging camera. Binding checks conservatively consider every colour the contact can enter across those views; simultaneous ownership conflicts are rejected across the hands. Around possible grace exits, candidate paths briefly favour the intended colour's centre before returning to tick-based shortcuts. These guards are part of planning and are checked for coverage, physical screen bounds and speed; they are not manual replay edits or a full-tracing requirement. The bounded search and camera envelope are conservative, not a proof of a globally optimal native score.

Native seek initialization consumes arc ticks exactly at the window start through `Arc.ResetJudgeTo` / inclusive `ComboAt`. Such ticks are excluded from the planner's remaining scoring jobs and prediction ledger. Notes after the window remain in the exported chart and continue producing native judgements during the video tail.

The fresh 65–75 s score-aware take `20261107-000550-9a4498` disables tracing preference and plans zero skipped ticks. Native ArcCreate confirms 76/76 remaining arc catches: 96 total hits, zero misses within the requested window, and two tail misses at 75.383 s and 75.633 s. It uses the unedited output of the dashboard planner. The saved take retains its actual touch replay, native judgement report, scene, and `input_model` identifier.

**Prefer tracing arc geometry** adds a soft preference within Score-aware mode, with an adjustable **Geometry deviation cost** (`prefer_arc_trace`, `arc_trace_weight`, 1000/s when enabled). It measures the entire visual snake, including its head, tail and release gaps: one second released or at least 20 mm from the projected centreline costs the full rate; smaller offsets cost proportionally less. Tick jobs partition that lifetime, so adding ticks does not multiply the penalty. The planner considers full traces, partial traces and shortcuts, and measures the actual composed contact path rather than just a tick's target. Contact travel also contributes to the existing distance cost, letting small deviation costs favour less motion. Missed ticks rank before these preferences, and the movement cap remains hard. The existing full-hold tail cost is additional when enabled. This preference is on by default; explicitly selected historical takes retain their original paths.

Tick-based release (`full_arc_hold=false`) lets a finger leave once the final scoring tick is secured, rather than following the visual tail. Connected same-colour arc segments form one snake. Score-aware mode uses native judgement windows instead of the upstream approximation of half a tick interval early. Contact ends on a 60 Hz frame after settlement; exported contact and Blender movement use that same release time.

**Prefer holding arcs to the visual end** is enabled by default, retaining the upstream policy. Early arc-release cost (`partial_hold_weight`, 1000 per untouched second) then discourages partial contact without forbidding it. The equivalent ground-hold preference and cost (`full_ground_hold`, `partial_ground_hold_weight`) are also exposed; ground holds retain the upstream full-hold default and 500 cost. The extra held-release gap applies only to contacts held through their visual end. Costs are inactive while their full-hold preference is off.

Historical takes retain their original full-hold policy when selected. On opening the dashboard with a historical take, the current defaults revision applies new preferences once and marks changed settings; explicitly selecting a take restores its saved settings. Existing renders stay intact. The local vendor patch to `multi_finger._release` removes hypothetical early air time from the acceleration calculation. Early release now must be an actual selected candidate, so acceleration departure, exported contact end, and animation agree, including when full holding is enabled. The upstream arrival-window preference remains unchanged.

The camera now uses perspective projection from in front of the tablet. Its default distance is 0.29 m with a 30 mm lens and 70° elevation. You can adjust distance, view angle, horizontal angle, and lens independently.

## Missed notes and live playback

The planner first attempts a perfect-contact search in the selected arc mode. If that fails, it expands arcs into individual scoring-tick jobs with their judgement windows. A touch can catch successive ticks, release, and reacquire the same snake when a hand becomes available. With **Allow missed notes** enabled, skip transitions rank paths by missed scoring ticks before ergonomic cost. Ground holds and point notes remain whole jobs. Search width defaults to 256 states and is adjustable under Search effort; the finite candidate/beam search does not prove a globally optimal score. Dependent covered-note/meeting shortcuts are disabled in the fallback search. Strict mode still permits release/reacquisition, but rejects plans requiring missed ticks.

Arc reacquisition respects ArcCreate 1.2.68's colour input lock after lifting an assigned finger: up to `min(4 × judgement interval, 1000 ms)`, cleared when no arc of that colour remains or when cross-colour grace starts. Tick windows can allow later recovery despite that delay. The planner retains colour-lock history across intervening ground contacts. Native ArcCreate replay remains the authority for actual judgements, including incidental hits and the engine's exact hitboxes; reports distinguish planned misses from native results.

Gloves now pivot around their fixed index-pad centres instead of feeding collision failures back into note planning. `hand_pose.py` searches a bounded, look-ahead corridor of paired pitch/yaw poses. Rotations use the angular and full-glove speed budgets left after fingertip translation. Only released hands can lift or detour; every active contact sample stays pinned. Pose fitting never adds misses or contact-exclusion retries. Truly incompatible contacts or a search shortfall remain non-blocking diagnostics.

Long idle gaps now use return–wait–approach motion. Each released hand returns to its own starting side (left/right), rests there, then approaches the next contact late enough to avoid drifting across the gap. The home detour needs at least a one-second gap and room for a stationary dwell; otherwise the hand takes the direct transfer. Smooth travel is fitted under the movement cap, all chart contacts remain fixed, and stationary home samples are pinned through collision posing. `motion_paths.py` contains this path construction; its source is included in the motion-cache signature.

The idle-motion take `20261106-233453-9d2967` reuses the unchanged, native-confirmed BIBBIDIBA 20–40 s touch replay. Its right-hand home waits remain stationary through pose solving, and all 141 Blender/WebGL checks pass. The tracing-preference take `20261106-233629-41d276` covers BIBBIDIBA 20–26.5 s with a 1000/s geometry cost: native replay confirms 27/27 planned arc catches (50 total hits, two misses), zero residual proxy overlaps, and all 57 baked/WebGL checks pass.

The collision model encloses each glove/cuff ellipsoid in a sphere and checks pose interpolation at quarter-frame steps. It is conservative rather than an exact skin-contact model. The dashboard exposes preferred clearance and distinguishes overlap from missing the preferred clearance by a fraction of a millimetre. Solved poses are cached across takes by actual sampled paths, pinned contacts, pose parameters and solver sources, so camera, scroll and audio changes reuse identical hand motion. Blender and WebGL use the same 60 Hz root samples and pitch/yaw interpolation.

Pivot-pose verification used the unchanged 20–40 s BIBBIDIBA contact plan in `output/pose-validation`: 1.130 mm minimum proxy clearance, zero active-contact drift in the solver, no mesh intersections in 192 sampled baked poses, and 141 passing independent Blender/WebGL alignment checks. Maximum baked root drift was 0.000063 mm. The user's 40–101.4 s take (`20261106-221443-ff38a7`) also completed with the new poses: 0.687 mm minimum proxy clearance, 3.19 m/s peak glove speed and 300°/s peak turn. Positive clearances below the preferred 1 mm remain valid, non-intersecting poses. Local live playback measured 116 fps at 1280 × 720 in Draft mode; that is a Mac browser measurement, not a phone benchmark.

The replay adapter subclasses ArcCreate's native TouchInputHandler. It projects tablet coordinates through ArcCreate's own camera, supplies down/move/up events, disables autoplay, and records native ScoreService judgements. The original chart remains intact, so skipped notes visibly miss. ArcCreate results, rather than planner predictions, drive the dashboard hit/miss totals. A replay-specific cache includes the input samples, chart, stable project metadata, media assets, and adapter version; perfect autoplay footage is never reused for a missed-note replay.

Score-aware takes also include `arc-tick-check.json`. A passive wrapper around native arc judgement receivers records each tick's real result without changing input or scoring rules. The dashboard shows how many predicted catches native replay confirmed and exposes unconfirmed predictions and incidental hits separately. A synthetic 12-segment zigzag scores 12/12 with a stationary finger at a 0.1 m/s movement limit, whereas tracing skips all 12; the empty-input native control scores 0/12. BIBBIDIBA 20–40 s at 4 m/s (`20261106-225755-32889f`) produces 136 native hits and 9 misses versus 129/16 in the earlier trace take. Native replay confirms all 107 planned arc catches. It retains zero solver contact drift; closely spaced catches can still leave residual glove overlap, reported without blocking playback.

WebGL loads the same glove/tablet/set geometry from the selected take’s GLB and samples `motion.json`. Video media time is the only clock, including seeks and 0.5×/0.25× playback. Blender Z-up coordinates and XYZ rotation conventions are converted to glTF/Three.js explicitly. Exported area lights are approximated by broad fill and a directional key in the live view; MP4 export retains Blender lighting. Paused previews stop redrawing until a pose, video frame or camera changes.

Verification of the 10–35 s BIBBIDIBA take (`20261106-205842-d8dd7b`) passed 177 independent baked-frame and between-frame comparisons: maximum position error below 0.1 micrometre, rotation error below 0.00001 degrees, and camera-projection error below 0.000001 normalized units. Its final native result was 150 hits and 20 misses with a verified 4 m/s glove-speed cap. An empty native replay produced zero hits and zero score, proving that the replay export is not secretly autoplaying.

The per-tick recovery take (`20261106-213200-6ba879`, BIBBIDIBA 10–35 s with full-hold preferences) plans 11 individual missed arc ticks and three reacquisitions. Native ArcCreate reports 159 hits and 13 misses, including window/release-tail judgements. The glove check retains 4 m/s, 360°/s and 1 mm limits; all 177 independent WebGL/Blender alignment checks pass. The previous same-settings whole-chain take (`20261106-211512-04c1db`) reported 151 hits and 21 misses. Neither take skips the cross-hand arc at 23.75–24.75 s; the 25.75/26 s arcs originally conflicted with ground holds, and the 33.12 s failure is an independently diagnosed glove-clearance failure.

Recheck a saved take with:

```sh
"${BLENDER_BIN:-.tools/Blender.app/Contents/MacOS/Blender}" --factory-startup -b output/runs/TAKE/scene.blend \
  --python-exit-code 1 -P tests/export_alignment_reference.py -- output/runs/TAKE
node tests/check_webgl_alignment.mjs output/runs/TAKE
.venv/bin/python -m pytest vendor/arc-hand-order/tests tests -q
```

Three.js 0.187.0 and the required GLTFLoader helpers are served locally from `dashboard/vendor/` under the included MIT license. No CDN is needed on the phone. ArcCreate source for the matching 1.2.68 tag is available locally under `.tools/ArcCreate/source`; adapters modify a separate app copy.

## Generate the contacts

The prototype vendors [arc-hand-order](https://github.com/BFvX/arc-hand-order/tree/73604eea75c088db39d332be5e6f77ab16d850aa) at commit `73604ee` under GPL-3.0, with local release-timing, skip-search, and excerpt-occupation patches described above. Run the commands below from the prototype directory.

```sh
uv venv --python python3.13 .venv
uv pip install --python .venv/bin/python -e 'vendor/arc-hand-order[dev]'
./plan.sh charts/imported/ex-otogibanashi/2.aff output/ex-otogibanashi.plan.json
.venv/bin/python plan_excerpt.py charts/imported/bibideba/2.aff 3500 11750 output/bibideba-excerpt.plan.json
.venv/bin/python plan_excerpt.py charts/imported/ex-otogibanashi/2.aff 0 6650 output/ex-otogibanashi-excerpt.plan.json --prefer-same-hand
```

Both planner entry points disable the second finger and its repeat behavior. The excerpt helper reconnects retained arcs and keeps the audio start time. It preserves original scoring-tick placement for notes crossing window boundaries, then clips the planning occupation and contact windows. `plan.sh` retains the upstream strict policy; `plan_excerpt.py` and the dashboard allow misses by default. Ex-Otogibanashi favors same-index repeats to avoid rapid exchanges at one screen position.

## Export ArcCreate gameplay

[ArcCreate 1.2.68](https://github.com/Arcthesia/ArcCreate/releases/tag/1.2.68) is the AFF renderer. Its local copy lives under `.tools/ArcCreate`. The adapter in `arc-create-export/` adds command-line arguments to a separate copy of the app and calls ArcCreate's built-in frame and audio exporters. It requires Mono with Cecil 0.11 and FFmpeg. The setup script accepts a `MONO_CECIL_DLL` path if Mono is installed elsewhere.

```sh
mkdir -p .tools/ArcCreate
curl -fL https://github.com/Arcthesia/ArcCreate/releases/download/1.2.68/StandaloneOSX.zip -o /tmp/ArcCreate-1.2.68.zip
ditto -x -k /tmp/ArcCreate-1.2.68.zip .tools/ArcCreate
./arc-create-export/setup.sh
./arc-create-export/export.sh charts/imported/bibideba/project.arcproj output/gameplay/bibideba.mp4 3500 12167
./arc-create-export/export.sh charts/imported/ex-otogibanashi/project.arcproj output/gameplay/ex-otogibanashi.mp4 0 7134
```

The export is 1148 × 802 at 30 fps to match the planner's landscape iPad screen ratio. Each export starts at the plan's audio start time and includes the short hand release at the end. The four-argument legacy command above uses autoplay. New dashboard builds pass a fifth argument, `replay.json`, and use real touch judgement instead. Their HUD, hit/miss effects, combo, and score are engine results; successful native hit callbacks supply the mixed hit sounds. The engine simulates at 60 Hz and footage is encoded at 30 fps. Source music remains unmodified, with a silent judgement-expiry tail added in memory when needed.

## Rebuild a saved take manually

For a geometry-only refresh, keep the dashboard running and use its presentation endpoint (or the **Update presentation** button):

```sh
curl -fsS http://127.0.0.1:8765/api/presentation \
  -H 'Content-Type: application/json' -d '{"id":"your-take-id"}'
```

This asynchronous operation updates the take metadata as well as its versioned `.blend` and `.glb`. Poll `/api/state` or watch the dashboard, then use **Export MP4**. Avoid invoking `animate.py` for an environment-only change: that entry point builds a new animation, while `refresh_scene.py` preserves the existing baked keys. The porting guide includes direct Blender audit commands.

The upstream planner suite passed 81 tests during setup. The one-index configuration rejected three disjoint simultaneous holds. Targeted motion checks verify that movement obeys the glove speed cap and wrist turns obey the angular cap. A Blender regression check confirms that identical, overlapping glove paths preserve their touch points and finish successfully; excessive speed still fails.

## Planner performance and completed-plan reuse

The contact planner now memoizes fixed projection/polygon geometry and state-independent arc extensions while retaining the same DP candidates, scoring checks, costs and search width. Three-window fresh-process benchmarks measured a 1.92× aggregate search speedup, with identical complete plans (apart from elapsed-time metadata). See [the optimization report](docs/ipad-rhythm-planner-optimization.md) and [repeatable benchmark commands](benchmarks/README.md).

CLI/dashboard planning reuses validated completed results under `output/contact-plans/`. The key includes chart/device/rule implementation, window, planner settings and exclusions; camera, presentation, hand posing and export settings do not invalidate contact planning. Use `--no-plan-cache` for uncached search measurements or `--plan-cache-dir PATH` for isolation. Library calls remain uncached unless `cache_dir` is supplied. Cache hits preserve the actual plan and emit `cache_hit=true` through the existing progress stream; corrupt/incompatible entries trigger normal search.

Blender is resolved from `BLENDER_BIN`, then the system PATH, then `.tools/Blender.app/Contents/MacOS/Blender`. This Mac’s launchd configuration explicitly points to the existing shared Blender installation. No original room scene is loaded.

## Language and current defaults

The dashboard defaults to Simplified Chinese with a saved English/简体中文 selector. Switching languages preserves the selected take, settings and playback without rebuilding media. Project documentation is Chinese; this English README remains an alternative. Third-party documentation/licenses and raw technical evidence remain in their original language.

Current defaults are comfortable repeat gap 167 ms, fast-run gap 133 ms, two unpenalized taps, extra fast-run cost 10, scroll speed 2.5, song delay 0, geometry tracing and visual-end holding both on, camera elevation 70°, and idle/detour speed 1 m/s. Earlier release-policy examples above describe historical takes; explicit historical selection restores that take's original settings. The shared pose cache now works across takes independently of scroll/audio; see [cache layers](docs/cache-layers.md).

All prior feature branches were merged into `main` in eight feature/fix chunks. Localization is isolated on `feature/zh-cn-i18n`; see [Git workflow](docs/rhythm-git-workflow.md) and [i18n maintenance](docs/i18n.md).

Preview recording supports 30/60 fps (default 60). New native touch-replay footage retains its original 60 fps instead of being downsampled; old saved gameplay remains 30 fps until rebuilt. Negative audio start times caused by chart offsets are supported without changing touch timing. The optional Blender export retains its existing 30 fps timeline.

Completed chart takes can add ArcCreate's native results flow from saved judgements: triangle/decorative transition, animated results entrance, native ranks such as All Perfect, and the native results music. Older endings offer Update results flow. Export ending only records the short tail with the existing WebGL scene; `dashboard/ending.py --finalize ... --body ...` splices it onto a matching 60 fps body without rerendering that body. Results endings currently use the WebGL export path. See [results ending notes](docs/native-results-ending.md).

Arm winding is now bounded to **±260°** by default, adjustable under Hand motion. The unwrapped cuff-to-cuff bearing retains its history through releases and idle returns. Free hands can take longer, movement-penalized routes to unwind while all held touch samples remain pinned. Older saved takes explicitly require regeneration. See the [winding design and Dantalion verification](docs/arm-winding.md).

# Calculation and media caches

## What happened on the scroll-speed-only rebuild

The Arghena takes `20261107-041829-013531` and `20261107-042826-bb0f61` differ only in scroll speed, **5.2 → 2.0**. The latter's log confirms completed contact-plan hits in **0.011–0.013 s** (including the separate exclusion variants encountered by the motion retry). Its first glove-pose calculation nevertheless ran again. Only the second Blender invocation, within that same take, reused poses.

The old pose signature already excluded scroll/audio settings, but the file lookup was `RUN_DIRECTORY/motion.poses.npz`. Every new take used a new directory. That was same-take reuse between motion verification and scene export, not sharing across takes. Later scene/GLB construction was also always performed. This made the build appear to redo everything despite reusing contact search.

## Current cache layers

| Work | Scope / location | Key inputs |
| --- | --- | --- |
| Completed contact plan | Shared disk cache, `output/contact-plans/` | Exact chart bytes **and path**; device-profile bytes; chart start/end in milliseconds; all planner-affecting settings; same-hand preference; forced excluded events and local arc-avoidance windows; planner/parser/rules source fingerprint. |
| Solved hand positions and wrist angles | **Now shared**, `output/hand-poses/` | Full generated 60 Hz finger paths before fitting; contact/rest pin mask; main move speed, wrist limit, lift, clearance and idle speed; shape/sample count; animation, path, hand geometry/solver and cache source fingerprint. |
| Native ArcCreate video, judgments and sound stems | Shared disk cache, `output/gameplay/replays/` | Exact touch replay including scroll speed; chart start/source offsets; clip length; AFF/project bytes; audio/artwork/SFX/Lua asset bytes; exporter source files. |
| Final gameplay audio mix | Shared disk cache, `output/gameplay/replays/mixes/` | Native replay cache identity; song delay; the single hit-sounds toggle; mixer source. Video is stream-copied. |
| Scene, GLB and Blender animation curves | Per take, `output/runs/ID/` | **No shared scene-build cache.** Each new take assembles the scene and writes its snapshots, even if solved hand poses were loaded. Scene-only refresh reuses saved motion/gameplay but explicitly rebuilds presentation geometry. |
| Blender video export | Final MP4 reused within a take | Settings/view plus presentation revision identify the per-take MP4. An existing completed file skips EEVEE frame rendering. The inexpensive export-camera scene preparation can still run. Different takes do not share final 3D MP4s. |
| WebGL video export | Browser-local download | Each Export action records in real time; no server computation cache. The previous recording remains downloadable in the page until replaced or closed. |

Contact-plan keys deliberately omit camera, quality/resolution, scroll speed, song delay, hit-sounds toggle, wrist/lift/clearance and idle speed. These do not alter contact search. Unknown future settings remain in the plan key unless explicitly excluded. Search itself also memoizes geometry and transition work inside its worker process; that in-memory work disappears with the worker. A completed-plan hit skips the whole search instead.

Pose keys use actual paths and pins, not full plan JSON or all settings. Camera, scroll/audio controls, chart labels, file paths, planner statistics and elapsed-time metadata cannot invalidate otherwise identical hand motion. Pose-affecting changes and source changes do invalidate it. Source invalidation is currently conservative: even unrelated edits to `animate.py` cause a cold pose calculation, because that whole file is hashed. The cache stores only finite, verified, contact-preserving arrays, validates checksums on read and writes atomically. Failed/partial calculations are not cached. Corrupt or unavailable caches fall back to computing.

## What different changes should redo

- **Scroll speed only:** reuse contact plan and hand poses; rerender native ArcCreate visuals at the new approach speed, obtain/reuse the corresponding audio mix, then assemble the new scene/GLB snapshot.
- **Song delay or hit sounds only:** reuse contacts, poses and native footage/stems; mix audio, then assemble the new scene/GLB snapshot.
- **Camera/display detail only:** live camera updates immediately. A newly requested take may rebuild its scene, but identical hand poses reuse the shared entry. Blender MP4 export still renders a new view when needed.
- **Idle speed, lift, wrist or clearance:** reuse contact search; solve different hand motion. A changed ending retreat duration can also change the required footage length.
- **Tap timing, speed limits, arc preferences or chart/window:** contact planning may need a new entry; hand posing reuses an entry only if the resulting actual motion inputs match.
- **Code changes:** relevant source fingerprints intentionally invalidate affected layers. The first build after a solver update can be cold; changing only scroll speed afterward should not be.

## Verification of the shared-pose fix

Two separate output directories use the exact full Arghena contact plan from `output/idle-qa/reposed/`, changing only scroll speed from 2.0 to 5.2. First solve: **21.59 s**, cache miss. Second process/take: **1.11 s**, cache hit, including Blender startup and motion JSON writing. Finger trajectories, wrist angles, sample clock and duration are exactly identical. The successful entry also seeds the normal shared pose cache for matching future builds. Evidence is under `output/pose-cache-qa/`.

Run logs now say `Reused contact-preserving glove poses from shared cache.`; the dashboard's current stage reports `Reusing cached hand pivots` when that line arrives. The final motion report includes `pose_cache.hit` and `pose_cache.key` for its Blender invocation. Old per-take pose files remain as historical artifacts but are not treated as verified entries in the new shared cache.

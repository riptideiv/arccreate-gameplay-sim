# Arghena end-region miss and fast free-hand motion

Source take: `20261107-042826-bb0f61`, Arghena Future 11, chart window **56.550–118.378 s**. Original source assets, contact plan, replay and media remain unchanged. Revised motion-only take: **20261107-044336-ca9335**, linked into normal saved takes; artifacts live in `output/idle-qa/reposed/`.

## The miss

The lane-3 ground tap at **114.485 s** (57.935 s into the video) is explicitly skipped in `plan.json`, with reason `no feasible contact in the bounded search`. ArcCreate reports its MissLate at **114.617 s**. The chosen plan reserves the left finger for a blue arc chain from 114.000 to 114.583 s and uses the right finger for the simultaneous sky tap at 114.483–114.517 s. The ground tap is therefore absent from that chosen contact sequence.

This is not evidence that the chart requires three fingers. Native scoring had already secured the blue chain's ticks at **114.000, 114.167 and 114.333 s**. The planner nevertheless retains the combined left contact until **114.583 s**, including a conservatively late reservation for an earlier arc tick. It does not choose the available release/repress allocation.

A diagnostic two-finger input alternative truncates that left contact at 114.467 s, re-presses the left finger for the sky note at 114.483 s, and moves the right finger's simultaneous press to lane 3. Native ArcCreate on the same 113.9–115.4 s test window changes from **14 hits / 1 miss** to **15 hits / 0 misses**, without losing the snake ticks. This establishes a contact-search/handoff limitation, rather than a necessary chart miss. This investigation does not install that hand-edited input as an automatic planner fix.

The other end-of-window planned skip, the blue arc tick at **118.376 s**, was actually caught incidentally at **118.383 s**. The selected interval ends at 118.378 s; the subsequent five native misses occur after the cutoff during the existing judgement tail. The initial skipped sky tap at 56.594 s is a separate motion-boundary exclusion.

## Why the hand flew around

Around the lane-3 miss the original right-hand touch sequence has short release gaps, not a return-to-home dwell. `motion_paths.positions` only inserts optional home rests for gaps of at least one second, so it did not send this hand home.

The later `hand_pose.air_paths` pass adds collision-avoidance excursions. It could add **up to roughly 12.7 cm** to this hand's path while spending the full **4 m/s** main move budget. The original direct transfer peaks at about **1.19 m/s**, but the fitted detour peaks at **3.46 m/s** around 114.55 s. Similar excursions also occur later in this take.

## Motion change

The dashboard now exposes **Idle / detour speed**, default **1 m/s**. Optional home-return scheduling may not raise its speed to squeeze in a full rest excursion. Terminal retreat time is allocated with this slower limit. When a full home trip will not fit, no forced high-speed excursion is inserted.

Both pad-avoidance and full-glove avoidance passes now cap optional translation to the idle limit, with an allowance for any faster direct movement already required by the fixed contacts. They also retain the existing combined translation/rotation main speed bound. Avoidance cannot use the main speed budget just to make a larger detour. The solver continues trying wrist pivots; unresolved overlap stays diagnostic and cannot trigger contact replanning or a failure loop.

Reposing the **entire original saved plan**, with its original ArcCreate video, reduces the right-hand translation peak over 114.3–114.7 s from **3.4606 to 1.1885 m/s**. The remaining speed is the required sky-to-ground transfer. Every contact position, timing and plan byte is preserved. The resulting motion has the same 3,746 samples, so the gameplay clock stays aligned. The full take passes its main glove speed and wrist-turn checks.

The bounded pose solver still reports residual proxy overlap: 14 sampled intervals versus 9 in the original, with the same approximately 12 mm worst proxy penetration. These include very close fixed-touch configurations. The idle cap trades away large airborne detours; it does not claim a collision-free solution for every coincident contact. Both takes remain viewable without collision gates.

## Defaults and validation

Dashboard defaults now use repeat comfort 167 ms, fast-run gap 133 ms, two free presses, extra fast-run cost 10, scroll speed 2.5, song delay 0, arc-geometry preference on, visual-end arc holding on, camera elevation 70°, and idle/detour speed 1 m/s. Default migration preserves the selected chart/window and other existing controls; explicit historical take selection still restores the saved values. A defaults revision on new runs prevents this migration from overwriting subsequent tuning on every page load.

**170 project/vendor tests pass**, including separate idle caps, bounded avoidance, contact preservation, faster required transfers, defaults and cache separation. Early-release tests still exercise the explicit off policy; the dashboard's new default prefers holding to the visual end. Idle speed is excluded from contact-plan cache keys but included in pose identity.

Evidence: `output/idle-qa/verification.json`, original/alternative native replay results under `output/idle-qa/native/`, regenerated motion/scene/report under `output/idle-qa/reposed/`, and live browser screenshot `output/idle-qa/dashboard.jpg`. The browser loads the revised GLB, exposes the 1 m/s control, shows the requested migrated defaults, and was inspected around 57.9–58.05 s into the replay without browser errors. Original contacts/gameplay were reused; no ArcCreate rerender was needed for the motion-only saved take.

# Contact-planner optimization — 2026-11-07

The finite-candidate DP, candidate generation, pruning, search width and scoring rules are unchanged. The implementation eliminates repeated projection/polygon work and repeated physical extension construction. Completed-plan reuse is a separate optional disk cache, enabled by the CLI/dashboard and disabled for headline search trials.

## Search measurements

Apple M2 Max, macOS arm64, Python 3.13.14. All runs use the copied 65–75 s settings (max move speed 4 m/s, search width 256, score-aware arcs, geometry tracing preference off, missed notes allowed). Only start/end change for each interval. Timing begins before planner imports and includes chart loading, preprocessing, search and plan serialization. Interpreter bootstrap and benchmark-harness imports are outside that clock. Three fresh processes per implementation/window, sequential and interleaved, fixed hash seed, identical progress logging, no completed-plan disk cache. No Blender, ArcCreate or video export is included.

| Bibideba window | Baseline median (range), s | Optimized median (range), s | Speedup | Peak RSS baseline → optimized, MiB |
| --- | ---: | ---: | ---: | ---: |
| 20–30 s | 21.156 (21.000–21.260) | 10.343 (10.320–10.389) | 2.05× | 197.6 → 188.6 |
| 65–75 s | 2.160 (2.146–2.161) | 1.363 (1.358–1.369) | 1.58× | 73.4 → 63.9 |
| 90–100 s | 1.543 (1.540–1.551) | 1.212 (1.210–1.234) | 1.27× | 73.4 → 63.5 |

Aggregate speedup, **sum of baseline interval medians / sum of optimized interval medians = 1.92×**. Raw CPU times, process IDs, timings, settings, source fingerprints and comparisons are committed under `benchmarks/results/`. The full uncompressed evidence is also under `output/benchmark/contact-planner-20261107/`.

The dashboard was idle and was not restarted or interrupted for benchmarking. No agent-started render/export overlapped headline trials. Unrelated lightweight chair-inspection files appeared during part of the interval; no heavy render was observed, and worker CPU/wall ratios exceeded 99%. Actual timing ranges are reported instead of assuming a perfectly controlled machine. The preliminary pilot used source-local device paths; final measurements pin the same canonical device file in both isolated imports, so even device-path metadata is identical.

## Completed-plan reuse — separate measurements

These are additional fresh-process requests against a previously completed, validated disk entry. No search runs. They are not included in the search speedup above.

| Window | Cache-hit median (range), s | Peak RSS, MiB |
| --- | ---: | ---: |
| 20–30 s | 0.121 (0.120–0.123) | 44.9 |
| 65–75 s | 0.122 (0.121–0.123) | 45.0 |
| 90–100 s | 0.120 (0.119–0.121) | 45.0 |

## Changes and correctness boundaries

- `GameplayProjection` computes its fixed camera matrix once. Coordinate arithmetic and finite/behind-camera checks remain intact. The public camera API is unchanged.
- `Regions` memoizes polygons by retained geometry identity and exact effective clamped time, scoped to one projection/region-settings instance. Split tick occupations selecting another source segment cannot collide merely because their parent IDs match. Cached polygons are immutable internally; callers receive fresh mutable lists. Memory is bounded at 65,536 entries per instance.
- `_Graph._scoring_extensions` caches the complete ordered physical alternatives for `(old touch ID, candidate ID)` in one graph, including infeasible results. Native grace guards and tick coverage are evaluated once per physical alternative. `_finish` still runs on **every DP state**, preserving the other hand's interactions, colour ownership/release locks, repeat/jack history, crossings and coupled costs.
- Prefix speed is checked once at this branch's exact original tolerance; each new extension checks its tail **and boundary edge**. Coverage still examines the full path because a newly introduced tick can be caught on an earlier prefix frame. Existing binding/trace-prefix summaries remain intact.
- Long histories retain immutable tuple paths and efficient O(1) random access. A larger persistent-prefix representation was deliberately deferred: after the smaller caches, interning/future/binding work is a small fraction of the representative profile, while earlier-frame colour/tick checks make a representation rewrite risky. No candidate or search-quality reduction was used.
- `plan_cache.py` keys completed results by chart bytes/path, selected window, device bytes, all planner sources/version, planner settings, explicit exclusions and avoided-contact intervals. Known camera/presentation/hand-pose/export-only settings are excluded; unknown future settings remain included conservatively. Cache entries carry a digest and structural validation, and are published only after a completed search via fsync + atomic replace. Failed/cancelled searches cannot publish partial results. Corrupt/incompatible entries fall back to normal planning.

CLI/dashboard requests enable completed reuse under `output/contact-plans/`. `--no-plan-cache` disables it, and `--plan-cache-dir` isolates it. Library `create_plan()` defaults to `cache_dir=None`, so benchmarks and existing test calls remain uncached. Presentation snapshots, native footage and hand-pose caches retain their own independent invalidation. Progress reporting is preserved; cache hits emit an explicit completed event with `cache_hit=true`.

## Profiles and validation

Separate cProfile runs on 20–30 s explain the removed work; these are not headline times:

| Work | Baseline calls | Optimized calls |
| --- | ---: | ---: |
| Camera matrix construction | 627,783 | 2 |
| Actual hitbox polygon construction | 122,980 | 559 |
| Native path guards | 109,602 | 14,690 |
| Touch interning | 91,931 | 13,914 |
| Coupled `_finish` validation | 243,201 | 243,201 |

The final optimized profile is about 24 s versus 45.5 s for baseline; normal unprofiled medians above are about 10.3/21.2 s. Remaining bottlenecks are DP state/candidate iteration and coupled transition checks/costs. Arc-profile construction, native hit testing and AFF parsing also form a larger fraction after caching. `future`, `bound_frames` and interning remain small enough to defer a larger contact-history rewrite.

All **18 headline output plans match in their entirety after removing only `stats.elapsed_s`**. This includes touch times, assignments, sampled paths, planned misses, tick ledger, cost components, and visited/pruned state counts. Profile/cache-hit contacts match as well. There is no observed tie-breaking change, score regression or ergonomic-cost change, so no native ArcCreate replay rerender was necessary.

**150 existing and focused Python tests pass.** New tests cover projection equivalence, source/time/projection polygon isolation, mutable-copy safety, negative physical-extension reuse, prefix tolerance and boundary checks, earlier-prefix catches, cache corruption, device/chart/window/settings/version invalidation, presentation-only reuse and cancelled-plan publication. Existing score windows, camera-envelope checks, colour locks/grace, partial catches, tracing/visual-end preferences and planning-progress tests remain unchanged.

## Repeatability and evidence

See `benchmarks/README.md` for commands. The packaged 95 KB runnable baseline snapshot and its source hashes do not require Git to execute; baseline commit `25f6003` remains separately available. The source import origin is asserted in every worker to prevent mixing baseline and optimized modules.

`results/raw-evidence.tar.gz` preserves configurations, plans, raw logs, timing records, profiles, source manifests, earlier exploratory measurements and test logs. `report.json`, `raw-timings.json`, `cache-hit-report.json` and profile summaries are also unpacked for review. The benchmark command refuses source/chart changes during measured runs and stops on any full-plan difference.

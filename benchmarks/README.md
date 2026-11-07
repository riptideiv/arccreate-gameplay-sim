# Contact-planner benchmark

Use the prototype's Python environment. No Blender/ArcCreate/native replay or video encode is involved. Keep the dashboard and other heavy jobs idle, but **do not restart or cancel them to benchmark**. Defer trials if they are busy.

From this prototype directory, set `WORKSPACE` to this independent simulator repository:

```sh
WORKSPACE="$PWD"
OUT="$WORKSPACE/output/benchmark/contact-planner-recheck"
PYTHON="$WORKSPACE/.venv/bin/python"
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage snapshot
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage all
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage profiles
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage cache-hits
```

`--baseline-revision 25f6003` is the default: snapshot restores the baseline's exact planner sources from the packaged runnable archive; Git is only a read-only fallback for a different requested revision. Execution does not depend on Git or the live working tree. Each worker inserts only that snapshot or the optimized sources into `sys.path` and asserts the imported package's origin. The interpreter/dependencies are shared. Both use the same original chart file and identical device file, including its path in output metadata. Settings are the checked-in copy of `20261107-000550-9a4498/settings.json`, changing only the requested start/end interval.

Headline trials run in fresh processes, sequentially interleaved baseline/optimized, three per interval. They use the same progress callback and logging; library calls disable completed-plan caching. Ordinary invocation-scoped memoization is enabled. Timings include chart parsing, preprocessing, search and serialization to JSON. Resource metrics cover that worker (macOS peak RSS bytes converted to MiB). CPU time and source fingerprints are saved beside every plan. Profiles run separately and are never used as headline timings. The `cache-hits` stage performs an actual cache fill and then three additional fresh-process hits, asserts no search phase ran, and reports them separately.

`report.json` compares the entire output plan across all 18 trials, removing **only `stats.elapsed_s`**. Touch samples, hand assignments, misses, tick coverage, all cost components, and visited-state counts must match. A mismatch stops the script. No native replay rerender is needed for byte-equivalent contact plans.

Raw configurations, logs, JSON plans, source hashes, timings and `.prof` files are retained under the output directory. Summary data and the interpretation are committed under `benchmarks/results/` and `docs/ipad-rhythm-planner-optimization.md` at this repository root.

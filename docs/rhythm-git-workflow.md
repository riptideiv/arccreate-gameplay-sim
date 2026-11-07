# Rhythm simulator Git workflow

This folder is an independent local Git repository. It contains only the simulator; the parent `dafeiyu-3d-room` repository owns the room, character and chair animation work and ignores this folder.

| Branch | Purpose | Simulator-only feature commit |
| --- | --- | --- |
| `main` | Original simulator baseline | `2aa579c` |
| `feature/webgl-video-export` | Canvas + gameplay-audio recording | `08101b4` |
| `perf/contact-planner` | Memoization, completed-plan cache and benchmark evidence | `260a5d9` |
| `integration/rhythm-updates` | Combined live dashboard, plus subsequent maintenance/import commits | Integration based on `bdb4d7f` |

The original parent commit IDs changed when histories were filtered to remove room/animation files. [Repository separation](repository-separation.md) records the mapping. The packaged pre-optimization source snapshot and benchmark evidence remain unchanged and runnable without Git. No remote was created or pushed.

Work directly from this folder. Use separate worktrees here for future feature work; the older worktrees under the parent project's `work/branches/` are historical parent-repository snapshots, not the canonical simulator checkout. Do not switch the live checkout during a running build/export.

Git's interactive signing prompt was unavailable during setup. Task commits used per-command `commit.gpgsign=false`; global signing and identity settings were not changed. Downloaded tools, environments, imported media, saved takes and caches remain local and ignored. Chart manifests and reproducible source/configuration are tracked.

The normal dashboard remains at loopback:8765 and the existing Tailscale URL. Its launchd service explicitly configures `BLENDER_BIN`; source defaults use PATH or `.tools/Blender.app`. There is no original-room scene dependency.

Validation of the combined features: 150 Python tests, recorder lifecycle checks, native frame/audio inspection and unchanged Blender/WebGL alignment checks. See the [optimization report](ipad-rhythm-planner-optimization.md), [recording notes](ipad-rhythm-webgl-export.md), and [porting guide](ipad-rhythm-environment-porting.md).

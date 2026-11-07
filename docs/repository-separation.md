# Independent simulator repository

The simulator is a standalone Git repository at ``, even though its local folder remains inside the room project. Its `.git` contains only simulator history and files. The parent `dafeiyu-3d-room` repository ignores this entire folder. Chair, room and dafeiyu animation work belongs only to that parent project.

The split retained all simulator source/runtime files in place and preserved the branch topology. The filtered histories have new commit IDs because room files were removed from their trees:

| Branch | Old parent commit | Simulator-only commit |
| --- | --- | --- |
| `main` baseline | `25f6003` | `2aa579c` |
| `feature/webgl-video-export` | `fdb5173` | `08101b4` |
| `perf/contact-planner` | `1cd19e2` | `260a5d9` |
| `integration/rhythm-updates` | `74a220b` | `bdb4d7f` |

Subsequent separation/documentation/chart-import commits extend the integration branch. Historical benchmark archives still identify the original baseline as `25f6003`; the packaged source snapshot remains runnable without Git and is unchanged. No planner, scoring, hand animation or presentation geometry was altered by the split.

Blender is a configurable external tool: set `BLENDER_BIN`, install `blender` on PATH, or place the application under `.tools/Blender.app`. This Mac's launchd service uses an explicit `BLENDER_BIN` pointing to the existing installation. It does not depend on the original room blend. ArcCreate, the virtual environment, imported packages, extracted media, saved takes and caches remain local and untracked.

Use `git -C prototypes/ipad-rhythm ...` from the parent folder, or work directly in this simulator folder. Future simulator documentation lives under this repo's `docs/`. The parent retains its own Git history and chair edits; those were neither moved nor included in simulator commits.

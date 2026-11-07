# Local chart library

Imported charts and package archives are local assets excluded from Git. Manifests under `manifests/` record their origin, contents and hashes.

`imported/10plus-import-novedio/` is the completed 379,909,860-byte ZIP collection downloaded on 2026-11-07. A verified copy of the original archive is in `packages/10plus-import-novedio.zip`. It contains **77 song folders and 268 AFF files**, plus audio/artwork and per-song `songlist` metadata. All ZIP CRC checks passed; all AFF files have chart headers and nonempty note/arc bodies. The inventory is `manifests/10plus-import-novedio.json`.

This pack uses **Arcaea song-folder format**, not `.arcpkg` or `.arcproj`. All **268 difficulties across 77 songs** are now imported into the dashboard alongside the two original projects. No Arcahv song was found in the collection.

To regenerate the local catalog and ArcCreate projects:

```sh
.venv/bin/python import_charts.py charts/imported/10plus-import-novedio
```

`imported/catalog.json` is the dashboard catalog. Each difficulty has its own `project.arcproj` under `imported/library/`, with relative links to the copied source AFF, audio, artwork and sound effects. This avoids duplicating the collection for every difficulty. Originals are unchanged. Difficulty-specific audio/artwork (including PRAGMATISM -RESURRECTION-) override base assets. The import is repeatable; restart the dashboard **when idle** after catalog changes. These generated local assets remain ignored by Git. Import results are recorded in `manifests/10plus-import-novedio-import.json`.

Current parser compatibility: **268/268 AFF files parse**. The original inventory records the initial check, before `designant` was supported; the import report records the current status. Forty-four charts have other feature warnings (for example extended lanes, scene controls, arc effects/resolution and timing-group attributes), so successful parsing alone does not guarantee full simulator compatibility. Source files remain unchanged.

All eight Designant/Lament Rain difficulties are now enabled. The parser represents `designant` as a red decorative trace whose sky taps have zero scoring weight. The dashboard has separate **Enable red notes** and **Target every red note** toggles. With targeting off, no extra contacts are planned, but visible red notes can be caught incidentally. With targeting on, the bounded search protects scoring ticks first, then maximizes red catches, then reduces movement cost. Unreachable red notes remain skippable even with ordinary misses disabled.

ArcCreate receives a per-take compatibility chart under that take's `arccreate/` folder. Red traces/taps are tinted red, use native sky-tap touch judgement, and produce particles/sounds without changing score, combo or score normalization. Red hits/misses are reported separately. Turning visibility off removes the red traces and their sky taps from this temporary render chart. Build the patched exporter with `./arc-create-export/setup.sh` before using these charts in a new environment; see the environment-porting guide for validation details.

# Local chart library

Imported charts and package archives are local assets excluded from Git. Manifests under `manifests/` record their origin, contents and hashes.

`imported/10plus-import-novedio/` is the completed 379,909,860-byte ZIP collection downloaded on 2026-11-07. A verified copy of the original archive is in `packages/10plus-import-novedio.zip`. It contains **77 song folders and 268 AFF files**, plus audio/artwork and per-song `songlist` metadata. All ZIP CRC checks passed; all AFF files have chart headers and nonempty note/arc bodies. The inventory is `manifests/10plus-import-novedio.json`.

This pack uses **Arcaea song-folder format**, not `.arcpkg` or `.arcproj`. All **268 difficulties across 77 songs** are now imported into the dashboard alongside the two original projects. No Arcahv song was found in the collection.

To regenerate the local catalog and ArcCreate projects:

```sh
.venv/bin/python import_charts.py charts/imported/10plus-import-novedio
```

`imported/catalog.json` is the dashboard catalog. Each difficulty has its own `project.arcproj` under `imported/library/`, with relative links to the copied source AFF, audio, artwork and sound effects. This avoids duplicating the collection for every difficulty. Originals are unchanged. Difficulty-specific audio/artwork (including PRAGMATISM -RESURRECTION-) override base assets. The import is repeatable; restart the dashboard **when idle** after catalog changes. These generated local assets remain ignored by Git. Import results are recorded in `manifests/10plus-import-novedio-import.json`.

Parser compatibility check: **260/268 AFF files parse without a blocking error; 8 are rejected**. The rejected files are listed in the manifest, with their exact diagnostics. Other charts include warnings for features outside the current planner model (for example extended lanes, scene controls, arc effects/resolution and timing-group attributes), so successful parsing alone does not guarantee full simulator compatibility. No chart contents were rewritten or filtered.

The eight Designant/Lament Rain difficulties use a `designant` arc type unsupported by both the current planner and installed ArcCreate parser. They appear as unavailable in the chart selector rather than silently dropping their arcs. Forty of the remaining charts show compatibility notices; those notices do not guarantee faithful support for every chart gimmick.

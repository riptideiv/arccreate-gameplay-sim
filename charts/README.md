# Local chart library

Imported charts and package archives are local assets excluded from Git. Manifests under `manifests/` record their origin, contents and hashes.

`imported/10plus-import-novedio/` is the completed 379,909,860-byte ZIP collection downloaded on 2026-11-07. A verified copy of the original archive is in `packages/10plus-import-novedio.zip`. It contains **77 song folders and 268 AFF files**, plus audio/artwork and per-song `songlist` metadata. All ZIP CRC checks passed; all AFF files have chart headers and nonempty note/arc bodies. The inventory is `manifests/10plus-import-novedio.json`.

This pack uses **Arcaea song-folder format**, not `.arcpkg` or `.arcproj`. It is extracted and inventoried without altering the files. It is not automatically added to the dashboard's two configured ArcCreate projects; playing these charts there will require importing/converting the selected song into an ArcCreate project. No Arcahv song was found in the collection.

Parser compatibility check: **260/268 AFF files parse without a blocking error; 8 are rejected**. The rejected files are listed in the manifest, with their exact diagnostics. Other charts include warnings for features outside the current planner model (for example extended lanes, scene controls, arc effects/resolution and timing-group attributes), so successful parsing alone does not guarantee full simulator compatibility. No chart contents were rewritten or filtered.

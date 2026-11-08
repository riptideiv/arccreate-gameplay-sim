# arccreate-gameplay-sim

[简体中文](README.md) · **English**

A tool for simulating two-finger play on AFF charts. It plans where each index finger presses and how it moves, replays those touches in ArcCreate to record judgements, and generates a 3D hand animation.

You can adjust motion settings in the browser, scrub or slow down the replay to inspect the hand order, and export video with audio. The planner builds on arc-hand-order, gameplay rendering and judgement use ArcCreate, and the 3D animation uses Blender and Three.js. Shared here for anyone interested in hand-order planning or gameplay animation.

## What it does

- Imports AFF song folders and groups charts by song and difficulty. You can generate a full song or a selected section.
- Lets you adjust movement speed, repeated taps, arc paths, contact positions, and wrist preferences to compare hand orders.
- Reports hits, misses, combo, and score from an actual ArcCreate touch replay.
- Shows the hand animation in a 3D preview with camera controls, seeking, and slow playback.
- Exports 30/60 fps video with gameplay audio and optional ArcCreate results endings. Blender rendering is also available.

Each hand currently uses one index finger. Planning considers movement speed, release gaps, and arm winding, but does not guarantee the best hand order. Planned skips and misses in the actual replay are reported separately.

## Installation

These steps are for macOS. Other systems need changes to the native ArcCreate export scripts; a complete installation workflow is not available for them yet.

### 1. Install the dependencies

| Dependency | Purpose |
| --- | --- |
| Git | Clone the repository |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.13 | Set up Python and run the planner and workbench |
| [Blender](https://www.blender.org/download/) | Generate the gloves, scene, and animation; render offline video |
| FFmpeg, including ffprobe | Read audio and process video |
| [Mono](https://www.mono-project.com/docs/getting-started/install/mac/) and Mono.Cecil 0.11 | Compile the ArcCreate input and export adapters |

The macOS Mono installer uses `/Library/Frameworks/Mono.framework/`, where the setup script looks for Cecil by default. Check that `mono`, `mcs`, `ffmpeg`, and `ffprobe` are available in your terminal.

### 2. Clone the project and install Python dependencies

```sh
git clone https://github.com/riptideiv/arccreate-gameplay-sim.git
cd arccreate-gameplay-sim
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e 'vendor/arc-hand-order[dev]'
```

Run the remaining commands from the repository root. You do not need to activate the virtual environment separately.

If `blender` is not on your PATH, specify its location. A typical installation in Applications uses:

```sh
export BLENDER_BIN="/Applications/Blender.app/Contents/MacOS/Blender"
```

### 3. Set up ArcCreate

The adapter currently targets [ArcCreate 1.2.68](https://github.com/Arcthesia/ArcCreate/releases/tag/1.2.68). Download the macOS build and extract it into the directory expected by the script:

```sh
mkdir -p .tools/ArcCreate/build
curl -fL https://github.com/Arcthesia/ArcCreate/releases/download/1.2.68/StandaloneOSX.zip \
  -o .tools/ArcCreate/StandaloneOSX.zip
ditto -x -k .tools/ArcCreate/StandaloneOSX.zip .tools/ArcCreate/build
./arc-create-export/setup.sh
```

Before running `setup.sh`, check that the app is at `.tools/ArcCreate/build/StandaloneOSX/ArcCreate.app`. The script creates an adapted copy at `.tools/ArcCreate/ArcCreate-media.app` for replay and export.

If Cecil is installed elsewhere, set `MONO_CECIL_DLL` to the path of your Mono.Cecil 0.11 DLL before running `setup.sh`.

### 4. Import charts

Bring your own charts and audio; song packs are not included. The bulk importer currently reads song folders with `songlist` metadata. It does not directly import arbitrary `.arcpkg` packages or individual `.aff` files.

Extract a collection under `charts/imported/`, for example:

```text
charts/imported/my-collection/
└── song-id/
    ├── songlist
    ├── base.ogg
    ├── base.jpg
    └── 2.aff
```

The `songlist` contains song information and difficulty entries matching the AFF filenames. Songs can have multiple difficulties and separate audio or jackets for each difficulty. See the [chart guide](charts/README.md) for format details.

```sh
.venv/bin/python import_charts.py charts/imported/my-collection
```

The importer generates the workbench catalog and ArcCreate projects. Import before starting the workbench. If it is already running, wait for the current task to finish and restart it to load the new catalog.

### 5. Start the workbench

```sh
./dashboard.sh
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/). The interface defaults to Simplified Chinese; use the language selector at the top to switch to English. Processing runs locally, with generated files saved under `output/`.

If the port is in use, choose another with `./dashboard.sh --port 8766`. See the [environment guide](docs/ipad-rhythm-environment-porting.md) for phone access and running the service in the background.

## Using it

1. Select a song and difficulty that you imported. Start with a few seconds to try it out, or select the full chart.
2. You can leave the motion settings at their defaults and click **Build live preview**. The program plans the touches, generates hand motion, and records an ArcCreate replay.
3. Play the preview when it finishes. Scrub or slow it down to inspect the hand order. Camera changes apply immediately.
4. Change the motion settings and build again to try another hand order. Chart, scroll speed, and audio changes also require a new build to take effect.
5. Click **Export preview video** and choose 30 or 60 fps. Keep the page visible while recording, then download the result. MP4 is used when the browser supports it, with WebM as a fallback.

Full-chart takes can include an ArcCreate results ending. **Export ending only** can reuse an existing body recording; see the [results ending guide](docs/native-results-ending.md). For offline rendering, expand **Blender MP4 export**.

Saved takes live in `output/runs/` and can be reopened in the workbench. Imported song packs, downloaded tools, and generated files are excluded from Git.

## Adjusting the hand order

These are useful settings to start with:

| What you want to change | Settings |
| --- | --- |
| Hand movement and turning speed | Maximum move speed and turn speed |
| Repeated taps with the same hand | Comfortable repeat gap and fast-run costs |
| Following arc centers or using more of the hitbox | Tracing preference and note judgement areas |
| Hand crossings, wrist strain, and arm winding | Crossing and wrist costs; winding limit |
| Skipping notes when motion limits are restrictive | Allow missed notes |

A plan is not proof of an optimal hand order. Some chart extensions and scene controls may not be fully supported. The workbench shows known compatibility notices, and the final score comes from the actual ArcCreate replay. Glove overlaps can still occur and are reported.

For implementation details and examples, see [contact placement](docs/contact-placement.md), [continuous acceleration](docs/continuous-acceleration.md), and [crossing and wrist costs](docs/crossing-wrist-costs.md).

## Running only the planner

You can generate a touch plan without gameplay footage or 3D animation. Only the Python dependencies are needed. A small test chart is included:

```sh
./plan.sh charts/desk-study.aff output/desk-study.plan.json
```

You can also plan a section of your own AFF chart. Start and end times below are in milliseconds; the output is JSON:

```sh
.venv/bin/python plan_excerpt.py path/to/chart.aff 0 10000 output/excerpt.plan.json
```

`plan.sh` uses the stricter upstream policy. The excerpt planner and workbench allow misses by default.

## Contributing

PRs are welcome; you do not need to open an issue first. Bug fixes, planner changes, interface improvements, support for other systems, and documentation are all welcome. Briefly explain what changed and how you checked it. Screenshots or video are useful for interface and animation changes.

For code changes, you can run these checks. The JavaScript checks require Node.js:

```sh
.venv/bin/python -m pytest vendor/arc-hand-order/tests tests -q
node tests/test_i18n.mjs
node tests/test_preview_recorder.mjs
node tests/check_planning_progress.mjs
```

`dashboard/` contains the browser interface and local server, `vendor/arc-hand-order/` contains the modified planner, and `arc-create-export/` contains the ArcCreate adapters. Hand motion is mainly in `hand_pose.py`, `motion_paths.py`, and `animate.py`.

The [documentation index](docs/README.md) links to more detail on [video export](docs/ipad-rhythm-webgl-export.md), [cache reuse](docs/cache-layers.md), [interface translation](docs/i18n.md), and [scene and animation validation](docs/ipad-rhythm-environment-porting.md). Most detailed documentation is in Simplified Chinese; the English and Chinese READMEs cover the same getting-started steps.

## Acknowledgments

Thanks to the authors and contributors of the open-source projects that make this work possible:

- **[ArcCreate](https://github.com/Arcthesia/ArcCreate)**, by Arcthesia and contributors, provides the chart editor and player. This project uses **1.2.68** for native gameplay rendering, touch judgement, audio, and results, with custom input and export adapters for recorded replays. ArcCreate is licensed under [GPL-3.0](https://github.com/Arcthesia/ArcCreate/blob/trunk/LICENSE).
- **[arc-hand-order](https://github.com/BFvX/arc-hand-order)**, by BFvX, provides the AFF hand-order and contact planner that this project's planning code builds on. The copy in `vendor/arc-hand-order/` starts from upstream commit [`73604ee`](https://github.com/BFvX/arc-hand-order/commit/73604eea75c088db39d332be5e6f77ab16d850aa), with extensions for score-aware paths, contact optimization, and motion costs. Its [upstream README](vendor/arc-hand-order/README.md) and [GPL-3.0 license](vendor/arc-hand-order/LICENSE) are retained.
- **[Three.js](https://github.com/mrdoob/three.js)** provides the browser's 3D preview and glTF loading. The bundled dependencies retain their [MIT license](dashboard/vendor/THREE-LICENSE).

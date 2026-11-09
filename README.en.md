# arccreate-gameplay-sim

[简体中文](README.md) · **English**

A tool for simulating two-finger play on AFF charts. It plans where each index finger presses and how it moves, replays those touches in ArcCreate to record judgements, and generates a 3D hand animation. I’m sharing it for anyone interested in hand-order planning or gameplay animation.

You can build a full chart or a short section, adjust repeated taps, crossings, arc tracing, and release timing, then slow down the preview to see what the hands are doing. The workbench shows planned skips separately from hits, misses, and score in the ArcCreate replay. You can adjust the preview camera and use Blender to export a 30 fps MP4 with gameplay audio.

## Installation

These steps are for macOS. Install Git, uv, Python 3.13, Blender, FFmpeg including `ffprobe`, and Mono with Mono.Cecil 0.11. The ArcCreate adapter uses `mono` and `mcs`, so check that both commands are available in your terminal. [Install uv](https://docs.astral.sh/uv/getting-started/installation/) · [Download Blender](https://www.blender.org/download/) · [Install Mono](https://www.mono-project.com/docs/getting-started/install/mac/)

### Clone the project

```sh
git clone https://github.com/riptideiv/arccreate-gameplay-sim.git
cd arccreate-gameplay-sim
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e 'vendor/arc-hand-order[dev]'
```

Run the remaining commands from the repository root.

### Set up ArcCreate

The adapter uses the macOS build of ArcCreate 1.2.68. Extract `StandaloneOSX.zip` so that `ArcCreate.app` is under `.tools/ArcCreate/build/StandaloneOSX/`, then run the setup script. [ArcCreate 1.2.68](https://github.com/Arcthesia/ArcCreate/releases/tag/1.2.68)

```sh
mkdir -p .tools/ArcCreate/build
curl -fL https://github.com/Arcthesia/ArcCreate/releases/download/1.2.68/StandaloneOSX.zip \
  -o .tools/ArcCreate/StandaloneOSX.zip
ditto -x -k .tools/ArcCreate/StandaloneOSX.zip .tools/ArcCreate/build
./arc-create-export/setup.sh
```

The script creates `.tools/ArcCreate/ArcCreate-export.app` for replay. If Mono.Cecil is outside the default Mono installation, set `MONO_CECIL_DLL` to the absolute path of the 0.11 DLL first.

### Add chart projects

This version of the workbench supports Ex-Otogibanashi and BIBBIDIBA. Supply your own charts and audio, and put the corresponding ArcCreate projects in these directories.

| Chart | Project directory |
| --- | --- |
| Ex-Otogibanashi | `charts/imported/ex-otogibanashi/` |
| BIBBIDIBA | `charts/imported/bibideba/` |

Each directory needs `2.aff`, a `project.arcproj` that points to that chart, and the audio and other assets referenced by the project. If you set up just one chart, select it in the workbench.

### Start the workbench

```sh
./dashboard.sh
```

Open the local workbench. It starts in Simplified Chinese. Use the selector at the top to switch to English. The browser remembers your choice. [Open workbench](http://127.0.0.1:8765/)

You can set the absolute path to Blender if it is installed elsewhere. If the port is in use, pass `--port 8766`.

```sh
BLENDER_BIN="/Applications/Blender.app/Contents/MacOS/Blender" ./dashboard.sh
```

## Using it

1. Select a chart and a time range. Start with a few seconds for a first try.
2. Keep the default motion settings and click **Build live preview**. Wait for the touch plan, hand animation, and ArcCreate replay.
3. Play, seek, or slow down the preview to inspect the hand order. Camera changes appear immediately. Build again after changing the chart or motion settings.
4. Click **Export MP4** to render the current view in Blender, then download the video. You can also download the gameplay footage on its own.

Takes are saved under `output/runs/` and can be reopened from **Recent takes**. The planner uses one index finger per hand and may skip notes when the motion limits are restrictive.

## Running only the planner

The Python dependencies are enough to try the planner with the included test chart. The start and end times are in milliseconds, and the plan is written as JSON.

```sh
mkdir -p output
.venv/bin/python plan_excerpt.py charts/desk-study.aff 0 10000 output/excerpt.plan.json
```

## Contributing

Feel free to open a PR directly. Bug fixes, planner changes, interface improvements, translations, and docs are welcome. Briefly say what changed and how you checked it. A screenshot or short clip helps for interface and animation changes.

```sh
.venv/bin/python -m pytest vendor/arc-hand-order/tests tests -q
node tests/test_i18n.mjs
node tests/check_planning_progress.mjs
```

`dashboard/` contains the browser interface and local server, `vendor/arc-hand-order/` contains the planner, and `arc-create-export/` contains the ArcCreate adapter. The JavaScript checks require Node.js.

## Acknowledgments

Thanks to the authors and contributors of these projects.

- **ArcCreate** provides gameplay rendering, touch judgement, and audio. This project uses version 1.2.68 through its input and export adapter. [Upstream project](https://github.com/Arcthesia/ArcCreate) · [GPL-3.0](https://github.com/Arcthesia/ArcCreate/blob/trunk/LICENSE)
- **arc-hand-order** is the basis of the planner. The copy included here builds on upstream commit `73604ee`. [Upstream project](https://github.com/BFvX/arc-hand-order/tree/73604eea75c088db39d332be5e6f77ab16d850aa) · [Included license](vendor/arc-hand-order/LICENSE)
- **Three.js** provides the browser’s 3D preview and glTF loading. [Upstream project](https://github.com/mrdoob/three.js) · [MIT](dashboard/vendor/THREE-LICENSE)

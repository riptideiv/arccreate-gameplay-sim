# arccreate-gameplay-sim

**简体中文** · [English](README.en.md)

这是一个模拟双手打 AFF 谱面的工具。程序会规划两根食指的落点和移动路线，用 ArcCreate 回放并记录判定，再把手部动作做成三维动画。

可以在浏览器里调整动作参数、拖动进度、慢放看手序，也可以导出带音频的视频。规划器基于 arc-hand-order，游戏画面和判定使用 ArcCreate，三维部分使用 Blender 和 Three.js。整理出来分享给对手序规划或打谱动画感兴趣的朋友。

## 能做什么

- 导入 AFF 歌曲文件夹，按歌曲和难度选择谱面，可以生成整首或其中一段。
- 调整移动速度、连打习惯、蛇的路径、落指位置和转腕偏好，比较不同设置下的手序。
- 查看 ArcCreate 实际回放得到的命中、失误、连击和分数。
- 在三维预览中调整相机、拖动进度或慢放，检查双手动作。
- 导出带游戏音频的 30／60 fps 视频，添加 ArcCreate 结算片尾，也可以用 Blender 离线渲染。

目前每只手使用一根食指。规划会考虑速度、松手间隔和手臂缠绕等限制，但不保证找到最好的手序；规划中跳过的音符和实际回放中的失误会分别显示。

## 安装

下面的步骤面向 macOS。其他系统需要适配 ArcCreate 原生导出脚本，目前还没有完整的安装流程。

### 1. 准备依赖

需要先安装：

| 依赖 | 用途 |
| --- | --- |
| Git | 下载仓库 |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python 3.13 | 创建 Python 环境、运行规划器和工作台 |
| [Blender](https://www.blender.org/download/) | 生成手套、场景和动画，离线渲染视频 |
| FFmpeg（含 ffprobe） | 读取音频、处理和导出视频 |
| [Mono](https://www.mono-project.com/docs/getting-started/install/mac/) 和 Mono.Cecil 0.11 | 编译 ArcCreate 的输入与导出适配器 |

Mono 的 macOS 安装包会安装到 `/Library/Frameworks/Mono.framework/`，适配脚本默认在那里查找 Cecil。安装后，确认终端能运行 `mono`、`mcs`、`ffmpeg` 和 `ffprobe`。

### 2. 下载项目并安装 Python 依赖

```sh
git clone https://github.com/riptideiv/arccreate-gameplay-sim.git
cd arccreate-gameplay-sim
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e 'vendor/arc-hand-order[dev]'
```

后面的命令都在仓库根目录运行，不需要另外激活虚拟环境。

如果 `blender` 不在 PATH 中，可以指定它的位置。下面是安装到「应用程序」后的常见路径：

```sh
export BLENDER_BIN="/Applications/Blender.app/Contents/MacOS/Blender"
```

### 3. 配置 ArcCreate

适配器目前对应 [ArcCreate 1.2.68](https://github.com/Arcthesia/ArcCreate/releases/tag/1.2.68)。下载 macOS 版本并解压到脚本预期的位置：

```sh
mkdir -p .tools/ArcCreate/build
curl -fL https://github.com/Arcthesia/ArcCreate/releases/download/1.2.68/StandaloneOSX.zip \
  -o .tools/ArcCreate/StandaloneOSX.zip
ditto -x -k .tools/ArcCreate/StandaloneOSX.zip .tools/ArcCreate/build
./arc-create-export/setup.sh
```

运行 `setup.sh` 前，确认应用位于 `.tools/ArcCreate/build/StandaloneOSX/ArcCreate.app`。脚本会生成用于回放和导出的应用副本 `.tools/ArcCreate/ArcCreate-media.app`。

如果 Cecil 安装在其他位置，先将 `MONO_CECIL_DLL` 设为 Mono.Cecil 0.11 的 DLL 文件路径，再运行 `setup.sh`。

### 4. 导入谱面

歌曲和音频需要自行准备，仓库不附带曲包。当前批量导入器读取的是带 `songlist` 元数据的歌曲文件夹，不能直接导入任意 `.arcpkg` 或单独的 `.aff` 文件。

将曲包解压到 `charts/imported/` 下，例如：

```text
charts/imported/my-collection/
└── song-id/
    ├── songlist
    ├── base.ogg
    ├── base.jpg
    └── 2.aff
```

`songlist` 应包含歌曲信息及与 AFF 文件名对应的难度。歌曲也可以有多个难度，或使用单独的音频、封面；具体格式见[曲库说明](charts/README.md)。

```sh
.venv/bin/python import_charts.py charts/imported/my-collection
```

导入器会生成工作台曲目列表和 ArcCreate 工程。导入完成后再启动工作台；如果工作台已经在运行，等当前任务结束后重启，让新曲目出现在列表中。

### 5. 启动工作台

```sh
./dashboard.sh
```

打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。界面默认简体中文，可以在页首切换 English。程序在本机处理谱面，生成结果保存在 `output/`。

如果端口已被占用，可以用 `./dashboard.sh --port 8766` 换一个端口。手机访问和后台运行的配置见[环境指南](docs/ipad-rhythm-environment-porting.md)。

## 怎么用

1. 选择自己导入的歌曲和难度，先选几秒钟的片段试跑，也可以点「整首」。
2. 可以先保留默认动作设置，点击「生成实时预览」。程序会依次规划触点、生成手部动作并录制 ArcCreate 回放。
3. 完成后播放预览。拖动进度或慢放查看手序，相机可以直接调整。
4. 如果想试其他手序，修改动作参数后再次生成。曲目、流速和音频设置的改动也需要重新生成才能生效。
5. 点击「导出当前预览视频」，选择 30 或 60 fps。录制时保持页面可见，完成后下载；浏览器支持时使用 MP4，否则使用 WebM。

完整谱面可以添加 ArcCreate 结算片尾。「仅导出片尾」可以复用已有正文，具体用法见[结算片尾说明](docs/native-results-ending.md)。如果需要离线渲染，可以展开「Blender MP4 导出」。

生成记录保存在 `output/runs/`，可以在工作台重新打开。导入的曲包、下载的工具和生成结果都不会被 Git 跟踪。

## 调整手序

不熟悉参数时，可以先只改下面几项：

| 想调整的效果 | 对应设置 |
| --- | --- |
| 限制手的移动和转腕速度 | 最大移动速度、最大转腕速度 |
| 改变同一只手连续点按的倾向 | 舒适连打间隔、快速连打代价 |
| 让蛇更贴近中心线，或更多利用判定区域 | 优先描线、利用音符判定区域 |
| 减少交叉、反拧和手臂缠绕 | 交叉与手腕相关代价、缠绕角限制 |
| 在动作限制较严格时允许跳过部分音符 | 允许漏键 |

规划结果有限制：它不是最优手序的证明，部分谱面扩展或场景控制也可能没有完整支持。工作台会显示已知兼容性提示；最终成绩以 ArcCreate 实际回放为准。动作中仍可能出现手套重叠，程序会报告这些情况。

具体参数的实现和示例见[判定区落指](docs/contact-placement.md)、[连续加速度](docs/continuous-acceleration.md)和[交叉与手腕代价](docs/crossing-wrist-costs.md)。

## 只运行规划器

如果只想生成触点计划，不需要游戏画面或三维动画，安装 Python 依赖后就可以运行。仓库里有一张小型测试谱面：

```sh
./plan.sh charts/desk-study.aff output/desk-study.plan.json
```

也可以规划自己的 AFF 片段。下面的起止时间单位是毫秒，输出为 JSON：

```sh
.venv/bin/python plan_excerpt.py path/to/chart.aff 0 10000 output/excerpt.plan.json
```

`plan.sh` 使用较严格的上游策略；片段入口和工作台默认允许漏键。

## 参与贡献

欢迎直接提 PR，不用先开 issue。修 bug、改规划逻辑、调整界面、适配其他系统或补文档都可以。PR 里简单说明改了什么、怎么验证的；界面和动画改动可以附上截图或视频。

涉及代码改动时，可以运行这些检查，需要另外安装 Node.js：

```sh
.venv/bin/python -m pytest vendor/arc-hand-order/tests tests -q
node tests/test_i18n.mjs
node tests/test_preview_recorder.mjs
node tests/check_planning_progress.mjs
```

`dashboard/` 是浏览器界面和本地服务，`vendor/arc-hand-order/` 是带本地修改的规划器，`arc-create-export/` 是 ArcCreate 适配器，手部动作主要在 `hand_pose.py`、`motion_paths.py` 和 `animate.py`。

更多说明见[文档目录](docs/README.md)，包括[视频导出](docs/ipad-rhythm-webgl-export.md)、[缓存复用](docs/cache-layers.md)、[界面翻译](docs/i18n.md)和[场景与动画验证](docs/ipad-rhythm-environment-porting.md)。文档主要使用简体中文，英文 README 与中文介绍保持同步。

## 致谢

感谢以下开源项目的作者与贡献者，为这个项目提供基础：

- **[ArcCreate](https://github.com/Arcthesia/ArcCreate)** — 由 Arcthesia 与贡献者开发的谱面编辑器和播放器。本项目基于 **1.2.68** 的原生渲染、触摸判定、音效及结算流程，通过自定义输入与导出适配器生成实际回放。上游采用 [GPL-3.0](https://github.com/Arcthesia/ArcCreate/blob/trunk/LICENSE) 许可。
- **[arc-hand-order](https://github.com/BFvX/arc-hand-order)** — BFvX 的 AFF 手序与触点规划器，是本项目规划代码的基础。`vendor/arc-hand-order/` 从上游提交 [`73604ee`](https://github.com/BFvX/arc-hand-order/commit/73604eea75c088db39d332be5e6f77ab16d850aa) 引入，并加入计分感知路径、接触点优化与动作代价等扩展；保留[上游说明](vendor/arc-hand-order/README.md)和 [GPL-3.0 许可证](vendor/arc-hand-order/LICENSE)。
- **[Three.js](https://github.com/mrdoob/three.js)** — 提供浏览器中的三维预览与 glTF 加载；随附依赖保留 [MIT 许可证](dashboard/vendor/THREE-LICENSE)。

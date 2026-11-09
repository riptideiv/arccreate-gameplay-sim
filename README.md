# arccreate-gameplay-sim

**简体中文** · [English](README.en.md)

这是一个模拟双手打 AFF 谱面的工具。程序会规划两根食指的落点和移动路线，用 ArcCreate 回放并记录判定，再把手部动作做成三维动画。整理出来分享给对手序规划或打谱动画感兴趣的朋友。

可以生成整首或其中一段，调整连打、交叉、描蛇和松手设置，再用慢放查看动作。工作台会分别显示规划时跳过的音符和 ArcCreate 实际回放的命中、漏键与分数。预览里可以调整相机，也可以用 Blender 导出带游戏音频的 30 fps MP4。

## 安装

下面的步骤适用于 macOS。需要 Git、uv、Python 3.13、Blender、FFmpeg（含 `ffprobe`）以及 Mono 和 Mono.Cecil 0.11。ArcCreate 适配器使用 `mono` 和 `mcs`，安装后确认这两个命令在终端可用。[uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/) · [Blender 下载](https://www.blender.org/download/) · [Mono 安装说明](https://www.mono-project.com/docs/getting-started/install/mac/)

### 下载项目

```sh
git clone https://github.com/riptideiv/arccreate-gameplay-sim.git
cd arccreate-gameplay-sim
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e 'vendor/arc-hand-order[dev]'
```

其余命令都在仓库根目录运行。

### 准备 ArcCreate

适配器使用 ArcCreate 1.2.68 的 macOS 版本。将 `StandaloneOSX.zip` 解压后，使 `ArcCreate.app` 位于 `.tools/ArcCreate/build/StandaloneOSX/`，再运行安装脚本。[ArcCreate 1.2.68](https://github.com/Arcthesia/ArcCreate/releases/tag/1.2.68)

```sh
mkdir -p .tools/ArcCreate/build
curl -fL https://github.com/Arcthesia/ArcCreate/releases/download/1.2.68/StandaloneOSX.zip \
  -o .tools/ArcCreate/StandaloneOSX.zip
ditto -x -k .tools/ArcCreate/StandaloneOSX.zip .tools/ArcCreate/build
./arc-create-export/setup.sh
```

脚本会创建用于回放的 `.tools/ArcCreate/ArcCreate-export.app`。如果 Mono.Cecil 不在 Mono 的默认安装目录，先把 `MONO_CECIL_DLL` 设为 0.11 版 DLL 的完整路径。

### 放入谱面工程

这个版本的工作台支持 Ex-Otogibanashi 和 BIBBIDIBA。谱面和音频需自行准备，把对应的 ArcCreate 工程放到下面的目录。

| 曲目 | 工程目录 |
| --- | --- |
| Ex-Otogibanashi | `charts/imported/ex-otogibanashi/` |
| BIBBIDIBA | `charts/imported/bibideba/` |

每个目录应有 `2.aff`、指向该谱面的 `project.arcproj`，以及工程引用的音频和其他素材。只准备一首时，在工作台选这首即可。

### 启动工作台

```sh
./dashboard.sh
```

打开本机的工作台。界面默认是简体中文，页首可以切换到 English，浏览器会记住选择。[打开工作台](http://127.0.0.1:8765/)

如果 Blender 安装在其他位置，可以指定可执行文件的完整路径。端口已被占用时，也可以加上 `--port 8766`。

```sh
BLENDER_BIN="/Applications/Blender.app/Contents/MacOS/Blender" ./dashboard.sh
```

## 使用方法

1. 选择谱面并划定范围。第一次可以先试几秒钟。
2. 保留默认设置，点击「生成实时预览」。等触点规划、手部动画和 ArcCreate 回放生成完成。
3. 播放、拖动进度或慢放检查手序。修改相机可以直接看效果，改动谱面或动作设置后需要重新生成。
4. 要保存视频时，点击「导出 MP4」，等 Blender 渲染完后下载。也可以仅下载游戏画面。

已生成的演奏会保存在 `output/runs/`，可以在「最近的演奏」中打开。每只手使用一根食指，遇到动作限制较紧的片段时，规划器可能跳过部分音符。

## 只运行规划器

只想看触点计划时，装好 Python 依赖即可用仓库中的测试谱面试跑。起止时间以毫秒为单位，结果写成 JSON。

```sh
mkdir -p output
.venv/bin/python plan_excerpt.py charts/desk-study.aff 0 10000 output/excerpt.plan.json
```

## 参与贡献

欢迎直接提 PR，不用先开 issue。修 bug、改规划逻辑、调整界面、补翻译或文档都可以。PR 里简单说说改了什么、怎么检查的。界面和动画改动可以附一张截图或一段视频。

```sh
.venv/bin/python -m pytest vendor/arc-hand-order/tests tests -q
node tests/test_i18n.mjs
node tests/check_planning_progress.mjs
```

`dashboard/` 是浏览器界面和本地服务，`vendor/arc-hand-order/` 是规划器，`arc-create-export/` 是 ArcCreate 适配器。JavaScript 检查需要 Node.js。

## 致谢

感谢这些项目的作者和贡献者。

- **ArcCreate** 提供游戏画面、触摸判定和音效。本项目通过输入与导出适配器使用 1.2.68 版。[上游项目](https://github.com/Arcthesia/ArcCreate) · [GPL-3.0](https://github.com/Arcthesia/ArcCreate/blob/trunk/LICENSE)
- **arc-hand-order** 是规划器的基础，随项目附带的版本从上游提交 `73604ee` 修改而来。[上游项目](https://github.com/BFvX/arc-hand-order/tree/73604eea75c088db39d332be5e6f77ab16d850aa) · [随附许可证](vendor/arc-hand-order/LICENSE)
- **Three.js** 用于浏览器中的三维预览和 glTF 加载。[上游项目](https://github.com/mrdoob/three.js) · [MIT](dashboard/vendor/THREE-LICENSE)

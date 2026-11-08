# iPad 音游双手模拟器

**简体中文** · [English](README.en.md)

用两只单指手套在虚拟 iPad 上演奏 Arcaea AFF 谱面：规划触点，用 ArcCreate 实际判定，再在浏览器中播放三维手部动作。可以调流速、点按节奏、描蛇偏好、待机速度与相机，直接导出带游戏音频的预览视频。

这是独立 Git 仓库。当前文件夹虽然位于 `dafeiyu-3d-room/prototypes/ipad-rhythm`，但房间、角色和椅子动画属于父仓库；这里的展示场景、源码和历史独立维护。原房间资产不参与新场景构建。

## 打开工作台

在本仓库目录中运行：

```sh
./dashboard.sh
```

默认地址为 `http://127.0.0.1:8765/`。现有 Mac 会话由 launchd 的 `com.dafeiyu.rhythm-workbench` 服务维持运行，不要重复启动同端口进程。服务带 `KeepAlive` 和 `caffeinate`；定义位于 `output/workbench.launchd.plist`，日志位于 `output/dashboard-server.log`。

```sh
launchctl print gui/$(id -u)/com.dafeiyu.rhythm-workbench
# 仅在没有规划、生成或导出任务时重新加载：
launchctl kickstart -k gui/$(id -u)/com.dafeiyu.rhythm-workbench
# 注销／重启后，从本目录加载现有定义：
launchctl bootstrap gui/$(id -u) "$PWD/output/workbench.launchd.plist"
```

Tailscale Serve 已将本机服务代理至现有的 tailnet HTTPS 地址，可从已登录同一网络的手机访问。地址和部署细节见[环境迁移指南](docs/ipad-rhythm-environment-porting.md)。重载前检查 `/api/state`，不要中断正在运行的任务。

界面默认简体中文；页首可切换 **English / 简体中文**，选择保存在当前浏览器。切换语言不重新加载谱面、视频或设置，不参与缓存键。浏览器的原生菜单、底层异常详情和 ArcCreate 自身画面可能保留原语言。

## 使用流程

1. 选择歌曲及难度。双端滑块分别控制起点、终点，点「整首」可选择完整歌曲；没有 20 秒上限，最短片段为 0.5 秒。
2. 调整动作与蛇的策略，点击「生成实时预览」。规划进度按目标数计算，不表示剩余时间线性减少。
3. 播放、拖动进度或慢放检查。相机修改立即生效；曲目、流速、音频与手部设置需再次生成。
4. 点「导出当前预览视频」录制当前 WebGL 画面及音频；可选择 30／60 fps，录制期间保持页面可见。完成后下载 MP4，浏览器不支持时使用 WebM。
5. 完整谱面演奏可点「添加结算片尾」，使用 ArcCreate 原生过场、结果入场动画、All Perfect 等原生等级和结算音乐；旧片尾可点「更新结算流程」升级。「仅导出片尾」复用正文，只录制末尾。详见[结算片尾](docs/native-results-ending.md)。
6. 如需离线 EEVEE 渲染，可展开「Blender MP4 导出」。旧演奏的「更新展示场景」只更新布景，不重新规划触点或渲染 ArcCreate。

最近的演奏保存在 `output/runs/<id>/`，包含设置、规划、60 Hz 动作、原生回放与判定、场景快照和日志。原生判定结果与「规划中跳过的物量」分别展示；片段边界及判定收尾也可能漏键。

## 设置与默认值

| 设置 | 默认值／作用 |
| --- | --- |
| 单手指数量 | 每只手一根食指；第二根手指及相关连打逻辑关闭 |
| 最大移动／转腕速度 | 1.6 m/s、360°/s；移动约束覆盖整只手套，包括转腕产生的位移 |
| 待机／避让速度 | 1 m/s，限制可选回位和绕行动作；赶键所需移动仍可使用主速度上限 |
| 抬指／期望间距 | 6 mm／1 mm；无法解决的重叠仅诊断，不阻止播放或触发重规划循环 |
| 舒适连打间隔／代价 | 167 ms／5；从上一次松开到下一次按下计算 |
| 快速连打阈值／免费次数／追加代价 | 133 ms／2／10；从按下到按下计算 |
| 点按／最短离屏／完整长按后离屏 | 2／1／4 帧；规划为 60 Hz，每帧约 16.7 ms |
| 蛇的路径 | 计分优先；允许在判定区域内等候、走捷径和逐计分点续接 |
| 优先描线／按到视觉尾端 | 均开启；几何偏离代价 1000/s，提前松蛇代价 1000/s |
| 地面长条按到尾端 | 开启，提前松开代价 500/s |
| 允许漏键／搜索宽度 | 开启／256；有界候选搜索，不保证全局最优 |
| 谱面流速 | 2.5；ArcCreate 1–10 档，只改变音符接近速度，不改判定时刻和歌曲节奏 |
| 歌曲延迟／击打音效 | 0 ms／开启；正延迟表示歌曲晚于画面，一个开关统一控制实际命中音效 |
| 红键显示／追打 | 显示开启／追打关闭；红键不计分，漏掉不掉连击 |
| 相机距离／俯视角／焦距 | 0.29 m／70°／30 mm，支持水平角度和精度调整 |

历史演奏显式选中后会恢复其原设置。新默认值通过版本迁移应用一次，保留当前曲目、范围及其他参数；不会每次刷新都覆盖后续手动调参。

几何偏离代价按时间累计：离中心线 20 mm 或离屏时收取完整代价，更近时按比例减小。计分与硬速度限制优先，视觉尾端偏好另行增加代价。长条／蛇的松手时刻同时用于规划和动画，不能在动画中偷偷多按或提早释放。

## 规划、判定与双手动作

蛇不要求全程精确描线。计分优先模式按 ArcCreate 的判定区域、提前宽限、颜色归属与抬指锁定生成候选路径。整条蛇不可行时可拆为逐计分点任务，松开再续接，只跳过无法接住的物量。优先比较漏掉的计分量，再比较人体工学代价；追打红键时，其优先级位于正常计分之后、动作代价之前。

原生 ArcCreate 输入适配器提供真实 down/move/up 事件并关闭自动演奏。漏键仍留在谱面中，由引擎显示失误；HUD、连击、分数、特效和音效均来自实际回放。`arc-tick-check.json` 对比规划中的接取与原生判定，另列意外接到的计分点。候选空间及搜索宽度有限，因此计划漏键不等于两根手指必定无法完成。

手套围绕固定指腹触点转腕。`hand_pose.py` 在有界前瞻范围内搜索左右手姿态，只有松开的手能抬高或绕行；按住的触点和待机固定样本不会漂移。长空闲段采用回位—等候—赶键，时间不足时缩小或取消可选回位。碰撞代理较保守，剩余重叠会报告，但不会导致无限重试。主速度和角速度限制仍需通过校验。

浏览器从 Blender 导出的 GLB 加载手套、平板与布景，按 `motion.json` 播放。视频媒体时间是唯一时钟，拖动、暂停和慢放保持同步。Blender 为 Z 向上，Three.js 为 Y 向上；转换坐标和旋转，并沿用线性 pitch/yaw 插值。暂停时只在画面、相机或姿态改变后重绘。

展示场景的唯一几何源为 `presentation_scene.py`：胡桃木桌面、织物垫、绿植、圆珠笔与圆角平板。木纹以图像材质打包，不依赖浏览器无法导出的程序化节点。屏幕有效尺寸、UV、触点与原相机接口不变，布景独立于 dafeiyu 房间。

## 曲库与依赖

本机已导入 77 首歌曲、268 张 AFF 谱面，加上最初两张，共 270 个选项。下载的谱面、音频、工具和生成结果均不提交 Git，只跟踪导入器和清单。源包未被修改。详见[曲库说明](charts/README.md)。

```sh
uv venv --python python3.13 .venv
uv pip install --python .venv/bin/python -e 'vendor/arc-hand-order[dev]'
.venv/bin/python import_charts.py charts/imported/10plus-import-novedio
```

规划器基于 [arc-hand-order](https://github.com/BFvX/arc-hand-order/tree/73604eea75c088db39d332be5e6f77ab16d850aa) 的 `73604ee`，按其 GPL-3.0 许可保留上游源码及本地补丁。Three.js 0.187.0 与 GLTFLoader 依赖本地提供，保留 MIT 许可证。第三方原始说明和许可证保持原文。

Blender 查找顺序：`BLENDER_BIN` → PATH 上的 `blender` → `.tools/Blender.app/Contents/MacOS/Blender`。本机 launchd 显式指定现有安装，构建不会载入原房间 blend。

## 命令行入口

从本仓库根目录执行：

```sh
./plan.sh charts/imported/ex-otogibanashi/2.aff output/ex-otogibanashi.plan.json
.venv/bin/python plan_excerpt.py charts/imported/bibideba/2.aff 3500 11750 output/bibideba-excerpt.plan.json
.venv/bin/python plan_excerpt.py charts/imported/ex-otogibanashi/2.aff 0 6650 output/ex-otogibanashi-excerpt.plan.json --prefer-same-hand
```

`plan.sh` 保留上游严格策略；片段入口和工作台默认允许漏键。片段处理保留跨边界音符的原计分时刻、拼接关系与音频起点，再裁剪占用范围。

AFF 画面来自 [ArcCreate 1.2.68](https://github.com/Arcthesia/ArcCreate/releases/tag/1.2.68)，本机工具位于 `.tools/ArcCreate`。`arc-create-export/` 修补独立应用副本，依赖 Mono、Cecil 0.11 和 FFmpeg。可通过 `MONO_CECIL_DLL` 指定库，`ARCCREATE_EXPORT_APP` 指定适配后的应用。

```sh
mkdir -p .tools/ArcCreate
curl -fL https://github.com/Arcthesia/ArcCreate/releases/download/1.2.68/StandaloneOSX.zip -o /tmp/ArcCreate-1.2.68.zip
ditto -x -k /tmp/ArcCreate-1.2.68.zip .tools/ArcCreate
./arc-create-export/setup.sh
./arc-create-export/export.sh charts/imported/bibideba/project.arcproj output/gameplay/bibideba.mp4 3500 12167
```

上面的四参数旧入口使用自动播放。工作台会加传第五个 `replay.json` 参数，使用真实触点判定。真实回放保留引擎 60 Hz 模拟、1148×802、60 fps 画面（旧自动播放入口为 30 fps）；导出器分别保存无声画面、歌曲和实际命中音效，再混音。歌曲可在内存中添加收尾静音，不改源文件。

几何更新使用工作台「更新展示场景」，或：

```sh
curl -fsS http://127.0.0.1:8765/api/presentation \
  -H 'Content-Type: application/json' -d '{"id":"your-take-id"}'
```

它异步更新演奏元数据与带版本的 `.blend`、`.glb`，保留原规划、动作和游戏画面。不要为纯布景更新调用 `animate.py`，那是新建动作的入口。

## 验证与维护

```sh
.venv/bin/python -m pytest vendor/arc-hand-order/tests tests -q
node tests/test_i18n.mjs
node tests/test_preview_recorder.mjs
node tests/check_webgl_alignment.mjs output/runs/TAKE
```

几何、Blender 对齐与实际视频的完整检查命令见[迁移指南](docs/ipad-rhythm-environment-porting.md)。搜索优化在三段 Bibideba 基准上取得 **1.92×** 总体加速，完整规划除计时字段外一致；共享姿态缓存也已独立验证。各缓存的键和失效条件见[缓存说明](docs/cache-layers.md)。

[文档目录](docs/README.md) · [Git 分支与合并记录](docs/rhythm-git-workflow.md) · [中文化维护](docs/i18n.md) · [视频导出](docs/ipad-rhythm-webgl-export.md) · [Arghena 漏键调查](docs/arghena-end-miss-and-idle-motion.md)

双臂现在默认限制袖口连线的累计缠绕角为 **±260°**；可在「手部动作」调整。松手后可绕远路主动解缠，运动代价仍计入轨迹选择，所有按住的触点保持固定。旧演奏会提示需要更新动作。详见[双臂缠绕与 Dantalion 验证](docs/arm-winding.md)。

默认开启[判定区落指](docs/contact-placement.md)：纵连可让双手在音符两侧交替落指，蛇可搜索共同静止接取区。实际接触、动画及 ArcCreate 回放保持一致；工作台「利用音符判定区域」可关闭作对照。

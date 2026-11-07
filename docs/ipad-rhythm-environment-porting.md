# iPad 音游模拟器：环境迁移指南

本指南记录将模拟器移植到其他布景、渲染器、浏览器或系统的接口与经验。每次迁移后更新经验记录。第一版展示迁移把 dafeiyu 房间替换为独立胡桃木桌面；原房间项目保持独立。

## 源文件与产物归属

此目录是模拟器独立仓库根目录。

| 文件／产物 | 职责与迁移边界 |
| --- | --- |
| `plan_excerpt.py`、`plan.json` | 原生触点坐标、归属和时序。纯布景迁移不能重新规划 |
| `hand_pose.py`、`motion_paths.py`、`motion.json` | 手套形状、姿态及采样动作。纯展示修改保留这些数据 |
| `animate.py` | 从 Blender 出厂空场景构建新演奏并烘焙动作，不加载房间 blend |
| `presentation_scene.py` | **唯一展示几何源**：圆角平板、桌面、垫、植物、笔、图像材质及 Blender 灯光 |
| `refresh_scene.py` | 升级已烘焙演奏的布景，复用手部网格、动画、相机与视频材质 |
| `dashboard/live-preview.js` | 加载生成的 GLB 和精确动作，将命名屏幕材质替换为视频；提供实时灯光，不单独重建道具 |
| `export_camera.py`、`render-preview.sh` | 复制选定快照，应用相机／精度，渲染 EEVEE 帧并复用已有游戏音频 |
| `output/runs/<id>/run.json` | 记录选中场景、动作地址、布景版本及最后 MP4；保存演奏以此元数据为准 |

原房间 `outputs/whale-room-final.blend` 等资产不加载也不修改。旧演奏保留原 `scene.blend`、`scene.glb`；更新会在旁边发布 `presentation-<revision>.blend/.glb`。旧导出文件留在磁盘，仅成功更新后清除失效的界面下载入口。

## 稳定场景接口

单位为米。Blender Z 向上，glTF／Three Y 向上：`(x,y,z) → (x,z,-y)`；旋转四元数也需等价转换，不能移动屏幕去弥补坐标转换错误。

- 屏幕世界中心 **(0.405, 0.802, 0.708)**，水平放置，本地 X/Y 对应原生触点 X/Y。
- 有效区域 **0.2297 × 0.1605 m**。其他设备从 `plan.device.screen_mm` 读取，避免渲染端再硬编码一份尺寸。
- 显示对象 **`Tablet display | ArcCreate`**。预览兼容 glTF 对空格／下划线的名称规范化。
- 动画根 **`Left index target`**、**`Right index target`**，各食指指腹中心即旋转支点。保留子级、网格及局部变换。
- 保留 **`Tablet chassis`**、**`Tablet bezel`**、**`Camera | Tablet play`** 和集合 **`90 Tablet rhythm prototype`**。
- 屏幕 UV 为仿射映射 `u=x/width+0.5`、`v=y/height+0.5`。圆角增加轮廓顶点与中心三角形，不把整张图挤入多边形。glTF 已转换 UV 约定，Three `VideoTexture` 用 `flipY=false`。
- 实时动画以媒体时间为唯一时钟，样本索引为 `(mediaTime + audio_offset_ms/1000)*60`。沿用 Blender 的 **pitch/yaw 线性插值**，不要换成四元数 slerp。
- 相机目标 **(0.405, 0.794, 0.728)**；距离、俯视角、水平角、36 mm 传感器与焦距投影和 `export_camera.py` 保持一致。画布／视频为 4:3。按现有相机布置道具，不擅自改相机掩盖构图问题。

### 删除旧布景前先检查父子关系

原 `Desk` 还挂在 `Room` 下，平板和手根是 `Desk` 子级并带 parent-inverse。先删桌子会破坏姿态接口。

1. 求值场景，记录世界矩阵、父级与动画数据。
2. 保留原型集合及其全部后代。根节点解除父级时用 `world = obj.matrix_world.copy(); obj.parent=None; obj.matrix_world=world` 保留世界变换。
3. 动画根需检查 `parent.matrix_world @ matrix_parent_inverse`。已检查快照中的基是单位阵，现有位置／Euler 关键帧已表示世界坐标。`detach()` 对意外的非单位动画基拒绝继续；其他环境应先采样烘焙世界变换再移除父级。
4. 只从生成快照移除周围环境，不编辑原房间资产。
5. 对比所有保留对象世界矩阵，运行烘焙动作对齐检查；只查第一帧不够。

## 圆角与材质

盒子 bevel 的圆角受厚度限制，不能在 10 mm 厚机身上做独立 12 mm 平面圆角。共享构建器挤出真正的圆角矩形轮廓，再单独添加细小边缘高光。机身半径 **12 mm**，边框 **11 mm**，显示 **3 mm**，与厚度独立。屏幕尺寸、中心与 UV 不变。

圆角屏幕仅遮住矩形图像四角约 **0.021%** 面积。必须把原生触点样本对照轮廓，并在真实画面中看分数 UI 和四角；不要缩图或重投影触点遮掩问题。

跨渲染器材质使用 glTF 支持的 Principled 基色、金属度、粗糙度与法线。Noise／Voronoi 等 Blender 程序节点不能直接作为导出格式。胡桃木生成确定性颜色及切线法线 PNG，连接到 Principled 并打包入 blend 和 GLB；资源目录带布景版本，避免旧纹理误用，无外部下载或房间纹理依赖。

Blender 面光没有对应的 glTF 点光表示。实时端使用半球填充和带阴影的暖色平行主光，ACES 色调映射；Blender 使用 AgX。几何和材质来源相同，高光、软阴影近似，因此两个端都要实际看。实时阴影用 1024² VSM、小范围模糊和紧凑阴影相机，轻微 normal bias 抑制接触条纹。视频屏幕在 WebGL 中不受光、不参与色调映射，不能让灯光改变游戏颜色。

## 保存演奏更新与缓存失效

工作台正常流程：选已完成实时演奏 → **更新展示场景** → 检查 → **导出当前预览视频** → 下载 MP4／WebM；也可展开 Blender MP4 离线导出。新建演奏自动使用当前布景。

`POST /api/presentation {"id":"<take-id>"}` 只对选定快照运行一次 Blender，不调用触点规划或 ArcCreate。成功后才发布新地址并清除 `export_video`／`export_view`；取消／失败保留旧的可用产物。布景错误与触点／计分验证分开处理。

布景版本是共享构建器的 SHA-256 前缀；GLB 用版本文件名防止浏览器与旧演奏误认为仍是同一资源。MP4 键包含视角与选定布景版本。导出打开 `run.presentation.blend`，未更新的演奏才回退 `scene.blend`。换一个 GLB 不会自动换掉 Blender 快照或旧 MP4。

纯几何迁移前后保存 `plan.json`、`motion.json`、`gameplay.mp4`、`judgements.json` 的散列清单，要求字节不变。触点规划、共享姿态、原生画面、混音与导出各自有缓存边界，详见[缓存层说明](cache-layers.md)。

## 验证步骤

从仓库根目录执行，替换实际演奏与版本文件名：

```sh
BLENDER="${BLENDER_BIN:-.tools/Blender.app/Contents/MacOS/Blender}"
TAKE=output/runs/20261107-000550-9a4498
SCENE="$TAKE/presentation-50aa44fcc9c4.blend"
"$BLENDER" --factory-startup -b "$SCENE" --python-exit-code 1 -P tests/check_presentation.py -- "$TAKE"
"$BLENDER" --factory-startup -b "$SCENE" --python-exit-code 1 -P tests/export_alignment_reference.py -- "$TAKE"
node tests/check_webgl_alignment.mjs "$TAKE"
"$BLENDER" --factory-startup -b --python-exit-code 1 -P tests/check_preserved_scene.py -- "$TAKE"
.venv/bin/python -m pytest tests/test_dashboard_export.py tests/test_presentation_workflow.py -q
```

原对齐测试保持原样。布景审计包含：显示尺寸／UV、触点圆角遮罩、旧房间／桌子清除、根节点脱离、平板与道具三角形相交，以及全段 60 Hz 手套／袖口真实网格与道具相交、手与垫面间距。这些是所查演奏上的有限检查；新谱面和新手形仍需独立运动包络审计，不能宣称所有未来姿态均无碰撞。

在浏览器看默认相机、近距离交叉手、高低与左右视角、广角和手机宽度。极端变焦／斜角允许边缘道具裁切，但不能挡屏或偷偷自动改用户相机。检查真实视频帧及音频，不以自动播放截图代替真实触点 WebGL 证据。

测性能需记录机器、视口、精度、画布尺寸、GLB 大小、**播放时** FPS 和帧间隔 p95。画布有相应 `data-*` 字段；静止重绘率没有意义。桌面模拟手机宽度不代表真机 GPU 性能。

## 部署与系统差异

- 本 Mac 用 `BLENDER_BIN` 显式选 Blender **5.2.3 LTS**，Python 为本仓库 `.venv`。不同 Blender 版本需检查 EEVEE／动画 API，不能假设相同。
- Blender 查找：`BLENDER_BIN` → PATH → `.tools/Blender.app`。launchd 中可指向已有共享安装，但源码不假设父工作区路径；保持相机投影与 FFmpeg 混流一致。
- 新游戏画面才需要原生 ArcCreate；纯展示迁移需要已完成演奏、Blender、Node 和 FFmpeg。
- 服务 `com.dafeiyu.rhythm-workbench` 绑定 loopback:8765，Tailscale Serve 提供 `https://macbook-pro---riptideiv.tail57056e.ts.net/`。先看 `/api/state`；不要为静态界面改动中断活动规划／导出。新增静态文件若不在服务白名单，必须等空闲后重载。
- Linux／其他引擎保持名称大小写、相对视频路径、打包图像、30 fps 视频和 60 Hz 动作。测试 HTTP byte-range、移动端拖动。无头 Blender 可能需要明确图形后端；实际渲染确认，不能仅凭配置旗标推断 GPU 生效。
- 保留旧房间和历史快照。后续环境修改共享布景构建器并更新保存演奏，不分叉维护浏览器道具。

## 经验记录：展示迁移（2026-11-07）

- 先检查真实层级再脱离；初次保留矩阵最大差 **5.96e-8**，再次布景更新在比较容差内完全一致。
- 用轮廓网格替换受厚度约束的盒子 bevel，UV 和触点映射保持。
- 植物一次缩放把已按世界坐标建模的网格又平移了一遍，出现漂浮／裁切；真实 Blender 和 WebGL 检查发现后修复局部／世界坐标处理，并按现有相机缩小、移位。对象变换和网格坐标必须分开考虑。
- 两端检查后分别调整木材和灯光；程序节点存在不等于材质可移植。
- 复用 BIBBIDIBA **65–75 s**，包含媒体约 **9.317 s** 最近交叉手，规划／动作／游戏／判定 SHA-256 全不变，原 **76/76** 蛇计分点仍有效。
- 首段 **637** 姿态、**883** 触点，无裁切或道具相交；手至垫顶至少 **12.82 mm**。Blender／Three **77** 个子帧对齐检查，位置最大误差 **6.82e-8 m**、旋转 **1.89e-7 rad**、投影 **3.69e-7**。
- Apple M2 Max 手机尺寸 **375×812**（布局 360 px），Draft **324×243**，约 **120 fps／p95 9.2 ms**，当时另有真实 EEVEE 导出。GLB 约 **4.5 MB**，67 draws／145,206 三角形（含阴影）。这是桌面 GPU 证据，不是真机基准。
- 正常导出得到 H.264 **640×480／30 fps**、视频 **10.633 s**、AAC 音频，检查了普通命中和交叉手帧。证据在 `output/presentation-qa/` 及演奏的 `presentation-check.json`／`alignment-check.json`。
- 扩展至 **120–150 s** 演奏，**1,837** 姿态、**1,975** 触点，零道具相交／裁切、**209** 对齐检查。两段合计 **2,474** 姿态、**286** 对齐检查，当时项目测试 **142** 通过。
- 原／新快照的手部网格坐标、拓扑、动画关键帧字节相同。曲线转换前显式取消其他对象选择，避免道具转换时误应用手部修改器。
- 出厂空场景试建复用原规划／视频、不加载房间，按住触点和转腕匹配；从头重建动作曾使一个旧缓存的空中抬指变化 **4 mm**。因此纯环境迁移使用 refresh，保留烘焙曲线，不用新动作构建器。

最终布景版本 **`50aa44fcc9c4`**。验证视频 `output/runs/20261107-000550-9a4498/preview-2e27af8e3808.mp4`，流审计 `output/presentation-qa/export-streams.json`，截图 `webgl-default.png`、`webgl-cross-hand.png`、`mobile.png`。Tailscale 的 GLB／MP4 范围请求返回 HTTP **206**。

## 经验记录：WebGL 录制

主导出改用同一 WebGL 画布 `captureStream(30)`，通过 Web Audio 将同一游戏媒体元素音频送入 MediaRecorder。无新场景、无游戏重渲染，当前相机／灯光／材质原速实时录制，结束后恢复原播放状态。页面隐藏时取消，避免后台降频产生坏视频。

运行时检测 MP4，回退 WebM。H.264 曾把奇数 **859 px** 画布裁成 **858 px**；现录制前冲刷 ResizeObserver，改用宽 8 像素对齐且高为偶数的完整 4:3 画布，锁定大小至清理结束。音频图每媒体元素只建一次，结束只停克隆音轨，保留后续播放与导出。无需麦克风／屏幕共享权限。

浏览器下载随关页失效，Blender 仍保留服务器文件。移植到新浏览器／系统必须用 ffprobe 检查真实文件并听音／比对音频，不能仅靠特性检测。前台渲染和编码会有启动延迟、帧间隔波动。实测数据见[视频导出说明](ipad-rhythm-webgl-export.md)。

## 经验记录：仓库、曲库与音频

模拟器有独立 `.git` 和 `docs/`，父房间仓库忽略此目录；椅子／房间不在本历史。基准打包源在 `--workspace` 指向本仓库时不依赖 Git 可运行。运行资源与 Tailscale 地址未移动，见[仓库拆分](repository-separation.md)。

曲库复制后运行 `.venv/bin/python import_charts.py charts/imported/10plus-import-novedio`，生成每难度工程与 `catalog.json`。迁移时同时保留 `library/`、原始解压目录和相对符号链接，或在目标重生成；ffprobe 用于实际音频时长，难度音频覆盖也需保留。目录按曲包原子替换，可重复执行。

全部 **268** 原始／工程 AFF 散列对照初始清单通过。初次有八张 `designant` 被禁用，现通过零分红键兼容层全部启用；当前 **44** 张仍有其他功能警告。导入可解析不等于所有 gimmick 完整支持。清单在 `charts/manifests/`。

端到端曾发现：Build 必须发全部表单字段（含谱面和起止），只发相机字段会回退默认曲目；Blender 准备用 `-b` 才会执行后退出，背景模式 `bpy.context.screen` 可为空。目录更新先等空闲再重载。

红键兼容层在临时 AFF 使用红色渲染与原生天键判定，零分、不掉连击；原始谱面不动。新环境运行 `./arc-create-export/setup.sh`，默认适配应用为 `.tools/ArcCreate/ArcCreate-media.app`，可用 `ARCCREATE_EXPORT_APP` 覆盖。检查实际红色画面、独立红键命中／漏掉字段、正常分数归一化与空输入对照，不能仅看到红色就认为计分已正确隔离。

音频导出为原生无声画面、原采样率歌曲 WAV、48 kHz 命中 WAV 和声音事件 JSON。歌曲带两端 2 秒余量用于 ±2000 ms 延迟；只移动歌曲，命中音效与画面固定。一个开关包含单键、长条头、蛇实际计分点和捕获红键；漏键静音，保留自定义音效。原生蛇头亮起事件不冒充计分点音效。流速映射为原生 `DropRate=round(scroll_speed*30)`，不改谱面时间。

## 经验记录：中文化

中文 UI 仅显示层，语言不进入设置请求或缓存键。文本节点与动态状态均保存英文源，切换不重建表单或播放对象；歌曲元数据和机器报告保留原值。新增 `/i18n.js`、`/i18n-messages.js` 时，旧服务白名单返回 404；确认活动演奏完成后才重载服务，未取消任务。后续新增资源要同时检查静态路由和真实加载，不能只过语法检查。简体中文／English、桌面／手机断点的验证方法见[中文化维护](i18n.md)。

## 经验记录：负音频起点与 60 fps

谱面起点与歌曲起点并不相同。负 `AudioOffset` 的谱面从 0 演奏会要求负音频起点；不要拒绝或强行夹取到 0，原生渲染器可处理前置静音。应同时检查最初一秒的画面时钟、音乐静音长度和首个触点。原生错误应在退出内存报告之前提取并显示，否则界面只能报通用失败。

真实回放现在保留原生 60 fps，提取无声视频用 stream copy。迁移缓存必须保留导出脚本源码指纹。WebGL 捕获可选 30／60 fps，检查实际下载文件的帧时间戳／平均速率，确认原生输入不是先降到 30 fps 再重复帧。Blender 离线时间轴仍为 30 fps，不能仅把 FFmpeg 的输入帧率改成 60，那会把动作加速两倍。

## 经验记录：原生结果场景与局部片尾

ArcCreate 桌面构建排除 Selection 控制器，但保留 Result 场景资产。可直接加载原生场景、复用布局／徽章并填入已保存的真实判定，无需重演整首。平板比例的 CanvasScaler 需按宽度匹配，否则底部分数被裁切；原生封面 RawImage 路径为 `InfoCanvas/Jacket/Jacket`。移植时实际检查字体、标题、分数、计分表和原生等级，而非只判断视频文件存在。详见[结算片尾说明](native-results-ending.md)。

局部导出从 `ending.start_s` 开始，手部和相机仍沿原采样时钟。把短片与相同相机／尺寸／60 fps 的既有成片拼接，正文在安全的 GOP 边界之前 stream copy，逐帧 MD5 验证；边界到片尾之间最多一个 GOP 从旧视频解码重编码，再接新尾段。任意 B 帧位置直接裁切会造成重复显示时间戳。最后规范解码时钟、保持显示时刻，再检查全段每帧间隔与实际解码。等级按 ArcCreate 的 `PlayResult.ClearResult` 顺序判断；全 GOOD 对应 All Good，含 GOOD 的普通全连对应 Full Combo。

原生结算过场采用 `StorageData.SwitchToResultScene` 的同一序列：OnShow 的 TriangleTileTransition，加 OnBoth 的 DecorationTransition，然后播放 Result 主 ScriptedAnimator。三角背景只显示、不在 Hide 阶段消失，必须位于结果画布下面。编辑器将过场绑定到 Gameplay UI 相机的 Topmost 图层，片尾需要恢复原 Transition 相机／Default 图层；还要先完成一次 HideTriangleTile，把编辑器启动后的不透明材质重置成正常演奏时的透明状态，否则第一帧会直接跳白。不要逐个启动子动画器：只启动 InfoCanvas 的主动画器。按实际捕获帧记录结果激活时刻，用它对齐原生音乐，避免凭固定时长猜测。

### 批量导出与资源调度

2026-11-07 的四谱面批次在本机 32 GB／12 核配置上，采用最多两个并行触点规划，ArcCreate 原生渲染与浏览器录制依次执行。完整谱面的单个规划进程实测可占约 6.7 GB，不能只按 CPU 核数决定并行数。检查内存压力和系统温度提示后再增加并行任务；本批次未观察到系统温度／性能警告，856×642 录制的预览帧耗时 P95 约 9 ms。

可以在其他演奏生成时预热正常 `output/contact-plans/` 缓存；后台参数必须与正式演奏一致，包括谱面绝对路径、范围与规划偏好。低优先级的 `animate.py --motion-only` 也能预热 `output/hand-poses/`，不需要 GPU 画面；正式工作台继续校验并复用缓存。不要另外维护一份演奏管线，或在后台替换当前活动演奏的文件。

录制结束后下载原始浏览器文件，再检查实际帧数、帧间隔、尺寸、完整解码和音轨。MediaRecorder 的 60 fps 是目标值；必要时把实际录制规范为恒定 60 fps，保留同一音画时钟。音频波形相关只测得相对源素材的延迟，不能单独证明音画失步：浏览器启动可能同时延迟声音和画面。应结合投影还原后的屏幕动画、原生片尾时间证书与声音进行比较。全片导出已包含结算时，无需再单独录一遍片尾。

### 双臂缠绕与动作插值

手臂连接点应使用实际袖口对象，而非指尖或屏幕像素。缠绕角在平板世界平面中累计，不能受相机角度影响；Blender 的 `cuff`／`cuff.001` 随对应 `index target` 父节点运动。移植后独立读取这些对象的世界坐标，检查全曲展开角度，以及地面长按释放后的解缠。姿态之后不能再添加未参与认证的横向避让；按当前俯仰／偏航插值复核连续轨迹，不替换成四元数 slerp。Dantalion 的验证方法和缓存要求见[双臂缠绕说明](arm-winding.md)。

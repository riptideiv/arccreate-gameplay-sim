# 原生结算流程与局部导出

完整演奏可点击「添加结算片尾」，旧版本片尾可点击「更新结算流程」。随后可正常导出整段 WebGL 预览，或用「仅导出片尾」只录制短尾段。片尾要求演奏从 0 开始，且原生判定覆盖完整谱面。

## ArcCreate 的原生流程

遵循 ArcCreate 1.2.68 `StorageData.SwitchToResultScene` 的顺序：

1. `TransitionSequence.OnShow` 的 `TriangleTileTransition` 与 `OnBoth` 的 `DecorationTransition` 同时进入。
2. 过场覆盖后显示原生 `Result` 场景、启动其音乐，并调用 InfoCanvas 主 `ScriptedAnimator.Show()`。
3. `sequence.Hide()` 撤走装饰；三角背景保留在结果页下方。封面、计分表、分数与徽章按原生动画入场。
4. 动画完成后保留结果页 6 秒，作为有限长度视频的结束。

不再添加自制的完成横幅，也不使用 Arcaea 的结果术语。等级严格按原生 `PlayResult.ClearResult` 的优先级：Max → All Perfect → All Good → Full Combo → Clear → Fail。Gauge 必须严格大于通关线才是 Clear。Arghena 示例为 **All Perfect**：MAX 1433、Late Perfect 11、GOOD／MISS 0、总物量和最高连击均为 1444、分数 10,001,433。

原生 `ResultScreen.ogg` 在结果场景激活的实际捕获帧开始，沿用场景音量 1；之前继续原有游戏音轨。不加入自制横幅、渐弱、半音量或转场音效。原生结果序列本身没有 SoundTransition。

## 桌面适配与真实数据

桌面构建保留 `Result` 场景资产，但排除了 Selection 的完整控制器。适配器复用现有字体、布局、等级显示组件、动画器与 SceneTransition 模块；从已保存的原生判定填入结果，并按上述原生调用顺序执行。预加载场景避免等待不存在的 ResultSceneRepresentative；不会重新演奏触点。

NoteCount 通过保存的原生记录核对；旧记录可用原生分数、MAX 加分和判定台账交叉验证。片尾还实际加载原谱、读取引擎 NoteCount 再次检查。完美片段不能伪装成完整演奏。最大连击由判定序列重建。红键不计入评分；未记录的个人最佳、游玩次数和偏移统计保持隐藏。

平板 1148×802 的 CanvasScaler 改为按宽度匹配，避免裁掉底部分数。编辑器过场相机的 Topmost 绑定需恢复为原 Transition 相机和 Default 图层；三角层保持在结果画布下方。启动后还需先调用一次原生 HideTriangleTile，恢复正常演奏的透明材质状态，否则会在第一个捕获帧跳白。只调用 InfoCanvas 主动画器，不能再单独启动其所有子动画器。

## 局部重渲染与缓存

`dashboard/ending.py` 在最后一次原生判定后留 1.2 秒，替换多余等待尾段。规划、原生触点回放、动作、三维场景和相机保持原样。片尾长度、音乐起点和动画完成时刻来自原生捕获证书，而非固定时长猜测。

`POST /api/ending {"id":"<take-id>"}` 使用正常工作台队列；活动任务时拒绝并发。成功后才发布新 `run.video`、`ending.flow=arccreate-native-v1`，清除旧导出入口；失败保留可用预览。源码、结果、接缝和原媒体路径共同决定 `ending-<revision>/` 指纹。版本化媒体 URL 同时使浏览器录制缓存失效。历史导出保留，不冒充当前结果。

「仅导出片尾」从 `ending.start_s` 开始录制相同 WebGL 画面及游戏音频，结束恢复原预览时间、速度和播放状态。带结果页的导出目前使用 WebGL；旧 Blender 导出按钮禁用，避免生成不含后加片尾的视频。

将短片拼回已导出的 **同尺寸、同相机、60 fps H.264** 成片：

```sh
.venv/bin/python dashboard/ending.py output/runs/TAKE \
  --finalize /path/to/ending-webgl.mp4 \
  --body /path/to/original-60fps.mp4
```

输出 `preview-60fps-arccreate-results.mp4`，拼接证据在 `ending-<revision>/final-export.json`。正文在安全的 GOP 边界前直接复制视频流；仅解码重编码不足一个 GOP 的接缝和新短片。任意 B 帧位置直接裁切会导致缺帧／重复显示时间戳。最后使用 `setts=pts=PTS:dts=N/(60*TB)+STARTDTS:duration=1/(60*TB)`，必须显式保留 PTS，再验证实际解码帧数与每帧 1/60 秒间隔。

## 迁移与验证

运行 `./arc-create-export/setup.sh` 重建本地适配器；保留同版本源码的 `Assets/AudioClips/ResultScreen.ogg` 和 SceneTransition／UniTask 模块。源程序集不改动，适配器仅写入独立的 ArcCreate-media.app。

验证需同时观看透明过渡的前几帧、装饰进出、结果入场中途与完整结果页，并检查实际 WebGL 及最终导出；不能只判断文件存在。结果音轨起点以证书 `results_start_s` 为准。场景切换的层级、相机、字体、CanvasScaler、截图方向和尺寸都是移植检查项。三维触点与相机不改动，也不重新搜索整首谱面。

### 2026-11-07 实测

沿用 Arghena `20261107-052526-611446`，在 **151.5 s** 开始原生过场；结果页于片尾 **1.0333 s** 激活，完整入场于 **3.0333 s** 完成，再停留 6 秒。新尾段共 **542 帧／9.0333 s**，原生片尾缓存为 `ending-82c717843df0`。

通过工作台实际录制短尾段后，与原 60 fps 正文拼接成 **160.5333 s、856×642、60 fps、9632 帧 H.264／AAC** 成片。前 **9000 帧（150 s）**逐帧 MD5 完全一致；接下来的 90 帧只是安全拼接接缝重编码，没有三维重渲染。plan、motion、judgements 和原成片的 SHA-256 均不变。实际解码每帧间隔 1/60 秒，无解码错误；Tailscale 视频范围请求返回 HTTP 206。

检查了透明过场、原生入场中途、实时 WebGL 结果页和真实成片帧。证据：`output/canonical-results-qa/`、演奏目录的 `canonical-ending-verification.json` 与片尾目录的 `final-export.json`。完整 Python 测试 **186 项通过**，Node 录制器和 346 条界面翻译检查通过。

### 短音轨的过场补齐

当原演奏音轨早于结果场景激活时刻结束，必须补静音至原生证书给出的时刻，再拼接 ResultScreen 音乐。仅对短音轨 atrim 后直接 concat 会使音乐提前；本批次在 Breach／Extradimensional 分别发现约 0.85／0.40 秒的提前。修复只需重混音并复用原生片尾画面、触点和三维视频；新版本媒体 URL 使浏览器缓存失效。真实 FFmpeg 回归检查演奏声音、静音间隙和结果音乐三个区间；新增回归后完整 Python 测试 187 项通过。

最终 MP4 还可以复用与预览完全相同的游戏音轨，按实测的画面启动延迟对齐，避免把 Web Audio 输出延迟写入成片。重混音时复制视频流并比较其 SHA-256，确认画面字节和帧时钟未改变；保留原始浏览器录制供追溯。

# 原生结算片尾与局部导出

工作台已完成演奏可点击「添加结算片尾」，然后用「仅导出片尾」录制短片，或正常导出整段预览。当前片尾用于从 0 开始且原生判定已覆盖完整谱面的演奏；完美片段不冒充整曲 Pure Memory。

## 原生与适配部分

ArcCreate 1.2.68 有 `Result` 场景，但 `ArcCreate.Selection` 程序集明确排除桌面平台，桌面编辑器没有完整 `ResultScreen` 控制器。适配器加载已有场景，复用原生布局、字体、封面框、等级与 ClearResult 徽章，填入保存的原生判定。原生等级名称为 **All Perfect**；前置 **Pure Memory** 横幅使用 Arcaea 的术语，由同一实际结果决定。

`arc-create-export/Ending.cs` 只生成片尾，不加载触点回放或重新演奏。横幅 2.2 秒、结果页 6 秒，以 60 fps 截取。所有 PERFECT（含非 MAX PERFECT）才显示 Pure Memory；有 GOOD 而不断连显示 Full Recall；存在 MISS 不显示上述标记。最大连击从原生判定序列重建，不把最终连击误作最大连击。示例 Arghena 为 MAX 1433、Late Perfect 11、GOOD／MISS 0、总物量 1444、最高连击 1444、分数 10,001,433。

已保存的旧报告没有单独 NoteCount 时，以原生计分公式、MAX 额外分和判定台账交叉验证分母；无法可靠确认或未覆盖全谱时拒绝。片尾渲染器还加载原生谱面，读取引擎的真实 NoteCount 再次核对，不需要模拟或重放触点。结果不伪造个人最佳、游玩次数或偏移统计，因此这些区域不显示。红键不会计入计分台账。

## 只更新末尾

`dashboard/ending.py` 在最后一次原生判定后留 1.2 秒，再替换原来多余的等待尾段。原先的游戏画面、规划、动作与判定保留。新的无声视频前段直接复制视频流，原歌曲在横幅段渐弱，结果页使用 ArcCreate 自带 `ResultScreen.ogg`。音频重新混合很快，不渲染前段视频。

`POST /api/ending {"id":"<take-id>"}` 由正常工作台队列执行；活动任务时拒绝并发。成功后才发布新 `run.video` 与 `ending` 元数据，并清除旧离线 MP4 入口；失败／取消保留原预览。片尾资产放在带源码和结果指纹的 `ending-<revision>/`，不覆盖原游戏缓存。

片尾在 WebGL 中仍使用同一 GLB、相机和手部样本。录制器 `startTime` 从片尾起点录起，结束恢复原预览状态。当前带结算的导出使用 WebGL；Blender 旧离线入口不导出该后加片尾，界面会说明并禁用该动作，避免下载不含结算的旧画面。

要把局部录制拼回已经导出的 **60 fps、同尺寸、同相机** 成片：

```sh
.venv/bin/python dashboard/ending.py output/runs/TAKE \
  --finalize /path/to/ending-webgl.mp4 \
  --body /path/to/original-60fps.mp4
```

只重编码短尾段和它前面不足一个 GOP 的编码接缝，较早正文视频流复制，音轨拼接混合，产物为 `preview-60fps-with-results.mp4`。`ending-<revision>/final-export.json` 记录来源与拼接点。修改相机或尺寸后，旧正文不能直接复用。

## 2026-11-07 验证

沿用 `20261107-052526-611446`，片尾起点 **151.5 s**，新尾段 **8.2 s**，最终 **159.7 s、856×642、60 fps、9582 帧、H.264／AAC**。原最后判定为 150.300 s；1444 次命中、0 漏键的原生台账完全不变。

原生结果页先实际检查，再检查真实 WebGL 和导出帧中的 Pure Memory、封面、分数、1444 PERFECT 与 0 GOOD／MISS。调整 CanvasScaler 为按宽度匹配，避免在 1148×802 平板比例下裁掉底部分数；封面和原生徽章均可见，双手保持原动作。场景、相机与接触映射没有更改。

完整项目测试 **180 项通过**，另有两种真实 FFmpeg 局部拼接／B 帧时钟回归通过（共 182 项），Node 录制器测试覆盖局部录制、原时间／速度恢复、帧率和取消，翻译检查通过。保存证据位于 `output/60fps-qa/final-pure-memory.png`、`final-results.png`、演奏的 `ending-verification.json`。原版成片与 plan／motion／judgements 均保留，正文逐帧 MD5 与新成片前段比较：前 **9000 帧（150 秒）完全一致**。之后 **90 帧（1.5 秒）**从原视频解码，仅为安全的 GOP 边界拼接而重新编码，不进行三维渲染；新三维渲染只有 8.2 秒。最终用 setts 规范解码时钟并保持显示时间，实际每帧间隔 1/60 秒、无非单调时间戳或解码错误。

## 迁移经验

运行 `./arc-create-export/setup.sh` 更新本机适配器；需保留匹配版本 ArcCreate 源码中的 `Assets/AudioClips/ResultScreen.ogg`。Unity 的原生 Result 场景必须实际存在，不能仅凭源码判断桌面功能完整。桌面缺失控制器会产生序列化提示，适配器不用该组件；仍使用场景中的原生图形和 Utility 显示组件。编辑器弹窗日志回调不适用于已关闭的编辑器画布，因此片尾模式移除该 UI 回调，保留文件日志。

新平台需检查结果场景对象路径、原生字体与徽章、CanvasScaler、截图方向／尺寸和真实导出。相机／手部不改动；只有片尾屏幕纹理和片尾音频变化。不要为了增加结算重新搜索或回放整首谱面。

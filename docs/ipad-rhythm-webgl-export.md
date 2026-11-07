# WebGL 预览视频导出

主操作「导出当前预览视频」录制现有 Three 渲染器的同一场景、相机、材质、灯光和手部动画。通过持久 Web Audio 媒体元素源捕获现有游戏音频，与 canvas 视频轨组成 MediaRecorder 输入。不需要 Blender 帧、屏幕捕获、麦克风、重新规划或 ArcCreate 重渲染。

浏览器优先检测支持的 MP4 编码，否则回退 WebM。所用平台接口参考原始文档：[canvas 录制](https://developer.mozilla.org/en-US/docs/Web/API/HTMLCanvasElement/captureStream)、[媒体元素音频源](https://developer.mozilla.org/en-US/docs/Web/API/AudioContext/createMediaElementSource)、[运行时编码检测](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/isTypeSupported_static)。

从所选演奏开头以 1× 录制，不受慢放检查速度影响，耗时约等于片长。分辨率来自当前画布精度；录制时调整为完整、偶数尺寸的 4:3，再恢复正常响应式大小。录制期间锁定设置与播放控制，切换演奏会延后处理。结束恢复原时间、速度、静音／音量及暂停／播放状态；再次导出复用同一音频图。取消、隐藏页面或播放卡住会丢弃不完整结果，保留上一次成功下载。视频必须在关页／刷新前下载。

界面切换中文／English 只更新文字，不更换 canvas、视频源、相机或音频图；录制状态也用当前语言显示。游戏画面本身来自 ArcCreate，不由此 UI 翻译。

可展开的 Blender 导出保留原 API 和服务器端演奏 MP4，适用于不支持录制的浏览器或离线 EEVEE。

## 实际验证

Apple M2 Max 开发 Mac 上，使用正常工作台录制并下载 65–75 s 演奏。首个桌面文件为 H.264 **30 fps**／AAC，与原游戏音频波形相关 **0.986**，共同启动延迟约 **53 ms**。最终对齐版 **856×642**，**10.83 s** 完成，音频相关 **0.984**、共同启动延迟约 **49 ms**。已看真实帧，场景和原生画面均保留。实时录制并非逐帧离线编码，前台渲染／编码延迟和帧间隔仍依设备变化。

最初奇数宽 **859 px** 被 H.264 编码为 **858 px**；现在录制前直接渲染完整偶数 4:3 画布，不接受隐式裁切。**375×812** 手机尺寸测试得到 **320×240 H.264／AAC**，**10.77 s** 源片在 **10.83 s** 完成。取消后保留旧 MP4 URL，恢复原 **5.65 s、0.5×、暂停** 状态。两种尺寸使用同一源和控制器；这是 Mac 浏览器缩尺寸测试，不是真实 iPhone／Safari 性能基准。

证据位于 `output/webgl-export-qa/`，包括桌面／手机尺寸视频、导出帧、音频检查 JSON 和截图。Node 回归覆盖成功录制、音频图复用、取消及状态恢复。场景／姿态映射未改，独立 Blender／WebGL 对齐检查仍适用。

```sh
cd /path/to/ipad-rhythm
node tests/test_preview_recorder.mjs
node tests/check_webgl_alignment.mjs output/runs/20261107-000550-9a4498
```

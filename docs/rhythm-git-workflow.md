# 模拟器 Git 工作流

这是独立本地仓库，父 `dafeiyu-3d-room` 负责房间、角色与椅子动画，并忽略此目录。模拟器 Git 命令只在本目录运行。

## 2026-11-07：已有工作按功能并入 main

原功能分支彼此叠加，因此按依赖顺序使用 `--no-ff` 合并，保留原提交和分支，未压缩或改写历史。

| main 合并提交 | 来源分支 | 功能块 |
| --- | --- | --- |
| `d2d9ca4` | `feature/webgl-video-export` | 同 WebGL 画面与游戏音频录制 |
| `253f754` | `perf/contact-planner` | 搜索记忆化、完成规划缓存及可重复基准 |
| `86b3bdd` | `chore/standalone-repository` | 独立仓库、维护记录与曲包清点；保留原 integration 分支 |
| `882a871` | `feature/chart-library` | 全量曲库导入、分组选择与兼容性提示 |
| `7ae1168` | `feature/red-notes` | 零分红键显示与追打两个开关 |
| `90df2c9` | `feature/scroll-audio-settings` | 流速、歌曲延迟与实际命中音效 |
| `838ea6f` | `fix/idle-detour-speed` | 新默认值、Arghena 调查及可选移动限速 |
| `fbc66da` | `fix/shared-hand-pose-cache` | 跨演奏复用已验证姿态 |

合并后 main 与合并前最后工作分支 `fix/shared-hand-pose-cache` 文件树完全一致。没有把父仓库的房间／椅子修改带入，也没有创建远端或执行推送。旧基线是 `2aa579c`，原父仓库提交映射见[拆分记录](repository-separation.md)。

## 中文化分支

`feature/zh-cn-i18n` 从上述 main 创建，单独维护简体中文默认界面、无重载 English／中文切换、中文文档及 `README.en.md`。不与规划／姿态实现混在同一功能块，不改变计分规则和缓存键。

项目自有文档用中文，技术标识、命令、数据报告保留原值。第三方说明和许可证不改写。维护与验证见[中文化说明](i18n.md)。

## 后续开发与运行

未来可从本仓库创建分支／worktree；父项目 `work/branches/` 的旧 worktree 是父历史快照，不是当前模拟器规范工作区。运行规划／导出时不要切换实时工作区。已打开页面保留原内存代码，重新加载页面后才使用新的 UI。

下载工具、虚拟环境、导入媒体、演奏与缓存忽略于 Git；清单与可复现源码／配置提交。交互签名提示在当前执行环境不可用，任务提交按命令使用 `commit.gpgsign=false`，未修改全局签名或身份。

服务仍为 loopback:8765 与已有 Tailscale 地址；launchd 显式配置 `BLENDER_BIN`，源码默认 PATH／本地安装，不加载房间场景。重载前检查 `/api/state`；新增 UI 模块要检查静态白名单，不能为加载新界面中断活动演奏。

## 后续修复分支

`fix/negative-audio-start-60fps` 从中文化分支创建，包含 Arghena 负音频起点修复、真正保留原生 60 fps、浏览器 30／60 fps 导出选项及完整重跑验证。未改 main，也未改动保存的规划设置；详见[调查记录](arghena-negative-offset-60fps.md)。

`feature/native-results-ending` 基于上述修复分支，单独加入原生结果场景、Pure Memory 判定、局部导出和不重渲染正文的拼接流程。

## 2026-11-07：后续功能分块并入 main 并发布

在旧 main `fbc66da` 之后，继续按依赖顺序用 `--no-ff` 合并，保留全部 23 个原始提交和原功能分支，不压缩或改写历史：

| main 合并提交 | 来源分支 | 功能块 |
| --- | --- | --- |
| `bb694bb` | `feature/zh-cn-i18n` | 默认中文界面、英文切换和双语说明 |
| `dc13317` | `fix/negative-audio-start-60fps` | 负音频起点修复与 60 fps 导出 |
| `ca497ec` | `feature/native-results-ending` | 保存判定生成结果片尾与仅导出片尾 |
| `ab40f2d` | `fix/canonical-arccreate-results` | ArcCreate 原生结果过场、入场、评级及音频时序 |
| `aa734c0` | `feature/arm-winding-limits` | 手臂累计绕转限制与空闲解绕路径 |
| `12ac8ab` | `feature/off-center-contacts` | 原生判定区域内的接触点搜索与细化 |
| `c320a20` | `fix/fine-trace-weight-control` | 小数描线权重与抖动调查 |
| `f6eeeab` | `feature/continuous-acceleration` | 连续指尖加速度代价与平滑整条蛇路径 |
| `8bb387d` | `feature/note-center-preference` | 音符中心偏好、调参和原生回放对照 |
| `b9ef6d2` | `feature/crossing-wrist-costs` | 交叉深度、手腕应变代价及界面调参 |

合并操作在临时 worktree 中进行，未中断现有工作台。十次合并后的 main 文件树与 `feature/crossing-wrist-costs` 完全相同；随后只更新本维护记录并清理片尾模块末尾空行。导入曲包、媒体、工具、演奏输出与父仓库房间／椅子文件均未纳入发布。

发布目标为 [riptideiv/arcaea-gameplay-simulator](https://github.com/riptideiv/arcaea-gameplay-simulator)，默认分支为 `main`。只推送合并后的 main；原功能分支引用保留在本地，完整提交历史随 main 发布。

发布前验证：Python 测试 **229 项通过**；Node 的界面翻译（377 条消息）、预览录制器及规划进度三个检查全部通过；`git diff --check` 通过。本次验证未重新生成原生回放或 Blender 视频，既有验证证据见各功能说明。

# 独立模拟器仓库

模拟器是独立 Git 仓库，当前本地路径为 `prototypes/ipad-rhythm/`。虽然位于房间项目文件夹内，其 `.git` 只含模拟器历史与文件；父 `dafeiyu-3d-room` 忽略整个目录。椅子、房间、dafeiyu 动画只属于父仓库。

拆分保留原地源码／运行文件和分支拓扑；去掉房间文件后树改变，因此过滤后的提交 ID 不同：

| 分支 | 原父仓库提交 | 模拟器提交 |
| --- | --- | --- |
| `main` 基线 | `25f6003` | `2aa579c` |
| `feature/webgl-video-export` | `fdb5173` | `08101b4` |
| `perf/contact-planner` | `1cd19e2` | `260a5d9` |
| `integration/rhythm-updates` | `74a220b` | `bdb4d7f` |

后续拆分说明与曲库提交延伸 integration 历史。历史基准归档仍标记原基线 `25f6003`，打包源未改、不依赖 Git 可运行。拆仓库没有改变规划器、计分、动作或展示几何，也没有创建远端或推送。

Blender 通过 `BLENDER_BIN`、PATH 或 `.tools/Blender.app` 配置；本机 launchd 显式指向现有安装，不依赖原房间 blend。ArcCreate、虚拟环境、导入包、媒体、演奏与缓存仍是本地忽略资源。

在父目录可用 `git -C prototypes/ipad-rhythm ...`，或直接进入模拟器目录。后续文档在本仓库 `docs/`。父仓库历史及椅子修改没有移入模拟器提交。

# 本地曲库

导入谱面和包归档是本地资源，排除在 Git 之外；`manifests/` 保留来源、内容和散列清单。

`imported/10plus-import-novedio/` 是 2026-11-07 下载完成的 **379,909,860 字节 ZIP** 曲包。校验过的原归档副本为 `packages/10plus-import-novedio.zip`。包含 **77 个歌曲文件夹、268 个 AFF**，以及歌曲音频、封面和 `songlist` 元数据。ZIP CRC 全部通过；AFF 均有谱面头和非空音符／蛇内容。清单见 `manifests/10plus-import-novedio.json`。

它使用 **Arcaea 歌曲文件夹格式**，不是 `.arcpkg` 或 `.arcproj`。77 首的全部 268 个难度已加入工作台，加上原有两张共 270 张。包中未发现 Arcahv。

重新生成本地目录和 ArcCreate 工程：

```sh
.venv/bin/python import_charts.py charts/imported/10plus-import-novedio
```

工作台目录为 `imported/catalog.json`。每个难度在 `imported/library/` 下拥有独立 `project.arcproj`，通过相对链接引用复制后的原始 AFF、音频、封面和音效，避免每个难度重复存媒体。源文件不改动；特定难度的音频／封面优先于基础资源，包括 PRAGMATISM -RESURRECTION-。导入可重复运行，更新目录后仅在工作台空闲时重载服务。生成资源仍忽略于 Git，导入报告见 `manifests/10plus-import-novedio-import.json`。

当前 **268/268 AFF 可解析**。最早清单记录的是尚不支持 `designant` 的初次检查，导入报告记录当前状态。44 张谱面还有扩展轨道、场景控制、蛇特效／计分密度、时间组属性等警告；解析成功不代表所有判定或视觉机关均支持。

八张 Designant/Lament Rain 难度现均已启用。`designant` 表示红色装饰蛇，其天键计分权重为零。界面分为「显示红键」与「尽量打全红键」两个开关：关闭追打不额外规划触点，但仍可顺带命中可见红键；开启后先保护计分，再尽量多接红键，最后优化动作。即使普通漏键关闭，不可达红键仍可跳过。

每段演奏在自己的 `arccreate/` 下生成兼容谱面，红蛇／红天键着红，使用原生天键判定，显示粒子并发声，但不影响分数、连击或归一化。红键命中／漏掉单独报告。关闭显示会从临时渲染谱面去掉红蛇及其天键，不改原始 AFF。新环境先执行 `./arc-create-export/setup.sh` 构建修补版，验证方法见[环境迁移指南](../docs/ipad-rhythm-environment-porting.md)。

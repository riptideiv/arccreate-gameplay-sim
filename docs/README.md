# 项目文档

项目说明默认使用简体中文；英文入门说明保留在 [README.en.md](../README.en.md)。界面语言可独立切换，不影响谱面或计算结果。代码标识、命令、技术证据与第三方许可证保持原文。

| 文档 | 内容 |
| --- | --- |
| [项目首页](../README.md) | 安装、使用流程、默认值与命令行入口 |
| [环境迁移指南](ipad-rhythm-environment-porting.md) | 场景接口、材质、坐标、缓存、部署与迁移经验 |
| [缓存层说明](cache-layers.md) | 哪些操作重算、缓存键和共享姿态复用 |
| [搜索优化报告](ipad-rhythm-planner-optimization.md) | 不改变计分规则的优化、基准和正确性证据 |
| [原生结算片尾](native-results-ending.md) | Pure Memory、原生结果场景与仅重渲染末尾 |
| [WebGL 视频导出](ipad-rhythm-webgl-export.md) | 当前画面与音频录制、兼容性、取消和验证 |
| [Arghena 负音频起点与 60 fps](arghena-negative-offset-60fps.md) | 导出失败原因、保持原设置重跑与成片验证 |
| [Arghena 漏键与动作调查](arghena-end-miss-and-idle-motion.md) | 计划漏键原因、原生验证和待机／避让限速 |
| [仓库拆分](repository-separation.md) | 与房间项目的边界及旧提交映射 |
| [Git 工作流](rhythm-git-workflow.md) | main 的功能分块合并和中文化分支 |
| [中文化维护](i18n.md) | 翻译目录、动态消息、无重载切换和检查方法 |
| [曲库说明](../charts/README.md) | 77 首／268 张导入、红键兼容和本地资源 |
| [基准运行方法](../benchmarks/README.md) | 可重复的隔离测量与完整规划比对 |

`output/` 中的报告、日志、截图与视频是本机证据，不随 Git 分发；`benchmarks/results/` 和 `charts/manifests/` 中的原始 JSON、摘要及归档按机器可读格式保存。历史测试数量和成绩对应文中注明的版本，不能替代当前版本的验证。

# 触点规划器基准

使用本项目 Python 环境，不涉及 Blender、ArcCreate、原生回放或编码。工作台及其他重任务应空闲；**不要为了测量重启或取消它们**，忙时推迟试验。

在本仓库根目录执行：

```sh
WORKSPACE="$PWD"
OUT="$WORKSPACE/output/benchmark/contact-planner-recheck"
PYTHON="$WORKSPACE/.venv/bin/python"
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage snapshot
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage all
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage profiles
"$PYTHON" benchmarks/run.py --workspace "$WORKSPACE" --output "$OUT" --stage cache-hits
```

默认 `--baseline-revision 25f6003`；snapshot 从随附可运行归档恢复精确基线源码，只有请求其他版本才只读回退 Git。执行不依赖 Git 或当前工作区。worker 只把指定快照或优化源码插入 `sys.path`，断言导入来源；共享解释器及依赖，使用相同原谱面、设备文件及路径。设置来自提交的 `20261107-000550-9a4498/settings.json` 副本，只改区间。

主试验采用新进程、按基线／优化交错串行，每区间各三次，进度回调与日志相同，关闭已完成规划磁盘缓存，保留单次进程内记忆化。计时含解析、预处理、搜索与 JSON 序列化；资源指标仅 worker，macOS RSS 字节转 MiB。CPU 和源码指纹随规划保存。剖析另外运行，不作主结果。`cache-hits` 先真实填充，再用三个新进程命中，断言未进入搜索并单独报告。

`report.json` 比对全部 **18** 份完整规划，**只移除 `stats.elapsed_s`**。触点、手分配、漏键、计分覆盖、代价与状态数必须一致，否则停止。字节等价触点不需重渲染原生回放。

原始配置、日志、JSON、源码散列、计时与 `.prof` 留在输出目录；已提交摘要位于 `benchmarks/results/`，解释见[优化报告](../docs/ipad-rhythm-planner-optimization.md)。

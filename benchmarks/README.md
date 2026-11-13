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

默认 `--baseline-revision HEAD`；snapshot 保存当前独立模拟器 Git 仓库的完整基线源码，包含根目录辅助模块。显式指定 `--baseline-revision 25f6003` 时仍可从随附归档恢复历史版本。完成 snapshot 后的执行不依赖 Git 历史。worker 只把指定快照或优化源码插入 `sys.path`，断言导入来源；两种模式都显式请求 `search_backend=python`，不会把默认 `auto` 的原生执行误记为优化 Python。共享解释器及依赖，使用相同原谱面、设备文件及路径。设置来自提交的 `20261107-000550-9a4498/settings.json` 副本，只改区间。

主试验采用新进程、按基线／优化交错串行，每区间各三次，进度回调与日志相同，关闭已完成规划磁盘缓存，保留单次进程内记忆化。计时含解析、预处理、搜索与 JSON 序列化；资源指标仅 worker，macOS RSS 字节转 MiB。CPU 和源码指纹随规划保存。剖析另外运行，不作主结果。`cache-hits` 先真实填充，再用三个新进程命中，断言未进入搜索并单独报告。

`report.json` 比对全部 **18** 份完整规划，**只移除 `stats.elapsed_s` 与存在时的 `contact_placement.elapsed_s`**。触点、手分配、漏键、计分覆盖、代价与状态数必须一致，否则停止。字节等价触点不需重渲染原生回放。

原始配置、日志、JSON、源码散列、计时与 `.prof` 留在输出目录；已提交摘要位于 `benchmarks/results/`，解释见[优化报告](../docs/ipad-rhythm-planner-optimization.md)。

## near-side-travel 原生后端

本分支从 `codex/near-side-travel` 的 `39c4ecb` 移植原生优化。整曲设置来自该工作台，Arghena 区间为 0–154.325 秒，Undying Macula 为 0–163.295 秒，**beam width 均为 256**。保留 FAR 重试、检查点、near-side travel 和接触偏移设置。

本轮采用针对性规则测试、单次短区间性能对照及两首整曲的规划／姿态／原生回放。没有重新执行旧分支的 90 次矩阵，也不沿用旧分支的性能结论。操作说明和实测见[原生搜索移植](../docs/near-side-native-search.md)。

保留 `full_search.py` 作为可选的深入测量工具。额外矩阵须在 prepare 阶段显式指定 `--baseline-revision 39c4ecb`。

`worker.py` 区分 Python 与 native，拒绝把回退结果算作原生试验。完整规划比较忽略耗时，质量比较还检查 FAR 和非 MAX 判定数。`replay_check.py` 可复查 ArcCreate 回放输入、判定文件和来源散列。

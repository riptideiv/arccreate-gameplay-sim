# near-side-travel 原生搜索

此分支基于 `codex/near-side-travel` 的 `39c4ecb`，移植 `177697a` 的 Rust / PyO3 / Rayon 搜索基础。Python 保留谱面解析、候选生成、重试调度、结果序列化及参考实现。原生循环负责触点、颜色锁、代价、手臂路径、穿越顺序、near-side 曲线、去重和 beam 裁剪，worker 不调用 Python。

## 规则与使用

默认 beam width 为 **256**。原生实现保留最小连击间隔、独立 XY 接触偏移、Perfect → 检查点 FAR → 分割 arc → 完整 arc 的重试顺序、FAR / 非 MAX 排名、释放时序多样性，以及最终路径恢复。计划中的 `arm_crossings` 和 `travel` 仍交给该分支原有的手套／肘部动画流程。

`create_plan`、`api.plan_chart`、`plan_multi_finger` 及两个 CLI 支持 `search_backend=auto|python|native` 和 `search_workers=0|N`。0 默认保留一个 CPU、最多四个 worker；少于 32 个状态时串行扩展。worker 数不改变搜索宽度，执行方式通过进度诊断报告。缺少扩展或遇到不支持的配置时会明确回退 Python。食指与拇指的按判定规划支持原生搜索，双臂交叉路径仍使用每手一个触点的模式。

`auto` 已启用原生搜索；未安装扩展或配置不受支持时回退 Python，并在进度诊断中说明原因。原生扩展独立安装，Python 包仍可单独使用。目前原生数值兼容范围为 CPython 3.13，其他解释器版本显式回退：

```sh
uv pip install --python .venv/bin/python ./native-search
.venv/bin/python plan_excerpt.py CHART.aff START_MS END_MS output/plan.json \
  --settings SETTINGS.json --search-backend native --search-workers 4 --no-plan-cache
```

合入多指搜索后的输入 schema 为 8。需要重新构建扩展，防止载入旧版。完成规划缓存包含原生源码和二进制身份。检查点保留原生前沿、祖先触点、手臂状态和累计代价；恢复时重新映射当前图的触点。取消会终止未完成层并阻止发布部分规划。

## 精简验证

规则检查覆盖 Python 缓存、颜色释放、接触排除、FAR 连击、检查点恢复、最终停留、空区间、取消及原生穿越／曲线路径。小谱面比较 1／2／4／8 workers；检查点比较 1／4 workers。完整计划接入 Python 逐步触点复查，最终手臂路径再次检查锁定触点的上方重叠。

单次 Arghena 131–131.95 秒对照使用未改动的 `39c4ecb` 和相同设置、beam 256、关闭完成规划缓存。Python 19.86 秒，最终 native 四 worker 2.82 秒，约 7.03 倍。除耗时与 worktree 设备文件路径外完整结果相同。这是共享工作机上的一次短区间观测，运行时还有整曲任务；不能推断整曲倍率。

整曲 Arghena（0–154.325 秒）最终原生规划 295.1 秒，经过一次检查点恢复，零漏计分点、6 个 FAR、93 次穿越、22 条 near-side 曲线。整曲运行使用 workbench 保存的 256 beam 设置。原始日志和规划在本地忽略目录 `output/port-validation/`。

Arghena 最终完整计划与已回放计划仅耗时不同。ArcCreate 判定 1444 命中、0 miss，549 个预期 arc tick 全部确认。

最终集中检查：80 个 Python 测试通过、2 个跳过；21 个原生 Rust 规则测试及两项 JavaScript 工作台检查通过。启用 auto 后，23 个执行策略／缓存测试通过，并确认不同 worker 数可以复用完整规划缓存。按个人项目范围采用上述精简检查，没有执行原定的多轮整曲基准矩阵。

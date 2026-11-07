# arc-hand-order

Arcaea 谱面的手序规划（多指 DP）。给一张 `.aff` 谱面，程序规划：
- 每个音符由哪只手、哪根手指（第一指或第二指）来打；
- 每根手指在哪些帧按在屏幕的什么位置。

然后写出一个 plan JSON。输入和输出的说明见 [docs/FORMAT.md](docs/FORMAT.md)。

仓库只含规划本身：谱面解析、触点规则和手序规划。不含判定、渲染和手部动作。

## 两个预设

| 预设 | 内容 |
|---|---|
| `on4` | 多指规划：<br>- 虚线 arc 不抬手滑过；<br>- 共用触点；<br>- 第二指；<br>- meeting 处换手（X 交换、接力）；<br>- 被顺带按住的 hold 和 arc；<br>- 抖动 arc 合并；<br>- 连打代价 |
| `on5`（默认） | on4 再加十条规则，包括 arc 末端归属、清楚分层交叉减价、加速度上限、天空键按画出的宽度瞄准、arc 跳回时滑过去等。<br>逐条说明见 FORMAT.md 1.3 |

两个预设共用同一组基础选项 `presets.BASE`：
- 点键在音符时间 ±25 ms 内按下；
- 每条 arc 和 hold 从头按到尾；
- 按住的接触松开后至少离开 4 帧；
- 颜色、交叉、移动、距离、相隔、连按代价。

## 运行

需要 Python 3.12 以上和 numpy、pydantic（版本见 `pyproject.toml`）。

```bash
python -m pip install -e ".[dev]"
```

```bash
python -m arc_hand_order plan chart.aff --out chart.plan.json
```

```bash
python -m arc_hand_order plan chart.aff --preset on4 --set jack_weight=0
```

```bash
python -m arc_hand_order options --preset on5
```

- 不安装也可以：把 `src` 加进 `PYTHONPATH` 再运行。
- 默认设备是 iPad Pro 11（M2），`--device` 可换。
- 一张 2–3 分钟的高难谱面，单进程规划要几十秒到几分钟。

Python 里调用：

```python
from arc_hand_order import api
result = api.plan_chart(api.load_chart("chart.aff"), preset="on5")
api.write_plan(result, "chart.plan.json")
```

## 测试

```bash
python -m pytest -p no:cacheprovider tests
```

## 目录

```
src/arc_hand_order/
  __main__.py, api.py        命令行和接口
  presets.py                 BASE、on4、on5
  planning/                  multi_finger.py（多指 DP）、two_finger.py（候选与基础代价）、rules.py（触点规则）、
                             occupations.py、projection.py
  chart/                     AFF 解析
  schemas/                   谱面和设备的数据结构
  camera.py                  游戏相机
  devices/                   设备文件
docs/FORMAT.md               输入输出格式
tests/
LICENSE                      GPL-3.0 全文
```

## 限制

- 规划到触点为止：手、第一或第二指、每帧的触点位置。
  - 手在空中怎么移动、用哪根实际的手指、手臂和身体，都不在规划范围内。
- 设备只用到屏幕的物理尺寸。
  - 平板平放固定，相机按固定姿态计算。
- 规划只按 FORMAT.md 1.4 的触点规则进行，不做判定，计划里也没有判定结果。
- 只显示、不判定的音符，黑线，以及零长度的 hold 和 arc 不参与。

## 许可证

本项目按 GNU 通用公共许可证第 3 版（GPL-3.0）发布，全文见 [LICENSE](LICENSE)。

以下两部分参照 [ArcCreate](https://github.com/Arcthesia/ArcCreate)（GPL-3.0）的实现编写：
- arc 曲线的计算：`src/arc_hand_order/chart/interpolation.py`；
- arc 连接的建立：`src/arc_hand_order/chart/arc_connections.py`。

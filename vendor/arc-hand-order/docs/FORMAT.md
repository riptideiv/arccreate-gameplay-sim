# 输入与输出格式

程序做一件事：读一张 Arcaea 谱面（`.aff`）和一个设备文件，按预设（on4 / on5）规划每个音符由哪只手、哪根手指、在哪些帧按在屏幕哪里，写出一个 plan JSON。

```
python -m arc_hand_order plan CHART.aff [--out plan.json] [--preset on4|on5] [--device DEVICE.json]
                                        [--set 名称=值 ...] [--planner-set 名称=值 ...]
python -m arc_hand_order options [--preset on4|on5]
```

## 1 输入

### 1.1 谱面 `.aff`

输入是一张 Arcaea 的 `.aff` 谱面文件。

- 解析出错时程序报错退出（状态 1）。
- 规划不支持的内容只记一条 warning，不影响规划（`api.load_chart(...).parsed.diagnostics` 里能看到）。

**参与规划的音符**，即 occupation（`planning/occupations.py`）：

| 音符 | occupation 种类 | 说明 |
|---|---|---|
| 地面点键 | `tap` | 轨道 1–4 |
| hold | `hold` | 零长度的不参与 |
| arc（黑线除外） | `chain` | 零长度的不参与；同色并且首尾相接的若干段合成一条 chain，按住全程 |
| 天空点键 | `sky` | 挂在黑线或彩线上都一样 |

- 只显示、不判定的音符不参与。
- 黑线本身不需要触摸。

**时间。**
- 谱面时间（chart ms）就是谱面里写的毫秒数。
- 手指按 60 Hz 的游戏帧规划：第 f 帧对应谱面时间 `round(f × 1000 / 60) − 音频偏移`（`planning/two_finger.py: frame_ms`）。

**事件 id。**
- 解析器给每个音符事件一个 id，形如 `<指令>_<哈希>_<n>`，由音符的内容决定：
  - 同一份谱面文字，id 永远相同；
  - 完全相同的重复音符用 n（从 1 开始）区分。
- 输出里所有 `events` 都用这个 id。

### 1.2 设备文件（JSON）

- 格式（`schemas/device.py: DeviceProfile`）：

```json
{"schema_version": "1.0", "profile_id": "ipad_pro_11_m2_landscape",
 "description": "iPad Pro 11-inch (4th generation, M2), landscape: 2388 x 1668 px at 264 ppi, 229.7 x 160.5 mm",
 "screen": {"width_m": 0.2297, "height_m": 0.1605, "pixel_width": 2388, "pixel_height": 1668}}
```

- 屏幕按横屏写：`width_m`、`height_m` 是显示区域的物理宽高，单位米。
- 游戏相机（`camera.py`、`planning/projection.py`）按屏幕宽高比得到判定线、轨道和天空区在屏幕上的位置，再按物理尺寸换算成米。
- 自带两个：

| 文件 | 设备 |
|---|---|
| `devices/ipad_pro_11_m2.json` | 默认。iPad Pro 11（第 4 代，M2），229.7 × 160.5 mm |
| `devices/ipad_10_9.json` | iPad Air（第 5 代）10.9 英寸，227.1 × 157.8 mm，单元测试用 |

### 1.3 选项

- 规划用两组选项：
  - `PlannerOptions`（`planning/two_finger.py`）：基础代价和时间窗。两个预设都用 `presets.BASE`；
  - `MultiFingerOptions`（`planning/multi_finger.py`）：多指功能。由 `--preset` 选 `on4` 或 `on5`。
- 每个字段的意思写在这两个 dataclass 的注释和 `multi_finger.py` 开头的说明里；`python -m arc_hand_order options --preset on5` 打印全部值。
- `--set 名称=值` 改 `MultiFingerOptions` 的字段，`--planner-set 名称=值` 改 `PlannerOptions` 的字段，可以重复。值按字段类型读：
  - 布尔：`1/0/true/false/yes/no/on/off`；
  - 整数、小数：照写；
  - 元组：写 JSON 列表，例如 `--planner-set offsets_ms=[0]`；
  - 可为空的字段：写 `none`。
- 字段名写错会报错。
- `PlannerOptions.audio_offset_ms` 总是被谱面里的音频偏移替换，不需要设。

**on4 和 on5 的区别：** on5（`multi_finger.RECOMMENDED`）= on4 再加下面这些规则。

| 字段 | on5 | 作用 |
|---|---|---|
| `arc_takeover_weight` | 5 | arc 末端归属。一只手刚结束 arc 或 hold，另一只手从 4 cm 外过来、在那个末端位置按下，就等于两手互换了角色，收费。<br>窗口 150 ms；原手又在那里按下后延长到 210 ms |
| `apart_clear_weight` | −8 | 两手上下分层、下方指尖离上方前臂较远（clear）时，apart 代价的权重从 10 减到 2。<br>设了这类权重就启用几何特征 |
| `enveloped_arcs` | 开 | 另一色较长 arc 上的手指能在宽限内顺带保住的短 arc 片段，不再单独派手指。<br>规划后检查（见 2.3）；不成立就整谱不用这条规则重排 |
| `share_jump_max_m` | 0.035 | 服务点键或天空键的触点，在所服务音符变化的地方一帧最多移动 35 mm |
| `accel_max_m_s2` | 75 | 手在两次触点之间空中移动所需的峰值加速度上限，单位 m/s²。<br>按静止到静止的最小加加速度轨迹计算；触点内部的组合跳动也算。<br>超出一次收 `accel_weight`（300） |
| `side_by_side_weight` | 5 | 同一只手两指同时按着、两点几乎同高（并排）时，每秒额外收费 |
| `sky_aim_drawn` | 开 | 天空键的触点落在它画出来的宽度内，而不是 1.4 倍宽的判定盒里任意处 |
| `jump_slides` | 开 | arc 在一处结束、在较远处接着开始下一段时，手指不抬，按速度和加速度上限滑过去再跟上 arc。<br>错过的开头不收费 |
| `repeat_comfort_s` | 0.18 | 同一只手下一次接触不收 repeat 代价的间隔，基础是 0.25 s |
| `second_finger_again` | 开 | 一只手一指按着 arc 或 hold 时，另一指在它上次点过的位置再点，不再收第二指的费用 |

### 1.4 触点规则

- 规划器按 `planning/rules.py` 的规则判断一个触点算不算按到、跟住了音符。下表的数值都是规划参数。
- 单位是游戏单位，每个世界单位 100：
  - 天空输入在判定线所在的竖直平面上，aff 坐标 (x, y) 对应 x = 850x − 425、y = 100 + 450y；
  - 地面输入在轨道平面上，x 横跨轨道，z 沿轨道方向。

| 音符 | 规则 |
|---|---|
| 地面点键 | 在它的轨道里按下，离音符时间不超过 `POINT_WINDOW_MS`（50 ms）。<br>轨道在 x = −425、0、425 处分界，外侧两条向外不限，深度 \|z\| < `LANE_HALF_DEPTH`（500） |
| 天空键 | 按下的点在它的矩形里：左右各 `SKY_TAP_X`（302），向下 `SKY_TAP_DOWN`（310），向上 `SKY_TAP_UP`（250）。<br>触点高度先限制在 `SKY_FLOOR`–`SKY_INPUT_HEIGHT`（100–550） |
| hold | 从开始前 `HOLD_EARLY_MS`（100 ms）到结束，有触点在它的轨道里按下；之后这条轨道里的任何触点都算 |
| arc | 触点在 arc 当前位置的 ±`HITBOX_X` × ±`HITBOX_Y`（190 × 250）以内，并且被这种颜色接受。<br>一种颜色同时只绑定一个触点：没有绑定时，按按下顺序，第一个进入它某条 arc 判定框、又没有绑定别的颜色的触点被绑定，之后只接受这个触点，直到它抬起。<br>两条不同颜色的 arc 相距小于 `GRACE_DISTANCE`（200）时进入宽限：两种颜色都接受任何触点、不绑定，直到最后一次靠近之后 `GRACE_MS`（500 ms）。<br>颜色 3 接受任何触点 |
| hold 和 arc 的 tick | 每半拍一个，BPM 不低于 `FULL_BEAT_BPM`（255）时每拍一个。<br>从 tick 前半个间隔起，有触点就算完成；一直没有触点，过了 min(2 个间隔, `MAX_EXPIRY_MS`) 就算漏（`MAX_EXPIRY_MS` 为 500 ms） |
| arc 相接 | 首尾时间相差不超过 `JOIN_MS`（9 ms）、aff x 相差小于 `JOIN_X`（0.1）、高度相同 |

- 计划只按这些规则规划，不含判定结果。

## 2 输出：plan JSON（`"format": "arc-hand-order.plan/1"`）

- 一个 UTF-8 JSON 对象，LF 换行。
- 长度单位毫米；坐标系是设备屏幕：
  - 原点在屏幕中心，x 向右，y 向上（横屏）；
  - 判定线约在 y = −50.4 mm。
- 时间有两种：帧号（60 Hz）和谱面 ms。

### 2.1 顶层字段

| 字段 | 内容 |
|---|---|
| `format` | `"arc-hand-order.plan/1"` |
| `chart` | `{path, sha256（文件字节）, audio_offset_ms}` |
| `device` | `{path, profile_id, screen_mm: [宽, 高]}` |
| `preset` | `"on4"` 或 `"on5"` |
| `planner_options`、`multi_finger_options` | 实际使用的全部字段，已含 `--set` 的修改 |
| `frame_rate_hz` | 60 |
| `touches` | 触点列表，见 2.2 |
| `covered` | 不需要自己触点的音符，见 2.3 |
| `notes` | 每个可判定事件由谁打，见 2.4 |
| `cost` | 代价，见 2.5 |
| `stats` | 统计，见 2.6 |

### 2.2 `touches`：触点

一个触点 = 一根手指从按下到抬起的一次接触。

| 字段 | 内容 |
|---|---|
| `id` | 1 起，按 (`down_frame`, 手, 手指) 排序编号 |
| `hand` | `"left"` / `"right"` |
| `finger` | `1`：这只手的第一指，一般是食指<br>`2`：同一只手在第一指按着时用的第二指<br>具体是哪根手指不在这里决定 |
| `down_frame` / `up_frame` | 按下的第一帧 / 抬起后的第一帧，半开区间 [down, up) |
| `down_ms` / `up_ms` | 这两帧的谱面时间 |
| `path_mm` | 从 `down_frame` 到 `up_frame − 1` 每帧一个 `[x, y]`：触点位置<br>空中的移动不在规划范围内 |
| `parts` | 这个触点服务的音符，见下 |
| `bridges` | `[[a, b], ...]`，帧 [a, b) 按着但不服务任何音符：<br>手指在虚线 arc 两段之间或 arc 跳回处滑过去（`bridge_max_gap_ms`、`jump_slides`） |

**`parts`：** 每个 part 是一个 occupation 加上服务它的帧段。

| 字段 | 内容 |
|---|---|
| `kind` | `tap` / `sky` / `hold` / `chain` |
| `occupation_id` | 普通：`<kind>:<第一个事件 id>`<br>meeting 换手：`<链A>><链B>@<切换 ms>`<br>抖动 arc 合并：`<第一段>~<段数>` |
| `events` | 这个 occupation 包含的全部事件 id |
| `start_ms` / `end_ms` | 音符本身的谱面时间段 |
| `colour` | chain 的颜色：0 蓝、1 红、2 绿 |
| `lane` | tap / hold 的轨道 1–4 |
| `from_frame` / `to_frame`，`from_ms` / `to_ms` | 这个触点服务它的帧段 [from, to) |
| `served_events` | 这一段实际打到的事件（规则见 2.4） |
| `switch`（只有 meeting 换手才有） | `{at_ms, before, after}`<br>两条 arc 在 meeting 处交接：切换前按着 `before` 链，切换后沿 `after` 链走（X 交换或接力，见 `multi_finger.meeting_candidates`） |
| `steady_pieces`（只有抖动 arc 合并才有） | 合并的各段 occupation id |

- 一个触点有多个时间重叠的 part，就是共用触点（`share`），例如一根手指同时压着 hold 和其上的 arc。
- 前后相接的 part 是手指滑过去接下一段。

### 2.3 `covered`：不需要自己触点的音符

格式同 part 的 occupation 部分，没有帧段。有三种：
- 被其他触点按下并一直按住的 hold（`covered_holds`）；
- 颜色一直在宽限内、并且判定盒每个 tick 都有天空键或点键触点经过的 arc 片段（`covered_arcs`）；
- 被另一色手指整段包住的短 arc 片段（`enveloped_arcs`）。

这些音符和抖动 arc 合并段（`steady_arcs`）在规划后都按 1.4 的规则检查一遍（`multi_finger._unkept`）：
- arc 的每个 tick 在结算窗口内，要有触点在判定框里并且被这种颜色接受。颜色绑定按整份计划的触点逐帧推算（`multi_finger._taken`）：一根手指在另一种颜色的宽限结束时停在它的判定框里，就会把那种颜色绑走，原本按它的手指不再被接受，这根手指自己的颜色也不再接受它。
- hold 的每个 tick 在结算窗口内要有触点在它的轨道里；被顺带按住的 hold 还要有触点在可以按下它的时候落在它的轨道里。

没通过的放回去单独派手指，整谱重新规划。

### 2.4 `notes`：每个可判定事件由谁打

- 每个参与规划的事件一项（地面点键、hold、arc 段、arctap），按时间排序：

```json
{"event_id": "arc_b10337a2835d33c790a63ee3_1", "type": "arc", "start_ms": 1600, "end_ms": 2400,
 "lane": null, "colour": 1, "hands": ["right"], "touches": [4]}
```

- `type` 是解析器的事件类型（`tap` / `hold` / `arc` / `arctap`）。
- `hands` 是 `"left"`、`"right"`、`"covered"` 的子集：
  - 一条 arc 在中途换手，两只手都会列出；
  - `covered` 表示由 2.3 的方式完成。
- `touches` 列出服务它的触点 id。

**`served_events` 的规则（`api._served`）。**
- meeting 换手的 part 服务：
  - `before` 链里在切换时刻之前开始的事件；
  - `after` 链里在切换时刻之后结束的事件。
- 被提前放开的 chain（接力时交出去的那只手）只服务在它放开之前开始的事件。
- 其余 part 服务它的全部事件。
- 跨过切换时刻的 arc 段两只手都算；正好在切换时刻结束的 arc 段只算切换前那只手。

### 2.5 `cost`

- `weighted`：DP 最小化的总代价，各项乘对应权重后相加（`MultiCost.weighted`）。
- `components`：未加权的各项合计。

| 项 | 单位 | 权重字段 | 含义 |
|---|---|---|---|
| `colour` | 次 | `colour_weight` | arc 用了不习惯的那只手（蓝左红右为习惯） |
| `crossing_seconds` | s | `crossing_weight` | 两手交叉的时长 |
| `movement` | (m/s)² | `movement_weight` | 超过舒适速度的移动 |
| `handover` | 次 | `handover_weight` | meeting 处交接 |
| `apart` | m | `apart_weight` | 落点离另一只手刚到过的位置太近或越过 |
| `repeat` | — | `repeat_weight` | 同一只手连按太快 |
| `distance_m` | m | `distance_weight` | 手在两次触点之间的直线移动距离 |
| `untouched_arc_s` / `untouched_hold_s` | s | `partial_hold_weight` / `partial_ground_hold_weight` | 没按满的 arc / hold（全按住规则下应为 0） |
| `shares`、`second_touches`、`second_s`、`side_by_side_s`、`takeovers`、`steals`、`jack_presses`、`accel_over`、`crossing_depth` | 次或 s | `share_weight`、`second_finger_weight`、`second_finger_s_weight`、`side_by_side_weight`、`takeover_weight`、`steal_weight`、`jack_weight`、`accel_weight`、`crossing_depth_weight` | 多指各项，见 `MultiFingerOptions` 注释 |
| `apart_level` … `arc_takeovers` | — | 对应的 `*_weight` | 几何特征，任一权重非零时计入 |
| `bridges` | 次 | — | 只计数，不收费 |

### 2.6 `stats`

| 字段 | 内容 |
|---|---|
| `touches` | 触点数 |
| `second_finger_touches` | 第二指的触点数 |
| `shared_touches` | 共用触点数 |
| `bridges` | 滑过次数 |
| `meeting_switches` | 用到的 meeting 换手 occupation id |
| `steady_arcs` | 抖动 arc 合并数 |
| `states_visited` | DP 访问的状态数 |
| `pruned_items` | 状态被剪枝的步数（应为 0） |
| `elapsed_s` | 规划耗时 |

- 程序规划成功就返回 0；谱面或选项不合法、或者找不到可行的规划，返回 1。

## 3 Python 接口

```python
from arc_hand_order import api
chart = api.load_chart("chart.aff")                       # 解析；语法错误抛 ValueError
result = api.plan_chart(chart, preset="on5",              # device_path=..., multi={...}, planner={...}
                        multi={"side_by_side_weight": 5.0})
data = api.plan_to_dict(result)                           # 上面的 JSON（dict）
api.write_plan(result, "plan.json")
```

- `result.plan` 是 `multi_finger.MultiPlan` 原对象，有每个候选的完整信息。

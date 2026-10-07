# 谱面文件格式（`.json`）

谱面就是**普通的 JSON 文本文件**（UTF-8、缩进 2 空格，扩展名 `.json`），可以放在任意目录；
音频会复制到**谱面文件所在的文件夹**，谱面中用文件名（相对路径）引用。

> 编辑器只支持这一种格式：保存时一律写成 `.json`（其它后缀会被改写成 `.json`）。

## 1. 顶层结构

```json
{
  "format": "infalsus.chart",
  "version": 1,
  "metadata": { "...": "见 1.1" },
  "timing": { "...": "见 1.2" },
  "speed_groups": [ { "...": "见 1.4" } ],
  "lanes": [ { "...": "见 1.5" } ],
  "mouse": [ { "...": "见 1.6" } ]
}
```

## 1.1 metadata

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `title` | string | 歌曲名称 |
| `artist` | string | 歌手 |
| `difficulty_name` | string | 关卡难度名 |
| `difficulty_level` | string | 难度等级（文本，如 `"10+"`） |
| `author` | string | 谱面作者 |
| `audio_file` | string | 音频文件名，如 `song.mp3`（相对谱面所在目录）；空串表示未设置 |

## 1.2 timing

```json
{
  "offset_ms": 120.0,
  "bpm_segments": [
    { "start_beat": 0.0,  "bpm": 140.0, "beats_per_bar": 4 },
    { "start_beat": 32.0, "bpm": 168.0, "beats_per_bar": 4 }
  ]
}
```

* `offset_ms`：歌曲需要提前播放以对应节拍的毫秒数；第 0 拍对应歌曲位置 `offset_ms` 毫秒。
* `bpm_segments`：必须按 `start_beat` 升序且第一项从第 0 拍开始（读档时会自动修复 / 排序 / 去重）。
* `beats_per_bar`：该段每小节的拍数，用于画小节线与小节编号（每段起点重新起算）。
  **填 `0` 表示这一段关闭小节线**（只有拍线，没有小节线 / 小节编号）。
* **BPM 变更处一定新开一小节**：`start_beat` 不必落在上一条记录的小节线上，
  但它自己就是一条小节线；上一条记录末尾的不完整小节按**一整节**计入小节编号，
  因此变更后的第一拍正好是下一小节的第一拍。变更之后的网格按**这一段**的
  `beats_per_bar` 重新生长。
* BPM 决定**时间轴格子的大小**（纵向按秒线性：高 BPM 段落每拍更短），
  也决定“拍 ↔ 秒 / 歌曲位置”的换算；流速（速度组）则是在此之上的倍率。

## 1.3 editor（历史字段，读取时兼容）

`zoom_seconds` / `grid_division` / `horizontal_divisions` / `audio_latency_ms` /
`metronome` / `hit_enabled` / `side_lane_highlight` / `music_volume` /
`metronome_volume` / `hit_volume` 这些**只影响编辑器**的偏好不属于谱面，保存在用户
配置目录的 `settings.json` 里（Windows 为
`%APPDATA%/InFalsusEditor/settings.json`，其他系统为 `~/.config/infalsus-editor/settings.json`；
可用环境变量 `INFALSUS_EDITOR_SETTINGS` 指定别的路径）。

* 换谱面 / 新建谱面都沿用同一份全局设置；
* 读取带 `editor` 段的旧谱面时，如果全局设置文件尚未建立，会把它并入全局设置一次
  （之后以全局文件为准）；
* 写出的谱面不含 `editor` 段（`tools/acceptance.py` 会检查这一点）。

## 1.4 speed_groups

```json
{
  "id": 1,
  "name": "加速特效",
  "segments": [
    { "start_beat": 0.0,  "multiplier": 1.0 },
    { "start_beat": 24.0, "multiplier": 2.0 },
    { "start_beat": 28.0, "multiplier": 1.0 }
  ],
  "note_ids": ["L12", "L13", "M4"]
}
```

* `id = 0` 是“默认组”，不可删除。
* `segments` 第一项从第 0 拍开始（读档自动修复）。
* `multiplier`：流速 = 默认速度 × 倍率；可为正数、`0`（停留）或负数（反向）。
* 物件自身的 `group` 字段必须与这里的 `note_ids` 一致；不一致时读档会自动修复。

## 1.5 lanes（6K 下落式物件）

```json
{
  "id": "L7",
  "type": "lane",
  "lane_start": 1,
  "lane_end": 4,
  "beat_start": 20.0,
  "beat_end": 22.0,
  "group": 0
}
```

* `lane_start ≤ lane_end`，取值 `0..5`（对应界面上的 1..6 轨）。
* `beat_end == beat_start` → 按键（单轨或多轨）；`beat_end > beat_start` → 长按（粉色）。
* 多轨按键就是 `lane_end > lane_start` 的物件。

## 1.6 mouse（鼠标平面物件）

### 空域

```json
{
  "id": "M3",
  "type": "region",
  "beat_start": 16.0,
  "beat_end": 20.0,
  "left":  { "start": 0.25, "end": 0.05, "ease": "linear" },
  "right": { "start": 0.75, "end": 0.95, "ease": "ease_out" },
  "group": 0
}
```

* 横向位置以合法区域最左侧为 `0`、最右侧为 `1`，**允许超出**（例如 `-0.3`、`1.4`）。
* `ease` 取值：`linear`（线性）、`ease_in`（缓入）、`ease_out`（缓出）。
* 边界随时间线性（按缓动）插值，`beat_start` 时刻取 `start`，`beat_end` 时刻取 `end`。
* 判定：在 `[beat_start, beat_end]` 期间鼠标横坐标需满足 `left(t) ≤ x ≤ right(t)`。

### 左右滑键

```json
{
  "id": "M4",
  "type": "slide",
  "beat": 21.0,
  "x_start": 0.2,
  "x_end": 0.6,
  "group": 1
}
```

* `beat`：物件到达判定线的时刻。
* `x_end > x_start` → 向右滑动；`x_end < x_start` → 向左滑动。
* 判定：物件到达判定线时，鼠标在 `[min(x_start,x_end), max(x_start,x_end)]` 范围内
  朝箭头方向滑动**任意长度**即成功。

### 禁区

```json
{
  "id": "M10",
  "type": "forbidden",
  "beat_start": 12.0,
  "beat_end": 16.0,
  "left": { "start": 0.25, "end": 0.35, "ease": "linear" },
  "right": { "start": 0.75, "end": 0.65, "ease": "linear" },
  "group": 0
}
```

* 字段与**空域完全相同**（`beat_start` / `beat_end` / `left` / `right` / `group`），
  只是 `type` 为 `forbidden`：这段区间里鼠标**必须避开**该区域。
* 编辑器里放置、编辑、拖动、复制、镜像的操作与空域一致，显示为红色
  （半透明红底 + 红色边线）；与空域重叠时按两层半透明叠加显示。
* 合法性：滑键与空域在**任意时刻**都不能被禁区**完全覆盖**（同一时刻的多个禁区
  按横向范围的**并集**计算；禁区的开始 / 结束时刻**同样计入**）。

## 2. 手工编辑提示

* 文件是纯 JSON，可以用脚本批量生成（见 `src/demo.py` 的 `build_demo_chart`）。
* 只需保证 `format` / `version` 存在；缺失字段会用默认值补齐。
* 读档时会自动：排序去重 BPM 与流速段、把第一项钉到第 0 拍、
  修复速度组归属、裁剪非法轨道编号。

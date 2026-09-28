# 谱面文件格式（`.json`）

谱面就是**普通的 JSON 文本文件**（UTF-8、缩进 2 空格，扩展名 `.json`），可以放在任意目录；
音频会复制到**谱面文件所在的文件夹**，谱面中用文件名（相对路径）引用。

> 早期版本使用 `.ivchart` 扩展名，内容同样是 JSON，仍然可以直接打开；
> 保存时一律默认写成 `.json`。

## 1. 顶层结构

```json
{
  "format": "inverus.chart",
  "version": 1,
  "metadata": { "...": "见 1.1" },
  "timing": { "...": "见 1.2" },
  "editor": { "...": "见 1.3" },
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
* BPM **不影响物件运动速度**，只影响“拍 ↔ 秒 / 歌曲位置”的换算。

## 1.3 editor（全局编辑器设置）

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `zoom_seconds` | number | 4.0 | 屏幕上同时显示多长时间内的物件 |
| `grid_division` | int | 4 | 一拍细分为多少个音（1..64） |
| `horizontal_divisions` | int | 0 | 0..1 合法区域几等分（0 = 不启用横向辅助网格） |
| `audio_latency_ms` | number | 0 | 系统音频延迟（毫秒） |

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

### 停留区域

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

## 2. 手工编辑提示

* 文件是纯 JSON，可以用脚本批量生成（见 `src/rhythm_editor/demo.py` 的 `build_demo_chart`）。
* 只需保证 `format` / `version` 存在；缺失字段会用默认值补齐。
* 读档时会自动：排序去重 BPM 与流速段、把第一项钉到第 0 拍、
  修复速度组归属、裁剪非法轨道编号。

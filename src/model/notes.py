"""物件数据模型。

三件套：
* LaneNote     —— 6K 下落式物件：单轨 / 多轨（横跨相邻多条轨道）以及对应的长按。
* MouseRegion  —— 空域：左/右两条边界曲线（首尾位置 + 线型），期间鼠标需留在区域内。
* ForbiddenRegion —— 禁区：几何与编辑逻辑和空域完全相同，但属于必须避开的区域。
* MouseSlide   —— 鼠标滑键：到达判定线时需在范围内朝指定方向滑动。

所有物件都记录 group（所属速度组 id），速度组同时记录自己包含的物件 id，
两者保持一致（见 chart.py 的 assign_group）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, Tuple

import config

NOTE_LANE = "lane"
NOTE_REGION = "region"
NOTE_FORBIDDEN = "forbidden"
NOTE_SLIDE = "slide"


def ease_value(ease: str, progress: float) -> float:
    """把 0..1 的进度映射到 0..1 的插值系数。"""
    p = min(1.0, max(0.0, float(progress)))
    if ease == config.EASE_IN:
        return p * p
    if ease == config.EASE_OUT:
        return 1.0 - (1.0 - p) * (1.0 - p)
    return p


def normalize_ease(ease: str | None) -> str:
    value = (ease or "").strip()
    return value if value in config.EASE_CHOICES else config.EASE_LINEAR


@dataclass
class BoundaryCurve:
    """一条边界：从 start 到 end 的插值，线型为 ease。"""

    start: float = 0.0
    end: float = 0.0
    ease: str = config.EASE_LINEAR

    def value_at(self, progress: float) -> float:
        return self.start + (self.end - self.start) * ease_value(self.ease, progress)

    def mirrored(self) -> "BoundaryCurve":
        """沿 0..1 合法区域中轴线镜像（x -> 1 - x）。"""
        return BoundaryCurve(1.0 - self.start, 1.0 - self.end, self.ease)

    def to_dict(self) -> Dict[str, Any]:
        return {"start": float(self.start), "end": float(self.end), "ease": self.ease}

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "BoundaryCurve":
        data = data or {}
        return cls(
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
            ease=normalize_ease(data.get("ease")),
        )


class Note:
    """所有物件的基类。"""

    kind: str = ""

    def __init__(self, note_id: str, group: int = config.DEFAULT_GROUP_ID) -> None:
        self.id = note_id
        self.group = int(group)

    # ------------------------------------------------------------------ 时间
    @property
    def head_beat(self) -> float:  # pragma: no cover - 子类实现
        raise NotImplementedError

    @property
    def tail_beat(self) -> float:
        return self.head_beat

    @property
    def duration_beats(self) -> float:
        return self.tail_beat - self.head_beat

    # ------------------------------------------------------------------ 变换
    def shift(self, delta_beats: float) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def clone(self, new_id: str, delta_beats: float = 0.0) -> "Note":  # pragma: no cover
        raise NotImplementedError

    def to_dict(self) -> Dict[str, Any]:  # pragma: no cover - 子类实现
        raise NotImplementedError


class LaneNote(Note):
    """6K 下落式物件。

    * lane_start == lane_end  -> 单轨按键
    * lane_start <  lane_end  -> 横跨相邻多轨的按键
    * beat_end  >  beat_start -> 长按（长度 = beat_end - beat_start）
    """

    kind = NOTE_LANE

    def __init__(
        self,
        note_id: str,
        lane_start: int,
        lane_end: int,
        beat_start: float,
        beat_end: float | None = None,
        group: int = config.DEFAULT_GROUP_ID,
    ) -> None:
        super().__init__(note_id, group)
        self.lane_start = int(min(lane_start, lane_end))
        self.lane_end = int(max(lane_start, lane_end))
        self.beat_start = float(beat_start)
        self.beat_end = float(beat_start if beat_end is None else beat_end)
        self.normalize()

    def normalize(self) -> None:
        self.lane_start = max(0, min(config.LANE_COUNT - 1, int(self.lane_start)))
        self.lane_end = max(0, min(config.LANE_COUNT - 1, int(self.lane_end)))
        if self.lane_start > self.lane_end:
            self.lane_start, self.lane_end = self.lane_end, self.lane_start
        if self.beat_end < self.beat_start:
            self.beat_start, self.beat_end = self.beat_end, self.beat_start
        self.beat_start = float(self.beat_start)
        self.beat_end = float(self.beat_end)

    @property
    def is_hold(self) -> bool:
        return self.beat_end - self.beat_start > config.EPS

    @property
    def is_multi(self) -> bool:
        return self.lane_end > self.lane_start

    @property
    def head_beat(self) -> float:
        return self.beat_start

    @property
    def tail_beat(self) -> float:
        return self.beat_end

    def lane_span(self) -> Tuple[int, int]:
        return self.lane_start, self.lane_end

    def shift(self, delta_beats: float) -> None:
        self.beat_start += delta_beats
        self.beat_end += delta_beats

    def clone(self, new_id: str, delta_beats: float = 0.0) -> "LaneNote":
        return LaneNote(
            new_id,
            self.lane_start,
            self.lane_end,
            self.beat_start + delta_beats,
            self.beat_end + delta_beats,
            self.group,
        )

    def mirrored(self) -> None:
        a, b = 5 - self.lane_end, 5 - self.lane_start
        self.lane_start, self.lane_end = a, b

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": NOTE_LANE,
            "lane_start": self.lane_start,
            "lane_end": self.lane_end,
            "beat_start": round(self.beat_start, 9),
            "beat_end": round(self.beat_end, 9),
            "group": self.group,
        }


class MouseNote(Note):
    """鼠标平面物件基类：水平位置使用 0..1 归一化坐标（以合法区域为参照）。"""

    def x_range(self) -> Tuple[float, float]:  # pragma: no cover
        raise NotImplementedError

    def mirrored(self) -> None:  # pragma: no cover
        raise NotImplementedError


class MouseRegion(MouseNote):
    """空域：从 beat_start 出现到 beat_end 消失，期间鼠标需留在区域内。"""

    kind = NOTE_REGION

    def __init__(
        self,
        note_id: str,
        beat_start: float,
        beat_end: float,
        left: BoundaryCurve | None = None,
        right: BoundaryCurve | None = None,
        group: int = config.DEFAULT_GROUP_ID,
    ) -> None:
        super().__init__(note_id, group)
        self.beat_start = float(beat_start)
        self.beat_end = float(beat_end)
        self.left = left or BoundaryCurve(0.0, 0.0, config.EASE_LINEAR)
        self.right = right or BoundaryCurve(1.0, 1.0, config.EASE_LINEAR)
        self.normalize()

    def normalize(self) -> None:
        if self.beat_end < self.beat_start:
            self.beat_start, self.beat_end = self.beat_end, self.beat_start

    @property
    def head_beat(self) -> float:
        return self.beat_start

    @property
    def tail_beat(self) -> float:
        return self.beat_end

    def progress_at(self, beat: float) -> float:
        span = self.beat_end - self.beat_start
        if span <= config.EPS:
            return 0.0
        return min(1.0, max(0.0, (beat - self.beat_start) / span))

    def left_at(self, beat: float) -> float:
        return self.left.value_at(self.progress_at(beat))

    def right_at(self, beat: float) -> float:
        return self.right.value_at(self.progress_at(beat))

    def x_range(self) -> Tuple[float, float]:
        lo = min(self.left.start, self.left.end, self.right.start, self.right.end)
        hi = max(self.left.start, self.left.end, self.right.start, self.right.end)
        return lo, hi

    def contains(self, beat: float, x: float) -> bool:
        if beat < self.beat_start - 1e-9 or beat > self.beat_end + 1e-9:
            return False
        return self.left_at(beat) <= x <= self.right_at(beat)

    def shift(self, delta_beats: float) -> None:
        self.beat_start += delta_beats
        self.beat_end += delta_beats

    def clone(self, new_id: str, delta_beats: float = 0.0) -> "MouseRegion":
        return MouseRegion(
            new_id,
            self.beat_start + delta_beats,
            self.beat_end + delta_beats,
            replace(self.left),
            replace(self.right),
            self.group,
        )

    def mirrored(self) -> None:
        left, right = self.left, self.right
        self.left = right.mirrored()
        self.right = left.mirrored()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.kind,
            "beat_start": round(self.beat_start, 9),
            "beat_end": round(self.beat_end, 9),
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "group": self.group,
        }


class ForbiddenRegion(MouseRegion):
    """禁区：与空域完全相同的几何与放置 / 编辑 / 拖动逻辑，但必须避开。"""

    kind = NOTE_FORBIDDEN

    def clone(self, new_id: str, delta_beats: float = 0.0) -> "ForbiddenRegion":
        return ForbiddenRegion(
            new_id,
            self.beat_start + delta_beats,
            self.beat_end + delta_beats,
            replace(self.left),
            replace(self.right),
            self.group,
        )


class MouseSlide(MouseNote):
    """鼠标滑键：beat 时刻到达判定线，从 x_start 滑向 x_end。

    direction 由首尾位置决定（+1 向右，-1 向左）——编辑器里由第二次点击的位置决定。
    """

    kind = NOTE_SLIDE

    def __init__(
        self,
        note_id: str,
        beat: float,
        x_start: float,
        x_end: float | None = None,
        group: int = config.DEFAULT_GROUP_ID,
    ) -> None:
        super().__init__(note_id, group)
        self.beat = float(beat)
        self.x_start = float(x_start)
        self.x_end = float(x_start if x_end is None else x_end)

    @property
    def direction(self) -> int:
        return 1 if self.x_end >= self.x_start else -1

    @property
    def span(self) -> float:
        return abs(self.x_end - self.x_start)

    @property
    def head_beat(self) -> float:
        return self.beat

    def x_range(self) -> Tuple[float, float]:
        return (min(self.x_start, self.x_end), max(self.x_start, self.x_end))

    def shift(self, delta_beats: float) -> None:
        self.beat += delta_beats

    def clone(self, new_id: str, delta_beats: float = 0.0) -> "MouseSlide":
        return MouseSlide(new_id, self.beat + delta_beats, self.x_start, self.x_end, self.group)

    def mirrored(self) -> None:
        self.x_start, self.x_end = 1.0 - self.x_start, 1.0 - self.x_end

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": NOTE_SLIDE,
            "beat": round(self.beat, 9),
            "x_start": round(self.x_start, 6),
            "x_end": round(self.x_end, 6),
            "group": self.group,
        }


# ---------------------------------------------------------------- 图层顺序
# 渲染（编辑器画布 / 演示视图）与命中测试共用这唯一一份定义。
# 数值越小越先画（越靠下层），越大越靠上层：
# 多轨长按 < 单轨长按 < 多轨单键 < 单轨单键 < 空域 / 禁区 < 滑键。
LAYER_MULTI_HOLD = 0
LAYER_SINGLE_HOLD = 1
LAYER_MULTI_TAP = 2
LAYER_SINGLE_TAP = 3
LAYER_REGION = 4
LAYER_SLIDE = 5
LAYER_MAX = LAYER_SLIDE


def layer_rank(note: Note) -> int:
    """物件所属图层（0 = 最底层，LAYER_MAX = 最顶层）。"""
    if isinstance(note, LaneNote):
        if note.is_hold:
            return LAYER_MULTI_HOLD if note.is_multi else LAYER_SINGLE_HOLD
        return LAYER_MULTI_TAP if note.is_multi else LAYER_SINGLE_TAP
    if isinstance(note, MouseRegion):
        return LAYER_REGION
    if isinstance(note, MouseSlide):
        return LAYER_SLIDE
    return LAYER_MULTI_HOLD


def note_from_dict(data: Dict[str, Any]) -> Note:
    """由字典构造物件（用于读档与撤销快照）。"""
    kind = data.get("type")
    note_id = str(data.get("id", ""))
    group = int(data.get("group", config.DEFAULT_GROUP_ID))
    if kind == NOTE_LANE:
        return LaneNote(
            note_id,
            int(data.get("lane_start", 0)),
            int(data.get("lane_end", data.get("lane_start", 0))),
            float(data.get("beat_start", 0.0)),
            float(data.get("beat_end", data.get("beat_start", 0.0))),
            group,
        )
    if kind == NOTE_REGION:
        return MouseRegion(
            note_id,
            float(data.get("beat_start", 0.0)),
            float(data.get("beat_end", data.get("beat_start", 0.0))),
            BoundaryCurve.from_dict(data.get("left")),  # type: ignore[arg-type]
            BoundaryCurve.from_dict(data.get("right")),  # type: ignore[arg-type]
            group,
        )
    if kind == NOTE_FORBIDDEN:
        return ForbiddenRegion(
            note_id,
            float(data.get("beat_start", 0.0)),
            float(data.get("beat_end", data.get("beat_start", 0.0))),
            BoundaryCurve.from_dict(data.get("left")),  # type: ignore[arg-type]
            BoundaryCurve.from_dict(data.get("right")),  # type: ignore[arg-type]
            group,
        )
    if kind == NOTE_SLIDE:
        return MouseSlide(
            note_id,
            float(data.get("beat", 0.0)),
            float(data.get("x_start", 0.0)),
            float(data.get("x_end", data.get("x_start", 0.0))),
            group,
        )
    raise ValueError(f"unknown note type: {kind!r}")


def sort_key(note: Note) -> Tuple[float, float, str]:
    return (note.head_beat, note.tail_beat, note.id)


def clip_head(data: Dict[str, Any]) -> float:
    """剪贴板 / 快照条目的开头时刻。

    区间物件（长按 / 空域 / 禁区）只有 beat_start，瞬时物件只有 beat；
    这里按字段判断，新增同类物件时不用再改（否则粘贴会按错误的时间偏移落点）。
    """
    if "beat_start" in data:
        return float(data.get("beat_start", 0.0))
    return float(data.get("beat", 0.0))

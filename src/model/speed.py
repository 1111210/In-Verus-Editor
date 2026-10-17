"""速度组：用一个“流速列表”控制一组物件的运动速度，甚至反向运动。

位置推算约定：
每个物件的“判定时刻”一定位于判定线上；物件在其它时刻的位置由它所属速度组的
流速反推得到：

    D(t) = ∫_{t}^{t_judge} v(u) du

t 为判定线当前所在的拍（判定线永远吸附到时间网格上），v 为该组在当前拍的
流速倍率（默认速度 = 1，即与时间轴网格对齐：1 拍的时间 = 1 个网格距离单位）。
D 为正表示物件位于判定线上方（未来侧），为负表示被推向判定线下方（反向运动）。

因为 D 只由流速与拍决定，BPM 列表不会影响物件运动速度。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

import config
from . import segments as seglib

INF = seglib.INF


@dataclass
class SpeedSegment:
    """一条流速记录：从第 start_beat 拍（浮点）开始，流速为默认速度的 multiplier 倍。"""

    start_beat: float = 0.0
    multiplier: float = 1.0

    def to_dict(self) -> Dict[str, float]:
        return {"start_beat": float(self.start_beat), "multiplier": float(self.multiplier)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpeedSegment":
        return cls(float(data.get("start_beat", 0.0)), float(data.get("multiplier", 1.0)))


class SpeedGroup:
    """速度组：segments 为流速列表，note_ids 为组内物件。"""

    def __init__(
        self,
        gid: int,
        name: str,
        segments: Sequence[SpeedSegment] | None = None,
        note_ids: Iterable[str] | None = None,
    ) -> None:
        self.id = int(gid)
        self.name = str(name)
        self.segments: List[SpeedSegment] = []
        self.note_ids: List[str] = list(note_ids or [])
        # note_ids 的成员索引（派生数据，不参与序列化）：避免每次都线性查找
        self._note_set: set = {str(n) for n in self.note_ids}
        self._identity = True
        self._min_multiplier = config.DEFAULT_SPEED_MULTIPLIER
        self.set_segments(segments or [SpeedSegment(0.0, config.DEFAULT_SPEED_MULTIPLIER)])

    # ------------------------------------------------------------------ 规整
    def set_segments(self, segments: Iterable[SpeedSegment]) -> None:
        self.segments = seglib.normalize_segments(
            segments,
            make=lambda s: SpeedSegment(float(s.start_beat), float(s.multiplier)),
            default=lambda: SpeedSegment(0.0, config.DEFAULT_SPEED_MULTIPLIER),
            to_zero=lambda s: SpeedSegment(0.0, s.multiplier),
        )
        self._identity = all(abs(s.multiplier - 1.0) < 1e-9 for s in self.segments)
        self._min_multiplier = min(s.multiplier for s in self.segments)
        self._rebuild_caches()

    def _rebuild_caches(self) -> None:
        """段起点 + 累计积分（∫_0^{段起点} v）：查询从 O(段数) 降到 O(log 段数)。"""
        starts = [float(seg.start_beat) for seg in self.segments]
        cumulative = [0.0]
        for index, seg in enumerate(self.segments):
            if index + 1 < len(self.segments):
                span = starts[index + 1] - starts[index]
                cumulative.append(cumulative[-1] + span * seg.multiplier)
        self._starts: List[float] = starts
        self._cumulative: List[float] = cumulative

    def _index_at(self, beat: float) -> int:
        index = bisect.bisect_right(self._starts, float(beat) + 1e-12) - 1
        if index < 0:
            return 0
        if index >= len(self.segments):
            return len(self.segments) - 1
        return index

    @property
    def is_default(self) -> bool:
        return self.id == config.DEFAULT_GROUP_ID

    @property
    def is_identity(self) -> bool:
        """流速恒为 1（位置与时间网格完全一致），渲染时可以走快速路径。"""
        return self._identity

    @property
    def min_multiplier(self) -> float:
        """整条流速列表里的最小倍率（可能为负 = 反向）。"""
        return self._min_multiplier

    def scroll_seconds(self, timing, from_beat: float, to_beat: float) -> float:
        """∫ v(τ) · spb(τ) dτ（秒）：物件在屏幕上走过的“滚动秒数”。

        * v 是流速倍率（可为 0 或负数），spb 是该拍所在 BPM 段的“每拍秒数”；
        * 默认流速下就等于两拍之间的秒数差 —— 所以 BPM 变更处时间轴格子大小
          会立刻改变（高 BPM 的段落每拍更短）；
        * 流速与 BPM 都是分段常数，跨段时按两套边界切开逐段积分。
        """
        from_beat = float(from_beat)
        to_beat = float(to_beat)
        if abs(to_beat - from_beat) < 1e-12:
            return 0.0
        if to_beat < from_beat:
            return -self.scroll_seconds(timing, to_beat, from_beat)
        # 快速路径：两端落在同一流速段、同一 BPM 段内 -> 分段常数直接相乘
        if (
            self.segment_at(from_beat) is self.segment_at(to_beat)
            and timing.segment_at(from_beat) is timing.segment_at(to_beat)
        ):
            v = self.multiplier_at(from_beat)
            return v * timing.seconds_per_beat(from_beat) * (to_beat - from_beat)
        cuts = {from_beat, to_beat}
        cuts.update(self.segment_starts_between(from_beat, to_beat))
        cuts.update(timing.segment_starts_between(from_beat, to_beat))
        points = sorted(cuts)
        total = 0.0
        for start, end in zip(points, points[1:]):
            v = self.multiplier_at(start)
            total += v * timing.seconds_per_beat(start) * (end - start)
        return total

    # ------------------------------------------------------------------ 查询
    def segment_at(self, beat: float) -> SpeedSegment:
        return self.segments[self._index_at(beat)]

    def multiplier_at(self, beat: float) -> float:
        return self.segment_at(beat).multiplier

    def segment_end(self, index: int) -> float:
        return seglib.segment_end(self.segments, index)

    def cumulative(self, beat: float) -> float:
        """∫_{0}^{beat} v(u) du。"""
        beat = float(beat)
        if beat < 0.0:
            return beat * self.segments[0].multiplier
        index = self._index_at(beat)
        seg = self.segments[index]
        return self._cumulative[index] + (beat - seg.start_beat) * seg.multiplier

    def segment_starts_between(self, start_beat: float, end_beat: float) -> List[float]:
        """(start_beat, end_beat) 内（不含端点）的段起点，按拍升序。"""
        if end_beat <= start_beat:
            return []
        first = bisect.bisect_right(self._starts, float(start_beat))
        out: List[float] = []
        for index in range(first, len(self._starts)):
            value = self._starts[index]
            if value >= end_beat:
                break
            out.append(value)
        return out

    def distance(self, from_beat: float, to_beat: float) -> float:
        """∫_{from}^{to} v(u) du，正数表示向判定线上方（未来侧）移动。"""
        return self.cumulative(to_beat) - self.cumulative(from_beat)

    def judging_distance(self, current_beat: float, judge_beat: float) -> float:
        """判定线在 current_beat 时，判定时刻为 judge_beat 的物件距判定线的距离。"""
        return self.distance(current_beat, judge_beat)

    def is_reversed_at(self, beat: float) -> bool:
        return self.multiplier_at(beat) < 0.0

    # ------------------------------------------------------------------ 组内物件
    def add_notes(self, note_ids: Iterable[str]) -> None:
        for nid in note_ids:
            text = str(nid)
            if text not in self._note_set:
                self._note_set.add(text)
                self.note_ids.append(text)

    def remove_notes(self, note_ids: Iterable[str]) -> None:
        drop = {str(n) for n in note_ids}
        if not (self._note_set & drop):
            return
        self.note_ids = [n for n in self.note_ids if n not in drop]
        self._note_set = {str(n) for n in self.note_ids}

    def set_note_ids(self, note_ids: Iterable[str]) -> None:
        """整体替换成员列表（读档修复用），同时刷新成员索引。"""
        self.note_ids = list(note_ids)
        self._note_set = {str(n) for n in self.note_ids}

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "segments": [s.to_dict() for s in self.segments],
            # 组内成员按 id 排序输出：成员关系是集合语义，排序后存档 / 撤销比较都可复现
            "note_ids": sorted(self.note_ids),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpeedGroup":
        data = data if isinstance(data, dict) else {}
        segments_raw = data.get("segments", [])
        if not isinstance(segments_raw, list):
            segments_raw = []
        segments = []
        for segment in segments_raw:
            if not isinstance(segment, dict):
                continue
            try:
                segments.append(SpeedSegment.from_dict(segment))
            except (TypeError, ValueError):
                continue
        note_ids = data.get("note_ids", [])
        if not isinstance(note_ids, (list, tuple, set)):
            note_ids = []
        return cls(
            int(data.get("id", config.DEFAULT_GROUP_ID)),
            str(data.get("name", config.DEFAULT_GROUP_NAME)),
            segments,
            note_ids,
        )

    @classmethod
    def default(cls) -> "SpeedGroup":
        return cls(
            config.DEFAULT_GROUP_ID,
            config.DEFAULT_GROUP_NAME,
            [SpeedSegment(0.0, config.DEFAULT_SPEED_MULTIPLIER)],
            [],
        )

    def clone(self, gid: int, name: str | None = None) -> "SpeedGroup":
        return SpeedGroup(
            gid,
            name or self.name,
            [SpeedSegment(s.start_beat, s.multiplier) for s in self.segments],
            [],
        )

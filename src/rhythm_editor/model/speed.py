"""速度组：用一个“流速列表”控制一组物件的运动速度，甚至反向运动。

位置推算约定
------------
每个物件的“判定时刻”一定位于判定线上；物件在其它时刻的位置由它所属速度组的
流速反推得到::

    D(t) = ∫_{t}^{t_judge} v(u) du

``t`` 为判定线当前所在的拍（判定线永远吸附到时间网格上），``v`` 为该组在当前拍的
流速倍率（默认速度 = 1，即与时间轴网格对齐：1 拍的时间 = 1 个网格距离单位）。
``D`` 为正表示物件位于判定线上方（未来侧），为负表示被推向判定线下方（反向运动）。

因为 ``D`` 只由流速与 **拍** 决定，BPM 列表不会影响物件运动速度。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from .. import config

INF = float("inf")


@dataclass
class SpeedSegment:
    """一条流速记录：从第 ``start_beat`` 拍（浮点）开始，流速为默认速度的 ``multiplier`` 倍。"""

    start_beat: float = 0.0
    multiplier: float = 1.0

    def to_dict(self) -> Dict[str, float]:
        return {"start_beat": float(self.start_beat), "multiplier": float(self.multiplier)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpeedSegment":
        return cls(float(data.get("start_beat", 0.0)), float(data.get("multiplier", 1.0)))


class SpeedGroup:
    """速度组：``segments`` 为流速列表，``note_ids`` 为组内物件。"""

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
        self._identity = True
        self.set_segments(segments or [SpeedSegment(0.0, config.DEFAULT_SPEED_MULTIPLIER)])

    # ------------------------------------------------------------------ 规整
    def set_segments(self, segments: Iterable[SpeedSegment]) -> None:
        cleaned = [
            SpeedSegment(float(s.start_beat), float(s.multiplier)) for s in segments
        ]
        cleaned.sort(key=lambda s: s.start_beat)
        unique: List[SpeedSegment] = []
        for seg in cleaned:
            if unique and abs(unique[-1].start_beat - seg.start_beat) < 1e-9:
                unique[-1] = seg
            else:
                unique.append(seg)
        if not unique:
            unique = [SpeedSegment(0.0, config.DEFAULT_SPEED_MULTIPLIER)]
        # 第一项统一落在第 0 拍，保证任意时刻都有定义
        unique[0] = SpeedSegment(0.0, unique[0].multiplier)
        self.segments = unique
        self._identity = all(abs(s.multiplier - 1.0) < 1e-9 for s in unique)

    @property
    def is_default(self) -> bool:
        return self.id == config.DEFAULT_GROUP_ID

    @property
    def is_identity(self) -> bool:
        """流速恒为 1（位置与时间网格完全一致），渲染时可以走快速路径。"""
        return self._identity

    # ------------------------------------------------------------------ 查询
    def segment_at(self, beat: float) -> SpeedSegment:
        segs = self.segments
        if beat < segs[0].start_beat:
            return segs[0]
        current = segs[0]
        for seg in segs:
            if seg.start_beat <= beat + 1e-12:
                current = seg
            else:
                break
        return current

    def multiplier_at(self, beat: float) -> float:
        return self.segment_at(beat).multiplier

    def segment_end(self, index: int) -> float:
        if 0 <= index < len(self.segments) - 1:
            return self.segments[index + 1].start_beat
        return INF

    def cumulative(self, beat: float) -> float:
        """∫_{0}^{beat} v(u) du。"""
        beat = float(beat)
        segs = self.segments
        if beat < 0.0:
            return beat * segs[0].multiplier
        total = 0.0
        for i, seg in enumerate(segs):
            if beat <= seg.start_beat:
                break
            end = self.segment_end(i)
            span = min(beat, end) - seg.start_beat
            total += span * seg.multiplier
            if beat <= end:
                break
        return total

    def distance(self, from_beat: float, to_beat: float) -> float:
        """∫_{from}^{to} v(u) du，正数表示向判定线上方（未来侧）移动。"""
        return self.cumulative(to_beat) - self.cumulative(from_beat)

    def judging_distance(self, current_beat: float, judge_beat: float) -> float:
        """判定线在 ``current_beat`` 时，判定时刻为 ``judge_beat`` 的物件距判定线的距离。"""
        return self.distance(current_beat, judge_beat)

    def is_reversed_at(self, beat: float) -> bool:
        return self.multiplier_at(beat) < 0.0

    # ------------------------------------------------------------------ 组内物件
    def add_notes(self, note_ids: Iterable[str]) -> None:
        for nid in note_ids:
            if nid not in self.note_ids:
                self.note_ids.append(str(nid))

    def remove_notes(self, note_ids: Iterable[str]) -> None:
        drop = {str(n) for n in note_ids}
        self.note_ids = [n for n in self.note_ids if n not in drop]

    def clear_notes(self) -> None:
        self.note_ids = []

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
        return cls(
            int(data.get("id", config.DEFAULT_GROUP_ID)),
            str(data.get("name", config.DEFAULT_GROUP_NAME)),
            [SpeedSegment.from_dict(d) for d in data.get("segments", [])],  # type: ignore[arg-type]
            data.get("note_ids", []),  # type: ignore[arg-type]
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

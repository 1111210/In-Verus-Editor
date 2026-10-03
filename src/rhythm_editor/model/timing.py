"""BPM 列表与偏移：拍号 <-> 秒 的双向换算。

设计约定
--------
* 时间轴以 **拍（beat）** 为基本单位，歌曲位置以 **秒** 表示。
* ``beat_to_seconds(b)`` 返回从歌曲开始（第 0 拍）到第 ``b`` 拍经过的秒数。
* 偏移 ``offset_ms``：把歌曲“提前播放”的毫秒数。即第 0 拍对应的歌曲位置是
  ``offset_ms`` 毫秒。因此判定线位于第 b 拍时，歌曲播放位置为
  ``beat_to_seconds(b) * 1000 + offset_ms``。
* BPM 只影响“拍 -> 秒”的换算，**不影响**物件运动速度（见 ``speed.py``）。
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from . import segments as seglib

INF = seglib.INF


@dataclass
class BpmSegment:
    """一条 BPM 记录：从第 ``start_beat`` 拍开始，BPM 为 ``bpm``，每小节 ``beats_per_bar`` 拍。"""

    start_beat: float = 0.0
    bpm: float = 120.0
    beats_per_bar: int = 4

    def to_dict(self) -> Dict[str, object]:
        return {
            "start_beat": float(self.start_beat),
            "bpm": float(self.bpm),
            "beats_per_bar": int(self.beats_per_bar),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "BpmSegment":
        return cls(
            start_beat=float(data.get("start_beat", 0.0)),
            bpm=float(data.get("bpm", 120.0)),
            beats_per_bar=int(data.get("beats_per_bar", 4)),
        )


class TimingMap:
    """BPM 列表 + 偏移的换算器（纯函数式，可安全拷贝）。"""

    def __init__(
        self,
        segments: Sequence[BpmSegment] | None = None,
        offset_ms: float = 0.0,
    ) -> None:
        self.offset_ms = float(offset_ms)
        # 段列表的版本号（派生缓存失效用，不参与序列化）
        self.revision = 0
        self.segments: List[BpmSegment] = []
        self.set_segments(segments or [BpmSegment()])

    # ------------------------------------------------------------------ 规整
    def set_segments(self, segments: Iterable[BpmSegment]) -> None:
        self.segments = seglib.normalize_segments(
            segments,
            make=_clean_segment,
            default=BpmSegment,
            to_zero=_to_zero,
        )
        self.revision += 1
        self._rebuild_caches()

    def _rebuild_caches(self) -> None:
        """预计算段起点、累计秒数与累计小节数。

        只是内存里的派生数据，不参与 :meth:`to_dict` / :meth:`from_dict`：
        拍 ↔ 秒、小节查询因此从 O(段数) 降到 O(log 段数)。
        """
        starts = [float(seg.start_beat) for seg in self.segments]
        prefix = [0.0]
        for index, seg in enumerate(self.segments):
            if index + 1 < len(self.segments):
                span = starts[index + 1] - starts[index]
                prefix.append(prefix[-1] + span * 60.0 / seg.bpm)
        bars = []
        total = 0
        for index, seg in enumerate(self.segments):
            bars.append(total)
            if index + 1 < len(self.segments):
                step = float(seg.beats_per_bar)
                if step > 0:
                    span = starts[index + 1] - starts[index]
                    total += max(1, math.ceil(span / step - 1e-9))
                else:
                    total += 1
        self._starts: List[float] = starts
        self._prefix_seconds: List[float] = prefix
        self._bars_before: List[int] = bars

    def _index_at(self, beat: float) -> int:
        """``beat`` 落在第几段（早于第一段时返回 0）。"""
        index = bisect.bisect_right(self._starts, float(beat) + 1e-12) - 1
        if index < 0:
            return 0
        if index >= len(self.segments):
            return len(self.segments) - 1
        return index

    def copy(self) -> "TimingMap":
        return TimingMap([BpmSegment(s.start_beat, s.bpm, s.beats_per_bar) for s in self.segments],
                         self.offset_ms)

    # ------------------------------------------------------------------ 查询
    def bpm_at(self, beat: float) -> float:
        seg = self.segment_at(beat)
        return seg.bpm

    def beats_per_bar_at(self, beat: float) -> int:
        return self.segment_at(beat).beats_per_bar

    def seconds_per_beat(self, beat: float) -> float:
        return 60.0 / self.bpm_at(beat)

    def segment_at(self, beat: float) -> BpmSegment:
        return self.segments[self._index_at(beat)]

    def segment_end(self, index: int) -> float:
        return seglib.segment_end(self.segments, index)

    # ------------------------------------------------------------------ 换算
    def beat_to_seconds(self, beat: float) -> float:
        beat = float(beat)
        index = self._index_at(beat)
        seg = self.segments[index]
        start = self._starts[index]
        if beat < start:
            # 早于第一段：按第一段的每拍秒数外推（与旧实现对负数拍的处理一致）
            return (beat - start) * 60.0 / seg.bpm
        return self._prefix_seconds[index] + (beat - start) * 60.0 / seg.bpm

    def seconds_to_beat(self, seconds: float) -> float:
        seconds = float(seconds)
        if seconds < 0.0:
            seg = self.segments[0]
            return seg.start_beat + seconds * seg.bpm / 60.0
        index = bisect.bisect_right(self._prefix_seconds, seconds + 1e-12) - 1
        if index < 0:
            index = 0
        elif index >= len(self.segments):
            index = len(self.segments) - 1
        seg = self.segments[index]
        return seg.start_beat + (seconds - self._prefix_seconds[index]) * seg.bpm / 60.0

    # ------------------------------------------- 歌曲位置（毫秒，含偏移）
    def beat_to_song_ms(self, beat: float) -> float:
        """判定线位于第 ``beat`` 拍时，歌曲应播放到的位置（毫秒）。"""
        return self.beat_to_seconds(beat) * 1000.0 + self.offset_ms

    def song_ms_to_beat(self, song_ms: float) -> float:
        return self.seconds_to_beat((float(song_ms) - self.offset_ms) / 1000.0)

    # ------------------------------------------------------------------ 网格
    @staticmethod
    def snap_beat(beat: float, division: int) -> float:
        division = max(1, int(division))
        return round(float(beat) * division) / division

    # ------------------------------------------------------------------ 小节
    def bar_lines(self, start_beat: float, end_beat: float) -> List[float]:
        """返回 [start_beat, end_beat] 内的小节线拍位置。

        规则：**每逢 BPM 变更一定新开一小节**（变更拍本身就是小节线，不要求它落在
        上一段的小节网格上；上一段最后会留下一个不完整的小节）。
        每小节拍数为 ``0`` 的段落表示**关闭小节线**，该段内不产生任何小节线。
        """
        lines: List[float] = []
        if end_beat < start_beat:
            return lines
        # 只从覆盖 start_beat 的那一段开始扫；更早的段与 [start, end] 无交集
        first = self._index_at(start_beat)
        for i in range(first, len(self.segments)):
            seg = self.segments[i]
            if seg.start_beat > end_beat + 1e-9:
                break
            seg_end = self.segment_end(i)
            lo = max(start_beat, seg.start_beat)
            hi = min(end_beat, seg_end)
            if hi < lo - 1e-9:
                continue
            # 每逢 BPM 变更一定有一根小节线（即使这一段关闭了小节线）
            if start_beat - 1e-9 <= seg.start_beat <= end_beat + 1e-9:
                lines.append(round(seg.start_beat, 9))
            step = float(seg.beats_per_bar)
            if step <= 0:
                continue      # 关闭小节线：段内不再有额外的小节线
            n = int((lo - seg.start_beat) / step)
            b = seg.start_beat + n * step
            if b <= seg.start_beat + 1e-9:
                b = seg.start_beat + step      # 起点已经加过了，从下一条开始
            while b < lo - 1e-9:
                b += step
            while b <= hi + 1e-9:
                lines.append(round(b, 9))
                b += step
        # 段落交界处（上一段的结束线 == 下一段的起始线）会重复，去重后再返回
        unique: List[float] = []
        for value in lines:
            if not unique or abs(unique[-1] - value) > 1e-9:
                unique.append(value)
        return unique

    # ------------------------------------------------------- 小节对齐（BPM 变更点）
    def has_bars_at(self, beat: float) -> bool:
        """该拍所在段落是否画**段内**小节线（``beats_per_bar == 0`` 表示关闭）。

        注意：即使关闭，BPM 变更处仍然有一根小节线（见 :meth:`bar_lines`）。
        """
        return int(self.segment_at(beat).beats_per_bar) > 0

    def bar_start_at_or_before(self, beat: float) -> float:
        """返回 ``beat`` 之前（含）最近的一条小节线。

        * 每逢 BPM 变更一定新开一小节 —— 所以每条 BPM 记录的起点都是小节线；
        * 关闭小节线的段落内只有起点那一条线，段内后续拍都算在“这一节”里；
        * 一条都没有时返回第 0 拍。
        """
        beat = float(beat)
        seg = self.segments[self._index_at(beat)]
        step = float(seg.beats_per_bar)
        if step <= 0:
            return float(seg.start_beat)
        n = math.floor((beat - seg.start_beat) / step + 1e-9)
        return seg.start_beat + max(0, n) * step

    def is_bar_start(self, beat: float, tol: float = 1e-6) -> bool:
        """``beat`` 是否正好落在小节线上（BPM 变更处一定有）。"""
        return abs(self.bar_start_at_or_before(beat) - float(beat)) <= tol

    def bar_index_at(self, beat: float) -> Tuple[int, float]:
        """返回 (小节序号（从 0 开始，跨段累计）, 该小节内位置)。

        * 每逢 BPM 变更一定新开一小节：上一段末尾不完整的小节也按**一整节**计入，
          所以第 N 段的第一拍永远是某小节的第一拍；
        * 关闭小节线（``beats_per_bar == 0``）的段落没有小节编号，
          返回 ``(之前累计的小节数, 0.0)``。
        """
        beat = float(beat)
        index = self._index_at(beat)
        seg = self.segments[index]
        bars = self._bars_before[index]
        local = max(0.0, beat - seg.start_beat)
        step = float(seg.beats_per_bar)
        if step <= 0:
            # 该段关闭小节线：这一节从变更处开始，一直延伸下去
            return bars, local
        return bars + int(local // step), local % step

    def min_seconds_per_beat(self, start_beat: float, end_beat: float, fallback_beat: float) -> float:
        """``[start, end]`` 内（含第 0 段）最短的每拍秒数，用于网格抽稀。"""
        fastest = self.seconds_per_beat(fallback_beat)
        if end_beat <= start_beat:
            return fastest
        fastest = min(fastest, 60.0 / self.segments[0].bpm)
        first = bisect.bisect_left(self._starts, float(start_beat))
        for index in range(first, len(self.segments)):
            seg = self.segments[index]
            if seg.start_beat > end_beat:
                break
            fastest = min(fastest, 60.0 / seg.bpm)
        return fastest

    def segment_starts_between(self, start_beat: float, end_beat: float) -> List[float]:
        """``(start_beat, end_beat)`` 内（不含端点）的段起点，按拍升序。"""
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

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, object]:
        return {
            "offset_ms": self.offset_ms,
            "bpm_segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object] | None) -> "TimingMap":
        data = data or {}
        segs = [BpmSegment.from_dict(d) for d in data.get("bpm_segments", [])]  # type: ignore[arg-type]
        return cls(segs or [BpmSegment()], float(data.get("offset_ms", 0.0)))


def _clean_segment(seg: BpmSegment) -> BpmSegment:
    return BpmSegment(
        start_beat=max(0.0, float(seg.start_beat)),
        bpm=max(1e-3, float(seg.bpm)),
        # 每小节 0 拍 = 关闭小节线（允许 0；负数按 0 处理）
        beats_per_bar=max(0, int(seg.beats_per_bar)),
    )


def _to_zero(seg: BpmSegment) -> BpmSegment:
    """第 0 拍必须是第一项（从第 0 拍开始的一项不允许删除）。"""
    if seg.start_beat <= 1e-9:
        return seg
    return BpmSegment(0.0, seg.bpm, seg.beats_per_bar)

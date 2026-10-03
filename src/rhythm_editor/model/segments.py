"""分段常量序列的公共操作。

BPM 列表（``timing.py``）与流速列表（``speed.py``）是同一种数据：
「从某一拍开始，参数保持常量，直到下一条记录」。这里集中两处共用的
排序 / 去重 / 查询逻辑，具体参数的取值范围仍由各自的 ``make`` 负责。
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Protocol, Sequence, TypeVar

INF = float("inf")


class Segment(Protocol):
    """只有起点拍的最小接口。"""

    start_beat: float


T = TypeVar("T", bound=Segment)


def normalize_segments(
    segments: Iterable[T],
    *,
    make: Callable[[T], T],
    default: Callable[[], T],
    to_zero: Callable[[T], T] | None = None,
) -> List[T]:
    """排序、去重（同一拍保留最后一条），并保证列表非空、第一段从第 0 拍起。

    ``make`` 负责把一条记录换算成合法值；``to_zero`` 给出「第一段挪到第 0 拍」
    的写法（默认不改动）。
    """
    cleaned = [make(seg) for seg in segments]
    cleaned.sort(key=lambda seg: seg.start_beat)
    unique: List[T] = []
    for seg in cleaned:
        if unique and abs(unique[-1].start_beat - seg.start_beat) < 1e-9:
            unique[-1] = seg
        else:
            unique.append(seg)
    if not unique:
        unique = [default()]
    if to_zero is not None:
        unique[0] = to_zero(unique[0])
    return unique


def segment_index_at(segments: Sequence[Segment], beat: float, eps: float = 1e-12) -> int:
    """``beat`` 落在第几段；早于第一段时返回 0。"""
    index = 0
    for i, seg in enumerate(segments):
        if seg.start_beat <= beat + eps:
            index = i
        else:
            break
    return index


def segment_at(segments: Sequence[T], beat: float, eps: float = 1e-12) -> T:
    return segments[segment_index_at(segments, beat, eps)]


def segment_end(segments: Sequence[Segment], index: int) -> float:
    """第 ``index`` 段的结束拍；最后一段返回 ``INF``。"""
    if 0 <= index < len(segments) - 1:
        return segments[index + 1].start_beat
    return INF

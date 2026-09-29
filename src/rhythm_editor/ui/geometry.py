"""物件在屏幕上的几何：包围盒、多边形、命中测试。

渲染层与交互层共用这里的计算，保证“看到的”和“点得到的”一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .. import config
from ..model.chart import Chart
from ..model.notes import LaneNote, MouseClick, MouseNote, MouseRegion, MouseSlide, Note
from ..model.speed import SpeedGroup
from .view import ViewState

# 单键厚度（细矩形，以判定时刻为垂直中点）
TAP_MIN_PX = 9.0
SLIDE_MIN_PX = 22.0
REGION_SAMPLES = 40
MIN_SLIDE_PX = 18.0

Bounds = Tuple[float, float, float, float]
Point = Tuple[float, float]


def hit_signature(view: ViewState) -> tuple:
    """命中缓存的“有效性签名”：视图或物件数量一变就失效。"""
    layout = view.layout
    return (
        round(float(view.current_beat), 6),
        round(float(view.px_per_second()), 6),
        int(layout.width),
        int(layout.height),
        round(float(layout.judgment_y), 6),
        len(view.chart.lane_notes),
        len(view.chart.mouse_notes),
    )


def _depth(note: Note) -> int:
    """命中优先级 = 渲染图层顺序（越大越靠上）：

    点击按键 > 滑键 > 空域 > 单轨单键 > 多轨单键 > 单轨长按 > 多轨长按。
    """
    if isinstance(note, MouseClick):
        return 7
    if isinstance(note, MouseSlide):
        return 6
    if isinstance(note, MouseRegion):
        return 5
    if isinstance(note, LaneNote):
        if not note.is_hold and not note.is_multi:
            return 4
        if not note.is_hold and note.is_multi:
            return 3
        if note.is_hold and not note.is_multi:
            return 2
        return 1
    return 0


@dataclass
class HitCache:
    """上一帧渲染时算好的可见物件几何，供点击 / 框选复用。

    渲染器每帧把“真正画出来的”物件（已经过可见性与显示规则过滤）连同它们的
    包围盒（空域还带多边形）写进来，于是命中测试只需遍历屏幕内的物件，
    不必再对整张谱面重算几何。
    """

    signature: tuple = ()
    entries: List[Tuple[Note, Bounds, Optional[List[Point]]]] = field(default_factory=list)

    def valid_for(self, view: ViewState) -> bool:
        return bool(self.entries) and self.signature == hit_signature(view)

    def query_point(self, x: float, y: float) -> List[Note]:
        hits: List[Note] = []
        for note, bounds, polygon in self.entries:
            if polygon is not None:
                if point_in_polygon(x, y, polygon):
                    hits.append(note)
            elif point_in_rect(x, y, bounds, pad=config.HIT_PADDING_PX):
                hits.append(note)
        hits.sort(key=lambda n: (_depth(n), n.head_beat), reverse=True)
        return hits

    def query_box(self, x0: float, y0: float, x1: float, y1: float) -> List[Note]:
        left, right = min(x0, x1), max(x0, x1)
        top, bottom = min(y0, y1), max(y0, y1)
        found: List[Note] = []
        for note, bounds, _polygon in self.entries:
            bx0, by0, bx1, by1 = bounds
            if bx1 < left or bx0 > right or by1 < top or by0 > bottom:
                continue
            found.append(note)
        return found


def lane_note_pads(note: LaneNote) -> Tuple[float, float]:
    pad = 6.0 if note.is_multi else 9.0
    return pad, pad


def lane_note_bounds(note: LaneNote, chart: Chart, view: ViewState) -> Bounds:
    layout = view.layout
    group = chart.group_by_id(note.group)
    x0, x1 = layout.lane_span(note.lane_start, note.lane_end)
    pad, _ = lane_note_pads(note)
    x0 += pad
    x1 -= pad
    y_head = view.note_y(note.beat_start, group)
    if note.is_hold:
        y_tail = view.note_y(note.beat_end, group)
        top, bottom = min(y_head, y_tail), max(y_head, y_tail)
        if bottom - top < TAP_MIN_PX:
            bottom = top + TAP_MIN_PX
    else:
        # 单键：与渲染一致，以判定时刻为垂直中点
        top, bottom = y_head - TAP_MIN_PX / 2.0, y_head + TAP_MIN_PX / 2.0
    return x0, top, x1, bottom


def slide_bounds(note: MouseSlide, chart: Chart, view: ViewState) -> Bounds:
    group = chart.group_by_id(note.group)
    y = view.note_y(note.beat, group)
    x0 = view.norm_to_x(note.x_start)
    x1 = view.norm_to_x(note.x_end)
    if abs(x1 - x0) < MIN_SLIDE_PX:
        center = (x0 + x1) / 2
        x0, x1 = center - MIN_SLIDE_PX / 2, center + MIN_SLIDE_PX / 2
    return min(x0, x1) - 4, y - SLIDE_MIN_PX / 2, max(x0, x1) + 4, y + SLIDE_MIN_PX / 2


def click_bounds(note: MouseClick, chart: Chart, view: ViewState) -> Bounds:
    """鼠标点击按键的命中框：与渲染一致的长方形（宽度 = 2×半宽，高度固定、竖向居中）。"""
    group = chart.group_by_id(note.group)
    y = view.note_y(note.beat, group)
    half = max(1.0, float(note.radius) * view.layout.mouse_width)
    x = view.norm_to_x(note.center)
    return x - half, y - TAP_MIN_PX / 2.0, x + half, y + TAP_MIN_PX / 2.0


def region_polygon(note: MouseRegion, chart: Chart, view: ViewState, samples: int = REGION_SAMPLES) -> List[Point]:
    group = chart.group_by_id(note.group)
    left_edge: List[Point] = []
    right_edge: List[Point] = []
    for beat, progress in sample_beats(note.beat_start, note.beat_end, group, samples):
        y = view.note_y(beat, group)
        left_edge.append((view.norm_to_x(note.left.value_at(progress)), y))
        right_edge.append((view.norm_to_x(note.right.value_at(progress)), y))
    return left_edge + list(reversed(right_edge))


def sample_beats(
    beat_start: float,
    beat_end: float,
    group: SpeedGroup,
    samples: int = REGION_SAMPLES,
) -> List[Tuple[float, float]]:
    """给出采样点 ``(beat, progress)``：等分采样 + 速度段边界（保证折点精确）。

    流速在速度段边界会发生跳变，物件轨迹在那里有折点。只在等分点上采样会把折点
    用直线糊掉，导致“看起来的位置”和“真实积分位置”不一致 —— 因此在采样时强制
    把落在区间内的速度段起点也作为采样点。
    """
    samples = max(1, int(samples))
    span = float(beat_end) - float(beat_start)
    if abs(span) < 1e-12:
        return [(float(beat_start), 0.0)]
    beats = {float(beat_start), float(beat_end)}
    for i in range(samples + 1):
        beats.add(float(beat_start) + span * i / samples)
    for segment in group.segments:
        if min(beat_start, beat_end) < segment.start_beat < max(beat_start, beat_end):
            beats.add(float(segment.start_beat))
    ordered = sorted(beats)
    return [(beat, (beat - beat_start) / span) for beat in ordered]


def region_bounds(note: MouseRegion, chart: Chart, view: ViewState) -> Bounds:
    points = region_polygon(note, chart, view, samples=16)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def note_bounds(note: Note, chart: Chart, view: ViewState) -> Bounds:
    if isinstance(note, LaneNote):
        return lane_note_bounds(note, chart, view)
    if isinstance(note, MouseRegion):
        return region_bounds(note, chart, view)
    if isinstance(note, MouseClick):
        return click_bounds(note, chart, view)
    if isinstance(note, MouseSlide):
        return slide_bounds(note, chart, view)
    return 0.0, 0.0, 0.0, 0.0


def point_in_rect(px: float, py: float, bounds: Bounds, pad: float = 0.0) -> bool:
    x0, y0, x1, y1 = bounds
    return (x0 - pad) <= px <= (x1 + pad) and (y0 - pad) <= py <= (y1 + pad)


def point_in_polygon(px: float, py: float, points: Sequence[Point]) -> bool:
    inside = False
    n = len(points)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = points[i]
        xj, yj = points[j]
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi
            if px < x_cross:
                inside = not inside
        j = i
    return inside


def note_hit(note: Note, chart: Chart, view: ViewState, x: float, y: float) -> bool:
    if isinstance(note, MouseRegion):
        return point_in_polygon(x, y, region_polygon(note, chart, view))
    return point_in_rect(x, y, note_bounds(note, chart, view), pad=config.HIT_PADDING_PX)


def note_in_time(note: Note, current_beat: float) -> bool:
    """物件是否还没到判定时间（或正在判定中）。

    显示规则：**判定线越过物件的判定时刻后就不再显示**。
    长按与鼠标停留区域按结束时刻（``tail_beat``）计算，
    因此按住期间整条长按 / 整个区域依然可见。
    """
    return float(current_beat) <= float(note.tail_beat) + 1e-9


def notes_at(
    chart: Chart,
    view: ViewState,
    x: float,
    y: float,
    limit: Optional[int] = None,
    cache: Optional["HitCache"] = None,
) -> List[Note]:
    """返回该点下的物件（上层优先）。"""
    if cache is not None and cache.valid_for(view):
        hits = cache.query_point(x, y)
        return hits[:limit] if limit is not None else hits
    hits = [
        n
        for n in chart.all_notes()
        if note_in_time(n, view.current_beat) and note_hit(n, chart, view, x, y)
    ]
    hits.sort(key=lambda n: (_depth(n), n.head_beat), reverse=True)
    if limit is not None:
        return hits[:limit]
    return hits


def notes_in_box(
    chart: Chart,
    view: ViewState,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    cache: Optional["HitCache"] = None,
) -> List[Note]:
    if cache is not None and cache.valid_for(view):
        return cache.query_box(x0, y0, x1, y1)
    left, right = min(x0, x1), max(x0, x1)
    top, bottom = min(y0, y1), max(y0, y1)
    found: List[Note] = []
    for note in chart.all_notes():
        if not note_in_time(note, view.current_beat):
            continue
        bx0, by0, bx1, by1 = note_bounds(note, chart, view)
        if bx1 < left or bx0 > right or by1 < top or by0 > bottom:
            continue
        found.append(note)
    return found

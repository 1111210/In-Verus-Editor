"""物件在屏幕上的几何：包围盒、多边形、命中测试。

渲染层与交互层共用这里的计算，保证“看到的”和“点得到的”一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import config
from model.chart import Chart
from model.notes import (
    LaneNote,
    MouseClick,
    MouseRegion,
    MouseSlide,
    Note,
    layer_rank,
)
from model.speed import SpeedGroup
from .view import ViewState

# 单键厚度（细矩形，以判定时刻为垂直中点）
TAP_MIN_PX = 9.0
SLIDE_MIN_PX = 22.0
REGION_SAMPLES = 40
MIN_SLIDE_PX = 18.0

Bounds = Tuple[float, float, float, float]
Point = Tuple[float, float]


def mouse_pane_rect(view: ViewState) -> Bounds:
    """鼠标平面在屏幕上的显示范围（只在横向裁剪，y 方向不限制）。"""
    left, right = view.layout.pane_mouse
    return (float(left), -1e9, float(right), 1e9)


def clip_bounds(bounds: Bounds, rect: Bounds) -> Optional[Bounds]:
    """两个矩形的交集；没有交集时返回 ``None``。"""
    x0 = max(bounds[0], rect[0])
    y0 = max(bounds[1], rect[1])
    x1 = min(bounds[2], rect[2])
    y1 = min(bounds[3], rect[3])
    if x0 > x1 or y0 > y1:
        return None
    return (x0, y0, x1, y1)


def clip_segment(a: Point, b: Point, rect: Bounds) -> Optional[Tuple[Point, Point]]:
    """Liang–Barsky：把线段裁剪到矩形内；完全在外时返回 ``None``。"""
    x0, y0 = a
    x1, y1 = b
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, x0 - rect[0]),
        (dx, rect[2] - x0),
        (-dy, y0 - rect[1]),
        (dy, rect[3] - y0),
    ):
        if abs(p) < 1e-12:
            if q < 0.0:
                return None
            continue
        ratio = q / p
        if p < 0.0:
            if ratio > t1:
                return None
            t0 = max(t0, ratio)
        else:
            if ratio < t0:
                return None
            t1 = min(t1, ratio)
    return (
        (x0 + t0 * dx, y0 + t0 * dy),
        (x0 + t1 * dx, y0 + t1 * dy),
    )


def clip_polyline(points: Sequence[Point], rect: Bounds) -> List[List[Point]]:
    """把折线裁剪到矩形内，返回若干条连续可见段（可能为空）。"""
    runs: List[List[Point]] = []
    current: List[Point] = []
    for index in range(len(points) - 1):
        segment = clip_segment(points[index], points[index + 1], rect)
        if segment is None:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        start, end = segment
        if current and abs(current[-1][0] - start[0]) < 1e-6 and abs(current[-1][1] - start[1]) < 1e-6:
            current.append(end)
        else:
            if len(current) >= 2:
                runs.append(current)
            current = [start, end]
    if len(current) >= 2:
        runs.append(current)
    return runs


def clip_polygon(points: Sequence[Point], rect: Bounds) -> List[Point]:
    """Sutherland–Hodgman：把多边形裁剪到矩形内；完全在外时返回空列表。"""
    if len(points) < 3:
        return []
    x0, y0, x1, y1 = rect
    output: List[Point] = list(points)

    def inside(point: Point, edge: str) -> bool:
        px, py = point
        if edge == "left":
            return px >= x0
        if edge == "right":
            return px <= x1
        if edge == "top":
            return py >= y0
        return py <= y1

    def cross(a: Point, b: Point, edge: str) -> Point:
        ax, ay = a
        bx, by = b
        if edge in ("left", "right"):
            ex = x0 if edge == "left" else x1
            t = (ex - ax) / ((bx - ax) or 1e-12)
            return (ex, ay + t * (by - ay))
        ey = y0 if edge == "top" else y1
        t = (ey - ay) / ((by - ay) or 1e-12)
        return (ax + t * (bx - ax), ey)

    for edge in ("left", "right", "top", "bottom"):
        if not output:
            break
        source, output = output, []
        previous = source[-1]
        previous_inside = inside(previous, edge)
        for current in source:
            current_inside = inside(current, edge)
            if current_inside:
                if not previous_inside:
                    output.append(cross(previous, current, edge))
                output.append(current)
            elif previous_inside:
                output.append(cross(previous, current, edge))
            previous, previous_inside = current, current_inside
    return output if len(output) >= 3 else []


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
        int(getattr(view.chart, "revision", 0)),
    )


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
        # 只看签名：视图里一个物件都没有时 entries 为空，但缓存依然是有效的
        # （随机点在空白处点击不应退化成对整张谱面的全量扫描）。
        return self.signature != () and self.signature == hit_signature(view)

    def query_point(self, x: float, y: float) -> List[Note]:
        hits: List[Note] = []
        for note, bounds, polygon in self.entries:
            if polygon is not None:
                if point_in_polygon(x, y, polygon):
                    hits.append(note)
            elif point_in_rect(x, y, bounds, pad=config.HIT_PADDING_PX):
                hits.append(note)
        hits.sort(key=lambda n: (layer_rank(n), n.head_beat), reverse=True)
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


def region_edges(
    note: MouseRegion, chart: Chart, view: ViewState, samples: int = REGION_SAMPLES
) -> Tuple[List[Point], List[Point]]:
    """空域的左右边界点列（未裁剪，从开头到结尾）。"""
    group = chart.group_by_id(note.group)
    left_edge: List[Point] = []
    right_edge: List[Point] = []
    for beat, progress in sample_beats(note.beat_start, note.beat_end, group, samples):
        y = view.note_y(beat, group)
        left_edge.append((view.norm_to_x(note.left.value_at(progress)), y))
        right_edge.append((view.norm_to_x(note.right.value_at(progress)), y))
    return left_edge, right_edge


def region_polygon(note: MouseRegion, chart: Chart, view: ViewState, samples: int = REGION_SAMPLES) -> List[Point]:
    """空域的屏幕多边形（已裁剪到鼠标平面的显示范围）。"""
    left_edge, right_edge = region_edges(note, chart, view, samples)
    polygon = left_edge + list(reversed(right_edge))
    return clip_polygon(polygon, mouse_pane_rect(view))


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


def region_bounds(note: MouseRegion, chart: Chart, view: ViewState) -> Optional[Bounds]:
    points = region_polygon(note, chart, view, samples=16)
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def note_bounds(note: Note, chart: Chart, view: ViewState) -> Optional[Bounds]:
    """物件在屏幕上的命中包围盒；完全被鼠标平面裁掉时返回 ``None``。

    6K 物件不受裁剪；鼠标平面物件的真实几何可能超出显示范围，命中测试与渲染
    一致地裁剪到 ``pane_mouse``。
    """
    if isinstance(note, LaneNote):
        return lane_note_bounds(note, chart, view)
    if isinstance(note, MouseRegion):
        return region_bounds(note, chart, view)
    if isinstance(note, MouseClick):
        bounds = click_bounds(note, chart, view)
        return clip_bounds(bounds, mouse_pane_rect(view))
    if isinstance(note, MouseSlide):
        bounds = slide_bounds(note, chart, view)
        return clip_bounds(bounds, mouse_pane_rect(view))
    return None


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
    bounds = note_bounds(note, chart, view)
    return bounds is not None and point_in_rect(x, y, bounds, pad=config.HIT_PADDING_PX)


def note_in_time(note: Note, current_beat: float, show_past: bool = False) -> bool:
    """物件是否还没到判定时间（或正在判定中）。

    显示规则：**判定线越过物件的判定时刻后就不再显示**。
    长按与空域按结束时刻（``tail_beat``）计算，
    因此按住期间整条长按 / 整个区域依然可见。
    ``show_past=True`` 时（视图菜单「显示更早的物件」）不做这条过滤，
    已经判过的物件照样绘制在判定线下方。
    """
    if show_past:
        return True
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
    show_past = bool(getattr(view.chart.settings, "show_past_notes", False))
    hits = [
        n
        for n in view.candidate_notes(chart.sorted_notes())
        if note_in_time(n, view.current_beat, show_past) and note_hit(n, chart, view, x, y)
    ]
    hits.sort(key=lambda n: (layer_rank(n), n.head_beat), reverse=True)
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
    show_past = bool(getattr(view.chart.settings, "show_past_notes", False))
    found: List[Note] = []
    for note in view.candidate_notes(chart.sorted_notes()):
        if not note_in_time(note, view.current_beat, show_past):
            continue
        bounds = note_bounds(note, chart, view)
        if bounds is None:
            continue
        bx0, by0, bx1, by1 = bounds
        if bx1 < left or bx0 > right or by1 < top or by0 > bottom:
            continue
        found.append(note)
    return found

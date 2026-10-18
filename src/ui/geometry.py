"""物件在屏幕上的几何：包围盒、多边形、命中测试。

渲染层与交互层共用这里的计算，保证“看到的”和“点得到的”一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import config
from model.chart import Chart
from model.notes import (
    LaneNote,
    MouseRegion,
    MouseSlide,
    Note,
    layer_rank,
    note_stack_key,
)
from model.speed import SpeedGroup
from .view import ViewState

# 单键厚度（细矩形，以判定时刻为垂直中点）
TAP_MIN_PX = 9.0
SLIDE_MIN_PX = 22.0
REGION_SAMPLES = 40
# 半透明重叠补丁的行扫描上限：正常输入远低于它（多边形已裁剪到鼠标平面），
# 这里只是兜底，避免异常输入生成上万多边形点
MAX_OVERLAP_ROWS = 600

Bounds = Tuple[float, float, float, float]
Point = Tuple[float, float]


@dataclass(frozen=True)
class LaneNoteGeometry:
    """6K 物件的唯一屏幕几何，供渲染与命中测试共同使用。"""

    x0: float
    x1: float
    y_head: float
    y_tail: float
    top: float
    bottom: float

    @property
    def bounds(self) -> Bounds:
        return (self.x0, self.top, self.x1, self.bottom)


@dataclass(frozen=True)
class SlideGeometry:
    """鼠标滑键的屏幕位置与已裁剪的可见包围盒。"""

    y: float
    x_start: float
    x_end: float
    x0: float
    x1: float
    direction: float
    bounds: Bounds


def mouse_pane_rect(view: ViewState) -> Bounds:
    """鼠标平面在屏幕上的显示范围（只在横向裁剪，y 方向不限制）。"""
    left, right = view.layout.pane_mouse
    return (float(left), -1e9, float(right), 1e9)


def visible_y_band(view: ViewState, margin: float = 120.0) -> Tuple[float, float]:
    """纵向“值得保留采样点 / 需要参与叠加”的范围：游玩区上下各留一点余量。

    空域 / 禁区在屏幕外的连续段只用首尾两点表示（见
    region_beat_samples），这里就是判定“在不在屏幕内”的那条带。
    """
    layout = view.layout
    return (layout.play_top - margin, layout.play_bottom + margin)


def clip_bounds(bounds: Bounds, rect: Bounds) -> Optional[Bounds]:
    """两个矩形的交集；没有交集时返回 None。"""
    x0 = max(bounds[0], rect[0])
    y0 = max(bounds[1], rect[1])
    x1 = min(bounds[2], rect[2])
    y1 = min(bounds[3], rect[3])
    if x0 > x1 or y0 > y1:
        return None
    return (x0, y0, x1, y1)


def clip_segment(a: Point, b: Point, rect: Bounds) -> Optional[Tuple[Point, Point]]:
    """Liang–Barsky：把线段裁剪到矩形内；完全在外时返回 None。"""
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
    """命中缓存的“有效性签名”：视图、物件数量或显示模式一变就失效。"""
    layout = view.layout
    return (
        round(float(view.current_beat), 6),
        round(float(view.px_per_second()), 6),
        int(layout.width),
        int(layout.height),
        round(float(layout.judgment_y), 6),
        int(getattr(view.chart, "revision", 0)),
        bool(view.settings.ignore_speed_groups),
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
        hits.sort(key=note_stack_key, reverse=True)
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


def lane_note_geometry(note: LaneNote, view: ViewState) -> LaneNoteGeometry:
    """计算单键 / 长按的屏幕几何，供渲染和命中测试共同使用。"""

    layout = view.layout
    group = view.display_group(note)
    x0, x1 = layout.lane_span(note.lane_start, note.lane_end)
    pad, _ = lane_note_pads(note)
    x0 += pad
    x1 -= pad
    y_head = view.note_y(note.beat_start, group)
    y_tail = view.note_y(note.beat_end, group) if note.is_hold else y_head
    top = min(y_head, y_tail)
    bottom = max(y_head, y_tail)
    if note.is_hold:
        if bottom - top < TAP_MIN_PX:
            # 退化成一个点时向上补足：起始位置始终贴在它的时间网格线上
            top = bottom - TAP_MIN_PX
    else:
        top = y_head - TAP_MIN_PX / 2.0
        bottom = y_head + TAP_MIN_PX / 2.0
    return LaneNoteGeometry(x0, x1, y_head, y_tail, top, bottom)


def lane_note_bounds(note: LaneNote, chart: Chart, view: ViewState) -> Bounds:
    return lane_note_geometry(note, view).bounds


def slide_geometry(note: MouseSlide, view: ViewState) -> Optional[SlideGeometry]:
    """计算滑键的屏幕几何；完全落在鼠标平面横向范围外时返回 None。"""

    group = view.display_group(note)
    y = view.note_y(note.beat, group)
    x_start = view.norm_to_x(note.x_start)
    x_end = view.norm_to_x(note.x_end)
    x0, x1 = min(x_start, x_end), max(x_start, x_end)
    if x1 - x0 < 3.0:
        x1 = x0 + 3.0
    bounds = clip_bounds(
        (x0 - 1.5, y - SLIDE_MIN_PX / 2, x1 + 1.5, y + SLIDE_MIN_PX / 2),
        mouse_pane_rect(view),
    )
    if bounds is None:
        return None
    direction = 1.0 if x_end >= x_start else -1.0
    return SlideGeometry(y, x_start, x_end, x0, x1, direction, bounds)


def slide_bounds(note: MouseSlide, chart: Chart, view: ViewState) -> Optional[Bounds]:
    geometry = slide_geometry(note, view)
    return geometry.bounds if geometry is not None else None


def region_edges(
    note: MouseRegion, chart: Chart, view: ViewState, samples: int = REGION_SAMPLES
) -> Tuple[List[Point], List[Point]]:
    """空域的左右边界点列（未裁剪，从开头到结尾）。

    采样点只保留屏幕附近的部分（见 region_beat_samples）：
    长空域 / 禁区加上大量速度段时，屏幕外的段不会产出成百上千个无用点。
    """
    left_edge: List[Point] = []
    right_edge: List[Point] = []
    for beat, progress, y in region_beat_samples(note, chart, view, samples):
        left_edge.append((view.norm_to_x(note.left.value_at(progress)), y))
        right_edge.append((view.norm_to_x(note.right.value_at(progress)), y))
    return left_edge, right_edge


def region_beat_samples(
    note: MouseRegion, chart: Chart, view: ViewState, samples: int = REGION_SAMPLES
):
    """逐点给出空域/禁区的 (beat, progress, y)（按拍升序）。

    长空域 / 禁区 + 大量速度段的关键约束，以及这里的做法：

    * 断点 = 两端 ∪ 速度段边界 ∪ BPM 段边界。段内流速与每拍秒数都是常数，
      y 随拍线性，所以折线用这些点表示就是精确的（变速 / BPM 变化不糊）；
    * 距离按拍增量积分（每段一次乘法），整体 O(段数)，与空域跨度无关；
    * 与可视带不相交的连续段只保留首尾两个点（段内线性 ⇒ 连接的直线仍在该侧，
      形状不变），屏幕外的段边界不产生额外分点；
    * 与可视带相交的段按屏幕高度细分，samples 是所有细分点的总预算 ——
      一个空域被流速推成多段、几段同时可见时，每段都会单独细分，不会丢。
    """
    group = view.display_group(note)
    timing = chart.timing
    start, end = float(note.beat_start), float(note.beat_end)
    span = end - start
    if abs(span) < 1e-12:
        yield start, 0.0, view.note_y(start, group)
        return
    low, high = visible_y_band(view)
    low_beat, high_beat = (start, end) if start <= end else (end, start)
    cuts = {start, end}
    cuts.update(group.segment_starts_between(low_beat, high_beat))
    cuts.update(timing.segment_starts_between(low_beat, high_beat))
    ordered = sorted(cuts)

    judgment_y = view.layout.judgment_y
    px_per_second = view.px_per_second()
    previous_beat = float(view.current_beat)
    distance_seconds = 0.0
    ys: List[float] = []
    for index, beat in enumerate(ordered):
        if index == 0:
            # 第一段是「判定线 → 第一个断点」，可能跨好几个速度段：正常积分
            distance_seconds += group.scroll_seconds(timing, previous_beat, beat)
        else:
            # 相邻断点之间 v 与 spb 都是常数：直接相乘，避免每点都走一遍积分
            distance_seconds += (
                group.multiplier_at(previous_beat)
                * timing.seconds_per_beat(previous_beat)
                * (beat - previous_beat)
            )
        previous_beat = beat
        ys.append(judgment_y - distance_seconds * px_per_second)

    budget = max(2, int(samples))
    emitted: List[Tuple[float, float, float]] = []
    run: List[int] = []          # 连续落在可视带同一侧的段（只记端点下标）
    run_side = 0

    def progress_of(beat: float) -> float:
        return (beat - start) / span

    def push_point(index: int) -> None:
        beat, y = ordered[index], ys[index]
        if emitted and abs(emitted[-1][0] - beat) < 1e-9:
            return
        emitted.append((beat, progress_of(beat), y))

    def flush_run() -> None:
        nonlocal run, run_side
        if run:
            push_point(run[0])
            if len(run) > 1:
                push_point(run[-1])
            run = []
        run_side = 0

    for index in range(len(ordered) - 1):
        y_a, y_b = ys[index], ys[index + 1]
        piece_low, piece_high = (y_a, y_b) if y_a <= y_b else (y_b, y_a)
        if piece_high < low or piece_low > high:
            side = -1 if piece_high < low else 1
            if side != run_side:
                flush_run()
                run_side = side
            run.append(index)
            run.append(index + 1)
            continue
        flush_run()
        push_point(index)
        visible_px = min(piece_high, high) - max(piece_low, low)
        steps = min(budget, max(1, int(visible_px / 18.0) + 1))
        for step in range(1, steps):
            ratio = step / steps
            beat = ordered[index] + (ordered[index + 1] - ordered[index]) * ratio
            y = y_a + (y_b - y_a) * ratio
            progress = progress_of(beat)
            emitted.append((beat, progress, y))
        budget -= max(0, steps - 1)
        push_point(index + 1)
    flush_run()
    emitted.sort(key=lambda item: item[0])
    for item in emitted:
        yield item


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
    """给出采样点 (beat, progress)：等分采样 + 速度段边界（保证折点精确）。

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
    for start in group.segment_starts_between(
        min(beat_start, beat_end), max(beat_start, beat_end)
    ):
        beats.add(float(start))
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
    """物件在屏幕上的命中包围盒；完全被鼠标平面裁掉时返回 None。

    6K 物件不受裁剪；鼠标平面物件的真实几何可能超出显示范围，命中测试与渲染
    一致地裁剪到 pane_mouse。
    """
    if isinstance(note, LaneNote):
        return lane_note_bounds(note, chart, view)
    if isinstance(note, MouseRegion):
        return region_bounds(note, chart, view)
    if isinstance(note, MouseSlide):
        bounds = slide_bounds(note, chart, view)
        return clip_bounds(bounds, mouse_pane_rect(view))
    return None


# ---------------------------------------------------------------- 多边形叠加
def polygon_row_intervals(
    points: Sequence[Point], y: float, eps: float = 1e-9
) -> List[Tuple[float, float]]:
    """扫描线 y 与多边形的交点区间（奇偶规则；无交点时为空列表）。"""
    crossings: List[float] = []
    count = len(points)
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        if abs(y1 - y0) <= eps:
            continue
        low, high = (y0, y1) if y0 < y1 else (y1, y0)
        if low <= y < high:            # 半开区间：顶点只算一次
            crossings.append(x0 + (x1 - x0) * (y - y0) / (y1 - y0))
    crossings.sort()
    return [
        (crossings[i], crossings[i + 1])
        for i in range(0, len(crossings) - 1, 2)
    ]


def polygon_overlap(
    first: Sequence[Point],
    second: Sequence[Point],
    step: float = 3.0,
    band: Optional[Tuple[float, float]] = None,
) -> Optional[List[Point]]:
    """两个简单多边形的交集多边形；无法用单一区间表示时返回 None。

    逐行扫描：每行取两个多边形的横向区间求交，交集必须恰好一段，
    结果拼成一个左右两条边的多边形。用于把两层半透明填充的重叠处补成合成色。
    band 给出真正需要绘制的纵向范围（屏幕），扫描只在这个范围内进行 ——
    长空域 / 禁区被速度组推到很远时，包围盒可能高达几万像素。
    """
    if len(first) < 3 or len(second) < 3:
        return None
    # 交集只可能落在两个包围盒的交集里：先取包围盒交集，避免长空域 /
    # 禁区被扫描几万行（那是纯粹的浪费，还会生成巨大的多边形）
    first_ys = [p[1] for p in first]
    second_ys = [p[1] for p in second]
    top = max(min(first_ys), min(second_ys))
    bottom = min(max(first_ys), max(second_ys))
    if band is not None:
        top = max(top, float(band[0]))
        bottom = min(bottom, float(band[1]))
    if bottom - top < 2.0:
        return None
    rows = max(2, int((bottom - top) / max(1.0, float(step))))
    rows = min(rows, MAX_OVERLAP_ROWS)
    left_points: List[Point] = []
    right_points: List[Point] = []
    started = False
    ended = False
    for index in range(rows + 1):
        if index >= rows:
            # 最后一行贴着下沿取：扫描用半开区间 [low, high)，正好落在下沿的行会被
            # 判成空，补丁因此比真实范围短一行（同时间同边界的空域 / 禁区叠加时，
            # 补丁盖不满整个禁区，看起来禁区比实际短）。真实交集到不了下沿时，
            # 这一行同样取不到区间，不会把补丁撑长。
            y = bottom - 1e-6
        else:
            y = top + (bottom - top) * index / rows
        first_intervals = polygon_row_intervals(first, y)
        second_intervals = polygon_row_intervals(second, y)
        if len(first_intervals) > 1 or len(second_intervals) > 1:
            return None
        if not first_intervals or not second_intervals:
            ended = ended or started
            continue
        x0 = max(first_intervals[0][0], second_intervals[0][0])
        x1 = min(first_intervals[0][1], second_intervals[0][1])
        if x1 - x0 <= 0.5:
            ended = ended or started
            continue
        if ended:
            return None                # 交集在中间断开：不合成单一多边形
        started = True
        left_points.append((x0, y))
        right_points.append((x1, y))
    if len(left_points) < 2:
        return None
    return left_points + list(reversed(right_points))


def polygon_overlap_strips(
    first: Sequence[Point],
    second: Sequence[Point],
    step: float = 2.0,
    band: Optional[Tuple[float, float]] = None,
) -> List[Bounds]:
    """逐行扫描两个多边形的全部交集，返回矩形条。

    这是 ``polygon_overlap`` 的通用兜底：一个扫描行允许多段交集，交集也可以
    在纵向断开。只需要结果的正确性，不做跨行合并等性能优化。
    """
    if len(first) < 3 or len(second) < 3:
        return []
    first_ys = [p[1] for p in first]
    second_ys = [p[1] for p in second]
    top = max(min(first_ys), min(second_ys))
    bottom = min(max(first_ys), max(second_ys))
    if band is not None:
        top = max(top, float(band[0]))
        bottom = min(bottom, float(band[1]))
    if bottom <= top:
        return []

    row_height = max(1e-6, float(step))
    rows = max(1, int(math.ceil((bottom - top) / row_height)))
    strips: List[Bounds] = []
    for index in range(rows):
        y0 = top + (bottom - top) * index / rows
        y1 = top + (bottom - top) * (index + 1) / rows
        y = (y0 + y1) / 2.0
        first_intervals = polygon_row_intervals(first, y)
        second_intervals = polygon_row_intervals(second, y)
        intersections: List[Tuple[float, float]] = []
        for a0, a1 in first_intervals:
            for b0, b1 in second_intervals:
                x0 = max(a0, b0)
                x1 = min(a1, b1)
                if x1 - x0 > 0.25:
                    intersections.append((x0, x1))
        if not intersections:
            continue
        intersections.sort()
        merged: List[Tuple[float, float]] = []
        for x0, x1 in intersections:
            if merged and x0 <= merged[-1][1] + 1e-6:
                merged[-1] = (merged[-1][0], max(merged[-1][1], x1))
            else:
                merged.append((x0, x1))
        for x0, x1 in merged:
            strips.append((x0, y0, x1, y1))
    return strips


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

    显示规则：判定线越过物件的判定时刻后就不再显示。
    长按与空域按结束时刻（tail_beat）计算，
    因此按住期间整条长按 / 整个区域依然可见。
    show_past=True 时（视图菜单「显示更早的物件」）不做这条过滤，
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
    show_past = bool(getattr(view.settings, "show_past_notes", False))
    hits = [
        n
        for n in view.candidate_notes(chart.sorted_notes())
        if note_in_time(n, view.current_beat, show_past) and note_hit(n, chart, view, x, y)
    ]
    hits.sort(key=note_stack_key, reverse=True)
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
    show_past = bool(getattr(view.settings, "show_past_notes", False))
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

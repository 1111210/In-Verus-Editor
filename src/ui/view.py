"""视图变换：屏幕 <-> 时间轴（拍）的换算、落位、吸附与缩放。

两种布局模式
------------
* ``LAYOUT_SPLIT``（编辑器默认）：画布左右分成两个独立区域 ——
  左边是 6K 下落式区域，右边是鼠标平面区域。两个区域**共用同一条判定线**
  （同一个 y）与同一套时间网格，所以滚动 / 缩放天然同步。
* ``LAYOUT_OVERLAY``（演示模式）：只有一个区域，鼠标平面叠加在 6K 的中间四条轨道上。

两个区域的时间映射完全一致（`beat_to_y`），因此切换模式不会改变时间位置。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import List, Optional, Tuple

import config
from model.chart import Chart
from model.notes import Note
from model.speed import SpeedGroup

# 渲染 / 命中候选窗口的像素余量（与渲染器的剔除边界保持一致）：
# RENDER_MARGIN_PX 是画布上下各留的绘制余量，CANDIDATE_PAD_PX 覆盖图形自身厚度。
RENDER_MARGIN_PX = 80.0
CANDIDATE_PAD_PX = 32.0


def _column_rects(bounds: Tuple[float, float], count: int, gap: float = 2.0) -> List[Tuple[float, float]]:
    x0, x1 = bounds
    span = max(1.0, x1 - x0)
    width = (span - gap * (count - 1)) / count
    rects: List[Tuple[float, float]] = []
    x = float(x0)
    for _ in range(count):
        rects.append((x, x + width))
        x += width + gap
    rects[-1] = (rects[-1][0], float(x1))
    return rects


@dataclass
class Layout:
    """游玩区的几何划分（像素）。"""

    width: int = 1200
    height: int = 700
    margin_x: int = 14
    pad_top: int = 8
    pad_bottom: int = 12
    mode: str = config.LAYOUT_SPLIT
    split_ratio: float = config.SPLIT_6K_RATIO

    def __post_init__(self) -> None:
        self.recompute()

    # ------------------------------------------------------------------ 计算
    def recompute(self) -> None:
        self.play_left = self.margin_x
        self.play_right = max(self.margin_x + 10, self.width - self.margin_x)
        self.play_top = self.pad_top
        self.play_bottom = max(self.pad_top + 10, self.height - self.pad_bottom)
        self.judgment_y = (
            self.play_top + (self.play_bottom - self.play_top) * config.JUDGMENT_LINE_RATIO
        )
        self.play_width = self.play_right - self.play_left

        if self.mode == config.LAYOUT_OVERLAY:
            # 演示模式：只占窗口宽度的 1/3，水平居中
            width = self.play_width * float(config.OVERLAY_WIDTH_RATIO)
            center = (self.play_left + self.play_right) / 2.0
            left = max(self.play_left, center - width / 2.0)
            right = min(self.play_right, center + width / 2.0)
            self.overlay_span = (float(left), float(right))
            rects = _column_rects(self.overlay_span, config.LANE_COUNT)
            self.lane_rects = rects
            self.pane_6k = (float(left), float(right))
            mouse_left = rects[config.MOUSE_LANE_FIRST][0]
            mouse_right = rects[config.MOUSE_LANE_LAST][1]
            self.pane_mouse = (mouse_left, mouse_right)
            self.mouse_left = mouse_left
            self.mouse_right = mouse_right
            self.norm_min = 0.0
            self.norm_max = 1.0
        else:
            usable = max(120.0, self.play_width - config.PANE_GAP)
            k6_width = max(120.0, usable * self.split_ratio)
            self.pane_6k = (float(self.play_left), float(self.play_left + k6_width))
            self.pane_mouse = (float(self.play_left + k6_width + config.PANE_GAP), float(self.play_right))
            self.lane_rects = _column_rects(self.pane_6k, config.LANE_COUNT)
            # 鼠标平面显示范围 = 合法区域 0..1 + 两侧各 MOUSE_OVERFLOW_NORM 的溢出余量：
            # 区域边界允许超出 0..1，多出来的部分也要看得见。
            pane_w = max(20.0, self.pane_mouse[1] - self.pane_mouse[0])
            self.norm_min = -float(config.MOUSE_OVERFLOW_NORM)
            self.norm_max = 1.0 + float(config.MOUSE_OVERFLOW_NORM)
            scale = pane_w / (self.norm_max - self.norm_min)
            self.mouse_left = self.pane_mouse[0] - self.norm_min * scale
            self.mouse_right = self.pane_mouse[0] + (1.0 - self.norm_min) * scale

        self.mouse_width = self.mouse_right - self.mouse_left
        # 1 个归一化单位对应多少像素（鼠标平面的显示范围内插用）
        self.norm_scale = self.mouse_width / max(1e-6, self.norm_max - self.norm_min)
        self.divider_x = (self.pane_6k[1] + self.pane_mouse[0]) / 2.0
        if self.mode != config.LAYOUT_OVERLAY:
            self.overlay_span = (float(self.play_left), float(self.play_right))

    # -------------------------------------------------------------- 区域判断
    def in_6k_pane(self, x: float) -> bool:
        return self.pane_6k[0] - 1 <= x <= self.pane_6k[1] + 1

    def in_mouse_pane(self, x: float) -> bool:
        return self.pane_mouse[0] - 1 <= x <= self.pane_mouse[1] + 1

    def in_legal_area(self, x: float) -> bool:
        return self.mouse_left - 1 <= x <= self.mouse_right + 1

    # ------------------------------------------------------------ 分栏拖动
    def usable_width(self) -> float:
        """两个区域可以分配的总宽度（扣掉中间间隔）。"""
        return max(120.0, self.play_width - config.PANE_GAP)

    def min_split_ratio(self) -> float:
        """6K 区域的最小占比（保证两边都还看得清）。"""
        return min(0.45, config.SPLIT_MIN_PANE_PX / self.usable_width())

    def ratio_from_divider(self, x: float) -> float:
        """把画布横坐标换算成 6K 的宽度占比（含最小宽度夹取）。"""
        ratio = (float(x) - self.play_left) / self.usable_width()
        low = self.min_split_ratio()
        return min(1.0 - low, max(low, ratio))

    # -------------------------------------------------------------- 轨道几何
    def lane_span(self, lane_start: int, lane_end: int) -> Tuple[float, float]:
        lane_start = max(0, min(config.LANE_COUNT - 1, lane_start))
        lane_end = max(0, min(config.LANE_COUNT - 1, lane_end))
        if lane_start > lane_end:
            lane_start, lane_end = lane_end, lane_start
        return self.lane_rects[lane_start][0], self.lane_rects[lane_end][1]

    def lane_at(self, x: float) -> Optional[int]:
        """返回该横坐标所在的轨道；不在 6K 区域内返回 None。"""
        if not self.in_6k_pane(x):
            return None
        for index, (x0, x1) in enumerate(self.lane_rects):
            if x0 - 1 <= x <= x1 + 1:
                return index
        return None

    def lane_center(self, lane: int) -> float:
        x0, x1 = self.lane_rects[max(0, min(config.LANE_COUNT - 1, lane))]
        return (x0 + x1) / 2.0


class ViewState:
    """把 (beat, 归一化横向位置) 映射到画布像素。"""

    def __init__(self, chart: Chart, layout: Optional[Layout] = None) -> None:
        self.chart = chart
        self.layout = layout or Layout()
        self.current_beat = 0.0
        self.playing = False

    # ------------------------------------------------------------------ 绑定
    def set_chart(self, chart: Chart) -> None:
        self.chart = chart

    def resize(self, width: int, height: int) -> None:
        self.layout.width = max(320, int(width))
        self.layout.height = max(240, int(height))
        self.layout.recompute()

    def set_layout_mode(self, mode: str) -> None:
        if mode not in (config.LAYOUT_SPLIT, config.LAYOUT_OVERLAY):
            return
        if mode != self.layout.mode:
            self.layout.mode = mode
            self.layout.recompute()

    def set_split_ratio(self, ratio: float) -> None:
        """拖动分界线：设置 6K 区域占比并重算布局（分栏模式下生效）。"""
        low = self.layout.min_split_ratio()
        self.layout.split_ratio = min(1.0 - low, max(low, float(ratio)))
        self.layout.recompute()

    def divider_at(self, x: float) -> bool:
        """该横坐标是否落在分界线的可抓取范围内（叠加模式没有分界线）。"""
        if not self.split_layout:
            return False
        return abs(float(x) - self.layout.divider_x) <= config.DIVIDER_GRAB_PX

    @property
    def split_layout(self) -> bool:
        return self.layout.mode == config.LAYOUT_SPLIT

    # ------------------------------------------------------------------ 缩放
    @property
    def zoom_seconds(self) -> float:
        return self.chart.settings.zoom_seconds

    @property
    def grid_division(self) -> int:
        return self.chart.settings.grid_division

    def seconds_per_beat(self) -> float:
        return self.chart.timing.seconds_per_beat(self.current_beat)

    def px_per_second(self) -> float:
        """纵向比例：判定线以上固定显示 ``zoom_seconds`` 秒，因此是常数。

        这是“BPM 变更处时间轴立刻改变大小”的关键 —— 比例只由缩放决定，
        不随判定线所在的 BPM 段变化，所以判定线经过 BPM 变更时不会整屏突变；
        每拍在屏幕上的长度则 = ``每秒像素 × 该段的每拍秒数``（BPM 越高格子越密）。
        """
        above = max(40.0, self.layout.judgment_y - self.layout.play_top)
        return above / max(1e-6, self.zoom_seconds)

    def px_per_beat_at(self, beat: float) -> float:
        """该拍所在 BPM 段的“每拍像素长度”（高 BPM -> 更短）。"""
        return self.chart.timing.seconds_per_beat(beat) * self.px_per_second()

    def px_per_beat(self) -> float:
        """判定线所在处的一格有多长（渲染密度判断、命中签名用）。"""
        return self.px_per_beat_at(self.current_beat)

    def min_px_per_beat(self, beat_low: float, beat_high: float) -> float:
        """可视范围内**最小**的每拍像素长度（最密的格子），用于决定画不画细分线。"""
        fastest = self.chart.timing.min_seconds_per_beat(
            beat_low, beat_high, self.current_beat
        )
        return fastest * self.px_per_second()

    def visible_beats(self) -> float:
        """当前可视窗口实际覆盖多少拍（按真实映射，BPM 不均匀也准确）。"""
        low, high = self.beat_range()
        return max(0.0, high - low)

    def zoom_by(self, factor: float) -> None:
        settings = self.chart.settings
        settings.zoom_seconds = min(
            config.ZOOM_MAX_SECONDS,
            max(config.ZOOM_MIN_SECONDS, settings.zoom_seconds * float(factor)),
        )

    # ------------------------------------------------------------------ 时间
    def snap_beat(self, beat: float) -> float:
        return self.chart.timing.snap_beat(beat, self.grid_division)

    def grid_step(self) -> float:
        return 1.0 / max(1, self.grid_division)

    def beat_to_y(self, beat: float) -> float:
        """时间网格（默认流速）的映射：两个区域共用。

        纵向按**秒**线性：`y = 判定线 − (两拍的秒数差) × 每秒像素`。
        于是 BPM 变更处之后的格子立刻按新 BPM 变密 / 变疏，判定线经过时不会整屏突变。
        """
        timing = self.chart.timing
        delta = timing.beat_to_seconds(beat) - timing.beat_to_seconds(self.current_beat)
        return self.layout.judgment_y - delta * self.px_per_second()

    def y_to_beat(self, y: float) -> float:
        timing = self.chart.timing
        target = (
            timing.beat_to_seconds(self.current_beat)
            + (self.layout.judgment_y - float(y)) / self.px_per_second()
        )
        return timing.seconds_to_beat(target)

    def note_y(self, beat: float, group: SpeedGroup | None = None) -> float:
        """物件映射：判定时刻一定落在判定线上，其余时刻由速度组反推。"""
        if group is None or group.is_identity:
            return self.beat_to_y(beat)
        distance = group.scroll_seconds(self.chart.timing, self.current_beat, float(beat))
        return self.layout.judgment_y - distance * self.px_per_second()

    def beat_range(self) -> Tuple[float, float]:
        return (
            self.y_to_beat(self.layout.play_bottom),
            self.y_to_beat(self.layout.play_top),
        )

    def candidate_notes(self, notes: List[Note]) -> List[Note]:
        """按可视拍窗口粗筛候选物件（``notes`` 需已按开头拍排序）。

        只在所有速度组都是默认流速时生效：此时位置对拍单调，窗口外的物件不可能
        落进屏幕；流速非 1 / 反向的组会把任意远处的物件推回屏幕，只能返回原列表
        让调用方逐个按真实位置判断。
        """
        for group in self.chart.speed_groups:
            if not group.is_identity:
                return notes
        heads = self.chart.sorted_head_beats()
        if len(heads) != len(notes):       # 传进来的不是本谱面的排序列表：退回全量
            return notes
        beat_hi = self.y_to_beat(self.layout.play_top - RENDER_MARGIN_PX - CANDIDATE_PAD_PX)
        beat_lo = self.y_to_beat(self.layout.height + RENDER_MARGIN_PX + CANDIDATE_PAD_PX)
        if beat_hi < beat_lo:
            beat_lo, beat_hi = beat_hi, beat_lo
        span = self.chart.max_note_span()
        first = bisect.bisect_left(heads, beat_lo - span)
        last = bisect.bisect_right(heads, beat_hi)
        if first == 0 and last == len(notes):
            return notes
        return notes[first:last]

    def scroll_beats(self, delta: float, snap: bool = True) -> None:
        self.current_beat += float(delta)
        if snap:
            self.current_beat = self.snap_beat(self.current_beat)

    def set_beat(self, beat: float, snap: bool = True) -> None:
        self.current_beat = self.snap_beat(beat) if snap else float(beat)

    # ---------------------------------------------------------- 竖向滚动条
    def scroll_doc_range(self) -> Tuple[float, float]:
        """滚动条代表的“文档范围”（**秒**）：谱面起点到终点，两端各留一点余量。"""
        timing = self.chart.timing
        low, high = self.chart.beat_bounds()
        low_s = timing.beat_to_seconds(min(0.0, low))
        high_s = timing.beat_to_seconds(max(high, 1.0))
        window_low, window_high = self.beat_range()
        span = max(1e-6, timing.beat_to_seconds(window_high) - timing.beat_to_seconds(window_low))
        pad = max(self.seconds_per_beat(), span * 0.25)
        return low_s - pad, high_s + pad

    def scroll_fractions(self) -> Tuple[float, float]:
        """当前可视时间窗口在滚动条上的位置（0..1），用于设置滑块。

        **方向是反的**：滚动条顶端 = 曲末、底端 = 曲首（往下拖是往回走），
        这样“往下拖 = 时间往前走”的旧手感被反过来。
        """
        timing = self.chart.timing
        low, high = self.scroll_doc_range()
        span = max(1e-6, high - low)
        window_low = timing.beat_to_seconds(self.y_to_beat(self.layout.play_bottom))
        window_high = window_low + self.visible_seconds()
        first = min(max((window_low - low) / span, 0.0), 1.0)
        last = min(max((window_high - low) / span, first + 0.01), 1.0)
        return 1.0 - last, 1.0 - first

    def window_fraction(self) -> float:
        """可视窗口在文档范围内占多大比例（= 滚动条滑块的长度）。"""
        low, high = self.scroll_doc_range()
        span = max(1e-6, high - low)
        return min(1.0, self.visible_seconds() / span)

    def set_from_fraction(self, fraction: float) -> None:
        """把滑块放到滚动条比例 ``fraction`` 处（方向同 :meth:`scroll_fractions`）。

        ``fraction`` 是**滑块上边缘**的位置，而滑块长度等于可视窗口占比，
        所以窗口的“低端”（较早的一侧）在 ``1 - fraction - 窗口占比`` 处 ——
        少减这一项，拖动时滑块就会先跳出去一个滑块长度再跟着鼠标走。
        """
        timing = self.chart.timing
        low, high = self.scroll_doc_range()
        position = 1.0 - float(fraction) - self.window_fraction()
        position = min(1.0, max(0.0, position))
        window_low = low + position * (high - low)
        self.set_beat(timing.seconds_to_beat(window_low + self.seconds_below()))

    def visible_seconds(self) -> float:
        """可视窗口一共覆盖多少秒（判定线以上固定为 ``zoom_seconds``）。"""
        return (
            self.layout.play_bottom - self.layout.play_top
        ) / max(1e-6, self.px_per_second())

    def seconds_below(self) -> float:
        """判定线下面那一段可视区对应多少秒。"""
        return (
            self.layout.play_bottom - self.layout.judgment_y
        ) / max(1e-6, self.px_per_second())

    # ------------------------------------------------------------------ 横向
    def norm_to_x(self, value: float) -> float:
        """0..1 的规范化横坐标 → 鼠标平面像素坐标（按真实值映射，不裁剪）。

        鼠标平面的显示范围是 ``layout.pane_mouse``：超出它的部分由渲染层与命中
        测试裁剪（见 ``ui/geometry.py`` 的裁剪工具）。把坐标夹到面板边界会让
        物件的边界看起来落在屏幕边上。
        """
        return self.layout.mouse_left + float(value) * self.layout.mouse_width

    def x_to_norm(self, x: float) -> float:
        return (float(x) - self.layout.mouse_left) / max(1e-6, self.layout.mouse_width)

    def snap_norm(self, value: float) -> float:
        """按横向辅助网格吸附（未启用时保持原值）。"""
        divisions = self.chart.settings.horizontal_divisions
        if divisions <= 0:
            return float(value)
        return round(float(value) * divisions) / divisions

    def in_mouse_pane(self, x: float) -> bool:
        """是否位于鼠标平面这块画布区域内（比合法区域更宽，含溢出余量）。"""
        return self.layout.in_mouse_pane(x)

    def in_play_area(self, x: float, y: float) -> bool:
        return (
            self.layout.play_left - 2 <= x <= self.layout.play_right + 2
            and self.layout.pad_top <= y <= self.layout.height
        )

    # ------------------------------------------------------------------ 小组件
    def time_label(self, beat: float) -> str:
        seconds = self.chart.timing.beat_to_seconds(beat)
        minutes = int(seconds // 60)
        rest = seconds - minutes * 60
        return f"{minutes:02d}:{rest:05.2f}"

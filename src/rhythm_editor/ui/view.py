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

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .. import config
from ..model.chart import Chart
from ..model.speed import SpeedGroup


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
            rects = _column_rects((self.play_left, self.play_right), config.LANE_COUNT)
            self.lane_rects = rects
            self.pane_6k = (float(self.play_left), float(self.play_right))
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

    # -------------------------------------------------------------- 区域判断
    def in_6k_pane(self, x: float) -> bool:
        return self.pane_6k[0] - 1 <= x <= self.pane_6k[1] + 1

    def in_mouse_pane(self, x: float) -> bool:
        return self.pane_mouse[0] - 1 <= x <= self.pane_mouse[1] + 1

    def in_legal_area(self, x: float) -> bool:
        return self.mouse_left - 1 <= x <= self.mouse_right + 1

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
        self.show_horizontal_grid = True
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

    @property
    def layout_mode(self) -> str:
        return self.layout.mode

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

    def visible_beats(self) -> float:
        return self.zoom_seconds / max(1e-6, self.seconds_per_beat())

    def px_per_beat(self) -> float:
        above = max(40.0, self.layout.judgment_y - self.layout.play_top)
        return above / max(1e-6, self.visible_beats())

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
        """时间网格（默认流速）的映射：两个区域共用。"""
        return self.layout.judgment_y - (float(beat) - self.current_beat) * self.px_per_beat()

    def y_to_beat(self, y: float) -> float:
        return self.current_beat + (self.layout.judgment_y - float(y)) / self.px_per_beat()

    def note_y(self, beat: float, group: SpeedGroup | None = None) -> float:
        """物件映射：判定时刻一定落在判定线上，其余时刻由速度组反推。"""
        if group is None or group.is_identity:
            return self.beat_to_y(beat)
        distance = group.distance(self.current_beat, float(beat))
        return self.layout.judgment_y - distance * self.px_per_beat()

    def beat_range(self) -> Tuple[float, float]:
        ppb = self.px_per_beat()
        up = (self.layout.judgment_y - self.layout.play_top) / ppb
        down = (self.layout.play_bottom - self.layout.judgment_y) / ppb
        return self.current_beat - down, self.current_beat + up

    def scroll_beats(self, delta: float, snap: bool = True) -> None:
        self.current_beat += float(delta)
        if snap:
            self.current_beat = self.snap_beat(self.current_beat)

    def set_beat(self, beat: float, snap: bool = True) -> None:
        self.current_beat = self.snap_beat(beat) if snap else float(beat)

    def screen_center_beat(self) -> float:
        low, high = self.beat_range()
        return (low + high) / 2.0

    # ---------------------------------------------------------- 竖向滚动条
    def scroll_doc_range(self) -> Tuple[float, float]:
        """滚动条代表的“文档范围”（拍）：谱面起点到终点，两端各留一点余量。"""
        low, high = self.chart.beat_bounds()
        pad = max(1.0, self.visible_beats() * 0.25)
        return min(0.0, low) - pad, max(high, 1.0) + pad

    def scroll_fractions(self) -> Tuple[float, float]:
        """当前可视时间窗口在文档范围内的位置（0..1），用于设置滚动条滑块。"""
        low, high = self.scroll_doc_range()
        span = max(1e-6, high - low)
        window_low, window_high = self.beat_range()
        first = min(max((window_low - low) / span, 0.0), 1.0)
        last = min(max((window_high - low) / span, first + 0.01), 1.0)
        return first, last

    def set_from_fraction(self, fraction: float) -> None:
        """把可视窗口的“低端”（较早的一侧）移到文档的指定比例处。"""
        low, high = self.scroll_doc_range()
        window_low = low + float(fraction) * (high - low)
        below = (self.layout.play_bottom - self.layout.judgment_y) / max(1e-6, self.px_per_beat())
        self.set_beat(window_low + below)

    # ------------------------------------------------------------------ 横向
    def norm_to_x(self, value: float) -> float:
        """0..1 的规范化横坐标 -> 鼠标平面像素坐标（超出显示范围的部分在边界截断）。"""
        x = self.layout.mouse_left + float(value) * self.layout.mouse_width
        return min(max(x, self.layout.pane_mouse[0]), self.layout.pane_mouse[1])

    def x_to_norm(self, x: float) -> float:
        return (float(x) - self.layout.mouse_left) / max(1e-6, self.layout.mouse_width)

    def snap_norm(self, value: float) -> float:
        """按横向辅助网格吸附（未启用时保持原值）。"""
        divisions = self.chart.settings.horizontal_divisions
        if divisions <= 0:
            return float(value)
        return round(float(value) * divisions) / divisions

    def horizontal_grid_values(self) -> List[float]:
        divisions = self.chart.settings.horizontal_divisions
        if divisions <= 0:
            return []
        return [i / divisions for i in range(divisions + 1)]

    def in_mouse_area(self, x: float) -> bool:
        """是否位于鼠标平面的合法区域内（0..1 的范围）。"""
        return self.layout.in_legal_area(x)

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

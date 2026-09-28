"""视图变换：屏幕 <-> 时间轴（拍）的换算、落位、吸附与缩放。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .. import config
from ..model.chart import Chart
from ..model.speed import SpeedGroup


@dataclass
class Layout:
    """游玩区的几何划分（像素）。"""

    width: int = 1200
    height: int = 700
    margin_x: int = 14
    pad_top: int = 8
    pad_bottom: int = 26

    def __post_init__(self) -> None:
        self.recompute()

    def recompute(self) -> None:
        self.play_left = self.margin_x
        self.play_right = max(self.margin_x + 10, self.width - self.margin_x)
        self.play_top = self.pad_top
        self.play_bottom = max(self.pad_top + 10, self.height - self.pad_bottom)
        self.judgment_y = self.play_top + (self.play_bottom - self.play_top) * config.JUDGMENT_LINE_RATIO
        span = (self.play_right - self.play_left)
        gap = 2
        lane_w = (span - gap * (config.LANE_COUNT - 1)) / config.LANE_COUNT
        self.lane_rects: List[Tuple[float, float]] = []
        x = float(self.play_left)
        for _ in range(config.LANE_COUNT):
            self.lane_rects.append((x, x + lane_w))
            x += lane_w + gap
        self.lane_rects[-1] = (self.lane_rects[-1][0], float(self.play_right))
        self.mouse_left = self.lane_rects[config.MOUSE_LANE_FIRST][0]
        self.mouse_right = self.lane_rects[config.MOUSE_LANE_LAST][1]
        self.mouse_width = self.mouse_right - self.mouse_left

    # -------------------------------------------------------------- 轨道几何
    def lane_span(self, lane_start: int, lane_end: int) -> Tuple[float, float]:
        lane_start = max(0, min(config.LANE_COUNT - 1, lane_start))
        lane_end = max(0, min(config.LANE_COUNT - 1, lane_end))
        if lane_start > lane_end:
            lane_start, lane_end = lane_end, lane_start
        return self.lane_rects[lane_start][0], self.lane_rects[lane_end][1]

    def lane_at(self, x: float) -> Optional[int]:
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
        """时间网格（默认流速）的映射。"""
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

    # ------------------------------------------------------------------ 横向
    def norm_to_x(self, value: float) -> float:
        return self.layout.mouse_left + float(value) * self.layout.mouse_width

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
        return self.layout.mouse_left - 2 <= x <= self.layout.mouse_right + 2

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

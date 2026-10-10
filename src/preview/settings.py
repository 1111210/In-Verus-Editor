"""演示视图的渲染参数（编辑器「演示视图设置」的落点）。

这些参数只影响画面上物件的样子：厚度、流速、侧轨高亮，以及随播放倍速变化的
滚动速度。它们存在编辑器的全局设置里（EditorSettings.demo_*），与谱面文件、
与任何其它设置都互相独立。
"""

from __future__ import annotations

from dataclasses import dataclass

from .scene import UNIT_SCALE

# 流速 1.0 对应的可视毫秒：物件从轨道尽头走到判定线需要的歌曲毫秒。
BASE_SCROLL_MS = 1500.0

THICKNESS_MIN = 0.05
THICKNESS_MAX = 1.0
PATTERN_THICKNESS_MIN = 0.005
PATTERN_THICKNESS_MAX = 0.3
SPEED_MIN = 1.0
SPEED_MAX = 10.0


@dataclass
class ViewSettings:
    """演示视图的绘制参数（世界单位：鼠标合法区宽 = 1）。"""

    note_thickness: float = 0.35 * UNIT_SCALE          # 单键
    multi_thickness: float = 0.35 * UNIT_SCALE         # 多轨单键
    slide_thickness: float = 0.03 * UNIT_SCALE         # 滑键图案
    speed: float = 1.0                                 # 流速 1.0 ~ 10.0
    side_lane_highlight: bool = False                  # 6K 第 1 / 第 6 轨高亮
    rate: float = 1.0                                  # 当前播放倍速（由编辑器传入）

    def normalize(self) -> None:
        self.note_thickness = min(
            THICKNESS_MAX, max(THICKNESS_MIN, float(self.note_thickness))
        )
        self.multi_thickness = min(
            THICKNESS_MAX, max(THICKNESS_MIN, float(self.multi_thickness))
        )
        self.slide_thickness = min(
            PATTERN_THICKNESS_MAX, max(PATTERN_THICKNESS_MIN, float(self.slide_thickness))
        )
        self.speed = min(SPEED_MAX, max(SPEED_MIN, float(self.speed)))
        self.side_lane_highlight = bool(self.side_lane_highlight)
        self.rate = max(0.01, float(self.rate))

    @property
    def scroll_ms(self) -> float:
        """可视毫秒：流速越大，物件从轨道尽头走到判定线越快。"""
        return BASE_SCROLL_MS / max(0.1, float(self.speed))

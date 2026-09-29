"""配色与字体。

Tk 画布不支持真正的 alpha 通道，半透明效果用 ``stipple``（点阵填充）来模拟。
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config


@dataclass(frozen=True)
class Theme:
    # 基础
    bg: str = "#000000"
    panel: str = "#000000"
    panel_alt: str = "#141414"
    play_bg: str = "#000000"
    pane_bg: str = "#050505"
    lane_alt: str = "#000000"
    lane_alt2: str = "#0a0a0a"
    lane_border: str = "#2f2f2f"
    lane_border_strong: str = "#4d4d4d"
    lane_side_highlight: str = "#3a2352"   # 侧轨高亮：第 1 / 第 6 轨背景（暗紫）

    grid_sub: str = "#1a1a1a"
    grid_beat: str = "#2b2b2b"
    grid_bar: str = "#4f4f4f"
    grid_label: str = "#808080"
    hgrid: str = "#333333"
    legal_border: str = "#7fa8ff"

    judge_line: str = "#ffffff"
    judge_glow: str = "#7aa2ff"

    note_white: str = "#ffffff"
    note_border: str = "#b9c6da"
    note_blue: str = "#4da6ff"        # 按键的亮蓝色环 / 长按两侧边线
    note_line_width: int = 3          # 白色横线 / 竖线粗细（单键与长按一致）
    hold_pink: str = "#ff8ec7"
    hold_border: str = "#ffc0e0"

    region_fill: str = "#2f6df6"
    region_edge: str = "#3f8bff"
    slide_fill: str = "#25c06b"
    slide_edge: str = "#25e07f"
    # 向左滑键：半透明黄色（与向右的绿色区分开）
    slide_left_fill: str = "#e8c24a"
    slide_left_edge: str = "#ffd666"

    # 鼠标点击按键：左右两条红色竖线标出范围 + 中间一条红色横线（粗细都与 6K 按键的中心实线相同）
    #             + 中央红色实心菱形
    click_edge: str = "#ff2d20"
    click_diamond: str = "#ff2d20"

    select: str = "#ffd166"
    pending: str = config.PENDING_COLOR
    rubber: str = "#8fb4ff"
    text: str = "#e8e8e8"
    text_dim: str = "#909090"
    text_warn: str = "#ffb454"
    accent: str = "#59b0ff"

    # 半透明（点阵）
    stipple_fill: str = "gray50"
    stipple_soft: str = "gray25"
    stipple_strong: str = "gray75"

    # 字号
    font_family: str = "Microsoft YaHei UI"
    font_small: int = 9
    font_normal: int = 10
    font_large: int = 12
    font_title: int = 14

    def font(self, size: int | None = None, bold: bool = False):
        """返回 Tk 字体元组（纯数据，任何 Tk 组件都能直接用）。"""
        size = size or self.font_normal
        family = self.font_family
        return (family, size, "bold") if bold else (family, size)


DARK = Theme()

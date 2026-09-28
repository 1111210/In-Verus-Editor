"""配色与字体。

Tk 画布不支持真正的 alpha 通道，半透明效果用 ``stipple``（点阵填充）来模拟。
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config


@dataclass(frozen=True)
class Theme:
    # 基础
    bg: str = "#0f131b"
    panel: str = "#151b26"
    panel_alt: str = "#1b2230"
    play_bg: str = "#101622"
    lane_alt: str = "#131a27"
    lane_alt2: str = "#161e2c"
    lane_border: str = "#39455a"
    lane_border_strong: str = "#55637d"

    grid_sub: str = "#1e2637"
    grid_beat: str = "#2b3549"
    grid_bar: str = "#4b5a78"
    grid_label: str = "#6f7d95"
    hgrid: str = "#31405c"
    legal_border: str = "#7fa8ff"

    judge_line: str = "#f2f5fa"
    judge_glow: str = "#7aa2ff"

    note_white: str = "#ffffff"
    note_border: str = "#b9c6da"
    hold_pink: str = "#ff8ec7"
    hold_border: str = "#ffc0e0"

    region_fill: str = "#2f6df6"
    region_edge: str = "#3f8bff"
    slide_fill: str = "#25c06b"
    slide_edge: str = "#25e07f"

    select: str = "#ffd166"
    pending: str = config.PENDING_COLOR
    rubber: str = "#8fb4ff"
    text: str = "#dbe3f0"
    text_dim: str = "#8b97ad"
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

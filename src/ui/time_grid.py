"""时间网格线的分类与样式。

只负责把“拍内位置 + 网格密度”转换为完整的画布样式，不接触 Tk 或画布状态。
完整样式（包括空 stipple）让渲染器在档位切换时可以覆盖旧对象的所有样式字段。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .theme import Theme


@dataclass(frozen=True)
class TimeGridLineStyle:
    fill: str
    width: int
    stipple: str = ""


_RED_FRACTIONS = frozenset({(1, 4), (1, 2), (3, 4)})
_GREEN_FRACTIONS = frozenset({(1, 8), (3, 8), (5, 8), (7, 8)})
_BLUE_FRACTIONS = frozenset(
    {
        (1, 12),
        (1, 6),
        (1, 3),
        (5, 12),
        (7, 12),
        (2, 3),
        (5, 6),
        (11, 12),
    }
)
BLUE_MAX_DENOMINATOR = 12


def _reduced_fraction(numerator: int, denominator: int) -> tuple[int, int]:
    divisor = math.gcd(abs(int(numerator)), int(denominator))
    return int(numerator) // divisor, int(denominator) // divisor


def subdivision_line_style(
    theme: Theme, numerator: int, denominator: int
) -> Optional[TimeGridLineStyle]:
    """返回拍内细分线样式；``None`` 表示高密度网格中应省略该线。"""
    fraction = _reduced_fraction(numerator, denominator)
    if fraction in _RED_FRACTIONS:
        return TimeGridLineStyle(theme.grid_sub_red, 1, theme.stipple_soft)
    if fraction in _GREEN_FRACTIONS:
        return TimeGridLineStyle(theme.grid_sub_green, 1, theme.stipple_soft)

    # 高密度网格只保留二分、四分与八分位置，避免六分 / 十二分线过密。
    if denominator > BLUE_MAX_DENOMINATOR:
        return None
    if fraction in _BLUE_FRACTIONS:
        return TimeGridLineStyle(theme.grid_sub_blue, 1, theme.stipple_soft)
    return TimeGridLineStyle(theme.grid_sub, 1)


def time_grid_line_style(
    theme: Theme,
    *,
    index: int,
    division: int,
    is_beat: bool,
    is_bar: bool,
    draw_subdivision: bool,
    draw_beat: bool,
) -> Optional[TimeGridLineStyle]:
    """返回一条时间网格线的完整样式；``None`` 表示当前密度下不绘制。"""
    if is_bar:
        return TimeGridLineStyle(theme.grid_bar, 2)
    if is_beat:
        return TimeGridLineStyle(theme.grid_beat, 1) if draw_beat else None
    if not draw_subdivision:
        return None
    return subdivision_line_style(theme, index % division, division)

"""演示视图预设贴图的加载与规格化。

规则（在“加载中”阶段一次性做完，避免游戏内卡顿）：

* 单像素图（空域边界 / 填充 / 判定线等）：文件里出现多个像素时，
  取所有像素的平均值（颜色与 alpha 分别平均），得到一张 1×1 的图；
* 其他图：尺寸和期望不符时拉伸到期望尺寸（平滑缩放）；
* 文件缺失或读不出来：按期望尺寸生成占位图（单像素图用白色半透明），保证游戏仍能跑。

贴图默认放在 assets/，缺失时按下文的占位规则生成。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import pygame

LANE_COUNT = 6

# 长按中间那条纯白竖线占键宽的比例：贴图与软渲染手绘白线必须用同一个值，
# 否则开头 / 主体接缝处会一粗一细。
HOLD_LINE_FRACTION = 1.0 / 8.0

# 期望尺寸（宽, 高）：与实际文件不符时会拉伸 / 取平均
EXPECTED_TAP = (96, 160)
EXPECTED_HOLD_HEAD = (96, 96)
EXPECTED_HOLD_BODY = (32, 128)
# 滑键贴图是两倍高的：可见楔形 / 箭头画在上半、下半完全透明，
# 按“图片中心对齐鼠标运动平面”摆放（见 preview/frame.py::slide_center）
EXPECTED_SLIDE_ARROW = (48, 96)
EXPECTED_SLIDE_BODY = (256, 256)
EXPECTED_LANE = (128, 512)
EXPECTED_LANE_EDGE = (8, 512)
EXPECTED_REGION_AREA = (256, 256)
EXPECTED_MOUSE_MARKER = (96, 96)
SINGLE_PIXEL_SIZE = (1, 1)

# 单像素图（多像素时取平均）：空域（蓝）与禁区（红）
SINGLE_PIXEL_NAMES = (
    "region_fill",
    "region_edge",
    "region_line",
    "forbidden_fill",
    "forbidden_edge",
    "forbidden_line",
)


def expected_sizes() -> Dict[str, Tuple[int, int]]:
    """所有贴图名字 -> 期望尺寸。"""
    sizes: Dict[str, Tuple[int, int]] = {name: SINGLE_PIXEL_SIZE for name in SINGLE_PIXEL_NAMES}
    sizes["region_area"] = EXPECTED_REGION_AREA
    sizes["forbidden_area"] = EXPECTED_REGION_AREA
    sizes["mouse_marker_solid"] = EXPECTED_MOUSE_MARKER
    sizes["mouse_marker_outline"] = EXPECTED_MOUSE_MARKER
    for lane in range(1, LANE_COUNT + 1):
        sizes[f"tap_{lane}"] = EXPECTED_TAP
        sizes[f"hold_head_{lane}"] = EXPECTED_HOLD_HEAD
        sizes[f"hold_body_{lane}"] = EXPECTED_HOLD_BODY
        sizes[f"lane_{lane}_default"] = EXPECTED_LANE
        sizes[f"lane_{lane}_pressed"] = EXPECTED_LANE
    for name in ("left", "right"):
        sizes[f"slide_arrow_{name}"] = EXPECTED_SLIDE_ARROW
        sizes[f"slide_body_{name}"] = EXPECTED_SLIDE_BODY
    for side in ("left", "right"):
        sizes[f"lane_edge_{side}_highlight"] = EXPECTED_LANE_EDGE
        sizes[f"lane_edge_{side}_highlight_pressed"] = EXPECTED_LANE_EDGE
    return sizes


def average_color(surface: pygame.Surface) -> Tuple[int, int, int, int]:
    """一张图的平均像素（颜色与 alpha 一起平均）。"""
    width, height = surface.get_size()
    sums = [0, 0, 0, 0]
    count = 0
    step_x = max(1, width // 32)
    step_y = max(1, height // 32)
    for y in range(0, height, step_y):
        for x in range(0, width, step_x):
            pixel = surface.get_at((x, y))
            for index in range(4):
                sums[index] += pixel[index]
            count += 1
    if count == 0:                                  # pragma: no cover - 空图
        return (255, 255, 255, 255)
    return tuple(int(round(value / count)) for value in sums)  # type: ignore[return-value]


def normalize_surface(name: str, surface: pygame.Surface, expected: Tuple[int, int]) -> pygame.Surface:
    """按规则把一张贴图规格化：单像素图取平均，其它拉伸到期望尺寸。"""
    if name in SINGLE_PIXEL_NAMES:
        if surface.get_size() != SINGLE_PIXEL_SIZE:
            color = average_color(surface)
            averaged = pygame.Surface(SINGLE_PIXEL_SIZE, pygame.SRCALPHA)
            averaged.set_at((0, 0), color)
            return averaged
        if surface.get_flags() & pygame.SRCALPHA:
            return surface
        converted = pygame.Surface(SINGLE_PIXEL_SIZE, pygame.SRCALPHA)
        converted.set_at((0, 0), surface.get_at((0, 0)))
        return converted
    if surface.get_size() != expected:
        return pygame.transform.smoothscale(surface, expected)
    if not surface.get_flags() & pygame.SRCALPHA:
        return surface.convert_alpha()
    return surface


def placeholder(name: str, expected: Tuple[int, int]) -> pygame.Surface:
    """缺失贴图的占位：单像素图给白色半透明，其它给透明图。"""
    surface = pygame.Surface(expected, pygame.SRCALPHA)
    if name in SINGLE_PIXEL_NAMES:
        surface.set_at((0, 0), (255, 255, 255, 180))
    return surface


def load_textures(directory: str, names: Optional[List[str]] = None) -> Dict[str, pygame.Surface]:
    """加载并规格化所有贴图（在加载页调用一次即可）。"""
    sizes = expected_sizes()
    result: Dict[str, pygame.Surface] = {}
    for name, expected in sizes.items():
        if names is not None and name not in names:
            continue
        path = os.path.join(directory, f"{name}.png")
        surface: Optional[pygame.Surface] = None
        if os.path.isfile(path):
            try:
                loaded = pygame.image.load(path)
                try:
                    surface = loaded.convert_alpha()   # 需要已创建显示；没有就原样用
                except Exception:                      # pragma: no cover - 无显示环境
                    surface = loaded
            except Exception:                       # pragma: no cover - 坏文件
                surface = None
        if surface is None:
            result[name] = placeholder(name, expected)
            continue
        try:
            result[name] = normalize_surface(name, surface, expected)
        except Exception:                           # pragma: no cover - 极端尺寸
            result[name] = placeholder(name, expected)
    return result

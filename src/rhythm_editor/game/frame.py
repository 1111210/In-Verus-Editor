"""游玩画面的世界坐标描述：**软件渲染与 Panda3D 渲染共用的唯一几何来源**。

两条渲染路径（pygame 软件投影 / Panda3D 卡片）只负责把这里算出的形状投到
屏幕或场景里，不再各自推导物件的位置、尺寸与图层。这样同一个物件在两条路径上
一定落在同一处，跨后端切换时画面不会跳变。

坐标单位与 :mod:`rhythm_editor.game.scene` 一致：鼠标合法区宽度 = 6K 中间四轨
宽度 = 1 单位。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..model.chart import Chart
from ..model.notes import LaneNote, MouseClick, MouseRegion, MouseSlide, Note, layer_rank
from ..model.timing import TimingMap
from .scene import (
    K6_LEFT,
    LANE_COUNT,
    LANE_WIDTH,
    MOUSE_LEFT,
    MOUSE_LEGAL_WIDTH,
    MOUSE_PLANE_HEIGHT,
    UNIT_SCALE,
)

# 6K 按键左右各留出的轨宽比例：贴图两侧自带透明收边，这里再留一点，
# 免得相邻轨道的按键边缘贴在一起。
NOTE_SIDE_INSET = 0.02
# 单键贴图两侧各有一段透明收边：实测 alpha≥128 的可见宽度 ≈ 贴图宽度的 92/96。
# 长按的头 / 主体贴图是不透明的，要按同样比例向内收，视觉宽度才与单键一致。
TAP_VISIBLE_FRACTION = 92.0 / 96.0
# 长按开头的可见下限（正常情况下与单键同厚度）。
HOLD_HEAD_MIN = 0.25 * UNIT_SCALE
# 滑键 / 鼠标点击图案的厚度下限。
PATTERN_MIN_THICKNESS = 0.006 * UNIT_SCALE


# ---------------------------------------------------------------- 可见性
def head_ms(timing: TimingMap, note: Note) -> float:
    """物件的判定（开头）时刻，歌曲毫秒。"""
    return timing.beat_to_song_ms(note.head_beat)


def end_ms(timing: TimingMap, note: Note) -> float:
    """物件停止显示的末尾时刻：长按 / 空域按末尾算，其余按判定时刻。"""
    if isinstance(note, LaneNote) and note.is_hold:
        return timing.beat_to_song_ms(note.tail_beat)
    if isinstance(note, MouseRegion):
        return timing.beat_to_song_ms(note.beat_end)
    return head_ms(timing, note)


def note_visible(timing: TimingMap, note: Note, now_ms: float) -> bool:
    """判定时刻已过的物件不再显示（长按 / 空域画到它们的末尾过去为止）。"""
    return now_ms <= end_ms(timing, note) + 1e-6


def note_ahead(timing: TimingMap, note: Note, now_ms: float, span_ms: float) -> bool:
    """物件是否已进入可视范围（比可视跨度更远的物件不画）。"""
    return head_ms(timing, note) - now_ms <= span_ms


def visible_notes(chart: Chart, now_ms: float, span_ms: float) -> List[Note]:
    """当前应绘制的物件，按图层（自下而上）与判定时刻排序。"""
    timing = chart.timing
    notes = [
        note
        for note in chart.all_notes()
        if note_ahead(timing, note, now_ms, span_ms)
        and note_visible(timing, note, now_ms)
    ]
    notes.sort(key=lambda note: (layer_rank(note), note.head_beat))
    return notes


# ---------------------------------------------------------------- 世界坐标
@dataclass(frozen=True)
class LaneShape:
    """6K 按键（单键 / 多轨 / 长按）的横向范围与贴图序号。"""

    x0: float
    x1: float
    texture_index: int


@dataclass(frozen=True)
class SlideShape:
    """滑键的起止位置与方向（世界坐标）。"""

    start_x: float
    end_x: float
    left_x: float
    right_x: float
    direction: int


@dataclass(frozen=True)
class ClickShape:
    """鼠标点击按键的横向范围与中心（世界坐标）。"""

    x0: float
    x1: float
    center: float


def lane_shape(note: LaneNote) -> LaneShape:
    low = min(note.lane_start, note.lane_end)
    high = max(note.lane_start, note.lane_end)
    x0 = K6_LEFT + low * LANE_WIDTH + LANE_WIDTH * NOTE_SIDE_INSET
    x1 = K6_LEFT + (high + 1) * LANE_WIDTH - LANE_WIDTH * NOTE_SIDE_INSET
    if note.is_hold:
        # 长按按单键的**可见宽度**收窄（左右对称，中心不变）
        inset = (x1 - x0) * (1.0 - TAP_VISIBLE_FRACTION) / 2.0
        x0 += inset
        x1 -= inset
    texture_index = min(LANE_COUNT, max(1, low + 1))
    return LaneShape(x0, x1, texture_index)


def slide_shape(note: MouseSlide) -> SlideShape:
    start_x = MOUSE_LEFT + note.x_start * MOUSE_LEGAL_WIDTH
    end_x = MOUSE_LEFT + note.x_end * MOUSE_LEGAL_WIDTH
    return SlideShape(
        start_x=start_x,
        end_x=end_x,
        left_x=min(start_x, end_x),
        right_x=max(start_x, end_x),
        direction=1 if note.direction >= 0 else -1,
    )


def click_shape(note: MouseClick) -> ClickShape:
    low, high = note.x_range()
    x0 = MOUSE_LEFT + min(low, high) * MOUSE_LEGAL_WIDTH
    x1 = MOUSE_LEFT + max(low, high) * MOUSE_LEGAL_WIDTH
    return ClickShape(x0, x1, MOUSE_LEFT + note.center * MOUSE_LEGAL_WIDTH)


def region_bounds(note: MouseRegion, beat: float) -> Tuple[float, float]:
    """空域在某一拍处的左右边界（世界横坐标）。"""
    return (
        MOUSE_LEFT + note.left_at(beat) * MOUSE_LEGAL_WIDTH,
        MOUSE_LEFT + note.right_at(beat) * MOUSE_LEGAL_WIDTH,
    )


def hold_head_height(note_thickness: float) -> float:
    """长按开头的高度：与单键同厚度，但不小于 :data:`HOLD_HEAD_MIN`。"""
    return max(HOLD_HEAD_MIN, note_thickness)


def pattern_height(thickness: float) -> float:
    """滑键 / 鼠标点击图案的可见厚度（含下限）。"""
    return max(PATTERN_MIN_THICKNESS, thickness)


def click_center(thickness: float) -> Tuple[float, float]:
    """点击图案的 ``(竖直中心 y, 竖直尺寸)``。

    贴图内容占满整张图，中心落在鼠标运动平面上（``MOUSE_PLANE_HEIGHT``）——
    与编辑器画布一致（那里图案以判定时刻为垂直中点），不是底边贴在平面上。
    """
    return (MOUSE_PLANE_HEIGHT, pattern_height(thickness))


def click_span(thickness: float) -> Tuple[float, float]:
    """点击图案的竖直范围 ``(下沿, 上沿)``：上下各一半。"""
    center, height = click_center(thickness)
    half = height / 2.0
    return (center - half, center + half)


# 滑键主体 / 箭头贴图是“可见楔形在上半、下半完全透明”的双倍高图
# （见 ``tools/make_player_textures.py::slide_body``）：按图片中心对齐鼠标平面
# 摆放时，可见的楔形依旧整条落在平面上方（底边贴平面），与旧版视觉一致。
SLIDE_IMAGE_SCALE = 2.0


def slide_center(thickness: float) -> Tuple[float, float]:
    """滑键（主体 / 箭头）的 ``(竖直中心 y, 竖直尺寸)``。

    与点击图案同一套约定：**图片中心**落在鼠标运动平面上；滑键贴图是双倍高，
    所以可见的楔形只占上半、落在平面上方。
    """
    return (MOUSE_PLANE_HEIGHT, SLIDE_IMAGE_SCALE * pattern_height(thickness))


def slide_span(thickness: float) -> Tuple[float, float]:
    """滑键贴图画到画面上的竖直范围 ``(下沿, 上沿)``（可见楔形只占上半）。

    由 :func:`slide_center` 推出；软件渲染按这两个高度投影，Panda3D 路径
    直接用其中心与尺寸画卡片。
    """
    center, height = slide_center(thickness)
    half = height / 2.0
    return (center - half, center + half)

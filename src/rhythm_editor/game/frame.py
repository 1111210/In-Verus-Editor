"""游玩画面的世界坐标描述：**软件渲染与 Panda3D 渲染共用的唯一几何来源**。

两条渲染路径（pygame 软件投影 / Panda3D 卡片）只负责把这里算出的形状投到
屏幕或场景里，不再各自推导物件的位置、尺寸与图层。这样同一个物件在两条路径上
一定落在同一处，跨后端切换时画面不会跳变。

坐标单位与 :mod:`rhythm_editor.game.scene` 一致：鼠标合法区宽度 = 6K 中间四轨
宽度 = 1 单位。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

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
    START_Z,
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


def scroll_ms(chart: Chart, group, now_ms: float, to_beat: float) -> float:
    """``now_ms`` 时刻到 ``to_beat`` 拍，该速度组滚过的**毫秒**（可为负 = 反向）。

    与编辑器 ``ViewState.note_y`` 共用同一套语义：
    ``∫ v(τ)·spb(τ) dτ``（见 ``model/speed.py::SpeedGroup.scroll_seconds``）——
    判定时刻一定落在判定线上，其它时刻由所属速度组反推。
    """
    timing = chart.timing
    now_beat = timing.song_ms_to_beat(now_ms)
    return group.scroll_seconds(timing, now_beat, float(to_beat)) * 1000.0


def note_scroll_ms(chart: Chart, note: Note, now_ms: float) -> float:
    """物件**开头**距判定线的滚动毫秒（用它所属速度组积分）。"""
    return scroll_ms(chart, chart.group_of_note(note), now_ms, note.head_beat)


def note_ahead(chart: Chart, note: Note, now_ms: float, span_ms: float) -> bool:
    """物件是否已进入可视范围。

    按**滚动距离**判断，而不是拍差：流速可正可负，拍差排序 ≠ 位置排序。
    """
    return note_scroll_ms(chart, note, now_ms) <= span_ms


def _note_z_span_at(
    chart: Chart, note: Note, now_beat: float, span_ms: float
) -> Optional[Tuple[float, float]]:
    """``note_z_span`` 的按拍版本（避免每条物件都换算一次当前时刻）。"""
    timing = chart.timing
    head = float(note.head_beat)
    tail = float(note.tail_beat)
    start = max(head, float(now_beat))
    if start > tail + 1e-9:
        return None                      # 这段已经判完，没有要画的部分
    group = chart.group_of_note(note)
    start_z = z_from_distance(
        group.scroll_seconds(timing, now_beat, start) * 1000.0, span_ms
    )
    if abs(tail - start) < 1e-9:          # 单键 / 滑键 / 点击：只有判定时刻那一点
        return (start_z, start_z)
    tail_z = z_from_distance(
        group.scroll_seconds(timing, now_beat, tail) * 1000.0, span_ms
    )
    return (min(start_z, tail_z), max(start_z, tail_z))


def note_z_span(
    chart: Chart, note: Note, now_ms: float, span_ms: float
) -> Optional[Tuple[float, float]]:
    """物件**还要绘制的那一段**的世界 z 范围 ``(近端, 远端)``。

    判定区间里**时间已经过去的部分不画**：长按 / 空域从「当前时刻」画到它们的
    结束时刻（``Note.tail_beat``），所以正在判定的长按只剩还没走完的那一截，
    不会在判定线之前拖出一条已经判过的尾巴；整个物件都已经过去时返回 ``None``。
    单键 / 滑键 / 鼠标点击只占判定时刻那一点。
    """
    return _note_z_span_at(chart, note, chart.timing.song_ms_to_beat(now_ms), span_ms)


def note_in_view(
    chart: Chart,
    note: Note,
    now_ms: float,
    span_ms: float,
    z_near: float,
) -> bool:
    """物件此刻是否应当绘制：**时间与位置两个条件都要满足**。

    * 时间：判定还没结束（``note_visible``：单键 / 滑键 / 点击到判定时刻，
      长按 / 空域到结束时刻）——编辑器演示模式与游玩器用的是同一条规则；
    * 位置：物件**还没过去的那部分**的 z 范围与可视范围 ``[z_near, START_Z]`` 相交
      （``z_near`` 由 ``PlayerScene.visible_z_near`` 按窗口尺寸估算）。
    """
    if not note_visible(chart.timing, note, now_ms):
        return False
    span_z = note_z_span(chart, note, now_ms, span_ms)
    if span_z is None:
        return False
    low, high = span_z
    return high >= z_near and low <= START_Z


def z_from_distance(distance_ms: float, span_ms: float, start_z: Optional[float] = None) -> float:
    """滚动距离（毫秒）→ 世界 z：``0`` = 判定线、``span_ms`` = 轨道尽头。

    **不夹取**：反向流速的距离为负 → z 为负（判定平面与镜头之间）。调用方按
    “可视范围 ~ 运动起点”（``PlayerScene.visible_z_near`` ~ ``START_Z``）判断可见性。
    """
    span_z = START_Z if start_z is None else float(start_z)
    return span_z * float(distance_ms) / max(1.0, float(span_ms))


def distance_from_z(z: float, span_ms: float, start_z: Optional[float] = None) -> float:
    """:func:`z_from_distance` 的逆运算：世界 z → 滚动距离（毫秒）。"""
    span_z = START_Z if start_z is None else float(start_z)
    return float(span_ms) * float(z) / max(1e-6, span_z)


def region_samples(
    chart: Chart,
    note: MouseRegion,
    now_ms: float,
    span_ms: float,
    samples: int = 32,
    near_ms: float = 0.0,
) -> List[List[Tuple[float, float]]]:
    """空域在当前画面上的可见段：``[[(beat, distance_ms), ...], ...]``。

    * 采样在**拍**上等分，并强制包含速度段边界（流速跳变处是折点，不能糊掉）；
    * **时间已经过去的部分不采样**（``beat < 当前拍``）：空域正在判定时只画
      还没走完的那一段，不在判定线之前拖一条已经判过的尾巴；
    * 只保留滚动距离落在 ``[near_ms, span_ms]`` 的部分：``near_ms`` 由相机对
      该平面的可视下界（``PlayerScene.visible_z_near``）换算而来，默认 0 = 判定线；
      超出轨道尽头的点不再采样，否则它们会全挤在远端相互叠色（看起来像空域堆在
      平面尽头）；
    * 距离在相邻采样之间是线性的（段边界已在采样点上），交点用线性插值精确定位；
    * 流速可正可负，同一空域可能被推成多段，所以返回**若干段**。
    """
    group = chart.group_of_note(note)
    timing = chart.timing
    start, end = float(note.beat_start), float(note.beat_end)
    span_beats = end - start
    if abs(span_beats) < 1e-12:
        return []
    now_beat = timing.song_ms_to_beat(now_ms)
    beats = {start, end}
    steps = max(1, int(samples))
    for index in range(1, steps):
        beats.add(start + span_beats * index / steps)
    for segment in group.segments:
        if min(start, end) < segment.start_beat < max(start, end):
            beats.add(float(segment.start_beat))
    if min(start, end) < now_beat < max(start, end):
        beats.add(float(now_beat))       # 时间下界也作为采样点：截断位置精确
    points = [(beat, scroll_ms(chart, group, now_ms, beat)) for beat in sorted(beats)]
    points = [item for item in points if item[0] >= now_beat - 1e-9]
    if len(points) < 2:
        return []
    low, high = float(near_ms), float(span_ms)
    runs: List[List[Tuple[float, float]]] = []
    current: List[Tuple[float, float]] = []
    for (beat_a, dist_a), (beat_b, dist_b) in zip(points, points[1:]):
        piece: List[Tuple[float, float]]
        if abs(dist_b - dist_a) < 1e-12:
            piece = [(beat_a, dist_a), (beat_b, dist_b)] if low <= dist_a <= high else []
        else:
            t0 = (low - dist_a) / (dist_b - dist_a)
            t1 = (high - dist_a) / (dist_b - dist_a)
            begin, finish = max(0.0, min(t0, t1)), min(1.0, max(t0, t1))
            if finish - begin < 1e-9:
                piece = []
            else:
                piece = [
                    (beat_a + (beat_b - beat_a) * begin, dist_a + (dist_b - dist_a) * begin),
                    (beat_a + (beat_b - beat_a) * finish, dist_a + (dist_b - dist_a) * finish),
                ]
        if not piece:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        if current and abs(current[-1][0] - piece[0][0]) < 1e-9:
            current.extend(piece[1:])
        else:
            if len(current) >= 2:
                runs.append(current)
            current = list(piece)
    if len(current) >= 2:
        runs.append(current)
    return runs


def region_crosses_line(
    chart: Chart,
    note: MouseRegion,
    now_ms: float,
    span_ms: float,
    near_ms: float = 0.0,
) -> bool:
    """空域此刻是否正压在判定线上（可见段跨过距离 0）。

    用来决定要不要在判定线上画空域高亮：时间没到 / 已经结束的空域不该留一条亮线。
    """
    return runs_cross_line(region_samples(chart, note, now_ms, span_ms, near_ms=near_ms))


def runs_cross_line(runs: List[List[Tuple[float, float]]]) -> bool:
    """``region_samples`` 的可见段里是否有跨过距离 0 的（= 空域压在判定线上）。"""
    for run in runs:
        distances = [distance for _beat, distance in run]
        if min(distances) <= 0.0 <= max(distances):
            return True
    return False


def visible_notes(chart: Chart, now_ms: float, span_ms: float) -> List[Note]:
    """当前应绘制的物件：按图层（自下而上）排序，层内**由远到近**。

    距离与剔除都用所属速度组的滚动毫秒（``∫v``），因此流速不同的物件
    不会因为“判定时刻更晚”就被错误地排到更近 / 更远的位置。

    可见性按**物件还没判过的那一段**判断（``_note_z_span_at``）：反向流速时
    物件开头可能还在很远处，而它的后半段已经跑到判定线**靠近摄像机的一侧** ——
    只按开头剔除会把整段一起丢掉。``all_notes()`` 里绝大多数物件都远在可视
    范围之外，所以流速全非负时先用“起点距离”这个严格下界快速剔除。
    """
    timing = chart.timing
    now_beat = timing.song_ms_to_beat(now_ms)
    group_cache: dict = {}
    entries = []
    for note in chart.all_notes():
        head = head_ms(timing, note)
        start_beat = max(float(note.head_beat), now_beat)
        if start_beat > float(note.tail_beat) + 1e-9:
            continue                                  # 判定已经全部结束
        group = group_cache.get(note.group)
        if group is None:
            group = chart.group_of_note(note)
            group_cache[note.group] = group
        if group.min_multiplier >= 0.0 and group.min_multiplier * (head - now_ms) > span_ms:
            continue                                   # 再快也整段都在可视跨度之外
        span_z = _note_z_span_at(chart, note, now_beat, span_ms)
        if span_z is None:
            continue
        low_z, high_z = span_z
        if low_z > START_Z:
            continue                                   # 整段都在运动起点之外
        entries.append((
            layer_rank(note),
            -distance_from_z(high_z, span_ms),         # 层内由远到近
            note.head_beat,
            note.id,
            note,
        ))
    entries.sort(key=lambda item: item[:4])
    return [item[4] for item in entries]


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

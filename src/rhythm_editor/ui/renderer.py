"""把谱面与视图状态画到画布上。

布局
----
* 编辑器（``LAYOUT_SPLIT``）：左右两个独立区域 —— 左边 6K 下落式、右边鼠标平面。
  两个区域共用同一条判定线的 y 坐标与同一套时间网格，因此滚动 / 缩放同步。
* 演示模式（``LAYOUT_OVERLAY``）：只有一个区域，鼠标平面叠加在 6K 的中间四条轨道上。

渲染顺序（自下而上）：
背景 -> 区域底色 / 轨道 -> 时间网格 -> 合法区域边界 -> 横向辅助网格
-> 物件（多轨长按 -> 单轨长按 -> 多轨单键 -> 单轨单键 -> 空域 -> 滑键 -> 点击按键）
-> 判定线 -> 区域标题 -> 预览幽灵 / 橡皮筋 -> HUD

物件的七层顺序刻意“从大到小”摆放：即使谱面故意写成非法重叠，
小按键也压在长按之上、半透明空域与滑键再压在 6K 物件之上，交叠处依然可读。

时间轴导航由右侧朴素的竖向滚动条负责（不在画布上画总览条）。
"""

from __future__ import annotations

import math
import tkinter.font as tkfont
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .. import config
from ..model.chart import Chart
from ..model.notes import (
    BoundaryCurve,
    LaneNote,
    MouseClick,
    MouseNote,
    MouseRegion,
    MouseSlide,
    Note,
)
from ..model.speed import SpeedGroup
from .geometry import (
    SLIDE_MIN_PX,
    TAP_MIN_PX,
    HitCache,
    hit_signature,
    note_in_time,
    sample_beats,
)
from .scene import Scene
from .theme import Theme
from .view import ViewState

GROUP_COLORS = (
    "#59b0ff",
    "#ffd166",
    "#7ee787",
    "#ff9de2",
    "#c792ea",
    "#ff8a65",
    "#4dd0e1",
    "#f4a259",
)

REGION_SAMPLES = 40


@dataclass
class PendingPreview:
    """放置流程中的预览数据（由交互层填充）。"""

    tool: str = ""
    step: int = 0          # 交互阶段（0 = 还没落下第一个点）
    lane_start: Optional[int] = None
    lane_end: Optional[int] = None
    beat_start: Optional[float] = None
    beat_end: Optional[float] = None
    x_start: Optional[float] = None
    x_end: Optional[float] = None
    left: Optional[BoundaryCurve] = None
    right: Optional[BoundaryCurve] = None
    hint: str = ""
    clipboard: List[dict] = field(default_factory=list)
    clipboard_beat: Optional[float] = None


@dataclass
class RenderState:
    selection: Set[str] = field(default_factory=set)
    pending: PendingPreview = field(default_factory=PendingPreview)
    rubber_band: Optional[Tuple[float, float, float, float]] = None
    hover: Optional[Tuple[float, float]] = None
    demo: bool = False
    playing: bool = False
    message: str = ""
    # 上一帧画出来的物件几何（点击 / 框选直接复用，避免重算整张谱面）
    hit_cache: Optional[HitCache] = None


class Renderer:
    def __init__(self, scene: Scene, theme: Theme) -> None:
        self.scene = scene
        self.theme = theme
        self._hit_entries: List[Tuple[Note, Tuple[float, float, float, float], Optional[List]]] = []
        try:
            self._small_font = tkfont.Font(family=theme.font_family, size=theme.font_small)
        except Exception:  # pragma: no cover - 无 Tk 环境
            self._small_font = None

    def _text_width(self, text: str, fallback_per_char: float = 7.5) -> float:
        if self._small_font is None:
            return len(text) * fallback_per_char
        return float(self._small_font.measure(text))

    # ------------------------------------------------------------------ 主入口
    def draw(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        scene = self.scene
        layout = view.layout
        scene.begin()
        scene.set_layer(0)
        scene.rect("bg", 0, 0, layout.width, layout.height, fill=self.theme.play_bg, outline="")
        beat_low, beat_high = view.beat_range()
        if view.split_layout:
            self._draw_split_view(chart, view, state, beat_low, beat_high)
        else:
            self._draw_overlay_view(chart, view, state, beat_low, beat_high)
        scene.set_layer(13)
        self._draw_hud(chart, view, state)
        scene.end()

    # ------------------------------------------------------------------ 两种布局
    def _draw_split_view(
        self, chart: Chart, view: ViewState, state: RenderState, beat_low: float, beat_high: float
    ) -> None:
        """编辑器布局：左 6K、右鼠标平面，两个区域各自绘制但共享时间轴。"""
        layout = view.layout
        theme = self.theme
        self.scene.set_layer(0)
        # 两个区域的底色（纯黑背景 + 细分隔线）
        self.scene.rect(
            "pane6:bg",
            layout.pane_6k[0],
            layout.play_top,
            layout.pane_6k[1],
            layout.play_bottom,
            fill=theme.lane_alt,
            outline="",
        )
        self.scene.rect(
            "panem:bg",
            layout.pane_mouse[0],
            layout.play_top,
            layout.pane_mouse[1],
            layout.play_bottom,
            fill=theme.pane_bg,
            outline=theme.lane_border,
            width=1,
        )
        self.scene.line(
            "pane:divider",
            layout.divider_x,
            layout.play_top,
            layout.divider_x,
            layout.play_bottom,
            fill=theme.lane_border_strong,
            width=1,
            dash=(4, 6),
        )

        self.scene.set_layer(1)
        self._draw_lanes(view, theme, "lane")
        self.scene.set_layer(2)
        self._draw_time_grid(chart, view, beat_low, beat_high, *layout.pane_6k, "grid6", True, False)
        self._draw_time_grid(chart, view, beat_low, beat_high, *layout.pane_mouse, "gridm", False, True)
        self.scene.set_layer(3)
        self._draw_legal_area(view, "mouse", label=None)
        self._draw_horizontal_grid(chart, view)
        self.scene.set_layer(4)
        self._draw_notes(chart, view, state)
        self.scene.set_layer(10)
        self._draw_judgment_line(view, state, *layout.pane_6k, "judge", accent=False, label="判定线")
        self._draw_judgment_line(
            view, state, *layout.pane_mouse, "judge-mouse", accent=True, label="鼠标判定线"
        )
        self._draw_pane_titles(view)
        if not state.demo:
            self.scene.set_layer(11)
            self._draw_pending(chart, view, state)
            self.scene.set_layer(12)
            self._draw_rubber_band(state)

    def _draw_overlay_view(
        self, chart: Chart, view: ViewState, state: RenderState, beat_low: float, beat_high: float
    ) -> None:
        """演示布局：鼠标平面叠加在 6K 的中间四条轨道上。"""
        layout = view.layout
        theme = self.theme
        self.scene.set_layer(1)
        self._draw_lanes(view, theme, "lane")
        self.scene.set_layer(2)
        self._draw_time_grid(
            chart, view, beat_low, beat_high, layout.play_left, layout.play_right, "grid", True, True
        )
        self.scene.set_layer(3)
        self._draw_legal_area(view, "mouse", label="鼠标平面合法区域 (0 .. 1)", label_anchor="ne")
        self._draw_horizontal_grid(chart, view)
        self.scene.set_layer(4)
        self._draw_notes(chart, view, state)
        self.scene.set_layer(10)
        self._draw_judgment_line(
            view, state, layout.play_left, layout.play_right, "judge", accent=False, label="判定线"
        )
        self._draw_judgment_line(
            view, state, layout.mouse_left, layout.mouse_right, "judge-mouse", accent=True
        )
        if not state.demo:
            self.scene.set_layer(11)
            self._draw_pending(chart, view, state)
            self.scene.set_layer(12)
            self._draw_rubber_band(state)

    def _draw_pane_titles(self, view: ViewState) -> None:
        layout = view.layout
        theme = self.theme
        # 6K 区域的标题放在 HUD 下方，避免与 BPM 信息重叠
        self.scene.text(
            "pane6:title",
            layout.pane_6k[0] + 6,
            layout.play_top + 24,
            "6K 下落式（轨道 1 - 6）",
            fill=theme.text_dim,
            font=theme.font(theme.font_small, bold=True),
            anchor="nw",
        )
        self.scene.text(
            "panem:title",
            layout.pane_mouse[0] + 6,
            layout.play_top + 24,
            f"鼠标平面 · 合法区域 0 .. 1（显示 {layout.norm_min:.2f} .. {layout.norm_max:.2f}）",
            fill=theme.legal_border,
            font=theme.font(theme.font_small, bold=True),
            anchor="nw",
        )
        # 两侧溢出余量的边界标注：合法区域之外也画得出来
        self.scene.text(
            "panem:edge-left",
            layout.pane_mouse[0] + 4,
            layout.judgment_y - 32,
            f"{layout.norm_min:.2f}",
            fill=theme.text_dim,
            font=theme.font(theme.font_small),
            anchor="nw",
        )
        self.scene.text(
            "panem:edge-right",
            layout.pane_mouse[1] - 4,
            layout.judgment_y - 32,
            f"{layout.norm_max:.2f}",
            fill=theme.text_dim,
            font=theme.font(theme.font_small),
            anchor="ne",
        )

    # ------------------------------------------------------------------ 轨道
    def _draw_lanes(self, view: ViewState, theme: Theme, key_prefix: str = "lane") -> None:
        layout = view.layout
        for index, (x0, x1) in enumerate(layout.lane_rects):
            fill = theme.lane_alt if index % 2 == 0 else theme.lane_alt2
            self.scene.rect(
                f"{key_prefix}:{index}",
                x0,
                layout.play_top,
                x1,
                layout.play_bottom,
                fill=fill,
                outline=theme.lane_border,
                width=1,
            )
        self.scene.line(
            f"{key_prefix}:frame",
            layout.lane_rects[0][0],
            layout.play_top,
            layout.lane_rects[-1][1],
            layout.play_top,
            fill=theme.lane_border_strong,
            width=1,
        )

    def _draw_legal_area(
        self,
        view: ViewState,
        key_prefix: str = "mouse",
        label: Optional[str] = None,
        label_anchor: str = "nw",
    ) -> None:
        layout = view.layout
        theme = self.theme
        self.scene.rect(
            f"{key_prefix}:legal",
            layout.mouse_left,
            layout.play_top,
            layout.mouse_right,
            layout.play_bottom,
            fill="",
            outline=theme.legal_border,
            width=2,
            dash=(6, 4),
        )
        if label:
            x = layout.mouse_left + 6 if label_anchor.endswith("w") else layout.mouse_right - 6
            self.scene.text(
                f"{key_prefix}:legal-label",
                x,
                layout.play_top + 4,
                label,
                fill=theme.legal_border,
                font=theme.font(theme.font_small),
                anchor=label_anchor,
            )

    def _draw_horizontal_grid(self, chart: Chart, view: ViewState) -> None:
        # 只要设置了横向等分就必然显示（没有单独的开关）
        if not chart.settings.horizontal_grid_enabled:
            return
        theme = self.theme
        layout = view.layout
        divisions = chart.settings.horizontal_divisions
        for i in range(divisions + 1):
            x = view.norm_to_x(i / divisions)
            self.scene.line(
                f"hgrid:{i}",
                x,
                layout.play_top,
                x,
                layout.play_bottom,
                fill=theme.hgrid,
                width=1,
                dash=(2, 6),
            )
            self.scene.text(
                f"hgrid-label:{i}",
                x + 3,
                layout.judgment_y - 16,
                f"{i / divisions:.3g}",
                fill=theme.text_dim,
                font=theme.font(theme.font_small),
                anchor="nw",
            )

    # ------------------------------------------------------------------ 网格
    def _draw_time_grid(
        self,
        chart: Chart,
        view: ViewState,
        beat_low: float,
        beat_high: float,
        x0: float,
        x1: float,
        key_prefix: str = "grid",
        bar_labels: bool = True,
        time_labels: bool = True,
        beat_labels: bool = True,
    ) -> None:
        theme = self.theme
        layout = view.layout
        # 密度用“可视范围内最密的一格”（最快 BPM）判断，避免高速段笔画太密
        ppb = view.min_px_per_beat(beat_low, beat_high)
        division = max(1, view.grid_division)
        sub_step = 1.0 / division
        draw_sub = (ppb / division) >= 7.0
        draw_beat = ppb >= 9.0
        # 拍号标注：方便用户照着网格填写“第几拍”的数据
        show_beat_labels = beat_labels and ppb >= 24.0
        show_sub_labels = beat_labels and (ppb / division) >= 44.0
        # 密度自适应：太密时只画拍线，再密时只画小节线
        if draw_sub:
            base_step = sub_step
        elif draw_beat:
            base_step = 1.0
        else:
            base_step = float(max(1, chart.timing.beats_per_bar_at(view.current_beat)))

        bar_lines = set(round(b, 9) for b in chart.timing.bar_lines(beat_low, beat_high))
        first = int(math.floor(beat_low / base_step)) - 1
        last = int(math.ceil(beat_high / base_step)) + 1
        for i in range(first, last + 1):
            beat = i * base_step
            y = view.beat_to_y(beat)
            if y < layout.play_top - 1 or y > layout.play_bottom + 1:
                continue
            is_beat = abs(beat - round(beat)) < 1e-6
            is_bar = round(beat, 9) in bar_lines
            if is_bar:
                color, width = theme.grid_bar, 2
            elif is_beat:
                if not draw_beat:
                    continue
                color, width = theme.grid_beat, 1
            else:
                if not draw_sub:
                    continue
                color, width = theme.grid_sub, 1
            self.scene.line(f"{key_prefix}:{i}", x0, y, x1, y, fill=color, width=width)
            if is_bar:
                if bar_labels:
                    bar_index, _ = chart.timing.bar_index_at(beat + 1e-9)
                    self.scene.text(
                        f"{key_prefix}-label:{i}",
                        x0 + 4,
                        y + 2,
                        f"小节 {bar_index + 1} · 第 {beat:g} 拍",
                        fill=theme.grid_label,
                        font=theme.font(theme.font_small),
                        anchor="nw",
                    )
                if time_labels:
                    self.scene.text(
                        f"{key_prefix}-time:{i}",
                        x1 - 4,
                        y + 2,
                        view.time_label(beat),
                        fill=theme.grid_label,
                        font=theme.font(theme.font_small),
                        anchor="ne",
                    )
            elif show_beat_labels and is_beat:
                self.scene.text(
                    f"{key_prefix}-beat:{i}",
                    x0 + 4,
                    y + 2,
                    f"第 {beat:g} 拍",
                    fill=theme.text_dim,
                    font=theme.font(theme.font_small),
                    anchor="nw",
                )
            elif show_sub_labels and not is_beat:
                self.scene.text(
                    f"{key_prefix}-sub:{i}",
                    x0 + 4,
                    y + 2,
                    f"第 {beat:g} 拍",
                    fill=theme.text_dim,
                    font=theme.font(theme.font_small),
                    anchor="nw",
                )

    # ------------------------------------------------------------------ 物件
    def _group(self, chart: Chart, note: Note) -> SpeedGroup:
        return chart.group_by_id(note.group)

    def _group_color(self, group_id: int) -> str:
        return GROUP_COLORS[group_id % len(GROUP_COLORS)]

    def _draw_notes(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        """逐帧绘制物件。

        重要：速度组的流速可正可负，一个物件的屏幕位置完全由 ∫v 决定，
        因此**判定时刻很远的物件可能被速度叠加推回判定线附近，判定时刻很近的物件也可能
        被甩出画面**。所以这里绝不能用“拍差”做剔除，只能按实际算出的像素位置判断可见性。

        唯一的例外是**判定时间**：一旦判定线越过了物件的判定时刻（长按 / 停留区域按结束
        时刻算），该物件就不再显示 —— 这是显示规则，与它的位置无关。
        """
        margin = 80.0
        y_min = view.layout.play_top - margin
        y_max = view.layout.height + margin
        notes = chart.sorted_notes()
        region_count = sum(1 for n in notes if isinstance(n, MouseRegion))
        region_samples = REGION_SAMPLES if region_count <= 40 else 12
        cache = state.hit_cache
        if cache is None:
            cache = HitCache()
            state.hit_cache = cache
        self._hit_entries = cache.entries
        self._hit_entries.clear()
        cache.signature = hit_signature(view)
        # 图层顺序（自下而上）：多轨长按 → 单轨长按 → 多轨单键 → 单轨单键 → 空域 → 滑键
        # 这样即使谱面故意写成非法重叠，小按键也压在长按之上、空域与滑键再压在最上面，交叠处依然可读。
        def multi_hold(note: Note) -> bool:
            return isinstance(note, LaneNote) and note.is_hold and note.is_multi

        def single_hold(note: Note) -> bool:
            return isinstance(note, LaneNote) and note.is_hold and not note.is_multi

        def multi_tap(note: Note) -> bool:
            return isinstance(note, LaneNote) and not note.is_hold and note.is_multi

        def single_tap(note: Note) -> bool:
            return isinstance(note, LaneNote) and not note.is_hold and not note.is_multi

        layers = (
            (4, multi_hold, lambda n, s: self._draw_lane_note(n, chart, view, s, y_min, y_max)),
            (5, single_hold, lambda n, s: self._draw_lane_note(n, chart, view, s, y_min, y_max)),
            (6, multi_tap, lambda n, s: self._draw_lane_note(n, chart, view, s, y_min, y_max)),
            (7, single_tap, lambda n, s: self._draw_lane_note(n, chart, view, s, y_min, y_max)),
            (8, lambda n: isinstance(n, MouseRegion),
                lambda n, s: self._draw_region(n, chart, view, s, y_min, y_max, region_samples)),
            (9, lambda n: isinstance(n, MouseSlide),
                lambda n, s: self._draw_slide(n, chart, view, s, y_min, y_max)),
            (10, lambda n: isinstance(n, MouseClick),
                lambda n, s: self._draw_click(n, chart, view, s, y_min, y_max)),
        )
        for layer, matches, draw in layers:
            self.scene.set_layer(layer)
            visible = [
                note
                for note in notes
                if matches(note) and note_in_time(note, view.current_beat)
            ]
            if layer in (4, 6):
                # 多轨物件：跨的轨道越多越先画（越靠下层），同跨度保持时间顺序
                visible.sort(key=lambda n: n.lane_end - n.lane_start, reverse=True)
            for note in visible:
                draw(note, note.id in state.selection)

    def _draw_lane_note(
        self,
        note: LaneNote,
        chart: Chart,
        view: ViewState,
        selected: bool,
        y_min: float = -1e9,
        y_max: float = 1e9,
    ) -> None:
        theme = self.theme
        layout = view.layout
        group = self._group(chart, note)
        x0, x1 = layout.lane_span(note.lane_start, note.lane_end)
        pad = 6 if note.is_multi else 9
        x0 += pad
        x1 -= pad
        y_head = view.note_y(note.beat_start, group)
        y_tail = view.note_y(note.beat_end, group) if note.is_hold else y_head
        top = min(y_head, y_tail)
        bottom = max(y_head, y_tail)
        if note.is_hold:
            if abs(bottom - top) < TAP_MIN_PX:
                # 退化成一个点时向上补足：起始位置始终贴在它的时间网格线上
                top = bottom - TAP_MIN_PX
            style = "hold"
        else:
            top = y_head - TAP_MIN_PX
            bottom = y_head
            style = "tap"
        if bottom < y_min or top > y_max:
            return
        self._hit_entries.append((note, (x0, top, x1, bottom), None))
        if style == "hold":
            # 长按：两侧蓝色边线 + 中间白色竖线（粗细固定，不随跨轨道数变化）
            self.scene.line(
                f"note-side-l:{note.id}", x0, top, x0, bottom,
                fill=theme.note_blue, width=3,
            )
            self.scene.line(
                f"note-side-r:{note.id}", x1, top, x1, bottom,
                fill=theme.note_blue, width=3,
            )
            center = (x0 + x1) / 2.0
            self.scene.line(
                f"note:{note.id}", center, top, center, bottom,
                fill=theme.note_white, width=theme.note_line_width,
            )
            # 开头处与单键相同的白色横线
            self.scene.line(
                f"note-head:{note.id}", x0, y_head, x1, y_head,
                fill=theme.note_white, width=theme.note_line_width,
            )
        else:
            # 单键 / 多轨单键：外侧白色边 + 亮蓝色环 + 中间一条白色横线
            self.scene.rect(
                f"note:{note.id}", x0, top, x1, bottom,
                fill="", outline=theme.note_white, width=2,
            )
            self.scene.rect(
                f"note-ring:{note.id}", x0 + 2, top + 2, x1 - 2, bottom - 2,
                fill="", outline=theme.note_blue, width=3,
            )
            middle = (top + bottom) / 2.0
            self.scene.line(
                f"note-line:{note.id}", x0 + 2, middle, x1 - 2, middle,
                fill=theme.note_white, width=theme.note_line_width,
            )
        if selected:
            self.scene.rect(
                f"sel:{note.id}",
                x0 - 2,
                top - 2,
                x1 + 2,
                bottom + 2,
                fill="",
                outline=theme.select,
                width=2,
            )
        if note.group != config.DEFAULT_GROUP_ID:
            self.scene.line(
                f"grp:{note.id}", x0 - 3, top, x0 - 3, bottom,
                fill=self._group_color(note.group), width=3,
            )
        if selected:
            label = f"{note.id} · 轨道{note.lane_start + 1}-{note.lane_end + 1} · 组{note.group}"
            self.scene.text(
                f"lbl:{note.id}",
                x0,
                top - 14,
                label,
                fill=theme.select,
                font=theme.font(theme.font_small),
                anchor="nw",
            )

    def _draw_click(
        self,
        note: MouseClick,
        chart: Chart,
        view: ViewState,
        selected: bool,
        y_min: float = -1e9,
        y_max: float = 1e9,
    ) -> None:
        """鼠标点击按键：左右两条红色实线标出范围 + 中间一条红色横线 + 中央红色实心菱形。

        三条实线的粗细都与 6K 按键的中心实线相同（``theme.note_line_width``），
        整个物件**竖向居中**在判定时刻上（中间横线与菱形中心都落在时间网格线上）。
        """
        theme = self.theme
        group = self._group(chart, note)
        y = view.note_y(note.beat, group)
        if y < y_min or y > y_max:
            return
        x = view.norm_to_x(note.center)
        radius_px = max(1.0, float(note.radius) * view.layout.mouse_width)
        # 高度与 6K 单键相同，且以判定时刻为垂直中点
        top, bottom = y - TAP_MIN_PX / 2.0, y + TAP_MIN_PX / 2.0
        x0, x1 = x - radius_px, x + radius_px
        self._hit_entries.append(
            (note, (x0, top, x1, bottom), None)
        )
        # 左右两条红色实线（= 范围边界），粗细与 6K 按键中心实线一致
        self.scene.line(
            f"click-left:{note.id}",
            x0, top, x0, bottom,
            fill=theme.click_edge,
            width=theme.note_line_width,
        )
        self.scene.line(
            f"click-right:{note.id}",
            x1, top, x1, bottom,
            fill=theme.click_edge,
            width=theme.note_line_width,
        )
        # 中间红色横线：连起左右两条竖线，粗细与 6K 单键的横线一致
        self.scene.line(
            f"click-line:{note.id}",
            x0, y, x1, y,
            fill=theme.click_edge,
            width=theme.note_line_width,
        )
        # 中央红色实心菱形：尺寸固定（不随按键大小变化），中心落在判定时刻上
        d = float(config.CLICK_DIAMOND_RADIUS_PX)
        self.scene.polygon(
            f"click-diamond:{note.id}",
            [(x - d, y), (x, y - d), (x + d, y), (x, y + d)],
            fill=theme.click_diamond,
            outline="",
        )
        if selected:
            self.scene.rect(
                f"sel:{note.id}",
                x0 - 4,
                top - 4,
                x1 + 4,
                bottom + 4,
                fill="",
                outline=theme.select,
                width=2,
                dash=(4, 3),
            )
            self.scene.text(
                f"lbl:{note.id}",
                x0,
                top - 18,
                f"{note.id} · 点击按键 · 组{note.group}",
                fill=theme.select,
                font=theme.font(theme.font_small),
                anchor="nw",
            )

    def _region_points(
        self,
        note: MouseRegion,
        chart: Chart,
        view: ViewState,
        samples: int = REGION_SAMPLES,
    ) -> List[Tuple[float, float]]:
        group = self._group(chart, note)
        left_edge: List[Tuple[float, float]] = []
        right_edge: List[Tuple[float, float]] = []
        samples = max(2, int(samples))
        for beat, progress in sample_beats(note.beat_start, note.beat_end, group, samples):
            y = view.note_y(beat, group)
            left_edge.append((view.norm_to_x(note.left.value_at(progress)), y))
            right_edge.append((view.norm_to_x(note.right.value_at(progress)), y))
        return left_edge + list(reversed(right_edge))

    def _region_sample_count(
        self, note: MouseRegion, group: SpeedGroup, view: ViewState, base: int
    ) -> int:
        """按空域在屏幕上的高度自适应采样：屏幕内越矮的点越少。"""
        span_px = abs(
            view.note_y(note.head_beat, group) - view.note_y(note.tail_beat, group)
        )
        adaptive = int(span_px / 18.0) + 3
        return max(4, min(base, adaptive))

    def _region_may_be_visible(
        self,
        note: MouseRegion,
        group: SpeedGroup,
        view: ViewState,
        y_min: float,
        y_max: float,
    ) -> bool:
        """便宜地判断空域是否可能出现在屏幕里。

        * 匀速组（流速恒为 1）：位置是拍的线性函数，直接用可视**时间窗**判断，
          O(1) 即可排除绝大多数空域 —— 被排除的空域完全不需要计算边界形状。
        * 非匀速组：流速分段常数，所以在“分段边界 + 等分点”上采样是**精确**的
          （每段内位置线性变化，极值一定出现在端点），据此判断可见性；
          仍然只算 y，不算左右边界曲线。
        """
        span_low, span_high = view.beat_range()
        if group.is_identity:
            margin = (span_high - span_low) * 0.5 + 1.0
            return not (
                note.tail_beat < span_low - margin or note.head_beat > span_high + margin
            )
        loudness = max(1.0, (y_max - y_min))
        low, high = y_min - loudness * 0.2, y_max + loudness * 0.2
        for beat, _progress in sample_beats(note.beat_start, note.beat_end, group, 6):
            if low <= view.note_y(beat, group) <= high:
                return True
        return False

    def _draw_region(
        self,
        note: MouseRegion,
        chart: Chart,
        view: ViewState,
        selected: bool,
        y_min: float = -1e9,
        y_max: float = 1e9,
        samples: int = REGION_SAMPLES,
    ) -> None:
        theme = self.theme
        group = self._group(chart, note)
        # 先做可见性预判：屏幕外的空域不算边界形状
        if not self._region_may_be_visible(note, group, view, y_min, y_max):
            return
        samples = self._region_sample_count(note, group, view, samples)
        points = self._region_points(note, chart, view, samples)
        ys = [p[1] for p in points]
        if max(ys) < y_min or min(ys) > y_max:
            return
        self._hit_entries.append(
            (
                note,
                (
                    min(p[0] for p in points),
                    min(ys),
                    max(p[0] for p in points),
                    max(ys),
                ),
                points,
            )
        )
        self.scene.polygon(
            f"region:{note.id}",
            points,
            fill=theme.region_fill,
            outline="",
            stipple=theme.stipple_fill,
        )
        half = len(points) // 2
        left_edge = points[:half]
        right_edge = list(reversed(points[half:]))
        # 按需求：蓝色停留区域只绘制**左右两条边界线**，不画上下封口横线
        self.scene.polyline(f"region-left:{note.id}", left_edge, fill=theme.region_edge, width=3)
        self.scene.polyline(f"region-right:{note.id}", right_edge, fill=theme.region_edge, width=3)
        if selected:
            self.scene.rect(
                f"sel:{note.id}",
                min(p[0] for p in points) - 3,
                min(ys) - 3,
                max(p[0] for p in points) + 3,
                max(ys) + 3,
                fill="",
                outline=theme.select,
                width=2,
                dash=(4, 3),
            )
            self.scene.text(
                f"lbl:{note.id}",
                min(p[0] for p in points),
                min(ys) - 16,
                f"{note.id} · 空域 · 组{note.group}",
                fill=theme.select,
                font=theme.font(theme.font_small),
                anchor="nw",
            )

    def _draw_slide(
        self,
        note: MouseSlide,
        chart: Chart,
        view: ViewState,
        selected: bool,
        y_min: float = -1e9,
        y_max: float = 1e9,
    ) -> None:
        theme = self.theme
        group = self._group(chart, note)
        y = view.note_y(note.beat, group)
        if y < y_min or y > y_max:
            return
        leftward = note.direction < 0
        fill = theme.slide_left_fill if leftward else theme.slide_fill
        edge = theme.slide_left_edge if leftward else theme.slide_edge
        x_start = view.norm_to_x(note.x_start)
        x_end = view.norm_to_x(note.x_end)
        min_dx = max(18.0, config.MIN_SLIDE_SPAN * view.layout.mouse_width)
        # 方框按实际长度显示（只留 1.5px 的可见下限），不再为了好看把方框拉长
        x0, x1 = min(x_start, x_end), max(x_start, x_end)
        if x1 - x0 < 3.0:
            x1 = x0 + 3.0
        self._hit_entries.append(
            (
                note,
                (x0 - 1.5, y - SLIDE_MIN_PX / 2, x1 + 1.5, y + SLIDE_MIN_PX / 2),
                None,
            )
        )
        self.scene.rect(
            f"slide:{note.id}",
            x0 - 1.5,
            y - SLIDE_MIN_PX / 2,
            x1 + 1.5,
            y + SLIDE_MIN_PX / 2,
            fill=fill,
            outline=edge,
            width=2,
            stipple=theme.stipple_fill,
        )
        # 箭头从起点指向终点（向左的滑键指向左）；长度不足时按最小长度补足，保证方向可见
        direction = 1.0 if x_end >= x_start else -1.0
        arrow_from = x_start
        arrow_to = x_start + direction * max(abs(x_end - x_start), min_dx)
        self.scene.line(
            f"slide-arrow:{note.id}",
            arrow_from,
            y,
            arrow_to,
            y,
            fill=edge,
            width=3,
            arrow="last",
            arrowshape=(12, 14, 5),
        )
        if selected:
            self.scene.rect(
                f"sel:{note.id}",
                x0 - 6,
                y - SLIDE_MIN_PX / 2 - 4,
                x1 + 6,
                y + SLIDE_MIN_PX / 2 + 4,
                fill="",
                outline=theme.select,
                width=2,
            )
            direction = "右" if note.direction > 0 else "左"
            self.scene.text(
                f"lbl:{note.id}",
                x0,
                y - SLIDE_MIN_PX / 2 - 20,
                f"{note.id} · 滑键({direction}) · 组{note.group}",
                fill=theme.select,
                font=theme.font(theme.font_small),
                anchor="nw",
            )

    # ------------------------------------------------------------------ 判定线
    def _draw_judgment_line(
        self,
        view: ViewState,
        state: RenderState,
        x0: float,
        x1: float,
        key_prefix: str = "judge",
        accent: bool = False,
        label: Optional[str] = None,
    ) -> None:
        theme = self.theme
        y = view.layout.judgment_y
        self.scene.line(
            key_prefix,
            x0,
            y,
            x1,
            y,
            fill=theme.accent if accent else theme.judge_line,
            width=3,
        )
        if label:
            self.scene.text(
                f"{key_prefix}-label",
                x1 - 4,
                y - 16,
                label,
                fill=theme.judge_line,
                font=theme.font(theme.font_small, bold=True),
                anchor="ne",
            )

    # ------------------------------------------------------------------ 预览
    def _draw_pending(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        pending = state.pending
        theme = self.theme
        layout = view.layout
        if pending.tool == config.TOOL_LANE_TAP and pending.lane_start is not None and pending.beat_start is not None:
            x0, x1 = layout.lane_span(pending.lane_start, pending.lane_start)
            y = view.note_y(pending.beat_start)
            self.scene.rect(
                "pending:tap",
                x0 + 9,
                y - TAP_MIN_PX,
                x1 - 9,
                y,
                fill=theme.pending,
                outline=theme.pending,
                width=2,
                dash=(4, 3),
                stipple=theme.stipple_soft,
            )
        elif pending.tool == config.TOOL_LANE_MULTI and pending.lane_start is not None and pending.lane_end is not None:
            x0, x1 = layout.lane_span(pending.lane_start, pending.lane_end)
            y = view.note_y(pending.beat_start or 0.0)
            self.scene.rect(
                "pending:multi",
                x0 + 6,
                y - TAP_MIN_PX,
                x1 - 6,
                y,
                fill=theme.pending,
                outline=theme.pending,
                width=2,
                dash=(4, 3),
                stipple=theme.stipple_soft,
            )
        elif (
            pending.tool == config.TOOL_LANE_HOLD
            and pending.lane_start is not None
            and pending.beat_start is not None
        ):
            lane_end = pending.lane_end if pending.lane_end is not None else pending.lane_start
            x0, x1 = layout.lane_span(pending.lane_start, lane_end)
            pad = 9 if x1 - x0 < 2 else 6
            end = pending.beat_end if pending.beat_end is not None else pending.beat_start
            y_head = view.note_y(pending.beat_start)
            y_tail = view.note_y(end)
            top, bottom = min(y_head, y_tail), max(y_head, y_tail)
            if bottom - top < TAP_MIN_PX:
                # 起始位置的预览向上补足，落在它的时间网格线上方（原来画到了下方）
                top = bottom - TAP_MIN_PX
            self.scene.rect(
                "pending:hold",
                x0 + pad,
                top,
                x1 - pad,
                bottom,
                fill=theme.pending,
                outline=theme.pending,
                width=2,
                dash=(4, 3),
                stipple=theme.stipple_soft,
            )
        elif pending.tool == config.TOOL_MOUSE_REGION and pending.beat_start is not None:
            end = pending.beat_end if pending.beat_end is not None else pending.beat_start
            left = pending.left or BoundaryCurve(0.0, 0.0)
            right = pending.right or BoundaryCurve(1.0, 1.0)
            left_points: List[Tuple[float, float]] = []
            right_points: List[Tuple[float, float]] = []
            for i in range(21):
                p = i / 20
                beat = pending.beat_start + (end - pending.beat_start) * p
                y = view.note_y(beat)
                left_points.append((view.norm_to_x(left.value_at(p)), y))
                right_points.append((view.norm_to_x(right.value_at(p)), y))
            self.scene.polygon(
                "pending:region",
                left_points + list(reversed(right_points)),
                fill=theme.region_fill,
                outline="",
                stipple=theme.stipple_soft,
            )
            # 预览同样只画左右两条边界线
            self.scene.polyline(
                "pending:region-left", left_points, fill=theme.pending, width=2, dash=(4, 3)
            )
            self.scene.polyline(
                "pending:region-right", right_points, fill=theme.pending, width=2, dash=(4, 3)
            )
        elif pending.tool == config.TOOL_MOUSE_SLIDE and pending.x_start is not None and pending.beat_start is not None:
            x0 = view.norm_to_x(pending.x_start)
            x1 = view.norm_to_x(pending.x_end if pending.x_end is not None else pending.x_start)
            min_dx = max(18.0, config.MIN_SLIDE_SPAN * view.layout.mouse_width)
            if pending.step == 0:
                # 选起点：只画一根黄色虚线，标出即将放置的横向位置
                self.scene.line(
                    "pending:slide-guide",
                    x0,
                    layout.play_top,
                    x0,
                    layout.play_bottom,
                    fill=theme.pending,
                    width=2,
                    dash=(6, 4),
                )
            else:
                # 选终点：按方向给出绿色（向右）/ 黄色（向左）预览
                leftward = x1 < x0
                fill = theme.slide_left_fill if leftward else theme.slide_fill
                edge = theme.slide_left_edge if leftward else theme.slide_edge
                y = view.note_y(pending.beat_start)
                lo, hi = min(x0, x1), max(x0, x1)
                if hi - lo < 3.0:
                    hi = lo + 3.0
                self.scene.rect(
                    "pending:slide",
                    lo - 1.5,
                    y - SLIDE_MIN_PX / 2,
                    hi + 1.5,
                    y + SLIDE_MIN_PX / 2,
                    fill=fill,
                    outline=edge,
                    width=2,
                    dash=(4, 3),
                    stipple=theme.stipple_fill,
                )
                # 方框按实际长度显示；箭头保持最小长度以便看清方向
                arrow_from = x0
                arrow_to = x0 + (-1.0 if leftward else 1.0) * max(abs(x1 - x0), min_dx)
                self.scene.line(
                    "pending:slide-arrow",
                    arrow_from,
                    y,
                    arrow_to,
                    y,
                    fill=edge,
                    width=3,
                    arrow="last",
                    arrowshape=(12, 14, 5),
                )
        elif pending.tool == config.TOOL_MOUSE_CLICK and pending.x_start is not None and pending.beat_start is not None:
            # 点击按键预览：选中心时给一根竖直虚线；选大小时给半透明红圆盘 + 实心菱形
            y = view.note_y(pending.beat_start)
            x = view.norm_to_x(pending.x_start)
            d = float(config.CLICK_DIAMOND_RADIUS_PX)
            top, bottom = y - TAP_MIN_PX / 2.0, y + TAP_MIN_PX / 2.0
            if pending.step == 0:
                self.scene.line(
                    "pending:click-guide",
                    x,
                    layout.play_top,
                    x,
                    layout.play_bottom,
                    fill=theme.pending,
                    width=2,
                    dash=(6, 4),
                )
            else:
                end = pending.x_end if pending.x_end is not None else pending.x_start
                # 与真实放置保持一致：半径不小于 MIN_CLICK_RADIUS
                radius_px = max(
                    config.MIN_CLICK_RADIUS, abs(end - pending.x_start)
                ) * layout.mouse_width
                # 与成品一致：左右两条红色实线标出范围（虚线表示尚未放置）
                for key, edge in (
                    ("pending:click-left", x - radius_px),
                    ("pending:click-right", x + radius_px),
                ):
                    self.scene.line(
                        key,
                        edge,
                        top,
                        edge,
                        bottom,
                        fill=theme.click_edge,
                        width=theme.note_line_width,
                        dash=(4, 3),
                    )
                self.scene.line(
                    "pending:click-line",
                    x - radius_px,
                    y,
                    x + radius_px,
                    y,
                    fill=theme.click_edge,
                    width=theme.note_line_width,
                    dash=(4, 3),
                )
            self.scene.polygon(
                "pending:click-diamond",
                [(x - d, y), (x, y - d), (x + d, y), (x, y + d)],
                fill=theme.click_diamond,
                outline="",
            )
        if pending.hint:
            self.scene.text(
                "pending:hint",
                layout.play_left + 8,
                layout.play_top + 46,
                pending.hint,
                fill=theme.pending,
                font=theme.font(theme.font_normal, bold=True),
                anchor="nw",
            )
        if pending.clipboard and pending.clipboard_beat is not None:
            self._draw_clipboard_ghost(chart, view, pending)

    def _draw_clipboard_ghost(self, chart: Chart, view: ViewState, pending: PendingPreview) -> None:
        from ..model.notes import note_from_dict

        heads = [float(d.get("beat", d.get("beat_start", 0.0))) for d in pending.clipboard]
        base = pending.clipboard_beat - (min(heads) if heads else 0.0)
        theme = self.theme
        for index, data in enumerate(pending.clipboard):
            note = note_from_dict(dict(data))
            note.shift(base)
            key = f"ghost:{index}"
            if isinstance(note, LaneNote):
                x0, x1 = view.layout.lane_span(note.lane_start, note.lane_end)
                y = view.note_y(note.beat_start)
                y2 = view.note_y(note.beat_end) if note.is_hold else y - TAP_MIN_PX
                self.scene.rect(
                    key, x0 + 6, min(y, y2), x1 - 6, max(y, y2),
                    fill="", outline=theme.pending, width=2, dash=(3, 3),
                )
            elif isinstance(note, MouseRegion):
                y = view.note_y(note.beat_start)
                y2 = view.note_y(note.beat_end)
                self.scene.rect(
                    key,
                    view.norm_to_x(note.left.start),
                    min(y, y2),
                    view.norm_to_x(note.right.start),
                    max(y, y2),
                    fill="", outline=theme.pending, width=2, dash=(3, 3),
                )
            else:
                y = view.note_y(note.beat)
                self.scene.rect(
                    key,
                    view.norm_to_x(min(note.x_start, note.x_end)) - 4,
                    y - SLIDE_MIN_PX / 2,
                    view.norm_to_x(max(note.x_start, note.x_end)) + 4,
                    y + SLIDE_MIN_PX / 2,
                    fill="", outline=theme.pending, width=2, dash=(3, 3),
                )

    def _draw_rubber_band(self, state: RenderState) -> None:
        if not state.rubber_band:
            return
        x0, y0, x1, y1 = state.rubber_band
        self.scene.rect(
            "rubber",
            x0,
            y0,
            x1,
            y1,
            fill=self.theme.rubber,
            outline=self.theme.rubber,
            width=1,
            stipple=self.theme.stipple_soft,
        )

    # ------------------------------------------------------------------ HUD
    def _draw_hud(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        theme = self.theme
        layout = view.layout
        timing = chart.timing
        bar_index, beat_in_bar = timing.bar_index_at(view.current_beat)
        position = (
            f"小节 {bar_index + 1} 第 {beat_in_bar + 1:.2f} 拍 · "
            f"第 {view.current_beat:.3f} 拍"
        )
        if not timing.has_bars_at(view.current_beat):
            # 段内小节线已关闭：这一节从最近的 BPM 变更处一直延伸下来
            position += "（段内小节线已关闭）"
        text = (
            f"{timing.bpm_at(view.current_beat):.2f} BPM · "
            f"{position} · {view.time_label(view.current_beat)}"
        )
        if state.playing:
            text = "▶ 播放中 · " + text
        hud_w = self._text_width(text) + 12
        self.scene.rect(
            "hud-time-bg",
            layout.play_left + 1,
            layout.play_top + 2,
            layout.play_left + 1 + hud_w,
            layout.play_top + 19,
            fill=theme.panel_alt,
            outline=theme.lane_border,
            width=1,
        )
        self.scene.text(
            "hud-time",
            layout.play_left + 6,
            layout.play_top + 4,
            text,
            fill=theme.text_dim,
            font=theme.font(theme.font_small),
            anchor="nw",
        )
        if state.demo:
            self.scene.text(
                "hud-demo",
                layout.width / 2,
                layout.play_top + 30,
                "演示模式（不可编辑 · 两个平面叠加显示）",
                fill=theme.pending,
                font=theme.font(theme.font_title, bold=True),
                anchor="n",
            )
        if state.message:
            self.scene.text(
                "hud-message",
                layout.width / 2,
                layout.play_top + 54,
                state.message,
                fill=theme.text_warn,
                font=theme.font(theme.font_normal, bold=True),
                anchor="n",
            )

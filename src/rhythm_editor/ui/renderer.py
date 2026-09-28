"""把谱面与视图状态画到画布上。

布局
----
* 编辑器（``LAYOUT_SPLIT``）：左右两个独立区域 —— 左边 6K 下落式、右边鼠标平面。
  两个区域共用同一条判定线的 y 坐标与同一套时间网格，因此滚动 / 缩放同步。
* 演示模式（``LAYOUT_OVERLAY``）：只有一个区域，鼠标平面叠加在 6K 的中间四条轨道上。

渲染顺序（自下而上）：
背景 -> 区域底色 / 轨道 -> 时间网格 -> 合法区域边界 -> 横向辅助网格 -> 物件
-> 判定线 -> 区域标题 -> 预览幽灵 / 橡皮筋 -> HUD

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
    MouseNote,
    MouseRegion,
    MouseSlide,
    Note,
)
from ..model.speed import SpeedGroup
from .geometry import SLIDE_MIN_PX, TAP_MIN_PX, note_in_time, sample_beats
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


class Renderer:
    def __init__(self, scene: Scene, theme: Theme) -> None:
        self.scene = scene
        self.theme = theme
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
        scene.set_layer(11)
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
        self.scene.set_layer(7)
        self._draw_judgment_line(view, state, *layout.pane_6k, "judge", accent=False, label="判定线")
        self._draw_judgment_line(
            view, state, *layout.pane_mouse, "judge-mouse", accent=True, label="鼠标判定线"
        )
        self._draw_pane_titles(view)
        if not state.demo:
            self.scene.set_layer(8)
            self._draw_pending(chart, view, state)
            self.scene.set_layer(9)
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
        self.scene.set_layer(7)
        self._draw_judgment_line(
            view, state, layout.play_left, layout.play_right, "judge", accent=False, label="判定线"
        )
        self._draw_judgment_line(
            view, state, layout.mouse_left, layout.mouse_right, "judge-mouse", accent=True
        )
        if not state.demo:
            self.scene.set_layer(8)
            self._draw_pending(chart, view, state)
            self.scene.set_layer(9)
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
        if not chart.settings.horizontal_grid_enabled or not view.show_horizontal_grid:
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
        ppb = view.px_per_beat()
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
        # 绘制顺序：停留区域（半透明，最底层）→ 6K 物件 → 滑键（最上层）
        layers = (
            (MouseRegion, 4, lambda n, s: self._draw_region(n, chart, view, s, y_min, y_max, region_samples)),
            (LaneNote, 5, lambda n, s: self._draw_lane_note(n, chart, view, s, y_min, y_max)),
            (MouseSlide, 6, lambda n, s: self._draw_slide(n, chart, view, s, y_min, y_max)),
        )
        for note_type, layer, draw in layers:
            self.scene.set_layer(layer)
            for note in notes:
                if not isinstance(note, note_type):
                    continue
                if not note_in_time(note, view.current_beat):
                    continue  # 判定时间已过 -> 不再显示
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
            fill = theme.hold_pink
            outline = theme.hold_border
        else:
            top = y_head - TAP_MIN_PX
            bottom = y_head
            fill = theme.note_white
            outline = theme.note_border
        if bottom < y_min or top > y_max:
            return
        key = f"note:{note.id}"
        self.scene.rect(key, x0, top, x1, bottom, fill=fill, outline=outline, width=1)
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
            self.scene.rect(
                f"grp:{note.id}",
                x0,
                top,
                x0 + 4,
                bottom,
                fill=self._group_color(note.group),
                outline="",
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
        points = self._region_points(note, chart, view, samples)
        ys = [p[1] for p in points]
        if max(ys) < y_min or min(ys) > y_max:
            return
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
        x_start = view.norm_to_x(note.x_start)
        x_end = view.norm_to_x(note.x_end)
        min_dx = max(18.0, config.MIN_SLIDE_SPAN * view.layout.mouse_width)
        x0, x1 = min(x_start, x_end), max(x_start, x_end)
        if x1 - x0 < min_dx:
            center = (x0 + x1) / 2
            x0, x1 = center - min_dx / 2, center + min_dx / 2
        self.scene.rect(
            f"slide:{note.id}",
            x0 - 4,
            y - SLIDE_MIN_PX / 2,
            x1 + 4,
            y + SLIDE_MIN_PX / 2,
            fill=theme.slide_fill,
            outline=theme.slide_edge,
            width=2,
            stipple=theme.stipple_fill,
        )
        arrow_from = x_start if x_end >= x_start else x_end
        arrow_to = x_end if x_end >= x_start else x_start
        self.scene.line(
            f"slide-arrow:{note.id}",
            arrow_from,
            y,
            arrow_to,
            y,
            fill=theme.slide_edge,
            width=3,
            arrow="last",
            arrowshape=(12, 14, 5),
        )
        if selected:
            self.scene.rect(
                f"sel:{note.id}",
                x0 - 8,
                y - SLIDE_MIN_PX / 2 - 4,
                x1 + 8,
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
            y = view.note_y(pending.beat_start)
            x0 = view.norm_to_x(pending.x_start)
            x1 = view.norm_to_x(pending.x_end if pending.x_end is not None else pending.x_start)
            self.scene.rect(
                "pending:slide",
                min(x0, x1) - 4,
                y - SLIDE_MIN_PX / 2,
                max(x0, x1) + 4,
                y + SLIDE_MIN_PX / 2,
                fill=theme.pending,
                outline=theme.pending,
                width=2,
                dash=(4, 3),
                stipple=theme.stipple_soft,
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
        bar_index, beat_in_bar = chart.timing.bar_index_at(view.current_beat)
        text = (
            f"{chart.timing.bpm_at(view.current_beat):.2f} BPM · "
            f"小节 {bar_index + 1} 第 {beat_in_bar + 1:.2f} 拍 · "
            f"第 {view.current_beat:.3f} 拍 · {view.time_label(view.current_beat)}"
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

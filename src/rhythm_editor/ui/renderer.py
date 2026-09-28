"""把谱面与视图状态画到画布上。

渲染顺序（自下而上）：
背景 -> 轨道 -> 鼠标合法区域 -> 时间网格 -> 横向辅助网格 -> 物件 -> 判定线
-> 预览/幽灵物件 -> 橡皮筋 -> 底部总览条 -> 画布内提示文字
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
from .geometry import SLIDE_MIN_PX, TAP_MIN_PX, ruler_to_x, sample_beats
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
    show_ruler: bool = True


class Renderer:
    def __init__(self, scene: Scene, theme: Theme) -> None:
        self.scene = scene
        self.theme = theme
        # 用于测量文本宽度（给画布内的提示文字加底色）
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
        theme = self.theme
        layout = view.layout
        scene.begin()
        scene.rect("bg", 0, 0, layout.width, layout.height, fill=theme.play_bg, outline="")

        beat_low, beat_high = view.beat_range()
        self._draw_lanes(view, theme)
        self._draw_time_grid(chart, view, beat_low, beat_high)
        if state.show_ruler:
            self._draw_ruler(chart, view, state)
        self._draw_legal_area(view)
        self._draw_horizontal_grid(chart, view)
        self._draw_notes(chart, view, state)
        self._draw_judgment_line(view, state)
        if not state.demo:
            self._draw_pending(chart, view, state)
            self._draw_rubber_band(state)
        self._draw_hud(chart, view, state)
        scene.end()

    # ------------------------------------------------------------------ 轨道
    def _draw_lanes(self, view: ViewState, theme: Theme) -> None:
        layout = view.layout
        for index, (x0, x1) in enumerate(layout.lane_rects):
            fill = theme.lane_alt if index % 2 == 0 else theme.lane_alt2
            self.scene.rect(
                f"lane:{index}",
                x0,
                layout.play_top,
                x1,
                layout.play_bottom,
                fill=fill,
                outline=theme.lane_border,
                width=1,
            )
        self.scene.line(
            "lane_frame",
            layout.play_left,
            layout.play_top,
            layout.play_right,
            layout.play_top,
            fill=theme.lane_border_strong,
            width=1,
        )

    def _draw_legal_area(self, view: ViewState) -> None:
        layout = view.layout
        theme = self.theme
        key = "mouse:legal"
        self.scene.rect(
            key,
            layout.mouse_left,
            layout.play_top,
            layout.mouse_right,
            layout.judgment_y,
            fill="",
            outline=theme.legal_border,
            width=2,
            dash=(6, 4),
        )
        self.scene.text(
            "mouse:legal-label",
            layout.mouse_right - 6,
            layout.play_top + 4,
            "鼠标平面合法区域 (0 .. 1)",
            fill=theme.legal_border,
            font=theme.font(theme.font_small),
            anchor="ne",
        )

    def _draw_horizontal_grid(self, chart: Chart, view: ViewState) -> None:
        if not chart.settings.horizontal_grid_enabled or not view.show_horizontal_grid:
            return
        theme = self.theme
        layout = view.layout
        divisions = chart.settings.horizontal_divisions
        for i in range(divisions + 1):
            x = view.norm_to_x(i / divisions)
            label = f"{i / divisions:.3g}"
            self.scene.line(
                f"hgrid:{i}",
                x,
                layout.play_top,
                x,
                layout.judgment_y,
                fill=theme.hgrid,
                width=1,
                dash=(2, 6),
            )
            self.scene.text(
                f"hgrid-label:{i}",
                x + 3,
                layout.judgment_y - 16,
                label,
                fill=theme.text_dim,
                font=theme.font(theme.font_small),
                anchor="nw",
            )

    # ------------------------------------------------------------------ 网格
    def _draw_time_grid(self, chart: Chart, view: ViewState, beat_low: float, beat_high: float) -> None:
        theme = self.theme
        layout = view.layout
        ppb = view.px_per_beat()
        division = max(1, view.grid_division)
        sub_step = 1.0 / division
        draw_sub = (ppb / division) >= 7.0
        draw_beat = ppb >= 9.0
        # 密度自适应：太密时只画拍线，再密时只画小节线，避免糊成一片
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
            self.scene.line(f"grid:{i}", layout.play_left, y, layout.play_right, y, fill=color, width=width)
            if is_bar:
                bar_index, _ = chart.timing.bar_index_at(beat + 1e-9)
                self.scene.text(
                    f"grid-label:{i}",
                    layout.play_left + 4,
                    y + 2,
                    f"小节 {bar_index + 1}",
                    fill=theme.grid_label,
                    font=theme.font(theme.font_small),
                    anchor="nw",
                )
                seconds = chart.timing.beat_to_seconds(beat)
                self.scene.text(
                    f"grid-time:{i}",
                    layout.play_right - 4,
                    y + 2,
                    f"{view.time_label(beat)}",
                    fill=theme.grid_label,
                    font=theme.font(theme.font_small),
                    anchor="ne",
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
        """
        margin = 80.0
        y_min = view.layout.play_top - margin
        y_max = view.layout.height + margin
        notes = chart.sorted_notes()
        region_count = sum(1 for n in notes if isinstance(n, MouseRegion))
        # 物件很多时适当降低区域采样数，保证帧率
        region_samples = REGION_SAMPLES if region_count <= 40 else 12
        # 绘制顺序：停留区域（半透明，位于最底层）→ 6K 物件 → 滑键（最上层）
        # 这样鼠标平面的半透明填充不会盖住 6K 的白色/粉色矩形，编辑时看得更清楚。
        layers = (
            (MouseRegion, lambda n, s: self._draw_region(n, chart, view, s, y_min, y_max, region_samples)),
            (LaneNote, lambda n, s: self._draw_lane_note(n, chart, view, s, y_min, y_max)),
            (MouseSlide, lambda n, s: self._draw_slide(n, chart, view, s, y_min, y_max)),
        )
        for note_type, draw in layers:
            for note in notes:
                if isinstance(note, note_type):
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
                bottom = top + TAP_MIN_PX
            fill = theme.hold_pink
            outline = theme.hold_border
        else:
            top = min(y_head - TAP_MIN_PX, y_head)
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
        self.scene.polyline(
            f"region-left:{note.id}", left_edge, fill=theme.region_edge, width=3, smooth=False
        )
        self.scene.polyline(
            f"region-right:{note.id}", right_edge, fill=theme.region_edge, width=3, smooth=False
        )
        self.scene.line(
            f"region-cap0:{note.id}",
            left_edge[0][0],
            left_edge[0][1],
            right_edge[0][0],
            right_edge[0][1],
            fill=theme.region_edge,
            width=2,
        )
        self.scene.line(
            f"region-cap1:{note.id}",
            left_edge[-1][0],
            left_edge[-1][1],
            right_edge[-1][0],
            right_edge[-1][1],
            fill=theme.region_edge,
            width=2,
        )
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
                f"{note.id} · 停留区域 · 组{note.group}",
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
    def _draw_judgment_line(self, view: ViewState, state: RenderState) -> None:
        theme = self.theme
        layout = view.layout
        y = layout.judgment_y
        self.scene.line(
            "judge",
            layout.play_left,
            y,
            layout.play_right,
            y,
            fill=theme.judge_line,
            width=3,
        )
        # 鼠标平面的判定线（与 6K 共用同一条线，这里用强调色标出叠加区间）
        self.scene.line(
            "judge-mouse",
            layout.mouse_left,
            y,
            layout.mouse_right,
            y,
            fill=theme.accent,
            width=3,
        )
        self.scene.text(
            "judge-label",
            layout.play_right - 4,
            y - 16,
            "判定线",
            fill=theme.judge_line,
            font=theme.font(theme.font_small, bold=True),
            anchor="ne",
        )
        if state.playing:
            self.scene.text(
                "playing-label",
                layout.play_left + 4,
                layout.play_top + 4,
                "▶ 播放中",
                fill=theme.accent,
                font=theme.font(theme.font_normal, bold=True),
                anchor="nw",
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
        elif pending.tool == config.TOOL_LANE_HOLD and pending.lane_start is not None and pending.beat_start is not None:
            x0, x1 = layout.lane_span(pending.lane_start, pending.lane_start)
            end = pending.beat_end if pending.beat_end is not None else pending.beat_start
            y_head = view.note_y(pending.beat_start)
            y_tail = view.note_y(end)
            top, bottom = min(y_head, y_tail), max(y_head, y_tail)
            if bottom - top < TAP_MIN_PX:
                bottom = top + TAP_MIN_PX
            self.scene.rect(
                "pending:hold",
                x0 + 9,
                top,
                x1 - 9,
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
            points: List[Tuple[float, float]] = []
            for i in range(21):
                p = i / 20
                beat = pending.beat_start + (end - pending.beat_start) * p
                y = view.note_y(beat)
                points.append((view.norm_to_x(left.value_at(p)), y))
            for i in range(20, -1, -1):
                p = i / 20
                beat = pending.beat_start + (end - pending.beat_start) * p
                y = view.note_y(beat)
                points.append((view.norm_to_x(right.value_at(p)), y))
            self.scene.polygon(
                "pending:region",
                points,
                fill=theme.region_fill,
                outline=theme.pending,
                width=2,
                stipple=theme.stipple_soft,
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
                layout.play_top + 26,
                pending.hint,
                fill=theme.pending,
                font=theme.font(theme.font_normal, bold=True),
                anchor="nw",
            )
        if pending.clipboard and pending.clipboard_beat is not None:
            self._draw_clipboard_ghost(chart, view, pending)

    def _draw_clipboard_ghost(self, chart: Chart, view: ViewState, pending: PendingPreview) -> None:
        from ..model.notes import note_from_dict

        heads = []
        for data in pending.clipboard:
            heads.append(float(data.get("beat", data.get("beat_start", 0.0))))
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

    # ------------------------------------------------------------------ 总览条
    def _draw_ruler(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        theme = self.theme
        layout = view.layout
        y0 = layout.play_bottom + 6
        y1 = layout.height - 4
        if y1 - y0 < 6:
            return
        low, high = chart.beat_bounds()
        pad = 8.0
        usable = max(10.0, layout.width - 2 * pad)

        def to_x(beat: float) -> float:
            return ruler_to_x(chart, layout.width, beat, pad)

        self.scene.rect("ruler", pad, y0, pad + usable, y1, fill=theme.panel_alt, outline=theme.lane_border)
        for note in chart.all_notes():
            x = to_x(note.head_beat)
            color = theme.note_white if isinstance(note, LaneNote) else (
                theme.region_edge if isinstance(note, MouseRegion) else theme.slide_edge
            )
            self.scene.line(f"ruler-note:{note.id}", x, y0 + 1, x, y1 - 1, fill=color, width=1)
        low_view, high_view = view.beat_range()
        vx0 = to_x(max(low, low_view))
        vx1 = to_x(min(high, high_view))
        self.scene.rect(
            "ruler-view",
            vx0,
            y0,
            max(vx0 + 2, vx1),
            y1,
            fill="",
            outline=theme.accent,
            width=2,
        )
        cx = to_x(min(max(view.current_beat, low), high))
        self.scene.line("ruler-play", cx, y0, cx, y1, fill=theme.judge_line, width=2)

    # ------------------------------------------------------------------ HUD
    def _draw_hud(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        theme = self.theme
        layout = view.layout
        bar_index, _ = chart.timing.bar_index_at(view.current_beat)
        _, beat_in_bar = chart.timing.bar_index_at(view.current_beat)
        text = (
            f"{chart.timing.bpm_at(view.current_beat):.2f} BPM · "
            f"小节 {bar_index + 1} 第 {beat_in_bar + 1:.2f} 拍 · "
            f"第 {view.current_beat:.3f} 拍 · {view.time_label(view.current_beat)}"
        )
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
                layout.play_top + 6,
                "演示模式（不可编辑）",
                fill=theme.pending,
                font=theme.font(theme.font_title, bold=True),
                anchor="n",
            )
        if state.message:
            self.scene.text(
                "hud-message",
                layout.width / 2,
                layout.play_top + 30,
                state.message,
                fill=theme.text_warn,
                font=theme.font(theme.font_normal, bold=True),
                anchor="n",
            )

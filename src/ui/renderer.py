"""把谱面与视图状态画到画布上。

布局：
* 编辑器（LAYOUT_SPLIT）：左右两个独立区域 —— 左边 6K 下落式、右边鼠标平面。
  两个区域共用同一条判定线的 y 坐标与同一套时间网格，因此滚动 / 缩放同步。
* 演示模式（LAYOUT_OVERLAY）：只有一个区域，鼠标平面叠加在 6K 的中间四条轨道上。

渲染顺序（自下而上）：
背景 -> 区域底色 / 轨道 -> 时间网格 -> 合法区域边界 -> 横向辅助网格
-> 物件（多轨长按 -> 单轨长按 -> 多轨单键 -> 单轨单键 -> 空域 / 禁区 -> 滑键）
-> 判定线 -> 区域标题 -> HUD

上面这一整层是"放置层"，只在内容变化时重画（Renderer.draw）；
放置预览 / 框选橡皮筋 / 剪贴板幽灵另成"预览层"，每帧重画（Renderer.draw_preview），
因此拖放过程中预览始终跟手，而已经放置的物件不会被反复重算。

物件的七层顺序刻意“从大到小”摆放：即使谱面故意写成非法重叠，
小按键也压在长按之上、半透明空域与滑键再压在 6K 物件之上，交叠处依然可读。

时间轴导航由右侧朴素的竖向滚动条负责（不在画布上画总览条）。
"""

from __future__ import annotations

import math
import tkinter.font as tkfont
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

import config
from model.chart import Chart
from model.notes import (
    BoundaryCurve,
    ForbiddenRegion,
    LAYER_MAX,
    LAYER_MULTI_HOLD,
    LAYER_MULTI_TAP,
    LAYER_REGION,
    LAYER_SINGLE_HOLD,
    LAYER_SINGLE_TAP,
    LAYER_SLIDE,
    LaneNote,
    MouseRegion,
    MouseSlide,
    NOTE_REGION,
    Note,
    layer_rank,
)
from model.speed import SpeedGroup
from .geometry import (
    SLIDE_MIN_PX,
    TAP_MIN_PX,
    HitCache,
    clip_bounds,
    clip_polygon,
    clip_polyline,
    clip_segment,
    hit_signature,
    lane_note_geometry,
    mouse_pane_rect,
    note_in_time,
    polygon_overlap,
    polygon_overlap_strips,
    region_edges,
    region_beat_samples,
    sample_beats,
    slide_geometry,
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

# 复杂交集无法用单多边形表示时，逐行兜底的条带高度。
OVERLAP_STRIP_STEP = 2.0

# 空域 / 禁区相关图层。补丁颜色按优先级分到独立图层，确保增量重绘时
# 高优先级颜色不会因为新建图元较晚而被低优先级补丁盖住。
REGION_FILL_LAYER = 8
REGION_MULTI_LAYER = 9
FORBIDDEN_MULTI_LAYER = 10
MIXED_OVERLAP_LAYER = 11
REGION_EDGE_LAYER = 12
SLIDE_LAYER = 13
JUDGE_LAYER = 14
PREVIEW_PENDING_LAYER = 15
PREVIEW_RUBBER_LAYER = 16
HUD_LAYER = 17

REGION_SAMPLES = 40
# 横向辅助网格：线距小于这个值就抽稀（等分没有上限，不能逐条画）
HGRID_MIN_STEP_PX = 8.0
HGRID_LABEL_MIN_PX = 44.0

# Scene 的分组名：放置层只在内容变化时重画，预览层每帧重画
LAYER_PLACED = "placed"
LAYER_PREVIEW = "preview"


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
    # 鼠标悬停 / 正在拖动分界线（画粗一点做反馈）
    divider_active: bool = False
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
        """放置层：底色 / 网格 / 已放置物件 / 判定线 / HUD。

        只在内容真正变化时调用（画面指纹比较），已放置的物件因此不会因为
        鼠标移动而反复重算。放置过程中的预览由 draw_preview 每帧重画。
        """
        scene = self.scene
        layout = view.layout
        scene.begin(LAYER_PLACED)
        scene.set_layer(0)
        scene.rect("bg", 0, 0, layout.width, layout.height, fill=self.theme.play_bg, outline="")
        beat_low, beat_high = view.beat_range()
        if view.split_layout:
            self._draw_split_view(chart, view, state, beat_low, beat_high)
        else:
            self._draw_overlay_view(chart, view, state, beat_low, beat_high)
        scene.set_layer(HUD_LAYER)
        self._draw_hud(chart, view, state)
        scene.end(LAYER_PLACED)

    def draw_preview(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        """预览层：放置预览 / 框选 / 剪贴板幽灵，每帧重画。

        预览数据由交互层每帧重算（点击推进阶段后不必等鼠标移动），
        这里照常声明一遍 —— 内容没变的图元由 Scene 的差分比较跳过。
        """
        scene = self.scene
        scene.begin(LAYER_PREVIEW)
        if not state.demo:
            scene.set_layer(PREVIEW_PENDING_LAYER)
            self._draw_pending(chart, view, state)
            scene.set_layer(PREVIEW_RUBBER_LAYER)
            self._draw_rubber_band(state)
        scene.end(LAYER_PREVIEW)

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
            width=3 if state.divider_active else 1,
            dash=(4, 6),
        )

        self.scene.set_layer(1)
        self._draw_lanes(
            view, theme, "lane", view.settings.side_lane_highlight
        )
        self.scene.set_layer(2)
        self._draw_time_grid(chart, view, beat_low, beat_high, *layout.pane_6k, "grid6", True, False)
        self._draw_time_grid(chart, view, beat_low, beat_high, *layout.pane_mouse, "gridm", False, True)
        self.scene.set_layer(3)
        self._draw_legal_area(view, "mouse", label=None)
        self._draw_horizontal_grid(chart, view)
        self.scene.set_layer(4)
        self._draw_notes(chart, view, state)
        self.scene.set_layer(JUDGE_LAYER)
        self._draw_judgment_line(view, state, *layout.pane_6k, "judge", accent=False, label="判定线")
        self._draw_judgment_line(
            view, state, *layout.pane_mouse, "judge-mouse", accent=True, label="鼠标判定线"
        )
        self._draw_pane_titles(view)

    def _draw_overlay_view(
        self, chart: Chart, view: ViewState, state: RenderState, beat_low: float, beat_high: float
    ) -> None:
        """演示布局：鼠标平面叠加在 6K 的中间四条轨道上。"""
        layout = view.layout
        theme = self.theme
        self.scene.set_layer(1)
        # 演示区（1/3 宽、居中）底色 + 边框，其余保持纯黑
        self.scene.rect(
            "overlay:bg",
            layout.overlay_span[0],
            layout.play_top,
            layout.overlay_span[1],
            layout.play_bottom,
            fill=theme.lane_alt,
            outline=theme.lane_border,
            width=1,
        )
        self._draw_lanes(
            view, theme, "lane", view.settings.side_lane_highlight
        )
        self.scene.set_layer(2)
        self._draw_time_grid(
            chart,
            view,
            beat_low,
            beat_high,
            layout.overlay_span[0],
            layout.overlay_span[1],
            "grid",
            True,
            True,
        )
        self.scene.set_layer(3)
        self._draw_legal_area(view, "mouse", label="鼠标平面合法区域 (0 .. 1)", label_anchor="ne")
        self._draw_horizontal_grid(chart, view)
        self.scene.set_layer(4)
        self._draw_notes(chart, view, state)
        self.scene.set_layer(JUDGE_LAYER)
        self._draw_judgment_line(
            view,
            state,
            layout.overlay_span[0],
            layout.overlay_span[1],
            "judge",
            accent=False,
            label="判定线",
        )
        self._draw_judgment_line(
            view, state, layout.mouse_left, layout.mouse_right, "judge-mouse", accent=True
        )

    def _draw_pane_titles(self, view: ViewState) -> None:
        layout = view.layout
        theme = self.theme
        # 6K 区域的标题放在 HUD 下方，避免与 BPM 信息重叠
        self.scene.text(
            "pane6:title",
            layout.pane_6k[0] + 6,
            layout.play_top + 24,
            "6K 下落式",
            fill=theme.text_dim,
            font=theme.font(theme.font_small, bold=True),
            anchor="nw",
        )
        self.scene.text(
            "panem:title",
            layout.pane_mouse[0] + 6,
            layout.play_top + 24,
            "鼠标平面 · 合法区域 0 .. 1",
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
    def _draw_lanes(
        self,
        view: ViewState,
        theme: Theme,
        key_prefix: str = "lane",
        highlight_sides: bool = False,
    ) -> None:
        layout = view.layout
        for index, (x0, x1) in enumerate(layout.lane_rects):
            if highlight_sides and index in (0, len(layout.lane_rects) - 1):
                # 侧轨高亮：第 1 / 第 6 轨背景用暗紫色
                fill = theme.lane_side_highlight
            else:
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
        if not view.settings.horizontal_grid_enabled:
            return
        theme = self.theme
        layout = view.layout
        divisions = max(1, int(view.settings.horizontal_divisions))
        # 等分数量没有上限：太密时隔若干条画一条，只保留有限条线与标签
        spacing = layout.mouse_width / divisions
        stride = max(1, int(math.ceil(HGRID_MIN_STEP_PX / max(1e-9, spacing))))
        show_labels = spacing * stride >= HGRID_LABEL_MIN_PX
        indices = list(range(0, divisions + 1, stride))
        if indices[-1] != divisions:
            indices.append(divisions)          # 右边界那条线始终画
        for i in indices:
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
            if show_labels:
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
    def _group_color(self, group_id: int) -> str:
        return GROUP_COLORS[group_id % len(GROUP_COLORS)]

    def _draw_notes(self, chart: Chart, view: ViewState, state: RenderState) -> None:
        """逐帧绘制物件。

        重要：速度组的流速可正可负，一个物件的屏幕位置完全由 ∫v 决定，
        因此判定时刻很远的物件可能被速度叠加推回判定线附近，判定时刻很近的物件也可能
        被甩出画面。所以这里绝不能用“拍差”做剔除，只能按实际算出的像素位置判断可见性。

        唯一的例外是判定时间：一旦判定线越过了物件的判定时刻（长按 / 空域按结束
        时刻算），该物件就不再显示 —— 这是显示规则，与它的位置无关。

        这一条可以用视图菜单的「显示更早的物件」关掉：开启后不再做时间过滤，
        已判过的物件照样按真实位置画在判定线下方；仍然只画落在可见区域里的物件。
        """
        margin = 80.0
        y_min = view.layout.play_top - margin
        y_max = view.layout.height + margin
        notes = chart.sorted_notes()
        region_count = chart.note_counts()[NOTE_REGION]
        region_samples = REGION_SAMPLES if region_count <= 40 else 12
        cache = state.hit_cache
        if cache is None:
            cache = HitCache()
            state.hit_cache = cache
        self._hit_entries = cache.entries
        self._hit_entries.clear()
        cache.signature = hit_signature(view)
        show_past = bool(view.settings.show_past_notes) and not state.demo
        current_beat = view.current_beat
        # 一遍扫过候选物件，按图层分桶（避免逐图层各扫一遍全表）
        buckets: List[List[Note]] = [[] for _ in range(LAYER_MAX + 1)]
        for note in view.candidate_notes(notes):
            if not show_past and not note_in_time(note, current_beat, False):
                continue
            buckets[layer_rank(note)].append(note)
        # 图层顺序（自下而上）：多轨长按 → 单轨长按 → 多轨单键 → 单轨单键 → 空域 → 滑键
        # 这样即使谱面故意写成非法重叠，小按键也压在长按之上、空域与滑键再压在最上面，交叠处依然可读。
        layers = (
            (LAYER_MULTI_HOLD, lambda n, s: self._draw_lane_note(n, view, s, y_min, y_max)),
            (LAYER_SINGLE_HOLD, lambda n, s: self._draw_lane_note(n, view, s, y_min, y_max)),
            (LAYER_MULTI_TAP, lambda n, s: self._draw_lane_note(n, view, s, y_min, y_max)),
            (LAYER_SINGLE_TAP, lambda n, s: self._draw_lane_note(n, view, s, y_min, y_max)),
            (LAYER_REGION, None),          # 单独处理：填充 / 重叠合成 / 边线三遍
            (LAYER_SLIDE, lambda n, s: self._draw_slide(n, view, s, y_min, y_max)),
        )
        for rank, draw in layers:
            visible = buckets[rank]
            if rank == LAYER_REGION:
                self._draw_region_layer(
                    visible, chart, view, state, y_min, y_max, region_samples
                )
                continue
            self.scene.set_layer(SLIDE_LAYER if rank == LAYER_SLIDE else 4 + rank)
            if rank in (LAYER_MULTI_HOLD, LAYER_MULTI_TAP):
                # 多轨物件：跨的轨道越多越先画（越靠下层），同跨度保持时间顺序
                visible.sort(key=lambda n: n.lane_end - n.lane_start, reverse=True)
            for note in visible:
                draw(note, note.id in state.selection)


    def _draw_lane_note(
        self,
        note: LaneNote,
        view: ViewState,
        selected: bool,
        y_min: float = -1e9,
        y_max: float = 1e9,
    ) -> None:
        theme = self.theme
        shape = lane_note_geometry(note, view)
        x0, x1 = shape.x0, shape.x1
        y_head, top, bottom = shape.y_head, shape.top, shape.bottom
        style = "hold" if note.is_hold else "tap"
        if bottom < y_min or top > y_max:
            return
        self._hit_entries.append((note, shape.bounds, None))
        if style == "hold":
            # 长按：实心蓝色矩形打底，上面画白色竖线（粗细固定）+ 开头白横线
            self.scene.rect(
                f"note-body:{note.id}", x0, top, x1, bottom,
                fill=theme.note_blue, outline=theme.note_white, width=1,
            )
            center = (x0 + x1) / 2.0
            self.scene.line(
                f"note:{note.id}", center, top, center, bottom,
                fill=theme.note_white, width=theme.note_line_width,
            )
            # 开头：与单键同款（细矩形 + 压在起始拍线上的白色横线）
            self.scene.rect(
                f"note-head-body:{note.id}",
                x0, y_head - TAP_MIN_PX / 2.0, x1, y_head + TAP_MIN_PX / 2.0,
                fill=theme.note_blue, outline=theme.note_white, width=1,
            )
            self.scene.line(
                f"note-head:{note.id}", x0, y_head, x1, y_head,
                fill=theme.note_white, width=theme.note_line_width,
            )
        else:
            # 单键 / 多轨单键：实心蓝色矩形打底 + 中间一条白色横线（压在节拍线上）
            self.scene.rect(
                f"note:{note.id}", x0, top, x1, bottom,
                fill=theme.note_blue, outline=theme.note_white, width=1,
            )
            self.scene.line(
                f"note-line:{note.id}", x0 + 1, y_head, x1 - 1, y_head,
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

    def _region_points(
        self,
        note: MouseRegion,
        chart: Chart,
        view: ViewState,
        samples: int = REGION_SAMPLES,
    ) -> List[Tuple[float, float]]:
        left_edge, right_edge = region_edges(note, chart, view, max(2, int(samples)))
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
        chart: Chart,
        group: SpeedGroup,
        view: ViewState,
        y_min: float,
        y_max: float,
    ) -> bool:
        """便宜地判断空域是否可能出现在屏幕里。

        * 匀速组（流速恒为 1）：位置是拍的线性函数，直接用可视时间窗判断，
          O(1) 即可排除绝大多数空域 —— 被排除的空域完全不需要计算边界形状。
        * 非匀速组：流速分段常数，所以在“分段边界 + 等分点”上采样是精确的
          （每段内位置线性变化，极值一定出现在端点），据此判断可见性；
          仍然只算 y，不算左右边界曲线。
        """
        span_low, span_high = view.beat_range()
        if group.is_identity:
            margin = (span_high - span_low) * 0.5 + 1.0
            return not (
                note.tail_beat < span_low - margin or note.head_beat > span_high + margin
            )
        if group.min_multiplier > 0.0:
            # 流速全为正：位置对拍单调。把“可视秒数”按最小滚动速度折算成拍范围，
            # 整段都在这个范围之外的区域直接排除 —— 否则每个远处的区域都要从判定线
            # 积分过去，谱面里长空域多时开销很大。
            timing = chart.timing
            now = float(view.current_beat)
            near_end = note.head_beat if note.head_beat > now else note.tail_beat
            low_beat, high_beat = (now, near_end) if now <= near_end else (near_end, now)
            slowest = group.min_multiplier * timing.min_seconds_per_beat(
                low_beat, high_beat, now
            )
            if slowest > 0.0:
                px_per_second = max(1e-6, view.px_per_second())
                seconds_above = float(view.zoom_seconds)
                seconds_below = (
                    view.layout.play_bottom - view.layout.judgment_y
                ) / px_per_second
                slack = (max(seconds_above, seconds_below) + 1.0) / slowest
                if note.tail_beat < now - slack or note.head_beat > now + slack:
                    return False
        loudness = max(1.0, (y_max - y_min))
        low, high = y_min - loudness * 0.2, y_max + loudness * 0.2
        for _beat, _progress, y in region_beat_samples(note, chart, view, 6):
            if low <= y <= high:
                return True
        return False

    # ------------------------------------------------------------ 空域 / 禁区
    @staticmethod
    def _region_is_forbidden(note: MouseRegion) -> bool:
        return isinstance(note, ForbiddenRegion)

    def _region_fill_color(self, note: MouseRegion) -> str:
        return self.theme.forbidden_fill if self._region_is_forbidden(note) else self.theme.region_fill

    def _region_edge_color(self, note: MouseRegion) -> str:
        return self.theme.forbidden_edge if self._region_is_forbidden(note) else self.theme.region_edge

    def _overlap_color(self, first: MouseRegion, second: MouseRegion) -> Tuple[int, str]:
        """返回一对可见区域的补丁优先级和图元图层对应的预设颜色。"""
        first_forbidden = self._region_is_forbidden(first)
        second_forbidden = self._region_is_forbidden(second)
        if first_forbidden != second_forbidden:
            return MIXED_OVERLAP_LAYER, self.theme.mixed_overlap_fill
        if first_forbidden:
            return FORBIDDEN_MULTI_LAYER, self.theme.forbidden_multi_fill
        return REGION_MULTI_LAYER, self.theme.region_multi_fill

    def _region_geometry(
        self,
        note: MouseRegion,
        chart: Chart,
        view: ViewState,
        y_min: float,
        y_max: float,
        samples: int,
    ):
        """空域 / 禁区的可见多边形、原始边界点与包围盒；完全在屏幕外时返回 None。"""
        group = view.display_group(note)
        if not self._region_may_be_visible(note, chart, group, view, y_min, y_max):
            return None
        samples = self._region_sample_count(note, group, view, samples)
        points = self._region_points(note, chart, view, samples)
        ys = [p[1] for p in points]
        if max(ys) < y_min or min(ys) > y_max:
            return None
        pane = mouse_pane_rect(view)
        visible = clip_polygon(points, pane)
        if len(visible) < 3:
            return None
        xs = [p[0] for p in visible]
        return visible, points, (min(xs), min(ys), max(xs), max(ys))

    def _draw_region_layer(
        self,
        notes: List[Note],
        chart: Chart,
        view: ViewState,
        state: RenderState,
        y_min: float,
        y_max: float,
        samples: int,
    ) -> None:
        """空域 / 禁区：先画单层填充，再补两两重叠色，最后画边线。

        Tk 的点阵（stipple）相位固定：同相位的两层半透明填充会互相遮盖，
        按顺序画会变成“只看见上面那层”。所以简单遍历全部实际显示的可见区域，
        在重叠处按预设优先级选择空禁混合 / 多重禁区 / 多重空域颜色。
        """
        theme = self.theme
        pane = mouse_pane_rect(view)
        # 1) 半透明填充：登记命中区域，并留下多边形供重叠处使用
        self.scene.set_layer(REGION_FILL_LAYER)
        drawn = []
        for note in notes:
            item = self._region_geometry(note, chart, view, y_min, y_max, samples)
            if item is None:
                continue
            visible, points, bounds = item
            self._hit_entries.append((note, bounds, visible))
            self.scene.polygon(
                f"region:{note.id}",
                visible,
                fill=self._region_fill_color(note),
                outline="",
                stipple=theme.stipple_fill,
            )
            drawn.append((note, visible, points, bounds))
        # 2) 重叠处：简单遍历所有实际显示的可见对象。补丁按预设优先级
        # 从低到高分图层绘制，复杂交集用逐行条带兜底。
        patches: List[Tuple[int, str, str, str, object]] = []
        for index, (note_a, poly_a, _points_a, bounds_a) in enumerate(drawn):
            for note_b, poly_b, _points_b, bounds_b in drawn[index + 1:]:
                if (
                    bounds_a[0] > bounds_b[2] or bounds_b[0] > bounds_a[2]
                    or bounds_a[1] > bounds_b[3] or bounds_b[1] > bounds_a[3]
                ):
                    continue
                layer, color = self._overlap_color(note_a, note_b)
                patch = polygon_overlap(poly_a, poly_b, band=(y_min, y_max))
                if patch is not None:
                    patches.append((layer, color, note_a.id, note_b.id, patch))
                    continue
                for strip_index, rect in enumerate(
                    polygon_overlap_strips(
                        poly_a, poly_b, step=OVERLAP_STRIP_STEP, band=(y_min, y_max)
                    )
                ):
                    patches.append(
                        (layer, color, note_a.id, note_b.id, (strip_index, rect))
                    )
        patches.sort(key=lambda item: item[0])
        current_layer = None
        for layer, color, note_a, note_b, geometry in patches:
            if layer != current_layer:
                self.scene.set_layer(layer)
                current_layer = layer
            if (
                isinstance(geometry, tuple)
                and len(geometry) == 2
                and isinstance(geometry[0], int)
            ):
                strip_index, rect = geometry
                self.scene.rect(
                    f"region-overlap:{note_a}:{note_b}:strip:{strip_index}",
                    *rect,
                    fill=color,
                    outline="",
                    stipple=theme.stipple_fill,
                )
            else:
                self.scene.polygon(
                    f"region-overlap:{note_a}:{note_b}",
                    geometry,
                    fill=color,
                    outline="",
                    stipple=theme.stipple_fill,
                )
        # 3) 边界线 / 选中框 / 标签
        self.scene.set_layer(REGION_EDGE_LAYER)
        for note, _visible, points, bounds in drawn:
            half = len(points) // 2
            left_edge = points[:half]
            right_edge = list(reversed(points[half:]))
            # 空域 / 禁区都只绘制左右两条边界线（不画上下封口横线）；边界线同样
            # 裁剪到鼠标平面的显示范围：整体在外的不画，跨界在面板边裁断
            edge_color = self._region_edge_color(note)
            for prefix, edge in (("region-left", left_edge), ("region-right", right_edge)):
                for index, run in enumerate(clip_polyline(edge, pane)):
                    key = (
                        f"{prefix}:{note.id}"
                        if index == 0
                        else f"{prefix}:{note.id}#{index + 1}"
                    )
                    self.scene.polyline(key, run, fill=edge_color, width=3)
            if note.id not in state.selection:
                continue
            self.scene.rect(
                f"sel:{note.id}",
                bounds[0] - 3,
                bounds[1] - 3,
                bounds[2] + 3,
                bounds[3] + 3,
                fill="",
                outline=theme.select,
                width=2,
                dash=(4, 3),
            )
            label = "禁区" if self._region_is_forbidden(note) else "空域"
            self.scene.text(
                f"lbl:{note.id}",
                bounds[0],
                bounds[1] - 16,
                f"{note.id} · {label} · 组{note.group}",
                fill=theme.select,
                font=theme.font(theme.font_small),
                anchor="nw",
            )

    def _draw_slide(
        self,
        note: MouseSlide,
        view: ViewState,
        selected: bool,
        y_min: float = -1e9,
        y_max: float = 1e9,
    ) -> None:
        theme = self.theme
        shape = slide_geometry(note, view)
        if shape is None:
            return
        y = shape.y
        if y < y_min or y > y_max:
            return
        leftward = note.direction < 0
        fill = theme.slide_left_fill if leftward else theme.slide_fill
        edge = theme.slide_left_edge if leftward else theme.slide_edge
        x_start = shape.x_start
        x_end = shape.x_end
        min_dx = max(18.0, config.MIN_SLIDE_SPAN * view.layout.mouse_width)
        x0, x1 = shape.x0, shape.x1
        pane = mouse_pane_rect(view)
        bounds = shape.bounds
        self._hit_entries.append((note, bounds, None))
        self.scene.rect(
            f"slide:{note.id}",
            bounds[0],
            bounds[1],
            bounds[2],
            bounds[3],
            fill=fill,
            outline=edge,
            width=2,
            stipple=theme.stipple_fill,
        )
        # 箭头从起点指向终点（向左的滑键指向左）；长度不足时按最小长度补足，保证方向可见。
        # 箭头按真实位置裁剪到显示范围：Tk 会把箭头头画在（裁剪后）最后一个坐标上，
        # 所以只有真实的箭头端在显示范围内时才带头，否则只画可见的线段部分，
        # 避免在面板边上出现一个假的箭头头。
        arrow_from = x_start
        arrow_to = x_start + shape.direction * max(abs(x_end - x_start), min_dx)
        if not (pane[0] - 0.5 <= arrow_to <= pane[2] + 0.5):
            # 最小长度补足伸出显示范围：退回真实终点（真实终点在内时仍能看见箭头头）
            arrow_to = x_end
        segment = clip_segment((arrow_from, y), (arrow_to, y), pane)
        if segment is not None and abs(segment[1][0] - segment[0][0]) >= 1.0:
            head_inside = pane[0] - 0.5 <= arrow_to <= pane[2] + 0.5
            self.scene.line(
                f"slide-arrow:{note.id}",
                segment[0][0],
                segment[0][1],
                segment[1][0],
                segment[1][1],
                fill=edge,
                width=3,
                arrow="last" if head_inside else "none",
                arrowshape=(12, 14, 5),
            )
        if selected:
            selection = clip_bounds(
                (x0 - 6, y - SLIDE_MIN_PX / 2 - 4, x1 + 6, y + SLIDE_MIN_PX / 2 + 4), pane
            )
            if selection is not None:
                self.scene.rect(
                    f"sel:{note.id}",
                    *selection,
                    fill="",
                    outline=theme.select,
                    width=2,
                )
            direction = "右" if note.direction > 0 else "左"
            self.scene.text(
                f"lbl:{note.id}",
                bounds[0],
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
                # 起始位置的预览向上补足，落在它的时间网格线上方
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
        elif pending.tool in (config.TOOL_MOUSE_REGION, config.TOOL_MOUSE_FORBIDDEN) \
                and pending.beat_start is not None:
            forbidden = pending.tool == config.TOOL_MOUSE_FORBIDDEN
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
            pane = mouse_pane_rect(view)
            visible = clip_polygon(left_points + list(reversed(right_points)), pane)
            if len(visible) >= 3:
                self.scene.polygon(
                    "pending:region",
                    visible,
                    fill=theme.forbidden_fill if forbidden else theme.region_fill,
                    outline="",
                    stipple=theme.stipple_soft,
                )
            # 预览同样只画左右两条边界线，并裁剪到鼠标平面的显示范围
            for prefix, edge in (
                ("pending:region-left", left_points),
                ("pending:region-right", right_points),
            ):
                for index, run in enumerate(clip_polyline(edge, pane)):
                    key = prefix if index == 0 else f"{prefix}#{index + 1}"
                    self.scene.polyline(key, run, fill=theme.pending, width=2, dash=(4, 3))
        elif pending.tool == config.TOOL_MOUSE_SLIDE and pending.x_start is not None and pending.beat_start is not None:
            x0 = view.norm_to_x(pending.x_start)
            x1 = view.norm_to_x(pending.x_end if pending.x_end is not None else pending.x_start)
            min_dx = max(18.0, config.MIN_SLIDE_SPAN * view.layout.mouse_width)
            pane = mouse_pane_rect(view)
            if pending.step == 0:
                # 选起点：只画一根黄色虚线，标出即将放置的横向位置
                segment = clip_segment(
                    (x0, layout.play_top), (x0, layout.play_bottom), pane
                )
                if segment is not None:
                    self.scene.line(
                        "pending:slide-guide",
                        segment[0][0], segment[0][1], segment[1][0], segment[1][1],
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
                bounds = clip_bounds(
                    (lo - 1.5, y - SLIDE_MIN_PX / 2, hi + 1.5, y + SLIDE_MIN_PX / 2), pane
                )
                if bounds is not None:
                    self.scene.rect(
                        "pending:slide",
                        *bounds,
                        fill=fill,
                        outline=edge,
                        width=2,
                        dash=(4, 3),
                        stipple=theme.stipple_fill,
                    )
                # 方框按实际长度显示；箭头保持最小长度以便看清方向。
                # 与成品一致：箭头按真实位置裁剪，只有真实箭头端在显示范围内时才带头
                arrow_from = x0
                arrow_to = x0 + (-1.0 if leftward else 1.0) * max(abs(x1 - x0), min_dx)
                if not (pane[0] - 0.5 <= arrow_to <= pane[2] + 0.5):
                    arrow_to = x1
                segment = clip_segment((arrow_from, y), (arrow_to, y), pane)
                if segment is not None and abs(segment[1][0] - segment[0][0]) >= 1.0:
                    head_inside = pane[0] - 0.5 <= arrow_to <= pane[2] + 0.5
                    self.scene.line(
                        "pending:slide-arrow",
                        segment[0][0],
                        segment[0][1],
                        segment[1][0],
                        segment[1][1],
                        fill=edge,
                        width=3,
                        arrow="last" if head_inside else "none",
                        arrowshape=(12, 14, 5),
                    )
        self.scene.text(
            "pending:hint",
            layout.play_left + 8,
            layout.play_top + 46,
            pending.hint or config.TOOL_HINTS[config.TOOL_SELECT],
            fill=theme.pending,
            font=theme.font(theme.font_normal, bold=True),
            anchor="nw",
        )
        if pending.clipboard and pending.clipboard_beat is not None:
            self._draw_clipboard_ghost(chart, view, pending)

    def _draw_clipboard_ghost(self, chart: Chart, view: ViewState, pending: PendingPreview) -> None:
        from model.notes import clip_head, note_from_dict

        heads = [clip_head(d) for d in pending.clipboard]
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
                xa = view.norm_to_x(note.left.start)
                xb = view.norm_to_x(note.right.start)
                bounds = clip_bounds(
                    (min(xa, xb), min(y, y2), max(xa, xb), max(y, y2)),
                    mouse_pane_rect(view),
                )
                if bounds is not None:
                    self.scene.rect(
                        key, *bounds,
                        fill="", outline=theme.pending, width=2, dash=(3, 3),
                    )
            else:
                y = view.note_y(note.beat)
                bounds = clip_bounds(
                    (
                        view.norm_to_x(min(note.x_start, note.x_end)) - 4,
                        y - SLIDE_MIN_PX / 2,
                        view.norm_to_x(max(note.x_start, note.x_end)) + 4,
                        y + SLIDE_MIN_PX / 2,
                    ),
                    mouse_pane_rect(view),
                )
                if bounds is not None:
                    self.scene.rect(
                        key, *bounds,
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
                "演示模式",
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

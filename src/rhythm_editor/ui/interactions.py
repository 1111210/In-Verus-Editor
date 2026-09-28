"""工具状态机：放置、选择、粘贴、滚动与缩放。

所有修改都通过 ``Document`` 进行，因此每一步都可撤销。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .. import config
from ..model.chart import Chart
from ..model.document import Document
from ..model.notes import (
    BoundaryCurve,
    LaneNote,
    MouseRegion,
    MouseSlide,
    Note,
)
from .geometry import notes_at, notes_in_box, ruler_x_to_beat
from .renderer import PendingPreview, RenderState
from .view import ViewState

RegionEditor = Callable[[float, float, Tuple[float, float], Tuple[float, float]], Optional[Dict]]


class ToolController:
    """把鼠标/滚轮/键盘事件翻译成谱面编辑操作。"""

    def __init__(
        self,
        document: Document,
        view: ViewState,
        state: RenderState,
        region_editor: RegionEditor,
        status: Callable[[str], None],
    ) -> None:
        self.document = document
        self.view = view
        self.state = state
        self.region_editor = region_editor
        self.status = status
        self.tool = config.TOOL_SELECT
        self.paste_mode = False
        self.hover: Optional[Tuple[float, float]] = None
        self._press: Optional[Tuple[float, float]] = None
        self._rubber_origin: Optional[Tuple[float, float]] = None
        self._rubber_ctrl = False
        # 未完成操作的“已提交”阶段：0 = 只有悬停预览，1 = 等待第二次点击
        self._stage = 0
        self._first_lane: Optional[int] = None
        self._first_beat: Optional[float] = None
        self._first_x: Optional[float] = None
        # 空域放置：结束时间与四条边界数值（未点击的为 None）
        self._region_end_beat: Optional[float] = None
        self._region_left_start: Optional[float] = None
        self._region_left_end: Optional[float] = None
        self._region_right_start: Optional[float] = None
        self._region_right_end: Optional[float] = None

    # ------------------------------------------------------------------ 工具
    def set_tool(self, tool: str) -> None:
        if tool not in config.TOOL_ORDER:
            return
        if tool != self.tool:
            self.cancel_pending()
        self.tool = tool
        self.status(f"当前工具：{config.TOOL_LABELS[tool]}")

    def cancel_pending(self) -> None:
        """放弃未完成的操作（切换工具 / Esc）。"""
        pending = self.state.pending
        pending.tool = ""
        pending.lane_start = None
        pending.lane_end = None
        pending.beat_start = None
        pending.beat_end = None
        pending.x_start = None
        pending.x_end = None
        pending.left = None
        pending.right = None
        pending.hint = ""
        pending.clipboard = []
        pending.clipboard_beat = None
        self._stage = 0
        self._first_lane = None
        self._first_beat = None
        self._first_x = None
        self._region_end_beat = None
        self._region_left_start = None
        self._region_left_end = None
        self._region_right_start = None
        self._region_right_end = None
        self.paste_mode = False
        self._rubber_origin = None
        self.state.rubber_band = None

    def has_pending(self) -> bool:
        return self._stage > 0 or self.paste_mode

    # ------------------------------------------------------------------ 鼠标
    def on_press(self, x: float, y: float, ctrl: bool, shift: bool) -> None:
        self._press = (x, y)
        layout = self.view.layout
        if y >= layout.play_bottom:
            hold_pending = (
                self.tool in (config.TOOL_LANE_HOLD, config.TOOL_LANE_HOLD_MULTI)
                and self._stage == 1
            )
            if hold_pending and not self.paste_mode:
                self._place_lane_hold(x, y)
            else:
                self._jump_via_ruler(x)
            return
        if self.paste_mode:
            self._do_paste(x, y)
            return
        tool = self.tool
        if tool == config.TOOL_SELECT:
            self._press_select(x, y, ctrl)
        elif tool == config.TOOL_LANE_TAP:
            self._place_lane_tap(x, y)
        elif tool == config.TOOL_LANE_MULTI:
            self._place_lane_multi(x, y)
        elif tool == config.TOOL_LANE_HOLD:
            self._place_lane_hold(x, y)
        elif tool == config.TOOL_LANE_HOLD_MULTI:
            self._place_lane_hold(x, y)
        elif tool == config.TOOL_MOUSE_REGION:
            self._place_region(x, y)
        elif tool == config.TOOL_MOUSE_SLIDE:
            self._place_slide(x, y)

    def on_motion(self, x: float, y: float) -> None:
        self.hover = (x, y)
        self.state.hover = (x, y)
        self._update_ghost(x, y)
        if self._rubber_origin is not None:
            ox, oy = self._rubber_origin
            self.state.rubber_band = (ox, oy, x, y)

    def on_release(self, x: float, y: float, ctrl: bool) -> None:
        if self.tool == config.TOOL_SELECT and self._rubber_origin is not None:
            ox, oy = self._rubber_origin
            width = abs(x - ox)
            height = abs(y - oy)
            self._rubber_origin = None
            self.state.rubber_band = None
            if width > 4 or height > 4:
                found = notes_in_box(self.document.chart, self.view, ox, oy, x, y)
                ids = [n.id for n in found]
                if ctrl or self._rubber_ctrl:
                    self.document.xor_selection(ids)
                else:
                    self.document.select_only(ids)
                self.status(f"区域选择：{len(ids)} 个物件（当前共 {len(self.document.selection)} 个）")
        self._press = None

    def on_wheel(self, notches: float, ctrl: bool, shift: bool) -> None:
        if ctrl:
            self.view.zoom_by(config.ZOOM_STEP ** (-notches))
            self.status(f"缩放：可视 {self.view.zoom_seconds:.3g} 秒 ≈ {self.view.visible_beats():.2f} 拍")
            return
        # 滚轮一格 = 时间网格一格（1 / grid_division 拍）；Shift 加快到 1 拍。
        # scroll_beats 默认 snap=True，即使有零碎滚动量也会落回网格，避免判定线偏离刻度。
        per_notch = config.SCROLL_WHEEL_FAST_BEATS if shift else self.view.grid_step()
        self.view.scroll_beats(-notches * per_notch, snap=config.SCROLL_WHEEL_SNAP)
        unit = f"{config.SCROLL_WHEEL_FAST_BEATS:g} 拍（Shift 加速）" if shift else (
            f"1/{self.view.grid_division} 拍"
        )
        self.status(
            f"判定线：第 {self.view.current_beat:.3f} 拍（每次滚轮 = {unit}）"
            f" · {self.view.time_label(self.view.current_beat)}"
        )

    # ------------------------------------------------------------------ 选择
    def _press_select(self, x: float, y: float, ctrl: bool) -> None:
        hits = notes_at(self.document.chart, self.view, x, y)
        if hits:
            note = hits[0]
            if ctrl:
                self.document.xor_selection([note.id])
                self.status(f"切换选择：{note.id}（当前 {len(self.document.selection)} 个）")
            elif note.id not in self.document.selection:
                self.document.select_only([note.id])
                self.status(f"已选择 {note.id}")
            return
        if not ctrl:
            self.document.clear_selection()
        self._rubber_origin = (x, y)
        self._rubber_ctrl = ctrl
        self.state.rubber_band = (x, y, x, y)

    # ------------------------------------------------------------------ 放置
    def _lane_at(self, x: float) -> Optional[int]:
        return self.view.layout.lane_at(x)

    def _in_play(self, x: float, y: float) -> bool:
        return self.view.in_play_area(x, y)

    def _place_lane_tap(self, x: float, y: float) -> None:
        if not self._in_play(x, y):
            self.status("请在游玩区域内点击放置物件")
            return
        lane = self._lane_at(x)
        if lane is None:
            self.status("请在 6K 轨道内点击")
            return
        beat = self.view.snap_beat(self.view.y_to_beat(y))
        note = LaneNote(self.document.chart.new_note_id("L"), lane, lane, beat, beat)
        self.document.add_note(note, "放置单轨按键")
        self.status(f"已放置单轨按键 {note.id}：轨道 {lane + 1}，第 {beat:g} 拍")

    def _place_lane_multi(self, x: float, y: float) -> None:
        if not self._in_play(x, y):
            self.status("请在游玩区域内点击放置物件")
            return
        lane = self._lane_at(x)
        if lane is None:
            self.status("请在 6K 轨道内点击")
            return
        if self._stage == 0:
            self._stage = 1
            self._first_lane = lane
            self._first_beat = self.view.snap_beat(self.view.y_to_beat(y))
            hint = "多轨按键：请点击另一条轨道（时间以第一次点击为准，Esc 取消）"
            self._set_hint(hint)
            self.status(hint)
            return
        start_lane = self._first_lane if self._first_lane is not None else lane
        if start_lane == lane:
            self.status("请点击另一条轨道（或按 Esc 取消）")
            return
        beat = self._first_beat or 0.0
        note = LaneNote(
            self.document.chart.new_note_id("L"),
            min(start_lane, lane),
            max(start_lane, lane),
            beat,
            beat,
        )
        self.document.add_note(note, "放置多轨按键")
        self.status(f"已放置多轨按键 {note.id}：轨道 {note.lane_start + 1}-{note.lane_end + 1}，第 {beat:g} 拍")
        self.cancel_pending()

    def _place_lane_hold(self, x: float, y: float) -> None:
        multi = self.tool == config.TOOL_LANE_HOLD_MULTI
        if self._stage == 0:
            if not self._in_play(x, y):
                self.status("请在游玩区域内点击长按起点")
                return
            lane = self._lane_at(x)
            if lane is None:
                self.status("请在 6K 轨道内点击长按起点")
                return
            self._stage = 1
            self._first_lane = lane
            self._first_beat = self.view.snap_beat(self.view.y_to_beat(y))
            hint = (
                "多轨长按：请点击结束轨道与结束时刻（Esc 取消）"
                if multi
                else "长按：请点击结束时刻（可滚动/缩放，任意位置点击均可，Esc 取消）"
            )
            self._set_hint(hint)
            self.status(hint)
            return
        # 结束时刻：接受任意位置的点击（包括鼠标游玩区域）
        end = self.view.snap_beat(self.view.y_to_beat(y))
        start = self._first_beat or 0.0
        if end <= start:
            end = start + self.view.grid_step()
        start_lane = self._first_lane if self._first_lane is not None else 0
        end_lane = start_lane
        if multi:
            # 多轨长按：第二次点击的横坐标决定结束轨道
            lane = self._lane_at(x)
            if lane is not None:
                end_lane = lane
        note = LaneNote(
            self.document.chart.new_note_id("L"),
            min(start_lane, end_lane),
            max(start_lane, end_lane),
            start,
            end,
        )
        self.document.add_note(note, "放置多轨长按" if multi else "放置长按")
        self.status(
            f"已放置{'多轨长按' if multi else '长按'} {note.id}："
            f"轨道 {note.lane_start + 1}-{note.lane_end + 1}，第 {start:g} → {end:g} 拍"
        )
        self.cancel_pending()

    def _place_region(self, x: float, y: float) -> None:
        """放置空域：先定开始/结束时间，再依次点左边界首尾、右边界首尾，最后选线型。"""
        if self._stage <= 1:
            if not self._in_play(x, y):
                self.status("请在游玩区域内点击")
                return
            beat = self.view.snap_beat(self.view.y_to_beat(y))
            if self._stage == 0:
                self._stage = 1
                self._first_beat = beat
                self._region_end_beat = beat
                self._set_hint("空域：请点击结束时间（Esc 取消）")
            else:
                start = self._first_beat if self._first_beat is not None else beat
                end = beat if beat > start else start + self.view.grid_step()
                self._region_end_beat = end
                self._stage = 2
                self._set_hint(
                    "空域：请点击左边界起点位置（未放置的左边界点按 0 显示，Esc 取消）"
                )
            self.status(self.state.pending.hint)
            return

        if not self.view.in_mouse_area(x):
            self.status("空域边界请点击鼠标平面的合法区域（右侧区域）")
            return
        value = self._norm_x(x)
        if self._stage == 2:
            self._region_left_start = value
            self._set_hint("空域：请点击左边界终点位置（未放置的左边界点按 0 显示）")
        elif self._stage == 3:
            self._region_left_end = value
            self._set_hint("空域：请点击右边界起点位置（未放置的右边界点按 1 显示）")
        elif self._stage == 4:
            self._region_right_start = value
            self._set_hint("空域：请点击右边界终点位置，之后选择线型")
        elif self._stage == 5:
            self._region_right_end = value
            self._set_hint("空域：选择两条边界的线型")
            self._finish_region()
            return
        self._stage += 1
        self.status(self.state.pending.hint)

    def _finish_region(self) -> None:
        start = self._first_beat if self._first_beat is not None else 0.0
        end = self._region_end_beat if self._region_end_beat is not None else start
        left = (
            self._region_left_start
            if self._region_left_start is not None
            else config.REGION_PENDING_LEFT_DEFAULT,
            self._region_left_end
            if self._region_left_end is not None
            else config.REGION_PENDING_LEFT_DEFAULT,
        )
        right = (
            self._region_right_start
            if self._region_right_start is not None
            else config.REGION_PENDING_RIGHT_DEFAULT,
            self._region_right_end
            if self._region_right_end is not None
            else config.REGION_PENDING_RIGHT_DEFAULT,
        )
        data = self.region_editor(start, end, left, right)
        self.cancel_pending()
        if not data:
            self.status("已取消空域")
            return
        note = MouseRegion(
            self.document.chart.new_note_id("M"),
            float(data.get("beat_start", start)),
            float(data.get("beat_end", end)),
            BoundaryCurve.from_dict(data.get("left")),
            BoundaryCurve.from_dict(data.get("right")),
        )
        self.document.add_note(note, "放置空域")
        self.status(f"已放置空域 {note.id}：第 {note.beat_start:g} → {note.beat_end:g} 拍")

    def _norm_x(self, x: float) -> float:
        """鼠标平面像素横坐标 -> 归一化位置（开启横向网格时吸附到网格线）。"""
        return self.view.snap_norm(self.view.x_to_norm(x))

    def _slide_x(self, x: float) -> float:
        return self._norm_x(x)

    def _place_slide(self, x: float, y: float) -> None:
        if not self._in_play(x, y):
            self.status("请在游玩区域内点击")
            return
        if not self.view.in_mouse_area(x):
            self.status("滑键需要放在鼠标平面的合法区域内（右侧区域）")
            return
        if self._stage == 0:
            self._stage = 1
            self._first_beat = self.view.snap_beat(self.view.y_to_beat(y))
            self._first_x = self._slide_x(x)
            hint = "滑键：请点击第二次确定滑动方向与终点（Esc 取消）"
            self._set_hint(hint)
            self.status(hint)
            return
        x_start = self._first_x if self._first_x is not None else self._slide_x(x)
        x_end = self._slide_x(x)
        step = 1.0 / self.view.chart.settings.horizontal_divisions if (
            self.view.chart.settings.horizontal_divisions > 0
        ) else config.MIN_SLIDE_SPAN
        if abs(x_end - x_start) < 1e-9:
            x_end = x_start + max(step, config.MIN_SLIDE_SPAN)
        note = MouseSlide(
            self.document.chart.new_note_id("M"),
            self._first_beat or 0.0,
            x_start,
            x_end,
        )
        self.document.add_note(note, "放置鼠标滑键")
        direction = "右" if note.direction > 0 else "左"
        self.status(f"已放置滑键 {note.id}：第 {note.beat:g} 拍，向{direction}滑动")
        self.cancel_pending()

    # ------------------------------------------------------------------ 粘贴
    def begin_paste(self) -> None:
        if not self.document.clipboard:
            self.status("剪贴板为空：先用 Ctrl+C 复制物件")
            return
        self.paste_mode = True
        self._stage = 0
        self.state.pending.tool = ""
        self.state.pending.clipboard = list(self.document.clipboard)
        self.state.pending.clipboard_beat = self.view.snap_beat(self.view.current_beat)
        self.state.pending.hint = "粘贴：请点击目标开始时间（轨道/横向位置保持不变，Esc 取消）"
        self.status(self.state.pending.hint)

    def _do_paste(self, x: float, y: float) -> None:
        beat = self.view.snap_beat(self.view.y_to_beat(y))
        created = self.document.paste_clipboard(beat)
        self.cancel_pending()
        self.status(f"已粘贴 {len(created)} 个物件，起始时间第 {beat:g} 拍")

    # ------------------------------------------------------------------ 预览
    def _set_hint(self, text: str) -> None:
        self.state.pending.hint = text

    def _update_ghost(self, x: float, y: float) -> None:
        pending = self.state.pending
        if self.paste_mode:
            pending.clipboard_beat = self.view.snap_beat(self.view.y_to_beat(y))
            return
        tool = self.tool
        in_play = self._in_play(x, y)
        lane = self._lane_at(x) if in_play else None
        beat = self.view.snap_beat(self.view.y_to_beat(y))
        pending.tool = tool
        if tool == config.TOOL_LANE_TAP:
            if lane is None:
                pending.tool = ""
                pending.hint = ""
                return
            pending.lane_start = lane
            pending.lane_end = lane
            pending.beat_start = beat
            pending.beat_end = beat
            pending.hint = ""
        elif tool == config.TOOL_LANE_MULTI:
            if self._stage == 0:
                if lane is None:
                    pending.tool = ""
                    pending.hint = ""
                    return
                pending.lane_start = lane
                pending.lane_end = lane
                pending.beat_start = beat
                pending.beat_end = beat
                pending.hint = "多轨按键：点击起点轨道"
            else:
                pending.lane_start = self._first_lane
                pending.beat_start = self._first_beat
                pending.beat_end = self._first_beat
                pending.lane_end = lane if lane is not None else pending.lane_start
        elif tool in (config.TOOL_LANE_HOLD, config.TOOL_LANE_HOLD_MULTI):
            multi = tool == config.TOOL_LANE_HOLD_MULTI
            if self._stage == 0:
                if lane is None:
                    pending.tool = ""
                    pending.hint = ""
                    return
                pending.lane_start = lane
                pending.lane_end = lane
                pending.beat_start = beat
                pending.beat_end = beat
                pending.hint = "多轨长按：点击起点" if multi else "长按：点击起点"
            else:
                pending.lane_start = self._first_lane
                pending.beat_start = self._first_beat
                pending.beat_end = beat
                pending.lane_end = lane if (multi and lane is not None) else self._first_lane
        elif tool == config.TOOL_MOUSE_REGION:
            if not in_play:
                pending.tool = ""
                pending.hint = ""
                return
            default_left = config.REGION_PENDING_LEFT_DEFAULT
            default_right = config.REGION_PENDING_RIGHT_DEFAULT
            if self._stage == 0:
                pending.beat_start = beat
                pending.beat_end = beat + self.view.grid_step()
                pending.left = BoundaryCurve(default_left, default_left)
                pending.right = BoundaryCurve(default_right, default_right)
                pending.hint = "空域：点击开始时间"
                return
            pending.beat_start = self._first_beat
            end = self._region_end_beat
            if end is None:
                end = max(beat, (self._first_beat or 0.0) + self.view.grid_step())
            pending.beat_end = end
            cursor = self._norm_x(x) if self.view.in_mouse_area(x) else None
            left_start = self._region_left_start
            left_end = self._region_left_end
            right_start = self._region_right_start
            right_end = self._region_right_end
            # 尚未放置的点按默认值显示（左边界 0 / 右边界 1），正在等待的那个点跟随光标
            if self._stage == 2 and cursor is not None:
                left_start = cursor
            elif self._stage == 3 and cursor is not None:
                left_end = cursor
            elif self._stage == 4 and cursor is not None:
                right_start = cursor
            elif self._stage == 5 and cursor is not None:
                right_end = cursor
            pending.left = BoundaryCurve(
                left_start if left_start is not None else default_left,
                left_end if left_end is not None else default_left,
            )
            pending.right = BoundaryCurve(
                right_start if right_start is not None else default_right,
                right_end if right_end is not None else default_right,
            )
            pending.hint = {
                1: "空域：点击结束时间",
                2: "空域：点击左边界起点（未放置的点按 0 显示）",
                3: "空域：点击左边界终点",
                4: "空域：点击右边界起点（未放置的点按 1 显示）",
                5: "空域：点击右边界终点，之后选择线型",
            }.get(self._stage, "")
        elif tool == config.TOOL_MOUSE_SLIDE:
            if not in_play or not self.view.in_mouse_area(x):
                pending.tool = ""
                pending.hint = "滑键请在鼠标平面的合法区域内点击" if in_play else ""
                return
            if self._stage == 0:
                pending.beat_start = beat
                pending.x_start = self._slide_x(x)
                pending.x_end = pending.x_start
                pending.hint = "滑键：点击时间与起始位置"
            else:
                pending.beat_start = self._first_beat
                pending.x_start = self._first_x
                pending.x_end = self._slide_x(x)
        else:
            pending.tool = ""
            pending.hint = ""

    # ------------------------------------------------------------------ 其它
    def _jump_via_ruler(self, x: float) -> None:
        beat = ruler_x_to_beat(self.view.chart, self.view.layout.width, x)
        self.view.set_beat(beat)
        self.status(f"跳转到第 {self.view.current_beat:g} 拍")

    def selection_summary(self) -> str:
        count = len(self.document.selection)
        if count == 0:
            return "未选择物件"
        if count == 1:
            note = self.document.single_selected()
            return f"已选择 {note.id}" if note else f"已选择 {count} 个物件"
        return f"已选择 {count} 个物件"

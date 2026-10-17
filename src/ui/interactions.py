"""工具状态机：放置、选择、粘贴、滚动与缩放。

所有修改都通过 Document 进行，因此每一步都可撤销。
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

import config
from model.document import Document
from model.notes import (
    BoundaryCurve,
    ForbiddenRegion,
    LaneNote,
    MouseRegion,
    MouseSlide,
    Note,
)
from .geometry import notes_at, notes_in_box
from .renderer import RenderState
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
        self.state.pending.hint = self._tool_hint()
        self.paste_mode = False
        self.hover: Optional[Tuple[float, float]] = None
        self._press: Optional[Tuple[float, float]] = None
        self._rubber_origin: Optional[Tuple[float, float]] = None
        self._rubber_ctrl = False
        # 拖动选中物件：按下时记录原始数据，移动时按原始数据 + 偏移实时重建
        self._drag: Optional[Dict[str, object]] = None
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
        self.state.pending.hint = self._tool_hint()
        self.status(f"当前工具：{config.TOOL_LABELS[tool]}")

    def _tool_hint(self) -> str:
        return config.TOOL_HINTS.get(self.tool, config.TOOL_HINTS[config.TOOL_SELECT])

    def cancel_pending(self) -> None:
        """放弃未完成的操作（切换工具 / Esc）。"""
        pending = self.state.pending
        pending.tool = ""
        pending.step = 0
        pending.lane_start = None
        pending.lane_end = None
        pending.beat_start = None
        pending.beat_end = None
        pending.x_start = None
        pending.x_end = None
        pending.left = None
        pending.right = None
        pending.hint = self._tool_hint()
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
        # 点击也算一次“光标位置已知”：这样不移动鼠标时的预览也有基准点
        self.hover = (x, y)
        layout = self.view.layout
        if y >= layout.play_bottom:
            hold_pending = (
                self.tool == config.TOOL_LANE_HOLD and self._stage == 1
            )
            if hold_pending and not self.paste_mode:
                self._place_lane_hold(x, y)
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
        elif tool == config.TOOL_MOUSE_REGION:
            self._place_region(x, y)
        elif tool == config.TOOL_MOUSE_FORBIDDEN:
            self._place_region(x, y)
        elif tool == config.TOOL_MOUSE_SLIDE:
            self._place_slide(x, y)

    def on_motion(self, x: float, y: float) -> None:
        self.hover = (x, y)
        self.state.hover = (x, y)
        self._update_ghost(x, y)
        if self._drag is not None:
            self._drag_move(x, y)
            return
        if self._rubber_origin is not None:
            ox, oy = self._rubber_origin
            self.state.rubber_band = (ox, oy, x, y)

    def on_release(self, x: float, y: float, ctrl: bool) -> None:
        if self._drag is not None:
            before = self._drag["before"]
            self._drag = None
            after = [
                self.document.chart.find(str(item["id"])).to_dict()
                for item in before  # type: ignore[union-attr]
                if self.document.chart.find(str(item["id"])) is not None
            ]
            if after and after != before:
                self.document.commit(
                    self.document._notes_record(
                        "拖动物件", before, after, self.document.selection, self.document.selection  # type: ignore[arg-type]
                    )
                )
                self.status(f"已拖动 {len(after)} 个物件")
            self._press = None
            return
        if self.tool == config.TOOL_SELECT and self._rubber_origin is not None:
            ox, oy = self._rubber_origin
            width = abs(x - ox)
            height = abs(y - oy)
            self._rubber_origin = None
            self.state.rubber_band = None
            if width > 4 or height > 4:
                found = notes_in_box(
                    self.document.chart,
                    self.view,
                    ox,
                    oy,
                    x,
                    y,
                    cache=self.state.hit_cache,
                )
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
        unit = f"{config.SCROLL_WHEEL_FAST_BEATS:g} 拍" if shift else (
            f"1/{self.view.grid_division} 拍"
        )
        self.status(
            f"判定线：第 {self.view.current_beat:.3f} 拍（每次滚轮 = {unit}）"
            f" · {self.view.time_label(self.view.current_beat)}"
        )

    # ------------------------------------------------------------------ 选择
    def _press_select(self, x: float, y: float, ctrl: bool) -> None:
        hits = notes_at(
            self.document.chart, self.view, x, y, cache=self.state.hit_cache
        )
        if hits:
            note = hits[0]
            if ctrl:
                self.document.xor_selection([note.id])
                self.status(f"切换选择：{note.id}（当前 {len(self.document.selection)} 个）")
            elif note.id not in self.document.selection:
                self.document.select_only([note.id])
                self.status(f"已选择 {note.id}")
            # 选中的物件可以直接拖动（时间 / 轨道 / 横向位置）
            selected = self.document.selected_notes()
            if selected:
                self._drag = {
                    "origin": (x, y),
                    "before": [n.to_dict() for n in selected],
                    "start_beat": self.view.y_to_beat(y),
                    "moved": False,
                }
            return
        if not ctrl:
            self.document.clear_selection()
        self._rubber_origin = (x, y)
        self._rubber_ctrl = ctrl
        self.state.rubber_band = (x, y, x, y)

    # ------------------------------------------------------------------ 放置
    def _drag_move(self, x: float, y: float) -> None:
        """把选中的物件按“原始数据 + 偏移”实时重建（松手时才写一条撤销记录）。"""
        from model.notes import note_from_dict

        drag = self._drag
        if drag is None:
            return
        origin_x, origin_y = drag["origin"]  # type: ignore[misc]
        beats = self.view.snap_beat(self.view.y_to_beat(y)) - self.view.snap_beat(
            float(drag["start_beat"])  # type: ignore[arg-type]
        )
        lane_delta = 0
        lane_now = self.view.layout.lane_at(x)
        lane_was = self.view.layout.lane_at(origin_x)
        if lane_now is not None and lane_was is not None:
            lane_delta = lane_now - lane_was
        # 横向位移用鼠标光标的真实位移；吸附则在每个物件自己的“锚点”上做：
        # 滑键按起点、空域 / 禁区按起点（beat_start 处）左右边界的中点。
        # 这样即使物件是在另一套横向网格下创建的，拖动后锚点也一定落在当前网格线上，
        # 而且不会改变物件自身的长度 / 宽度。
        raw_delta = self.view.x_to_norm(x) - self.view.x_to_norm(origin_x)
        grid_on = self.view.settings.horizontal_divisions > 0
        if beats == 0.0 and lane_delta == 0 and abs(raw_delta) < 1e-9:
            return
        rebuilt: List[Dict[str, Any]] = []
        reported = raw_delta
        for item in drag["before"]:  # type: ignore[union-attr]
            note = note_from_dict(dict(item))
            x_delta = raw_delta
            anchor = self._anchor_x(note)
            if grid_on and anchor is not None:
                x_delta = self.view.snap_norm(anchor + raw_delta) - anchor
                reported = x_delta
            self._shift_note(note, beats, lane_delta, x_delta)
            rebuilt.append(note.to_dict())
        self.document._apply_notes_state([str(i["id"]) for i in drag["before"]], rebuilt)  # type: ignore[arg-type]
        drag["moved"] = True
        self.status(
            f"拖动中：{beats:+g} 拍"
            + (f" · 轨道 {lane_delta:+d}" if lane_delta else "")
            + (f" · 横向 {reported:+.3f}" if abs(reported) > 1e-9 else "")
        )

    @staticmethod
    def _anchor_x(note: Note) -> Optional[float]:
        """拖动的“对齐锚点”：滑键 = 起点；空域 / 禁区 = 起点处左右边界的中点。"""
        if isinstance(note, MouseSlide):
            return float(note.x_start)
        if isinstance(note, MouseRegion):
            return (float(note.left.start) + float(note.right.start)) / 2.0
        return None

    @staticmethod
    def _shift_note(note: Note, beats: float, lane_delta: int, x_delta: float) -> None:
        """按偏移移动一个物件（时间 / 轨道 / 横向位置三类按类型处理）。"""
        note.shift(beats)
        if isinstance(note, LaneNote):
            if lane_delta:
                span = note.lane_end - note.lane_start
                start = min(
                    max(0, note.lane_start + lane_delta), config.LANE_COUNT - 1 - span
                )
                note.lane_start = start
                note.lane_end = start + span
            return
        if isinstance(note, MouseRegion):
            for curve in (note.left, note.right):
                curve.start = min(1.0, max(0.0, curve.start + x_delta))
                curve.end = min(1.0, max(0.0, curve.end + x_delta))
            return
        if isinstance(note, MouseSlide):
            note.x_start = min(1.0, max(0.0, note.x_start + x_delta))
            note.x_end = min(1.0, max(0.0, note.x_end + x_delta))

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
            hint = "多轨按键：请点击另一条轨道"
            self._set_hint(hint)
            self.status(hint)
            return
        start_lane = self._first_lane if self._first_lane is not None else lane
        if start_lane == lane:
            self.status("请点击另一条轨道")
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
            hint = "长按：请点击结束轨道与结束时刻"
            self._set_hint(hint)
            self.status(hint)
            return
        # 结束轨道与结束时刻：任意位置点击都接受（点到鼠标区域时轨道保持不变）
        end = self.view.snap_beat(self.view.y_to_beat(y))
        start = self._first_beat or 0.0
        if end <= start:
            end = start + self.view.grid_step()
        start_lane = self._first_lane if self._first_lane is not None else 0
        end_lane = start_lane
        # 第二次点击的横坐标决定结束轨道（不在 6K 区域内则沿用起始轨道）
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
        self.document.add_note(note, "放置长按")
        self.status(
            f"已放置长按 {note.id}："
            f"轨道 {note.lane_start + 1}-{note.lane_end + 1}，第 {start:g} → {end:g} 拍"
        )
        self.cancel_pending()

    def _region_label(self) -> str:
        return "禁区" if self.tool == config.TOOL_MOUSE_FORBIDDEN else "空域"

    def _place_region(self, x: float, y: float) -> None:
        """放置空域 / 禁区：先定开始/结束时间，再依次点左边界首尾、右边界首尾，最后选线型。"""
        label = self._region_label()
        if self._stage <= 1:
            if not self._in_play(x, y):
                self.status("请在游玩区域内点击")
                return
            beat = self.view.snap_beat(self.view.y_to_beat(y))
            if self._stage == 0:
                self._stage = 1
                self._first_beat = beat
                # 结束时间还没选：保持为空，让预览跟着光标走（否则预览高度为 0，看不见）
                self._region_end_beat = None
                self._set_hint(f"{label}：请点击结束时间")
            else:
                start = self._first_beat if self._first_beat is not None else beat
                end = beat if beat > start else start + self.view.grid_step()
                self._region_end_beat = end
                self._stage = 2
                self._set_hint(
                    f"{label}：请点击左边界起点位置"
                )
            self.status(self.state.pending.hint)
            return

        value = self._norm_x(x)
        if value is None:
            self.status(f"{label}边界请点击鼠标平面区域")
            return
        if self._stage == 2:
            self._region_left_start = value
            self._set_hint(f"{label}：请点击左边界终点位置")
        elif self._stage == 3:
            self._region_left_end = value
            self._set_hint(f"{label}：请点击右边界起点位置")
        elif self._stage == 4:
            self._region_right_start = value
            self._set_hint(f"{label}：请点击右边界终点位置，之后选择线型")
        elif self._stage == 5:
            self._region_right_end = value
            self._set_hint(f"{label}：选择两条边界的线型")
            self._finish_region()
            return
        self._stage += 1
        self.status(self.state.pending.hint)

    def _finish_region(self) -> None:
        label = self._region_label()
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
            self.status(f"已取消{label}")
            return
        note_class = (
            ForbiddenRegion if self.tool == config.TOOL_MOUSE_FORBIDDEN else MouseRegion
        )
        note = note_class(
            self.document.chart.new_note_id("M"),
            float(data.get("beat_start", start)),
            float(data.get("beat_end", end)),
            BoundaryCurve.from_dict(data.get("left")),
            BoundaryCurve.from_dict(data.get("right")),
        )
        self.document.add_note(note, f"放置{label}")
        self.status(f"已放置{label} {note.id}：第 {note.beat_start:g} → {note.beat_end:g} 拍")

    def _norm_x(self, x: float) -> Optional[float]:
        """鼠标平面像素横坐标 -> 归一化位置。

        * 启用横向辅助网格时：吸附到最近的网格线，并夹进 0..1 合法区域内；
        * 未启用时：保留鼠标的真实位置 —— 即使超出合法区域（出界）也照常放置。
        * 只有当点击落在鼠标平面区域之外（例如点在 6K 区域里）才返回 None。
        """
        if not self.view.in_mouse_pane(x):
            return None
        value = self.view.x_to_norm(x)
        if self.view.settings.horizontal_divisions > 0:
            value = self.view.snap_norm(value)
            value = min(1.0, max(0.0, value))
        return float(value)

    def _slide_x(self, x: float) -> Optional[float]:
        return self._norm_x(x)

    def _place_slide(self, x: float, y: float) -> None:
        if not self._in_play(x, y):
            self.status("请在游玩区域内点击")
            return
        if self._stage == 0:
            value = self._slide_x(x)
            if value is None:
                self.status("滑键请点击鼠标平面区域")
                return
            self._stage = 1
            self._first_beat = self.view.snap_beat(self.view.y_to_beat(y))
            self._first_x = value
            hint = "滑键：请点击第二次确定滑动方向与终点"
            self._set_hint(hint)
            self.status(hint)
            return
        x_end_value = self._slide_x(x)
        if x_end_value is None:
            self.status("滑键请点击鼠标平面区域")
            return
        x_start = self._first_x if self._first_x is not None else x_end_value
        x_end = x_end_value
        step = 1.0 / self.view.settings.horizontal_divisions if (
            self.view.settings.horizontal_divisions > 0
        ) else config.MIN_SLIDE_SPAN
        if abs(x_end - x_start) < 1e-9:
            # 吸附后落在同一格：用未吸附的点击位置判断方向，再按方向补一个最小跨度，
            # 这样“第二次点在左边就是向左滑”，不会一律变成向右。
            raw = self.view.x_to_norm(x)
            direction = -1.0 if raw < x_start else 1.0
            x_end = x_start + direction * max(step, config.MIN_SLIDE_SPAN)
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
        self.state.pending.hint = "粘贴：请点击目标开始时间"
        self.status(self.state.pending.hint)

    def _do_paste(self, x: float, y: float) -> None:
        beat = self.view.snap_beat(self.view.y_to_beat(y))
        created = self.document.paste_clipboard(beat)
        self.cancel_pending()
        self.status(f"已粘贴 {len(created)} 个物件，起始时间第 {beat:g} 拍")

    # ------------------------------------------------------------------ 预览
    def _set_hint(self, text: str) -> None:
        self.state.pending.hint = text

    def refresh_preview(self) -> None:
        """按最后一次光标位置重算预览（主循环每帧调用）。

        点击会记录光标位置；每帧重算使提示与边界在阶段推进后立即更新，
        不依赖鼠标再次移动。
        """
        if self.hover is None:
            return
        self._update_ghost(self.hover[0], self.hover[1])

    def _update_ghost(self, x: float, y: float) -> None:
        pending = self.state.pending
        pending.step = self._stage
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
                pending.hint = self._tool_hint()
                return
            pending.lane_start = lane
            pending.lane_end = lane
            pending.beat_start = beat
            pending.beat_end = beat
            pending.hint = self._tool_hint()
        elif tool == config.TOOL_LANE_MULTI:
            if self._stage == 0:
                if lane is None:
                    pending.tool = ""
                    pending.hint = self._tool_hint()
                    return
                pending.lane_start = lane
                pending.lane_end = lane
                pending.beat_start = beat
                pending.beat_end = beat
                pending.hint = self._tool_hint()
            else:
                pending.lane_start = self._first_lane
                pending.beat_start = self._first_beat
                pending.beat_end = self._first_beat
                pending.lane_end = lane if lane is not None else pending.lane_start
                pending.hint = "多轨按键：请点击另一条轨道"
        elif tool == config.TOOL_LANE_HOLD:
            if self._stage == 0:
                if lane is None:
                    pending.tool = ""
                    pending.hint = self._tool_hint()
                    return
                pending.lane_start = lane
                pending.lane_end = lane
                pending.beat_start = beat
                pending.beat_end = beat
                pending.hint = self._tool_hint()
            else:
                pending.lane_start = self._first_lane
                pending.beat_start = self._first_beat
                pending.beat_end = beat
                pending.lane_end = lane if lane is not None else self._first_lane
                pending.hint = "长按：请点击结束轨道与结束时间"
        elif tool in (config.TOOL_MOUSE_REGION, config.TOOL_MOUSE_FORBIDDEN):
            label = self._region_label()
            stage_hints = {
                1: f"{label}：点击结束时间",
                2: f"{label}：点击左边界起点",
                3: f"{label}：点击左边界终点",
                4: f"{label}：点击右边界起点",
                5: f"{label}：点击右边界终点，之后选择线型",
            }
            if not in_play:
                pending.tool = ""
                pending.hint = stage_hints.get(self._stage, self._tool_hint())
                return
            default_left = config.REGION_PENDING_LEFT_DEFAULT
            default_right = config.REGION_PENDING_RIGHT_DEFAULT
            if self._stage == 0:
                pending.beat_start = beat
                pending.beat_end = beat + self.view.grid_step()
                pending.left = BoundaryCurve(default_left, default_left)
                pending.right = BoundaryCurve(default_right, default_right)
                pending.hint = f"{label}：点击开始时间"
                return
            pending.beat_start = self._first_beat
            if self._stage == 1 or self._region_end_beat is None:
                # 正在选结束时间：结束拍跟随光标预览（至少一个网格格）
                end = max(beat, (self._first_beat or 0.0) + self.view.grid_step())
            else:
                end = self._region_end_beat
            pending.beat_end = end
            cursor = self._norm_x(x)
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
            pending.hint = stage_hints.get(self._stage, self._tool_hint())
        elif tool == config.TOOL_MOUSE_SLIDE:
            if not in_play:
                pending.tool = ""
                pending.hint = self._tool_hint()
                return
            cursor = self._slide_x(x)
            if cursor is None:
                pending.tool = ""
                pending.hint = "滑键请点击鼠标平面区域"
                return
            if self._stage == 0:
                pending.beat_start = beat
                pending.x_start = cursor
                pending.x_end = pending.x_start
                pending.hint = self._tool_hint()
            else:
                pending.beat_start = self._first_beat
                pending.x_start = self._first_x
                pending.x_end = cursor
                pending.hint = "滑键：请点击终点与滑动方向"
        else:
            pending.tool = ""
            pending.hint = self._tool_hint()

    # ------------------------------------------------------------------ 其它
    def selection_summary(self) -> str:
        count = len(self.document.selection)
        if count == 0:
            return "未选择物件"
        if count == 1:
            note = self.document.single_selected()
            return f"已选择 {note.id}" if note else f"已选择 {count} 个物件"
        return f"已选择 {count} 个物件"

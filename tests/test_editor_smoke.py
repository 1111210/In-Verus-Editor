"""端到端冒烟测试：真实 Tk 画布 + 渲染器 + 工具状态机。

需要可用的图形环境（Windows 桌面 / Linux X11 或 Xvfb）；没有显示时自动跳过。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tkinter as tk

from rhythm_editor import config
from rhythm_editor.demo import build_demo_chart
from rhythm_editor.model.chart import Chart
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from rhythm_editor.ui.interactions import ToolController
from rhythm_editor.ui.renderer import RenderState, Renderer
from rhythm_editor.ui.scene import Scene
from rhythm_editor.ui.theme import DARK
from rhythm_editor.ui.view import ViewState

WIDTH, HEIGHT = 1280, 800


class EditorSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover - 无图形环境
            raise unittest.SkipTest(f"没有可用的图形环境：{exc}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass

    def setUp(self) -> None:
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, highlightthickness=0)
        self.chart = Chart()
        self.chart.settings.horizontal_divisions = 8
        self.document = Document(self.chart)
        self.view = ViewState(self.chart)
        self.view.resize(WIDTH, HEIGHT)
        self.view.set_beat(0.0)
        self.scene = Scene(self.canvas)
        self.renderer = Renderer(self.scene, DARK)
        self.state = RenderState()
        self.messages: list[str] = []
        self.region_result = None
        self.controller = ToolController(
            self.document,
            self.view,
            self.state,
            self._region_editor,
            lambda text: self.messages.append(text),
        )

    def tearDown(self) -> None:
        self.canvas.destroy()

    # -------------------------------------------------------------- 辅助
    def _region_editor(self, beat_start: float, beat_end: float):
        return self.region_result

    def draw(self, chart: Chart | None = None) -> None:
        chart = chart or self.document.chart
        self.state.selection = set(self.document.selection)
        self.renderer.draw(chart, self.view, self.state)

    def click(self, x: float, y: float, ctrl: bool = False) -> None:
        self.controller.on_motion(x, y)
        self.controller.on_press(x, y, ctrl, False)
        self.controller.on_release(x, y, ctrl)

    def y_of_beat(self, beat: float) -> float:
        return self.view.beat_to_y(beat)

    # -------------------------------------------------------------- 渲染
    def test_render_demo_chart(self) -> None:
        demo = build_demo_chart()
        self.view.set_chart(demo)
        self.view.set_beat(10.0)
        self.draw(demo)
        for key in ("bg", "lane:0", "lane:5", "judge", "mouse:legal", "ruler", "judge-label"):
            self.assertIn(key, self.scene._items, f"缺少画布元素 {key}")
        note_items = [k for k in self.scene._items if k.startswith(("note:", "region:", "slide:"))]
        self.assertGreater(len(note_items), 3)
        self.assertGreater(self.scene.item_count, 25)
        # 第二帧不应重建对象（差分渲染）
        before = dict(self.scene._items)
        self.draw(demo)
        self.assertEqual(set(before), set(self.scene._items))

    def test_ruler_and_horizontal_grid(self) -> None:
        self.draw()
        self.assertIn("hgrid:0", self.scene._items)
        self.assertIn("hgrid:8", self.scene._items)
        x0 = self.scene._items["hgrid:0"][2][0][0]
        x8 = self.scene._items["hgrid:8"][2][0][0]
        self.assertAlmostEqual(x0, self.view.layout.mouse_left, places=1)
        self.assertAlmostEqual(x8, self.view.layout.mouse_right, places=1)
        self.chart.settings.horizontal_divisions = 0
        self.draw()
        self.assertNotIn("hgrid:0", self.scene._items)

    def test_judgment_line_is_grid_aligned(self) -> None:
        self.chart.settings.grid_division = 4
        self.view.set_beat(3.4)
        self.assertAlmostEqual(self.view.current_beat, 3.5)
        self.draw()
        judge = self.scene._items["judge"][2][0]
        self.assertAlmostEqual(judge[1], self.view.layout.judgment_y, places=3)
        # 判定线上的拍与网格完全重合：该拍的网格线 y 与判定线 y 相同
        self.assertAlmostEqual(self.view.beat_to_y(self.view.current_beat), self.view.layout.judgment_y, places=6)

    # -------------------------------------------------------------- 放置
    def test_place_lane_tap(self) -> None:
        self.controller.set_tool(config.TOOL_LANE_TAP)
        x = self.view.layout.lane_center(3)
        self.click(x, self.y_of_beat(2.0))
        notes = self.document.chart.lane_notes
        self.assertEqual(len(notes), 1)
        self.assertEqual((notes[0].lane_start, notes[0].lane_end), (3, 3))
        self.assertAlmostEqual(notes[0].beat_start, 2.0)
        self.assertFalse(notes[0].is_hold)
        # 吸附：点击 2.03 拍的位置仍然吸附到 2.0
        self.click(x, self.y_of_beat(2.03))
        self.assertAlmostEqual(self.document.chart.lane_notes[1].beat_start, 2.0)

    def test_place_multi_lane(self) -> None:
        self.controller.set_tool(config.TOOL_LANE_MULTI)
        self.click(self.view.layout.lane_center(1), self.y_of_beat(3.0))
        self.assertTrue(self.state.pending.hint)
        self.click(self.view.layout.lane_center(4), self.y_of_beat(3.5))
        notes = self.document.chart.lane_notes
        self.assertEqual(len(notes), 1)
        self.assertEqual((notes[0].lane_start, notes[0].lane_end), (1, 4))
        # 时间以第一次点击为准
        self.assertAlmostEqual(notes[0].beat_start, 3.0)
        self.assertFalse(self.state.pending.hint)

    def test_place_hold_accepts_click_anywhere(self) -> None:
        self.controller.set_tool(config.TOOL_LANE_HOLD)
        self.click(self.view.layout.lane_center(0), self.y_of_beat(4.0))
        # 第二次点击落在鼠标平面（中间四条轨道）上，同样被接收
        self.click(self.view.norm_to_x(0.1), self.y_of_beat(6.0))
        notes = self.document.chart.lane_notes
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].is_hold)
        self.assertAlmostEqual(notes[0].beat_start, 4.0)
        self.assertAlmostEqual(notes[0].beat_end, 6.0)
        self.assertEqual(notes[0].lane_start, 0)

    def test_place_region_uses_dialog_result(self) -> None:
        self.region_result = {
            "beat_start": 4.0,
            "beat_end": 6.0,
            "left": {"start": 0.1, "end": 0.3, "ease": config.EASE_OUT},
            "right": {"start": 0.9, "end": 0.7, "ease": config.EASE_IN},
        }
        self.controller.set_tool(config.TOOL_MOUSE_REGION)
        self.click(self.view.norm_to_x(0.5), self.y_of_beat(4.0))
        self.assertTrue(self.state.pending.hint)
        self.click(self.view.norm_to_x(0.5), self.y_of_beat(6.0))
        regions = [n for n in self.document.chart.mouse_notes if isinstance(n, MouseRegion)]
        self.assertEqual(len(regions), 1)
        self.assertAlmostEqual(regions[0].left.start, 0.1)
        self.assertEqual(regions[0].right.ease, config.EASE_IN)
        self.assertFalse(self.state.pending.hint)

    def test_place_slide_snaps_to_horizontal_grid(self) -> None:
        self.controller.set_tool(config.TOOL_MOUSE_SLIDE)
        self.click(self.view.norm_to_x(0.26), self.y_of_beat(3.0))
        self.click(self.view.norm_to_x(0.74), self.y_of_beat(3.5))
        slides = [n for n in self.document.chart.mouse_notes if isinstance(n, MouseSlide)]
        self.assertEqual(len(slides), 1)
        self.assertAlmostEqual(slides[0].x_start, 0.25)
        self.assertAlmostEqual(slides[0].beat, 3.0)
        self.assertEqual(slides[0].direction, 1)
        # 反方向
        self.click(self.view.norm_to_x(0.75), self.y_of_beat(5.0))
        self.click(self.view.norm_to_x(0.25), self.y_of_beat(5.5))
        slides = [n for n in self.document.chart.mouse_notes if isinstance(n, MouseSlide)]
        self.assertEqual(slides[1].direction, -1)

    # -------------------------------------------------------------- 选择 / 编辑
    def test_rubber_band_and_ctrl_xor(self) -> None:
        self.controller.set_tool(config.TOOL_LANE_TAP)
        for lane in (0, 3, 5):
            self.click(self.view.layout.lane_center(lane), self.y_of_beat(2.0))
        self.controller.set_tool(config.TOOL_SELECT)
        layout = self.view.layout
        left = layout.lane_rects[0][0] - 6
        right = layout.lane_rects[5][1] + 6
        top, bottom = self.y_of_beat(2.5), self.y_of_beat(1.5)
        self.controller.on_motion(left, top)
        self.controller.on_press(left, top, False, False)
        self.controller.on_motion(right, bottom)
        self.controller.on_release(right, bottom, False)
        self.assertEqual(len(self.document.selection), 3)
        # Ctrl 框选同一批物件 -> 对称差，选择被清空
        self.controller.on_press(left, top, True, False)
        self.controller.on_motion(right, bottom)
        self.controller.on_release(right, bottom, True)
        self.assertEqual(len(self.document.selection), 0)

    def test_click_selection_and_edit(self) -> None:
        self.controller.set_tool(config.TOOL_LANE_TAP)
        self.click(self.view.layout.lane_center(2), self.y_of_beat(2.0))
        note = self.document.chart.lane_notes[0]
        self.controller.set_tool(config.TOOL_SELECT)
        bounds = None
        from rhythm_editor.ui.geometry import lane_note_bounds

        bounds = lane_note_bounds(note, self.document.chart, self.view)
        self.click((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        self.assertEqual(self.document.selection, {note.id})
        self.draw()
        self.assertIn(f"sel:{note.id}", self.scene._items)

    def test_paste_flow(self) -> None:
        self.controller.set_tool(config.TOOL_LANE_TAP)
        self.click(self.view.layout.lane_center(1), self.y_of_beat(2.0))
        self.controller.set_tool(config.TOOL_SELECT)
        self.document.select_all()
        self.document.copy_selection()
        self.controller.set_tool(config.TOOL_LANE_TAP)
        self.controller.begin_paste()
        self.assertTrue(self.controller.paste_mode)
        self.click(self.view.layout.lane_center(1), self.y_of_beat(20.0))
        self.assertEqual(len(self.document.chart.lane_notes), 2)
        self.assertAlmostEqual(self.document.chart.lane_notes[1].beat_start, 20.0)
        self.assertEqual(self.document.chart.lane_notes[1].lane_start, 1)
        self.assertFalse(self.controller.paste_mode)

    def test_cancel_pending_on_tool_switch(self) -> None:
        self.controller.set_tool(config.TOOL_LANE_HOLD)
        self.click(self.view.layout.lane_center(1), self.y_of_beat(4.0))
        self.assertTrue(self.state.pending.hint)
        self.controller.set_tool(config.TOOL_LANE_TAP)
        self.assertFalse(self.state.pending.hint)

    # -------------------------------------------------------------- 视图
    def test_scroll_snaps_to_grid(self) -> None:
        self.chart.settings.grid_division = 4
        self.controller.on_wheel(1.0, False, False)
        self.assertNotEqual(self.view.current_beat, 0.0)
        self.assertAlmostEqual(self.view.current_beat * 4, round(self.view.current_beat * 4))
        self.controller.on_wheel(-1.0, False, False)
        self.assertAlmostEqual(self.view.current_beat, 0.0)

    def test_zoom_changes_pixels_per_beat(self) -> None:
        before = self.view.px_per_beat()
        self.controller.on_wheel(1.0, True, False)
        after = self.view.px_per_beat()
        self.assertGreater(after, before)
        # 缩放后判定线仍在同一位置（只有时间尺度变化）
        self.assertAlmostEqual(self.view.beat_to_y(self.view.current_beat), self.view.layout.judgment_y, places=6)

    def test_ruler_click_jumps(self) -> None:
        self.controller.on_motion(WIDTH / 2, HEIGHT - 10)
        self.controller.on_press(WIDTH / 2, HEIGHT - 10, False, False)
        self.assertGreater(self.view.current_beat, 0.0)

    def test_note_geometry_reflects_speed_group(self) -> None:
        from rhythm_editor.model.speed import SpeedSegment

        group = self.chart.add_group("fast", [SpeedSegment(0.0, 1.0), SpeedSegment(0.0, 2.0)])
        note = LaneNote("L1", 0, 0, 4.0)
        self.chart.add(note, group=group.id)
        plain = LaneNote("L2", 0, 0, 4.0)
        self.chart.add(plain)
        self.view.set_beat(0.0)
        y_group = self.view.note_y(note.beat_start, group)
        y_plain = self.view.note_y(plain.beat_start, self.chart.group_by_id(0))
        # 2 倍流速下同一时间的物件离判定线更远（屏幕上更高）
        self.assertLess(y_group, y_plain)

    def test_far_note_pushed_back_to_judgment_line_is_drawn_and_hittable(self) -> None:
        """回归测试：正负流速叠加把“很远的”物件推回判定线时，必须照常渲染并可点选。

        不能用拍差做剔除：这里的物件判定时刻与判定线相差 200 拍，
        但 ∫v 恰好为 0，所以它实实在在就在判定线上。
        """
        from rhythm_editor.model.speed import SpeedSegment
        from rhythm_editor.ui.geometry import notes_at

        group = self.chart.add_group(
            "roundtrip",
            [
                SpeedSegment(0.0, 1.0),
                SpeedSegment(100.0, -1.0),
                SpeedSegment(200.0, 1.0),
            ],
        )
        far_note = LaneNote("L1", 2, 2, 300.0)
        self.chart.add(far_note, group=group.id)
        self.view.set_beat(100.0)
        self.assertAlmostEqual(group.judging_distance(100.0, 300.0), 0.0, places=9)
        self.draw()
        self.assertIn("note:L1", self.scene._items)
        coords = self.scene._items["note:L1"][2][0]
        # 按键的判定边（下边缘）正好压在判定线上
        self.assertAlmostEqual(max(coords[1], coords[3]), self.view.layout.judgment_y, places=6)
        hits = notes_at(
            self.chart,
            self.view,
            (coords[0] + coords[2]) / 2,
            (coords[1] + coords[3]) / 2,
        )
        self.assertTrue(any(note.id == "L1" for note in hits))

    def test_note_flung_far_below_line_is_culled_by_pixels_only(self) -> None:
        """同一速度组内，被甩到画面外的物件按像素位置剔除（而不是按拍差）。"""
        from rhythm_editor.model.speed import SpeedSegment

        group = self.chart.add_group(
            "roundtrip2",
            [
                SpeedSegment(0.0, 1.0),
                SpeedSegment(100.0, -1.0),
                SpeedSegment(200.0, 1.0),
            ],
        )
        flung = LaneNote("L2", 0, 0, 200.0)
        self.chart.add(flung, group=group.id)
        self.view.set_beat(100.0)
        # 判定线在 100 拍：判定时刻 200 拍的物件被推到判定线下方 100 个单位（画面外）
        self.assertAlmostEqual(group.judging_distance(100.0, 200.0), -100.0, places=9)
        self.draw()
        self.assertNotIn("note:L2", self.scene._items)


if __name__ == "__main__":
    unittest.main()

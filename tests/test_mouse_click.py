"""鼠标点击按键：数据模型、放置工具、渲染与自动检查。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tkinter as tk

from rhythm_editor import config
from rhythm_editor.model.chart import Chart
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import BoundaryCurve, MouseClick, MouseRegion, MouseSlide, note_from_dict
from rhythm_editor.model.validate import (
    ISSUE_CLICK,
    ISSUE_CLICK_SAME_BEAT,
    validate_chart,
)
from rhythm_editor.ui.interactions import ToolController
from rhythm_editor.ui.geometry import TAP_MIN_PX, notes_at, notes_in_box
from rhythm_editor.ui.renderer import RenderState, Renderer
from rhythm_editor.ui.scene import Scene
from rhythm_editor.ui.theme import DARK
from rhythm_editor.ui.view import ViewState

WIDTH, HEIGHT = 1280, 800


class ClickModelTests(unittest.TestCase):
    def test_geometry_and_serialization(self) -> None:
        note = MouseClick("M1", 8.0, 0.5, 0.2)
        self.assertAlmostEqual(note.x_start, 0.3)
        self.assertAlmostEqual(note.x_end, 0.7)
        self.assertTrue(note.contains_x(0.5))
        self.assertFalse(note.contains_x(0.75))
        restored = note_from_dict(note.to_dict())
        self.assertEqual(restored.to_dict(), note.to_dict())

    def test_min_radius(self) -> None:
        note = MouseClick("M2", 0.0, 0.5, 0.0)
        self.assertAlmostEqual(note.radius, config.MIN_CLICK_RADIUS)

    def test_mirror_and_shift_and_clone(self) -> None:
        note = MouseClick("M3", 4.0, 0.2, 0.1)
        note.mirrored()
        self.assertAlmostEqual(note.center, 0.8)
        note.shift(2.0)
        self.assertAlmostEqual(note.beat, 6.0)
        clone = note.clone("M4", 1.0)
        self.assertEqual(clone.id, "M4")
        self.assertAlmostEqual(clone.beat, 7.0)
        self.assertAlmostEqual(clone.center, 0.8)
        self.assertAlmostEqual(clone.radius, 0.1)

    def test_counts(self) -> None:
        chart = Chart()
        chart.add(MouseClick("M1", 1.0, 0.5, 0.1))
        self.assertEqual(chart.note_counts()["click"], 1)


class ClickValidateTests(unittest.TestCase):
    def test_same_beat_is_illegal(self) -> None:
        chart = Chart()
        chart.add(MouseClick("M1", 4.0, 0.3, 0.1))
        chart.add(MouseClick("M2", 4.0, 0.7, 0.1))
        issues = validate_chart(chart)
        self.assertTrue(any(i.kind == ISSUE_CLICK_SAME_BEAT for i in issues), [i.message for i in issues])

    def test_different_beats_are_fine(self) -> None:
        chart = Chart()
        chart.add(MouseClick("M1", 4.0, 0.3, 0.1))
        chart.add(MouseClick("M2", 5.0, 0.3, 0.1))
        self.assertEqual(validate_chart(chart), [])

    def test_region_rules_same_as_slide(self) -> None:
        chart = Chart()
        chart.add(MouseRegion("R1", 0.0, 8.0, BoundaryCurve(0.6, 0.6), BoundaryCurve(0.9, 0.9)))
        # 与空域相交 -> 合法
        chart.add(MouseClick("M1", 4.0, 0.5, 0.15))     # 0.35~0.65 与 0.6~0.9 相交
        self.assertEqual(validate_chart(chart), [])
        # 完全分离 -> 报错
        chart.add(MouseClick("M2", 5.0, 0.1, 0.05))     # 0.05~0.15
        issues = validate_chart(chart)
        self.assertTrue(any(i.kind == ISSUE_CLICK for i in issues), [i.message for i in issues])

    def test_no_region_is_fine(self) -> None:
        chart = Chart()
        chart.add(MouseClick("M1", 4.0, 0.1, 0.05))
        self.assertEqual(validate_chart(chart), [])

    def test_click_and_slide_same_beat_is_allowed(self) -> None:
        chart = Chart()
        chart.add(MouseClick("M1", 4.0, 0.2, 0.1))
        chart.add(MouseSlide("S1", 4.0, 0.2, 0.6))
        self.assertEqual(validate_chart(chart), [])


class ClickUndoEditTests(unittest.TestCase):
    def test_undo_redo_roundtrip(self) -> None:
        chart = Chart()
        document = Document(chart)
        before = chart.to_dict()
        document.add_note(MouseClick("M1", 4.0, 0.5, 0.2))
        after = chart.to_dict()
        self.assertNotEqual(before, after)
        document.undo()
        self.assertEqual(chart.to_dict(), before)
        document.redo()
        self.assertEqual(chart.to_dict(), after)

    def test_copy_paste_keeps_center(self) -> None:
        chart = Chart()
        chart.add(MouseClick("M1", 4.0, 0.25, 0.2))
        document = Document(chart)
        document.select_only(["M1"])
        document.copy_selection()
        created = document.paste_clipboard(20.0)
        self.assertEqual(len(created), 1)
        self.assertAlmostEqual(created[0].center, 0.25)
        self.assertAlmostEqual(created[0].radius, 0.2)
        self.assertAlmostEqual(created[0].beat, 20.0)

    def test_replace_note(self) -> None:
        chart = Chart()
        chart.add(MouseClick("M1", 4.0, 0.5, 0.2))
        document = Document(chart)
        data = chart.find("M1").to_dict()
        data["radius"] = 0.3
        document.replace_note("M1", data)
        self.assertAlmostEqual(chart.find("M1").radius, 0.3)
        document.undo()
        self.assertAlmostEqual(chart.find("M1").radius, 0.2)


class ClickToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover
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
        self.document = Document(self.chart)
        self.view = ViewState(self.chart)
        self.view.resize(WIDTH, HEIGHT)
        self.view.set_beat(0.0)
        self.scene = Scene(self.canvas)
        self.renderer = Renderer(self.scene, DARK)
        self.state = RenderState()
        self.messages: list[str] = []
        self.controller = ToolController(
            self.document, self.view, self.state, lambda a, b, c, d: None,
            lambda text: self.messages.append(text),
        )

    def tearDown(self) -> None:
        self.canvas.destroy()

    def click(self, x: float, y: float) -> None:
        self.controller.on_motion(x, y)
        self.controller.on_press(x, y, False, False)
        self.controller.on_release(x, y, False)

    def test_two_click_creation(self) -> None:
        """第一次点中心、第二次点旁边确定大小。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_CLICK)
        self.click(self.view.norm_to_x(0.5), self.view.beat_to_y(3.0))
        self.assertTrue(self.state.pending.hint)
        self.click(self.view.norm_to_x(0.8), self.view.beat_to_y(3.0))
        clicks = [n for n in self.chart.mouse_notes if isinstance(n, MouseClick)]
        self.assertEqual(len(clicks), 1)
        note = clicks[0]
        self.assertAlmostEqual(note.beat, 3.0)
        self.assertAlmostEqual(note.center, 0.5, places=3)
        self.assertAlmostEqual(note.radius, 0.3, places=3)
        self.assertFalse(self.state.pending.hint)

    def test_snaps_and_clamps_when_horizontal_grid_enabled(self) -> None:
        """启用横向辅助网格：吸附到网格线并夹进 0..1 合法区域内。"""
        self.chart.settings.horizontal_divisions = 8
        self.controller.set_tool(config.TOOL_MOUSE_CLICK)
        self.click(self.view.norm_to_x(0.26), self.view.beat_to_y(3.0))
        self.click(self.view.norm_to_x(1.2), self.view.beat_to_y(3.0))   # 出界点击
        note = [n for n in self.chart.mouse_notes if isinstance(n, MouseClick)][0]
        self.assertAlmostEqual(note.center, 0.25, places=6)
        self.assertLessEqual(note.x_end, 1.0 + 1e-9, "启用网格时必须夹进合法区域内")

    def test_out_of_bounds_allowed_when_grid_disabled(self) -> None:
        """未启用横向辅助网格：出界也按真实位置放置。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_CLICK)
        self.click(self.view.norm_to_x(0.1), self.view.beat_to_y(3.0))
        self.click(self.view.norm_to_x(-0.2), self.view.beat_to_y(3.0))
        note = [n for n in self.chart.mouse_notes if isinstance(n, MouseClick)][0]
        self.assertAlmostEqual(note.center, 0.1, places=3)
        self.assertLess(note.x_start, 0.0, "未启用网格时应允许超出合法区域")

    def test_slide_out_of_bounds_when_grid_disabled(self) -> None:
        """滑键同样遵守新规则：网格关闭时可以出界放置。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_SLIDE)
        self.click(self.view.norm_to_x(1.1), self.view.beat_to_y(3.0))
        self.click(self.view.norm_to_x(1.3), self.view.beat_to_y(3.0))
        slides = [n for n in self.chart.mouse_notes if isinstance(n, MouseSlide)]
        self.assertEqual(len(slides), 1)
        self.assertAlmostEqual(slides[0].x_start, 1.1, places=3)
        self.assertAlmostEqual(slides[0].x_end, 1.3, places=3)

    def test_render_style_and_fixed_diamond(self) -> None:
        """渲染：左右两条红色实线标范围（粗细同 6K 中心实线）+ 固定大小的红色实心菱形。"""
        small = MouseClick("S", 3.0, 0.3, 0.05)
        big = MouseClick("B", 5.0, 0.6, 0.25)
        self.chart.add(small)
        self.chart.add(big)
        self.view.set_beat(0.0)
        self.renderer.draw(self.chart, self.view, self.state)

        # 左右两条红色实线：粗细与 6K 按键中心实线相同，以判定时刻为垂直中点
        left = self.scene._items["click-left:S"]
        right = self.scene._items["click-right:S"]
        left_coords, right_coords = left[2][0], right[2][0]
        left_opts, right_opts = dict(left[2][1]), dict(right[2][1])
        self.assertEqual(left_opts["fill"], DARK.click_edge)
        self.assertEqual(right_opts["fill"], DARK.click_edge)
        self.assertEqual(int(left_opts["width"]), DARK.note_line_width)
        self.assertEqual(int(right_opts["width"]), DARK.note_line_width)
        self.assertAlmostEqual(left_coords[3] - left_coords[1], TAP_MIN_PX, places=6)
        self.assertAlmostEqual(
            (left_coords[1] + left_coords[3]) / 2, self.view.beat_to_y(3.0), places=6
        )
        # 不再有填充 / 白边 / 中间白实线
        self.assertNotIn("click:S", self.scene._items)
        self.assertNotIn("click-ring:S", self.scene._items)
        self.assertNotIn("click-line:S", self.scene._items)

        diamond_small = self.scene._items["click-diamond:S"][2][0]
        diamond_big = self.scene._items["click-diamond:B"][2][0]
        size_small = max(diamond_small[0::2]) - min(diamond_small[0::2])
        size_big = max(diamond_big[0::2]) - min(diamond_big[0::2])
        self.assertAlmostEqual(size_small, size_big, places=6, msg="菱形大小必须固定")
        self.assertAlmostEqual(size_small, 2 * config.CLICK_DIAMOND_RADIUS_PX, places=6)
        # 菱形：红色实心（不透明）、中心正好落在判定时刻上
        diamond_opts = dict(self.scene._items["click-diamond:S"][2][1])
        self.assertEqual(diamond_opts["fill"], DARK.click_diamond)
        self.assertNotIn("stipple", diamond_opts, "菱形必须不透明")
        self.assertAlmostEqual(
            (diamond_small[1] + diamond_small[5]) / 2, self.view.beat_to_y(3.0), places=6
        )
        # 两条实线的间距随按键尺寸变化
        left_big = self.scene._items["click-left:B"][2][0]
        self.assertLess(left_coords[0], left_big[0], "按键越大，左边界越靠左")

    def test_fallback_hit_path_and_box_selection(self) -> None:
        """兜底命中路径（无渲染缓存）也能选中长方形：点击落在框内、框外不命中。"""
        note = MouseClick("M1", 3.0, 0.5, 0.2)
        self.chart.add(note)
        self.view.set_beat(0.0)
        x = self.view.norm_to_x(0.5)
        y = self.view.beat_to_y(3.0)
        half = note.radius * self.view.layout.mouse_width

        inside = notes_at(self.chart, self.view, x, y - TAP_MIN_PX / 2)
        self.assertEqual([n.id for n in inside], ["M1"])
        outside = notes_at(self.chart, self.view, x + half + 20, y - TAP_MIN_PX / 2)
        self.assertEqual(outside, [])

        boxed = notes_in_box(self.chart, self.view, x - 5, y - TAP_MIN_PX - 2, x + 5, y - 1)
        self.assertEqual([n.id for n in boxed], ["M1"])


if __name__ == "__main__":
    unittest.main()

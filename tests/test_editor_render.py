"""编辑器画面与交互的关键行为（真实 Tk 画布 + 渲染器 + 工具状态机）。"""

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
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseClick, MouseRegion, MouseSlide
from rhythm_editor.ui import geometry
from rhythm_editor.ui.geometry import notes_at
from rhythm_editor.ui.interactions import ToolController
from rhythm_editor.ui.renderer import RenderState, Renderer
from rhythm_editor.ui.scene import Scene
from rhythm_editor.ui.theme import DARK
from rhythm_editor.ui.view import ViewState

WIDTH, HEIGHT = 1280, 800


class EditorRenderTests(unittest.TestCase):
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

    def _region_editor(self, beat_start: float, beat_end: float, left, right):
        self.region_args = (beat_start, beat_end, left, right)
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
        for key in ("bg", "lane:0", "lane:5", "judge", "mouse:legal", "judge-label"):
            self.assertIn(key, self.scene._items, f"缺少画布元素 {key}")
        note_items = [k for k in self.scene._items if k.startswith(("note:", "region:", "slide:"))]
        self.assertGreater(len(note_items), 3)
        self.assertGreater(self.scene.item_count, 25)
        # 第二帧不应重建对象（差分渲染）
        before = dict(self.scene._items)
        self.draw(demo)
        self.assertEqual(set(before), set(self.scene._items))

    def test_split_layout_geometry(self) -> None:
        """编辑器布局：左 6K、右鼠标平面，两个区域互不重叠但共用判定线。"""
        layout = self.view.layout
        self.assertTrue(self.view.split_layout)
        self.assertLess(layout.pane_6k[1], layout.pane_mouse[0])
        # 6K 只占约四分之一（扣掉中间间隔），其余给鼠标平面
        usable = layout.play_right - layout.play_left - config.PANE_GAP
        self.assertAlmostEqual(layout.pane_6k[1] - layout.pane_6k[0], usable * 0.25, delta=1.0)
        self.assertGreater(layout.pane_mouse[1] - layout.pane_mouse[0], usable * 0.7)
        # 鼠标平面在合法区域之外还有余量（0..1 之外也看得到）
        self.assertGreater(layout.mouse_left, layout.pane_mouse[0] + 5)
        self.assertLess(layout.mouse_right, layout.pane_mouse[1] - 5)
        self.assertAlmostEqual(self.view.norm_to_x(layout.norm_min), layout.pane_mouse[0], delta=1.0)
        self.assertAlmostEqual(self.view.norm_to_x(layout.norm_max), layout.pane_mouse[1], delta=1.0)
        self.assertIsNone(layout.lane_at(self.view.norm_to_x(0.5)))
        self.assertFalse(layout.in_6k_pane(self.view.norm_to_x(0.5)))
        self.assertTrue(layout.in_mouse_pane(self.view.norm_to_x(0.5)))
        self.draw()
        for key in ("pane6:bg", "panem:bg", "judge", "judge-mouse", "mouse:legal"):
            self.assertIn(key, self.scene._items)
        judge = self.scene._items["judge"][2][0]
        judge_mouse = self.scene._items["judge-mouse"][2][0]
        self.assertAlmostEqual(judge[1], judge_mouse[1], places=6)
        self.assertLessEqual(judge[2], layout.pane_6k[1] + 1)
        self.assertGreaterEqual(judge_mouse[0], layout.pane_mouse[0] - 1)

    def test_note_layer_order_matches_readability_spec(self) -> None:
        """图层自下而上：多轨长按 → 单轨长按 → 多轨单键 → 单轨单键 → 空域 → 滑键 → 点击按键。"""
        notes = [
            LaneNote("MH", 1, 4, 0.0, 4.0),          # 多轨长按
            LaneNote("SH", 2, 2, 0.0, 4.0),          # 单轨长按
            LaneNote("MT", 1, 3, 2.0),               # 多轨单键
            LaneNote("ST", 2, 2, 2.0),               # 单轨单键
            MouseRegion("RG", 0.0, 4.0, BoundaryCurve(0.0, 0.0), BoundaryCurve(1.0, 1.0)),
            MouseClick("CK", 2.0, 0.5, 0.2),
            MouseSlide("SL", 2.0, 0.2, 0.8),
        ]
        for note in notes:
            self.chart.add(note)
        self.view.set_beat(0.0)
        self.draw()
        order = list(self.canvas.find_all())

        def index(key: str) -> int:
            return order.index(self.scene._items[key][0])

        self.assertLess(index("note:MH"), index("note:SH"))
        self.assertLess(index("note:SH"), index("note:MT"))
        self.assertLess(index("note:MT"), index("note:ST"))
        self.assertLess(index("note:ST"), index("region:RG"))
        self.assertLess(index("region:RG"), index("slide:SL"))
        self.assertLess(index("slide:SL"), index("click-left:CK"))

    def test_region_boundary_beyond_display_is_clipped(self) -> None:
        """边界超出鼠标平面显示范围时：填充裁到面板边，**面板边不当作空域边界**。"""
        note = MouseRegion(
            "M1",
            0.0,
            4.0,
            BoundaryCurve(-0.8, -0.8, config.EASE_LINEAR),
            BoundaryCurve(0.6, 0.6, config.EASE_LINEAR),
        )
        self.chart.add(note)
        self.view.set_beat(0.0)
        self.draw()
        pane_left, pane_right = self.view.layout.pane_mouse
        fill = self.scene._items["region:M1"][2][0]
        xs = fill[0::2]
        self.assertGreaterEqual(min(xs), pane_left - 0.5)
        self.assertAlmostEqual(min(xs), pane_left, delta=1.0, msg="填充应被裁到面板边")
        # 左边界整体在显示范围之外：不画（不能在面板边缘画一条假边界）
        self.assertNotIn("region-left:M1", self.scene._items)
        # 右边界在范围内：照常画
        self.assertIn("region-right:M1", self.scene._items)
        right_xs = self.scene._items["region-right:M1"][2][0][0::2]
        self.assertLess(max(right_xs), pane_right + 1)

    def test_slide_beyond_display_is_clipped(self) -> None:
        """滑键起点越界、终点在内：方框与箭头都裁到显示范围，箭头头仍落在真实终点。"""
        note = MouseSlide("S1", 3.0, -0.8, 0.4)
        self.chart.add(note)
        self.view.set_beat(0.0)
        self.draw()
        pane_left, _pane_right = self.view.layout.pane_mouse
        box = self.scene._items["slide:S1"][2][0]
        self.assertGreaterEqual(min(box[0::2]), pane_left - 0.5)
        self.assertAlmostEqual(min(box[0::2]), pane_left, delta=1.0, msg="方框应裁到面板边")
        arrow = self.scene._items["slide-arrow:S1"]
        arrow_coords, arrow_opts = arrow[2][0], dict(arrow[2][1])
        self.assertAlmostEqual(
            min(arrow_coords[0], arrow_coords[2]), pane_left, delta=1.0, msg="箭头尾部裁到面板边"
        )
        self.assertAlmostEqual(
            max(arrow_coords[0], arrow_coords[2]),
            self.view.norm_to_x(0.4),
            delta=1.0,
            msg="箭头头仍应落在真实终点上",
        )
        self.assertEqual(arrow_opts.get("arrow"), "last")

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

    def test_place_mouse_click_two_clicks(self) -> None:
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_CLICK)
        self.click(self.view.norm_to_x(0.5), self.y_of_beat(3.0))
        self.assertTrue(self.state.pending.hint)
        self.click(self.view.norm_to_x(0.8), self.y_of_beat(3.0))
        clicks = [note for note in self.chart.mouse_notes if isinstance(note, MouseClick)]
        self.assertEqual(len(clicks), 1)
        self.assertAlmostEqual(clicks[0].center, 0.5, places=3)
        self.assertAlmostEqual(clicks[0].radius, 0.3, places=3)
        self.assertFalse(self.state.pending.hint)

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

    def test_place_hold_sets_end_lane_and_time(self) -> None:
        """长按（含多轨）：起点定轨道与时间，第二次点击的横坐标定结束轨道、纵坐标定结束时间。"""
        self.controller.set_tool(config.TOOL_LANE_HOLD)
        self.click(self.view.layout.lane_center(1), self.y_of_beat(4.0))
        self.assertTrue(self.state.pending.hint)
        self.click(self.view.layout.lane_center(4), self.y_of_beat(6.5))
        notes = self.document.chart.lane_notes
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertEqual((note.lane_start, note.lane_end), (1, 4))
        self.assertAlmostEqual(note.beat_start, 4.0)
        self.assertAlmostEqual(note.beat_end, 6.5)
        self.assertTrue(note.is_hold)
        self.assertTrue(note.is_multi)
        # 结束时间早于起点时自动补一格
        self.click(self.view.layout.lane_center(3), self.y_of_beat(4.0))
        self.click(self.view.layout.lane_center(3), self.y_of_beat(3.0))
        fixed = self.document.chart.lane_notes[1]
        self.assertAlmostEqual(fixed.beat_end - fixed.beat_start, self.view.grid_step())

    def test_place_region_uses_dialog_result(self) -> None:
        """空域新流程：先两次点击定时间，再四次点击定左右边界首尾，最后弹窗选线型。"""
        self.chart.settings.horizontal_divisions = 0
        self.region_result = {
            "beat_start": 4.0,
            "beat_end": 6.0,
            "left": {"start": 0.1, "end": 0.3, "ease": config.EASE_OUT},
            "right": {"start": 0.9, "end": 0.7, "ease": config.EASE_IN},
        }
        self.controller.set_tool(config.TOOL_MOUSE_REGION)
        x = self.view.norm_to_x
        # 1) 开始时间 2) 结束时间（与原来的行为一致）
        self.click(self.view.norm_to_x(0.5), self.y_of_beat(4.0))
        self.assertTrue(self.state.pending.hint)
        self.click(self.view.norm_to_x(0.5), self.y_of_beat(6.0))
        # 3) 左边界起点 4) 左边界终点 5) 右边界起点 6) 右边界终点
        self.click(x(0.2), self.y_of_beat(5.0))
        self.click(x(0.35), self.y_of_beat(5.0))
        self.click(x(0.8), self.y_of_beat(5.0))
        self.click(x(0.65), self.y_of_beat(5.0))
        # 弹窗收到的四个位置
        for got, expected in zip(self.region_args[2], (0.2, 0.35)):
            self.assertAlmostEqual(got, expected, places=6)
        for got, expected in zip(self.region_args[3], (0.8, 0.65)):
            self.assertAlmostEqual(got, expected, places=6)
        regions = [n for n in self.document.chart.mouse_notes if isinstance(n, MouseRegion)]
        self.assertEqual(len(regions), 1)
        self.assertAlmostEqual(regions[0].left.start, 0.1)
        self.assertEqual(regions[0].right.ease, config.EASE_IN)
        self.assertFalse(self.state.pending.hint)

    def test_notes_hidden_after_judgment_time(self) -> None:
        """显示规则：判定线越过判定时刻后不再显示（长按 / 区域按结束时刻）。"""
        from rhythm_editor.ui.geometry import lane_note_bounds, notes_at

        tap = LaneNote("L1", 1, 1, 4.0)
        hold = LaneNote("L2", 2, 2, 4.0, 6.0)
        region = MouseRegion("M1", 4.0, 6.0, BoundaryCurve(0.0, 0.0), BoundaryCurve(1.0, 1.0))
        slide = MouseSlide("M2", 4.0, 0.2, 0.6)
        for note in (tap, hold, region, slide):
            self.chart.add(note)

        def key_of(note) -> str:
            if isinstance(note, LaneNote):
                return f"note:{note.id}"
            if isinstance(note, MouseRegion):
                return f"region:{note.id}"
            return f"slide:{note.id}"

        # 判定之前：四个物件都在
        self.view.set_beat(4.0)
        self.draw()
        for note in (tap, hold, region, slide):
            self.assertIn(key_of(note), self.scene._items)

        # 越过 4 拍：单点物件消失，长按 / 空域（结束于 6 拍）仍在
        self.view.set_beat(4.25)
        self.draw()
        self.assertNotIn("note:L1", self.scene._items)
        self.assertNotIn("slide:M2", self.scene._items)
        self.assertIn("note:L2", self.scene._items)
        self.assertIn("region:M1", self.scene._items)

        # 越过 6 拍：全部不再显示
        self.view.set_beat(6.25)
        self.draw()
        for key in ("note:L1", "note:L2", "region:M1", "slide:M2"):
            self.assertNotIn(key, self.scene._items)

        # 已经判过的物件也不能再被点选
        bounds = lane_note_bounds(tap, self.chart, self.view)
        hits = notes_at(
            self.chart,
            self.view,
            (bounds[0] + bounds[2]) / 2,
            (bounds[1] + bounds[3]) / 2,
        )
        self.assertFalse(any(note.id == "L1" for note in hits))

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
        # 单键以判定时刻为垂直中点：白色横线压在判定线上
        line_coords = self.scene._items["note-line:L1"][2][0]
        self.assertAlmostEqual(line_coords[1], self.view.layout.judgment_y, places=6)
        self.assertAlmostEqual(
            (coords[1] + coords[3]) / 2, self.view.layout.judgment_y, places=6
        )
        hits = notes_at(
            self.chart,
            self.view,
            (coords[0] + coords[2]) / 2,
            (coords[1] + coords[3]) / 2,
        )
        self.assertTrue(any(note.id == "L1" for note in hits))


class HitCacheTests(unittest.TestCase):
    """命中测试复用渲染缓存：缓存里只有屏幕内的物件，且不再全量算几何。"""

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
        # 1000 个远处物件 + 少量屏内物件
        for index in range(1000):
            beat = 200.0 + index * 0.5
            self.chart.add(LaneNote(f"L{index}", index % config.LANE_COUNT, index % config.LANE_COUNT, beat))
        self.near = LaneNote("NEAR", 2, 2, 2.0)
        self.chart.add(self.near)
        self.chart.add(
            MouseRegion("M1", 1.0, 3.0, BoundaryCurve(0.1, 0.1), BoundaryCurve(0.9, 0.9))
        )
        self.document = Document(self.chart)
        self.view = ViewState(self.chart)
        self.view.resize(WIDTH, HEIGHT)
        self.view.set_beat(0.0)
        self.scene = Scene(self.canvas)
        self.renderer = Renderer(self.scene, DARK)
        self.state = RenderState()

    def tearDown(self) -> None:
        self.canvas.destroy()

    def draw(self) -> None:
        self.renderer.draw(self.chart, self.view, self.state)

    def test_notes_at_uses_cache_without_touching_all_notes(self) -> None:
        self.draw()
        calls = {"count": 0}
        original = geometry.note_hit

        def counting(note, chart, view, x, y):
            calls["count"] += 1
            return original(note, chart, view, x, y)

        geometry.note_hit = counting  # type: ignore[assignment]
        try:
            bounds = geometry.lane_note_bounds(self.near, self.chart, self.view)
            hits = notes_at(
                self.chart,
                self.view,
                (bounds[0] + bounds[2]) / 2,
                (bounds[1] + bounds[3]) / 2,
                cache=self.state.hit_cache,
            )
        finally:
            geometry.note_hit = original  # type: ignore[assignment]
        self.assertTrue(any(note.id == "NEAR" for note in hits))
        self.assertEqual(calls["count"], 0, "走缓存时不应再对整张谱面做几何计算")


if __name__ == "__main__":
    unittest.main()

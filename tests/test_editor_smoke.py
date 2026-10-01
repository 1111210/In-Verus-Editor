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
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseClick, MouseRegion, MouseSlide
from rhythm_editor.ui.interactions import ToolController
from rhythm_editor.ui.geometry import TAP_MIN_PX, notes_at
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

    def test_huge_grid_divisions_are_thinned(self) -> None:
        """细分没有上限：特别密时自动抽稀 / 退化成拍线，图元数量保持有限。"""
        self.chart.settings.grid_division = 100000
        self.chart.settings.horizontal_divisions = 10000
        self.view.set_beat(0.0)
        self.draw()
        grid = [k for k in self.scene._items if k.startswith(("grid6:", "gridm:"))]
        hgrid = [k for k in self.scene._items if k.startswith("hgrid:")]
        self.assertGreater(len(grid), 0)
        self.assertLess(len(grid), 500, "时间网格应退化成拍线 / 小节线")
        self.assertGreater(len(hgrid), 2)
        self.assertLess(len(hgrid), 300, "横向网格应抽稀到有限条")
        # 右边界那条线始终在
        self.assertIn("hgrid:10000", self.scene._items)

    def test_grid_labels_show_bar_and_beat_numbers(self) -> None:
        """网格旁要同时标注小节号与绝对拍号（方便照抄填写数据）。"""
        self.view.set_beat(8.0)
        self.draw()
        texts = [
            dict(options).get("text", "")
            for _item_id, _kind, spec in self.scene._items.values()
            for options in (spec[1],)
        ]
        bar_labels = [t for t in texts if t.startswith("小节 ")]
        self.assertTrue(bar_labels, "应该有小节标注")
        self.assertTrue(any("第 8 拍" in t for t in bar_labels), f"小节标注要带拍号：{bar_labels}")
        self.assertTrue(any(t == "第 9 拍" or t == "第 7 拍" for t in texts), "拍线旁应有绝对拍号")

    def test_judgment_line_is_grid_aligned(self) -> None:
        self.chart.settings.grid_division = 4
        self.view.set_beat(3.4)
        self.assertAlmostEqual(self.view.current_beat, 3.5)
        self.draw()
        judge = self.scene._items["judge"][2][0]
        self.assertAlmostEqual(judge[1], self.view.layout.judgment_y, places=3)
        # 判定线上的拍与网格完全重合：该拍的网格线 y 与判定线 y 相同
        self.assertAlmostEqual(self.view.beat_to_y(self.view.current_beat), self.view.layout.judgment_y, places=6)

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

    def test_drag_selected_note_moves_time_and_lane(self) -> None:
        """拖动物件：直接拖动选中的按键改时间与轨道，松手才记一条撤销。"""
        note = LaneNote("D1", 0, 0, 4.0)
        self.chart.add(note)
        self.controller.set_tool(config.TOOL_SELECT)
        lane0 = self.view.layout.lane_center(0)
        lane2 = self.view.layout.lane_center(2)
        self.click(lane0, self.y_of_beat(4.0))
        self.assertEqual(self.document.selection, {"D1"})
        # 按住已选中的物件往右下拖：+2 拍、跨 2 条轨道
        self.controller.on_press(lane0, self.y_of_beat(4.0), False, False)
        self.controller.on_motion(lane2, self.y_of_beat(6.0))
        self.controller.on_release(lane2, self.y_of_beat(6.0), False)
        moved = self.chart.find("D1")
        self.assertAlmostEqual(moved.beat_start, 6.0)
        self.assertEqual((moved.lane_start, moved.lane_end), (2, 2))
        # 撤销 / 重做都能还原
        self.document.undo()
        restored = self.chart.find("D1")
        self.assertAlmostEqual(restored.beat_start, 4.0)
        self.assertEqual((restored.lane_start, restored.lane_end), (0, 0))
        self.document.redo()
        again = self.chart.find("D1")
        self.assertAlmostEqual(again.beat_start, 6.0)
        self.assertEqual((again.lane_start, again.lane_end), (2, 2))

    def test_drag_snaps_mouse_anchor_to_grid(self) -> None:
        """拖动鼠标物件时按自身锚点吸附：滑键起点 / 点击中心 / 空域起点中点。"""
        from rhythm_editor.model.notes import MouseClick

        self.chart.settings.grid_division = 4
        self.chart.settings.horizontal_divisions = 8
        self.chart.add(MouseSlide("S1", 5.0, 0.30, 0.55))
        self.chart.add(MouseClick("C1", 7.0, 0.30, 0.10))
        self.chart.add(
            MouseRegion("R1", 9.0, 10.0, BoundaryCurve(0.20, 0.20), BoundaryCurve(0.40, 0.60))
        )
        self.controller.set_tool(config.TOOL_SELECT)
        self.view.set_beat(4.0, snap=False)

        def drag(note_id: str, beat: float, dx: float) -> None:
            self.document.select_only([note_id])
            x0 = self.view.norm_to_x(0.30)
            y0 = self.view.beat_to_y(beat)
            x1 = self.view.norm_to_x(0.30 + dx)
            self.controller.on_press(x0, y0, False, False)
            self.controller.on_motion(x1, y0)
            self.controller.on_release(x1, y0, False)

        drag("S1", 5.0, 0.02)          # 起点 0.32 -> 吸附到 0.375
        slide = self.chart.find("S1")
        self.assertAlmostEqual(slide.x_start, 0.375, places=6)
        self.assertAlmostEqual(slide.x_end - slide.x_start, 0.25, places=6, msg="长度不变")

        drag("C1", 7.0, 0.02)          # 中心 0.32 -> 吸附到 0.375
        click = self.chart.find("C1")
        self.assertAlmostEqual(click.center, 0.375, places=6)
        self.assertAlmostEqual(click.radius, 0.10, places=6, msg="半径不变")

        drag("R1", 9.5, 0.02)          # 起点中点 0.30 -> 吸附到 0.375
        region = self.chart.find("R1")
        self.assertAlmostEqual(
            (region.left.start + region.right.start) / 2.0, 0.375, places=6
        )
        self.assertAlmostEqual(
            region.right.start - region.left.start, 0.20, places=6, msg="宽度不变"
        )

    def test_overlay_demo_is_one_third_and_centered(self) -> None:
        """演示模式只占窗口宽度的 1/3，并且水平居中。"""
        self.view.set_layout_mode(config.LAYOUT_OVERLAY)
        layout = self.view.layout
        low, high = layout.overlay_span
        self.assertAlmostEqual(
            high - low, layout.play_width * config.OVERLAY_WIDTH_RATIO, delta=1.0
        )
        self.assertAlmostEqual(low - layout.play_left, layout.play_right - high, delta=1.0)
        # 轨道都落在这条 1/3 的区域里
        self.assertAlmostEqual(layout.lane_rects[0][0], low, delta=1.0)
        self.assertAlmostEqual(layout.lane_rects[-1][1], high, delta=1.0)
        self.draw()
        self.assertIn("overlay:bg", self.scene._items)
        box = self.scene._items["overlay:bg"][2][0]
        self.assertAlmostEqual(box[0], low, delta=0.01)
        self.assertAlmostEqual(box[2], high, delta=0.01)
        judge = self.scene._items["judge"][2][0]
        self.assertAlmostEqual(judge[0], low, delta=1.0)
        self.assertAlmostEqual(judge[2], high, delta=1.0)

    def test_divider_drag_resizes_both_panes(self) -> None:
        """拖动分界线：两栏宽度实时变化，并夹在最小宽度内。"""
        layout = self.view.layout
        before_6k = layout.pane_6k[1] - layout.pane_6k[0]
        before_mouse = layout.pane_mouse[1] - layout.pane_mouse[0]
        divider = layout.divider_x

        self.assertTrue(self.view.divider_at(divider))
        self.assertTrue(self.view.divider_at(divider + config.DIVIDER_GRAB_PX - 1))
        self.assertFalse(self.view.divider_at(divider + config.DIVIDER_GRAB_PX + 5))

        # 往右拖：6K 变宽、鼠标平面变窄
        self.view.set_split_ratio(layout.ratio_from_divider(divider + 120))
        self.assertGreater(layout.pane_6k[1] - layout.pane_6k[0], before_6k)
        self.assertLess(layout.pane_mouse[1] - layout.pane_mouse[0], before_mouse)
        self.assertAlmostEqual(
            layout.pane_6k[1] - layout.pane_6k[0] + config.PANE_GAP
            + (layout.pane_mouse[1] - layout.pane_mouse[0]),
            layout.play_width,
            delta=1.0,
        )

        # 极端拖动会被夹住：两边都不小于最小宽度
        self.view.set_split_ratio(layout.ratio_from_divider(layout.play_right + 500))
        self.assertGreaterEqual(layout.pane_mouse[1] - layout.pane_mouse[0], config.SPLIT_MIN_PANE_PX - 1)
        self.view.set_split_ratio(layout.ratio_from_divider(layout.play_left - 500))
        self.assertGreaterEqual(layout.pane_6k[1] - layout.pane_6k[0], config.SPLIT_MIN_PANE_PX - 1)

        # 画布上那根分界线跟着走
        self.draw()
        line = self.scene._items["pane:divider"][2][0]
        self.assertAlmostEqual(line[0], layout.divider_x, places=6)

    def test_divider_drag_does_not_place_notes(self) -> None:
        """抓分界线拖拽时不会顺手放下物件，也不会选中东西。"""
        layout = self.view.layout
        self.controller.set_tool(config.TOOL_LANE_TAP)
        self.click(layout.divider_x, self.y_of_beat(3.0))
        self.assertEqual(len(self.chart.lane_notes), 0)
        self.assertFalse(self.state.pending.hint)
        # 叠加（演示）模式下没有分界线可拖
        self.view.set_layout_mode(config.LAYOUT_OVERLAY)
        self.assertFalse(self.view.divider_at(layout.divider_x))

    def test_region_outside_legal_area_still_visible(self) -> None:
        """超出 0..1 的区域边界要画得出来（鼠标平面留了溢出余量）。"""
        note = MouseRegion(
            "M1",
            0.0,
            4.0,
            BoundaryCurve(-0.3, -0.2, config.EASE_LINEAR),
            BoundaryCurve(1.4, 1.3, config.EASE_LINEAR),
        )
        self.chart.add(note)
        self.view.set_beat(0.0)
        self.draw()
        self.assertIn("region:M1", self.scene._items)
        coords = self.scene._items["region:M1"][2][0]
        xs = coords[0::2]
        layout = self.view.layout
        self.assertGreaterEqual(min(xs), layout.pane_mouse[0] - 1)
        self.assertLessEqual(max(xs), layout.pane_mouse[1] + 1)
        # 合法区域左右界仍在 0 / 1 对应的位置上
        self.assertAlmostEqual(self.view.norm_to_x(0.0), layout.mouse_left, places=6)
        self.assertAlmostEqual(self.view.norm_to_x(1.0), layout.mouse_right, places=6)

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

    def test_slide_arrow_clipped_without_fake_head(self) -> None:
        """箭头按真实几何裁剪：终点在外时只画可见线段，不把箭头头挪到面板边。"""
        right = MouseSlide("SR", 2.0, 0.2, 5.0)     # 起点在内、终点极远
        slight = MouseSlide("SE", 2.5, 1.40, 1.47)  # 终点略微出界
        left = MouseSlide("SL", 3.0, -5.0, 0.4)     # 起点极远、终点在内
        inside = MouseSlide("SI", 4.0, 0.2, 0.6)    # 完全在显示范围内
        self.chart.add(right)
        self.chart.add(slight)
        self.chart.add(left)
        self.chart.add(inside)
        self.view.set_beat(0.0)
        self.draw()
        pane_left, pane_right = self.view.layout.pane_mouse
        # 终点在外（极远或略微）：画出可见的箭头线段，但不带头（不能在面板边伪造箭头头）
        for note_id in ("SR", "SE"):
            box = self.scene._items[f"slide:{note_id}"][2][0]
            self.assertGreaterEqual(min(box[0::2]), pane_left - 0.5)
            self.assertLessEqual(max(box[0::2]), pane_right + 0.5)
            arrow = self.scene._items[f"slide-arrow:{note_id}"]
            coords, opts = arrow[2][0], dict(arrow[2][1])
            self.assertAlmostEqual(
                max(coords[0], coords[2]), pane_right, delta=1.0, msg="可见线段应画到面板边"
            )
            self.assertEqual(opts.get("arrow"), "none", "终点在外时不应画箭头头")
        # 起点在外、终点在内：箭头正常绘制，箭头头落在真实终点
        arrow = self.scene._items["slide-arrow:SL"]
        coords, opts = arrow[2][0], dict(arrow[2][1])
        self.assertAlmostEqual(min(coords[0], coords[2]), pane_left, delta=1.0)
        self.assertAlmostEqual(max(coords[0], coords[2]), self.view.norm_to_x(0.4), delta=1.0)
        self.assertEqual(opts.get("arrow"), "last")
        # 完全在显示范围内的滑键：箭头照常画
        arrow = self.scene._items["slide-arrow:SI"][2][0]
        self.assertGreaterEqual(min(arrow[0], arrow[2]), pane_left)
        self.assertLessEqual(max(arrow[0], arrow[2]), pane_right)
        self.assertEqual(dict(self.scene._items["slide-arrow:SI"][2][1]).get("arrow"), "last")

    def test_click_beyond_display_does_not_cover_k6(self) -> None:
        """点击按键超出显示范围时裁到面板内，不画进左侧 6K 区域。"""
        left_over = MouseClick("C1", 3.0, -0.25, 0.4)   # 左边界超出显示范围
        right_over = MouseClick("C2", 5.0, 1.25, 0.4)   # 右边界超出显示范围
        self.chart.add(left_over)
        self.chart.add(right_over)
        self.view.set_beat(0.0)
        self.draw()
        pane_left, pane_right = self.view.layout.pane_mouse
        for note_id in ("C1", "C2"):
            for kind in ("click-left", "click-right", "click-line", "click-diamond"):
                key = f"{kind}:{note_id}"
                if key not in self.scene._items:
                    continue
                xs = self.scene._items[key][2][0][0::2]
                self.assertGreaterEqual(
                    min(xs), pane_left - 0.5, f"{key} 画进了 6K 区域"
                )
                self.assertLessEqual(max(xs), pane_right + 0.5, f"{key} 超出鼠标平面")
        # 超出的一侧不画；范围内的一侧照常画
        self.assertNotIn("click-left:C1", self.scene._items)
        self.assertIn("click-right:C1", self.scene._items)
        self.assertIn("click-left:C2", self.scene._items)
        self.assertNotIn("click-right:C2", self.scene._items)

    def test_clipped_mouse_notes_are_not_hittable_outside_pane(self) -> None:
        """被裁掉的部分不参与命中：6K 区域点不到，可见部分照常命中。"""
        from rhythm_editor.ui.geometry import notes_at

        note = MouseClick("C1", 3.0, -0.25, 0.4)
        self.chart.add(note)
        self.view.set_beat(0.0)
        self.draw()
        pane_left, _pane_right = self.view.layout.pane_mouse
        y = self.y_of_beat(3.0)
        outside = notes_at(
            self.chart, self.view, pane_left - 30.0, y, cache=self.state.hit_cache
        )
        self.assertNotIn(note, outside, "被裁掉的部分不该能点到")
        inside = notes_at(
            self.chart, self.view, pane_left + 30.0, y, cache=self.state.hit_cache
        )
        self.assertIn(note, inside, "可见部分应当可以点选")

    def test_region_draws_only_left_and_right_edges(self) -> None:
        """蓝色空域只绘制左右两条边界线，不画上下封口横线。"""
        note = MouseRegion("M1", 4.0, 8.0, BoundaryCurve(0.2, 0.1), BoundaryCurve(0.8, 0.9))
        self.chart.add(note)
        self.view.set_beat(4.0)
        self.draw()
        self.assertIn("region:M1", self.scene._items)
        self.assertIn("region-left:M1", self.scene._items)
        self.assertIn("region-right:M1", self.scene._items)
        for cap in ("region-cap0:M1", "region-cap1:M1"):
            self.assertNotIn(cap, self.scene._items)

    def test_offscreen_regions_skip_boundary_computation(self) -> None:
        """回归：屏幕外的空域不计算边界形状（预判直接跳过采样）。"""
        calls = {"count": 0}
        original = self.renderer._region_points

        def counting(note, chart, view, samples=40):
            calls["count"] += 1
            return original(note, chart, view, samples)

        self.renderer._region_points = counting  # type: ignore[assignment]
        try:
            # 很远的空域（判定线还在第 0 拍）
            for index in range(30):
                beat = 500.0 + index * 4.0
                self.chart.add(
                    MouseRegion(
                        f"M{index}",
                        beat,
                        beat + 2.0,
                        BoundaryCurve(0.0, 0.0),
                        BoundaryCurve(1.0, 1.0),
                    )
                )
            # 一个就在屏幕里的空域
            self.chart.add(
                MouseRegion("MV", 1.0, 3.0, BoundaryCurve(0.1, 0.1), BoundaryCurve(0.9, 0.9))
            )
            self.view.set_beat(0.0)
            self.draw()
            self.assertEqual(calls["count"], 1, "只有屏幕内的空域才需要计算边界形状")
            self.assertIn("region:MV", self.scene._items)
            self.assertNotIn("region:M0", self.scene._items)
        finally:
            self.renderer._region_points = original  # type: ignore[assignment]

    def test_region_pending_preview_has_only_side_edges(self) -> None:
        """放置预览同样只画左右边界线。"""
        self.controller.set_tool(config.TOOL_MOUSE_REGION)
        self.click(self.view.norm_to_x(0.5), self.y_of_beat(2.0))
        self.draw()
        self.assertIn("pending:region", self.scene._items)
        self.assertIn("pending:region-left", self.scene._items)
        self.assertIn("pending:region-right", self.scene._items)

    def test_overlay_layout_puts_mouse_area_on_middle_lanes(self) -> None:
        self.view.set_layout_mode(config.LAYOUT_OVERLAY)
        layout = self.view.layout
        self.assertFalse(self.view.split_layout)
        self.assertAlmostEqual(layout.mouse_left, layout.lane_rects[1][0], places=6)
        self.assertAlmostEqual(layout.mouse_right, layout.lane_rects[4][1], places=6)
        self.draw()
        self.assertIn("judge", self.scene._items)
        self.assertIn("judge-mouse", self.scene._items)
        self.view.set_layout_mode(config.LAYOUT_SPLIT)

    def test_layer_order_survives_layout_switch(self) -> None:
        """回归测试：布局切换会重建区域底色对象，不能让它们盖住物件。"""
        self.controller.set_tool(config.TOOL_LANE_TAP)
        self.click(self.view.layout.lane_center(1), self.y_of_beat(2.0))
        note_id = self.document.chart.lane_notes[0].id
        self.draw()
        self.view.set_layout_mode(config.LAYOUT_OVERLAY)
        self.draw()
        self.view.set_layout_mode(config.LAYOUT_SPLIT)
        self.draw()
        order = list(self.canvas.find_all())
        pane_bg = self.scene._items["pane6:bg"][0]
        note_item = self.scene._items[f"note:{note_id}"][0]
        self.assertGreater(order.index(note_item), order.index(pane_bg), "物件必须画在区域底色之上")

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

    def test_tap_note_style(self) -> None:
        """单键 / 多轨单键：实心蓝色矩形 + 白边，白色横线压在节拍线上。"""
        self.chart.add(LaneNote("T1", 2, 2, 2.0))
        self.view.set_beat(0.0)
        self.draw()
        outer = dict(self.scene._items["note:T1"][2][1])
        line = dict(self.scene._items["note-line:T1"][2][1])
        self.assertEqual(outer["outline"], DARK.note_white)
        self.assertEqual(outer["fill"], DARK.note_blue, "实心蓝色矩形打底")
        self.assertEqual(line["fill"], DARK.note_white)
        self.assertEqual(int(line["width"]), DARK.note_line_width)
        outer_coords = self.scene._items["note:T1"][2][0]
        line_coords = self.scene._items["note-line:T1"][2][0]
        # 单键更薄，且以判定时刻为垂直中点：白色横线正好落在节拍线上
        self.assertAlmostEqual(outer_coords[3] - outer_coords[1], TAP_MIN_PX, delta=0.01)
        self.assertAlmostEqual(
            (outer_coords[1] + outer_coords[3]) / 2, self.view.beat_to_y(2.0), places=6
        )
        self.assertAlmostEqual(line_coords[1], self.view.beat_to_y(2.0), places=6)

    def test_hold_note_style_and_constant_center_line(self) -> None:
        """长按：实心蓝色矩形 + 白色竖线（粗细固定），开头套用单键样式。"""
        self.chart.add(LaneNote("H1", 1, 1, 2.0, 4.0))     # 单轨长按
        self.chart.add(LaneNote("H2", 0, 5, 2.0, 4.0))     # 跨 6 轨的长按
        self.view.set_beat(0.0)
        self.draw()
        widths = set()
        for note_id in ("H1", "H2"):
            body = dict(self.scene._items[f"note-body:{note_id}"][2][1])
            center = dict(self.scene._items[f"note:{note_id}"][2][1])
            head = dict(self.scene._items[f"note-head:{note_id}"][2][1])
            head_body = dict(self.scene._items[f"note-head-body:{note_id}"][2][1])
            self.assertEqual(body["fill"], DARK.note_blue, "长按主体是实心蓝色矩形")
            self.assertEqual(body["outline"], DARK.note_white)
            self.assertEqual(center["fill"], DARK.note_white)
            self.assertEqual(head["fill"], DARK.note_white)
            self.assertEqual(int(head["width"]), DARK.note_line_width)
            # 开头与单键同款：实心蓝矩形 + 白边
            self.assertEqual(head_body["fill"], DARK.note_blue)
            self.assertEqual(head_body["outline"], DARK.note_white)
            head_coords = self.scene._items[f"note-head-body:{note_id}"][2][0]
            self.assertAlmostEqual(head_coords[3] - head_coords[1], TAP_MIN_PX, delta=0.01)
            widths.add(int(center["width"]))
            # 开头横线位于起始拍的网格线（判定边）上
            line_coords = self.scene._items[f"note-head:{note_id}"][2][0]
            self.assertAlmostEqual(line_coords[1], self.view.beat_to_y(2.0), places=6)
        self.assertEqual(widths, {DARK.note_line_width}, "白竖线粗细应与轨道数无关")

    def test_multi_holds_stack_by_lane_span(self) -> None:
        """同为多轨长按时，跨轨道越多越先画（越靠下层）。"""
        wide = LaneNote("HW", 0, 5, 2.0, 4.0)
        narrow = LaneNote("HN", 2, 3, 2.0, 4.0)
        taps = LaneNote("TM", 1, 4, 2.0)
        self.chart.add(narrow)
        self.chart.add(wide)
        self.chart.add(taps)
        self.view.set_beat(0.0)
        self.draw()
        order = list(self.canvas.find_all())
        index = lambda key: order.index(self.scene._items[key][0])  # noqa: E731
        self.assertLess(index("note:HW"), index("note:HN"), "跨得多的长按应在下层")
        # 多轨单键同样按跨度排序（同为多轨物件层）
        self.assertLess(index("note:HN"), index("note:TM"))

    def test_hit_priority_follows_layer_order(self) -> None:
        """点击命中的优先顺序与图层一致：点击按键 > 滑键 > 空域 > 单轨单键 > 多轨单键 > 长按。"""
        hold_multi = LaneNote("MH", 1, 4, 0.0, 4.0)
        hold_single = LaneNote("SH", 2, 2, 0.0, 4.0)
        tap_multi = LaneNote("MT", 1, 3, 2.0)
        tap_single = LaneNote("ST", 2, 2, 2.0)
        region = MouseRegion("RG", 0.0, 4.0, BoundaryCurve(0.0, 0.0), BoundaryCurve(1.0, 1.0))
        click = MouseClick("CK", 2.0, 0.2, 0.2)
        slide = MouseSlide("SL", 2.0, 0.2, 0.8)
        for note in (hold_multi, hold_single, tap_multi, tap_single, region, click, slide):
            self.chart.add(note)
        self.view.set_beat(0.0)
        # 6K 区域：单轨单键最靠上
        lane_x = self.view.layout.lane_center(2)
        lane_hits = notes_at(self.chart, self.view, lane_x, self.y_of_beat(2.0))
        ids = [note.id for note in lane_hits]
        self.assertIn("ST", ids[:2])
        self.assertLess(ids.index("ST"), ids.index("SH"))
        self.assertLess(ids.index("ST"), ids.index("MH"))
        # 鼠标平面：点击按键最靠上，其次滑键，再到空域
        mouse_hits = notes_at(
            self.chart, self.view, self.view.norm_to_x(0.2), self.y_of_beat(2.0)
        )
        mouse_ids = [note.id for note in mouse_hits]
        self.assertEqual(mouse_ids[:3], ["CK", "SL", "RG"])

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

    def test_show_past_notes_option(self) -> None:
        """「显示更早的物件」：已判过的物件继续画在判定线下方，仍只画可见区域。"""
        from rhythm_editor.ui.geometry import notes_at

        tap = LaneNote("P1", 1, 1, 3.5)
        far = LaneNote("P2", 2, 2, -20.0)          # 远在可见窗口之外
        self.chart.add(tap)
        self.chart.add(far)
        self.view.set_beat(4.0)
        self.draw()
        self.assertNotIn("note:P1", self.scene._items)
        self.assertNotIn("note:P2", self.scene._items)

        self.chart.settings.show_past_notes = True
        self.draw()
        self.assertIn("note:P1", self.scene._items)
        coords = self.scene._items["note:P1"][2][0]
        center_y = (coords[1] + coords[3]) / 2.0
        self.assertGreater(center_y, self.view.layout.judgment_y, "应画在判定线下方")
        self.assertLessEqual(center_y, self.view.layout.play_bottom + 1, "仍在可见区域内")
        self.assertNotIn("note:P2", self.scene._items, "可见窗口之外的物件仍然不画")

        # 命中测试与渲染一致：开启后可以点选已判过的物件（复用渲染缓存）
        hits = notes_at(
            self.chart,
            self.view,
            (coords[0] + coords[2]) / 2.0,
            center_y,
            cache=self.state.hit_cache,
        )
        self.assertTrue(any(n.id == "P1" for n in hits))

        # 演示模式不受影响：仍然只显示尚未判过的物件
        self.state.demo = True
        self.draw()
        self.assertNotIn("note:P1", self.scene._items)
        self.state.demo = False

        # 关掉选项后恢复原来的显示规则
        self.chart.settings.show_past_notes = False
        self.draw()
        self.assertNotIn("note:P1", self.scene._items)

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

    def test_hold_preview_extends_upward_from_start_line(self) -> None:
        """长按起始位置的预览画在时间网格线**上方**。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_LANE_HOLD)
        lane_x = self.view.layout.lane_center(2)
        self.click(lane_x, self.y_of_beat(3.0))
        self.controller.on_motion(lane_x, self.y_of_beat(3.0))   # 光标还停在同一拍
        self.draw()
        coords = self.scene._items["pending:hold"][2][0]
        top, bottom = min(coords[1], coords[3]), max(coords[1], coords[3])
        line_y = self.view.beat_to_y(3.0)
        self.assertAlmostEqual(bottom, line_y, places=6, msg="预览底边应贴在起始拍网格线上")
        self.assertGreater(bottom - top, 5.0, "预览应在网格线上方有可见高度")

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

    def test_region_preview_defaults_for_unplaced_points(self) -> None:
        """未点击的边界点按默认值预览：左边界 0、右边界 1。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_REGION)
        x = self.view.norm_to_x
        self.click(x(0.5), self.y_of_beat(4.0))     # 开始时间
        self.click(x(0.5), self.y_of_beat(6.0))     # 结束时间 -> 等待左边界起点
        self.controller.on_motion(x(0.25), self.y_of_beat(5.0))
        pending = self.state.pending
        self.assertAlmostEqual(pending.left.start, 0.25, places=6)  # 等待中的点跟随光标
        self.assertAlmostEqual(pending.left.end, 0.0, places=6)     # 未放置 -> 0
        self.assertAlmostEqual(pending.right.start, 1.0, places=6)  # 未放置 -> 1
        self.assertAlmostEqual(pending.right.end, 1.0, places=6)

        self.click(x(0.25), self.y_of_beat(5.0))    # 放下左边界起点
        self.controller.on_motion(x(0.4), self.y_of_beat(5.0))
        self.assertAlmostEqual(pending.left.start, 0.25, places=6)
        self.assertAlmostEqual(pending.left.end, 0.4, places=6)     # 下一个点跟随光标
        self.assertAlmostEqual(pending.right.start, 1.0, places=6)

        self.click(x(0.4), self.y_of_beat(5.0))     # 放下左边界终点
        self.controller.on_motion(x(0.7), self.y_of_beat(5.0))
        self.assertAlmostEqual(pending.left.end, 0.4, places=6)
        self.assertAlmostEqual(pending.right.start, 0.7, places=6)
        self.assertAlmostEqual(pending.right.end, 1.0, places=6)    # 右边界终点仍未放置

    def test_region_time_selection_preview_follows_cursor(self) -> None:
        """回归：选结束时间时预览要跟随光标。

        之前结束时间被固定在起始时间上，预览高度为 0，看起来像“没有预览”。
        """
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_REGION)
        x = self.view.norm_to_x
        self.controller.on_motion(x(0.5), self.y_of_beat(3.0))
        self.assertGreater(self.state.pending.beat_end, self.state.pending.beat_start)
        self.click(x(0.5), self.y_of_beat(3.0))        # 开始时间
        self.controller.on_motion(x(0.5), self.y_of_beat(6.0))
        pending = self.state.pending
        self.assertAlmostEqual(pending.beat_start, 3.0, places=9)
        self.assertAlmostEqual(pending.beat_end, 6.0, places=9)
        self.draw()
        self.assertIn("pending:region", self.scene._items)
        ys = self.scene._items["pending:region"][2][0][1::2]
        self.assertGreater(max(ys) - min(ys), 20.0, "预览要有可见高度")

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

    def test_slide_without_horizontal_grid_uses_click_x(self) -> None:
        """未开启横向辅助网格时，滑键放在鼠标点击的横坐标上（不吸附网格线）。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_SLIDE)
        self.click(self.view.norm_to_x(0.33), self.y_of_beat(3.0))
        self.click(self.view.norm_to_x(0.61), self.y_of_beat(3.5))
        slides = [n for n in self.document.chart.mouse_notes if isinstance(n, MouseSlide)]
        self.assertEqual(len(slides), 1)
        self.assertAlmostEqual(slides[0].x_start, 0.33, places=6)
        self.assertAlmostEqual(slides[0].x_end, 0.61, places=6)
        self.assertEqual(slides[0].direction, 1)

    def test_slide_direction_follows_click_order_when_snapped_to_same_line(self) -> None:
        """回归：两次点击吸附到同一条网格线时，方向也要按点击的左右关系判断。"""
        self.chart.settings.horizontal_divisions = 8
        self.controller.set_tool(config.TOOL_MOUSE_SLIDE)
        # 先点 0.26（吸附 0.25），再点 0.20（也吸附到 0.25）-> 应判定为向左
        self.click(self.view.norm_to_x(0.26), self.y_of_beat(3.0))
        self.click(self.view.norm_to_x(0.20), self.y_of_beat(3.5))
        slides = [n for n in self.document.chart.mouse_notes if isinstance(n, MouseSlide)]
        self.assertEqual(len(slides), 1)
        self.assertAlmostEqual(slides[0].x_start, 0.25, places=6)
        self.assertEqual(slides[0].direction, -1, "第二次点击在左边 -> 向左滑")
        self.assertLess(slides[0].x_end, slides[0].x_start)
        # 反向：先 0.20 再 0.26 -> 向右
        self.click(self.view.norm_to_x(0.20), self.y_of_beat(5.0))
        self.click(self.view.norm_to_x(0.26), self.y_of_beat(5.5))
        slides = [n for n in self.document.chart.mouse_notes if isinstance(n, MouseSlide)]
        self.assertEqual(slides[1].direction, 1, "第二次点击在右边 -> 向右滑")
        self.assertGreater(slides[1].x_end, slides[1].x_start)

    def test_slide_arrow_points_to_swipe_direction(self) -> None:
        """向左滑键的箭头指向左。"""
        right = MouseSlide("S1", 3.0, 0.2, 0.6)
        left = MouseSlide("S2", 5.0, 0.8, 0.3)
        self.chart.add(right)
        self.chart.add(left)
        self.view.set_beat(0.0)
        self.draw()
        right_arrow = self.scene._items["slide-arrow:S1"][2][0]
        left_arrow = self.scene._items["slide-arrow:S2"][2][0]
        self.assertLess(right_arrow[0], right_arrow[2], "向右滑键箭头应从左指向右")
        self.assertGreater(left_arrow[0], left_arrow[2], "向左滑键箭头应从右指向左")

    def test_left_slide_uses_yellow_color(self) -> None:
        """向左滑键使用半透明黄色，向右保持绿色。"""
        self.chart.add(MouseSlide("S1", 3.0, 0.2, 0.6))
        self.chart.add(MouseSlide("S2", 5.0, 0.8, 0.3))
        self.view.set_beat(0.0)
        self.draw()
        right_opts = dict(self.scene._items["slide:S1"][2][1])
        left_opts = dict(self.scene._items["slide:S2"][2][1])
        self.assertEqual(right_opts["fill"], DARK.slide_fill)
        self.assertEqual(left_opts["fill"], DARK.slide_left_fill)
        self.assertEqual(left_opts["outline"], DARK.slide_left_edge)
        # 半透明：仍然是点阵填充
        self.assertEqual(left_opts["stipple"], DARK.stipple_fill)

    def test_slide_box_uses_actual_length_but_arrow_keeps_min_length(self) -> None:
        """方框按实际长度显示；箭头仍有最小长度限制（保证方向看得清）。"""
        span = 0.03
        note = MouseSlide("S1", 3.0, 0.5, 0.5 + span)
        self.chart.add(note)
        self.view.set_beat(0.0)
        self.draw()
        box = self.scene._items["slide:S1"][2][0]
        box_width = abs(box[2] - box[0])
        actual_px = span * self.view.layout.mouse_width
        self.assertLess(box_width - actual_px, 6.0, "方框应基本等于实际长度")
        min_dx = max(18.0, config.MIN_SLIDE_SPAN * self.view.layout.mouse_width)
        self.assertLess(box_width, min_dx, "方框不应被拉到最小箭头长度")
        arrow = self.scene._items["slide-arrow:S1"][2][0]
        self.assertGreaterEqual(abs(arrow[2] - arrow[0]), min_dx - 0.5, "箭头要保持最小长度")

    def test_slide_pending_start_is_single_yellow_line(self) -> None:
        """选起点时只画一根黄色虚线。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_SLIDE)
        self.controller.on_motion(self.view.norm_to_x(0.3), self.y_of_beat(3.0))
        self.draw()
        self.assertIn("pending:slide-guide", self.scene._items)
        self.assertNotIn("pending:slide", self.scene._items)
        guide = dict(self.scene._items["pending:slide-guide"][2][1])
        self.assertEqual(guide["fill"], DARK.pending)
        self.assertTrue(guide.get("dash"))

    def test_slide_pending_end_colors_follow_direction(self) -> None:
        """选终点时按方向给绿色（向右）/ 黄色（向左）预览。"""
        self.chart.settings.horizontal_divisions = 0
        self.controller.set_tool(config.TOOL_MOUSE_SLIDE)
        self.click(self.view.norm_to_x(0.8), self.y_of_beat(3.0))      # 起点
        self.controller.on_motion(self.view.norm_to_x(0.3), self.y_of_beat(3.0))
        self.draw()
        left_opts = dict(self.scene._items["pending:slide"][2][1])
        self.assertEqual(left_opts["fill"], DARK.slide_left_fill, "向左 -> 黄色预览")
        self.assertNotIn("pending:slide-guide", self.scene._items)
        self.controller.on_motion(self.view.norm_to_x(0.95), self.y_of_beat(3.0))
        self.draw()
        right_opts = dict(self.scene._items["pending:slide"][2][1])
        self.assertEqual(right_opts["fill"], DARK.slide_fill, "向右 -> 绿色预览")

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

    def test_wheel_scrolls_exactly_one_grid_step(self) -> None:
        """滚轮一格 = 时间网格一格（1 / 网格细分 拍），并且总是吸附到网格。"""
        self.chart.settings.grid_division = 4
        self.view.set_beat(8.0)
        self.controller.on_wheel(1.0, False, False)
        self.assertAlmostEqual(self.view.current_beat, 7.75, places=9)
        self.controller.on_wheel(1.0, False, False)
        self.assertAlmostEqual(self.view.current_beat, 7.5, places=9)
        self.controller.on_wheel(-2.0, False, False)
        self.assertAlmostEqual(self.view.current_beat, 8.0, places=9)
        # 细分变细后：“一格”跟着变细
        self.chart.settings.grid_division = 8
        self.controller.on_wheel(1.0, False, False)
        self.assertAlmostEqual(self.view.current_beat, 7.875, places=9)
        # Shift + 滚轮 = 一次 1 拍
        self.controller.on_wheel(-1.0, False, True)
        self.assertAlmostEqual(self.view.current_beat, 8.875, places=9)
        # 总是落在网格上
        for _ in range(3):
            self.controller.on_wheel(1.0, False, False)
            steps = self.view.current_beat * self.chart.settings.grid_division
            self.assertAlmostEqual(steps, round(steps), places=6)

    def test_zoom_changes_pixels_per_beat(self) -> None:
        before = self.view.px_per_beat()
        self.controller.on_wheel(1.0, True, False)
        after = self.view.px_per_beat()
        self.assertGreater(after, before)
        # 缩放后判定线仍在同一位置（只有时间尺度变化）
        self.assertAlmostEqual(self.view.beat_to_y(self.view.current_beat), self.view.layout.judgment_y, places=6)

    def test_bpm_change_shows_up_on_the_axis_immediately(self) -> None:
        """BPM 变更处之后的时间轴格子立刻按新 BPM 变密 / 变疏（不必等判定线过去）。"""
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        self.chart.timing = TimingMap(
            [BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 240.0, 4)]
        )
        self.view.set_beat(0.0, snap=False)
        # 120 BPM：1 拍 = 0.5 s；240 BPM：1 拍 = 0.25 s -> 后者格子只有一半高
        slow = self.view.beat_to_y(0.0) - self.view.beat_to_y(1.0)
        fast = self.view.beat_to_y(8.0) - self.view.beat_to_y(9.0)
        self.assertGreater(slow, 0.0)
        self.assertAlmostEqual(fast, slow / 2.0, places=6)
        # 而且现在就看得见：第 9 拍的位置已经按 240 BPM 算好
        self.assertAlmostEqual(
            self.view.beat_to_y(9.0),
            self.view.layout.judgment_y - (4.0 + 0.25) * self.view.px_per_second(),
            places=6,
        )

    def test_crossing_bpm_change_does_not_rescale_the_view(self) -> None:
        """判定线跨过 BPM 变更时不再整屏突变：远处物件只按滚动量平移。"""
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        self.chart.timing = TimingMap(
            [BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 240.0, 4)]
        )
        self.view.set_beat(7.9, snap=False)
        before = self.view.beat_to_y(20.0)
        self.view.set_beat(8.1, snap=False)
        after = self.view.beat_to_y(20.0)
        # 0.2 拍的滚动最多让物件移动 0.2 拍的距离（不会因为换段而整体缩放）
        limit = 0.2 * max(self.view.px_per_beat_at(7.9), self.view.px_per_beat_at(8.1)) + 1e-6
        self.assertLess(abs(after - before), limit)
        # 比例本身只由缩放决定，跨段不变
        self.assertAlmostEqual(self.view.px_per_second(), self.view.px_per_second(), places=9)

    def test_y_to_beat_roundtrip_across_bpm_segments(self) -> None:
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        self.chart.timing = TimingMap(
            [BpmSegment(0.0, 140.0, 4), BpmSegment(12.0, 220.0, 3)]
        )
        self.view.set_beat(6.0, snap=False)
        for beat in (-2.0, 0.0, 5.5, 12.0, 18.25, 40.0):
            self.assertAlmostEqual(self.view.y_to_beat(self.view.beat_to_y(beat)), beat, places=6)

    def test_note_on_grid_stays_on_grid_when_bpm_changes(self) -> None:
        """默认流速的物件必须精确落在时间网格线上（BPM 分段时也不许错位）。"""
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        self.chart.timing = TimingMap(
            [BpmSegment(0.0, 140.0, 4), BpmSegment(12.0, 220.0, 3)]
        )
        self.view.set_beat(8.0, snap=False)
        for beat in (0.0, 4.0, 12.0, 15.0, 21.0):
            self.assertAlmostEqual(
                self.view.note_y(beat, self.chart.group_by_id(0)),
                self.view.beat_to_y(beat),
                places=6,
            )

    def test_scrollbar_drag_round_trip_keeps_thumb_under_cursor(self) -> None:
        """回归：拖动滑块时不能“先跳一个滑块长度”。

        `set_from_fraction(f)` 应当把**滑块上边缘**放到 f，再读回来必须还是 (f, f+窗口占比)。
        """
        for beat in (0.0, 40.0, 80.0):
            self.chart.add(LaneNote(f"DL{int(beat)}", 0, 0, beat))
        self.view.set_beat(0.0)
        width = self.view.window_fraction()
        self.assertGreater(width, 0.0)
        self.assertLess(width, 0.9)
        for fraction in (0.0, 0.2, 0.5, 0.8, 1.0 - width):
            self.view.set_from_fraction(fraction)
            first, last = self.view.scroll_fractions()
            self.assertAlmostEqual(first, fraction, delta=0.02, msg=f"f={fraction}")
            self.assertAlmostEqual(last - first, width, delta=0.02, msg=f"f={fraction}")

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

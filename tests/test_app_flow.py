"""应用级（EditorApp）端到端测试：播放/暂停吸附、演示模式、设置与保存。

需要可用的图形环境；没有显示时自动跳过。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tkinter as tk
from tkinter import ttk

from rhythm_editor import config
from rhythm_editor.app import EditorApp
from rhythm_editor.io.chart_io import load_chart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_CHART = os.path.join(ROOT, "charts", "demo", "demo.ivchart")


class AppFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            probe = tk.Tk()
            probe.destroy()
        except tk.TclError as exc:  # pragma: no cover - 无图形环境
            raise unittest.SkipTest(f"没有可用的图形环境：{exc}")
        chart = DEMO_CHART if os.path.isfile(DEMO_CHART) else None
        self.app = EditorApp(
            chart, demo=False, width=1100, height=700, maximized=False, fullscreen=False
        )
        self.app.root.withdraw()
        self.app.root.update_idletasks()
        self.addCleanup(self._close)

    def _close(self) -> None:
        try:
            self.app.audio.stop()
            self.app.audio.unload()
            self.app.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass

    # -------------------------------------------------------------- 基本状态
    def test_app_boots_with_chart(self) -> None:
        self.assertEqual(len(self.app.document.chart.all_notes()), 41)
        self.assertIn("demo.ivchart", self.app.root.title())
        self.assertEqual(self.app.controller.tool, config.TOOL_SELECT)
        self.assertIn("音频后端", self.app.info_var.get())
        # 编辑器默认是左右分栏，不是叠加
        self.assertTrue(self.app.view.split_layout)
        self.assertFalse(self.app.fullscreen)
        self.assertFalse(self.app.maximized)

    def test_split_layout_and_demo_overlay(self) -> None:
        view = self.app.view
        layout = view.layout
        # 两个区域不重叠，鼠标区域在右侧
        self.assertLess(layout.pane_6k[1], layout.pane_mouse[0])
        # 6K 占约四分之一，剩下给鼠标平面；鼠标平面在合法区域外还留有显示余量
        usable = layout.play_right - layout.play_left - config.PANE_GAP
        self.assertAlmostEqual(layout.pane_6k[1] - layout.pane_6k[0], usable * 0.25, delta=1.0)
        self.assertGreater(layout.mouse_left, layout.pane_mouse[0])
        self.assertLess(layout.mouse_right, layout.pane_mouse[1])
        # 在鼠标区域里点不到 6K 轨道
        self.assertIsNone(layout.lane_at(view.norm_to_x(0.5)))
        self.assertTrue(layout.in_6k_pane(layout.lane_center(0)))
        # 规范化横坐标映射落在鼠标区域内
        self.assertAlmostEqual(view.x_to_norm(view.norm_to_x(0.25)), 0.25, places=6)
        # 演示模式：叠加到中间四条轨道
        self.app.set_demo_mode(True)
        self.assertFalse(view.split_layout)
        self.assertAlmostEqual(layout.mouse_left, layout.lane_rects[1][0], places=6)
        self.assertAlmostEqual(layout.mouse_right, layout.lane_rects[4][1], places=6)
        self.assertEqual(layout.lane_at(view.norm_to_x(0.5)), 2)
        self.app.set_demo_mode(False)
        self.assertTrue(view.split_layout)

    def test_zoom_plus_button_is_always_available(self) -> None:
        """回归测试：缩放 + 按钮曾经会被挤掉，现在固定在右侧优先分配空间。"""
        plus = None
        container = None
        for child in self.app.toolbar.winfo_children():
            if not isinstance(child, ttk.Frame):
                continue
            for sub in child.winfo_children():
                if isinstance(sub, ttk.Button) and sub.cget("text") == "+":
                    plus, container = sub, child
        self.assertIsNotNone(plus, "工具栏里应该有 + 缩放按钮")
        self.assertEqual(plus.winfo_manager(), "pack")
        self.assertEqual(container.pack_info()["side"], "right")

    def test_toolbar_groups_without_menubar(self) -> None:
        toolbar = self.app.toolbar
        labels = set(toolbar._group_buttons)
        # 面板已拆成「谱面设置 / 速度组 / 编辑器设置」；帮助保留
        self.assertTrue({"文件", "编辑", "谱面设置", "放置", "视图", "帮助"} <= labels)
        # 谱面设置要排在 编辑 与 放置 之间
        order = [group.label for group in toolbar._groups]
        self.assertEqual(
            order, ["文件", "编辑", "谱面设置", "放置", "视图", "速度组", "编辑器设置", "帮助"]
        )
        self.assertFalse(hasattr(toolbar, "menubar"), "不再保留系统菜单栏")
        self.assertEqual(str(self.app.root.cget("menu")), "")
        # 顶栏只保留分组 + 播放；演示模式、缩放文字都移到二级菜单 / 去掉了
        self.assertTrue(hasattr(self.app.toolbar, "btn_play"))
        self.assertFalse(hasattr(toolbar, "btn_demo"))
        self.assertFalse(hasattr(toolbar, "lbl_zoom"))

    def test_panel_groups_split(self) -> None:
        """谱面设置（元数据/偏移）、速度组、编辑器设置三个入口。"""
        toolbar = self.app.toolbar
        chart_menu = toolbar._menus["谱面设置"]
        labels = [
            chart_menu.entrycget(i, "label")
            for i in range(chart_menu.index("end") + 1)
            if chart_menu.type(i) == "command"
        ]
        self.assertEqual(labels, ["元数据设置…", "BPM 与偏移…"])
        direct = [button.cget("text") for button, _ in toolbar._direct_buttons]
        self.assertIn("速度组", direct)
        self.assertIn("编辑器设置", direct)

    def test_help_menu_has_guide_and_shortcuts_only(self) -> None:
        menu = self.app.toolbar._menus["帮助"]
        labels = [
            menu.entrycget(i, "label")
            for i in range(menu.index("end") + 1)
            if menu.type(i) == "command"
        ]
        self.assertEqual(labels, ["操作指南…", "快捷键…"])
        self.assertNotIn("关于", labels)

    def test_zoom_buttons_direction(self) -> None:
        """+ 放大（可视时间变少、物件变大），− 缩小，与直觉一致。"""
        toolbar = self.app.toolbar
        plus = minus = None
        for child in toolbar.winfo_children():
            for sub in child.winfo_children():
                if isinstance(sub, ttk.Button):
                    if sub.cget("text") == "+":
                        plus = sub
                    elif sub.cget("text") == "−":
                        minus = sub
        self.assertIsNotNone(plus)
        self.assertIsNotNone(minus)
        before = self.app.view.zoom_seconds
        plus.invoke()
        self.assertLess(self.app.view.zoom_seconds, before, "+ 应该放大（可视时间变少）")
        plus.invoke()
        zoomed_in = self.app.view.zoom_seconds
        minus.invoke()
        self.assertGreater(self.app.view.zoom_seconds, zoomed_in, "− 应该缩小（可视时间变多）")

    def test_menu_not_reconfigured_every_frame(self) -> None:
        """回归测试：菜单项只在状态变化时才写入，避免展开中的二级菜单频闪。"""
        toolbar = self.app.toolbar
        menu = toolbar._menus["编辑"]
        calls: list = []
        original = menu.entryconfigure

        def recorder(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        menu.entryconfigure = recorder
        try:
            toolbar.sync()
            first = len(calls)
            for _ in range(5):
                toolbar.sync()
            self.assertEqual(len(calls), first, "状态没变时不应该再改写菜单项")
            # 状态真的改变时才写入
            self.app.document.select_all()
            self.app.document.copy_selection()
            before = len(calls)
            toolbar.sync()
            self.assertGreater(len(calls), before)
        finally:
            menu.entryconfigure = original

    def test_tick_skips_redraw_while_menu_is_posted(self) -> None:
        """回归测试：二级菜单展开期间不重绘画布（避免画布盖住菜单造成频闪）。"""
        app = self.app
        frames = app.scene.frames
        app._tick()
        self.assertGreater(app.scene.frames, frames)
        original = app.toolbar.is_menu_posted
        app.toolbar.is_menu_posted = lambda: True  # type: ignore[assignment]
        try:
            frames = app.scene.frames
            for _ in range(3):
                app._tick()
            self.assertEqual(app.scene.frames, frames, "菜单展开时不应重绘画布")
        finally:
            app.toolbar.is_menu_posted = original  # type: ignore[assignment]

    def test_fullscreen_toggle(self) -> None:
        before = self.app.fullscreen
        self.app.toggle_fullscreen()
        self.assertNotEqual(self.app.fullscreen, before)
        self.app.toggle_fullscreen()
        self.assertEqual(self.app.fullscreen, before)

    def test_maximized_by_default(self) -> None:
        """默认以最大化启动（保留标题栏与任务栏），而不是无边框全屏。"""
        chart = DEMO_CHART if os.path.isfile(DEMO_CHART) else None
        app = EditorApp(chart, demo=False, width=900, height=600)
        try:
            self.assertTrue(app.maximized)
            self.assertFalse(app.fullscreen)
            self.assertIn(str(app.root.state()), ("zoomed", "normal", "iconic"))
            self.assertEqual(int(app.root.attributes("-fullscreen")), 0)
            app.root.update_idletasks()
            # 最大化后的窗口不会小于构造时给的尺寸
            self.assertGreaterEqual(app.root.winfo_width(), 900)
            app.root.withdraw()
        finally:
            app.audio.stop()
            app.audio.unload()
            app.root.destroy()

    def test_toggle_maximize_and_fullscreen(self) -> None:
        app = self.app
        app.maximize_window()
        self.assertTrue(app.maximized)
        app.restore_window()
        self.assertFalse(app.maximized)
        app.toggle_maximize()
        self.assertTrue(app.maximized)
        app.toggle_maximize()
        self.assertFalse(app.maximized)
        # 无边框全屏与最大化互斥
        app.set_fullscreen(True)
        app.root.update_idletasks()
        self.assertEqual(int(app.root.attributes("-fullscreen")), 1)
        app.toggle_maximize()
        self.assertEqual(int(app.root.attributes("-fullscreen")), 0)
        self.assertTrue(app.maximized)
        app.restore_window()

    def test_place_edit_and_undo_through_app(self) -> None:
        from rhythm_editor.model.notes import LaneNote
        from rhythm_editor.ui.geometry import lane_note_bounds

        note = LaneNote(self.app.document.chart.new_note_id("L"), 2, 2, 40.0)
        self.app.document.add_note(note)
        bounds = lane_note_bounds(note, self.app.document.chart, self.app.view)
        self.app.controller.set_tool(config.TOOL_SELECT)
        center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        self.app.controller.on_press(center[0], center[1], False, False)
        self.app.controller.on_release(center[0], center[1], False)
        self.assertEqual(self.app.document.selection, {note.id})
        self.app.copy_selection()
        self.app.document.select_only([note.id])
        self.app.mirror_selection()
        self.assertEqual(self.app.document.chart.find(note.id).lane_start, 3)
        self.app.delete_selection()
        self.assertIsNone(self.app.document.chart.find(note.id))
        self.app.undo()
        self.assertIsNotNone(self.app.document.chart.find(note.id))

    # -------------------------------------------------------------- 播放
    def test_play_pause_snaps_judgment_line(self) -> None:
        self.app.set_grid_division("4")
        self.app.jump(8.4)
        self.assertAlmostEqual(self.app.view.current_beat, 8.5)
        self.app.toggle_play()
        self.assertTrue(self.app.audio.is_playing)
        time.sleep(0.12)
        self.app._tick()
        advanced = self.app.view.current_beat
        self.assertGreater(advanced, 8.5)
        self.app.toggle_play()
        self.assertFalse(self.app.audio.is_playing)
        snapped = self.app.view.current_beat
        self.assertAlmostEqual(snapped * 4, round(snapped * 4), places=9)
        # 暂停后音频位置与判定线一致（继续播放会从同一位置接续）
        timing = self.app.document.chart.timing
        self.assertAlmostEqual(timing.song_ms_to_beat(self.app.audio.position_ms()), snapped, places=6)

    def test_pause_with_latency_compensation(self) -> None:
        self.app.document.chart.settings.audio_latency_ms = 200.0
        self.app.audio.latency_ms = 200.0
        self.app.jump(20.0)
        self.app.play()
        time.sleep(0.1)
        self.app.pause()
        # 视觉位置应落后于“后端位置”，差值约等于系统延迟（±1 拍）
        timing = self.app.document.chart.timing
        backend_beat = timing.song_ms_to_beat(self.app.audio.position_ms())
        self.assertLessEqual(abs(backend_beat - self.app.view.current_beat), 1.0)

    # -------------------------------------------------------------- 演示模式
    def test_demo_mode_hides_toolbar_and_blocks_editing(self) -> None:
        self.app.set_demo_mode(True)
        self.assertTrue(self.app.demo_mode)
        # 窗口是隐藏的，用 winfo_manager 判断是否仍被 pack（而不是用 ismapped）
        self.assertEqual(self.app.toolbar.winfo_manager(), "")
        count = len(self.app.document.chart.all_notes())
        # 演示模式下点击画布不会放置物件
        self.app.set_tool(config.TOOL_LANE_TAP)
        self.app.canvas.event_generate("<ButtonPress-1>", x=400, y=300)
        self.app.canvas.event_generate("<ButtonRelease-1>", x=400, y=300)
        self.assertEqual(len(self.app.document.chart.all_notes()), count)
        self.app.set_demo_mode(False)
        self.assertFalse(self.app.demo_mode)
        self.assertEqual(self.app.toolbar.winfo_manager(), "pack")

    # -------------------------------------------------------------- 设置 / 保存
    def test_settings_and_save(self) -> None:
        self.app.set_grid_division("3")
        self.assertEqual(self.app.document.chart.settings.grid_division, 3)
        self.app.set_horizontal_divisions("8")
        self.assertEqual(self.app.document.chart.settings.horizontal_divisions, 8)
        self.app.set_horizontal_divisions("关")
        self.assertEqual(self.app.document.chart.settings.horizontal_divisions, 0)
        before = self.app.view.zoom_seconds
        self.app.zoom(1 / config.ZOOM_STEP)
        self.assertLess(self.app.view.zoom_seconds, before)
        self.app.set_horizontal_divisions("8")

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "saved.ivchart")
            self.app.document.path = path
            self.assertTrue(self.app.save_chart(False))
            self.assertTrue(os.path.isfile(path))
            reloaded, _raw = load_chart(path)
            self.assertEqual(reloaded.to_dict(), self.app.document.chart.to_dict())
            self.assertFalse(self.app.document.dirty)


if __name__ == "__main__":
    unittest.main()

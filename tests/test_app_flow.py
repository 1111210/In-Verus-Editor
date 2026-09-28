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
        self.app = EditorApp(chart, demo=False, width=1100, height=700, fullscreen=False)
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

    def test_split_layout_and_demo_overlay(self) -> None:
        view = self.app.view
        layout = view.layout
        # 两个区域不重叠，鼠标区域在右侧
        self.assertLess(layout.pane_6k[1], layout.pane_mouse[0])
        self.assertLess(layout.pane_mouse[0], layout.mouse_left)
        self.assertLessEqual(layout.mouse_right, layout.pane_mouse[1])
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

    def test_toolbar_groups_and_menubar(self) -> None:
        labels = set(self.app.toolbar._group_buttons)
        self.assertTrue({"文件", "编辑", "放置", "视图", "面板", "帮助"} <= labels)
        self.assertIsNotNone(self.app.toolbar.menubar)
        # 顶栏只保留分组 + 播放/演示，不再是一长串按钮
        self.assertTrue(hasattr(self.app.toolbar, "btn_play"))

    def test_fullscreen_toggle(self) -> None:
        before = self.app.fullscreen
        self.app.toggle_fullscreen()
        self.assertNotEqual(self.app.fullscreen, before)
        self.app.toggle_fullscreen()
        self.assertEqual(self.app.fullscreen, before)

    def test_fullscreen_by_default(self) -> None:
        """默认以全屏启动（构造函数参数 fullscreen=True）。"""
        chart = DEMO_CHART if os.path.isfile(DEMO_CHART) else None
        app = EditorApp(chart, demo=False, width=900, height=600, fullscreen=True)
        try:
            self.assertTrue(app.fullscreen)
            self.assertEqual(int(app.root.attributes("-fullscreen")), 1)
            app.root.withdraw()
        finally:
            app.audio.stop()
            app.audio.unload()
            app.root.destroy()

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

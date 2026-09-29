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
DEMO_CHART = os.path.join(ROOT, "charts", "demo", "demo.json")


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
        # 示例谱面：32 个 6K 物件 + 3 个空域 + 6 个滑键 + 4 个鼠标点击按键
        self.assertEqual(len(self.app.document.chart.all_notes()), 45)
        self.assertEqual(self.app.document.chart.note_counts()["click"], 4)
        self.assertIn("demo.json", self.app.root.title())
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

    def test_rate_buttons_are_always_available(self) -> None:
        """回归测试：右侧［−］［倍速］［＋］固定在右侧优先分配空间，不会被挤掉。"""
        plus = None
        container = None
        for child in self.app.toolbar.winfo_children():
            if not isinstance(child, ttk.Frame):
                continue
            for sub in child.winfo_children():
                if isinstance(sub, ttk.Button) and sub.cget("text") == "+":
                    plus, container = sub, child
        self.assertIsNotNone(plus, "工具栏里应该有 + 倍速按钮")
        self.assertEqual(plus.winfo_manager(), "pack")
        self.assertEqual(container.pack_info()["side"], "right")
        self.assertEqual(str(self.app.toolbar.lbl_rate.cget("text")), "1.0x")

    def test_toolbar_groups_without_menubar(self) -> None:
        toolbar = self.app.toolbar
        labels = set(toolbar._group_buttons)
        # 文件与编辑合并；新增「音效」；帮助收进「编辑器设置」
        self.assertTrue(
            {"文件 / 编辑", "谱面设置", "放置物件", "视图", "音效", "编辑器设置"} <= labels
        )
        direct = [button.cget("text") for button, _ in toolbar._direct_buttons]
        self.assertIn("速度组", direct)
        self.assertIn("编辑物件", direct)
        # 顺序：文件/编辑 → 谱面设置 → 放置物件 → 编辑物件 → 视图 → 速度组 → 音效 → 编辑器设置
        order = [group.label for group in toolbar._groups]
        self.assertEqual(
            order,
            ["文件 / 编辑", "谱面设置", "放置物件", "编辑物件", "视图", "速度组", "音效", "编辑器设置"],
        )
        self.assertFalse(hasattr(toolbar, "menubar"), "不再保留系统菜单栏")
        self.assertEqual(str(self.app.root.cget("menu")), "")
        # 顶栏只保留分组 + 播放；演示模式、缩放文字都移到二级菜单 / 去掉了
        self.assertTrue(hasattr(self.app.toolbar, "btn_play"))
        self.assertFalse(hasattr(toolbar, "btn_demo"))
        self.assertFalse(hasattr(toolbar, "lbl_zoom"))

    def test_panel_groups_split(self) -> None:
        """谱面设置（元数据/偏移）、速度组、编辑器设置；编辑器设置里含帮助。"""
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
        editor_menu = toolbar._menus["编辑器设置"]
        editor_labels = [
            editor_menu.entrycget(i, "label")
            for i in range(editor_menu.index("end") + 1)
            if editor_menu.type(i) == "command"
        ]
        self.assertIn("编辑器设置…", editor_labels)
        self.assertIn("操作指南…", editor_labels)
        self.assertIn("快捷键…", editor_labels)
        # 播放按钮固定在工具栏最右端（原「帮助」的位置）
        self.assertEqual(self.app.toolbar.btn_play.pack_info()["side"], "right")

    def test_edit_note_button_is_in_toolbar_not_menu(self) -> None:
        """「编辑物件」是顶栏直执按钮，排在「放置物件」之后。"""
        toolbar = self.app.toolbar
        direct = [button.cget("text") for button, _ in toolbar._direct_buttons]
        self.assertIn("编辑物件", direct)
        edit_menu = toolbar._menus["文件 / 编辑"]
        labels = [
            edit_menu.entrycget(i, "label")
            for i in range(edit_menu.index("end") + 1)
            if edit_menu.type(i) == "command"
        ]
        self.assertNotIn("编辑选中物件…", labels)
        # 顺序：文件/编辑 -> 谱面设置 -> 放置物件 -> 编辑物件
        order = [group.label for group in toolbar._groups]
        self.assertEqual(
            order[:4], ["文件 / 编辑", "谱面设置", "放置物件", "编辑物件"]
        )
        # 只有一个物件被选中时才可用
        group = next(g for g in toolbar._groups if g.label == "编辑物件")
        self.assertFalse(group.enabled())
        self.app.document.select_all()
        self.assertFalse(group.enabled())
        note = self.app.document.chart.lane_notes[0]
        self.app.document.select_only([note.id])
        self.assertTrue(group.enabled())

    def test_toolbar_scrolls_horizontally_when_narrow(self) -> None:
        """窗口宽度不足时工具栏自动出现横向滚动条，宽窗口时自动隐藏。"""
        toolbar = self.app.toolbar
        root = self.app.root
        root.deiconify()
        try:
            toolbar._update_scrollbar()
            # 用一个“视口明显比内容宽”的窗口
            needed = (
                toolbar._strip.winfo_reqwidth() + toolbar._right.winfo_reqwidth() + 80
            )
            root.geometry(f"{min(needed, root.winfo_screenwidth() - 40)}x700")
            root.update()
            toolbar._update_scrollbar()
            wide = toolbar._hscroll_visible
            root.geometry("560x700")
            root.update()
            toolbar._update_scrollbar()
            narrow = toolbar._hscroll_visible
        finally:
            root.withdraw()
        self.assertFalse(wide, "宽窗口不需要横向滚动条")
        self.assertTrue(narrow, "窄窗口应自动出现横向滚动条")
        self.assertEqual(toolbar._hscroll.winfo_manager(), "pack")

    def test_help_menu_has_guide_and_shortcuts_only(self) -> None:
        # 帮助已并入「编辑器设置」菜单
        menu = self.app.toolbar._menus["编辑器设置"]
        labels = [
            menu.entrycget(i, "label")
            for i in range(menu.index("end") + 1)
            if menu.type(i) == "command"
        ]
        self.assertEqual(labels, ["编辑器设置…", "操作指南…", "快捷键…"])
        self.assertNotIn("关于", labels)

    def test_auto_check_is_in_edit_menu_and_selects_issues(self) -> None:
        """自动检查放在「编辑」二级菜单里；运行时选中问题物件并弹出报告。"""
        toolbar = self.app.toolbar
        edit_menu = toolbar._menus["文件 / 编辑"]
        labels = [
            edit_menu.entrycget(i, "label")
            for i in range(edit_menu.index("end") + 1)
            if edit_menu.type(i) == "command"
        ]
        self.assertTrue(any("自动检查" in label for label in labels), labels)

        # 造一个冲突：同一轨道同一时刻两个按键
        from rhythm_editor.model.notes import LaneNote

        chart = self.app.document.chart
        chart.add(LaneNote("L900", 0, 0, 40.0))
        chart.add(LaneNote("L901", 0, 0, 40.0))
        issues = self.app.auto_check_issues()
        self.assertTrue(issues)

        captured = {}

        def fake_run_dialog(cls, parent, theme, title="", content="", *args, **kwargs):
            captured["title"] = title
            captured["content"] = content
            return None

        import rhythm_editor.app as app_module

        original = app_module.run_dialog
        app_module.run_dialog = fake_run_dialog  # type: ignore[assignment]
        try:
            self.app.run_auto_check()
        finally:
            app_module.run_dialog = original  # type: ignore[assignment]
        self.assertEqual(captured["title"], "自动检查报告")
        self.assertIn("L900", captured["content"])
        self.assertEqual(self.app.document.selection, {"L900", "L901"})

    def test_rate_buttons_step_and_clamp(self) -> None:
        """倍速按钮：[−] 减 0.1x、[+] 加 0.1x，范围 0.1 ~ 1.0x。"""
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
        minus.invoke()
        self.assertAlmostEqual(self.app.audio.rate, 0.9, places=6)
        toolbar.sync()
        self.assertEqual(str(toolbar.lbl_rate.cget("text")), "0.9x")
        plus.invoke()
        self.assertAlmostEqual(self.app.audio.rate, 1.0, places=6)
        # 上限：1.0x 再加不上去
        plus.invoke()
        self.assertAlmostEqual(self.app.audio.rate, 1.0, places=6)
        # 下限：一路减到 0.1x 就停住
        for _ in range(20):
            minus.invoke()
        self.assertAlmostEqual(self.app.audio.rate, config.RATE_MIN, places=6)
        toolbar.sync()
        self.assertEqual(str(toolbar.lbl_rate.cget("text")), "0.1x")
        self.app.set_playback_rate(1.0)

    def test_menu_not_reconfigured_every_frame(self) -> None:
        """回归测试：菜单项只在状态变化时才写入，避免展开中的二级菜单频闪。"""
        toolbar = self.app.toolbar
        menu = toolbar._menus["文件 / 编辑"]
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

    def test_menu_protection_has_no_hold_time(self) -> None:
        """回归：菜单保护没有任何等待时间 —— 一旦继续操作就立刻恢复重绘。"""
        toolbar = self.app.toolbar
        toolbar.mark_menu_open()
        self.assertTrue(toolbar.is_menu_posted())
        toolbar.mark_interaction()
        self.assertFalse(toolbar.is_menu_posted(), "交互后应立即解除保护（无保护时间）")
        # 菜单自己触发 <Unmap> 时同样立即解除
        toolbar.mark_menu_open()
        toolbar.mark_menu_closed()
        self.assertFalse(toolbar.is_menu_posted())

    def test_menu_toggles_closed_on_second_click(self) -> None:
        """回归：菜单按钮再次点击是收起，而不是重新弹出。"""
        toolbar = self.app.toolbar
        # tk_popup 在 Windows 会进入嵌套事件循环；测试里替换掉真正的弹出动作与“是否展开”查询
        toolbar._post_menu = lambda menu, button: None  # type: ignore[assignment]
        toolbar._menu_is_posted = lambda menu: toolbar._open_group is not None  # type: ignore[assignment]
        toolbar.toggle_menu("文件 / 编辑")
        self.assertEqual(toolbar._open_group, "文件 / 编辑")
        self.assertTrue(toolbar.is_menu_posted())
        toolbar.toggle_menu("文件 / 编辑")            # 再次点击同一个按钮
        self.assertIsNone(toolbar._open_group)
        self.assertFalse(toolbar.is_menu_posted())
        # 点另一个分组：切换而不是叠加
        toolbar.toggle_menu("文件 / 编辑")
        toolbar.toggle_menu("视图")
        self.assertEqual(toolbar._open_group, "视图")

    def test_outside_click_and_escape_close_menu(self) -> None:
        toolbar = self.app.toolbar
        toolbar._post_menu = lambda menu, button: None  # type: ignore[assignment]
        toolbar._menu_is_posted = lambda menu: toolbar._open_group is not None  # type: ignore[assignment]
        toolbar.toggle_menu("文件 / 编辑")
        toolbar.handle_outside_click(self.app.canvas)      # 点画布 -> 收起
        self.assertIsNone(toolbar._open_group)
        toolbar.toggle_menu("文件 / 编辑")
        toolbar.handle_outside_click(toolbar._menu_buttons["文件 / 编辑"])   # 点自身按钮 -> 保持
        self.assertEqual(toolbar._open_group, "文件 / 编辑")
        self.assertTrue(toolbar.close_if_open())           # Esc -> 收起
        self.assertFalse(toolbar.close_if_open())

    def test_blocking_popup_resets_state_after_close(self) -> None:
        """阻塞式原生菜单：tk_popup 返回时菜单已被“再次点击”关掉，状态应复位。"""
        toolbar = self.app.toolbar
        toolbar._post_menu = lambda menu, button: None      # type: ignore[assignment]
        toolbar._menu_is_posted = lambda menu: False        # type: ignore[assignment]
        toolbar.toggle_menu("文件 / 编辑")
        self.assertIsNone(toolbar._open_group)
        self.assertFalse(toolbar.is_menu_posted())
        # 再次点击就是重新打开，而不是“关掉不存在的菜单”
        toolbar.toggle_menu("文件 / 编辑")
        self.assertIsNone(toolbar._open_group)

    def test_fullscreen_toggle(self) -> None:
        before = self.app.fullscreen
        self.app.toggle_fullscreen()
        self.assertNotEqual(self.app.fullscreen, before)
        self.app.toggle_fullscreen()
        self.assertEqual(self.app.fullscreen, before)

    def test_vertical_scrollbar_drives_timeline(self) -> None:
        """右侧竖向滚动条：拖动/翻页/步进都能移动时间轴，滑块位置跟随。"""
        app = self.app
        self.assertEqual(app.vscroll.winfo_manager(), "pack")
        self.assertEqual(str(app.vscroll.pack_info()["side"]), "right")
        app.jump(0.0)
        app._sync_scrollbar(force=True)
        first_before = float(app.vscroll.get()[0])
        app._on_scroll("moveto", "0.6")
        self.assertGreater(app.view.current_beat, 0.0)
        self.assertGreater(float(app.vscroll.get()[0]), first_before)
        # 翻页 + 步进
        page_from = app.view.current_beat
        app._on_scroll("scroll", "1", "pages")
        self.assertGreater(app.view.current_beat, page_from)
        unit_from = app.view.current_beat
        app._on_scroll("scroll", "-1", "units")
        self.assertAlmostEqual(app.view.current_beat, unit_from - app.view.grid_step(), places=6)
        # 判定线仍然吸附在网格上
        steps = app.view.current_beat * app.document.chart.settings.grid_division
        self.assertAlmostEqual(steps, round(steps), places=6)

    def test_scrollbars_use_white_thumb_black_trough(self) -> None:
        """滚动条样式：滑条体白色、背景黑色（竖向时间轴与工具栏横向滚动条一致）。"""
        style = ttk.Style(self.app.root)
        for name in ("White.Vertical.TScrollbar", "White.Horizontal.TScrollbar"):
            self.assertEqual(str(style.lookup(name, "background")), "#ffffff")
            self.assertEqual(str(style.lookup(name, "troughcolor")), "#000000")
        # 两个滚动条都套用了该样式
        self.assertEqual(str(self.app.vscroll.cget("style")), "White.Vertical.TScrollbar")
        self.assertEqual(str(self.app.toolbar._hscroll.cget("style")), "White.Horizontal.TScrollbar")

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
            path = os.path.join(tmp, "saved.json")
            self.app.document.path = path
            self.assertTrue(self.app.save_chart(False))
            self.assertTrue(os.path.isfile(path))
            reloaded, _raw = load_chart(path)
            self.assertEqual(reloaded.to_dict(), self.app.document.chart.to_dict())
        self.assertFalse(self.app.document.dirty)

    def test_grid_settings_live_in_view_menu_only(self) -> None:
        """网格设置从「编辑器设置」移到「视图」子菜单，并且带“自定义…”项。"""
        from rhythm_editor.ui.dialogs import SettingsDialog

        # 编辑器设置面板里不再有网格字段
        dialog = SettingsDialog(self.app.root, self.app.theme, self.app.document.chart)
        try:
            self.assertFalse(hasattr(dialog, "e_division"))
            self.assertFalse(hasattr(dialog, "e_horizontal"))
        finally:
            dialog.destroy()
        # 视图菜单里带自定义项
        view_menu = self.app.toolbar._menus["视图"]
        labels = [
            view_menu.entrycget(i, "label")
            for i in range(view_menu.index("end") + 1)
            if view_menu.type(i) == "cascade"
        ]
        self.assertIn("时间网格细分", labels)
        self.assertIn("横向辅助网格", labels)
        for name in ("时间网格细分", "横向辅助网格"):
            index = next(
                i
                for i in range(view_menu.index("end") + 1)
                if view_menu.type(i) == "cascade" and view_menu.entrycget(i, "label") == name
            )
            sub = self.app.root.nametowidget(view_menu.entrycget(index, "menu"))
            sub_labels = [
                sub.entrycget(j, "label")
                for j in range(sub.index("end") + 1)
                if sub.type(j) == "command"
            ]
            self.assertTrue(
                any("自定义" in label for label in sub_labels),
                f"{name} 子菜单应有自定义项：{sub_labels}",
            )

    def test_horizontal_grid_has_no_separate_switch(self) -> None:
        """横向辅助网格只要设置了等分就必然显示（没有单独的显示开关）。"""
        self.assertFalse(hasattr(self.app.view, "show_horizontal_grid"))
        self.assertFalse(hasattr(self.app, "toggle_horizontal_grid"))
        view_menu = self.app.toolbar._menus["视图"]
        labels = [
            view_menu.entrycget(i, "label")
            for i in range(view_menu.index("end") + 1)
            if view_menu.type(i) == "command"
        ]
        self.assertNotIn("显示横向辅助网格", labels)
        # 设置等分后画布上必然出现 hgrid 图元
        self.app.set_horizontal_divisions("6")
        self.app._tick()
        hgrid = [k for k in self.app.scene._items if k.startswith("hgrid:")]
        self.assertTrue(hgrid, "设置了等分就应该画出横向辅助网格")

    def test_toolbar_buttons_share_same_height_style(self) -> None:
        """编辑物件 / 速度组 / 编辑器设置与能展开菜单的按钮同样高（同一套样式）。"""
        toolbar = self.app.toolbar
        styles = {button.cget("style") for button, _ in toolbar._direct_buttons}
        self.assertEqual(styles, {"Tool.TButton"})
        for button in toolbar._group_buttons.values():
            self.assertEqual(str(button.cget("style")), "Tool.TButton")


if __name__ == "__main__":
    unittest.main()

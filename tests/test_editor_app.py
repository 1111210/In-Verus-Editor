"""编辑器应用层的关键流程：启动、菜单、播放、演示模式、设置与保存。

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

# 全局设置会写到用户配置目录：测试里改到临时文件，别碰真实配置
_SETTINGS_TMP = tempfile.TemporaryDirectory()
os.environ["VERUS_SETTINGS"] = os.path.join(_SETTINGS_TMP.name, "settings.json")
os.environ["VERUS_STARTUP_LOG"] = os.path.join(_SETTINGS_TMP.name, "audio_startup.log")
from tkinter import ttk

import config
from app import EditorApp
from chart_io import load_chart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_CHART = os.path.join(ROOT, "charts", "demo", "demo.json")


class EditorAppTests(unittest.TestCase):
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

    def _softview(self):
        try:
            from preview import softview
        except Exception:  # pragma: no cover - 没有 pygame
            self.skipTest("没有 pygame")
        if not softview.available():  # pragma: no cover
            self.skipTest("没有 pygame")
        return softview

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

    def test_toolbar_groups_without_menubar(self) -> None:
        toolbar = self.app.toolbar
        labels = set(toolbar._group_buttons)
        # 文件与编辑合并成一个分组；音效独立成组；帮助收在「编辑器设置」里
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
        self.assertFalse(hasattr(toolbar, "menubar"), "没有系统菜单栏")
        self.assertEqual(str(self.app.root.cget("menu")), "")
        # 顶栏只保留分组 + 播放；演示模式与缩放文字都在二级菜单里
        self.assertTrue(hasattr(self.app.toolbar, "btn_play"))
        self.assertFalse(hasattr(toolbar, "btn_demo"))
        self.assertFalse(hasattr(toolbar, "lbl_zoom"))

    def test_menu_toggles_closed_on_second_click(self) -> None:
        """菜单按钮再次点击时收起。"""
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
        # 点另一个分组：切换到新分组
        toolbar.toggle_menu("文件 / 编辑")
        toolbar.toggle_menu("视图")
        self.assertEqual(toolbar._open_group, "视图")

    def test_vertical_scrollbar_drives_timeline(self) -> None:
        """右侧竖向滚动条：拖动/翻页/步进都能移动时间轴（方向已反过来）。"""
        app = self.app
        self.assertEqual(app.vscroll.winfo_manager(), "pack")
        self.assertEqual(str(app.vscroll.pack_info()["side"]), "right")
        app.jump(0.0)
        app._sync_scrollbar(force=True)
        first_before = float(app.vscroll.get()[0])
        app._on_scroll("moveto", "0.6")
        self.assertGreater(app.view.current_beat, 0.0)
        # 方向反转：往时间前方走时滑块往上（比例变小）
        self.assertLess(float(app.vscroll.get()[0]), first_before)
        # 翻页 + 步进
        page_from = app.view.current_beat
        app._on_scroll("scroll", "1", "pages")
        self.assertLess(app.view.current_beat, page_from, "往下翻 = 往回走")
        unit_from = app.view.current_beat
        app._on_scroll("scroll", "-1", "units")
        self.assertAlmostEqual(app.view.current_beat, unit_from + app.view.grid_step(), places=6)
        # 判定线仍然吸附在网格上
        steps = app.view.current_beat * app.document.chart.settings.grid_division
        self.assertAlmostEqual(steps, round(steps), places=6)

    def test_place_edit_and_undo_through_app(self) -> None:
        from model.notes import LaneNote
        from ui.geometry import lane_note_bounds

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
        from model.notes import LaneNote

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

        import app as app_module

        original = app_module.run_dialog
        app_module.run_dialog = fake_run_dialog  # type: ignore[assignment]
        try:
            self.app.run_auto_check()
        finally:
            app_module.run_dialog = original  # type: ignore[assignment]
        self.assertEqual(captured["title"], "自动检查报告")
        self.assertIn("L900", captured["content"])
        self.assertEqual(self.app.document.selection, {"L900", "L901"})

    def test_show_past_notes_menu_toggle(self) -> None:
        """视图菜单提供「显示更早的物件」；视图设置不进撤销栈。"""
        view_menu = self.app.toolbar._menus["视图"]
        labels = [
            view_menu.entrycget(i, "label")
            for i in range(view_menu.index("end") + 1)
            if view_menu.type(i) == "checkbutton"
        ]
        self.assertIn("显示更早的物件", labels)
        self.assertFalse(self.app.document.chart.settings.show_past_notes)
        self.app.document.dirty = False
        self.app.toggle_show_past_notes()
        self.assertTrue(self.app.document.chart.settings.show_past_notes)
        self.assertFalse(self.app.document.can_undo(), "视图设置不应产生撤销记录")
        self.assertFalse(self.app.document.dirty, "视图设置不应标记谱面已修改")
        self.app.undo()
        self.assertTrue(self.app.document.chart.settings.show_past_notes, "撤销不应回退视图设置")

    def test_idle_tick_skips_redraw_until_something_changes(self) -> None:
        """空闲帧不重绘；视图 / 选择 / 编辑器设置一变就立刻重绘。"""
        calls = {"n": 0}
        original = self.app.renderer.draw

        def counting(*args, **kwargs):
            calls["n"] += 1
            return original(*args, **kwargs)

        self.app.renderer.draw = counting
        self.app._tick()
        first = calls["n"]
        self.app._tick()
        self.app._tick()
        self.assertEqual(calls["n"], first, "没有变化时不应重新绘制")

        self.app.view.scroll_beats(1.0)
        self.app._tick()
        self.assertGreater(calls["n"], first, "滚动后应重绘")

        before = calls["n"]
        self.app.document.select_only([self.app.document.chart.all_notes()[0].id])
        self.app._tick()
        self.assertGreater(calls["n"], before, "选择变化后应重绘")

        before = calls["n"]
        self.app.document.chart.settings.grid_division = 16
        self.app._tick()
        self.assertGreater(calls["n"], before, "网格设置变化后应重绘")

        before = calls["n"]
        self.app._tick()
        self.assertEqual(calls["n"], before, "再次空闲仍不应重绘")

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

    def test_demo_mode_hides_toolbar_and_blocks_editing(self) -> None:
        self.app.set_demo_mode(True)
        self.assertTrue(self.app.demo_mode)
        # 窗口是隐藏的，用 winfo_manager 判断是否仍被 pack
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

    def test_demo_view_settings_are_independent_and_applied(self) -> None:
        """「编辑器设置 → 演示视图设置…」独立保存，改完立刻套到离屏演示视图。"""
        import app as app_module

        self._softview()
        app = self.app
        menu = app.toolbar._menus["编辑器设置"]
        labels = [
            menu.entrycget(i, "label")
            for i in range(menu.index("end") + 1)
            if menu.type(i) == "command"
        ]
        self.assertIn("演示视图设置…", labels)

        # 先建出演示视图（用默认演示视图设置），再改设置：应当原地刷新
        app.set_demo_mode(True)
        app._tick()
        try:
            view = app._demo_view
            if view is None:  # pragma: no cover - 初始化失败
                self.skipTest("演示视图不可用")
            self.assertAlmostEqual(
                view.settings.note_thickness, app.editor_settings.demo_note_thickness
            )

            result = {
                "demo_note_thickness": 0.5,
                "demo_multi_thickness": 0.4,
                "demo_slide_thickness": 0.1,
                "demo_click_thickness": 0.05,
                "demo_speed": 1.5,
                "demo_side_lane_highlight": True,
            }
            original = app_module.run_dialog
            app_module.run_dialog = lambda *args, **kwargs: result  # type: ignore[assignment]
            try:
                app.edit_demo_view_settings()
            finally:
                app_module.run_dialog = original  # type: ignore[assignment]
            self.assertAlmostEqual(app.editor_settings.demo_note_thickness, 0.5)
            self.assertAlmostEqual(app.editor_settings.demo_speed, 1.5)
            self.assertTrue(app.editor_settings.demo_side_lane_highlight)
            # 离屏视图立刻跟着变，且仍然固定软件渲染
            self.assertAlmostEqual(view.settings.note_thickness, 0.5)
            self.assertAlmostEqual(view.settings.speed, 1.5)
            self.assertTrue(view.settings.side_lane_highlight)
        finally:
            app.set_demo_mode(False)

    def test_demo_mode_uses_software_render(self) -> None:
        """演示模式贴的是演示视图软件渲染的帧（preview/softview）。"""
        softview = self._softview()
        calls = []
        original = softview.draw_frame

        def spy(view, chart, now_ms, **kwargs):
            calls.append((view, chart, now_ms, kwargs))
            return original(view, chart, now_ms, **kwargs)

        softview.draw_frame = spy  # type: ignore[assignment]
        try:
            self.app.set_demo_mode(True)
            self.app._tick()
        finally:
            softview.draw_frame = original  # type: ignore[assignment]
        try:
            self.assertTrue(calls, "演示模式应当调用演示视图的软件渲染")
            view, chart, _now, kwargs = calls[0]
            self.assertIs(chart, self.app.document.chart)
            self.assertIs(view, self.app._demo_view)
            self.assertIn("rate", kwargs)
            # 画布上只有演示帧（编辑器场景已清空）
            self.assertEqual(self.app.scene.item_count, 0)
            self.assertIsNotNone(self.app._demo_image_id)
            self.assertIn(self.app._demo_image_id, self.app.canvas.find_all())
            self.assertTrue(self.app.canvas.find_withtag("demo"))
            image = self.app.canvas.itemcget(self.app._demo_image_id, "image")
            self.assertEqual(
                int(self.app.root.tk.call("image", "width", image)),
                int(self.app.view.layout.width),
            )
            self.assertEqual(
                int(self.app.root.tk.call("image", "height", image)),
                int(self.app.view.layout.height),
            )
        finally:
            self.app.set_demo_mode(False)


class LauncherTests(unittest.TestCase):
    """启动脚本与音频自检：不检测 / 不切换解释器，日志写临时文件。"""

    def test_bootstrap_adds_src_to_path(self) -> None:
        import importlib.util

        path = os.path.join(ROOT, "run_editor.py")
        spec = importlib.util.spec_from_file_location("run_editor_launcher", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._bootstrap()
        self.assertIn(os.path.join(ROOT, "src"), sys.path)

    def test_startup_log_records_interpreter_and_backend(self) -> None:
        from audio import audio_backend_name, write_startup_log

        with tempfile.TemporaryDirectory() as tmp:
            target = write_startup_log(os.path.join(tmp, "_diag", "audio_startup.log"))
            self.assertTrue(os.path.isfile(target))
            with open(target, encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("解释器：", text)
        self.assertIn(sys.executable, text)
        self.assertIn(f"后端：{audio_backend_name()}", text)

    def test_device_failure_is_reported_as_such(self) -> None:
        import audio as audio_module
        from audio import AudioPlayer, audio_backend_state, audio_setup_hint

        original_error = audio_module._MIXER_ERROR
        original_factory = AudioPlayer.__dict__["_create_plain_backend"]
        audio_module._MIXER_ERROR = "error: Audio target 'x' not available"
        AudioPlayer._create_plain_backend = staticmethod(lambda: audio_module.SilentBackend())
        try:
            self.assertEqual(audio_backend_state(), "device")
            hint = audio_setup_hint()
            self.assertIn("音频设备", hint)
            self.assertNotIn("未检测到 pygame", hint)
            self.assertNotIn("未安装 pygame", AudioPlayer().status_text)
        finally:
            AudioPlayer._create_plain_backend = original_factory
            audio_module._MIXER_ERROR = original_error


if __name__ == "__main__":
    unittest.main()

"""游玩器 GUI 冒烟测试（headless：SDL dummy 驱动）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor.game import settings as psettings  # noqa: E402


class PlayerAppSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            import pygame  # noqa: F401
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"没有 pygame：{exc}")
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["VERUS_PLAYER_SETTINGS"] = os.path.join(cls.tmp.name, "player.json")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()
        os.environ.pop("VERUS_PLAYER_SETTINGS", None)

    def setUp(self) -> None:
        import pygame

        from rhythm_editor.game.app import PlayerApp

        chart = os.path.join(ROOT, "charts", "demo", "demo.json")
        if not os.path.isfile(chart):
            self.skipTest("缺少示例谱面")
        self.pygame = pygame
        self.app = PlayerApp([chart])
        self.addCleanup(self._close)

    def _close(self) -> None:
        try:
            self.app.audio.unload()
        finally:
            self.pygame.quit()

    def test_menu_loading_play_result_flow(self) -> None:
        app = self.app
        self.assertEqual(app.state, "menu")
        app._draw_menu()
        app._enter_loading()
        self.assertEqual(app.state, "loading")
        self.assertEqual(app.chart.metadata.title[:4], "Demo")
        app._start_play()
        self.assertEqual(app.state, "play")
        self.assertGreater(app.judge.total_notes, 0)
        for _ in range(10):          # 跑几帧：渲染 + 判定推进都不应抛异常
            app._update(1 / 120)
            app._draw_play()
        app._finish()
        self.assertEqual(app.state, "result")
        app._draw_result()

    def test_judge_windows_and_mouse_mapping(self) -> None:
        from rhythm_editor.game.judge import EXACT, Judge, grade_for_offset

        self.assertEqual(grade_for_offset(10.0), EXACT)
        app = self.app
        app._enter_loading()
        app._start_play()
        norm = app._mouse_norm()
        self.assertGreaterEqual(norm, 0.0)
        self.assertLessEqual(norm, 1.0, "默认（有界）鼠标应夹在 0..1")
        self.assertIsInstance(app.judge, Judge)

    def test_settings_are_saved_globally(self) -> None:
        app = self.app
        app.settings.player_name = "测试玩家"
        psettings.save_settings(app.settings)
        self.assertEqual(psettings.load_settings().player_name, "测试玩家")

    def test_lane_highlight_and_popups(self) -> None:
        """按下轨道时该轨高亮；判定结果会生成飘字。"""
        from rhythm_editor.game.judge import Judged

        app = self.app
        app._enter_loading()
        app._start_play()
        app.pressed_lanes.add(2)
        app._register(Judged("L1", "EXACT", 0.0))
        self.assertEqual(len(app.popups), 1)
        app._draw_play()          # 高亮 + 飘字都要能画出来（不抛异常）
        self.assertIn(2, app.pressed_lanes)

    def test_volumes_and_pause(self) -> None:
        """设置里能调音乐/打击音量；空格暂停并能在暂停后继续。"""
        app = self.app
        app.settings_row = 8
        before = app.settings.music_volume
        app._adjust_setting(-1)
        self.assertLess(app.settings.music_volume, before)
        app.settings_row = 9
        hit_before = app.settings.hit_volume
        app._adjust_setting(-1)
        self.assertLess(app.settings.hit_volume, hit_before)
        app._enter_loading()
        app._start_play()
        app._toggle_pause()
        self.assertTrue(app.paused)
        app._draw_play()          # 暂停遮罩也要能画
        app._toggle_pause()
        self.assertFalse(app.paused)

    def test_rate_control_and_score_multiplier(self) -> None:
        """游玩器倍速：设置里 0.1 一档、0.1~1.0x；游玩中按 [-] 改倍速并自动暂停。"""
        app = self.app
        app.settings_row = 11
        app._adjust_setting(-1)
        self.assertAlmostEqual(app.settings.rate, 0.9, places=6)
        app._enter_loading()
        app._start_play()
        self.assertAlmostEqual(app.judge.rate, 0.9, places=6)
        app.judge._judge_result("X", "EXACT", 0.0)
        self.assertEqual(app.judge.score, round(app.judge.base_score * (0.9 ** 1.5)))
        app._adjust_rate(-1)
        self.assertAlmostEqual(app.settings.rate, 0.8, places=6)
        self.assertTrue(app.paused, "改倍速会自动暂停")
        self.assertIn("0.8", app.status_hint)
        app._draw_play()          # 暂停遮罩 + 提示都要能画

    def test_renderer_switch_falls_back_without_gl(self) -> None:
        """切到 OpenGL：能建上下文就用 GL，否则自动回退软件渲染（不会崩）。"""
        app = self.app
        self.assertEqual(app.renderer_name, "software")
        app.settings_row = 10
        app._adjust_setting(1)
        self.assertEqual(app.settings.renderer, "opengl")
        self.assertIn(app.renderer_name, ("opengl", "software"))
        app._draw_menu()
        app._present()          # 两种后端都要能呈现一帧
        app._adjust_setting(1)  # 切回软件
        self.assertEqual(app.settings.renderer, "software")
        self.assertEqual(app.renderer_name, "software")

    def test_chart_list_shows_metadata_and_recent(self) -> None:
        """主界面列出元数据；打开过的谱面进入「最近」并排在前面。"""
        app = self.app
        path = app.paths[0]
        title, artist, difficulty, bpm = app._chart_info(path)
        self.assertTrue(title)
        self.assertGreater(bpm, 0.0)
        app._enter_loading()
        self.assertEqual(app.settings.recent[0], path)
        app._draw_menu()          # 列表 + 元数据渲染不抛异常
        self.assertEqual(app.paths[0], path, "最近打开应排在前面")

    def test_charts_folder_lists_one_entry_per_folder(self) -> None:
        """charts/ 下每个含 json 的文件夹算一张谱面；多个 json 取主谱面。"""
        from rhythm_editor.game.app import chart_files

        with tempfile.TemporaryDirectory() as tmp:
            alpha = os.path.join(tmp, "charts", "alpha")
            beta = os.path.join(tmp, "charts", "beta", "audio")
            empty = os.path.join(tmp, "charts", "empty")
            os.makedirs(alpha)
            os.makedirs(beta)
            os.makedirs(empty)
            for folder in (alpha, beta):
                for name in ("chart.json", "b_alt.json", "_autosave.json"):
                    with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
                        handle.write("{}")
            entries = chart_files(tmp)
            self.assertEqual(
                [os.path.basename(p) for p in entries],
                ["chart.json", "chart.json"],
                "一个文件夹只出一条（多个 json 时取主谱面）",
            )
            self.assertEqual([os.path.dirname(p) for p in entries], [alpha, beta])

            # 只有下划线开头的临时 json 的文件夹、以及没有 json 的文件夹都不算谱面
            solo = os.path.join(tmp, "charts", "solo")
            os.makedirs(solo)
            with open(os.path.join(solo, "_tmp.json"), "w", encoding="utf-8") as handle:
                handle.write("{}")
            self.assertEqual(len(chart_files(tmp)), 2)

    def test_menu_actions_are_reachable_without_any_chart(self) -> None:
        """没有谱面时主界面仍有「打开其他路径」与「设置」两行，Enter 能进设置。"""
        app = self.app
        app.paths = []
        app._rebuild_entries()
        self.assertEqual(app._menu_rows(), 2)
        app._draw_menu()                       # 空列表也要能画（不抛异常）
        app.selected = 1                       # 固定行：0 = 打开其他路径，1 = 设置
        app._activate_selected()
        self.assertEqual(app.state, "settings")
        app.state = "menu"
        app.selected = 99                      # 越界也要夹回设置那一行
        app._rebuild_entries()
        self.assertEqual(app.selected, 1)
        # ↑↓ / Home / End 只在两行之间循环
        app._event_menu(self.pygame.event.Event(self.pygame.KEYDOWN, key=self.pygame.K_DOWN))
        self.assertEqual(app.selected, 0)
        app._event_menu(self.pygame.event.Event(self.pygame.KEYDOWN, key=self.pygame.K_UP))
        self.assertEqual(app.selected, 1)

    def test_open_external_chart_by_path(self) -> None:
        """「打开其他路径」：直接给路径时能读入并加入列表（不弹对话框）。"""
        app = self.app
        source = os.path.join(ROOT, "charts", "demo", "demo.json")
        with tempfile.TemporaryDirectory() as tmp:
            outside = os.path.join(tmp, "somewhere", "my_chart.json")
            os.makedirs(os.path.dirname(outside))
            with open(source, "r", encoding="utf-8") as src, open(
                outside, "w", encoding="utf-8"
            ) as dst:
                dst.write(src.read())
            app.paths = []
            app.selected = 0
            self.assertTrue(app.open_external_chart(f'"{outside}"'), "带引号的路径也要认")
            self.assertEqual(app.state, "loading")
            self.assertIn(outside, app.paths, "外部谱面要留在列表里")
            self.assertEqual(app.selected, 0, "外部谱面打开后应被选中（最近打开排最前）")
            self.assertTrue(os.path.isabs(app._display_path(outside)), "仓库外显示绝对路径")
            app.state = "menu"
            self.assertFalse(app.open_external_chart(os.path.join(tmp, "none.json")))
            self.assertIn("找不到文件", app.message)
            self.assertIsNone(app._native_open_dialog(), "无显示 / 测试环境不弹系统对话框")
            app._draw_menu()


if __name__ == "__main__":
    unittest.main()

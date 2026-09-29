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


if __name__ == "__main__":
    unittest.main()

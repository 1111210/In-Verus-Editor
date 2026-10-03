"""演示模式的离屏软渲染：与游玩器的软件绘制共用同一份代码。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:
    import pygame
except Exception:  # pragma: no cover - 没有 pygame
    pygame = None  # type: ignore[assignment]

from rhythm_editor.demo import build_demo_chart


@unittest.skipUnless(pygame is not None, "需要 pygame")
class SoftwareViewTests(unittest.TestCase):
    def test_frame_matches_player_software_sequence(self) -> None:
        """softview.draw_frame 就是游玩器的软件绘制序列（同一份代码）。"""
        from rhythm_editor.game import softview
        from rhythm_editor.game.app import BG

        chart = build_demo_chart()
        view = softview.create_software_view(640, 480)
        now = chart.timing.beat_to_song_ms(4.0)
        frame = softview.draw_frame(view, chart, now, rate=1.0)
        first = pygame.image.tobytes(frame, "RGB")
        # 按游玩器 _draw_play 的软件序列重画同一帧，应当逐像素一致；
        # 演示视图不画鼠标指针（游玩器那条序列里的 _draw_mouse_marker 不在这里）
        view.screen.fill(BG)
        view._draw_planes(now)
        view._draw_notes(now)
        self.assertEqual(first, pygame.image.tobytes(view.screen, "RGB"))

    def test_frame_has_content_and_size(self) -> None:
        from rhythm_editor.game import softview

        chart = build_demo_chart()
        view = softview.create_software_view(320, 240)
        now = chart.timing.beat_to_song_ms(4.0)
        frame = softview.draw_frame(view, chart, now)
        self.assertEqual(frame.get_size(), (320, 240))
        raw = pygame.image.tobytes(frame, "RGB")
        self.assertGreater(max(raw), 100, "画面里应当有判定线 / 物件等亮像素")

    def test_mouse_position_does_not_change_frame(self) -> None:
        """演示视图不渲染鼠标指针：鼠标位置变化不影响画面。"""
        from rhythm_editor.game import softview

        chart = build_demo_chart()
        view = softview.create_software_view(480, 360)
        now = chart.timing.beat_to_song_ms(2.0)
        view.mouse_norm, view.mouse_raw = 0.1, 0.1
        left = pygame.image.tobytes(softview.draw_frame(view, chart, now), "RGB")
        view.mouse_norm, view.mouse_raw = 0.9, 0.9
        right = pygame.image.tobytes(softview.draw_frame(view, chart, now), "RGB")
        self.assertEqual(left, right, "鼠标位置不应改变演示画面（没有指针）")

    def test_resize_keeps_renderable(self) -> None:
        from rhythm_editor.game import softview

        chart = build_demo_chart()
        view = softview.create_software_view(320, 240)
        softview.resize(view, 400, 300)
        self.assertEqual(view.screen.get_size(), (400, 300))
        frame = softview.draw_frame(view, chart, chart.timing.beat_to_song_ms(2.0))
        self.assertEqual(frame.get_size(), (400, 300))

    def test_demo_view_follows_editor_settings_not_player_json(self) -> None:
        """演示视图跟随编辑器自己的演示视图设置（与 player.json 独立）。"""
        import json
        import os
        import tempfile

        from rhythm_editor.game import softview
        from rhythm_editor.game import settings as psettings
        from rhythm_editor.model.chart import EditorSettings

        editor_settings = EditorSettings(
            demo_note_thickness=0.9,
            demo_multi_thickness=0.8,
            demo_slide_thickness=0.2,
            demo_click_thickness=0.1,
            demo_speed=2.0,
            demo_side_lane_highlight=True,
        )
        view_settings = softview.view_settings_for(editor_settings)
        self.assertEqual(view_settings.renderer, "software", "演示视图只支持软件渲染")
        self.assertAlmostEqual(view_settings.note_thickness, 0.9)
        self.assertAlmostEqual(view_settings.multi_thickness, 0.8)
        self.assertAlmostEqual(view_settings.slide_thickness, 0.2)
        self.assertAlmostEqual(view_settings.click_thickness, 0.1)
        self.assertAlmostEqual(view_settings.speed, 2.0)
        self.assertTrue(view_settings.side_lane_highlight)

        # 即使 player.json 里是另一套值，演示视图也用编辑器这一份
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "player.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"note_thickness": 0.05, "speed": 8.0}, handle)
            old = os.environ.get(psettings.ENV_PLAYER_SETTINGS)
            os.environ[psettings.ENV_PLAYER_SETTINGS] = path
            try:
                view = softview.create_software_view(320, 240, view_settings=view_settings)
            finally:
                if old is None:
                    os.environ.pop(psettings.ENV_PLAYER_SETTINGS, None)
                else:
                    os.environ[psettings.ENV_PLAYER_SETTINGS] = old
        self.assertAlmostEqual(view.settings.note_thickness, 0.9)
        self.assertAlmostEqual(view.settings.speed, 2.0)


if __name__ == "__main__":
    unittest.main()

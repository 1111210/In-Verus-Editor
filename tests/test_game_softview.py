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
        frame = softview.draw_frame(view, chart, now, rate=1.0, mouse_norm=0.3)
        first = pygame.image.tobytes(frame, "RGB")
        # 按游玩器 _draw_play 的软件序列重画同一帧，应当逐像素一致
        view.screen.fill(BG)
        view._draw_planes(now)
        view._draw_notes(now)
        view._draw_mouse_marker()
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

    def test_mouse_position_moves_marker(self) -> None:
        from rhythm_editor.game import softview

        chart = build_demo_chart()
        view = softview.create_software_view(480, 360)
        now = chart.timing.beat_to_song_ms(2.0)
        left = pygame.image.tobytes(
            softview.draw_frame(view, chart, now, mouse_norm=0.1), "RGB"
        )
        right = pygame.image.tobytes(
            softview.draw_frame(view, chart, now, mouse_norm=0.9), "RGB"
        )
        self.assertNotEqual(left, right, "鼠标指针应当跟随归一化位置移动")

    def test_resize_keeps_renderable(self) -> None:
        from rhythm_editor.game import softview

        chart = build_demo_chart()
        view = softview.create_software_view(320, 240)
        softview.resize(view, 400, 300)
        self.assertEqual(view.screen.get_size(), (400, 300))
        frame = softview.draw_frame(view, chart, chart.timing.beat_to_song_ms(2.0))
        self.assertEqual(frame.get_size(), (400, 300))

    def test_mouse_norm_mapping(self) -> None:
        """屏幕横坐标 → 归一化位置：与游玩器的映射规则一致。"""
        from rhythm_editor.game import softview
        from rhythm_editor.game.scene import (
            MOUSE_LEFT,
            MOUSE_LEGAL_WIDTH,
            MOUSE_PLANE_HEIGHT,
        )

        view = softview.create_software_view(480, 360)
        view.settings.mouse_sensitivity = 1.0
        view.settings.unbounded_mouse = False
        left, _, _ = view.scene.point(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0, 480, 360)
        right, _, _ = view.scene.point(
            MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0, 480, 360
        )
        norm, raw = softview.mouse_norm_at(view, left)
        self.assertAlmostEqual(norm, 0.0, places=6)
        self.assertAlmostEqual(raw, 0.0, places=6)
        norm, _raw = softview.mouse_norm_at(view, right)
        self.assertAlmostEqual(norm, 1.0, places=6)
        # 出界：关闭无界鼠标时夹进 0..1，raw 保留真实比例
        norm, raw = softview.mouse_norm_at(view, right + (right - left))
        self.assertAlmostEqual(norm, 1.0, places=6)
        self.assertAlmostEqual(raw, 2.0, places=6)


if __name__ == "__main__":
    unittest.main()

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

    def test_mouse_pattern_images_are_centered_on_the_mouse_plane(self) -> None:
        """滑键 / 鼠标点击图案的**图片中心**都在鼠标运动平面上。

        点击图案的内容占满整张图（可见部分上下各一半）；滑键贴图是双倍高
        （上半楔形、下半透明），所以可见的楔形依旧整条落在平面上方。
        """
        from rhythm_editor.game import softview
        from rhythm_editor.game import frame as game_frame
        from rhythm_editor.game.scene import MOUSE_PLANE_HEIGHT
        from rhythm_editor.model.chart import Chart, EditorSettings
        from rhythm_editor.model.notes import MouseClick, MouseSlide
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        chart = Chart()
        chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])
        chart.add(MouseClick("C1", 4.0, 0.5, 0.2))
        chart.add(MouseSlide("S1", 4.0, 0.2, 0.8))
        now = chart.timing.beat_to_song_ms(4.0)
        settings = EditorSettings(demo_click_thickness=0.2, demo_slide_thickness=0.2)
        view = softview.create_software_view(
            640, 480, view_settings=softview.view_settings_for(settings)
        )
        captured: list = []
        original = view._blit_tex_quad
        view._blit_tex_quad = lambda name, quad, **kwargs: (
            captured.append((name, list(quad))),
            True,
        )[1]
        try:
            softview.draw_frame(view, chart, now, rate=1.0)
        finally:
            view._blit_tex_quad = original

        plane_y = view._proj(0.0, MOUSE_PLANE_HEIGHT, 0.0)[1]
        names = ("click_range", "click_diamond", "slide_body_right", "slide_arrow_right")
        drawn = [entry for entry in captured if entry[0] in names]
        self.assertEqual(
            {name for name, _quad in drawn}, set(names), "应当画出点击范围 / 菱形 / 滑键"
        )
        slide_height = game_frame.slide_center(settings.demo_slide_thickness)[1]
        self.assertAlmostEqual(
            slide_height, 2.0 * game_frame.pattern_height(settings.demo_slide_thickness),
            msg="滑键贴图应当是两倍高（上半内容 / 下半透明）",
        )
        self.assertAlmostEqual(
            game_frame.click_center(settings.demo_click_thickness)[0], MOUSE_PLANE_HEIGHT
        )
        self.assertAlmostEqual(
            game_frame.slide_center(settings.demo_slide_thickness)[0], MOUSE_PLANE_HEIGHT
        )
        for name, quad in drawn:
            ys = [point[1] for point in quad]
            self.assertLess(min(ys), plane_y - 1.0, f"{name} 应当伸到平面上方")
            self.assertGreater(max(ys), plane_y + 1.0, f"{name} 应当伸到平面下方")
            self.assertAlmostEqual(
                (min(ys) + max(ys)) / 2.0, plane_y, delta=3.0,
                msg=f"{name} 的图片中心应当落在鼠标运动平面上",
            )
        heights = {name: max(point[1] for point in quad) - min(point[1] for point in quad)
                   for name, quad in drawn}
        self.assertLess(heights["click_range"], heights["slide_body_right"],
                        "滑键贴图比点击图案高一倍（下半是透明填充）")

    def test_slide_texture_content_stays_above_the_mouse_plane(self) -> None:
        """滑键贴图下半透明：可见楔形（含箭头）依旧整条在平面上方。"""
        import numpy as np

        from rhythm_editor.game import softview
        from rhythm_editor.game import frame as game_frame
        from rhythm_editor.game.scene import MOUSE_PLANE_HEIGHT
        from rhythm_editor.model.chart import Chart, EditorSettings
        from rhythm_editor.model.notes import MouseSlide
        from rhythm_editor.model.timing import BpmSegment, TimingMap

        chart = Chart()
        chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])
        chart.add(MouseSlide("S1", 4.0, 0.15, 0.85))
        now = chart.timing.beat_to_song_ms(4.0)
        settings = EditorSettings(demo_slide_thickness=0.12)
        view = softview.create_software_view(
            960, 720, view_settings=softview.view_settings_for(settings)
        )
        frame = softview.draw_frame(view, chart, now, rate=1.0)
        arr = np.transpose(pygame.surfarray.array3d(frame), (1, 0, 2)).astype(int)
        plane_y = view._proj(0.0, MOUSE_PLANE_HEIGHT, 0.0)[1]
        # 右滑键是绿色：G 明显高于 R / B（鼠标平面、判定线都不是这个颜色）
        wedge = (arr[:, :, 1] > 90) & (arr[:, :, 1] - arr[:, :, 0] > 25) & (
            arr[:, :, 1] - arr[:, :, 2] > 25
        )
        rows = np.flatnonzero(wedge.any(axis=1))
        self.assertTrue(rows.size, "应当画出绿色楔形")
        self.assertLessEqual(int(rows.max()), plane_y + 2.0, "楔形不应低于平面")
        height = game_frame.slide_center(settings.demo_slide_thickness)[1] / 2.0
        apex_y = view._proj(0.0, MOUSE_PLANE_HEIGHT + height, 0.0)[1]
        self.assertGreater(
            plane_y - int(rows.min()), 0.4 * (plane_y - apex_y),
            "楔形应当明显伸到平面上方（接近平面 + 厚度处收尖）",
        )


if __name__ == "__main__":
    unittest.main()

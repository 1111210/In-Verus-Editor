"""演示视图渲染：3D 布局与投影、贴图规格化、软件渲染的可见性与速度组。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pygame  # noqa: E402

from demo import build_demo_chart  # noqa: E402
from preview import frame as frame_lib  # noqa: E402
from preview import scene as S, textures as T  # noqa: E402
from preview import softview  # noqa: E402
from model.chart import Chart, EditorSettings  # noqa: E402
from model.notes import LaneNote, MouseClick  # noqa: E402
from model.speed import SpeedSegment  # noqa: E402
from model.timing import BpmSegment, TimingMap  # noqa: E402

W, H = 1600, 900
ASSETS = os.path.join(ROOT, "assets", "textures", "preview")


class LayoutTests(unittest.TestCase):
    def test_units_match_editor(self) -> None:
        """鼠标合法区宽 = 6K 中间四轨 = 1 单位；世界 x 只差一个平移。"""
        self.assertAlmostEqual(S.MOUSE_LEGAL_WIDTH, S.UNIT)
        middle_four = S.PreviewScene.lane_span(1, 4)
        self.assertAlmostEqual(S.MOUSE_LEFT, middle_four[0])
        self.assertAlmostEqual(S.MOUSE_RIGHT, middle_four[1])
        self.assertAlmostEqual(S.K6_WIDTH, S.LANE_COUNT * S.LANE_WIDTH)
        self.assertAlmostEqual(S.PreviewScene.mouse_x(0.0), -0.5)
        self.assertAlmostEqual(S.PreviewScene.mouse_x(1.0), 0.5)
        self.assertAlmostEqual(S.LANE_WIDTH, 0.25)
        self.assertAlmostEqual(S.PreviewScene.note_z(0.0), S.START_Z)
        self.assertAlmostEqual(S.PreviewScene.note_z(1.0), 0.0)
        self.assertGreater(S.MOUSE_PLANE_HEIGHT, 0.0)

    def test_hold_render_width_matches_tap(self) -> None:
        for start, end in ((2, 2), (1, 4), (0, 5)):
            tap = frame_lib.lane_shape(LaneNote("T", start, end, 0.0))
            hold = frame_lib.lane_shape(LaneNote("H", start, end, 0.0, 4.0))
            tap_width = tap.x1 - tap.x0
            hold_width = hold.x1 - hold.x0
            self.assertAlmostEqual(hold_width / tap_width, frame_lib.TAP_VISIBLE_FRACTION, places=6)
            self.assertAlmostEqual((hold.x0 + hold.x1) / 2.0, (tap.x0 + tap.x1) / 2.0, places=9)


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scene = S.PreviewScene()

    def test_projection_and_visible_bounds(self) -> None:
        lines = self.scene.judgment_lines(W, H)
        self.assertLess(lines["mouse"], lines["k6"], "鼠标判定线在屏幕上更靠上")
        near = self.scene.point(0.0, 0.0, 0.0, W, H)
        far = self.scene.point(0.0, 0.0, S.START_Z, W, H)
        self.assertLess(far[1], near[1], "远处物件显示在屏幕上方")
        self.assertLess(far[2], near[2], "远处物件透视缩放更小")
        z_k6 = self.scene.visible_z_near(0.0, W, H)
        z_mouse = self.scene.visible_z_near(S.MOUSE_PLANE_HEIGHT, W, H)
        self.assertLess(z_k6, 0.0, "可视下界在判定平面之前")
        self.assertLess(z_mouse, z_k6, "鼠标平面离镜头更近，可视下界更靠前")
        for y, z_near in ((0.0, z_k6), (S.MOUSE_PLANE_HEIGHT, z_mouse)):
            self.assertLessEqual(self.scene.point(0.0, y, z_near, W, H)[1], H + 0.5)
            self.assertGreater(self.scene.point(0.0, y, z_near - 0.02, W, H)[1], H, "再近就掉出画面")

    def test_field_fills_window_and_stretches_on_wide_screens(self) -> None:
        scene = self.scene
        points = [scene.point(*corner, W, H) for corner in scene._field_corners()]
        ys = [p[1] for p in points]
        self.assertGreater(max(ys) - min(ys), H * 0.85, "竖直方向要铺满窗口")
        for width, height in ((1920, 1080), (1296, 839), (1600, 900)):
            xs = [scene.point(*corner, width, height)[0] for corner in scene._field_corners()]
            self.assertAlmostEqual(max(xs) - min(xs), width - 2 * scene.FIT_MARGIN_X, delta=1.0)
            self.assertAlmostEqual((min(xs) + max(xs)) / 2.0, width / 2.0, delta=1.0)
            self.assertGreater(scene.horizontal_stretch(width, height), 1.05)
        self.assertAlmostEqual(scene.horizontal_stretch(960, 720), 1.0, delta=1e-6, msg="4:3 不拉伸")


class TextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        pygame.init()

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def test_expected_names_cover_all_assets(self) -> None:
        sizes = T.expected_sizes()
        for name in (
            "tap_1", "hold_head_1", "hold_body_6", "region_fill", "region_edge", "region_line",
            "region_area", "slide_body_left", "click_diamond", "click_range", "click_edge",
            "lane_1_default", "lane_6_pressed", "lane_edge_left_highlight_pressed",
            "mouse_marker_outline",
        ):
            self.assertIn(name, sizes)
        self.assertEqual(len(sizes), 47)

    def test_loader_averages_stretches_and_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            surface = pygame.Surface((4, 4), pygame.SRCALPHA)
            surface.fill((10, 20, 30, 40))
            pygame.image.save(surface, os.path.join(tmp, "region_fill.png"))
            loaded = T.load_textures(tmp, names=["region_fill"])
            color = loaded["region_fill"].get_at((0, 0))
            self.assertEqual(loaded["region_fill"].get_size(), (1, 1))
            self.assertEqual(tuple(color)[:3], (10, 20, 30))
            stretched = pygame.Surface((10, 10), pygame.SRCALPHA)
            stretched.fill((200, 100, 50, 255))
            pygame.image.save(stretched, os.path.join(tmp, "click_diamond.png"))
            loaded = T.load_textures(tmp, names=["click_diamond", "tap_2"])
            self.assertEqual(loaded["click_diamond"].get_size(), T.EXPECTED_CLICK_DIAMOND)
            self.assertEqual(loaded["tap_2"].get_size(), T.EXPECTED_TAP, "缺失文件用占位图")
            with open(os.path.join(tmp, "tap_2.png"), "w", encoding="utf-8") as handle:
                handle.write("not a png")
            self.assertEqual(T.load_textures(tmp, names=["tap_2"])["tap_2"].get_size(), T.EXPECTED_TAP)

    def test_real_assets_have_expected_geometry(self) -> None:
        if not os.path.isdir(ASSETS):
            self.skipTest("缺少贴图目录（先跑 tools/make_preview_textures.py）")
        loaded = T.load_textures(ASSETS)
        for name, expected in T.expected_sizes().items():
            self.assertEqual(loaded[name].get_size(), expected, name)
        for name in ("slide_body_right", "slide_body_left", "slide_arrow_right", "slide_arrow_left"):
            surface = loaded[name]
            width, height = surface.get_size()
            half = height // 2
            self.assertEqual(height % 2, 0, name)
            bottom = [surface.get_at((x, y))[3] for y in range(half, height) for x in range(0, width, 4)]
            self.assertEqual(max(bottom), 0, f"{name} 下半必须完全透明")
        measured = {}
        for name in ("hold_head_1", "hold_body_1"):
            surface = loaded[name]
            width, height = surface.get_size()
            row = height // 2
            white = [x for x in range(width) if min(surface.get_at((x, row))[:3]) >= 250]
            measured[name] = len(white) / width
            self.assertAlmostEqual(measured[name], T.HOLD_LINE_FRACTION, delta=1.0 / width + 1e-6)
        self.assertAlmostEqual(measured["hold_head_1"], measured["hold_body_1"], delta=1.0 / 32 + 1e-6)


@unittest.skipUnless(pygame is not None, "需要 pygame")
class SoftwareViewTests(unittest.TestCase):
    def test_frame_matches_draw_sequence(self) -> None:
        chart = build_demo_chart()
        view = softview.create_software_view(640, 480)
        now = chart.timing.beat_to_song_ms(4.0)
        frame = softview.draw_frame(view, chart, now, rate=1.0)
        first = pygame.image.tobytes(frame, "RGB")
        view.screen.fill(softview.BG)
        view._draw_planes(now)
        view._draw_notes(now)
        self.assertEqual(first, pygame.image.tobytes(view.screen, "RGB"))

    def test_resize_keeps_rendering(self) -> None:
        chart = build_demo_chart()
        view = softview.create_software_view(480, 360)
        now = chart.timing.beat_to_song_ms(2.0)
        softview.resize(view, 800, 500)
        frame = softview.draw_frame(view, chart, now)
        self.assertEqual(frame.get_size(), (800, 500))

    def test_reverse_notes_draw_in_front_of_the_judgement_line(self) -> None:
        import numpy as np

        from preview.scene import MOUSE_PLANE_HEIGHT

        chart = Chart()
        chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])
        reversed_group = chart.add_group("rev", [SpeedSegment(0.0, -1.0)])
        chart.add(MouseClick("M", 6.0, 0.5, 0.2, group=reversed_group.id))
        judge_ms = chart.timing.beat_to_song_ms(6.0)
        blank_chart = Chart()
        blank_chart.timing = chart.timing

        def render(target, now):
            view = softview.create_software_view(
                640, 480, view_settings=softview.view_settings_for(EditorSettings())
            )
            frame = softview.draw_frame(view, target, now, rate=1.0)
            return view, np.transpose(pygame.surfarray.array3d(frame), (1, 0, 2)).astype(int)

        view, _blank = render(blank_chart, judge_ms - 20.0)
        line_y = view._proj(0.0, MOUSE_PLANE_HEIGHT, 0.0)[1]
        _view, near = render(chart, judge_ms - 20.0)
        blank = render(blank_chart, judge_ms - 20.0)[1]
        rows = np.flatnonzero(np.abs(near - blank).sum(axis=2).any(axis=1))
        self.assertTrue(rows.size, "反向点击图案应当被画出来")
        self.assertGreater(int(rows.max()), line_y + 1.0, "图案落在判定线之前（更靠近镜头）")
        _view, far = render(chart, judge_ms - 100.0)
        blank_far = render(blank_chart, judge_ms - 100.0)[1]
        self.assertFalse(np.abs(far - blank_far).sum(axis=2).any(), "超出可视下界后应当剔除")

    def test_demo_view_applies_speed_groups(self) -> None:
        """演示视图跟随速度组：同判定时刻，流速不同位置不同。

        回归：以前位置只按歌曲时间差算，速度组在演示视图里完全不生效。
        """
        import numpy as np

        def render(multiplier):
            chart = Chart()
            chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])   # 每拍 500ms
            if multiplier is not None:
                gid = 0
                if abs(multiplier - 1.0) > 1e-9:
                    gid = chart.add_group("g", [SpeedSegment(0.0, multiplier)]).id
                chart.add(LaneNote("A", 0, 0, 4.0, group=gid))
            now = chart.timing.beat_to_song_ms(3.0)
            view = softview.create_software_view(
                640, 480, view_settings=softview.view_settings_for(EditorSettings())
            )
            frame = softview.draw_frame(view, chart, now, rate=1.0)
            return np.transpose(pygame.surfarray.array3d(frame), (1, 0, 2)).astype(int)

        blank = render(None)

        def note_rows(multiplier) -> "np.ndarray":
            diff = np.abs(render(multiplier) - blank).sum(axis=2)
            return np.flatnonzero(diff.any(axis=1))

        rows_slow = note_rows(0.5)
        rows_normal = note_rows(1.0)
        rows_fast = note_rows(2.0)
        for name, rows in (("0.5x", rows_slow), ("1x", rows_normal), ("2x", rows_fast)):
            self.assertTrue(rows.size, f"{name} 组的物件应当被画出来")
        # 透视下越远越靠屏幕上方：2x 更远、0.5x 更近
        self.assertLess(int(rows_fast.min()), int(rows_normal.min()), "2x 组应当更远")
        self.assertGreater(int(rows_slow.min()), int(rows_normal.min()), "0.5x 组应当更近")


if __name__ == "__main__":
    unittest.main()

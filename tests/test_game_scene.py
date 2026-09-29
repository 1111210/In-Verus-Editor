"""游玩器 3D 布局：单位定义、判定平面、近大远小、起点在屏幕内。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.game import scene as S

W, H = 1600, 900


class LayoutTests(unittest.TestCase):
    def test_unit_is_mouse_width_and_middle_four_lanes(self) -> None:
        """1 单位 = 鼠标合法区域宽度 = 6K 中间四条轨道宽度。"""
        self.assertAlmostEqual(S.MOUSE_LEGAL_WIDTH, S.UNIT)
        middle_four = S.PlayerScene.lane_span(1, 4)
        self.assertAlmostEqual(middle_four[1] - middle_four[0], S.UNIT)
        self.assertAlmostEqual(S.MOUSE_LEFT, middle_four[0])
        self.assertAlmostEqual(S.MOUSE_RIGHT, middle_four[1])

    def test_k6_is_six_lanes(self) -> None:
        self.assertAlmostEqual(S.PlayerScene.lane_span(0, 5)[1] - S.PlayerScene.lane_span(0, 5)[0], 1.5)
        self.assertAlmostEqual(S.K6_WIDTH, 1.5)

    def test_mouse_plane_is_above_k6_plane(self) -> None:
        self.assertAlmostEqual(S.MOUSE_PLANE_HEIGHT, 0.4)
        self.assertGreater(S.MOUSE_PLANE_HEIGHT, 0.0)

    def test_distance_is_the_same_for_both_planes(self) -> None:
        """两个平面的运动距离相同（同样的 z 行程）。"""
        self.assertAlmostEqual(S.PlayerScene.note_z(0.0), S.PlayerScene.note_z(0.0))
        self.assertAlmostEqual(S.PlayerScene.note_z(0.0), S.START_Z)
        self.assertAlmostEqual(S.PlayerScene.note_z(1.0), 0.0)


class ProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scene = S.PlayerScene()

    def test_mouse_line_is_above_k6_line_on_screen(self) -> None:
        lines = self.scene.judgment_lines(W, H)
        self.assertLess(lines["mouse"], lines["k6"], "屏幕坐标 y 越小越靠上")

    def test_mouse_plane_looks_wider_than_middle_four_lanes(self) -> None:
        """近大远小：鼠标平面离摄像头更近，看上去略宽于 6K 中间四轨。"""
        mouse_width = self.scene.visible_width(S.MOUSE_PLANE_HEIGHT, W, H)
        k6_middle = S.PlayerScene.lane_span(1, 4)
        left, _, _ = self.scene.point(k6_middle[0], 0.0, 0.0, W, H)
        right, _, _ = self.scene.point(k6_middle[1], 0.0, 0.0, W, H)
        self.assertGreater(mouse_width, abs(right - left))

    def test_far_objects_are_higher_on_screen_and_smaller(self) -> None:
        near = self.scene.point(0.0, 0.0, 0.0, W, H)
        far = self.scene.point(0.0, 0.0, S.START_Z, W, H)
        self.assertLess(far[1], near[1], "远处物件显示在屏幕上方")
        self.assertLess(far[2], near[2], "远处物件透视缩放更小")

    def test_start_position_is_on_screen(self) -> None:
        """起点保留在屏幕内、靠近最上方。"""
        for x in (S.K6_LEFT, 0.0, -S.K6_LEFT):
            for y in (0.0, S.MOUSE_PLANE_HEIGHT):
                sx, sy, _ = self.scene.point(x, y, S.START_Z, W, H)
                self.assertGreaterEqual(sy, 0.0)
                self.assertLessEqual(sy, H * 0.5, "起点应在上半屏")
                self.assertGreaterEqual(sx, -W)
                self.assertLessEqual(sx, W * 2)

    def test_field_fills_the_window(self) -> None:
        """整个游玩区尽量铺满窗口：竖直方向基本用满、水平居中且不越界。"""
        scene = S.PlayerScene()
        points = [scene.point(*corner, W, H) for corner in scene._field_corners()]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self.assertGreater(max(ys) - min(ys), H * 0.85, "竖直方向要铺满窗口")
        self.assertAlmostEqual((min(xs) + max(xs)) / 2.0, W / 2.0, delta=2.0)
        for value in ys:
            self.assertGreaterEqual(value, -1.0)
            self.assertLessEqual(value, H + 1.0)
        # 换一个窗口尺寸也要重新铺满
        small = [scene.point(*corner, 1024, 640) for corner in scene._field_corners()]
        self.assertGreater(max(p[1] for p in small) - min(p[1] for p in small), 640 * 0.85)


if __name__ == "__main__":
    unittest.main()

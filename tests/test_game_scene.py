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
        """鼠标合法区域宽度 = 6K 中间四条轨道宽度 = 1 单位（与编辑器一致）。"""
        self.assertAlmostEqual(S.MOUSE_LEGAL_WIDTH, S.UNIT)
        self.assertAlmostEqual(S.MOUSE_LEGAL_WIDTH, 1.0)
        middle_four = S.PlayerScene.lane_span(1, 4)
        self.assertAlmostEqual(middle_four[1] - middle_four[0], S.MOUSE_LEGAL_WIDTH)
        self.assertAlmostEqual(S.MOUSE_LEFT, middle_four[0])
        self.assertAlmostEqual(S.MOUSE_RIGHT, middle_four[1])

    def test_k6_is_six_lanes(self) -> None:
        span = S.PlayerScene.lane_span(0, 5)
        self.assertAlmostEqual(span[1] - span[0], S.LANE_COUNT * S.LANE_WIDTH)
        self.assertAlmostEqual(S.K6_WIDTH, S.LANE_COUNT * S.LANE_WIDTH)

    def test_mouse_plane_is_above_k6_plane(self) -> None:
        # 0.4 是旧单位下的高度；换算到「合法区域宽 = 1」的新单位后是 0.4 × 5/7
        self.assertAlmostEqual(S.MOUSE_PLANE_HEIGHT, 0.4 * S.UNIT_SCALE)
        self.assertGreater(S.MOUSE_PLANE_HEIGHT, 0.0)

    def test_distance_is_the_same_for_both_planes(self) -> None:
        """两个平面的运动距离相同（同样的 z 行程）。"""
        self.assertAlmostEqual(S.PlayerScene.note_z(0.0), S.PlayerScene.note_z(0.0))
        self.assertAlmostEqual(S.PlayerScene.note_z(0.0), S.START_Z)
        self.assertAlmostEqual(S.PlayerScene.note_z(1.0), 0.0)

    def test_world_x_matches_editor_normalized_units(self) -> None:
        """世界 x 与编辑器的 0..1 归一化坐标只差一个平移（合法区宽 = 1）。"""
        self.assertAlmostEqual(S.PlayerScene.mouse_x(0.0), -0.5)
        self.assertAlmostEqual(S.PlayerScene.mouse_x(0.5), 0.0)
        self.assertAlmostEqual(S.PlayerScene.mouse_x(1.0), 0.5)
        self.assertAlmostEqual(S.LANE_WIDTH, 0.25)
        self.assertAlmostEqual(S.K6_WIDTH, 1.5)


class ScaleInvarianceTests(unittest.TestCase):
    """世界坐标（连同相机）整体缩小 1.4 倍时，屏幕上的像**完全不变**。

    这是本次改单位（合法区宽 1.4 -> 1）的关键约定：scene.py 里所有历史调参
    （轨道长度 / 相机位置 / 平面高度 …）都乘 ``UNIT_SCALE = 5/7``，等价于把
    整个世界做一个相似变换，所以游玩器画面与旧世界逐像素一致。
    """

    # 旧世界（合法区宽 1.4）下的取值
    OLD = {
        "LANE_WIDTH": 0.35,
        "K6_WIDTH": 2.1,
        "K6_LEFT": -1.05,
        "MOUSE_LEFT": -0.7,
        "MOUSE_RIGHT": 0.7,
        "MOUSE_LEGAL_WIDTH": 1.4,
        "MOUSE_PLANE_HEIGHT": 0.4,
        "NOTE_HEIGHT_UNIT": 0.03,
        "START_Z": 26.0,
    }

    @staticmethod
    def _corners():
        """旧单位下的游玩区角点（左右 × 上下 × 远近）。"""
        return [
            (x, y, z)
            for x in (-1.05, 0.0, 1.05)
            for y in (0.0, 0.4, 0.43)
            for z in (0.0, 26.0)
        ]

    def test_projection_matches_the_old_world(self) -> None:
        saved = {name: getattr(S, name) for name in self.OLD}
        try:
            for name, value in self.OLD.items():
                setattr(S, name, value)
            old = S.PlayerScene(S.Camera(y=1.60, z=-2.20, pitch_deg=16.0, fov_deg=55.0))
            old_points = [old.point(x, y, z, W, H) for x, y, z in self._corners()]
        finally:
            for name, value in saved.items():
                setattr(S, name, value)
        new = S.PlayerScene()
        new_points = [
            new.point(x * S.UNIT_SCALE, y * S.UNIT_SCALE, z * S.UNIT_SCALE, W, H)
            for x, y, z in self._corners()
        ]
        for (old_x, old_y, old_scale), (new_x, new_y, new_scale) in zip(old_points, new_points):
            self.assertAlmostEqual(old_x, new_x, places=6)
            self.assertAlmostEqual(old_y, new_y, places=6)
            self.assertAlmostEqual(old_scale, new_scale * S.UNIT_SCALE, places=6)


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

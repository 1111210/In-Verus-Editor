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


class FrameShapeTests(unittest.TestCase):
    """game/frame.py 的世界坐标几何：长按宽度要与单键一致。"""

    def test_hold_render_width_matches_tap(self) -> None:
        """长按（含开头）的渲染宽度 = 单键的可见宽度（左右对称内收，中心不变）。"""
        from rhythm_editor.game import frame
        from rhythm_editor.model.notes import LaneNote

        for start, end in ((2, 2), (1, 4), (0, 5)):
            tap = LaneNote("T", start, end, 0.0)
            hold = LaneNote("H", start, end, 0.0, 4.0)
            tap_shape = frame.lane_shape(tap)
            hold_shape = frame.lane_shape(hold)
            tap_width = tap_shape.x1 - tap_shape.x0
            hold_width = hold_shape.x1 - hold_shape.x0
            self.assertLess(hold_width, tap_width, "长按应按单键的可见宽度收窄")
            self.assertAlmostEqual(
                hold_width / tap_width, frame.TAP_VISIBLE_FRACTION, places=6
            )
            self.assertAlmostEqual(
                (hold_shape.x0 + hold_shape.x1) / 2.0,
                (tap_shape.x0 + tap_shape.x1) / 2.0,
                places=9,
                msg="内收必须左右对称",
            )


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

    def test_visible_z_near_is_in_front_of_the_judgement_plane(self) -> None:
        """可视下界估算：两个平面都在判定平面（z=0）之前，鼠标平面能更靠近镜头。

        反向流速的物件会走到判定线更靠近摄像头的一侧，只要还在这个下界之内就
        应当画出来；再靠近就会掉出画面下缘。
        """
        z_k6 = self.scene.visible_z_near(0.0, W, H)
        z_mouse = self.scene.visible_z_near(S.MOUSE_PLANE_HEIGHT, W, H)
        self.assertLess(z_k6, 0.0, "6K 平面的可视下界在判定平面之前")
        self.assertLess(z_mouse, z_k6, "鼠标平面离镜头更近，可视下界更靠前")
        for y, z_near in ((0.0, z_k6), (S.MOUSE_PLANE_HEIGHT, z_mouse)):
            inside = self.scene.point(0.0, y, z_near, W, H)[1]
            outside = self.scene.point(0.0, y, z_near - 0.02, W, H)[1]
            self.assertLessEqual(inside, H + 0.5, "可视下界正好落在画面下缘")
            self.assertGreater(outside, H, "再靠近镜头就会掉出画面")
        # 结果按窗口尺寸缓存：同样尺寸重复调用一致
        self.assertAlmostEqual(
            self.scene.visible_z_near(0.0, W, H), z_k6, places=9
        )
        small = self.scene.visible_z_near(0.0, 1024, 640)
        self.assertLess(small, 0.0)

    def test_start_position_is_on_screen(self) -> None:
        """起点保留在屏幕内、靠近最上方。"""
        for x in (S.K6_LEFT, 0.0, -S.K6_LEFT):
            for y in (0.0, S.MOUSE_PLANE_HEIGHT):
                sx, sy, _ = self.scene.point(x, y, S.START_Z, W, H)
                self.assertGreaterEqual(sy, 0.0)
                self.assertLessEqual(sy, H * 0.5, "起点应在上半屏")
                self.assertGreaterEqual(sx, -W)
                self.assertLessEqual(sx, W * 2)

    def test_wide_window_stretches_the_field_horizontally(self) -> None:
        """宽窗口下把游玩区**横向拉伸**铺满可用宽度：6K 平面接近窗口左右边缘。

        竖直方向仍按整体比例（不上下变形），所以 4:3 等本来就铺满宽度的窗口
        不会被额外拉伸。
        """
        scene = self.scene
        for width, height in ((1920, 1080), (1296, 839), (1600, 900)):
            points = [scene.point(*corner, width, height) for corner in scene._field_corners()]
            xs = [point[0] for point in points]
            usable = width - 2 * S.PlayerScene.FIT_MARGIN_X
            self.assertAlmostEqual(
                max(xs) - min(xs), usable, delta=1.0,
                msg=f"{width}x{height}: 游玩区应当铺满可用宽度",
            )
            self.assertAlmostEqual((min(xs) + max(xs)) / 2.0, width / 2.0, delta=1.0)
            self.assertGreater(scene.horizontal_stretch(width, height), 1.05)
            # 6K 平面本身也要接近窗口左右边缘
            k6 = [scene.point(x, 0.0, 0.0, width, height)[0]
                  for x in (S.K6_LEFT, -S.K6_LEFT)]
            self.assertGreater(abs(k6[1] - k6[0]) / width, 0.9)
        # 4:3：本来就铺满宽度，不额外拉伸
        self.assertAlmostEqual(scene.horizontal_stretch(960, 720), 1.0, delta=1e-6)
        # 竖直方向不能被拉伸：远近比例与原来一致（整体比例缩放）
        near = scene.point(0.0, 0.0, 0.0, 1920, 1080)
        far = scene.point(0.0, 0.0, S.START_Z, 1920, 1080)
        self.assertLess(far[1], near[1])
        self.assertLess(far[2], near[2])

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

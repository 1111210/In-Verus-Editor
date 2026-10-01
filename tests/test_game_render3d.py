"""Panda3D 渲染层的几何与打包规则（不需要 GL 上下文）。"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor.game import render3d  # noqa: E402


@unittest.skipUnless(render3d.available(), "没有 Panda3D")
class TextureWrapTests(unittest.TestCase):
    """贴图必须夹边：Panda 默认 WM_repeat，会让卡片边缘混进贴图**对边**的颜色。"""

    def test_textures_are_clamped(self) -> None:
        from panda3d.core import Texture

        directory = os.path.join(ROOT, "assets", "textures", "player")
        if not os.path.isdir(directory):
            self.skipTest("缺少贴图目录（先跑 tools/make_player_textures.py）")
        scene = render3d.Scene3D(320, 240, directory)
        try:
            for name in ("hold_head_1", "hold_body_1", "tap_1", "region_area"):
                texture = scene.texture(name)
                self.assertIsNotNone(texture, name)
                self.assertEqual(texture.getWrapU(), Texture.WMClamp, name)
                self.assertEqual(texture.getWrapV(), Texture.WMClamp, name)
        finally:
            scene.close()


@unittest.skipUnless(render3d.available(), "没有 Panda3D")
class QuadCornerTests(unittest.TestCase):
    """批渲染用解析式算角点，必须和 Panda 自己的卡片变换完全一致。"""

    def test_matches_panda_card_transform(self) -> None:
        from panda3d.core import CardMaker, NodePath, Point3

        card = CardMaker("q")
        card.setFrame(-0.5, 0.5, -0.5, 0.5)
        cases = ((0.3, 5.0, 0.4, 0.25, 0.3), (-0.7, 1.5, 0.42, 1.5, 0.12))
        for vertical in (False, True):
            for angle in (0.0, 30.0, 45.0, 180.0):
                for x, z, y, sx, sz in cases:
                    node = NodePath(card.generate())
                    node.setPos(x, z, y)
                    node.setHpr(0.0, 0.0 if vertical else -90.0, angle)
                    node.setScale(sx, 1.0, sz)
                    matrix = node.getMat()
                    corners = render3d.quad_corners(x, z, sx, sz, y, vertical, angle)
                    for (u, v), got in zip(((0, 0), (1, 0), (1, 1), (0, 1)), corners):
                        want = matrix.xform(Point3(u - 0.5, 0.0, v - 0.5))
                        for axis in range(3):
                            self.assertAlmostEqual(
                                want[axis], got[axis], places=6,
                                msg=f"vertical={vertical} angle={angle} uv=({u},{v})",
                            )


if __name__ == "__main__":
    unittest.main()

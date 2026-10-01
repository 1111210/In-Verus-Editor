"""Panda3D 渲染层的几何与打包规则（不需要 GL 上下文）。"""

from __future__ import annotations

import os
import struct
import sys
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor.game import render3d  # noqa: E402


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


@unittest.skipUnless(render3d.available(), "没有 Panda3D")
class QuadBatchTests(unittest.TestCase):
    """一批四边形打包成 6 顶点/张（两个三角形），字节布局 = V3f + T2f。"""

    def test_packs_six_vertices_per_quad(self) -> None:
        from panda3d.core import NodePath

        parent = NodePath("parent")
        batch = render3d.QuadBatch(parent, "batch", None, (1, 1, 1, 1), 30)
        batch.add_quad(0.0, 3.0, 2.0, 1.0, 0.5, False, 0.0)
        batch.add_quad(1.0, 3.0, 0.5, 0.5, 0.4, True, 45.0)
        self.assertEqual(batch.count, 2)
        data = b"".join(batch.parts)
        self.assertEqual(len(data), 2 * 6 * 5 * 4)          # 2 张 × 6 顶点 × (3+2) float
        first = struct.unpack("<30f", batch.parts[0])
        # 第一张是水平卡片：四角在 y=0.5 平面（Panda 的 Z），UV 顺序 (0,0)(1,0)(1,1)(0,1)
        self.assertEqual((first[3], first[4]), (0.0, 0.0))
        self.assertAlmostEqual(first[2], 0.5)
        batch.reset()
        self.assertEqual((batch.count, batch.parts), (0, []))


if __name__ == "__main__":
    unittest.main()

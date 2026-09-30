"""游玩器预设贴图：加载与规格化规则（单像素取平均 / 尺寸拉伸 / 缺失占位）。"""

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

from rhythm_editor.game import textures as T  # noqa: E402


class TextureLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            pygame.init()
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"没有 pygame：{exc}")

    @classmethod
    def tearDownClass(cls) -> None:
        pygame.quit()

    def test_expected_names_cover_all_requested_assets(self) -> None:
        """6 单键 + 12 长按 + 空域 4 + 滑键 4 + 点击 3 + 轨道 12 + 边界 4 + 鼠标指针 2 = 47 张。"""
        sizes = T.expected_sizes()
        for name in (
            "tap_1", "tap_6",
            "hold_head_1", "hold_body_6",
            "region_fill", "region_edge", "region_line", "region_area",
            "slide_arrow_left", "slide_arrow_right", "slide_body_left", "slide_body_right",
            "click_diamond", "click_range", "click_edge",
            "lane_1_default", "lane_6_pressed",
            "lane_edge_left_highlight", "lane_edge_left_highlight_pressed",
            "lane_edge_right_highlight", "lane_edge_right_highlight_pressed",
        ):
            self.assertIn(name, sizes)
        self.assertEqual(len(sizes), 47)

    def test_single_pixel_images_are_averaged(self) -> None:
        """单像素图被改成多像素图时取各个像素平均值。"""
        with tempfile.TemporaryDirectory() as tmp:
            surface = pygame.Surface((4, 4), pygame.SRCALPHA)
            surface.fill((10, 20, 30, 40))
            pygame.image.save(surface, os.path.join(tmp, "region_fill.png"))
            loaded = T.load_textures(tmp, names=["region_fill"])
            color = loaded["region_fill"].get_at((0, 0))
            self.assertEqual(loaded["region_fill"].get_size(), (1, 1))
            self.assertEqual(tuple(color)[:3], (10, 20, 30))
            self.assertAlmostEqual(color[3], 40, delta=2)

    def test_multi_pixel_average_mixes_colors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            surface = pygame.Surface((2, 2), pygame.SRCALPHA)
            surface.set_at((0, 0), (0, 0, 0, 0))
            surface.set_at((1, 0), (200, 200, 200, 200))
            surface.set_at((0, 1), (0, 0, 0, 0))
            surface.set_at((1, 1), (200, 200, 200, 200))
            pygame.image.save(surface, os.path.join(tmp, "region_line.png"))
            loaded = T.load_textures(tmp, names=["region_line"])
            color = loaded["region_line"].get_at((0, 0))
            self.assertEqual(tuple(color)[:3], (100, 100, 100))
            self.assertAlmostEqual(color[3], 100, delta=2)

    def test_other_images_are_stretched_to_expected_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            surface = pygame.Surface((10, 10), pygame.SRCALPHA)
            surface.fill((200, 100, 50, 255))
            pygame.image.save(surface, os.path.join(tmp, "click_diamond.png"))
            loaded = T.load_textures(tmp, names=["click_diamond"])
            self.assertEqual(loaded["click_diamond"].get_size(), T.EXPECTED_CLICK_DIAMOND)

    def test_missing_files_fall_back_to_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = T.load_textures(tmp, names=["tap_1", "region_fill"])
            self.assertEqual(loaded["tap_1"].get_size(), T.EXPECTED_TAP)
            self.assertEqual(loaded["region_fill"].get_size(), (1, 1))

    def test_broken_file_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "tap_2.png"), "w", encoding="utf-8") as handle:
                handle.write("not a png")
            loaded = T.load_textures(tmp, names=["tap_2"])
            self.assertEqual(loaded["tap_2"].get_size(), T.EXPECTED_TAP)

    def test_real_assets_load_with_expected_sizes(self) -> None:
        directory = os.path.join(ROOT, "assets", "textures", "player")
        if not os.path.isdir(directory):
            self.skipTest("缺少贴图目录（先跑 tools/make_player_textures.py）")
        loaded = T.load_textures(directory)
        for name, expected in T.expected_sizes().items():
            self.assertEqual(loaded[name].get_size(), expected, name)


if __name__ == "__main__":
    unittest.main()

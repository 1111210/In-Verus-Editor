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

    def test_slide_textures_put_visible_art_in_top_half(self) -> None:
        """滑键贴图两倍高：上半是可见内容、下半完全透明（图片中心 = 判定线）。"""
        directory = os.path.join(ROOT, "assets", "textures", "player")
        names = ["slide_body_right", "slide_body_left", "slide_arrow_right", "slide_arrow_left"]
        if not os.path.isdir(directory):
            self.skipTest("缺少贴图目录（先跑 tools/make_player_textures.py）")
        loaded = T.load_textures(directory, names=names)
        for name in names:
            surface = loaded[name]
            width, height = surface.get_size()
            self.assertEqual(height % 2, 0, f"{name} 应当是两倍高（上半内容 / 下半透明）")
            half = height // 2
            top = [surface.get_at((x, y))[3] for y in range(half) for x in range(0, width, 4)]
            bottom = [surface.get_at((x, y))[3] for y in range(half, height) for x in range(0, width, 4)]
            self.assertEqual(max(bottom), 0, f"{name} 下半必须完全透明")
            if "body" in name:
                self.assertGreater(max(top), 0, f"{name} 上半应当有楔形")
            else:
                self.assertEqual(max(top), 0, "箭头尚未实装（占位应当全透明）")

    def test_hold_head_and_body_share_one_center_line(self) -> None:
        """长按开头与主体的中心白线必须一样粗（都用 ``HOLD_LINE_FRACTION``）。"""
        directory = os.path.join(ROOT, "assets", "textures", "player")
        names = ["hold_head_1", "hold_body_1"]
        if not os.path.isdir(directory):
            self.skipTest("缺少贴图目录（先跑 tools/make_player_textures.py）")
        loaded = T.load_textures(directory, names=names)
        measured = {}
        for name in names:
            surface = loaded[name]
            width, height = surface.get_size()
            row = height // 2
            white = [x for x in range(width) if min(surface.get_at((x, row))[:3]) >= 250]
            self.assertGreater(len(white), 0, f"{name} 没有中心白线")
            measured[name] = len(white) / width
            # 单像素量化误差内等于规格值（贴图宽度很小，允许 1 像素的误差）
            self.assertAlmostEqual(measured[name], T.HOLD_LINE_FRACTION,
                                   delta=1.0 / width + 1e-6, msg=name)
        self.assertAlmostEqual(measured["hold_head_1"], measured["hold_body_1"],
                               delta=1.0 / 32 + 1e-6, msg="开头与主体的白线粗细要一致")

    def test_tap_visible_width_matches_hold_inset_constant(self) -> None:
        """长按内收比例来自单键贴图的**实际可见宽度**（alpha≥128 的横向范围）。"""
        from rhythm_editor.game import frame

        directory = os.path.join(ROOT, "assets", "textures", "player")
        if not os.path.isdir(directory):
            self.skipTest("缺少贴图目录（先跑 tools/make_player_textures.py）")
        tap = T.load_textures(directory, names=["tap_1"])["tap_1"]
        width, height = tap.get_size()
        row = height - 10               # 靠近判定线的一行：单键在这里最实
        columns = [x for x in range(width) if tap.get_at((x, row))[3] >= 128]
        self.assertTrue(columns, "单键贴图这一行不应全透明")
        visible = (columns[-1] - columns[0] + 1) / width
        self.assertAlmostEqual(
            visible, frame.TAP_VISIBLE_FRACTION, delta=1.0 / width,
            msg="长按内收比例必须与单键贴图的实际可见宽度一致",
        )


if __name__ == "__main__":
    unittest.main()

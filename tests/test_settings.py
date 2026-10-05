"""编辑器的全局设置：存用户目录、不写谱面、损坏时回退默认。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import global_settings
from model.chart import Chart, EditorSettings


class EditorSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "settings.json")
        self._old = os.environ.get(global_settings.ENV_SETTINGS_PATH)
        os.environ[global_settings.ENV_SETTINGS_PATH] = self.path
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        if self._old is None:
            os.environ.pop(global_settings.ENV_SETTINGS_PATH, None)
        else:
            os.environ[global_settings.ENV_SETTINGS_PATH] = self._old

    def test_roundtrip_and_broken_file(self) -> None:
        settings = global_settings.load_settings()
        self.assertEqual(settings.grid_division, 4)      # 没有文件时用默认值
        self.assertEqual(global_settings.settings_path(), self.path)
        settings.grid_division = 8
        settings.zoom_seconds = 6.0
        settings.hit_volume = 0.3
        settings.demo_speed = 2.5
        global_settings.save_settings(settings)
        restored = global_settings.load_settings()
        self.assertEqual((restored.grid_division, restored.zoom_seconds), (8, 6.0))
        self.assertAlmostEqual(restored.hit_volume, 0.3)
        self.assertAlmostEqual(restored.demo_speed, 2.5)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ 这不是 JSON")
        self.assertEqual(global_settings.load_settings().grid_division, 4)

    def test_chart_has_no_editor_section_and_legacy_block_migrates_once(self) -> None:
        chart = Chart()
        chart.settings.grid_division = 16
        data = chart.to_dict()
        self.assertNotIn("editor", data)
        self.assertEqual(Chart.from_dict(data).settings.grid_division, 4)
        settings = EditorSettings()
        moved = global_settings.adopt_legacy_settings(
            settings, {"grid_division": 16, "audio_latency_ms": -120.0, "metronome": "bar"}
        )
        self.assertTrue(moved)
        self.assertEqual((settings.grid_division, settings.metronome), (16, "bar"))
        global_settings.save_settings(settings)
        self.assertFalse(global_settings.adopt_legacy_settings(EditorSettings(), {"grid_division": 2}))


class ViewSettingsTests(unittest.TestCase):
    """演示视图的渲染参数：流速 → 可视毫秒、厚度 / 流速夹取。"""

    def test_speed_maps_to_visible_ms_and_values_are_clamped(self) -> None:
        from preview.settings import BASE_SCROLL_MS, ViewSettings

        self.assertAlmostEqual(ViewSettings(speed=2.0).scroll_ms, BASE_SCROLL_MS / 2.0)
        self.assertAlmostEqual(ViewSettings(speed=3.0).scroll_ms, 500.0)
        settings = ViewSettings(
            speed=99.0, note_thickness=5.0, multi_thickness=0.0,
            slide_thickness=0.0, click_thickness=9.0, rate=0.0,
        )
        settings.normalize()
        self.assertAlmostEqual(settings.speed, 10.0)
        self.assertAlmostEqual(settings.note_thickness, 1.0)
        self.assertAlmostEqual(settings.multi_thickness, 0.05)
        self.assertAlmostEqual(settings.slide_thickness, 0.005)
        self.assertAlmostEqual(settings.click_thickness, 0.3)
        self.assertGreater(settings.rate, 0.0)


if __name__ == "__main__":
    unittest.main()

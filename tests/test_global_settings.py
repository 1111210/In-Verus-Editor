"""全局编辑器设置：存在用户配置目录，不写进谱面文件。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import global_settings
from rhythm_editor.model.chart import Chart, EditorSettings


class GlobalSettingsTests(unittest.TestCase):
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

    def test_save_and_load_roundtrip(self) -> None:
        settings = global_settings.load_settings()
        self.assertEqual(settings.grid_division, 4)      # 没有文件时用默认值
        settings.grid_division = 8
        settings.zoom_seconds = 6.0
        settings.side_lane_highlight = True
        settings.show_past_notes = True
        settings.hit_volume = 0.3
        settings.demo_note_thickness = 0.45
        settings.demo_speed = 2.5
        settings.demo_side_lane_highlight = True
        global_settings.save_settings(settings)
        self.assertTrue(os.path.isfile(self.path))
        restored = global_settings.load_settings()
        self.assertEqual(restored.grid_division, 8)
        self.assertAlmostEqual(restored.zoom_seconds, 6.0)
        self.assertTrue(restored.side_lane_highlight)
        self.assertTrue(restored.show_past_notes)
        self.assertAlmostEqual(restored.hit_volume, 0.3)
        self.assertAlmostEqual(restored.demo_note_thickness, 0.45)
        self.assertAlmostEqual(restored.demo_speed, 2.5)
        self.assertTrue(restored.demo_side_lane_highlight)

    def test_broken_file_falls_back_to_defaults(self) -> None:
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ 这不是 JSON")
        settings = global_settings.load_settings()
        self.assertEqual(settings.grid_division, 4)

    def test_chart_file_has_no_editor_section(self) -> None:
        """视图 / 音效 / 编辑器设置不再写进谱面。"""
        chart = Chart()
        chart.settings.grid_division = 16
        data = chart.to_dict()
        self.assertNotIn("editor", data)
        restored = Chart.from_dict(data)
        self.assertEqual(restored.settings.grid_division, 4)   # 默认值 4

    def test_legacy_chart_editor_block_is_migrated_once(self) -> None:
        """读老谱面时，把里面的 editor 段并入全局设置（只迁移一次）。"""
        settings = EditorSettings()
        moved = global_settings.adopt_legacy_settings(
            settings,
            {"grid_division": 16, "audio_latency_ms": -120.0, "metronome": "bar"},
        )
        self.assertTrue(moved)
        self.assertEqual(settings.grid_division, 16)
        self.assertAlmostEqual(settings.audio_latency_ms, -120.0)
        self.assertEqual(settings.metronome, "bar")
        # 文档已存在时不再迁移
        global_settings.save_settings(settings)
        again = EditorSettings()
        self.assertFalse(
            global_settings.adopt_legacy_settings(again, {"grid_division": 2})
        )
        self.assertEqual(again.grid_division, 4)


if __name__ == "__main__":
    unittest.main()

"""编辑器 / 游玩器的全局设置：存用户目录、不写谱面、损坏时回退默认。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import global_settings
from rhythm_editor.game import settings as PS
from rhythm_editor.model.chart import Chart, EditorSettings


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


class PlayerSettingsTests(unittest.TestCase):
    def test_defaults_and_ranges(self) -> None:
        settings = PS.PlayerSettings()
        settings.normalize()
        self.assertEqual(settings.keys, ["shift", "a", "s", "d", "f", "space"])
        self.assertAlmostEqual(settings.scroll_ms, 1500.0)
        settings = PS.PlayerSettings(mouse_sensitivity=99.0, note_thickness=5.0, speed=99.0)
        settings.normalize()
        self.assertAlmostEqual(settings.mouse_sensitivity, 10.0)
        self.assertAlmostEqual(settings.note_thickness, 1.0)
        self.assertAlmostEqual(settings.speed, 10.0)
        self.assertAlmostEqual(settings.scroll_ms, 150.0)
        tiny = PS.PlayerSettings(mouse_sensitivity=0.0, audio_latency_ms=-9999.0, speed=0.2)
        tiny.normalize()
        self.assertAlmostEqual(tiny.mouse_sensitivity, 0.1)
        self.assertAlmostEqual(tiny.audio_latency_ms, -5000.0)
        self.assertAlmostEqual(tiny.speed, 1.0)

    def test_speed_maps_to_visible_ms_and_migrates_legacy(self) -> None:
        self.assertAlmostEqual(PS.PlayerSettings(speed=2.0).scroll_ms, 750.0)
        self.assertAlmostEqual(PS.PlayerSettings(speed=3.0).scroll_ms, 500.0)
        legacy = PS.PlayerSettings.from_dict({"scroll_ms": 750.0})
        self.assertAlmostEqual(legacy.speed, 2.0)
        self.assertAlmostEqual(PS.PlayerSettings.from_dict({}).speed, 1.0)

    def test_keys_thickness_and_recent_are_normalized(self) -> None:
        settings = PS.PlayerSettings(keys=["SHIFT", "", "A"], recent=["a.json", "a.json", "", "b.json"])
        settings.normalize()
        self.assertEqual(len(settings.keys), 6)
        self.assertEqual(settings.keys[:2], ["shift", "a"])
        self.assertEqual(settings.recent, ["a.json", "b.json"])
        legacy = PS.PlayerSettings.from_dict({
            "note_thickness": 0.35, "multi_thickness": 0.35,
            "slide_thickness": 0.03, "click_thickness": 0.03,
        })
        self.assertAlmostEqual(legacy.note_thickness, 0.25, places=6)
        self.assertAlmostEqual(legacy.slide_thickness, 0.03 * PS.UNIT_SCALE, places=6)
        follow = PS.PlayerSettings.from_dict({"note_thickness": 0.7})
        self.assertAlmostEqual(follow.multi_thickness, 0.7 * PS.UNIT_SCALE, places=6)
        self.assertTrue(PS.PlayerSettings().to_dict()["units2"])

    def test_file_roundtrip_renderer_and_broken_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "player.json")
            saved = PS.save_settings(PS.PlayerSettings(player_name="阿岚", speed=2.0, renderer="opengl"), path)
            self.assertTrue(os.path.isfile(saved))
            loaded = PS.load_settings(path)
            self.assertEqual((loaded.player_name, loaded.renderer), ("阿岚", "opengl"))
            self.assertAlmostEqual(loaded.scroll_ms, 750.0)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{ 坏文件")
            self.assertEqual(PS.load_settings(path).player_name, "Player")
        # 老存档里的旧默认值 software 迁移到 panda3d；显式选择保留
        self.assertEqual(PS.PlayerSettings.from_dict({"renderer": "software"}).renderer, "panda3d")
        self.assertEqual(
            PS.PlayerSettings.from_dict({"renderer": "software", "renderer2": True}).renderer, "software"
        )
        self.assertEqual(PS.PlayerSettings.from_dict({"renderer": "vulkan"}).renderer, "panda3d")


if __name__ == "__main__":
    unittest.main()

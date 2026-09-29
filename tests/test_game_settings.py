"""游玩器设置：默认键位、取值范围、全局读写。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.game import settings as PS


class PlayerSettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        s = PS.PlayerSettings()
        s.normalize()
        self.assertEqual(s.keys, ["shift", "a", "s", "d", "f", "space"])
        self.assertAlmostEqual(s.mouse_sensitivity, 1.0)
        self.assertFalse(s.unbounded_mouse)
        self.assertAlmostEqual(s.audio_latency_ms, 0.0)
        self.assertAlmostEqual(s.scroll_ms, 1500.0)

    def test_ranges_are_clamped(self) -> None:
        s = PS.PlayerSettings(mouse_sensitivity=99.0, note_thickness=5.0, scroll_ms=5.0)
        s.normalize()
        self.assertAlmostEqual(s.mouse_sensitivity, 10.0)
        self.assertAlmostEqual(s.note_thickness, 1.0)
        self.assertAlmostEqual(s.scroll_ms, 200.0)
        s2 = PS.PlayerSettings(mouse_sensitivity=0.0, audio_latency_ms=-9999.0)
        s2.normalize()
        self.assertAlmostEqual(s2.mouse_sensitivity, 0.1)
        self.assertAlmostEqual(s2.audio_latency_ms, -5000.0)

    def test_keys_are_padded_and_normalized(self) -> None:
        s = PS.PlayerSettings(keys=["SHIFT", "", "A"])
        s.normalize()
        self.assertEqual(len(s.keys), 6)
        self.assertEqual(s.keys[0], "shift")
        self.assertEqual(s.keys[1], "a")     # 空串用默认键补上

    def test_roundtrip_via_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "player.json")
            saved = PS.save_settings(PS.PlayerSettings(player_name="阿岚", scroll_ms=900.0), path)
            self.assertTrue(os.path.isfile(saved))
            loaded = PS.load_settings(path)
            self.assertEqual(loaded.player_name, "阿岚")
            self.assertAlmostEqual(loaded.scroll_ms, 900.0)
            # 损坏文件回退默认值
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{ 坏文件")
            self.assertEqual(PS.load_settings(path).player_name, "Player")

    def test_recent_list_is_unique_and_capped(self) -> None:
        s = PS.PlayerSettings(recent=[f"chart{i}.json" for i in range(12)])
        s.normalize()
        self.assertEqual(len(s.recent), 8)
        self.assertEqual(s.recent[0], "chart0.json")
        s2 = PS.PlayerSettings(recent=["a.json", "a.json", "", "b.json"])
        s2.normalize()
        self.assertEqual(s2.recent, ["a.json", "b.json"])


if __name__ == "__main__":
    unittest.main()

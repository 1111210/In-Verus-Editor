from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.io.chart_io import (
    chart_audio_path,
    copy_audio_into_chart_dir,
    ensure_chart_suffix,
    load_chart,
    save_chart,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_chart_document import make_chart  # noqa: E402


class ChartIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chart = make_chart()

    def test_suffix(self) -> None:
        self.assertTrue(ensure_chart_suffix("song").endswith(".json"))
        self.assertTrue(ensure_chart_suffix("song.json").endswith(".json"))
        # 只支持 JSON：写别的后缀（含旧的 .ivchart）一律改成 .json
        self.assertTrue(ensure_chart_suffix("song.ivchart").endswith("song.json"))
        self.assertTrue(ensure_chart_suffix("song.txt").endswith("song.json"))

    def test_save_load_roundtrip(self) -> None:
        path = os.path.join(self.tmp.name, "chart.json")
        written = save_chart(self.chart, path)
        self.assertTrue(os.path.isfile(written))
        chart, raw = load_chart(written)
        self.assertEqual(chart.to_dict(), self.chart.to_dict())
        self.assertEqual(raw["format"], "inverus.chart")

    def test_audio_copy(self) -> None:
        audio_src = os.path.join(self.tmp.name, "song.mp3")
        with open(audio_src, "wb") as handle:
            handle.write(b"ID3fake")
        chart_path = os.path.join(self.tmp.name, "level", "chart.json")
        os.makedirs(os.path.dirname(chart_path), exist_ok=True)
        relative = copy_audio_into_chart_dir(audio_src, chart_path)
        self.assertEqual(relative, "song.mp3")
        copied = os.path.join(self.tmp.name, "level", "song.mp3")
        self.assertTrue(os.path.isfile(copied))
        self.chart.metadata.audio_file = relative
        resolved = chart_audio_path(chart_path, self.chart)
        self.assertEqual(os.path.abspath(resolved), os.path.abspath(copied))

    def test_audio_lookup_supports_subfolder_layout(self) -> None:
        chart_path = os.path.join(self.tmp.name, "level", "chart.json")
        sub_dir = os.path.join(self.tmp.name, "level", "audio")
        os.makedirs(sub_dir, exist_ok=True)
        legacy = os.path.join(sub_dir, "old.mp3")
        with open(legacy, "wb") as handle:
            handle.write(b"ID3old")
        self.chart.metadata.audio_file = "audio/old.mp3"
        resolved = chart_audio_path(chart_path, self.chart)
        self.assertEqual(os.path.abspath(resolved), os.path.abspath(legacy))

    def test_audio_missing_returns_none(self) -> None:
        self.chart.metadata.audio_file = "audio/none.mp3"
        self.assertIsNone(chart_audio_path(os.path.join(self.tmp.name, "c.json"), self.chart))


if __name__ == "__main__":
    unittest.main()

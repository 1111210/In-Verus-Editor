from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.model.timing import BpmSegment, TimingMap


class TimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timing = TimingMap(
            [BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 60.0, 4), BpmSegment(16.0, 240.0, 3)],
            offset_ms=250.0,
        )

    def test_beat_to_seconds(self) -> None:
        self.assertAlmostEqual(self.timing.beat_to_seconds(0.0), 0.0)
        self.assertAlmostEqual(self.timing.beat_to_seconds(4.0), 2.0)
        self.assertAlmostEqual(self.timing.beat_to_seconds(8.0), 4.0)
        # 60 BPM 之后 1 拍 = 1 秒
        self.assertAlmostEqual(self.timing.beat_to_seconds(9.0), 5.0)
        # 240 BPM 之后 1 拍 = 0.25 秒
        self.assertAlmostEqual(self.timing.beat_to_seconds(17.0), 12.25)

    def test_roundtrip(self) -> None:
        for beat in (-3.5, 0.0, 1.25, 7.999, 8.0, 12.5, 16.0, 40.0):
            seconds = self.timing.beat_to_seconds(beat)
            self.assertAlmostEqual(self.timing.seconds_to_beat(seconds), beat, places=9)

    def test_offset(self) -> None:
        self.assertAlmostEqual(self.timing.beat_to_song_ms(0.0), 250.0)
        self.assertAlmostEqual(self.timing.beat_to_song_ms(4.0), 2250.0)
        self.assertAlmostEqual(self.timing.song_ms_to_beat(2250.0), 4.0)

    def test_bpm_lookup(self) -> None:
        self.assertEqual(self.timing.bpm_at(3.0), 120.0)
        self.assertEqual(self.timing.bpm_at(10.0), 60.0)
        self.assertEqual(self.timing.bpm_at(20.0), 240.0)
        self.assertEqual(self.timing.beats_per_bar_at(20.0), 3)

    def test_bpm_does_not_affect_grid_distance(self) -> None:
        """BPM 只影响“拍到秒”的换算：物件运动速度定义在拍上，与 BPM 无关。"""
        from rhythm_editor.model.speed import SpeedGroup

        group = SpeedGroup.default()
        # 同样 4 拍的时间间隔，在 120 BPM 与 60 BPM 段落下都对应 4 个网格距离
        self.assertAlmostEqual(group.distance(0.0, 4.0), 4.0)
        self.assertAlmostEqual(group.distance(8.0, 12.0), 4.0)
        # 但对应的秒数不同
        self.assertAlmostEqual(self.timing.beat_to_seconds(4.0) - self.timing.beat_to_seconds(0.0), 2.0)
        self.assertAlmostEqual(self.timing.beat_to_seconds(12.0) - self.timing.beat_to_seconds(8.0), 4.0)

    def test_snap(self) -> None:
        self.assertAlmostEqual(TimingMap.snap_beat(1.3, 4), 1.25)
        self.assertAlmostEqual(TimingMap.snap_beat(1.3, 3), 1.3333333333, places=6)
        self.assertAlmostEqual(TimingMap.snap_beat(-0.3, 4), -0.25)

    def test_bar_lines(self) -> None:
        lines = self.timing.bar_lines(0.0, 16.0)
        self.assertIn(0.0, lines)
        self.assertIn(8.0, lines)
        self.assertIn(16.0, lines)
        # 240 BPM 段落 3 拍一小节
        self.assertIn(19.0, self.timing.bar_lines(16.0, 22.0))

    def test_bar_index(self) -> None:
        bar, in_bar = self.timing.bar_index_at(2.5)
        self.assertEqual(bar, 0)
        self.assertAlmostEqual(in_bar, 2.5)
        bar, _ = self.timing.bar_index_at(10.0)
        self.assertEqual(bar, 2)

    def test_serialization(self) -> None:
        data = self.timing.to_dict()
        restored = TimingMap.from_dict(data)
        self.assertEqual(len(restored.segments), 3)
        self.assertAlmostEqual(restored.offset_ms, 250.0)
        self.assertAlmostEqual(restored.beat_to_seconds(9.0), 5.0)

    def test_first_segment_forced_to_zero(self) -> None:
        timing = TimingMap([BpmSegment(4.0, 100.0, 4)])
        self.assertEqual(timing.segments[0].start_beat, 0.0)
        self.assertAlmostEqual(timing.seconds_per_beat(0.0), 0.6)


if __name__ == "__main__":
    unittest.main()

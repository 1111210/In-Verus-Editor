"""时间模型的核心行为：拍 ↔ 秒换算、小节线、流速积分。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.model.speed import SpeedGroup, SpeedSegment
from rhythm_editor.model.timing import BpmSegment, TimingMap


class TimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timing = TimingMap(
            [BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 60.0, 4), BpmSegment(16.0, 240.0, 3)],
            offset_ms=250.0,
        )

    def test_beat_seconds_roundtrip_and_offset(self) -> None:
        timing = self.timing
        self.assertAlmostEqual(timing.beat_to_seconds(4.0), 2.0)
        self.assertAlmostEqual(timing.beat_to_seconds(9.0), 5.0)
        self.assertAlmostEqual(timing.beat_to_seconds(17.0), 12.25)
        for beat in (-3.5, 0.0, 1.25, 8.0, 12.5, 40.0):
            self.assertAlmostEqual(timing.seconds_to_beat(timing.beat_to_seconds(beat)), beat, places=9)
        self.assertAlmostEqual(timing.beat_to_song_ms(0.0), 250.0)
        self.assertAlmostEqual(timing.song_ms_to_beat(2250.0), 4.0)

    def test_bpm_lookup_and_snap(self) -> None:
        self.assertEqual(self.timing.bpm_at(10.0), 60.0)
        self.assertEqual(self.timing.beats_per_bar_at(20.0), 3)
        self.assertAlmostEqual(TimingMap.snap_beat(1.3, 4), 1.25)
        self.assertAlmostEqual(TimingMap.snap_beat(-0.3, 4), -0.25)

    def test_bpm_change_opens_a_new_bar(self) -> None:
        """BPM 变更点不必落在上一段网格上，但它自己一定是小节线。"""
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 90.0, 4)])
        self.assertAlmostEqual(timing.segments[1].start_beat, 10.0, msg="不吸附、不改写")
        self.assertEqual(timing.bar_lines(0.0, 14.0), [0.0, 4.0, 8.0, 10.0, 14.0])
        self.assertTrue(timing.is_bar_start(10.0))
        self.assertTrue(timing.is_bar_start(14.0))
        self.assertFalse(timing.is_bar_start(12.0), "上一段的网格在新段里不再适用")
        # 上一段末尾的不完整小节按一整节计入，所以第 10 拍是第 4 小节的第一拍
        self.assertEqual(timing.bar_index_at(10.0), (3, 0.0))
        self.assertEqual(timing.bar_index_at(2.0), (0, 2.0))

    def test_zero_beats_per_bar_disables_bar_lines(self) -> None:
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 90.0, 0)])
        self.assertFalse(timing.has_bars_at(9.0))
        self.assertEqual(timing.bar_lines(0.0, 20.0), [0.0, 4.0, 8.0])
        self.assertEqual(timing.bar_index_at(9.0), (2, 1.0))

    def test_serialization_and_first_segment_pinned_to_zero(self) -> None:
        restored = TimingMap.from_dict(self.timing.to_dict())
        self.assertEqual(len(restored.segments), 3)
        self.assertAlmostEqual(restored.beat_to_seconds(9.0), 5.0)
        pinned = TimingMap([BpmSegment(4.0, 100.0, 4)])
        self.assertEqual(pinned.segments[0].start_beat, 0.0)
        self.assertAlmostEqual(pinned.seconds_per_beat(0.0), 0.6)


class SpeedGroupTests(unittest.TestCase):
    def test_scroll_seconds_follows_bpm_and_multiplier(self) -> None:
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 240.0, 4)])
        default = SpeedGroup.default()
        self.assertAlmostEqual(default.scroll_seconds(timing, 0.0, 4.0), 2.0)
        self.assertAlmostEqual(default.scroll_seconds(timing, 4.0, 0.0), -2.0)
        self.assertAlmostEqual(default.scroll_seconds(timing, 4.0, 12.0), 3.0)
        double = SpeedGroup(1, "x2", [SpeedSegment(0.0, 2.0)])
        back = SpeedGroup(2, "back", [SpeedSegment(0.0, -1.0)])
        self.assertAlmostEqual(double.scroll_seconds(timing, 0.0, 4.0), 4.0)
        self.assertAlmostEqual(back.scroll_seconds(timing, 0.0, 4.0), -2.0)

    def test_distance_with_zero_and_negative_speed(self) -> None:
        group = SpeedGroup(
            3, "mix", [SpeedSegment(0.0, 1.0), SpeedSegment(4.0, 0.0), SpeedSegment(6.0, -1.0)]
        )
        self.assertAlmostEqual(group.distance(0.0, 4.0), 4.0)
        self.assertAlmostEqual(group.distance(4.0, 6.0), 0.0)
        self.assertAlmostEqual(group.distance(6.0, 8.0), -2.0)
        self.assertTrue(group.is_reversed_at(7.0))
        self.assertFalse(group.is_reversed_at(3.0))

    def test_position_is_not_monotonic_in_beat(self) -> None:
        """正负流速叠加：判定时刻很远的物件也可能回到判定线上。"""
        group = SpeedGroup(
            4,
            "roundtrip",
            [SpeedSegment(0.0, 1.0), SpeedSegment(10.0, -1.0), SpeedSegment(20.0, 1.0)],
        )
        self.assertAlmostEqual(group.judging_distance(0.0, 5.0), 5.0, places=9)
        self.assertAlmostEqual(group.judging_distance(0.0, 15.0), 5.0, places=9)
        self.assertAlmostEqual(group.judging_distance(10.0, 30.0), 0.0, places=9)
        self.assertAlmostEqual(group.judging_distance(10.0, 20.0), -10.0, places=9)

    def test_group_bookkeeping_and_segment_cleanup(self) -> None:
        group = SpeedGroup(5, "book", [SpeedSegment(3.0, 2.0), SpeedSegment(0.0, 1.0)])
        self.assertEqual([s.start_beat for s in group.segments], [0.0, 3.0])
        self.assertAlmostEqual(group.multiplier_at(0.0), 1.0)
        group.add_notes(["L1", "L2"])
        group.add_notes(["L2", "M1"])
        self.assertEqual(group.note_ids, ["L1", "L2", "M1"])
        group.remove_notes(["L2"])
        self.assertEqual(group.note_ids, ["L1", "M1"])
        clone = group.clone(6)
        self.assertEqual(clone.id, 6)
        self.assertEqual(clone.note_ids, [])


if __name__ == "__main__":
    unittest.main()

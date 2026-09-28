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

    # ------------------------------------------- BPM 变更处一定新开一小节
    def test_change_keeps_its_beat_and_opens_a_new_bar(self) -> None:
        """变更点不必落在上一段的小节线上，但它自己就是一条小节线。"""
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 90.0, 4)])
        self.assertAlmostEqual(timing.segments[1].start_beat, 10.0, msg="不吸附、不改写")
        self.assertTrue(timing.is_bar_start(10.0), "BPM 变更处一定是小节开始")
        self.assertTrue(timing.is_bar_start(14.0), "之后按新段的每小节拍数生长")
        self.assertFalse(timing.is_bar_start(12.0), "上一段的网格在新段里不再适用")

    def test_bar_lines_stop_at_change_point(self) -> None:
        """上一段末尾会留下一个不完整的小节：不完整的小节线不再画出来。"""
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 90.0, 4)])
        lines = timing.bar_lines(0.0, 14.0)
        self.assertEqual(lines, [0.0, 4.0, 8.0, 10.0, 14.0])

    def test_bar_index_counts_partial_bar_as_one(self) -> None:
        """不完整的小节按一整节计入，于是变更处正好是下一小节的第一拍。"""
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 90.0, 4)])
        # 0-4 / 4-8 / 8-10（不完整，算一节） -> 第 10 拍是第 4 小节的第一拍
        self.assertEqual(timing.bar_index_at(10.0), (3, 0.0))
        self.assertEqual(timing.bar_index_at(11.0), (3, 1.0))
        self.assertEqual(timing.bar_index_at(2.0), (0, 2.0))

    def test_exact_multiple_is_not_over_counted(self) -> None:
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 90.0, 4)])
        self.assertEqual(timing.bar_index_at(8.0), (2, 0.0))

    def test_zero_beats_per_bar_disables_bar_lines(self) -> None:
        """每小节 0 拍 = 关闭小节线：该段没有小节线，也没有小节编号。"""
        timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 90.0, 0)])
        self.assertEqual(int(timing.segments[1].beats_per_bar), 0)
        self.assertFalse(timing.has_bars_at(9.0))
        # 关闭段落内没有任何小节线；第 8 拍那条属于上一段的网格（上一小节的结束线）
        self.assertEqual(timing.bar_lines(8.5, 20.0), [])
        self.assertEqual(timing.bar_lines(8.0, 20.0), [8.0])
        self.assertEqual(timing.bar_index_at(9.0), (2, 0.0))
        # 之后重新打开小节线：从该段起点重新起算
        timing2 = TimingMap(
            [BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 90.0, 0), BpmSegment(20.0, 90.0, 3)]
        )
        self.assertTrue(timing2.has_bars_at(20.0))
        self.assertTrue(timing2.is_bar_start(23.0))
        self.assertEqual(timing2.bar_lines(19.0, 27.0), [20.0, 23.0, 26.0])

    def test_bar_lines_inside_segment(self) -> None:
        timing = TimingMap([BpmSegment(0.0, 120.0, 3)])
        self.assertEqual(timing.bar_lines(0.0, 9.0), [0.0, 3.0, 6.0, 9.0])
        self.assertTrue(timing.is_bar_start(6.0))
        self.assertFalse(timing.is_bar_start(7.0))
        self.assertAlmostEqual(timing.bar_start_at_or_before(7.0), 6.0)

    def test_loading_keeps_change_beats(self) -> None:
        """读档不会改写 BPM 变更拍（只做排序 / 去重 / 第一项钉到第 0 拍）。"""
        data = {
            "offset_ms": 0.0,
            "bpm_segments": [
                {"start_beat": 0.0, "bpm": 120.0, "beats_per_bar": 4},
                {"start_beat": 10.6, "bpm": 90.0, "beats_per_bar": 0},
            ],
        }
        timing = TimingMap.from_dict(data)
        self.assertAlmostEqual(timing.segments[1].start_beat, 10.6)
        self.assertEqual(int(timing.segments[1].beats_per_bar), 0)


if __name__ == "__main__":
    unittest.main()

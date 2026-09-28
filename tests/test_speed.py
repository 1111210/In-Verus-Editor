from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.model.speed import SpeedGroup, SpeedSegment


class SpeedGroupTests(unittest.TestCase):
    def test_default_distance(self) -> None:
        group = SpeedGroup.default()
        self.assertAlmostEqual(group.distance(0.0, 4.0), 4.0)
        self.assertAlmostEqual(group.distance(4.0, 0.0), -4.0)
        self.assertAlmostEqual(group.judging_distance(2.0, 2.0), 0.0)

    def test_multiplier_segments(self) -> None:
        group = SpeedGroup(1, "test", [SpeedSegment(0.0, 1.0), SpeedSegment(4.0, 2.0), SpeedSegment(8.0, 0.5)])
        self.assertAlmostEqual(group.distance(0.0, 4.0), 4.0)
        self.assertAlmostEqual(group.distance(4.0, 6.0), 4.0)
        self.assertAlmostEqual(group.distance(6.0, 8.0), 4.0)
        self.assertAlmostEqual(group.distance(8.0, 10.0), 1.0)

    def test_zero_and_negative(self) -> None:
        group = SpeedGroup(2, "stop", [SpeedSegment(0.0, 1.0), SpeedSegment(4.0, 0.0), SpeedSegment(6.0, -1.0)])
        self.assertAlmostEqual(group.distance(4.0, 6.0), 0.0, places=9)
        self.assertAlmostEqual(group.distance(6.0, 8.0), -2.0)
        self.assertTrue(group.is_reversed_at(7.0))
        self.assertFalse(group.is_reversed_at(3.0))

    def test_note_stays_on_judgment_line(self) -> None:
        group = SpeedGroup(3, "mix", [SpeedSegment(0.0, 1.0), SpeedSegment(5.0, -2.0), SpeedSegment(9.0, 3.0)])
        judge = 12.0
        self.assertAlmostEqual(group.judging_distance(judge, judge), 0.0)
        # 负流速区间内被推离判定线的方向为负（判定线下方）
        self.assertLess(group.judging_distance(7.0, 8.0), 0.0)
        # 正流速区间内正常位于判定线上方
        self.assertGreater(group.judging_distance(11.0, judge), 0.0)

    def test_group_notes_bookkeeping(self) -> None:
        group = SpeedGroup(4, "book")
        group.add_notes(["L1", "L2"])
        group.add_notes(["L2", "M1"])
        self.assertEqual(group.note_ids, ["L1", "L2", "M1"])
        group.remove_notes(["L2"])
        self.assertEqual(group.note_ids, ["L1", "M1"])
        clone = group.clone(5)
        self.assertEqual(clone.id, 5)
        self.assertEqual(clone.note_ids, [])
        self.assertEqual(len(clone.segments), len(group.segments))

    def test_first_segment_zero(self) -> None:
        group = SpeedGroup(6, "shift", [SpeedSegment(3.0, 2.0)])
        self.assertEqual(group.segments[0].start_beat, 0.0)
        self.assertAlmostEqual(group.multiplier_at(0.0), 2.0)

    def test_superposed_speeds_return_object_to_judgment_line(self) -> None:
        """正负流速叠加：物体的位置不是“拍差”的单调函数。

        流速先正后负再正，可以让判定时刻很远的物件被推回判定线上，
        也可以让判定时刻很近的物件被甩到判定线下方 —— 因此位置只能由 ∫v 计算。
        """
        group = SpeedGroup(
            7,
            "roundtrip",
            [SpeedSegment(0.0, 1.0), SpeedSegment(10.0, -1.0), SpeedSegment(20.0, 1.0)],
        )
        # 同样的“到达判定线距离”，却对应不同的判定时刻
        self.assertAlmostEqual(group.judging_distance(0.0, 5.0), 5.0, places=9)
        self.assertAlmostEqual(group.judging_distance(0.0, 15.0), 5.0, places=9)
        # 判定线在 10 拍时，判定时刻 30 拍的物件恰好被推回判定线上
        self.assertAlmostEqual(group.judging_distance(10.0, 30.0), 0.0, places=9)
        # 判定线在 10 拍时，判定时刻 20 拍的物件被推到判定线下方 10 个单位
        self.assertAlmostEqual(group.judging_distance(10.0, 20.0), -10.0, places=9)
        # 反向同理：判定线在 30 拍时，判定时刻 10 拍的物件也回到判定线
        self.assertAlmostEqual(group.judging_distance(30.0, 10.0), 0.0, places=9)

    def test_position_is_not_monotonic_in_beat(self) -> None:
        group = SpeedGroup(
            8,
            "zigzag",
            [
                SpeedSegment(0.0, 1.0),
                SpeedSegment(100.0, -1.0),
                SpeedSegment(200.0, 1.0),
            ],
        )
        # 200 拍之外的判定时刻依旧回到判定线：拍差本身不能作为可见性 / 位置的判据
        self.assertAlmostEqual(group.judging_distance(100.0, 300.0), 0.0, places=9)
        self.assertAlmostEqual(group.judging_distance(100.0, 200.0), -100.0, places=9)


if __name__ == "__main__":
    unittest.main()

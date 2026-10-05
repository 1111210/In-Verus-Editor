"""物件模型与自动检查：几何、镜像、序列化、区间冲突。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import config
from model.chart import Chart
from model.document import Document
from model.notes import (
    BoundaryCurve,
    LaneNote,
    MouseClick,
    MouseRegion,
    MouseSlide,
    ease_value,
    note_from_dict,
)
from model.validate import (
    ISSUE_CLICK,
    ISSUE_CLICK_SAME_BEAT,
    ISSUE_LANE,
    ISSUE_SLIDE,
    check_lane_notes,
    check_slides,
    format_report,
    intervals_conflict,
    validate_chart,
)


class LaneNoteTests(unittest.TestCase):
    def test_single_multi_hold_and_mirror(self) -> None:
        single = LaneNote("L1", 2, 2, 4.0)
        self.assertFalse(single.is_multi)
        self.assertFalse(single.is_hold)
        multi = LaneNote("L2", 4, 1, 8.0, 8.0)
        self.assertTrue(multi.is_multi)
        hold = LaneNote("L3", 0, 5, 10.0, 12.0)
        self.assertTrue(hold.is_hold)
        self.assertAlmostEqual(hold.duration_beats, 2.0)
        mirrored = LaneNote("L4", 0, 2, 0.0)
        mirrored.mirrored()
        self.assertEqual((mirrored.lane_start, mirrored.lane_end), (3, 5))

    def test_normalize_shift_and_clone(self) -> None:
        hold = LaneNote("L5", 0, 0, 10.0, 4.0)
        hold.normalize()
        self.assertEqual((hold.beat_start, hold.beat_end), (4.0, 10.0))
        hold.shift(2.0)
        self.assertEqual((hold.beat_start, hold.beat_end), (6.0, 12.0))
        clone = hold.clone("L6", 1.0)
        self.assertEqual(clone.id, "L6")
        self.assertEqual((clone.beat_start, clone.beat_end), (7.0, 13.0))


class MouseNoteTests(unittest.TestCase):
    def test_boundary_easing_and_mirror(self) -> None:
        self.assertAlmostEqual(ease_value(config.EASE_LINEAR, 0.5), 0.5)
        self.assertAlmostEqual(ease_value(config.EASE_IN, 0.5), 0.25)
        self.assertAlmostEqual(ease_value(config.EASE_OUT, 0.5), 0.75)
        curve = BoundaryCurve(0.2, 0.8, config.EASE_OUT)
        self.assertAlmostEqual(curve.value_at(0.0), 0.2)
        self.assertAlmostEqual(curve.mirrored().start, 0.8)

    def test_region_geometry_and_mirror(self) -> None:
        region = MouseRegion(
            "M1", 4.0, 8.0, BoundaryCurve(0.0, 0.2, config.EASE_IN),
            BoundaryCurve(1.0, 0.8, config.EASE_OUT),
        )
        self.assertAlmostEqual(region.left_at(8.0), 0.2)
        self.assertAlmostEqual(region.right_at(8.0), 0.8)
        self.assertTrue(region.contains(6.0, region.left_at(6.0)))
        self.assertFalse(region.contains(9.0, 0.5))
        mirrored = MouseRegion("M2", 0.0, 4.0, BoundaryCurve(-0.5, 0.2), BoundaryCurve(1.4, 1.0))
        self.assertAlmostEqual(mirrored.x_range()[0], -0.5)
        mirrored.mirrored()
        self.assertLess(mirrored.left.start, mirrored.right.start)

    def test_slide_direction_and_mirror(self) -> None:
        self.assertEqual(MouseSlide("M3", 4.0, 0.2, 0.8).direction, 1)
        slide = MouseSlide("M4", 4.0, 0.8, 0.2)
        self.assertEqual(slide.direction, -1)
        self.assertAlmostEqual(slide.span, 0.6)
        slide.mirrored()
        self.assertEqual(slide.direction, 1)

    def test_click_geometry_min_radius_and_clone(self) -> None:
        note = MouseClick("M5", 8.0, 0.5, 0.2)
        self.assertAlmostEqual(note.x_start, 0.3)
        self.assertAlmostEqual(note.x_end, 0.7)
        self.assertTrue(note.contains_x(0.5))
        self.assertFalse(note.contains_x(0.75))
        self.assertAlmostEqual(MouseClick("M6", 0.0, 0.5, 0.0).radius, config.MIN_CLICK_RADIUS)
        note.mirrored()
        self.assertAlmostEqual(note.center, 0.5)
        clone = note.clone("M7", 1.0)
        self.assertAlmostEqual(clone.beat, 9.0)
        self.assertAlmostEqual(clone.radius, 0.2)

    def test_serialization_roundtrip(self) -> None:
        notes = [
            LaneNote("L1", 1, 3, 2.0, 4.0),
            MouseRegion("M1", 0.0, 2.0, BoundaryCurve(0.0, 0.5), BoundaryCurve(1.0, 0.75)),
            MouseSlide("M2", 6.0, 0.25, 0.75),
            MouseClick("M3", 8.0, 0.5, 0.15),
        ]
        for note in notes:
            self.assertEqual(note_from_dict(note.to_dict()).to_dict(), note.to_dict())


class ValidateTests(unittest.TestCase):
    def test_interval_rules(self) -> None:
        self.assertTrue(intervals_conflict(5.0, 5.0, 4.0, 6.0))
        self.assertFalse(intervals_conflict(4.0, 4.0, 4.0, 6.0))
        self.assertTrue(intervals_conflict(3.0, 3.0, 3.0, 3.0))
        self.assertTrue(intervals_conflict(0.0, 2.0, 1.0, 3.0))
        self.assertFalse(intervals_conflict(0.0, 2.0, 2.0, 3.0))

    def test_lane_overlap_rules(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 1, 4, 4.0))
        chart.add(LaneNote("L2", 3, 3, 4.0))
        issues = check_lane_notes(chart)
        self.assertEqual([i.kind for i in issues], [ISSUE_LANE])
        chart2 = Chart()
        chart2.add(LaneNote("L1", 0, 0, 0.0, 4.0))
        chart2.add(LaneNote("L2", 0, 0, 4.0))
        self.assertEqual(check_lane_notes(chart2), [], "紧贴端点允许")

    def test_slide_and_click_must_touch_region(self) -> None:
        chart = Chart()
        chart.add(MouseRegion("R1", 0.0, 10.0, BoundaryCurve(0.6, 0.6), BoundaryCurve(0.9, 0.9)))
        chart.add(MouseSlide("S1", 5.0, 0.5, 0.8))           # 与空域相交 -> 合法
        chart.add(MouseSlide("S2", 6.0, 0.0, 0.3))           # 完全分离 -> 报错
        chart.add(MouseClick("C1", 7.0, 0.75, 0.05))         # 在空域内 -> 合法
        chart.add(MouseClick("C2", 8.0, 0.1, 0.05))          # 分离 -> 报错
        chart.add(MouseClick("C3", 7.0, 0.2, 0.05))          # 同一时刻第二个点击 -> 报错
        kinds = [issue.kind for issue in validate_chart(chart)]
        self.assertIn(ISSUE_SLIDE, kinds)
        self.assertIn(ISSUE_CLICK, kinds)
        self.assertIn(ISSUE_CLICK_SAME_BEAT, kinds)
        self.assertEqual(len(validate_chart(Chart())), 0)

    def test_region_boundary_is_used_at_slide_time(self) -> None:
        chart = Chart()
        chart.add(MouseRegion("M1", 0.0, 10.0, BoundaryCurve(0.0, 0.8), BoundaryCurve(0.2, 1.0)))
        chart.add(MouseSlide("S1", 0.0, 0.5, 0.7))
        chart.add(MouseSlide("S2", 5.0, 0.4, 0.6))
        issues = check_slides(chart)
        self.assertEqual({issue.note_ids[0] for issue in issues}, {"S1"})

    def test_report(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 0, 0, 0.0))
        self.assertIn("未发现问题", format_report(chart, validate_chart(chart)))
        chart.add(LaneNote("L2", 0, 0, 0.0))
        report = format_report(chart, validate_chart(chart))
        self.assertIn("6K 轨道冲突", report)
        self.assertIn("L1", report)


class DocumentEditTests(unittest.TestCase):
    def test_click_undo_and_replace(self) -> None:
        chart = Chart()
        document = Document(chart)
        before = chart.to_dict()
        document.add_note(MouseClick("M1", 4.0, 0.5, 0.2))
        after = chart.to_dict()
        self.assertNotEqual(before, after)
        self.assertTrue(document.undo())
        self.assertEqual(chart.to_dict(), before)
        self.assertTrue(document.redo())
        self.assertEqual(chart.to_dict(), after)
        data = chart.find("M1").to_dict()
        data["radius"] = 0.3
        document.replace_note("M1", data)
        self.assertAlmostEqual(chart.find("M1").radius, 0.3)
        document.undo()
        self.assertAlmostEqual(chart.find("M1").radius, 0.2)


if __name__ == "__main__":
    unittest.main()

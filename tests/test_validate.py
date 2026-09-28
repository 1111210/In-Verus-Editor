"""自动检查（谱面合法性）的模型层测试。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.model.chart import Chart
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from rhythm_editor.model.validate import (
    ISSUE_LANE,
    ISSUE_SLIDE,
    check_lane_notes,
    check_slides,
    format_report,
    intervals_conflict,
    ranges_touch_or_overlap,
    validate_chart,
)


class IntervalRuleTests(unittest.TestCase):
    def test_point_and_interval(self) -> None:
        # 瞬时点落在长按内部 -> 冲突
        self.assertTrue(intervals_conflict(5.0, 5.0, 4.0, 6.0))
        # 紧贴端点 -> 允许
        self.assertFalse(intervals_conflict(4.0, 4.0, 4.0, 6.0))
        self.assertFalse(intervals_conflict(6.0, 6.0, 4.0, 6.0))
        # 同一时刻的两个按键 -> 冲突
        self.assertTrue(intervals_conflict(3.0, 3.0, 3.0, 3.0))
        self.assertFalse(intervals_conflict(3.0, 3.0, 3.5, 3.5))

    def test_two_intervals(self) -> None:
        self.assertTrue(intervals_conflict(0.0, 2.0, 1.0, 3.0))
        self.assertFalse(intervals_conflict(0.0, 2.0, 2.0, 3.0))
        self.assertTrue(intervals_conflict(0.0, 2.0, 0.0, 2.0))

    def test_ranges_touch_or_overlap(self) -> None:
        self.assertTrue(ranges_touch_or_overlap(0.0, 0.5, 0.4, 0.9))
        self.assertTrue(ranges_touch_or_overlap(0.0, 0.5, 0.5, 0.9))
        self.assertFalse(ranges_touch_or_overlap(0.0, 0.4, 0.5, 0.9))


class LaneCheckTests(unittest.TestCase):
    def test_same_lane_overlap(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 2, 2, 4.0))
        chart.add(LaneNote("L2", 2, 2, 4.0))
        issues = check_lane_notes(chart)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, ISSUE_LANE)
        self.assertIn("重叠", issues[0].message)

    def test_different_lane_is_fine(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 1, 1, 4.0))
        chart.add(LaneNote("L2", 2, 2, 4.0))
        self.assertEqual(check_lane_notes(chart), [])

    def test_tap_inside_hold(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 0, 0, 4.0, 8.0))       # 长按：4~8 拍
        chart.add(LaneNote("L2", 0, 0, 6.0))            # 落在内部 -> 冲突
        self.assertEqual(len(check_lane_notes(chart)), 1)

    def test_tap_touching_hold_end_is_fine(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 0, 0, 4.0, 8.0))
        chart.add(LaneNote("L2", 0, 0, 8.0))            # 紧贴结束时刻 -> 允许
        self.assertEqual(check_lane_notes(chart), [])

    def test_multi_lane_covers_single(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 1, 4, 4.0))            # 多轨
        chart.add(LaneNote("L2", 3, 3, 4.0))            # 被覆盖
        issues = check_lane_notes(chart)
        self.assertEqual(len(issues), 1)
        self.assertIn("轨道 4", issues[0].message)

    def test_multi_lane_hold_vs_multi_lane(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 0, 3, 4.0, 8.0))
        chart.add(LaneNote("L2", 2, 5, 7.0))            # 轨道 2-3 相交且时刻在内部
        self.assertEqual(len(check_lane_notes(chart)), 1)
        # 轨道不相交时不算冲突
        chart2 = Chart()
        chart2.add(LaneNote("L1", 0, 1, 4.0, 8.0))
        chart2.add(LaneNote("L2", 2, 5, 7.0))
        self.assertEqual(check_lane_notes(chart2), [])

    def test_hold_vs_hold_touching(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 0, 0, 0.0, 4.0))
        chart.add(LaneNote("L2", 0, 0, 4.0, 8.0))
        self.assertEqual(check_lane_notes(chart), [])


class SlideCheckTests(unittest.TestCase):
    def _chart_with_region(self) -> Chart:
        chart = Chart()
        chart.add(
            MouseRegion("M1", 10.0, 20.0, BoundaryCurve(0.6, 0.6), BoundaryCurve(0.9, 0.9))
        )
        return chart

    def test_no_region_at_slide_time(self) -> None:
        chart = self._chart_with_region()
        chart.add(MouseSlide("S1", 30.0, 0.0, 0.4))     # 该时刻没有空域 -> 通过
        self.assertEqual(check_slides(chart), [])

    def test_slide_intersects_region(self) -> None:
        chart = self._chart_with_region()
        chart.add(MouseSlide("S1", 15.0, 0.5, 0.8))     # 与 0.6~0.9 相交
        self.assertEqual(check_slides(chart), [])

    def test_slide_touching_region_edge(self) -> None:
        chart = self._chart_with_region()
        chart.add(MouseSlide("S1", 15.0, 0.2, 0.6))     # 终点正好贴住 0.6 -> 通过
        self.assertEqual(check_slides(chart), [])

    def test_slide_outside_region(self) -> None:
        chart = self._chart_with_region()
        chart.add(MouseSlide("S1", 15.0, 0.0, 0.3))     # 与 0.6~0.9 完全分离
        issues = check_slides(chart)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].kind, ISSUE_SLIDE)
        self.assertEqual(set(issues[0].note_ids), {"S1", "M1"})

    def test_region_boundary_is_used_at_slide_time(self) -> None:
        """空域边界随时间变化：按滑键所在时刻的边界位置判断。"""
        chart = Chart()
        chart.add(MouseRegion("M1", 0.0, 10.0, BoundaryCurve(0.0, 0.8), BoundaryCurve(0.2, 1.0)))
        # 第 0 拍时空域在 0.0~0.2，滑键在 0.5~0.7 -> 分离
        chart.add(MouseSlide("S1", 0.0, 0.5, 0.7))
        # 第 10 拍时空域在 0.8~1.0，滑键在 0.5~0.7 -> 也分离
        chart.add(MouseSlide("S2", 10.0, 0.5, 0.7))
        self.assertEqual(len(check_slides(chart)), 2)
        # 滑键与当时的空域重叠 -> 通过
        chart.add(MouseSlide("S3", 5.0, 0.4, 0.6))      # 第 5 拍边界约 0.4~0.6
        issues = check_slides(chart)
        self.assertEqual({issue.note_ids[0] for issue in issues}, {"S1", "S2"})


class ReportTests(unittest.TestCase):
    def test_report_when_clean(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 0, 0, 0.0))
        report = format_report(chart, validate_chart(chart))
        self.assertIn("未发现问题", report)

    def test_report_lists_issues(self) -> None:
        chart = Chart()
        chart.add(LaneNote("L1", 0, 0, 0.0))
        chart.add(LaneNote("L2", 0, 0, 0.0))
        issues = validate_chart(chart)
        report = format_report(chart, issues)
        self.assertIn("6K 轨道冲突", report)
        self.assertIn("L1", report)
        self.assertIn("L2", report)



if __name__ == "__main__":
    unittest.main()

"""游玩器判定规则（EXACT / Exact / Near / Break、滑键吸附、空域区间、分数）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.game.judge import (
    BREAK,
    EXACT,
    EXACT_MIXED,
    NEAR,
    Judge,
    grade_for_offset,
)
from rhythm_editor.model.chart import Chart
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseClick, MouseRegion, MouseSlide
from rhythm_editor.model.timing import BpmSegment, TimingMap


def make_chart(notes) -> Chart:
    chart = Chart()
    chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])   # 1 拍 = 500ms
    for note in notes:
        chart.add(note)
    return chart


class WindowTests(unittest.TestCase):
    def test_click_windows(self) -> None:
        self.assertEqual(grade_for_offset(0.0), EXACT)
        self.assertEqual(grade_for_offset(25.0), EXACT)
        self.assertEqual(grade_for_offset(-25.0), EXACT)
        self.assertEqual(grade_for_offset(26.0), EXACT_MIXED)
        self.assertEqual(grade_for_offset(50.0), EXACT_MIXED)
        self.assertEqual(grade_for_offset(51.0), NEAR)
        self.assertEqual(grade_for_offset(-100.0), NEAR)
        # 提前 100~150ms 算 Break；更早是空敲（None，不消耗物件）
        self.assertEqual(grade_for_offset(-120.0), BREAK)
        self.assertEqual(grade_for_offset(-150.0), BREAK)
        self.assertIsNone(grade_for_offset(-151.0))
        self.assertIsNone(grade_for_offset(101.0))


class ClickJudgeTests(unittest.TestCase):
    def test_nearest_click_object_is_judged(self) -> None:
        chart = make_chart([LaneNote("L1", 0, 0, 4.0), LaneNote("L2", 3, 3, 4.0)])
        judge = Judge(chart)
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        result = judge.on_press(hit_ms + 10.0, lane=3)
        self.assertIsNotNone(result)
        self.assertEqual(result.note_id, "L2")
        self.assertEqual(result.grade, EXACT)

    def test_empty_press_has_no_penalty(self) -> None:
        chart = make_chart([LaneNote("L1", 0, 0, 8.0)])
        judge = Judge(chart)
        self.assertIsNone(judge.on_press(0.0, lane=0))     # 离判定时刻还很远 -> 空敲
        self.assertEqual(judge.counts[BREAK], 0)
        self.assertEqual(judge.results, [])

    def test_lane_must_match(self) -> None:
        chart = make_chart([LaneNote("L1", 2, 2, 4.0)])
        judge = Judge(chart)
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        self.assertIsNone(judge.on_press(hit_ms, lane=0))
        self.assertIsNotNone(judge.on_press(hit_ms, lane=2))

    def test_miss_becomes_break(self) -> None:
        chart = make_chart([LaneNote("L1", 0, 0, 1.0)])
        judge = Judge(chart)
        hit_ms = chart.timing.beat_to_song_ms(1.0)
        judge.update(hit_ms, inside_click=None)
        judge.update(hit_ms + 200.0, inside_click=None)
        self.assertEqual(judge.counts[BREAK], 1)

    def test_mouse_click_range_is_padded(self) -> None:
        chart = make_chart([MouseClick("M1", 2.0, 0.5, 0.1)])   # 范围 0.4~0.6
        judge = Judge(chart)
        hit_ms = chart.timing.beat_to_song_ms(2.0)
        self.assertIsNone(judge.on_press(hit_ms, x=0.7))         # 超出范围
        self.assertIsNotNone(judge.on_press(hit_ms, x=0.605))    # 边界外 0.005 < 0.01 放宽


class SlideJudgeTests(unittest.TestCase):
    def test_exact_slide_triggers_immediately(self) -> None:
        chart = make_chart([MouseSlide("S1", 4.0, 0.2, 0.6)])
        judge = Judge(chart)
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        judge.on_move(hit_ms - 20.0, 0.1)
        result = judge.on_move(hit_ms + 5.0, 0.45)      # 位移跨过滑键范围
        self.assertIsNotNone(result)
        self.assertEqual(result.grade, EXACT)

    def test_best_grade_is_kept_then_committed_after_line(self) -> None:
        chart = make_chart([MouseSlide("S1", 4.0, 0.2, 0.6)])
        judge = Judge(chart)
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        judge.on_move(hit_ms - 80.0, 0.10)
        first = judge.on_move(hit_ms - 70.0, 0.40)      # 提前 70ms：Near，先记着
        self.assertIsNone(first)
        later = judge.on_move(hit_ms + 150.0, 0.9)      # 过了判定线 100ms 仍未更好
        self.assertIsNotNone(later)
        self.assertEqual(later.grade, NEAR)

    def test_no_movement_is_break(self) -> None:
        chart = make_chart([MouseSlide("S1", 4.0, 0.2, 0.6)])
        judge = Judge(chart)
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        judge.on_move(hit_ms, 0.9)                      # 完全没经过滑键范围
        judge.on_move(hit_ms + 250.0, 0.9)
        self.assertEqual(judge.counts[BREAK], 1)


class RegionJudgeTests(unittest.TestCase):
    def _region(self) -> MouseRegion:
        return MouseRegion("R1", 0.0, 2.0, BoundaryCurve(0.2, 0.2), BoundaryCurve(0.8, 0.8))

    def test_half_beat_intervals(self) -> None:
        chart = make_chart([self._region()])
        judge = Judge(chart)
        self.assertEqual(judge.total_notes, 4)          # 2 拍 = 4 个半拍区间

    def test_always_inside_is_exact(self) -> None:
        chart = make_chart([self._region()])
        judge = Judge(chart)
        region = chart.mouse_notes[0]
        for step in range(0, 11):
            judge.update(step * 100.0, inside_hold=region)
        self.assertEqual(judge.counts[EXACT], 4)
        self.assertEqual(judge.counts[BREAK], 0)

    def test_never_inside_is_break(self) -> None:
        chart = make_chart([self._region()])
        judge = Judge(chart)
        for step in range(0, 11):
            judge.update(step * 100.0, inside_hold=None)
        self.assertEqual(judge.counts[BREAK], 4)

    def test_brief_exit_is_exact_long_exit_is_near(self) -> None:
        chart = make_chart([self._region()])
        judge = Judge(chart)
        region = chart.mouse_notes[0]
        judge.update(0.0, inside_hold=region)
        judge.update(40.0, inside_hold=None)            # 离开 40ms（<=50）→ EXACT
        judge.update(500.0, inside_hold=region)
        self.assertGreaterEqual(judge.counts[EXACT], 1, "短暂离开（<=50ms）仍算 EXACT")
        self.assertEqual(judge.counts[BREAK], 0)


class ScoreTests(unittest.TestCase):
    def test_accuracy_and_score(self) -> None:
        chart = make_chart([LaneNote("L1", 0, 0, 1.0), LaneNote("L2", 1, 1, 2.0)])
        judge = Judge(chart)
        first = chart.timing.beat_to_song_ms(1.0)
        second = chart.timing.beat_to_song_ms(2.0)
        judge.on_press(first, lane=0)                  # EXACT
        judge.on_press(second + 60.0, lane=1)          # Near
        self.assertEqual(judge.counts[EXACT], 1)
        self.assertEqual(judge.counts[NEAR], 1)
        self.assertAlmostEqual(judge.accuracy, 0.75)   # (1 + 0.5) / 2
        # 分数 = 1.5 / 总物件数(2) × 1e8 + EXACT 数
        self.assertEqual(judge.score, 75_000_000 + 1)

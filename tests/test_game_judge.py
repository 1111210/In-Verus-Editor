"""游玩器判定规则：判定窗口、长按 / 空域区间、滑键、准度与分数。"""

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
    def test_grade_windows(self) -> None:
        self.assertEqual(grade_for_offset(0.0), EXACT)
        self.assertEqual(grade_for_offset(25.0), EXACT)
        self.assertEqual(grade_for_offset(26.0), EXACT_MIXED)
        self.assertEqual(grade_for_offset(51.0), NEAR)
        self.assertEqual(grade_for_offset(-100.0), NEAR)
        self.assertEqual(grade_for_offset(-120.0), BREAK)
        self.assertIsNone(grade_for_offset(-151.0))
        self.assertIsNone(grade_for_offset(101.0))

    def test_windows_are_real_time_not_song_time(self) -> None:
        """判定窗口按真实时间算：同一真实偏差在任何倍速下等级相同。"""
        for rate in (0.4, 1.0, 1.5):
            self.assertEqual(grade_for_offset(20.0 * rate, rate), EXACT)
            self.assertEqual(grade_for_offset(30.0 * rate, rate), EXACT_MIXED)
            self.assertEqual(grade_for_offset(80.0 * rate, rate), NEAR)
            self.assertEqual(grade_for_offset(-120.0 * rate, rate), BREAK)
        for rate in (0.4, 1.0, 1.5):
            chart = make_chart([LaneNote("L1", 0, 0, 4.0)])
            hit_ms = chart.timing.beat_to_song_ms(4.0)
            judge = Judge(chart, rate=rate)
            result = judge.on_press(hit_ms + 40.0 * rate, lane=0)
            self.assertEqual(result.grade, EXACT_MIXED, f"rate={rate} 时等级应一致")
            missed = Judge(chart, rate=rate)
            missed.update(hit_ms + 90.0 * rate)
            self.assertEqual(missed.counts[BREAK], 0, "过线 90ms 还不该 Break")
            missed.update(hit_ms + 120.0 * rate)
            self.assertEqual(missed.counts[BREAK], 1, "过线 120ms 应 Break")


class ClickJudgeTests(unittest.TestCase):
    def test_input_type_and_lane_must_match(self) -> None:
        chart = make_chart(
            [LaneNote("L1", 0, 0, 4.0), LaneNote("L2", 3, 3, 4.0), MouseClick("M1", 4.0, 0.5, 0.1)]
        )
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        judge = Judge(chart)
        self.assertIsNone(judge.on_press(hit_ms, lane=2), "轨道不匹配应空敲")
        self.assertEqual(judge.counts[BREAK], 0)
        result = judge.on_press(hit_ms + 10.0, lane=3)
        self.assertEqual((result.note_id, result.grade), ("L2", EXACT))
        click_judge = Judge(chart)
        clicked = click_judge.on_press(hit_ms, x=0.5)
        self.assertEqual(clicked.note_id, "M1", "鼠标点击不该判 6K 按键")
        self.assertIsNone(click_judge.on_press(0.0, lane=0))
        self.assertEqual(click_judge.results, [clicked])

    def test_miss_and_padding(self) -> None:
        chart = make_chart([LaneNote("L1", 0, 0, 1.0)])
        judge = Judge(chart)
        judge.update(chart.timing.beat_to_song_ms(1.0) + 200.0)
        self.assertEqual(judge.counts[BREAK], 1)
        padded = make_chart([MouseClick("M1", 2.0, 0.5, 0.1)])   # 范围 0.4~0.6
        judge = Judge(padded)
        hit_ms = padded.timing.beat_to_song_ms(2.0)
        self.assertIsNone(judge.on_press(hit_ms, x=0.7))
        self.assertIsNotNone(judge.on_press(hit_ms, x=0.605), "边界外 0.005 < 0.01 放宽")


class SlideJudgeTests(unittest.TestCase):
    def test_exact_near_and_no_movement(self) -> None:
        chart = make_chart([MouseSlide("S1", 4.0, 0.2, 0.6)])
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        judge = Judge(chart)
        judge.on_move(hit_ms - 80.0, 0.10)
        self.assertIsNone(judge.on_move(hit_ms - 70.0, 0.40), "提前 70ms：先记 Near，过线后才提交")
        self.assertEqual(judge.on_move(hit_ms + 150.0, 0.9).grade, NEAR)

        exact = Judge(chart)
        exact.on_move(hit_ms - 20.0, 0.1)
        self.assertEqual(exact.on_move(hit_ms + 5.0, 0.45).grade, EXACT)

        missed = Judge(chart)
        missed.on_move(hit_ms, 0.9)
        missed.on_move(hit_ms + 250.0, 0.9)
        self.assertEqual(missed.counts[BREAK], 1)


class RegionJudgeTests(unittest.TestCase):
    def _region(self) -> MouseRegion:
        return MouseRegion("R1", 0.0, 2.0, BoundaryCurve(0.2, 0.2), BoundaryCurve(0.8, 0.8))

    @staticmethod
    def _single_interval_grade(bpm: float, first_sample: float, inside: bool = False):
        """一个半拍区间（0..0.5 拍）的评级；`first_sample` 落在区间中间。"""
        chart = Chart()
        chart.timing = TimingMap([BpmSegment(0.0, bpm, 4)])
        region = MouseRegion("R", 0.0, 0.5, BoundaryCurve(0.3, 0.3), BoundaryCurve(0.7, 0.7))
        chart.add(region)
        judge = Judge(chart)
        judge.update(first_sample, inside_hold=region if inside else None)
        judge.update(chart.timing.beat_to_song_ms(0.5) + 20.0, inside_hold=region if inside else None)
        return judge.results[0].grade

    def test_never_entered_is_break_over_near_and_short_interval(self) -> None:
        """从未进入空域 → Break 优先：区间不足 50ms 或判定器中途才开始记录也一样。"""
        # 200ms 的区间，判定器从 60ms 才开始记录：此前会把开头一段漏算成 Near
        self.assertEqual(self._single_interval_grade(150.0, 60.0), BREAK)
        # 40ms 的区间（不足 50ms），判定器从 10ms 开始记录：此前会落进“离开 ≤50ms → EXACT”
        self.assertEqual(self._single_interval_grade(750.0, 10.0), BREAK)
        # 进入过空域时不受影响：仍按离开时长 rating
        self.assertEqual(self._single_interval_grade(150.0, 60.0, inside=True), EXACT)

    def test_intervals_include_partial_tail(self) -> None:
        self.assertEqual(Judge(make_chart([self._region()])).total_notes, 4, "2 拍 = 4 个半拍区间")
        tail = MouseRegion("R2", 0.0, 1.3, BoundaryCurve(0.2, 0.2), BoundaryCurve(0.8, 0.8))
        self.assertEqual(Judge(make_chart([tail])).total_notes, 3, "不足半拍的尾段也算一个区间")

    def test_inside_exact_sparse_updates_and_exit_timestamps(self) -> None:
        chart = make_chart([self._region()])
        judge = Judge(chart)
        region = chart.mouse_notes[0]
        judge.update(0.0, inside_hold=region)
        judge.update(1000.0, inside_hold=region)       # 一帧跨过全部 4 个区间
        self.assertEqual((judge.counts[EXACT], judge.counts[BREAK]), (4, 0))

        chart = make_chart([self._region()])
        judge = Judge(chart)
        region = chart.mouse_notes[0]
        judge.update(0.0, inside_hold=region)
        judge.on_inside(40.0, None)                    # 40ms 离开
        judge.on_inside(90.0, region)                  # 90ms 回来：离开正好 50ms
        judge.update(1000.0, inside_hold=region)
        self.assertEqual(judge.counts[EXACT], 4, judge.counts)

        chart = make_chart([self._region()])
        judge = Judge(chart)
        judge.update(0.0, inside_hold=None)
        judge.update(1000.0, inside_hold=None)
        self.assertEqual(judge.counts[BREAK], 4)


class ScoreTests(unittest.TestCase):
    def test_autoplay_exact_and_negative_multiplier(self) -> None:
        chart = make_chart([LaneNote("L1", 0, 0, 2.0), MouseClick("M1", 2.0, 0.5, 0.1)])
        judge = Judge(chart, autoplay=True)
        self.assertAlmostEqual(judge.score_multiplier, -1.0)
        judge.rate = 2.0
        self.assertAlmostEqual(judge.score_multiplier, -(2.0 ** 0.6), places=6)
        judge.rate = 1.0
        hit_ms = chart.timing.beat_to_song_ms(2.0)
        judge.update(0.0)
        self.assertEqual(judge.judged_notes, 0, "还没到判定时间不该提前判")
        judge.update(hit_ms)
        self.assertEqual((judge.counts[EXACT], judge.counts[BREAK]), (2, 0))
        self.assertEqual(judge.score, -judge.accuracy_score - judge.exact_bonus)
        hold_chart = make_chart([LaneNote("H1", 0, 0, 0.0, 3.0), MouseRegion("R1", 0.0, 3.0)])
        hold_judge = Judge(hold_chart, autoplay=True)
        hold_judge.update(hold_chart.timing.beat_to_song_ms(8.0))
        self.assertEqual(hold_judge.counts[EXACT], hold_judge.total_notes)

    def test_accuracy_combo_and_score_scaling(self) -> None:
        chart = make_chart([LaneNote("L1", 0, 0, 1.0), LaneNote("L2", 1, 1, 2.0)])
        judge = Judge(chart)
        judge.on_press(chart.timing.beat_to_song_ms(1.0), lane=0)
        judge.on_press(chart.timing.beat_to_song_ms(2.0) + 60.0, lane=1)
        self.assertEqual((judge.counts[EXACT], judge.counts[NEAR]), (1, 1))
        self.assertAlmostEqual(judge.accuracy, 0.75)
        self.assertEqual(judge.score, 75_000_000 + 1)
        accuracy_part, bonus = judge.accuracy_score, judge.exact_bonus
        for rate in (0.5, 0.1, 1.5):
            judge.rate = rate
            self.assertEqual(judge.score, round(accuracy_part * (rate ** 0.6)) + bonus)
        self.assertEqual(Judge(chart, rate=9.0).rate, 1.5)
        self.assertEqual(Judge(chart, rate=0.01).rate, 0.1)

        combo_chart = make_chart([LaneNote(f"L{i}", i - 1, i - 1, float(i)) for i in (1, 2, 3, 4)])
        combo = Judge(combo_chart)
        for beat in (1.0, 2.0):
            combo.on_press(combo_chart.timing.beat_to_song_ms(beat), lane=int(beat - 1))
        self.assertEqual(combo.combo, 2)
        combo.update(combo_chart.timing.beat_to_song_ms(3.0) + 200.0)
        self.assertEqual((combo.combo, combo.max_combo), (0, 2))
        combo.on_press(combo_chart.timing.beat_to_song_ms(4.0), lane=3)
        self.assertEqual((combo.combo, combo.max_combo), (1, 2))


class HoldJudgeTests(unittest.TestCase):
    def _hold(self, start: float, end: float):
        chart = make_chart([LaneNote("H1", 0, 0, start, end)])
        return chart, Judge(chart)

    def test_hold_exact_near_and_early_release(self) -> None:
        chart, judge = self._hold(0.0, 2.0)
        hold = chart.lane_notes[0]
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=0)
        for step in range(0, 11):
            judge.update(step * 100.0, inside_hold=hold, held=True)
        self.assertEqual(judge.counts[BREAK], 0)
        self.assertGreaterEqual(judge.counts[EXACT], 4)

        chart, judge = self._hold(0.0, 2.0)
        hold = chart.lane_notes[0]
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=0)
        judge.update(100.0, inside_hold=hold, held=True)
        judge.update(300.0, inside_hold=None, held=False)
        judge.update(1000.0, inside_hold=None, held=False)
        self.assertGreaterEqual(judge.counts[NEAR], 1, judge.counts)

        chart, judge = self._hold(0.0, 1.0)            # 2 个区间
        hold = chart.lane_notes[0]
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=0)
        judge.update(100.0, inside_hold=hold, held=True)
        judge.update(240.0, inside_hold=hold, held=True)
        judge.update(470.0, inside_hold=None, held=False)   # 结束前 30ms 抬起
        judge.update(600.0, inside_hold=None, held=False)
        self.assertEqual((judge.counts[NEAR], judge.counts[EXACT]), (0, 3))

    def test_sparse_updates_and_release_timestamps(self) -> None:
        chart, judge = self._hold(0.0, 2.0)
        judge.on_press(0.0, lane=0)
        judge.update(0.0, held={0})
        judge.update(1000.0, held={0})                 # 一帧跨过全部 4 个区间
        self.assertEqual(judge.counts[BREAK], 0)
        self.assertGreaterEqual(judge.counts[EXACT], 5)

        chart, judge = self._hold(0.0, 1.0)
        judge.on_press(0.0, lane=0)
        judge.update(100.0, held={0})
        judge.on_release(300.0, lane=0)                # 第 2 个区间内抬起
        judge.update(600.0, held=set())
        self.assertEqual((judge.counts[NEAR], judge.counts[BREAK]), (1, 0))

    def test_multi_lane_release_rules(self) -> None:
        chart = make_chart([LaneNote("H2", 1, 3, 0.0, 1.0)])
        judge = Judge(chart)
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=1)
        judge.update(100.0, held={1, 2, 3})
        judge.update(300.0, held={1, 2})               # 只松开一行：不算抬起
        judge.update(600.0, held={1, 2})
        self.assertEqual((judge.counts[NEAR], judge.counts[BREAK]), (0, 0))
        self.assertGreaterEqual(judge.counts[EXACT], 3)

        chart = make_chart([LaneNote("H4", 1, 3, 0.0, 2.0)])
        judge = Judge(chart)
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=1)
        judge.update(100.0, held={1, 2, 3})
        judge.update(300.0, held=set())                # 所有行同时抬起
        judge.update(1200.0, held=set())
        self.assertGreaterEqual(judge.counts[NEAR], 1, judge.counts)

    def test_short_tail_without_press_is_break_not_grace(self) -> None:
        """末区间不足 50ms 且完全没按下 → Break，优先于“结束前 50ms 宽限”。"""
        def tail_counts(release_ms: float):
            chart = Chart()
            chart.timing = TimingMap([BpmSegment(0.0, 120.0, 4)])
            chart.add(LaneNote("H", 0, 0, 0.0, 1.03))     # 末区间 [500, 515) = 15ms
            judge = Judge(chart)
            judge.on_press(0.0, lane=0)
            judge.update(10.0, held={0})
            judge.on_release(release_ms, lane=0)
            judge.update(600.0, held=set())
            return judge.counts

        blocked = tail_counts(499.0)                      # 尾区间开始前就松开
        self.assertEqual(blocked[BREAK], 1, blocked)      # 只有尾区间 Break
        self.assertEqual(blocked[EXACT], 3, blocked)
        graced = tail_counts(505.0)                       # 尾区间内松开（距结束 10ms）
        self.assertEqual(graced[BREAK], 0, graced)
        self.assertEqual(graced[EXACT], 4, graced)


if __name__ == "__main__":
    unittest.main()

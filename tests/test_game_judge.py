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
    def test_input_type_must_match_note_type(self) -> None:
        """6K 键只判 6K 按键、鼠标点击只判鼠标点击按键（两种输入不互相抢判）。"""
        chart = make_chart(
            [LaneNote("L1", 0, 0, 4.0), MouseClick("M1", 4.0, 0.5, 0.1)]
        )
        hit_ms = chart.timing.beat_to_song_ms(4.0)
        lane_judge = Judge(chart)
        result = lane_judge.on_press(hit_ms, lane=0)
        self.assertIsNotNone(result)
        self.assertEqual(result.note_id, "L1", "按 6K 键不该判鼠标点击按键")
        self.assertEqual(lane_judge.counts[BREAK], 0)

        click_judge = Judge(chart)
        result = click_judge.on_press(hit_ms, x=0.5)
        self.assertIsNotNone(result)
        self.assertEqual(result.note_id, "M1", "鼠标点击不该判 6K 按键")
        self.assertEqual(click_judge.counts[BREAK], 0)

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
        # 2 拍 = 4 个整半拍区间；不足半拍的**尾段也算一个**（这里恰好整除）
        self.assertEqual(judge.total_notes, 4)

    def test_partial_tail_is_an_extra_interval(self) -> None:
        """尾段不足半拍时也算一个判定区间。"""
        region = MouseRegion("R2", 0.0, 1.3, BoundaryCurve(0.2, 0.2), BoundaryCurve(0.8, 0.8))
        chart = make_chart([region])
        judge = Judge(chart)
        # [0,0.5) [0.5,1.0) + 尾段 [1.0,1.3) = 3 个区间
        self.assertEqual(judge.total_notes, 3)

    def test_always_inside_is_exact(self) -> None:
        chart = make_chart([self._region()])
        judge = Judge(chart)
        region = chart.mouse_notes[0]
        for step in range(0, 11):
            judge.update(step * 100.0, inside_hold=region)
        self.assertEqual(judge.counts[EXACT], 4)   # 2 拍 = 4 个区间
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
    def test_autoplay_scores_negative_and_judges_exact(self) -> None:
        """自动播放：完全过线即 EXACT（含区间），分数倍率 = -倍速^0.6。"""
        chart = make_chart([LaneNote("L1", 0, 0, 2.0), MouseClick("M1", 2.0, 0.5, 0.1)])
        judge = Judge(chart, autoplay=True)
        self.assertTrue(judge.autoplay)
        self.assertAlmostEqual(judge.score_multiplier, -1.0)          # 1.0x -> -1
        judge.rate = 2.0
        self.assertAlmostEqual(judge.score_multiplier, -(2.0 ** 0.6), places=6)
        judge.rate = 1.0
        hit_ms = chart.timing.beat_to_song_ms(2.0)
        judge.update(0.0)
        self.assertEqual(judge.judged_notes, 0, "还没到判定时间不该提前判")
        judge.update(hit_ms)
        self.assertEqual(judge.counts[EXACT], 2, "物件过线即 EXACT（不需要输入）")
        self.assertEqual(judge.counts[BREAK], 0)
        self.assertEqual(judge.score, -judge.accuracy_score - judge.exact_bonus, "负倍率时附加分变扣分")
        # 长按 / 空域的每个判定区间也全部 EXACT
        chart2 = make_chart([LaneNote("H1", 0, 0, 0.0, 3.0), MouseRegion("R1", 0.0, 3.0)])
        judge2 = Judge(chart2, autoplay=True)
        judge2.update(chart2.timing.beat_to_song_ms(8.0))
        self.assertEqual(judge2.counts[EXACT], judge2.total_notes)
        self.assertEqual(judge2.counts[BREAK], 0)

    def test_score_scales_with_rate_to_the_1_5(self) -> None:
        """分数 = 准度部分 × 倍速^0.6 + 附加分（附加分不吃倍率）。"""
        chart = make_chart([LaneNote("L1", 0, 0, 1.0)])
        judge = Judge(chart)
        judge.on_press(chart.timing.beat_to_song_ms(1.0), lane=0)     # EXACT
        accuracy_part = judge.accuracy_score
        bonus = judge.exact_bonus
        self.assertEqual(bonus, 1)
        self.assertEqual(judge.base_score, accuracy_part + bonus)
        self.assertEqual(judge.rate, 1.0)
        self.assertEqual(judge.score, accuracy_part + bonus)
        for rate in (0.5, 0.1, 1.5):
            judge.rate = rate
            self.assertEqual(
                judge.score,
                round(accuracy_part * (rate ** 0.6)) + bonus,
                msg="附加分不受倍率影响",
            )
            self.assertEqual(judge.scaled_score, round(accuracy_part * (rate ** 0.6)))
        # 倍速被夹在 0.1 ~ 3.0（上限 3 倍）
        self.assertEqual(Judge(chart, rate=1.5).rate, 1.5)
        self.assertEqual(Judge(chart, rate=9.0).rate, 1.5)
        self.assertEqual(Judge(chart, rate=0.01).rate, 0.1)

    def test_earliest_judge_time_wins_when_several_are_hittable(self) -> None:
        """同时可判多个物件时，取**判定时刻最靠前**的那个。"""
        chart = make_chart([LaneNote("L1", 0, 0, 4.0), LaneNote("L2", 0, 0, 4.05)])
        judge = Judge(chart)
        # 在 4.0 拍后 20ms 敲：两个都在范围内，应该判更早的 L1
        hit_ms = chart.timing.beat_to_song_ms(4.0) + 20.0
        result = judge.on_press(hit_ms, lane=0)
        self.assertIsNotNone(result)
        self.assertEqual(result.note_id, "L1")

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

    def test_combo_counts_hits_and_resets_on_break(self) -> None:
        """连击：命中 +1，Break 清零，并记录最大连击。"""
        chart = make_chart(
            [
                LaneNote("L1", 0, 0, 1.0),
                LaneNote("L2", 1, 1, 2.0),
                LaneNote("L3", 2, 2, 3.0),
                LaneNote("L4", 3, 3, 4.0),
            ]
        )
        judge = Judge(chart)
        for beat in (1.0, 2.0):
            judge.on_press(chart.timing.beat_to_song_ms(beat), lane=int(beat - 1))
        self.assertEqual(judge.combo, 2)
        # 第 3 个物件漏掉 -> Break，连击清零
        judge.update(chart.timing.beat_to_song_ms(3.0) + 200.0)
        self.assertEqual(judge.combo, 0)
        self.assertEqual(judge.max_combo, 2)
        judge.on_press(chart.timing.beat_to_song_ms(4.0), lane=3)
        self.assertEqual(judge.combo, 1)
        self.assertEqual(judge.max_combo, 2)


class HoldJudgeTests(unittest.TestCase):
    """长按：抬起事件 → Near；最后一个区间允许提前 ≤50ms 抬起仍算 EXACT。"""

    def _hold(self, start: float, end: float):
        chart = make_chart([LaneNote("H1", 0, 0, start, end)])
        return chart, Judge(chart)

    def test_hold_all_time_is_exact(self) -> None:
        chart, judge = self._hold(0.0, 2.0)
        hold = chart.lane_notes[0]
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=0)     # 开头 EXACT
        for step in range(0, 11):
            judge.update(step * 100.0, inside_hold=hold, held=True)
        self.assertEqual(judge.counts[BREAK], 0)
        self.assertGreaterEqual(judge.counts[EXACT], 4)

    def test_release_inside_interval_gives_near(self) -> None:
        """区间内出现抬起事件（且按下过）-> Near。"""
        chart, judge = self._hold(0.0, 2.0)
        hold = chart.lane_notes[0]
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=0)
        judge.update(100.0, inside_hold=hold, held=True)
        judge.update(300.0, inside_hold=None, held=False)     # 第 2 个区间内抬起
        judge.update(1000.0, inside_hold=None, held=False)
        self.assertGreaterEqual(judge.counts[NEAR], 1, judge.counts)

    def test_last_interval_allows_early_release(self) -> None:
        """最后一个区间：提前 ≤50ms 抬起仍算 EXACT。"""
        chart, judge = self._hold(0.0, 1.0)                   # 1 拍 = 500ms -> 2 个区间
        hold = chart.lane_notes[0]
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=0)
        judge.update(100.0, inside_hold=hold, held=True)
        judge.update(240.0, inside_hold=hold, held=True)
        judge.update(470.0, inside_hold=None, held=False)     # 结束前 30ms 抬起
        judge.update(600.0, inside_hold=None, held=False)
        self.assertEqual(judge.counts[NEAR], 0, judge.counts)
        self.assertEqual(judge.counts[EXACT], 3, judge.counts)   # 开头 + 两个区间

    def test_no_press_at_all_is_break(self) -> None:
        """整段都没按下 -> 每个区间都 Break。"""
        chart, judge = self._hold(0.0, 1.0)
        judge.update(100.0, inside_hold=None, held=False)
        judge.update(600.0, inside_hold=None, held=False)
        self.assertGreaterEqual(judge.counts[BREAK], 2, judge.counts)

    def test_multi_lane_hold_partial_release_keeps_exact(self) -> None:
        """多轨长按：只松开其中一行、其它行还按着 —— 不算“抬起”，仍是 EXACT。"""
        chart = make_chart([LaneNote("H2", 1, 3, 0.0, 1.0)])
        judge = Judge(chart)
        hold = chart.lane_notes[0]
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=1)
        judge.update(100.0, inside_hold=None, held={1, 2, 3})     # 三条轨道都按住
        judge.update(300.0, inside_hold=None, held={1, 2})        # 少了第 4 轨 -> 抬起
        judge.update(600.0, inside_hold=None, held={1, 2})
        self.assertEqual(judge.counts[NEAR], 0, judge.counts)
        self.assertEqual(judge.counts[BREAK], 0, judge.counts)
        self.assertGreaterEqual(judge.counts[EXACT], 3, judge.counts)

    def test_multi_lane_hold_all_rows_released_together_is_near(self) -> None:
        """多轨长按：出现“所有行同时抬起”的瞬间 -> 该区间 Near。"""
        chart = make_chart([LaneNote("H4", 1, 3, 0.0, 2.0)])
        judge = Judge(chart)
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=1)
        judge.update(100.0, inside_hold=None, held={1, 2, 3})
        judge.update(300.0, inside_hold=None, held=set())         # 全部同时抬起
        judge.update(1200.0, inside_hold=None, held=set())
        self.assertGreaterEqual(judge.counts[NEAR], 1, judge.counts)

    def test_multi_lane_hold_all_lanes_is_exact(self) -> None:
        chart = make_chart([LaneNote("H3", 1, 3, 0.0, 1.0)])
        judge = Judge(chart)
        judge.on_press(chart.timing.beat_to_song_ms(0.0), lane=1)
        for step in range(0, 7):
            judge.update(step * 100.0, inside_hold=None, held={1, 2, 3})
        self.assertEqual(judge.counts[BREAK], 0, judge.counts)
        self.assertGreaterEqual(judge.counts[EXACT], 3, judge.counts)

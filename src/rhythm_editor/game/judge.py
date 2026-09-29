"""游玩器判定核心（纯逻辑，便于单元测试）。

判定等级：``EXACT``（全大写，±25ms）/ ``Exact``（±50ms）/ ``Near``（±100ms）/
``Break``（漏掉或过早敲）。准度：EXACT 与 Exact 记 1，Near 记 0.5，Break 记 0。

规则要点（与需求一致）
----------------------
* 点击类（6K 单键 / 多轨按键 / 鼠标点击按键）：判定时刻 ±25/50/100ms 对应
  EXACT / Exact / Near；**提前 100~150ms** 的敲击算 Break；再早算“空敲”，
  **不消耗物件也没有惩罚**；物件过了 100ms 仍未命中 → Break。
  一次按键最多判定一个点击类物件（取时间上最接近的那个）。
* 长按开头与点击类相同；长按区间内与空域同样按“半拍一个判定区间”，
  只是把“离开空域”换成“松键”，而且**只要松过键就最多只能 Near**。
* 滑键：误差窗口与点击类相同，但会自动吸附到“滑动最准确的那一瞬间”：
  能做到 EXACT 立即触发；没到判定线时记录当前最好的等级；
  过了判定线后若出现更好的立即触发；到判定线 100ms 仍没有更好的就按记录的最好等级触发；
  完全没滑动 → Break。滑动方向不要求，只要**鼠标位移区间与滑键范围相交**即可。
* 空域：每半拍一个判定区间；区间内离开空域 ≤50ms → EXACT，≤100ms → Exact，
  超过 100ms 但只要有一瞬间在空域内 → Near，整段都不在 → Break。
* 空域 / 滑键的判定范围在谱面基础上**两边各放宽 0.01 单位**（0 宽度物件也能判定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..model.chart import Chart
from ..model.notes import LaneNote, MouseClick, MouseRegion, MouseSlide, Note
from .. import config

EXACT = "EXACT"
EXACT_MIXED = "Exact"
NEAR = "Near"
BREAK = "Break"

GRADES = (EXACT, EXACT_MIXED, NEAR, BREAK)
GRADE_ACCURACY = {EXACT: 1.0, EXACT_MIXED: 1.0, NEAR: 0.5, BREAK: 0.0}

WINDOW_EXACT_MS = 25.0
WINDOW_EXACT_MIXED_MS = 50.0
WINDOW_NEAR_MS = 100.0
EARLY_BREAK_MS = 150.0        # 提前 100~150ms = Break；更早算空敲
REGION_PAD = 0.01            # 空域 / 滑键判定范围两边各放宽
REGION_INTERVAL_BEATS = 0.5  # 空域与长按每半拍一个判定区间


def grade_for_offset(offset_ms: float) -> Optional[str]:
    """``offset_ms = 敲击时刻 − 判定时刻``（正数表示晚）。返回 None 表示空敲。"""
    if offset_ms > WINDOW_NEAR_MS or offset_ms < -EARLY_BREAK_MS:
        return None
    distance = abs(offset_ms)
    if distance <= WINDOW_EXACT_MS:
        return EXACT
    if distance <= WINDOW_EXACT_MIXED_MS:
        return EXACT_MIXED
    if distance <= WINDOW_NEAR_MS:
        return NEAR
    return BREAK            # 提前 100~150ms：算 Break


def better(a: str, b: str) -> str:
    """取更好的等级（EXACT > Exact > Near > Break）。"""
    order = {EXACT: 3, EXACT_MIXED: 2, NEAR: 1, BREAK: 0}
    return a if order.get(a, -1) >= order.get(b, -1) else b


@dataclass
class Judged:
    """一条判定结果。"""

    note_id: str
    grade: str
    beat: float                 # 物件判定时刻（拍）
    offset_ms: float = 0.0      # 敲击相对判定时刻的偏差（长按 / 空域区间为 0）

    @property
    def accuracy(self) -> float:
        return GRADE_ACCURACY[self.grade]


@dataclass
class _ClickState:
    """点击类物件的待判定状态。"""

    note: Note
    done: bool = False
    broken: bool = False


@dataclass
class _IntervalState:
    """长按 / 空域的逐区间判定状态。"""

    note: Note
    intervals: List[float] = field(default_factory=list)   # 每个区间的起始拍
    index: int = 0
    inside_ms: float = 0.0          # 当前区间内“在内”的累计时长
    outside_ms: float = 0.0         # 当前区间内“在内”的累计时长
    seen_inside: bool = False
    ever_outside: bool = False      # 长按：只要松开过就最多 Near
    released: bool = False          # 本区间内是否发生过“抬起”事件
    release_ms: float = 0.0         # 抬起事件发生的歌曲毫秒
    release_lead_ms: float = 0.0    # 抬起比区间结束早多少毫秒
    is_last: bool = False           # 是否是该长按 / 空域的最后一个区间
    end_beat: float = 0.0           # 物件本身的结束拍（最后一个区间在这里截断）


class Judge:
    """一张谱面的判定状态机（按“听到的歌曲毫秒”推进）。"""

    def __init__(self, chart: Chart, hit_sound=None) -> None:
        self.chart = chart
        self.timing = chart.timing
        self.results: List[Judged] = []
        self.notes: Dict[str, Note] = {n.id: n for n in chart.all_notes()}
        self._counts: Dict[str, int] = {g: 0 for g in GRADES}

        clicks, intervals = [], []
        for note in chart.all_notes():
            if isinstance(note, MouseSlide):
                clicks.append(_ClickState(note))       # 滑键也按“一次判定”计
            elif isinstance(note, MouseRegion):
                intervals.append(self._make_intervals(note))
            elif isinstance(note, MouseClick):
                clicks.append(_ClickState(note))
            elif isinstance(note, LaneNote):
                if note.is_hold:
                    clicks.append(_ClickState(note))      # 开头与点击类相同
                    intervals.append(self._make_intervals(note))
                else:
                    clicks.append(_ClickState(note))
        self._clicks = clicks
        self._intervals = [s for s in intervals if s is not None]
        self._hovered = None
        self._active = []
        self._last_ms = 0.0
        self._slide_best: Dict[str, str] = {}
        self._slide_track: Dict[str, bool] = {}
        self._mouse_x = 0.0
        self._mouse_prev = 0.0
        self._mouse_seen = False
        self._held_prev: set = set(range(config.LANE_COUNT))
        self._samples: List[Tuple[float, frozenset, Optional[str]]] = []

    # ------------------------------------------------------------ 输入
    def on_press(self, now_ms: float, lane: Optional[int] = None, x: Optional[float] = None) -> Optional[Judged]:
        """一次按键 / 点击：最多判定一个点击类物件（取时间上最接近的）。"""
        best: Optional[Tuple[float, float, _ClickState]] = None   # (判定时刻, 偏差, 状态)
        for state in self._clicks:
            note = state.note
            if state.done or isinstance(note, (MouseRegion,)):
                continue
            if isinstance(note, MouseSlide):
                continue
            if isinstance(note, MouseClick) and x is not None:
                if not self._in_x(note, x):
                    continue
            elif isinstance(note, LaneNote):
                if lane is not None and not (note.lane_start <= lane <= note.lane_end):
                    continue
            judge_ms = self.timing.beat_to_song_ms(getattr(note, "head_beat", 0.0))
            offset = now_ms - judge_ms
            if grade_for_offset(offset) is None:
                continue
            # 同时可判多个时取**判定时刻最靠前**的那个
            if best is None or judge_ms < best[0]:
                best = (judge_ms, offset, state)
        if best is None:
            return None      # 空敲：不消耗、无惩罚
        _, offset, state = best
        grade = grade_for_offset(offset) or BREAK
        state.done = True
        beat = getattr(state.note, "head_beat", 0.0)
        result = self._judge_result(state.note.id, grade, beat, offset)
        if isinstance(state.note, LaneNote) and state.note.is_hold:
            for interval in self._intervals:
                if interval.note.id == state.note.id:
                    interval.index = 0
        return result

    def on_move(self, now_ms: float, x: float) -> Optional[Judged]:
        """鼠标移动：滑键按“最准确瞬间”吸附判定。"""
        self._mouse_x = float(x)
        # 第一次采样只用来定位，不算“移动”（否则会凭空划出巨大位移）
        moved = self._mouse_seen and abs(self._mouse_x - self._mouse_prev) > 1e-4
        self._mouse_seen = True
        best_result: Optional[Judged] = None
        for state in self._clicks:
            note = state.note
            if state.done or not isinstance(note, MouseSlide):
                continue
            judge_ms = self.timing.beat_to_song_ms(note.beat)
            offset = now_ms - judge_ms
            if offset > WINDOW_NEAR_MS:
                # 过了判定线 100ms：用记录到的最好等级收尾（完全没有则 Break）
                grade = self._slide_best.get(note.id, BREAK)
                state.done = True
                best_result = self._judge_result(note.id, grade, note.beat)
                continue
            lo, hi = note.x_range()
            lo, hi = min(lo, hi) - REGION_PAD, max(lo, hi) + REGION_PAD
            if moved and self._segment_hits(lo, hi):
                grade = grade_for_offset(offset)
                if grade is not None:
                    self._slide_best[note.id] = better(
                        self._slide_best.get(note.id, BREAK), grade
                    )
            known = self._slide_best.get(note.id)
            if known == EXACT:
                state.done = True
                best_result = self._judge_result(note.id, EXACT, note.beat)
        self._mouse_prev = self._mouse_x
        return best_result

    def _segment_hits(self, lo: float, hi: float) -> bool:
        """鼠标这一帧的位移区间是否与滑键范围相交（不要求滑满）。"""
        a, b = self._mouse_prev, self._mouse_x
        return max(min(a, b), lo) <= min(max(a, b), hi) + 1e-9

    @staticmethod
    def _in_x(note: MouseClick, x: float) -> bool:
        lo, hi = note.x_range()
        return min(lo, hi) - REGION_PAD <= x <= max(lo, hi) + REGION_PAD

    # ------------------------------------------------------------ 统计
    @property
    def counts(self) -> Dict[str, int]:
        return dict(self._counts)

    @property
    def total_notes(self) -> int:
        """总物件数（长按 / 空域按它们的判定区间数计）。"""
        return len(self._clicks) + sum(len(s.intervals) for s in self._intervals)

    @property
    def judged_notes(self) -> int:
        return len(self.results)

    @property
    def accuracy(self) -> float:
        if not self.results:
            return 1.0
        return sum(r.accuracy for r in self.results) / len(self.results)

    @property
    def score(self) -> int:
        """分数 = 已取得准度 / 总物件数 × 1e8，四舍五入，再加 EXACT 数。"""
        total = max(1, self.total_notes)
        base = sum(r.accuracy for r in self.results) / total * 100_000_000
        return int(round(base)) + self._counts[EXACT]

    # ------------------------------------------------------------ 内部
    def _make_intervals(self, note: Note) -> _IntervalState:
        beat = float(getattr(note, "beat_start", getattr(note, "beat", 0.0)))
        end = float(getattr(note, "beat_end", beat))
        if end <= beat:
            end = beat + REGION_INTERVAL_BEATS
        starts: List[float] = []
        current = beat
        while current < end - 1e-9:
            starts.append(current)
            current += REGION_INTERVAL_BEATS
        # 尾段不足半拍时，最后一个区间的结束时刻会超过物件末尾，
        # 由 ``end_beat`` 夹住（它本身就是“最后一个判定区间”）
        return _IntervalState(note=note, intervals=starts, end_beat=end)

    def _judge_result(self, note_id: str, grade: str, beat: float, offset: float = 0.0) -> Judged:
        result = Judged(note_id, grade, beat, offset)
        self.results.append(result)
        self._counts[grade] += 1
        return result

    def _interval_grade(self, state: _IntervalState) -> str:
        if not state.seen_inside:
            return BREAK
        if state.note.__class__.__name__ == "LaneNote":
            # 长按：区间内**出现过抬起事件**就最多 Near；
            # 但最后一个区间允许提前 <=50ms 抬起，仍算 EXACT
            if not state.released:
                return EXACT
            if getattr(state, "is_last", False) and state.release_lead_ms <= 50.0:
                return EXACT
            return NEAR
        if state.outside_ms <= 0.0:
            return EXACT
        if state.outside_ms <= 50.0:
            return EXACT
        if state.outside_ms <= 100.0:
            return EXACT_MIXED
        return NEAR

    # ------------------------------------------------------------ 推进
    def update(
        self,
        now_ms: float,
        inside_click: Optional[Note] = None,
        inside_hold: Optional[Note] = None,
        held=True,
    ) -> List[Judged]:
        """按“听到的歌曲毫秒”推进一次；返回本次新增的判定。

        ``held`` 可以是 ``bool``（全部按住 / 全部松开），也可以是**按住的轨道集合**
        （多轨长按要求它覆盖的每条轨道都按住）。
        """
        out: List[Judged] = []
        delta = max(0.0, min(50.0, now_ms - self._last_ms))
        self._last_ms = now_ms
        held_lanes = self._normalize_held(held)
        # 本帧是否发生了“抬起”事件（长按判定用）
        released_now = bool(self._held_prev - held_lanes)
        self._held_prev = set(held_lanes)
        # 逐帧采样：(歌曲毫秒, 按住的轨道集合, 鼠标在哪个空域里)
        self._samples.append(
            (now_ms, frozenset(held_lanes), None if inside_hold is None else inside_hold.id)
        )
        if len(self._samples) > 20000:      # 剪掉过老的采样，避免长曲内存增长
            self._samples = self._samples[-10000:]
        # 只保留最近 20 秒的采样，避免长曲目内存增长
        for state in self._clicks:
            note = state.note
            if state.done:
                continue
            judge_ms = self.timing.beat_to_song_ms(getattr(note, "head_beat", 0.0))
            if now_ms - judge_ms > WINDOW_NEAR_MS and not isinstance(note, MouseSlide):
                state.done = True
                self._judge_result(note.id, BREAK, getattr(note, "head_beat", 0.0))
        for state in self._intervals:
            out.extend(
                self._update_interval(
                    state, now_ms, inside_hold, held, delta, released_now
                )
            )
        return out

    @staticmethod
    def _normalize_held(held) -> set:
        """``held`` -> 按住的轨道集合（兼容 bool 写法）。"""
        if held is True:
            return set(range(config.LANE_COUNT))
        if held is False or held is None:
            return set()
        try:
            return {int(lane) for lane in held}
        except TypeError:  # pragma: no cover - 兜底
            return set()

    def _update_interval(
        self,
        state: _IntervalState,
        now_ms: float,
        inside_hold,
        held: bool,
        delta: float,
        released_now: bool = False,
    ) -> List[Judged]:
        out: List[Judged] = []
        while state.index < len(state.intervals):
            start_beat = state.intervals[state.index]
            end_beat = start_beat + REGION_INTERVAL_BEATS
            start_ms = self.timing.beat_to_song_ms(start_beat)
            end_ms = self.timing.beat_to_song_ms(end_beat)
            if state.index == len(state.intervals) - 1:
                end_ms = min(end_ms, self.timing.beat_to_song_ms(state.end_beat))
            if now_ms < end_ms:
                break
            out.append(self._grade_interval(state, start_ms, end_ms, start_beat))
            state.index += 1
        return out

    def _window_samples(self, start_ms: float, end_ms: float):
        """区间窗口内的采样（含边界前一个点，便于识别跨边界的事件）。"""
        window = []
        previous = None
        for sample in self._samples:
            if sample[0] < start_ms:
                previous = sample
                continue
            if sample[0] > end_ms:
                break
            if previous is not None and not window:
                window.append(previous)
            window.append(sample)
            previous = sample
        return window

    def _grade_interval(
        self, state: _IntervalState, start_ms: float, end_ms: float, start_beat: float
    ) -> Judged:
        """按**采样时间戳**给一个区间评级：跨区间边界的按下 / 抬起事件不会算错。

        长按规则（每区间）：有按下且无抬起 -> EXACT；有按下且有抬起 -> Near；
        从未按下 -> Break。**结尾宽限**：最后一次抬起发生在
        `长按结束 − 50ms` 之内时，这段窗口覆盖到的区间（可能跨多个）都算 EXACT。
        """
        note = state.note
        is_hold = type(note).__name__ == "LaneNote"
        lanes = set(range(note.lane_start, note.lane_end + 1)) if is_hold else set()
        hold_end_ms = self.timing.beat_to_song_ms(state.end_beat) if is_hold else end_ms
        pressed = False
        released_at: Optional[float] = None
        inside_seconds = 0.0
        outside_seconds = 0.0
        seen_inside = False
        previous = None
        for sample in self._samples:
            stamp, held_now, inside_id = sample
            if stamp < start_ms:
                previous = sample
                continue
            if stamp > end_ms:
                break
            # “开始前就按住 / 已经在空域里”也算这个区间内有按下
            if previous is not None and previous[0] < start_ms:
                before = lanes.issubset(previous[1]) if is_hold else (previous[2] == note.id)
                if before:
                    pressed = pressed or is_hold
                    seen_inside = seen_inside or (not is_hold)
            inside = lanes.issubset(held_now) if is_hold else (inside_id == note.id)
            if inside:
                pressed = pressed or is_hold
                seen_inside = True
            if is_hold and previous is not None and lanes.issubset(previous[1]) and not lanes.issubset(held_now):
                released_at = stamp
            if previous is not None and previous[0] >= start_ms:
                gap = stamp - previous[0]
                before_inside = (
                    lanes.issubset(previous[1]) if is_hold else (previous[2] == note.id)
                )
                if before_inside:
                    inside_seconds += gap
                else:
                    outside_seconds += gap
            previous = sample
        if is_hold:
            # 多轨长按：“抬起”只在**所有行同时抬起**的瞬间才成立；
            # 只要其中任意一行还在按，就当作仍在按住（分段松键不降级）。
            all_up_ms = None
            any_press = False
            for sample in self._window_samples(start_ms, end_ms):
                stamp, held_now, _inside_id = sample
                if lanes & set(held_now):
                    any_press = True
                if lanes.isdisjoint(held_now):
                    all_up_ms = stamp
            if not any_press:
                return self._judge_result(note.id, BREAK, start_beat)
            if all_up_ms is None:
                return self._judge_result(note.id, EXACT, start_beat)
            grace = (hold_end_ms - all_up_ms) <= 50.0
            if grace and end_ms >= all_up_ms - 1e-9:
                return self._judge_result(note.id, EXACT, start_beat)
            if start_ms <= all_up_ms <= end_ms:
                return self._judge_result(note.id, NEAR, start_beat)
            return self._judge_result(note.id, EXACT, start_beat)
        if not seen_inside:
            return self._judge_result(note.id, BREAK, start_beat)
        outside = outside_seconds
        if outside <= 50.0:
            grade = EXACT
        elif outside <= 100.0:
            grade = EXACT_MIXED
        else:
            grade = NEAR
        return self._judge_result(note.id, grade, start_beat)

    def _inside(self, note: Note, inside_hold, held: bool) -> bool:
        if isinstance(note, MouseRegion):
            return bool(inside_hold is not None and inside_hold.id == note.id)
        return bool(held)       # 长按：按键是否仍然按住

    # ------------------------------------------------------------ 长按区间
    def hold_interval_grade(self, note_id: str, held_all_time: bool, seen_inside: bool,
                            outside_ms: float) -> str:
        """长按一个区间结束时给等级（松键 → 最多 Near；一直按住 → EXACT）。"""
        _ = note_id
        if not seen_inside:
            return BREAK
        if not held_all_time and outside_ms > 0.0:
            return NEAR if outside_ms > 100.0 else NEAR
        if outside_ms <= 50.0:
            return EXACT
        if outside_ms <= 100.0:
            return EXACT_MIXED
        return NEAR

"""游玩器判定核心（纯逻辑，便于单元测试）。

判定等级：``EXACT``（全大写，±25ms）/ ``Exact``（±50ms）/ ``Near``（±100ms）/
``Break``（漏掉或过早敲）。准度：EXACT 与 Exact 记 1，Near 记 0.5，Break 记 0。

规则要点
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
* **输入带时间戳**：按键 / 鼠标事件用自身时间戳（``event.timestamp``）写进一条
  “状态时间线”，每帧再补一次采样兜底；长按 / 空域的区间评级在这条时间线上做
  分段常数积分。因此一帧跨过多个区间（极高 BPM、掉帧）也不会把区间误判成 Break。
* 空域 / 滑键的判定范围在谱面基础上**两边各放宽 0.01 单位**（0 宽度物件也能判定）。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..model.chart import Chart
from ..model.notes import LaneNote, MouseClick, MouseRegion, MouseSlide, Note
from .. import config
from .settings import RATE_MAX, RATE_MIN

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


def grade_for_offset(offset_ms: float, rate: float = 1.0) -> Optional[str]:
    """``offset_ms = 敲击时刻 − 判定时刻``（**歌曲毫秒**，正数表示晚）；None = 空敲。

    **允许误差是真实时间，不随倍速变**：判定时钟按倍速推进，所以歌曲毫秒窗口
    = 常量 × 倍速（倍速 0.4 时 25ms 的真实误差 = 10 歌曲毫秒）。
    判定区间长度（半拍）仍然按谱面 BPM 的歌曲毫秒算，因此不同倍速判定数量一致。
    """
    scale = max(1e-6, float(rate))
    if offset_ms > WINDOW_NEAR_MS * scale or offset_ms < -EARLY_BREAK_MS * scale:
        return None
    distance = abs(offset_ms)
    if distance <= WINDOW_EXACT_MS * scale:
        return EXACT
    if distance <= WINDOW_EXACT_MIXED_MS * scale:
        return EXACT_MIXED
    if distance <= WINDOW_NEAR_MS * scale:
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


@dataclass(frozen=True)
class _State:
    """输入时间线上的一个“分段常数”状态。

    ``t`` 是**歌曲毫秒**；从这一刻起，``held`` 里的轨道被按住、鼠标在 ``inside``
    这个空域里（``None`` = 不在任何空域）。按键 / 鼠标事件用自身时间戳写入，
    每帧再补一条采样兜底，因此区间评级不再依赖“哪一帧刚好落在区间里”。
    """

    t: float
    held: frozenset = frozenset()
    inside: Optional[str] = None


_KEEP = object()      # _push() 的哨兵：保持当前的空域状态不变


class Judge:
    """一张谱面的判定状态机（按“听到的歌曲毫秒”推进）。"""

    def __init__(
        self,
        chart: Chart,
        hit_sound=None,
        rate: float = 1.0,
        autoplay: bool = False,
    ) -> None:
        self.chart = chart
        self.timing = chart.timing
    # 倍速：只影响分数倍率（分数 = 当前计分 × 倍速^0.6），不影响判定窗口
        self.rate = min(RATE_MAX, max(RATE_MIN, float(rate)))
        # 自动播放：不吃输入，物件 / 判定区间完全过线时自动判 EXACT；分数倍率固定 -1
        self.autoplay = bool(autoplay)
        self.results: List[Judged] = []
        self.notes: Dict[str, Note] = {n.id: n for n in chart.all_notes()}
        self._counts: Dict[str, int] = {g: 0 for g in GRADES}
        self.combo = 0
        self.max_combo = 0

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
        self._slide_best: Dict[str, str] = {}
        self._slide_track: Dict[str, bool] = {}
        self._mouse_x = 0.0
        self._mouse_prev = 0.0
        self._mouse_seen = False
        # 输入时间线（见 _State）：事件带精确时间戳，帧采样兜底
        self._timeline: List[_State] = []
        self._held: set = set()
        self._inside: Optional[str] = None

    # ------------------------------------------------------------ 输入
    def on_press(self, now_ms: float, lane: Optional[int] = None, x: Optional[float] = None) -> Optional[Judged]:
        """一次按键 / 点击：最多判定一个点击类物件（取判定时刻最靠前的）。

        **类型必须匹配**：按 6K 键（``lane=``）只判 6K 按键，鼠标点击（``x=``）只判
        鼠标点击按键 —— 否则两种输入会互相“抢判”，出现该判的没判、不该判的判了。

        ``now_ms`` 应当是该**输入事件**的时间戳换算出的歌曲毫秒（不是处理它的那一帧）。
        """
        if lane is not None:
            self._held.add(int(lane))
            self._push(now_ms)
        best: Optional[Tuple[float, float, _ClickState]] = None   # (判定时刻, 偏差, 状态)
        for state in self._clicks:
            note = state.note
            if state.done or isinstance(note, (MouseRegion, MouseSlide)):
                continue
            if isinstance(note, MouseClick):
                if x is None or not self._in_x(note, x):
                    continue
            elif isinstance(note, LaneNote):
                if lane is None or not (note.lane_start <= lane <= note.lane_end):
                    continue
            else:                                  # pragma: no cover - 兜底
                continue
            judge_ms = self.timing.beat_to_song_ms(getattr(note, "head_beat", 0.0))
            offset = now_ms - judge_ms
            if grade_for_offset(offset, self.rate) is None:
                continue
            # 同时可判多个时取**判定时刻最靠前**的那个
            if best is None or judge_ms < best[0]:
                best = (judge_ms, offset, state)
        if best is None:
            return None      # 空敲：不消耗、无惩罚
        _, offset, state = best
        grade = grade_for_offset(offset, self.rate) or BREAK
        state.done = True
        beat = getattr(state.note, "head_beat", 0.0)
        result = self._judge_result(state.note.id, grade, beat, offset)
        if isinstance(state.note, LaneNote) and state.note.is_hold:
            for interval in self._intervals:
                if interval.note.id == state.note.id:
                    interval.index = 0
        return result

    def on_release(self, now_ms: float, lane: Optional[int] = None) -> None:
        """按键抬起（``lane=None`` = 全部抬起，比如窗口失焦）。"""
        if lane is None:
            self._held.clear()
        else:
            self._held.discard(int(lane))
        self._push(now_ms)

    def on_inside(self, now_ms: float, inside_hold: Optional[Note]) -> None:
        """鼠标所在空域发生变化（由 MOUSEMOTION 事件时间戳调用）。"""
        self._push(now_ms, inside=None if inside_hold is None else inside_hold.id)

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
            if offset > WINDOW_NEAR_MS * self.rate:
                # 过了判定线 100ms：用记录到的最好等级收尾（完全没有则 Break）
                grade = self._slide_best.get(note.id, BREAK)
                state.done = True
                best_result = self._judge_result(note.id, grade, note.beat)
                continue
            lo, hi = note.x_range()
            lo, hi = min(lo, hi) - REGION_PAD, max(lo, hi) + REGION_PAD
            if moved and self._segment_hits(lo, hi):
                grade = grade_for_offset(offset, self.rate)
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
    def accuracy_score(self) -> int:
        """由准度决定的那部分：已取得准度 / 总物件数 × 1e8，四舍五入。"""
        total = max(1, self.total_notes)
        return int(round(sum(r.accuracy for r in self.results) / total * 100_000_000))

    @property
    def exact_bonus(self) -> int:
        """EXACT 数（附加分 / 额外扣分的基础值）。"""
        return self._counts[EXACT]

    @property
    def exact_adjustment(self) -> int:
        """附加调整：倍率为正时每个 EXACT 额外 +1；**倍率为负时为额外扣分（-1）**。

        两种情况都**不受倍速大小影响**（不吃倍率），只跟随倍率的正负号。
        """
        bonus = self.exact_bonus
        return -bonus if self.score_multiplier < 0 else bonus

    @property
    def base_score(self) -> int:
        """基础计分 = 准度部分 + 附加分（不加倍率）。"""
        return self.accuracy_score + self.exact_bonus

    @property
    def scaled_score(self) -> int:
        """结算页展示用：只把准度部分乘倍率（附加分单独写出来）。"""
        return int(round(self.accuracy_score * self.score_multiplier))

    @property
    def score(self) -> int:
        """最终分数 = 准度部分 × 分数倍率 + 附加调整（倍率为负时是扣分）。"""
        return self.scaled_score + self.exact_adjustment

    @property
    def score_multiplier(self) -> float:
        """分数倍率：正常是倍速^0.6；**自动播放取它的相反数（-倍速^0.6）**。"""
        base = self.rate ** 0.6
        return -base if self.autoplay else base

    @property
    def break_count(self) -> int:
        return self._counts[BREAK]

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
        if grade == BREAK:
            self.combo = 0
        else:
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
        return result

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

        这里写入的是**每帧一次的兜底采样**；按键 / 鼠标事件的精确时间戳由
        :meth:`on_press` / :meth:`on_release` / :meth:`on_inside` 写入时间线。
        """
        out: List[Judged] = []
        held_lanes = self._normalize_held(held)
        if held_lanes != self._held:
            # 事件流没覆盖到（失焦 / 外部改动）时，以调用方状态为准
            self._push(now_ms, held=held_lanes)
        self._push(now_ms, inside=None if inside_hold is None else inside_hold.id)
        for state in self._clicks:
            note = state.note
            if state.done:
                continue
            if self.autoplay:
                # 自动播放：物件到达判定线即判 EXACT（不需要输入）
                judge_ms = self.timing.beat_to_song_ms(getattr(note, "head_beat", 0.0))
                if now_ms >= judge_ms:
                    state.done = True
                    out.append(
                        self._judge_result(note.id, EXACT, getattr(note, "head_beat", 0.0))
                    )
                continue
            judge_ms = self.timing.beat_to_song_ms(getattr(note, "head_beat", 0.0))
            if (now_ms - judge_ms > WINDOW_NEAR_MS * self.rate
                    and not isinstance(note, MouseSlide)):
                state.done = True
                self._judge_result(note.id, BREAK, getattr(note, "head_beat", 0.0))
        for state in self._intervals:
            out.extend(self._update_interval(state, now_ms))
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

    # ------------------------------------------------------------ 输入时间线
    def _push(self, t: float, *, held: Optional[set] = None, inside=_KEEP) -> None:
        """把一条状态写进时间线：按键 / 鼠标事件带精确时间戳，帧采样负责兜底。"""
        if held is not None:
            self._held = {int(lane) for lane in held}
        if inside is not _KEEP:
            self._inside = inside
        entry = _State(float(t), frozenset(self._held), self._inside)
        if self._timeline and entry.t < self._timeline[-1].t:
            # 事件时间戳可能略早于上一帧采样：按时间插入
            keys = [state.t for state in self._timeline]
            self._timeline.insert(bisect.bisect_right(keys, entry.t), entry)
        elif (self._timeline
              and self._timeline[-1].held == entry.held
              and self._timeline[-1].inside == entry.inside):
            return                      # 状态没变：不重复记录
        else:
            self._timeline.append(entry)
        if len(self._timeline) > 20000:      # 剪掉过老的状态，避免长曲内存增长
            del self._timeline[: len(self._timeline) - 10000]

    def _states_between(self, start_ms: float, end_ms: float) -> List[_State]:
        """区间 ``[start, end]`` 的状态分段（含进入区间时的状态与区间内的每次变化）。"""
        if not self._timeline:
            return []
        index = bisect.bisect_right(self._timeline, start_ms, key=lambda s: s.t) - 1
        out: List[_State] = []
        if index >= 0:
            out.append(self._timeline[index])
            rest = self._timeline[index + 1:]
        else:
            rest = self._timeline
        for state in rest:
            if state.t > end_ms:
                break
            out.append(state)
        if not out:
            first = self._timeline[0]
            if first.t <= end_ms:
                out.append(first)
        return out

    @staticmethod
    def _outside_ms(
        states: List[_State], start_ms: float, end_ms: float, inside_id: str
    ) -> float:
        """``[start, end]`` 内“不在这个空域里”的毫秒数（分段常数积分，含首尾）。"""
        if end_ms <= start_ms:
            return 0.0
        if not states:
            return end_ms - start_ms
        outside = 0.0
        for index, state in enumerate(states):
            begin = max(state.t, start_ms)
            stop = states[index + 1].t if index + 1 < len(states) else end_ms
            stop = min(stop, end_ms)
            if stop > begin and state.inside != inside_id:
                outside += stop - begin
        return outside

    def _update_interval(self, state: _IntervalState, now_ms: float) -> List[Judged]:
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
            if self.autoplay:
                # 自动播放：判定区间完全过线即 EXACT
                out.append(self._judge_result(state.note.id, EXACT, start_beat))
            else:
                out.append(self._grade_interval(state, start_ms, end_ms, start_beat))
            state.index += 1
        return out

    def _grade_interval(
        self, state: _IntervalState, start_ms: float, end_ms: float, start_beat: float
    ) -> Judged:
        """在**输入时间线**上给一个区间评级（分段常数积分）。

        这样即使一帧跨过好几个区间、区间里一个采样点都没有，也能用进入区间时的状态
        与事件时间戳正确评级，不会把“全程按住 / 全程在空域内”判成 Break。

        长按规则（每区间）：任意一行按下过 → 至少不 Break；区间内出现“所有行同时抬起”
        → Near（最后一个区间在结束前 ≤50ms 抬起仍算 EXACT）。
        空域规则：区间内不在区域里的累计时长 ≤50ms → EXACT、≤100ms → Exact、
        更久但进去过 → Near、整段都没进去 → Break。
        """
        note = state.note
        states = self._states_between(start_ms, end_ms)
        if isinstance(note, LaneNote):
            lanes = set(range(note.lane_start, note.lane_end + 1))
            hold_end_ms = self.timing.beat_to_song_ms(state.end_beat)
            any_press = False
            all_up_ms: Optional[float] = None
            for entry in states:
                held_now = set(entry.held)
                if lanes & held_now:
                    any_press = True
                if entry.t >= start_ms and lanes.isdisjoint(held_now):
                    all_up_ms = entry.t
            if not any_press:
                return self._judge_result(note.id, BREAK, start_beat)
            if all_up_ms is None:
                return self._judge_result(note.id, EXACT, start_beat)
            grace = (hold_end_ms - all_up_ms) <= 50.0 * self.rate
            if grace and end_ms >= all_up_ms - 1e-9:
                return self._judge_result(note.id, EXACT, start_beat)
            if start_ms <= all_up_ms <= end_ms:
                return self._judge_result(note.id, NEAR, start_beat)
            return self._judge_result(note.id, EXACT, start_beat)
        outside = self._outside_ms(states, start_ms, end_ms, note.id)
        if outside >= (end_ms - start_ms) - 1e-9:
            return self._judge_result(note.id, BREAK, start_beat)   # 整段都没进去
        if outside <= 50.0 * self.rate:
            grade = EXACT
        elif outside <= 100.0 * self.rate:
            grade = EXACT_MIXED
        else:
            grade = NEAR
        return self._judge_result(note.id, grade, start_beat)

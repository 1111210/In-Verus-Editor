"""轨道编号与临时轨道：6K / 9K 的映射，以及轨道数改变事件。

6K 的原始轨道编号为 0..5（谱面文件同此编号）。三条临时轨道插在
1/2、2/3、3/4 条原始轨道之间，谱面文件中记为 1.5 / 2.5 / 3.5；
9K 显示时它们分别占据第 3、5、7 条轨道。

轨道数改变事件描述三条临时轨道共同的淡入 / 淡出：三条临时轨道始终同时
出现、同时消失。谱面开始时它们都不存在，所以第一个事件必须是淡入。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config

EPS = 1e-9

TEMP_LANE_POSITIONS: Tuple[float, ...] = tuple(
    float(value) for value in config.TEMPORARY_LANE_POSITIONS
)
LANE_POSITIONS: Tuple[float, ...] = tuple(float(value) for value in config.LANE_POSITIONS)


# ---------------------------------------------------------------- 编号与显示
def _format_number(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def format_lane_number(value: float) -> int | float:
    """谱面文件中的轨道编号：整数轨道写整数，临时轨道写 1.5 / 2.5 / 3.5。"""
    number = float(value)
    if abs(number - round(number)) <= 1e-9:
        return int(round(number))
    return round(number, 6)


def is_temporary_lane(value: float) -> bool:
    number = float(value)
    return any(abs(number - temp) <= 1e-6 for temp in TEMP_LANE_POSITIONS)


def is_lane_position(value: float) -> bool:
    """是否为可写入谱面的轨道编号（0..5 或 1.5 / 2.5 / 3.5）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return any(abs(number - candidate) <= 1e-6 for candidate in LANE_POSITIONS)


def normalize_lane_position(value: float, fallback: float = 0.0) -> float:
    """把任意轨道编号夹到 0..5 并吸附到最近一个合法轨道位置。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(fallback)
    if not math.isfinite(number):
        number = float(fallback)
    if number <= LANE_POSITIONS[0]:
        return LANE_POSITIONS[0]
    if number >= LANE_POSITIONS[-1]:
        return LANE_POSITIONS[-1]
    return min(LANE_POSITIONS, key=lambda candidate: abs(candidate - number))


def lane_position_index(value: float) -> int:
    """合法轨道位置在 LANE_POSITIONS 中的下标（0..8）。"""
    position = normalize_lane_position(value)
    for index, candidate in enumerate(LANE_POSITIONS):
        if abs(candidate - position) <= 1e-9:
            return index
    return 0


def lane_position_from_index(index: int) -> float:
    """LANE_POSITIONS 下标 -> 轨道编号。"""
    return LANE_POSITIONS[max(0, min(len(LANE_POSITIONS) - 1, int(index)))]


def lane_span_length(lane_start: float, lane_end: float) -> int:
    """两条轨道之间包含多少条物理轨道（含端点）。"""
    low = lane_position_index(min(lane_start, lane_end))
    high = lane_position_index(max(lane_start, lane_end))
    return high - low + 1


def lane_display_index(value: float, nine_key: bool) -> int:
    """轨道编号 -> 显示轨道下标。

    6K 模式下临时轨道折叠到左侧的原始轨道（1.5 -> 1、2.5 -> 2、3.5 -> 3）；
    9K 模式下按插入后的 0..8 排列。
    """
    position = normalize_lane_position(value)
    if nine_key:
        return lane_position_index(position)
    return int(math.floor(position + 1e-9))


def lane_covers_original(lane_start: float, lane_end: float) -> bool:
    """轨道区间是否覆盖至少一条原始轨道（0..5）。"""
    low, high = sorted(
        (normalize_lane_position(lane_start), normalize_lane_position(lane_end))
    )
    return any(
        low - 1e-9 <= float(index) <= high + 1e-9
        for index in range(config.LANE_COUNT)
    )


def lane_display_span(
    lane_start: float, lane_end: float, nine_key: bool
) -> Tuple[int, int]:
    """轨道区间 -> 显示轨道下标区间。

    6K 模式按区间内实际覆盖的原始轨道折叠：跨 2.5..3.5 只覆盖原始第 3 轨，
    因此显示为单条第 4 轨；区间不覆盖任何原始轨道时（例如 1.5..1.5）才
    折叠到左侧原始轨道。9K 模式直接映射两端。
    """
    low, high = sorted(
        (normalize_lane_position(lane_start), normalize_lane_position(lane_end))
    )
    if nine_key:
        return lane_position_index(low), lane_position_index(high)
    covered = [
        index
        for index in range(config.LANE_COUNT)
        if low - 1e-9 <= index <= high + 1e-9
    ]
    if covered:
        return covered[0], covered[-1]
    index = lane_display_index(low, False)
    return index, index


def lane_from_display_index(index: int, nine_key: bool) -> float:
    """显示轨道下标 -> 轨道编号（9K 下标 2/4/6 对应临时轨道 1.5/2.5/3.5）。"""
    if nine_key:
        return lane_position_from_index(index)
    return float(max(0, min(config.LANE_COUNT - 1, int(index))))


def lane_ui_number(value: float) -> float:
    """界面使用的 1 基轨道编号（临时轨道因此是 2.5 / 3.5 / 4.5）。"""
    return normalize_lane_position(value) + 1.0


def lane_ui_text(value: float) -> str:
    number = _format_number(lane_ui_number(value))
    return f"{number}（临时）" if is_temporary_lane(value) else number


def lane_span_ui_text(lane_start: float, lane_end: float) -> str:
    low, high = sorted((normalize_lane_position(lane_start), normalize_lane_position(lane_end)))
    if abs(low - high) <= 1e-9:
        return lane_ui_text(low)
    text = f"{_format_number(lane_ui_number(low))}-{_format_number(lane_ui_number(high))}"
    if is_temporary_lane(low) or is_temporary_lane(high):
        text += "（含临时）"
    return text


def lane_selection_labels() -> List[str]:
    return [lane_ui_text(position) for position in LANE_POSITIONS]


def lane_position_from_label(label: str) -> Optional[float]:
    for position in LANE_POSITIONS:
        if lane_ui_text(position) == label:
            return position
    return None


# ---------------------------------------------------------------- 事件模型
@dataclass(frozen=True)
class LaneChangeEvent:
    """一条轨道数改变事件：三条临时轨道在 [start_beat, end_beat] 内一起淡入 / 淡出。"""

    kind: str
    start_beat: float
    end_beat: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.kind,
            "start_beat": round(float(self.start_beat), 9),
            "end_beat": round(float(self.end_beat), 9),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LaneChangeEvent":
        if not isinstance(data, dict):
            raise ValueError("事件必须是对象")
        try:
            start = float(data.get("start_beat"))
            end = float(data.get("end_beat"))
        except (TypeError, ValueError) as exc:
            raise ValueError("事件缺少有效的拍号") from exc
        if not all(math.isfinite(value) for value in (start, end)):
            raise ValueError("事件的拍号不是有限数值")
        # 旧版逐轨事件带有 lane 字段；校验它仍然合法，但事件统一应用到三条
        # 临时轨道，事件本身不保存 lane。
        legacy_lane = data.get("lane")
        if legacy_lane is not None:
            try:
                lane = float(legacy_lane)
            except (TypeError, ValueError) as exc:
                raise ValueError("旧版事件的临时轨道编号无效") from exc
            if not math.isfinite(lane) or not is_temporary_lane(lane):
                raise ValueError(
                    f"临时轨道编号必须是 1.5 / 2.5 / 3.5：{legacy_lane}"
                )
        kind = str(data.get("type", "")).strip()
        if kind not in config.LANE_CHANGE_TYPES:
            raise ValueError(f"未识别的事件类型：{kind!r}")
        if end < start:
            start, end = end, start
        return cls(kind, float(start), float(end))


def validate_lane_change_events(events: Iterable[LaneChangeEvent]) -> Optional[str]:
    """检查事件顺序；通过时返回 None，否则返回错误说明。

    三条临时轨道共用同一份事件表，因此这里只校验一条全局时间线：
    第一个事件必须是淡入，之后淡入 / 淡出交替，且事件时间不能重叠。
    """
    ordered = sorted(events, key=lambda event: (event.start_beat, event.end_beat))
    for event in ordered:
        if event.kind not in config.LANE_CHANGE_TYPES:
            return f"未识别的事件类型：{event.kind!r}。"
        if event.end_beat <= event.start_beat + 1e-9:
            return (
                f"第 {event.start_beat:g} 拍的事件结束拍必须大于开始拍。"
            )
    if not ordered:
        return None
    first = ordered[0]
    if first.kind != config.LANE_CHANGE_IN:
        return (
            f"第一个事件必须是淡入（当前第 {first.start_beat:g} 拍为淡出）。"
        )
    for previous, current in zip(ordered, ordered[1:]):
        if current.kind == previous.kind:
            return (
                f"淡入与淡出必须交替（第 {current.start_beat:g} 拍连续出现了两次"
                f"{config.LANE_CHANGE_TYPE_LABELS.get(current.kind, current.kind)}）。"
            )
        if current.start_beat < previous.end_beat - 1e-9:
            return (
                f"事件时间不能重叠（第 {previous.start_beat:g} 拍的事件尚未结束）。"
            )
    return None


class LaneChangeMap:
    """一张谱面的轨道数改变事件表（三条临时轨道共用，拍升序）。"""

    def __init__(self, events: Sequence[LaneChangeEvent] | None = None) -> None:
        self.events: List[LaneChangeEvent] = []
        self.revision = 0
        self.set_events(events or [])

    # ------------------------------------------------------------------ 维护
    def set_events(self, events: Iterable[LaneChangeEvent]) -> None:
        ordered = sorted(
            (event for event in events if isinstance(event, LaneChangeEvent)),
            key=lambda event: (event.start_beat, event.end_beat),
        )
        # 旧版文件可能为三条临时轨道各保存一份相同事件：读入时去重。
        unique: List[LaneChangeEvent] = []
        seen = set()
        for event in ordered:
            key = (
                event.kind,
                round(float(event.start_beat), 9),
                round(float(event.end_beat), 9),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(event)
        self.events = unique
        self.revision += 1

    def copy(self) -> "LaneChangeMap":
        return LaneChangeMap(list(self.events))

    def __len__(self) -> int:
        return len(self.events)

    @property
    def is_empty(self) -> bool:
        return not self.events

    def validation_error(self) -> Optional[str]:
        return validate_lane_change_events(self.events)

    # ------------------------------------------------------------------ 查询
    def event_beats(self) -> List[float]:
        beats: List[float] = []
        for event in self.events:
            beats.append(float(event.start_beat))
            beats.append(float(event.end_beat))
        return beats

    def state_at(self, beat: float) -> str:
        """三条临时轨道在 beat 处的共同状态：不存在 / 正在淡入淡出 / 完全出现。"""
        state = config.LANE_CHANGE_STATE_ABSENT
        for event in self.events:
            if beat < event.start_beat - 1e-9:
                break
            if beat < event.end_beat - 1e-9:
                return config.LANE_CHANGE_STATE_FADING
            state = (
                config.LANE_CHANGE_STATE_PRESENT
                if event.kind == config.LANE_CHANGE_IN
                else config.LANE_CHANGE_STATE_ABSENT
            )
        return state

    def segments(
        self, beat_low: float, beat_high: float
    ) -> List[Tuple[float, float, str]]:
        """可见拍区间内三条临时轨道共同的状态段 [(起拍, 止拍, 状态)]。"""
        low = float(min(beat_low, beat_high))
        high = float(max(beat_low, beat_high))
        if high - low <= 1e-9:
            return []
        cuts = [low, high]
        for event in self.events:
            for beat in (event.start_beat, event.end_beat):
                if low + 1e-9 < beat < high - 1e-9:
                    cuts.append(float(beat))
        cuts = sorted(set(round(value, 9) for value in cuts))
        segments: List[Tuple[float, float, str]] = []
        for start, end in zip(cuts, cuts[1:]):
            if end - start <= 1e-9:
                continue
            state = self.state_at((start + end) / 2.0)
            if segments and segments[-1][2] == state:
                segments[-1] = (segments[-1][0], end, state)
            else:
                segments.append((start, end, state))
        return segments

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        return {"events": [event.to_dict() for event in self.events]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "LaneChangeMap":
        """容错读取；无法识别的事件直接跳过（调用方负责记录警告 / 保留数据）。"""
        if isinstance(data, list):
            raw_events: Any = data
        elif isinstance(data, dict):
            raw_events = data.get("events", [])
        else:
            raw_events = []
        if not isinstance(raw_events, list):
            raw_events = []
        events: List[LaneChangeEvent] = []
        for item in raw_events:
            try:
                events.append(LaneChangeEvent.from_dict(item))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return cls(events)

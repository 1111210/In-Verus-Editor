"""谱面：元数据 + BPM/偏移 + 编辑器设置 + 速度组 + 物件集合。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .. import config
from .notes import (
    LaneNote,
    MouseNote,
    MouseRegion,
    MouseSlide,
    NOTE_LANE,
    NOTE_REGION,
    NOTE_SLIDE,
    Note,
    note_from_dict,
    sort_key,
)
from .speed import SpeedGroup, SpeedSegment
from .timing import BpmSegment, TimingMap


@dataclass
class Metadata:
    title: str = "未命名曲目"
    artist: str = "未知歌手"
    difficulty_name: str = "普通"
    difficulty_level: str = "1"
    author: str = "作者"
    audio_file: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "difficulty_name": self.difficulty_name,
            "difficulty_level": self.difficulty_level,
            "author": self.author,
            "audio_file": self.audio_file,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "Metadata":
        data = data or {}
        return cls(
            title=str(data.get("title", "未命名曲目")),
            artist=str(data.get("artist", "未知歌手")),
            difficulty_name=str(data.get("difficulty_name", "普通")),
            difficulty_level=str(data.get("difficulty_level", "1")),
            author=str(data.get("author", "作者")),
            audio_file=str(data.get("audio_file", "")),
        )


@dataclass
class EditorSettings:
    """全局编辑器设置。

    * ``zoom_seconds``         —— 屏幕上同时显示多长时间内出现的物件（缩放）。
    * ``grid_division``        —— 时间网格：一拍细分为多少个音（正整数）。
    * ``horizontal_divisions`` —— 鼠标平面横向辅助网格：0..1 合法区域几等分，0 表示关闭。
    * ``audio_latency_ms``     —— 系统音频延迟：从执行播放到硬件出声的毫秒数。
    """

    zoom_seconds: float = config.DEFAULT_ZOOM_SECONDS
    grid_division: int = config.DEFAULT_GRID_DIVISION
    horizontal_divisions: int = config.DEFAULT_HORIZONTAL_DIVISIONS
    audio_latency_ms: float = config.DEFAULT_AUDIO_LATENCY_MS

    def normalize(self) -> None:
        self.zoom_seconds = min(
            config.ZOOM_MAX_SECONDS, max(config.ZOOM_MIN_SECONDS, float(self.zoom_seconds))
        )
        self.grid_division = max(
            config.GRID_DIVISION_MIN,
            min(config.GRID_DIVISION_MAX, int(self.grid_division)),
        )
        self.horizontal_divisions = max(
            0, min(config.HORIZONTAL_DIVISION_MAX, int(self.horizontal_divisions))
        )
        # 允许负数：负延迟表示声音比软件位置更早，需要反向补偿
        self.audio_latency_ms = min(
            config.AUDIO_LATENCY_MAX_MS,
            max(config.AUDIO_LATENCY_MIN_MS, float(self.audio_latency_ms)),
        )

    @property
    def horizontal_grid_enabled(self) -> bool:
        return self.horizontal_divisions > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zoom_seconds": self.zoom_seconds,
            "grid_division": self.grid_division,
            "horizontal_divisions": self.horizontal_divisions,
            "audio_latency_ms": self.audio_latency_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "EditorSettings":
        data = data or {}
        obj = cls(
            zoom_seconds=float(data.get("zoom_seconds", config.DEFAULT_ZOOM_SECONDS)),
            grid_division=int(data.get("grid_division", config.DEFAULT_GRID_DIVISION)),
            horizontal_divisions=int(
                data.get("horizontal_divisions", config.DEFAULT_HORIZONTAL_DIVISIONS)
            ),
            audio_latency_ms=float(data.get("audio_latency_ms", config.DEFAULT_AUDIO_LATENCY_MS)),
        )
        obj.normalize()
        return obj


class Chart:
    """一张谱面。"""

    def __init__(
        self,
        metadata: Optional[Metadata] = None,
        timing: Optional[TimingMap] = None,
        settings: Optional[EditorSettings] = None,
    ) -> None:
        self.metadata = metadata or Metadata()
        self.timing = timing or TimingMap([BpmSegment(0.0, config.DEFAULT_BPM, config.DEFAULT_BEATS_PER_BAR)])
        self.settings = settings or EditorSettings()
        self.lane_notes: List[LaneNote] = []
        self.mouse_notes: List[MouseNote] = []
        self.speed_groups: List[SpeedGroup] = [SpeedGroup.default()]
        self._id_counters: Dict[str, int] = {"L": 0, "M": 0}

    # ------------------------------------------------------------------ 物件
    def all_notes(self) -> List[Note]:
        return [*self.lane_notes, *self.mouse_notes]

    def lane_notes_sorted(self) -> List[LaneNote]:
        return sorted(self.lane_notes, key=sort_key)

    def mouse_notes_sorted(self) -> List[MouseNote]:
        return sorted(self.mouse_notes, key=sort_key)

    def sorted_notes(self) -> List[Note]:
        return sorted(self.all_notes(), key=sort_key)

    def notes_by_id(self) -> Dict[str, Note]:
        return {n.id: n for n in self.all_notes()}

    def find(self, note_id: str) -> Optional[Note]:
        for note in self.all_notes():
            if note.id == note_id:
                return note
        return None

    def find_many(self, note_ids: Iterable[str]) -> List[Note]:
        wanted = set(note_ids)
        return [n for n in self.sorted_notes() if n.id in wanted]

    def new_note_id(self, prefix: str) -> str:
        used = {n.id for n in self.all_notes()}
        index = self._id_counters.get(prefix, 0)
        while True:
            index += 1
            candidate = f"{prefix}{index}"
            if candidate not in used:
                self._id_counters[prefix] = index
                return candidate

    def register_note_id(self, note_id: str) -> None:
        """读档后同步 id 计数器，避免新建物件与旧 id 冲突。"""
        prefix = note_id[:1]
        if not prefix:
            return
        digits = note_id[1:]
        if not digits.isdigit():
            return
        self._id_counters[prefix] = max(self._id_counters.get(prefix, 0), int(digits))

    def add(  # noqa: A003 - 简单直观的命名
        self, note: Note, group: int | None = None
    ) -> Note:
        if group is not None:
            note.group = int(group)
        if isinstance(note, LaneNote):
            note.normalize()
            self.lane_notes.append(note)
        elif isinstance(note, MouseNote):
            self.mouse_notes.append(note)
        else:  # pragma: no cover - 防御性
            raise TypeError(f"unsupported note: {note!r}")
        self.register_note_id(note.id)
        group_obj = self.group_by_id(note.group)
        group_obj.add_notes([note.id])
        return note

    def add_many(self, notes: Iterable[Note], group: int | None = None) -> List[Note]:
        return [self.add(n, group) for n in notes]

    def remove(self, note_ids: Iterable[str]) -> List[Note]:
        drop = {str(i) for i in note_ids}
        removed: List[Note] = []
        keep_lane: List[LaneNote] = []
        for note in self.lane_notes:
            if note.id in drop:
                removed.append(note)
            else:
                keep_lane.append(note)
        keep_mouse: List[MouseNote] = []
        for note in self.mouse_notes:
            if note.id in drop:
                removed.append(note)
            else:
                keep_mouse.append(note)
        self.lane_notes = keep_lane
        self.mouse_notes = keep_mouse
        for group in self.speed_groups:
            group.remove_notes(drop)
        return removed

    def group_ids_of(self, note_ids: Iterable[str]) -> Set[int]:
        wanted = {str(i) for i in note_ids}
        result: Set[int] = set()
        for group in self.speed_groups:
            if wanted & set(group.note_ids):
                result.add(group.id)
        return result

    # ------------------------------------------------------------------ 速度组
    def group_by_id(self, gid: int) -> SpeedGroup:
        for group in self.speed_groups:
            if group.id == int(gid):
                return group
        return self.speed_groups[0]

    def next_group_id(self) -> int:
        return max((g.id for g in self.speed_groups), default=config.DEFAULT_GROUP_ID) + 1

    def group_of_note(self, note: Note) -> SpeedGroup:
        return self.group_by_id(note.group)

    def assign_group(self, note_ids: Iterable[str], gid: int) -> None:
        ids = [str(i) for i in note_ids]
        idset = set(ids)
        target = self.group_by_id(gid)
        for note in self.all_notes():
            if note.id in idset:
                note.group = target.id
        for group in self.speed_groups:
            if group.id == target.id:
                group.add_notes(ids)
            else:
                group.remove_notes(ids)

    def add_group(self, name: str, segments: Sequence[SpeedSegment] | None = None) -> SpeedGroup:
        group = SpeedGroup(self.next_group_id(), name, segments or [SpeedSegment(0.0, 1.0)], [])
        self.speed_groups.append(group)
        return group

    def delete_group(self, gid: int) -> bool:
        """删除速度组并把组内物件放回默认组（默认组不可删除）。"""
        if int(gid) == config.DEFAULT_GROUP_ID:
            return False
        group = self.group_by_id(int(gid))
        if group.id == config.DEFAULT_GROUP_ID:
            return False
        self.assign_group(list(group.note_ids), config.DEFAULT_GROUP_ID)
        self.speed_groups = [g for g in self.speed_groups if g.id != group.id]
        return True

    # ------------------------------------------------------------------ 统计
    def beat_bounds(self) -> tuple[float, float]:
        beats = [0.0, 1.0]
        for note in self.all_notes():
            beats.append(note.head_beat)
            beats.append(note.tail_beat)
        for seg in self.timing.segments:
            beats.append(seg.start_beat)
        return min(beats), max(beats)

    def note_counts(self) -> Dict[str, int]:
        return {
            NOTE_LANE: len(self.lane_notes),
            NOTE_REGION: sum(1 for n in self.mouse_notes if isinstance(n, MouseRegion)),
            NOTE_SLIDE: sum(1 for n in self.mouse_notes if isinstance(n, MouseSlide)),
        }

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        return {
            "format": config.CHART_FORMAT,
            "version": config.CHART_FORMAT_VERSION,
            "metadata": self.metadata.to_dict(),
            "timing": self.timing.to_dict(),
            "editor": self.settings.to_dict(),
            "speed_groups": [g.to_dict() for g in self.speed_groups],
            "lanes": [n.to_dict() for n in self.sorted_notes() if isinstance(n, LaneNote)],
            "mouse": [n.to_dict() for n in self.sorted_notes() if isinstance(n, MouseNote)],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "Chart":
        data = data or {}
        chart = cls(
            metadata=Metadata.from_dict(data.get("metadata")),  # type: ignore[arg-type]
            timing=TimingMap.from_dict(data.get("timing")),  # type: ignore[arg-type]
            settings=EditorSettings.from_dict(data.get("editor")),  # type: ignore[arg-type]
        )
        groups_data = data.get("speed_groups") or [SpeedGroup.default().to_dict()]
        chart.speed_groups = [SpeedGroup.from_dict(g) for g in groups_data]  # type: ignore[arg-type]
        if not any(g.id == config.DEFAULT_GROUP_ID for g in chart.speed_groups):
            chart.speed_groups.insert(0, SpeedGroup.default())
        chart.speed_groups.sort(key=lambda g: g.id)

        for raw in list(data.get("lanes", [])) + list(data.get("mouse", [])):  # type: ignore[arg-type]
            note = note_from_dict(raw)
            note = chart._attach(note)  # type: ignore[assignment]
        chart._repair_groups()
        return chart

    def _attach(self, note: Note) -> Note:
        if isinstance(note, LaneNote):
            self.lane_notes.append(note)
        else:
            self.mouse_notes.append(note)
        self.register_note_id(note.id)
        return note

    def _repair_groups(self) -> None:
        """保证物件与速度组的从属关系双向一致。"""
        known = self.notes_by_id()
        assigned: Set[str] = set()
        for group in self.speed_groups:
            group.note_ids = [nid for nid in group.note_ids if nid in known and nid not in assigned]
            assigned.update(group.note_ids)
        default = self.group_by_id(config.DEFAULT_GROUP_ID)
        for note in self.all_notes():
            if note.group not in {g.id for g in self.speed_groups}:
                note.group = config.DEFAULT_GROUP_ID
            if note.id not in assigned:
                default.add_notes([note.id])
                note.group = config.DEFAULT_GROUP_ID
                assigned.add(note.id)
            else:
                for group in self.speed_groups:
                    if note.id in group.note_ids:
                        note.group = group.id
                        break

"""谱面：元数据 + BPM/偏移 + 速度组 + 物件集合。"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import config
from editor_settings import EditorSettings
from .notes import (
    ForbiddenRegion,
    LaneNote,
    MouseNote,
    MouseRegion,
    MouseSlide,
    NOTE_FORBIDDEN,
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
        data = data if isinstance(data, dict) else {}
        return cls(
            title=str(data.get("title", "未命名曲目")),
            artist=str(data.get("artist", "未知歌手")),
            difficulty_name=str(data.get("difficulty_name", "普通")),
            difficulty_level=str(data.get("difficulty_level", "1")),
            author=str(data.get("author", "作者")),
            audio_file=str(data.get("audio_file", "")),
        )


def _list_item_key(item: Any) -> Optional[tuple]:
    if not isinstance(item, dict):
        return None
    if "id" in item:
        return ("id", str(item.get("id")))
    if "start_beat" in item:
        try:
            return ("start_beat", round(float(item.get("start_beat")), 9))
        except (TypeError, ValueError):
            return None
    return None


def _merge_preserved(generated: Any, raw: Any) -> Any:
    """把原始 JSON 中未识别的字段合回规范输出，不复活已删除的已知对象。"""
    if isinstance(generated, dict) and isinstance(raw, dict):
        merged = copy.deepcopy(generated)
        for key, value in raw.items():
            if key in merged:
                merged[key] = _merge_preserved(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    if isinstance(generated, list) and isinstance(raw, list):
        raw_by_key = {}
        for item in raw:
            key = _list_item_key(item)
            if key is not None:
                raw_by_key[key] = item
        merged = []
        for index, item in enumerate(generated):
            key = _list_item_key(item)
            source = raw_by_key.get(key) if key is not None else None
            if source is None and len(generated) == len(raw) and index < len(raw):
                # 标识字段被编辑（例如 BPM 起始拍变化）时，结构数量不变仍按位置保留附加值。
                source = raw[index]
            merged.append(_merge_preserved(item, source))
        return merged
    return copy.deepcopy(generated)


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
        # Editor state is owned by EditorApp.editor_settings; this attribute
        # can reference the same object for callers that use Chart directly.
        self.settings = settings or EditorSettings()
        self.lane_notes: List[LaneNote] = []
        self.mouse_notes: List[MouseNote] = []
        self.speed_groups: List[SpeedGroup] = [SpeedGroup.default()]
        # 读取时保留未识别 / 类型异常的字段，保存时合回到规范结构。
        self._source_data: Optional[Dict[str, Any]] = None
        self._unparsed_entries: Dict[str, List[Any]] = {}
        self.load_warnings: List[str] = []
        self._id_counters: Dict[str, int] = {"L": 0, "M": 0}
        # 按判定时刻排好序的物件列表缓存：只在增删 / 编辑后重建，避免每帧排序
        self._sorted_cache: Optional[List[Note]] = None
        self._sorted_heads: Optional[List[float]] = None
        self._max_note_span: Optional[float] = None
        self._note_counts: Optional[Dict[str, int]] = None
        self._beat_bounds: Optional[tuple[float, float]] = None
        self._beat_bounds_for: Optional[TimingMap] = None
        self._beat_bounds_key: Optional[tuple] = None
        # 谱面内容版本号（物件增删 / 编辑 / 时间变动都会 +1；渲染与命中缓存用）
        self.revision = 0
        # 下列都是派生索引（不参与序列化）：按需建立，增删物件时同步维护
        self._id_index: Optional[Dict[str, Note]] = None
        self._used_ids: Optional[set] = None
        self._group_index: Optional[Dict[int, SpeedGroup]] = None

    # ------------------------------------------------------------------ 物件
    def all_notes(self) -> List[Note]:
        return [*self.lane_notes, *self.mouse_notes]

    def lane_notes_sorted(self) -> List[LaneNote]:
        return [n for n in self.sorted_notes() if isinstance(n, LaneNote)]

    def mouse_notes_sorted(self) -> List[MouseNote]:
        return [n for n in self.sorted_notes() if isinstance(n, MouseNote)]

    def sorted_notes(self) -> List[Note]:
        """按 (判定时刻, 结束时刻, id) 排序的物件列表（带缓存）。

        调用方不要修改返回的列表。缓存会在 add / remove /
        invalidate_order() 之后自动重建；Document.apply 每次编辑也会调用它，
        所以就地修改物件（镜像 / 平移 / 改时间）后同样会刷新。
        """
        if self._sorted_cache is None:
            self._sorted_cache = sorted(self.all_notes(), key=sort_key)
            self._sorted_heads = [float(n.head_beat) for n in self._sorted_cache]
        return self._sorted_cache

    def sorted_head_beats(self) -> List[float]:
        """与 sorted_notes 平行的开头拍列表（用于二分可视窗口）。"""
        if self._sorted_heads is None:
            self.sorted_notes()
        assert self._sorted_heads is not None
        return self._sorted_heads

    def max_note_span(self) -> float:
        """全谱最长的 tail_beat − head_beat（可视窗口回扫用，带缓存）。"""
        if self._max_note_span is None:
            self._max_note_span = max(
                (float(n.tail_beat) - float(n.head_beat) for n in self.all_notes()),
                default=0.0,
            )
        return self._max_note_span

    def invalidate_order(self) -> None:
        """标记排序缓存失效（增删物件、修改物件时间后调用）。"""
        self._sorted_cache = None
        self._sorted_heads = None
        self._max_note_span = None
        self._note_counts = None
        self._beat_bounds = None
        self.revision += 1

    def notes_by_id(self) -> Dict[str, Note]:
        if self._id_index is None:
            self._id_index = {n.id: n for n in self.all_notes()}
        return dict(self._id_index)

    def find(self, note_id: str) -> Optional[Note]:
        if self._id_index is None:
            self._id_index = {n.id: n for n in self.all_notes()}
        return self._id_index.get(note_id)

    def find_many(self, note_ids: Iterable[str]) -> List[Note]:
        if self._id_index is None:
            self._id_index = {n.id: n for n in self.all_notes()}
        index = self._id_index
        out: List[Note] = []
        for note_id in note_ids:
            note = index.get(note_id)
            if note is not None:
                out.append(note)
        out.sort(key=sort_key)      # 与 sorted_notes() 的顺序一致
        return out

    def new_note_id(self, prefix: str) -> str:
        if self._used_ids is None:
            self._used_ids = {n.id for n in self.all_notes()}
        used = self._used_ids
        index = self._id_counters.get(prefix, 0)
        while True:
            index += 1
            candidate = f"{prefix}{index}"
            if candidate not in used:
                self._id_counters[prefix] = index
                used.add(candidate)
                return candidate

    def register_note_id(self, note_id: str) -> None:
        """读档后同步 id 计数器，避免新建物件与已有 id 冲突。"""
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
        if self._id_index is not None:
            self._id_index[note.id] = note
        if self._used_ids is not None:
            self._used_ids.add(note.id)
        group_obj = self.group_by_id(note.group)
        group_obj.add_notes([note.id])
        self.invalidate_order()
        return note

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
        if self._id_index is not None:
            for note in removed:
                self._id_index.pop(note.id, None)
        if self._used_ids is not None:
            self._used_ids.difference_update(note.id for note in removed)
        for group in self.speed_groups:
            group.remove_notes(drop)
        if removed:
            self.invalidate_order()
        return removed

    # ------------------------------------------------------------------ 速度组
    def group_by_id(self, gid: int) -> SpeedGroup:
        if self._group_index is None:
            self._group_index = {group.id: group for group in self.speed_groups}
        return self._group_index.get(int(gid), self.speed_groups[0])

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
        if self._group_index is not None:
            self._group_index[group.id] = group
        return group

    def replace_speed_groups(self, groups: Iterable[SpeedGroup]) -> None:
        """整体替换速度组列表（读档 / 撤销恢复用）并重建组索引。"""
        self.speed_groups = sorted(groups, key=lambda g: g.id)
        self._group_index = None

    def delete_group(self, gid: int) -> bool:
        """删除速度组并把组内物件放回默认组（默认组不可删除）。"""
        if int(gid) == config.DEFAULT_GROUP_ID:
            return False
        group = self.group_by_id(int(gid))
        if group.id == config.DEFAULT_GROUP_ID:
            return False
        self.assign_group(list(group.note_ids), config.DEFAULT_GROUP_ID)
        self.speed_groups = [g for g in self.speed_groups if g.id != group.id]
        if self._group_index is not None:
            self._group_index.pop(group.id, None)
        return True

    # ------------------------------------------------------------------ 统计
    def beat_bounds(self) -> tuple[float, float]:
        key = (self.timing.revision, self.revision)
        if (
            self._beat_bounds is None
            or self._beat_bounds_for is not self.timing
            or self._beat_bounds_key != key
        ):
            beats = [0.0, 1.0]
            for note in self.all_notes():
                beats.append(note.head_beat)
                beats.append(note.tail_beat)
            for seg in self.timing.segments:
                beats.append(seg.start_beat)
            self._beat_bounds = (min(beats), max(beats))
            self._beat_bounds_for = self.timing
            self._beat_bounds_key = key
        return self._beat_bounds

    def note_counts(self) -> Dict[str, int]:
        if self._note_counts is None:
            self._note_counts = {
                NOTE_LANE: len(self.lane_notes),
                NOTE_REGION: sum(
                    1 for n in self.mouse_notes
                    if isinstance(n, MouseRegion) and not isinstance(n, ForbiddenRegion)
                ),
                NOTE_FORBIDDEN: sum(
                    1 for n in self.mouse_notes if isinstance(n, ForbiddenRegion)
                ),
                NOTE_SLIDE: sum(1 for n in self.mouse_notes if isinstance(n, MouseSlide)),
            }
        return dict(self._note_counts)

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        data = {
            "format": config.CHART_FORMAT,
            "version": config.CHART_FORMAT_VERSION,
            "metadata": self.metadata.to_dict(),
            "timing": self.timing.to_dict(),
            # 注意：其他设置（视图 / 音效 / 其他设置）不写进谱面，
            # 它们保存在全局配置文件里（见 global_settings.py）。
            "speed_groups": [g.to_dict() for g in self.speed_groups],
            "lanes": [n.to_dict() for n in self.sorted_notes() if isinstance(n, LaneNote)],
            "mouse": [n.to_dict() for n in self.sorted_notes() if isinstance(n, MouseNote)],
        }
        if self._source_data is not None:
            data = _merge_preserved(data, self._source_data)
        for section, entries in self._unparsed_entries.items():
            target = data.get(section)
            if isinstance(target, list):
                target.extend(copy.deepcopy(entries))
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "Chart":
        raw = data if isinstance(data, dict) else {}
        chart = cls()
        chart._source_data = copy.deepcopy(raw)

        metadata = raw.get("metadata")
        if isinstance(metadata, dict):
            chart.metadata = Metadata.from_dict(metadata)
        elif metadata is not None:
            chart.load_warnings.append("metadata 不是对象，已使用默认元数据。")

        timing = raw.get("timing")
        if isinstance(timing, dict):
            try:
                chart.timing = TimingMap.from_dict(timing)
            except Exception as exc:
                chart.load_warnings.append(f"timing 无法解析，已使用默认时间轴：{exc}")
        elif timing is not None:
            chart.load_warnings.append("timing 不是对象，已使用默认时间轴。")

        groups_raw = raw.get("speed_groups")
        if groups_raw is None:
            groups_raw = [SpeedGroup.default().to_dict()]
        if not isinstance(groups_raw, list):
            chart.load_warnings.append("speed_groups 不是数组，已使用默认速度组。")
            groups_raw = [SpeedGroup.default().to_dict()]

        groups = []
        for index, group_data in enumerate(groups_raw):
            if not isinstance(group_data, dict):
                chart._unparsed_entries.setdefault("speed_groups", []).append(group_data)
                chart.load_warnings.append(f"speed_groups[{index}] 不是对象，已跳过。")
                continue
            try:
                groups.append(SpeedGroup.from_dict(group_data))
            except Exception as exc:
                chart._unparsed_entries.setdefault("speed_groups", []).append(group_data)
                chart.load_warnings.append(f"speed_groups[{index}] 无法解析：{exc}")
        chart.replace_speed_groups(groups or [SpeedGroup.default()])
        if not any(g.id == config.DEFAULT_GROUP_ID for g in chart.speed_groups):
            chart.speed_groups.insert(0, SpeedGroup.default())
        chart.speed_groups.sort(key=lambda g: g.id)

        for section in ("lanes", "mouse"):
            section_raw = raw.get(section, [])
            if section_raw is None:
                section_raw = []
            if not isinstance(section_raw, list):
                chart.load_warnings.append(f"{section} 不是数组，已跳过。")
                continue
            for index, note_data in enumerate(section_raw):
                if not isinstance(note_data, dict):
                    chart._unparsed_entries.setdefault(section, []).append(note_data)
                    chart.load_warnings.append(f"{section}[{index}] 不是对象，已跳过。")
                    continue
                try:
                    note = note_from_dict(note_data)
                    chart._attach(note)
                except Exception as exc:
                    chart._unparsed_entries.setdefault(section, []).append(note_data)
                    chart.load_warnings.append(
                        f"{section}[{index}] 无法解析，已跳过：{exc}"
                    )
        chart._repair_groups()
        return chart

    def _attach(self, note: Note) -> Note:
        if isinstance(note, LaneNote):
            self.lane_notes.append(note)
        else:
            self.mouse_notes.append(note)
        self.register_note_id(note.id)
        if self._id_index is not None:
            self._id_index[note.id] = note
        if self._used_ids is not None:
            self._used_ids.add(note.id)
        self.invalidate_order()
        return note

    def _repair_groups(self) -> None:
        """保证物件与速度组的从属关系双向一致。"""
        known = self.notes_by_id()
        assigned: Set[str] = set()
        valid_ids = {g.id for g in self.speed_groups}
        owner: Dict[str, int] = {}
        for group in self.speed_groups:
            group.set_note_ids([nid for nid in group.note_ids if nid in known and nid not in assigned])
            for nid in group.note_ids:
                owner.setdefault(nid, group.id)
            assigned.update(group.note_ids)
        default = self.group_by_id(config.DEFAULT_GROUP_ID)
        for note in self.all_notes():
            if note.group not in valid_ids:
                note.group = config.DEFAULT_GROUP_ID
            if note.id not in assigned:
                default.add_notes([note.id])
                note.group = config.DEFAULT_GROUP_ID
                assigned.add(note.id)
            else:
                note.group = owner.get(note.id, note.group)

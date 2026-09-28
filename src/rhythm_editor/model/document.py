"""文档：谱面 + 选择状态 + 剪贴板 + 撤销/重做。

撤销采用“整谱快照”策略：谱面数据量很小（几千个物件也只有几百 KB），
快照式撤销实现简单且不会遗漏任何字段，适合编辑器场景。
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .. import config
from .chart import Chart
from .notes import LaneNote, MouseNote, MouseRegion, MouseSlide, NOTE_LANE, NOTE_REGION, NOTE_SLIDE, Note, note_from_dict

Snapshot = Tuple[str, Dict[str, Any], Tuple[str, ...]]


class Document:
    def __init__(self, chart: Optional[Chart] = None, path: Optional[str] = None) -> None:
        self.chart = chart or Chart()
        self.path = path
        self.dirty = False
        self.selection: Set[str] = set()
        self.clipboard: List[Dict[str, Any]] = []
        self._undo: List[Snapshot] = []
        self._redo: List[Snapshot] = []
        self.max_undo = 200

    # ------------------------------------------------------------------ 文件
    @property
    def title(self) -> str:
        name = self.chart.metadata.title or "未命名曲目"
        if not self.path:
            return f"{name} *"
        import os

        base = os.path.basename(self.path)
        return f"{name} — {base}{' *' if self.dirty else ''}"

    def set_chart(self, chart: Chart, path: Optional[str] = None) -> None:
        self.chart = chart
        self.path = path
        self.dirty = False
        self.selection.clear()
        self._undo.clear()
        self._redo.clear()

    # ------------------------------------------------------------------ 快照
    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self.chart.to_dict())

    def _restore(self, data: Dict[str, Any], selection: Iterable[str]) -> None:
        self.chart = Chart.from_dict(copy.deepcopy(data))
        valid = self.chart.notes_by_id()
        self.selection = {sid for sid in selection if sid in valid}

    def push_undo(self, label: str) -> None:
        self._undo.append((label, self.snapshot(), tuple(sorted(self.selection))))
        if len(self._undo) > self.max_undo:
            self._undo.pop(0)
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> bool:
        if not self._undo:
            return False
        label, data, selection = self._undo.pop()
        self._redo.append((label, self.snapshot(), tuple(sorted(self.selection))))
        self._restore(data, selection)
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        label, data, selection = self._redo.pop()
        self._undo.append((label, self.snapshot(), tuple(sorted(self.selection))))
        self._restore(data, selection)
        self.dirty = True
        return True

    def apply(self, label: str, func: Callable[[], Any]) -> Any:
        """带撤销记录地修改谱面。"""
        self.push_undo(label)
        result = func()
        # 物件可能被就地修改（镜像 / 平移 / 改时间），统一让排序缓存失效
        self.chart.invalidate_order()
        self.dirty = True
        return result

    # ------------------------------------------------------------------ 选择
    def select_only(self, note_ids: Iterable[str]) -> None:
        self.selection = {str(i) for i in note_ids}

    def select_all(self) -> None:
        self.selection = {n.id for n in self.chart.all_notes()}

    def clear_selection(self) -> None:
        self.selection.clear()

    def add_to_selection(self, note_ids: Iterable[str]) -> None:
        self.selection.update(str(i) for i in note_ids)

    def xor_selection(self, note_ids: Iterable[str]) -> None:
        """Ctrl 选择：把新选择并入，两个集合的交集被移出（对称差）。"""
        self.selection ^= {str(i) for i in note_ids}

    def selected_notes(self) -> List[Note]:
        return self.chart.find_many(self.selection)

    def single_selected(self) -> Optional[Note]:
        if len(self.selection) != 1:
            return None
        return self.chart.find(next(iter(self.selection)))

    # ------------------------------------------------------------------ 编辑
    def add_note(self, note: Note, label: str = "放置物件", select: bool = True) -> Note:
        def _do() -> Note:
            self.chart.add(note)
            return note

        self.apply(label, _do)
        if select:
            self.select_only([note.id])
        return note

    def delete_notes(self, note_ids: Iterable[str], label: str = "删除物件") -> List[Note]:
        ids = [str(i) for i in note_ids]
        if not ids:
            return []

        def _do() -> List[Note]:
            return self.chart.remove(ids)

        removed = self.apply(label, _do)
        self.selection -= set(ids)
        return removed

    def copy_selection(self) -> int:
        notes = self.selected_notes()
        self.clipboard = [n.to_dict() for n in notes]
        return len(self.clipboard)

    def paste_clipboard(self, base_beat: float, label: str = "粘贴物件") -> List[Note]:
        """把剪贴板内容粘贴到 ``base_beat``：轨道/水平位置不动，只平移时间。"""
        if not self.clipboard:
            return []
        heads = [_clip_head(d) for d in self.clipboard]
        offset = float(base_beat) - min(heads)
        created: List[Note] = []

        def _do() -> List[Note]:
            for data in self.clipboard:
                raw = copy.deepcopy(data)
                note = note_from_dict(raw)
                note.id = self.chart.new_note_id("L" if isinstance(note, LaneNote) else "M")
                note.shift(offset)
                if isinstance(note, LaneNote):
                    note.normalize()
                self.chart.add(note)
                created.append(note)
            return created

        self.apply(label, _do)
        self.select_only([n.id for n in created])
        return created

    def mirror_notes(self, note_ids: Iterable[str], label: str = "镜像") -> int:
        ids = [str(i) for i in note_ids]
        notes = self.chart.find_many(ids)
        if not notes:
            return 0

        def _do() -> int:
            for note in notes:
                if isinstance(note, LaneNote):
                    note.mirrored()
                else:
                    note.mirrored()
            return len(notes)

        return self.apply(label, _do)

    def replace_note(self, note_id: str, data: Dict[str, Any], label: str = "编辑物件") -> Optional[Note]:
        """用新的数据替换一个物件（保持 id 与所属速度组）。"""
        old = self.chart.find(note_id)
        if old is None:
            return None
        new_note = note_from_dict(data)
        new_note.id = old.id
        if "group" not in data:
            new_note.group = old.group

        def _do() -> Note:
            self.chart.remove([note_id])
            self.chart.add(new_note, group=new_note.group)
            return new_note

        return self.apply(label, _do)

    def set_selection_group(self, gid: int, label: str = "设置速度组") -> int:
        ids = list(self.selection)
        if not ids:
            return 0
        return self.apply(label, lambda: (self.chart.assign_group(ids, gid), len(ids))[1])


def _clip_head(data: Dict[str, Any]) -> float:
    if data.get("type") == NOTE_LANE:
        return float(data.get("beat_start", 0.0))
    if data.get("type") == NOTE_REGION:
        return float(data.get("beat_start", 0.0))
    return float(data.get("beat", 0.0))

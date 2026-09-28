"""编辑器的各类对话框。

设计：对话框只负责收集数据并返回结果，真正的谱面修改由调用方通过
``Document.apply`` 完成，保证每一步都能撤销。
"""

from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import config
from ..model.chart import Chart, EditorSettings, Metadata
from ..model.document import Document
from ..model.notes import (
    BoundaryCurve,
    LaneNote,
    MouseRegion,
    MouseSlide,
    Note,
)
from ..model.speed import SpeedGroup, SpeedSegment
from ..model.timing import BpmSegment, TimingMap
from .theme import Theme


# ---------------------------------------------------------------- 小工具
def parse_float(text: str, default: float = 0.0) -> float:
    try:
        return float(str(text).strip().replace("，", ""))
    except (TypeError, ValueError):
        return default


def parse_int(text: str, default: int = 0) -> int:
    try:
        return int(float(str(text).strip()))
    except (TypeError, ValueError):
        return default


class BaseDialog(tk.Toplevel):
    """统一风格的模态对话框基类。"""

    title_text = "对话框"
    ok_text = "确定"

    def __init__(self, parent: tk.Misc, theme: Theme) -> None:
        super().__init__(parent)
        self.theme = theme
        self.result: Any = None
        self.title(self.title_text)
        self.configure(background=theme.panel)
        self.transient(parent)
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self._build()
        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(buttons, text=self.ok_text, style="Accent.TButton", command=self._ok).pack(
            side="right", padx=(0, 8)
        )
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.update_idletasks()
        self._center(parent)

    def _center(self, parent: tk.Misc) -> None:
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            w, h = self.winfo_width(), self.winfo_height()
            self.geometry(f"+{px + max(0, (pw - w) // 2)}+{py + max(0, (ph - h) // 3)}")
        except tk.TclError:  # pragma: no cover
            pass

    def _build(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def _ok(self) -> None:
        self.result = True
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def run_dialog(dialog_cls, parent: tk.Misc, theme: Theme, *args, **kwargs):
    dialog = dialog_cls(parent, theme, *args, **kwargs)
    dialog.grab_set()
    parent.wait_window(dialog)
    return dialog.result


def labeled_entry(parent: tk.Misc, row: int, label: str, value: str, width: int = 18,
                  column: int = 0) -> tk.Widget:
    ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=3, padx=(0, 6))
    entry = ttk.Entry(parent, width=width)
    entry.insert(0, value)
    entry.grid(row=row, column=column + 1, sticky="we", pady=3)
    return entry


def labeled_spin(parent: tk.Misc, row: int, label: str, value: int, frm: int, to: int,
                 column: int = 0, width: int = 8) -> tk.Widget:
    ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=3, padx=(0, 6))
    spin = ttk.Spinbox(parent, from_=frm, to=to, width=width)
    spin.set(str(value))
    spin.grid(row=row, column=column + 1, sticky="we", pady=3)
    return spin


def labeled_combo(parent: tk.Misc, row: int, label: str, values: Sequence[str], value: str,
                  column: int = 0, width: int = 14) -> tk.Widget:
    ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", pady=3, padx=(0, 6))
    combo = ttk.Combobox(parent, values=list(values), state="readonly", width=width)
    combo.set(value)
    combo.grid(row=row, column=column + 1, sticky="we", pady=3)
    return combo


# ---------------------------------------------------------------- 元数据
class MetadataDialog(BaseDialog):
    title_text = "元数据设置"

    def __init__(self, parent: tk.Misc, theme: Theme, metadata: Metadata, chart_path: Optional[str]) -> None:
        self.metadata = metadata
        self.chart_path = chart_path
        self.audio_source: Optional[str] = None
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        body.columnconfigure(1, weight=1)
        self.e_title = labeled_entry(body, 0, "歌曲名称", self.metadata.title, width=30)
        self.e_artist = labeled_entry(body, 1, "歌手", self.metadata.artist, width=30)
        self.e_diff_name = labeled_entry(body, 2, "关卡难度名", self.metadata.difficulty_name, width=30)
        self.e_diff_level = labeled_entry(body, 3, "难度等级（文本）", self.metadata.difficulty_level, width=30)
        self.e_author = labeled_entry(body, 4, "作者", self.metadata.author, width=30)

        ttk.Label(body, text="音频文件名").grid(row=5, column=0, sticky="w", pady=3, padx=(0, 6))
        audio_row = ttk.Frame(body)
        audio_row.grid(row=5, column=1, sticky="we", pady=3)
        audio_row.columnconfigure(0, weight=1)
        self.e_audio = ttk.Entry(audio_row, width=30)
        self.e_audio.insert(0, self.metadata.audio_file)
        self.e_audio.grid(row=0, column=0, sticky="we")
        ttk.Button(audio_row, text="选择文件…", command=self._pick_audio).grid(row=0, column=1, padx=(6, 0))
        ttk.Label(
            body,
            text="选择后会复制到谱面文件所在文件夹，谱面内只记录文件名。",
            style="Dim.TLabel",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Label(
            body,
            text="提示：先保存谱面（Ctrl+S）再选择音频，复制位置会更明确；未保存时会先复制到 charts/unsaved/。",
            style="Dim.TLabel",
            wraplength=420,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))

    def _pick_audio(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择歌曲音频",
            filetypes=[
                ("音频文件", "*.mp3 *.ogg *.wav *.flac *.m4a *.aac *.opus"),
                ("全部文件", "*.*"),
            ],
        )
        if not path:
            return
        self.audio_source = path
        relative = os.path.basename(path)
        self.e_audio.delete(0, "end")
        self.e_audio.insert(0, relative)

    def _ok(self) -> None:
        self.result = {
            "title": self.e_title.get().strip() or "未命名曲目",
            "artist": self.e_artist.get().strip(),
            "difficulty_name": self.e_diff_name.get().strip(),
            "difficulty_level": self.e_diff_level.get().strip(),
            "author": self.e_author.get().strip(),
            "audio_file": self.e_audio.get().strip(),
            "audio_source": self.audio_source,
        }
        self.destroy()


# ---------------------------------------------------------------- BPM / 偏移
class BpmDialog(BaseDialog):
    title_text = "BPM 与偏移设定"

    def __init__(self, parent: tk.Misc, theme: Theme, timing: TimingMap,
                 beat_hint: str = "") -> None:
        self.timing = timing
        self.beat_hint = beat_hint
        self.segments: List[BpmSegment] = [
            BpmSegment(s.start_beat, s.bpm, s.beats_per_bar) for s in timing.segments
        ]
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        head = ttk.Frame(body)
        head.pack(fill="x")
        head.columnconfigure(1, weight=1)
        self.e_offset = labeled_entry(head, 0, "偏移（毫秒）", f"{self.timing.offset_ms:.3f}", width=16)
        ttk.Label(
            body,
            text="偏移 = 歌曲需要提前播放以对应节拍的毫秒数；第 0 拍对应歌曲位置 offset 毫秒。",
            style="Dim.TLabel",
            wraplength=520,
        ).pack(anchor="w", pady=(0, 6))

        table = ttk.Frame(body)
        table.pack(fill="both", expand=True)
        columns = ("start", "bpm", "bar")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=9, selectmode="browse")
        for key, text, width in (
            ("start", "起始拍", 90),
            ("bpm", "BPM（正数）", 110),
            ("bar", "每小节拍数", 110),
        ):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        form = ttk.LabelFrame(body, text="编辑选中项", padding=8)
        form.pack(fill="x", pady=(8, 0))
        form.columnconfigure(5, weight=1)
        self.e_start = labeled_entry(form, 0, "起始拍", "0", width=10)
        self.e_bpm = labeled_entry(form, 0, "BPM", "120", width=10, column=2)
        self.e_bar = labeled_entry(form, 0, "每小节拍数", "4", width=10, column=4)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="新增", command=self._add).pack(side="left")
        ttk.Button(buttons, text="更新选中", command=self._update).pack(side="left", padx=6)
        ttk.Button(buttons, text="删除选中", command=self._delete).pack(side="left")
        ttk.Label(
            body,
            text="从第 0 拍开始的一项不允许删除，只能修改；BPM 列表不影响物件运动速度。",
            style="Warn.TLabel",
        ).pack(anchor="w", pady=(6, 0))
        if self.beat_hint:
            ttk.Label(body, text=self.beat_hint, style="Dim.TLabel").pack(anchor="w", pady=(2, 0))
        self._refresh()

    # ---------------------------------------------------------- 表格逻辑
    def _refresh(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, seg in enumerate(self.segments):
            tag = "first" if index == 0 else ""
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(f"{seg.start_beat:g}", f"{seg.bpm:g}", seg.beats_per_bar),
                tags=(tag,),
            )
        if self.segments:
            self.tree.selection_set("0")
            self._load(0)

    def _load(self, index: int) -> None:
        seg = self.segments[index]
        for entry, value in (
            (self.e_start, f"{seg.start_beat:g}"),
            (self.e_bpm, f"{seg.bpm:g}"),
            (self.e_bar, str(seg.beats_per_bar)),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)

    def _selected_index(self) -> Optional[int]:
        selection = self.tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def _on_select(self, _event=None) -> None:
        index = self._selected_index()
        if index is not None and 0 <= index < len(self.segments):
            self._load(index)

    def _read_form(self, index: int) -> Optional[BpmSegment]:
        start = parse_float(self.e_start.get(), -1.0)
        bpm = parse_float(self.e_bpm.get(), -1.0)
        bar = parse_int(self.e_bar.get(), 0)
        if index == 0:
            start = 0.0
        if start < 0 or bpm <= 0 or bar < 1:
            messagebox.showerror("数值错误", "起始拍需 ≥ 0，BPM 需 > 0，每小节拍数需 ≥ 1。", parent=self)
            return None
        return BpmSegment(start, bpm, bar)

    def _add(self) -> None:
        index = len(self.segments)
        seg = self._read_form(index)
        if seg is None:
            return
        self.segments.append(seg)
        self._sort_and_refresh(seg.start_beat)

    def _update(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        seg = self._read_form(index)
        if seg is None:
            return
        self.segments[index] = seg
        self._sort_and_refresh(seg.start_beat)

    def _delete(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        if index == 0 or abs(self.segments[index].start_beat) < 1e-9:
            messagebox.showwarning("不可删除", "从第 0 拍开始的一项不允许删除，只能修改。", parent=self)
            return
        del self.segments[index]
        self._sort_and_refresh()

    def _sort_and_refresh(self, focus_beat: Optional[float] = None) -> None:
        self.segments.sort(key=lambda s: s.start_beat)
        # 去重：同一拍只保留一条
        unique: List[BpmSegment] = []
        for seg in self.segments:
            if unique and abs(unique[-1].start_beat - seg.start_beat) < 1e-9:
                unique[-1] = seg
            else:
                unique.append(seg)
        self.segments = unique or [BpmSegment(0.0, config.DEFAULT_BPM, config.DEFAULT_BEATS_PER_BAR)]
        self.segments[0].start_beat = 0.0
        self._refresh()
        if focus_beat is not None:
            for index, seg in enumerate(self.segments):
                if abs(seg.start_beat - focus_beat) < 1e-9:
                    self.tree.selection_set(str(index))
                    self._load(index)
                    break

    def _ok(self) -> None:
        offset = parse_float(self.e_offset.get(), 0.0)
        timing = TimingMap([BpmSegment(s.start_beat, s.bpm, s.beats_per_bar) for s in self.segments], offset)
        self.result = timing
        self.destroy()


# ---------------------------------------------------------------- 编辑器设置
class SettingsDialog(BaseDialog):
    title_text = "编辑器设置"

    def __init__(self, parent: tk.Misc, theme: Theme, chart: Chart) -> None:
        self.chart = chart
        self.settings = EditorSettings(
            zoom_seconds=chart.settings.zoom_seconds,
            grid_division=chart.settings.grid_division,
            horizontal_divisions=chart.settings.horizontal_divisions,
            audio_latency_ms=chart.settings.audio_latency_ms,
        )
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        group = ttk.LabelFrame(body, text="缩放（屏幕上同时显示的时间长度）", padding=8)
        group.pack(fill="x")
        group.columnconfigure(1, weight=1)
        self.scale = ttk.Scale(group, from_=0.0, to=1.0, orient="horizontal", command=self._on_scale)
        self.scale.grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(group, text="时间短").grid(row=0, column=0)
        ttk.Label(group, text="时间长").grid(row=0, column=2)
        self.zoom_value = ttk.Label(group, text="")
        self.zoom_value.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.e_zoom = labeled_entry(group, 2, "可视秒数", f"{self.settings.zoom_seconds:g}", width=12)
        self.e_zoom.bind("<Return>", lambda _e: self._apply_zoom_entry())
        self.e_zoom.bind("<FocusOut>", lambda _e: self._apply_zoom_entry())

        ttk.Label(
            body,
            text="网格设置已移到「视图」菜单：时间网格细分 / 横向辅助网格 → 自定义…",
            style="Dim.TLabel",
            wraplength=420,
        ).pack(anchor="w", pady=(8, 0))

        latency = ttk.LabelFrame(body, text="系统音频延迟", padding=8)
        latency.pack(fill="x", pady=(8, 0))
        self.e_latency = labeled_entry(latency, 0, "延迟（毫秒）", f"{self.settings.audio_latency_ms:g}", width=12)
        ttk.Label(
            latency,
            text="从软件执行音频播放操作到电脑硬件放出声音的间隔；播放时用“后端位置 - 延迟”对齐画面与声音。",
            style="Dim.TLabel",
            wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._sync_scale_from_value()

    # -------------------------------------------------------------- 缩放
    @staticmethod
    def _slider_to_value(t: float) -> float:
        lo, hi = config.ZOOM_MIN_SECONDS, config.ZOOM_MAX_SECONDS
        return lo * (hi / lo) ** float(t)

    @staticmethod
    def _value_to_slider(value: float) -> float:
        lo, hi = config.ZOOM_MIN_SECONDS, config.ZOOM_MAX_SECONDS
        value = min(hi, max(lo, value))
        return math.log(value / lo) / math.log(hi / lo)

    def _sync_scale_from_value(self) -> None:
        self.scale.set(self._value_to_slider(self.settings.zoom_seconds))
        self._update_zoom_label()

    def _on_scale(self, _value: str) -> None:
        self.settings.zoom_seconds = self._slider_to_value(self.scale.get())
        self.e_zoom.delete(0, "end")
        self.e_zoom.insert(0, f"{self.settings.zoom_seconds:.3g}")
        self._update_zoom_label()

    def _apply_zoom_entry(self) -> None:
        self.settings.zoom_seconds = min(
            config.ZOOM_MAX_SECONDS,
            max(config.ZOOM_MIN_SECONDS, parse_float(self.e_zoom.get(), self.settings.zoom_seconds)),
        )
        self._sync_scale_from_value()

    def _update_zoom_label(self) -> None:
        timing = self.chart.timing
        seconds_per_beat = timing.seconds_per_beat(0.0)
        beats = self.settings.zoom_seconds / max(1e-6, seconds_per_beat)
        self.zoom_value.configure(
            text=f"当前约 {self.settings.zoom_seconds:.3g} 秒 ≈ {beats:.2f} 拍（按第 0 拍 BPM 估算）"
        )

    def _ok(self) -> None:
        self._apply_zoom_entry()
        self.result = {
            "zoom_seconds": self.settings.zoom_seconds,
            "audio_latency_ms": parse_float(self.e_latency.get(), 0.0),
        }
        self.destroy()


# ---------------------------------------------------------------- 速度组
class SpeedGroupDialog(BaseDialog):
    title_text = "速度组"
    ok_text = "关闭"

    def __init__(self, parent: tk.Misc, theme: Theme, document: Document,
                 beat_hint: str = "") -> None:
        self.document = document
        self.beat_hint = beat_hint
        self.changed = False
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        ttk.Label(left, text="速度组列表", style="Head.TLabel").pack(anchor="w")
        self.tree = ttk.Treeview(left, columns=("id", "name", "count"), show="headings", height=10,
                                 selectmode="browse")
        for key, text, width in (("id", "编号", 50), ("name", "名称", 130), ("count", "物件数", 60)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_segments())

        row = ttk.Frame(left)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="新建组", command=self._new_group).pack(side="left")
        ttk.Button(row, text="复制组", command=self._clone_group).pack(side="left", padx=4)
        ttk.Button(row, text="重命名", command=self._rename_group).pack(side="left", padx=4)
        ttk.Button(row, text="删除组", command=self._delete_group).pack(side="left", padx=4)
        row2 = ttk.Frame(left)
        row2.pack(fill="x", pady=(4, 0))
        ttk.Button(row2, text="把当前选择加入该组", command=self._assign_selection).pack(side="left")
        self.lbl_group_hint = ttk.Label(left, text="默认组不可删除；删除其它组时物件会回到默认组。",
                                        style="Dim.TLabel", wraplength=320)
        self.lbl_group_hint.pack(anchor="w", pady=(4, 0))

        ttk.Label(right, text="流速列表（从某拍开始，流速 = 默认速度 × 倍率）", style="Head.TLabel").pack(anchor="w")
        self.seg_tree = ttk.Treeview(right, columns=("start", "mult"), show="headings", height=8,
                                     selectmode="browse")
        self.seg_tree.heading("start", text="起始拍")
        self.seg_tree.heading("mult", text="流速倍率")
        self.seg_tree.column("start", width=90, anchor="center")
        self.seg_tree.column("mult", width=90, anchor="center")
        self.seg_tree.pack(fill="both", expand=True)
        self.seg_tree.bind("<<TreeviewSelect>>", self._on_seg_select)

        form = ttk.Frame(right)
        form.pack(fill="x", pady=(6, 0))
        self.e_start = labeled_entry(form, 0, "起始拍", "0", width=10)
        self.e_mult = labeled_entry(form, 0, "倍率", "1", width=10, column=2)
        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(4, 0))
        ttk.Button(btns, text="新增/更新", command=self._upsert_segment).pack(side="left")
        ttk.Button(btns, text="删除选中", command=self._delete_segment).pack(side="left", padx=6)
        ttk.Label(
            right,
            text="倍率可为正数、0 或负数：0 表示停留，负数表示反向运动。\n"
                 "第 0 拍开始的一项不可删除，只能修改。",
            style="Dim.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
        if self.beat_hint:
            ttk.Label(right, text=self.beat_hint, style="Dim.TLabel", justify="left").pack(
                anchor="w", pady=(2, 0)
            )
        self._refresh_groups()

    # -------------------------------------------------------------- 组
    def _current_group(self) -> Optional[SpeedGroup]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self.document.chart.group_by_id(int(selection[0]))

    def _refresh_groups(self, focus: Optional[int] = None) -> None:
        chart = self.document.chart
        self.tree.delete(*self.tree.get_children())
        for group in sorted(chart.speed_groups, key=lambda g: g.id):
            name = group.name + ("（默认）" if group.is_default else "")
            self.tree.insert("", "end", iid=str(group.id), values=(group.id, name, len(group.note_ids)))
        target = focus if focus is not None else (chart.speed_groups[0].id if chart.speed_groups else 0)
        if self.tree.exists(str(target)):
            self.tree.selection_set(str(target))
        self._refresh_segments()

    def _refresh_segments(self) -> None:
        self.seg_tree.delete(*self.seg_tree.get_children())
        group = self._current_group()
        if group is None:
            return
        for index, seg in enumerate(group.segments):
            self.seg_tree.insert("", "end", iid=str(index),
                                 values=(f"{seg.start_beat:g}", f"{seg.multiplier:g}"))
        if group.segments:
            self.seg_tree.selection_set("0")
            self._on_seg_select()

    def _on_seg_select(self, _event=None) -> None:
        group = self._current_group()
        selection = self.seg_tree.selection()
        if group is None or not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(group.segments):
            seg = group.segments[index]
            self.e_start.delete(0, "end")
            self.e_start.insert(0, f"{seg.start_beat:g}")
            self.e_mult.delete(0, "end")
            self.e_mult.insert(0, f"{seg.multiplier:g}")

    def _new_group(self) -> None:
        name = f"速度组{len(self.document.chart.speed_groups)}"
        group = self.document.apply("新建速度组", lambda: self.document.chart.add_group(name))
        self.changed = True
        self._refresh_groups(group.id)

    def _clone_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        clone = self.document.apply(
            "复制速度组",
            lambda: self.document.chart.add_group(
                group.name + " 副本",
                [SpeedSegment(s.start_beat, s.multiplier) for s in group.segments],
            ),
        )
        self.changed = True
        self._refresh_groups(clone.id)

    def _rename_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        dialog = simpledialog.askstring("重命名速度组", "组名称：", initialvalue=group.name, parent=self)
        if dialog:
            self.document.apply("重命名速度组", lambda: setattr(group, "name", dialog))
            self.changed = True
            self._refresh_groups(group.id)

    def _delete_group(self) -> None:
        group = self._current_group()
        if group is None:
            return
        if group.is_default:
            messagebox.showwarning("不可删除", "默认组不可删除，只能编辑。", parent=self)
            return
        if not messagebox.askyesno("删除速度组", f"删除「{group.name}」？组内物件会回到默认组。", parent=self):
            return
        self.document.apply("删除速度组", lambda: self.document.chart.delete_group(group.id))
        self.changed = True
        self._refresh_groups(config.DEFAULT_GROUP_ID)

    def _assign_selection(self) -> None:
        group = self._current_group()
        if group is None:
            return
        ids = sorted(self.document.selection)
        if not ids:
            messagebox.showinfo("没有选择", "请先在画布上选择物件。", parent=self)
            return
        self.document.apply("设置速度组", lambda: self.document.chart.assign_group(ids, group.id))
        self.changed = True
        self._refresh_groups(group.id)

    def _upsert_segment(self) -> None:
        group = self._current_group()
        if group is None:
            return
        start = parse_float(self.e_start.get(), -1.0)
        mult = parse_float(self.e_mult.get(), 1.0)
        if start < 0:
            messagebox.showerror("数值错误", "起始拍需 ≥ 0。", parent=self)
            return
        segments = [SpeedSegment(s.start_beat, s.multiplier) for s in group.segments]
        segments = [s for s in segments if abs(s.start_beat - start) > 1e-9]
        segments.append(SpeedSegment(start, mult))
        self.document.apply("编辑流速列表", lambda: group.set_segments(segments))
        self.changed = True
        self._refresh_segments()

    def _delete_segment(self) -> None:
        group = self._current_group()
        selection = self.seg_tree.selection()
        if group is None or not selection:
            return
        index = int(selection[0])
        if index == 0 or abs(group.segments[index].start_beat) < 1e-9:
            messagebox.showwarning("不可删除", "第 0 拍开始的一项不可删除，只能修改。", parent=self)
            return
        segments = [s for i, s in enumerate(group.segments) if i != index]
        self.document.apply("编辑流速列表", lambda: group.set_segments(segments))
        self.changed = True
        self._refresh_segments()

    def _ok(self) -> None:
        self.result = self.changed
        self.destroy()


# ---------------------------------------------------------------- 物件编辑
class NoteEditDialog(BaseDialog):
    title_text = "编辑物件"

    def __init__(self, parent: tk.Misc, theme: Theme, note: Note, beat_hint: str = "") -> None:
        self.note = note
        self.beat_hint = beat_hint
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        body.columnconfigure(1, weight=1)
        note = self.note
        if isinstance(note, LaneNote):
            ttk.Label(body, text=f"{note.id} · 6K 下落式物件", style="Head.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
            )
            self.e_lane_start = labeled_spin(body, 1, "开始轨道（1-6）", note.lane_start + 1, 1, 6)
            self.e_lane_end = labeled_spin(body, 2, "结束轨道（1-6）", note.lane_end + 1, 1, 6)
            self.e_beat_start = labeled_entry(body, 3, "开始拍", f"{note.beat_start:g}", width=14)
            self.e_beat_end = labeled_entry(body, 4, "结束拍（长按）", f"{note.beat_end:g}", width=14)
            ttk.Label(
                body,
                text="开始轨道编号必须小于或等于结束轨道；结束拍大于开始拍时即为长按。",
                style="Dim.TLabel",
                wraplength=380,
            ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        elif isinstance(note, MouseRegion):
            ttk.Label(body, text=f"{note.id} · 空域", style="Head.TLabel").grid(
                row=0, column=0, columnspan=4, sticky="w", pady=(0, 6)
            )
            self.e_beat_start = labeled_entry(body, 1, "开始拍", f"{note.beat_start:g}", width=14)
            self.e_beat_end = labeled_entry(body, 2, "结束拍", f"{note.beat_end:g}", width=14)
            ttk.Label(body, text="左边界", style="Head.TLabel").grid(row=3, column=0, columnspan=4, sticky="w",
                                                                    pady=(8, 0))
            self.e_left_start = labeled_entry(body, 4, "开始位置", f"{note.left.start:g}", width=10)
            self.e_left_end = labeled_entry(body, 4, "结束位置", f"{note.left.end:g}", width=10, column=2)
            self.c_left_ease = labeled_combo(
                body, 5, "线型", [config.EASE_LABELS[e] for e in config.EASE_CHOICES],
                config.EASE_LABELS[note.left.ease],
            )
            ttk.Label(body, text="右边界", style="Head.TLabel").grid(row=6, column=0, columnspan=4, sticky="w",
                                                                    pady=(8, 0))
            self.e_right_start = labeled_entry(body, 7, "开始位置", f"{note.right.start:g}", width=10)
            self.e_right_end = labeled_entry(body, 7, "结束位置", f"{note.right.end:g}", width=10, column=2)
            self.c_right_ease = labeled_combo(
                body, 8, "线型", [config.EASE_LABELS[e] for e in config.EASE_CHOICES],
                config.EASE_LABELS[note.right.ease],
            )
            ttk.Label(
                body,
                text="位置以合法区域最左侧为 0、最右侧为 1，允许超出（例如 -0.2 或 1.3）；\n"
                     "放置时空域尚未点击的边界点按默认值显示：左边界 0、右边界 1。",
                style="Dim.TLabel",
                wraplength=420,
            ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(4, 0))
        elif isinstance(note, MouseSlide):
            ttk.Label(body, text=f"{note.id} · 鼠标滑键", style="Head.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
            )
            self.e_beat = labeled_entry(body, 1, "判定拍", f"{note.beat:g}", width=14)
            self.e_x_start = labeled_entry(body, 2, "起始位置", f"{note.x_start:g}", width=14)
            self.e_x_end = labeled_entry(body, 3, "结束位置", f"{note.x_end:g}", width=14)
            ttk.Label(
                body,
                text="结束位置大于起始位置表示向右滑动，小于表示向左滑动。",
                style="Dim.TLabel",
            ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        else:  # pragma: no cover
            ttk.Label(body, text="未知物件类型").grid(row=0, column=0)
        if self.beat_hint:
            ttk.Label(body, text=self.beat_hint, style="Dim.TLabel").grid(
                row=30, column=0, columnspan=4, sticky="w", pady=(8, 0)
            )

    def _ok(self) -> None:
        note = self.note
        ease_by_label = config.EASE_LABEL_TO_VALUE
        if isinstance(note, LaneNote):
            lane_start = max(1, min(6, parse_int(self.e_lane_start.get(), note.lane_start + 1))) - 1
            lane_end = max(1, min(6, parse_int(self.e_lane_end.get(), note.lane_end + 1))) - 1
            if lane_start > lane_end:
                lane_start, lane_end = lane_end, lane_start
            beat_start = parse_float(self.e_beat_start.get(), note.beat_start)
            beat_end = parse_float(self.e_beat_end.get(), note.beat_end)
            if beat_end < beat_start:
                beat_end = beat_start
            self.result = {
                "id": note.id,
                "type": note.kind,
                "lane_start": lane_start,
                "lane_end": lane_end,
                "beat_start": beat_start,
                "beat_end": beat_end,
                "group": note.group,
            }
        elif isinstance(note, MouseRegion):
            self.result = {
                "id": note.id,
                "type": note.kind,
                "beat_start": parse_float(self.e_beat_start.get(), note.beat_start),
                "beat_end": parse_float(self.e_beat_end.get(), note.beat_end),
                "left": BoundaryCurve(
                    parse_float(self.e_left_start.get(), note.left.start),
                    parse_float(self.e_left_end.get(), note.left.end),
                    ease_by_label.get(self.c_left_ease.get(), config.EASE_LINEAR),
                ).to_dict(),
                "right": BoundaryCurve(
                    parse_float(self.e_right_start.get(), note.right.start),
                    parse_float(self.e_right_end.get(), note.right.end),
                    ease_by_label.get(self.c_right_ease.get(), config.EASE_LINEAR),
                ).to_dict(),
                "group": note.group,
            }
        else:
            x_start = parse_float(self.e_x_start.get(), note.x_start)
            x_end = parse_float(self.e_x_end.get(), note.x_end)
            if abs(x_end - x_start) < 1e-6:
                x_end = x_start + config.MIN_SLIDE_SPAN
            self.result = {
                "id": note.id,
                "type": note.kind,
                "beat": parse_float(self.e_beat.get(), note.beat),
                "x_start": x_start,
                "x_end": x_end,
                "group": note.group,
            }
        self.destroy()


# ---------------------------------------------------------------- 帮助
class HelpDialog(BaseDialog):
    """纯文本帮助窗口（操作指南 / 快捷键共用同一套界面）。"""

    title_text = "操作指南"
    ok_text = "关闭"

    def __init__(self, parent: tk.Misc, theme: Theme, title: str = "操作指南",
                 content: str = "") -> None:
        self.title_text = title
        self.content = content or GUIDE_TEXT
        super().__init__(parent, theme)

    def _build(self) -> None:
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True)
        text = tk.Text(
            frame,
            width=74,
            height=26,
            wrap="word",
            background=self.theme.bg,
            foreground=self.theme.text,
            insertbackground=self.theme.text,
            relief="flat",
            padx=10,
            pady=8,
            font=self.theme.font(self.theme.font_normal),
        )
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.insert("1.0", self.content)
        text.configure(state="disabled")

    def _ok(self) -> None:
        self.result = None
        self.destroy()


GUIDE_TEXT = """【界面】
编辑器把两个平面左右分栏：左边 6K 下落式、右边鼠标平面，两者共用同一条判定线
（同一个时间刻度）与同一套时间网格，所以滚动 / 缩放永远同步。
按 Tab 进入演示模式后，两个平面叠加显示（鼠标平面压在中四条轨道上）并且不可编辑。

【放置工具】（顶栏「放置 ▾」菜单，或按数字键 1-7 切换）
1 单轨按键：点击 6K 区域 -> 在对应轨道放置按键，时间自动吸附网格。
2 多轨按键：先点击一条轨道，再点击另一条轨道 -> 生成跨越这两条轨道的多轨按键；
   时间以第一次点击为准。
3 长按（含多轨）：先点击起点（轨道 + 时间），再点击终点 —— 第二次点击的横坐标决定
   结束轨道、纵坐标决定结束时间；点在鼠标区域内则只改时间、轨道不变。
   可以滚动 / 缩放后再点终点，时间自动吸附网格。
4 空域（鼠标停留区域）：先点击开始时间、结束时间；再依次点击「左边界起点 / 左边界终点 /
   右边界起点 / 右边界终点」四个位置（未点击的点按默认值预览：左边界 0、右边界 1）；
   最后在弹出的对话框里选择两条边界的线型（线性 / 缓入 / 缓出），也可以微调位置。
5 左右滑键：先点击设定时间与横向位置，再点击第二次决定滑动方向；
   开启横向辅助网格时会吸附到网格线上。
Esc 取消当前放置流程；切换工具同样会放弃未完成的操作。放置过程中会显示幽灵预览。

【选择与编辑】
选择工具下拖动 = 区域选择；Ctrl + 点击/框选 = 与上次选择做对称差（两次都选中的取消选择）。
顶栏「编辑 ▾」菜单里可以复制、粘贴（点击目标时间，轨道/横向位置保持不变）、
沿游玩区中轴线镜像、编辑选中物件（仅单选时可用）、删除与全选。

【视图】
滚轮：每次滚动时间网格的一格（1 / 网格细分 拍）；Shift + 滚轮一次滚动 1 拍。
滚动后判定线会自动吸附回网格，不会停在格子之间。
Ctrl + 滚轮、顶栏右侧的 − / + 按钮，或「视图 ▾ → 缩放」都能缩放：
+ 放大（屏幕内时间变少、物件变大），− 缩小（屏幕内时间变多）。
用**右侧的竖向滚动条**可以拖动/翻页移动时间轴（滑块长度 = 当前可视时间占比）；
Home / End 跳到开头 / 结尾。

【速度组】
顶栏「速度组」按钮里可新建/复制/删除组，编辑流速列表（从某拍开始，流速 = 默认速度 × 倍率，
支持 0 与负数实现停留/反向），并把当前选择加入组。删除组时组内物件回到默认组。
流速可正可负并会叠加，因此物件位置不是判定时刻的单调函数：远处的物件也可能被推回
判定线上，编辑器始终按 ∫v 的结果显示。

【播放与演示】
空格播放 / 暂停；暂停时判定线会自动吸附到时间轴刻度上。
「编辑器设置」里的系统音频延迟用于对齐画面与声音（蓝牙耳机常见 120~250 ms）。
演示模式在「视图 ▾」菜单里；进入后工具栏隐藏、画面只读，支持滚动、缩放与播放 / 暂停，
按 Tab 或 Esc 返回编辑。
"""

SHORTCUTS_TEXT = """【工具切换】
1  选择 / 区域选择
2  单轨按键
3  多轨按键
4  长按（含多轨）
5  空域
6  左右滑键
Esc  取消当前放置流程（演示模式下为退出演示）
Tab  进入 / 退出演示模式

【编辑】
Ctrl+C  复制选中物件
Ctrl+V  进入粘贴模式，点击目标时间完成粘贴（轨道 / 横向位置不变）
Ctrl+Z  撤销
Ctrl+Y / Ctrl+Shift+Z  重做
Ctrl+A  全选
M  沿游玩区域中轴线镜像
E  编辑选中物件（仅单选时可用）
Delete / Backspace  删除选中物件

【文件】
Ctrl+N  新建谱面
Ctrl+O  打开谱面
Ctrl+S  保存
Ctrl+Shift+S  另存为

【视图与播放】
滚轮  时间轴前进 / 后退一格（1 / 网格细分 拍，自动吸附网格）
Shift + 滚轮  一次滚动 1 拍
Ctrl + 滚轮  缩放
+ / =  放大（屏幕内时间变少、物件变大）
-  缩小（屏幕内时间变多）
← / →  按网格步进
PageUp / PageDown  半屏滚动
Home / End  跳到开头 / 结尾
空格  播放 / 暂停（暂停时判定线吸附网格）
F10  最大化 / 还原窗口
F11  无边框全屏切换
"""

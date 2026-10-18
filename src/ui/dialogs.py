"""编辑器的各类对话框。

设计：对话框只负责收集数据并返回结果，真正的谱面修改由调用方通过
Document.apply 完成，保证每一步都能撤销。
"""

from __future__ import annotations

import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import config
from editor_settings import EditorSettings
from model.chart import Chart, Metadata
from model.document import Document
from model.notes import (
    BoundaryCurve,
    ForbiddenRegion,
    LaneNote,
    MouseRegion,
    MouseSlide,
    Note,
)
from model.speed import SpeedGroup, SpeedSegment
from model.timing import BpmSegment, TimingMap
from shortcuts import (
    EDITABLE_ACTIONS,
    FIXED_SHORTCUTS,
    MODIFIER_TOKENS,
    event_token,
    format_modifiers,
    normalize_shortcut,
    normalize_shortcuts,
    shortcut_signature,
)
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
    """统一风格的模态对话框基类。

    底部只有一个确认按钮（各对话框的 ok_text）；取消走 Esc / 关闭窗口。
    """

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
        self.buttons = buttons
        self._extra_buttons(buttons)
        self.ok_button = ttk.Button(
            buttons, text=self.ok_text, style="Accent.TButton", command=self._ok
        )
        self.ok_button.pack(side="right")
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

    def _extra_buttons(self, buttons: ttk.Frame) -> None:
        """子类可以在确认按钮旁边加按钮（默认没有）。"""

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


class SaveChangesDialog(BaseDialog):
    """关闭 / 新建 / 打开前的“未保存修改”询问：保存 / 不保存。

    没有「取消」按钮：放弃这次操作走 Esc / 关闭窗口（_cancel）。
    """

    title_text = "未保存的修改"
    ok_text = "保存"

    def _build(self) -> None:
        ttk.Label(
            self.body, text="当前谱面有未保存的修改，是否保存？", wraplength=420
        ).pack(anchor="w")

    def _extra_buttons(self, buttons: ttk.Frame) -> None:
        ttk.Button(buttons, text="不保存", command=self._discard).pack(
            side="right", padx=(0, 8)
        )

    def _discard(self) -> None:
        self.result = False
        self.destroy()

    def _ok(self) -> None:
        self.result = True
        self.destroy()


class TextInputDialog(BaseDialog):
    """单行输入框（只有确认按钮）：替代 tkinter.simpledialog 的弹窗。

    kind="int" 时按整数返回（可给 minimum / maximum 夹取）；
    输入不合法时停在窗口里提示，不关闭。
    """

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        title: str,
        prompt: str,
        initial: str = "",
        kind: str = "text",
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> None:
        self.title_text = title
        self.prompt = prompt
        self.initial = initial
        self.kind = kind
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(parent, theme)

    def _build(self) -> None:
        ttk.Label(self.body, text=self.prompt, wraplength=420).pack(anchor="w")
        self.entry = ttk.Entry(self.body, width=24)
        self.entry.pack(anchor="w", pady=(6, 0))
        self.entry.insert(0, self.initial)
        self.entry.selection_range(0, "end")
        self.entry.bind("<Return>", lambda _e: self._ok())
        self.hint = ttk.Label(self.body, text="", style="Warn.TLabel")
        self.hint.pack(anchor="w", pady=(4, 0))
        self.entry.focus_set()

    def _ok(self) -> None:
        text = self.entry.get().strip()
        if self.kind == "int":
            try:
                value: Any = int(float(text))
            except (TypeError, ValueError):
                self.hint.configure(text="请输入整数")
                return
            if self.minimum is not None:
                value = max(self.minimum, value)
            if self.maximum is not None:
                value = min(self.maximum, value)
        else:
            if not text:
                self.hint.configure(text="请输入内容")
                return
            value = text
        self.result = value
        self.destroy()


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


class ScrollableCombobox(ttk.Combobox):
    """只读下拉框：弹窗最多显示指定行数，超出后可滚轮滚动。"""

    def __init__(self, parent: tk.Misc, *, max_visible: int = 5, **kwargs: Any) -> None:
        self.max_visible = max(1, int(max_visible))
        super().__init__(parent, state="readonly", **kwargs)
        # Combobox 类绑定展开时会重新设置默认高度，因此等整个事件结束后再修正。
        self.bind("<ButtonPress-1>", self._schedule_fit_popdown, add="+")
        self.bind("<KeyPress>", self._schedule_fit_popdown, add="+")
        self.after_idle(self._fit_popdown)

    def _schedule_fit_popdown(self, _event=None) -> None:
        self.after_idle(self._fit_popdown)

    def _fit_popdown(self, _event=None) -> None:
        try:
            popdown = str(self.tk.call("ttk::combobox::PopdownWindow", self))
            listbox = f"{popdown}.f.l"
            values = self.cget("values")
            height = min(self.max_visible, max(1, len(values)))
            self.tk.call(listbox, "configure", "-height", height)
        except tk.TclError:  # pragma: no cover - 依赖 Tk 实现
            pass


def labeled_scroll_combo(
    parent: tk.Misc,
    row: int,
    label: str,
    values: Sequence[str],
    value: str,
    *,
    column: int = 0,
    width: int = 24,
    max_visible: int = 5,
) -> ScrollableCombobox:
    ttk.Label(parent, text=label).grid(
        row=row, column=column, sticky="w", pady=3, padx=(0, 6)
    )
    combo = ScrollableCombobox(
        parent, values=list(values), width=width, max_visible=max_visible
    )
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
        self.e_diff_level = labeled_entry(body, 3, "难度等级", self.metadata.difficulty_level, width=30)
        self.e_author = labeled_entry(body, 4, "作者", self.metadata.author, width=30)

        ttk.Label(body, text="音频文件名").grid(row=5, column=0, sticky="w", pady=3, padx=(0, 6))
        audio_row = ttk.Frame(body)
        audio_row.grid(row=5, column=1, sticky="we", pady=3)
        audio_row.columnconfigure(0, weight=1)
        self.e_audio = ttk.Entry(audio_row, width=30)
        self.e_audio.insert(0, self.metadata.audio_file)
        self.e_audio.grid(row=0, column=0, sticky="we")
        ttk.Button(audio_row, text="选择文件…", command=self._pick_audio).grid(row=0, column=1, padx=(6, 0))

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

        table = ttk.Frame(body)
        table.pack(fill="both", expand=True)
        columns = ("start", "bpm", "bar")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=9, selectmode="browse")
        for key, text, width in (
            ("start", "起始拍", 90),
            ("bpm", "BPM", 110),
            ("bar", "每小节拍数", 150),
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
        ttk.Button(buttons, text="新增/更新", command=self._upsert).pack(side="left")
        ttk.Button(buttons, text="删除选中", command=self._delete).pack(side="left")
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

    def _read_form(self) -> Optional[BpmSegment]:
        start = parse_float(self.e_start.get(), -1.0)
        bpm = parse_float(self.e_bpm.get(), -1.0)
        bar = parse_int(self.e_bar.get(), -1)
        if start < 0 or bpm <= 0 or bar < 0:
            messagebox.showerror(
                "数值错误",
                "起始拍需 ≥ 0，BPM 需 > 0，每小节拍数需 ≥ 0。",
                parent=self,
            )
            return None
        return BpmSegment(start, bpm, bar)

    def _upsert(self) -> None:
        """新增 / 更新合成一个按钮（与速度组一致）：起始拍相同就覆盖，没有就追加。"""
        seg = self._read_form()
        if seg is None:
            return
        self.segments = [
            s for s in self.segments if abs(s.start_beat - seg.start_beat) > 1e-9
        ]
        self.segments.append(seg)
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
        if focus_beat is None:
            return
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


class VolumeDialog(BaseDialog):
    """音效菜单「音量…」：音乐 / 节拍音 / 打击音效三路音量（0 ~ 100%）。"""

    title_text = "音量"

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        chart: Chart,
        settings: Optional[EditorSettings] = None,
    ) -> None:
        source = settings or chart.settings
        self.settings = EditorSettings(
            music_volume=source.music_volume,
            metronome_volume=source.metronome_volume,
            hit_volume=source.hit_volume,
        )
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        box = ttk.LabelFrame(body, text="音量", padding=8)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self._vars: Dict[str, tk.DoubleVar] = {}
        self._labels: Dict[str, ttk.Label] = {}
        rows = (
            ("music_volume", "音乐", self.settings.music_volume),
            ("metronome_volume", "节拍音", self.settings.metronome_volume),
            ("hit_volume", "打击音效", self.settings.hit_volume),
        )
        for row, (key, text, value) in enumerate(rows):
            ttk.Label(box, text=text).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
            var = tk.DoubleVar(value=float(value))
            ttk.Scale(
                box,
                from_=0.0,
                to=1.0,
                orient="horizontal",
                variable=var,
                command=lambda _v, k=key: self._on_volume(k),
            ).grid(row=row, column=1, sticky="we", pady=3)
            label = ttk.Label(box, text=f"{value * 100:.0f}%", width=5, anchor="e")
            label.grid(row=row, column=2, sticky="e", pady=3)
            self._vars[key] = var
            self._labels[key] = label

    def _on_volume(self, key: str) -> None:
        value = float(self._vars[key].get())
        setattr(self.settings, key, value)
        self._labels[key].configure(text=f"{value * 100:.0f}%")

    def _ok(self) -> None:
        self.result = {
            "music_volume": float(self._vars["music_volume"].get()),
            "metronome_volume": float(self._vars["metronome_volume"].get()),
            "hit_volume": float(self._vars["hit_volume"].get()),
        }
        self.destroy()


# ---------------------------------------------------------------- 编辑器设置
class SettingsDialog(BaseDialog):
    title_text = "编辑器设置"

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        chart: Chart,
        settings: Optional[EditorSettings] = None,
    ) -> None:
        self.chart = chart
        source = settings or chart.settings
        self.settings = EditorSettings(
            zoom_seconds=source.zoom_seconds,
            grid_division=source.grid_division,
            horizontal_divisions=source.horizontal_divisions,
            audio_latency_ms=source.audio_latency_ms,
            music_volume=source.music_volume,
            metronome_volume=source.metronome_volume,
            hit_volume=source.hit_volume,
            invert_wheel_scroll=source.invert_wheel_scroll,
            invert_wheel_zoom=source.invert_wheel_zoom,
        )
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        group = ttk.LabelFrame(body, text="缩放", padding=8)
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

        latency = ttk.LabelFrame(body, text="系统音频延迟", padding=8)
        latency.pack(fill="x", pady=(8, 0))
        self.e_latency = labeled_entry(
            latency, 0, "延迟（毫秒）", f"{self.settings.audio_latency_ms:g}", width=12
        )
        ttk.Label(
            latency,
            text="允许负数，负值代表声音比软件位置更早。",
            style="Dim.TLabel",
            wraplength=520,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        wheel = ttk.LabelFrame(body, text="滚轮方向", padding=8)
        wheel.pack(fill="x", pady=(8, 0))
        self.var_invert_wheel_scroll = tk.BooleanVar(
            value=bool(self.settings.invert_wheel_scroll)
        )
        self.var_invert_wheel_zoom = tk.BooleanVar(
            value=bool(self.settings.invert_wheel_zoom)
        )
        ttk.Checkbutton(
            wheel,
            text="反转滚轮",
            variable=self.var_invert_wheel_scroll,
            command=lambda: setattr(
                self.settings,
                "invert_wheel_scroll",
                bool(self.var_invert_wheel_scroll.get()),
            ),
        ).pack(anchor="w")
        ttk.Checkbutton(
            wheel,
            text="反转滚轮缩放",
            variable=self.var_invert_wheel_zoom,
            command=lambda: setattr(
                self.settings,
                "invert_wheel_zoom",
                bool(self.var_invert_wheel_zoom.get()),
            ),
        ).pack(anchor="w", pady=(4, 0))

        # 音频后端诊断：状态栏显示 silent 时可以在这里看清原因。
        from audio import audio_backend_name, audio_backend_state

        backend_box = ttk.LabelFrame(body, text="音频后端", padding=8)
        backend_box.pack(fill="x", pady=(8, 0))
        state = audio_backend_state()
        state_text = {
            "ok": f"{audio_backend_name()}",
            "missing": "silent · 未安装 pygame（执行 pip install -r requirements.txt）",
            "device": "silent · pygame 已安装，但音频设备打不开（请检查系统输出设备或关闭独占声卡的软件）",
        }.get(state, state)
        ttk.Label(
            backend_box,
            text=f"当前：{state_text}\n解释器：{sys.executable}",
            style="Dim.TLabel",
            wraplength=520,
        ).pack(anchor="w")

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
            text=f"当前约 {self.settings.zoom_seconds:.3g} 秒 ≈ {beats:.2f} 拍"
        )

    def _ok(self) -> None:
        self._apply_zoom_entry()
        self.result = {
            "zoom_seconds": self.settings.zoom_seconds,
            "audio_latency_ms": parse_float(self.e_latency.get(), 0.0),
            "music_volume": self.settings.music_volume,
            "metronome_volume": self.settings.metronome_volume,
            "hit_volume": self.settings.hit_volume,
            "invert_wheel_scroll": bool(self.var_invert_wheel_scroll.get()),
            "invert_wheel_zoom": bool(self.var_invert_wheel_zoom.get()),
        }
        self.destroy()


# ---------------------------------------------------------------- 快捷键设置
class ShortcutDialog(BaseDialog):
    """逐项录制组合键；切换项、保存和关闭窗口都会提交当前录制。"""

    title_text = "修改快捷键"
    ok_text = "保存"

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        shortcuts: Dict[str, str],
    ) -> None:
        self.shortcuts = normalize_shortcuts(shortcuts)
        self._active_action: Optional[str] = None
        self._captured_modifiers: set[str] = set()
        self._labels: Dict[str, ttk.Label] = {}
        self._buttons: Dict[str, ttk.Button] = {}
        super().__init__(parent, theme)
        self.cancel_button = ttk.Button(
            self.buttons, text="取消", command=self._cancel
        )
        self.cancel_button.pack(side="right", padx=(0, 6))
        self.protocol("WM_DELETE_WINDOW", self._ok)
        self.update_idletasks()
        self._center(parent)

    def _build(self) -> None:
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            frame,
            width=680,
            height=480,
            background=self.theme.panel,
            highlightthickness=0,
            bd=0,
            yscrollincrement=24,
        )
        self._shortcut_canvas = canvas
        scroll = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        rows = ttk.Frame(canvas)
        self._shortcut_rows = rows
        window = canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.columnconfigure(0, weight=1)
        rows.columnconfigure(1, weight=0, minsize=210)
        rows.columnconfigure(2, weight=0, minsize=80)

        row = 0
        ttk.Label(rows, text="可修改功能", style="Head.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        row += 1
        for action_id, label in EDITABLE_ACTIONS:
            ttk.Label(rows, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            value = ttk.Label(
                rows,
                text=self.shortcuts.get(action_id) or "未启用",
                anchor="w",
            )
            value.grid(row=row, column=1, sticky="w", padx=(0, 8), pady=2)
            button = ttk.Button(rows, text="设置", width=8)
            button.configure(command=lambda action=action_id: self._toggle_capture(action))
            button.grid(row=row, column=2, sticky="e", pady=1)
            self._labels[action_id] = value
            self._buttons[action_id] = button
            row += 1

        ttk.Separator(rows).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        ttk.Label(rows, text="固定快捷键", style="Head.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        row += 1
        for label, value in FIXED_SHORTCUTS:
            ttk.Label(rows, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=1)
            ttk.Label(rows, text=value, anchor="w").grid(
                row=row, column=1, sticky="w", padx=(0, 8), pady=1
            )
            ttk.Label(rows, text="不可修改", style="Dim.TLabel").grid(
                row=row, column=2, sticky="e", pady=1
            )
            row += 1

        def update_scrollregion(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        rows.bind("<Configure>", update_scrollregion)
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window, width=event.width),
        )

        def bind_rows_wheel(widget) -> None:
            widget.bind("<MouseWheel>", self._on_shortcut_wheel, add="+")
            widget.bind(
                "<Button-4>",
                lambda event: self._on_shortcut_wheel_linux(event, -1),
                add="+",
            )
            widget.bind(
                "<Button-5>",
                lambda event: self._on_shortcut_wheel_linux(event, 1),
                add="+",
            )
            for child in widget.winfo_children():
                bind_rows_wheel(child)

        bind_rows_wheel(self)
        self.bind("<KeyPress>", self._capture_key, add="+")

    def _on_shortcut_wheel(self, event) -> str:
        delta = event.delta
        notches = delta / 120.0 if abs(delta) >= 120 else float(delta)
        self._shortcut_canvas.yview_scroll(int(-notches), "units")
        return "break"

    def _on_shortcut_wheel_linux(self, _event, direction: int) -> str:
        self._shortcut_canvas.yview_scroll(direction, "units")
        return "break"

    def _show_value(self, action_id: str) -> None:
        value = self.shortcuts.get(action_id, "")
        self._labels[action_id].configure(text=value or "未启用")
        self._buttons[action_id].configure(text="设置")

    def _commit_capture(self) -> None:
        if self._active_action is None:
            return
        action_id = self._active_action
        self._active_action = None
        self._captured_modifiers.clear()
        self._show_value(action_id)

    def _toggle_capture(self, action_id: str) -> None:
        if self._active_action == action_id:
            self._commit_capture()
            return
        if self._active_action is not None:
            self._commit_capture()
        self._active_action = action_id
        self._captured_modifiers.clear()
        self._labels[action_id].configure(text="按下组合键…")
        self._buttons[action_id].configure(text="取消设置")
        try:
            self.focus_set()
        except tk.TclError:  # pragma: no cover
            pass

    def _capture_key(self, event) -> Optional[str]:
        if self._active_action is None:
            return None
        if event.keysym == "Escape":
            action_id = self._active_action
            self._active_action = None
            self._captured_modifiers.clear()
            self._show_value(action_id)
            return "break"
        token = event_token(event.keysym)
        if token in MODIFIER_TOKENS:
            self._captured_modifiers.add(token)
            text = format_modifiers(self._captured_modifiers) or "按下组合键…"
            self._labels[self._active_action].configure(text=text)
            return "break"

        modifiers = set(self._captured_modifiers)
        if token == "+":
            modifiers.discard("Shift")
        parts = [format_modifiers(modifiers), token]
        action_id = self._active_action
        self.shortcuts[action_id] = normalize_shortcut(
            " ".join(part for part in parts if part)
        )
        self._active_action = None
        self._captured_modifiers.clear()
        self._show_value(action_id)
        return "break"

    def _conflict_lines(self) -> List[str]:
        """按固定快捷键优先、其余按默认顺序列出完全相同的组合。"""
        entries: List[Tuple[Tuple[Tuple[str, ...], str], str]] = []
        seen: set[Tuple[Tuple[Tuple[str, ...], str], str]] = set()

        def add(label: str, value: str) -> None:
            for variant in value.split(" / "):
                if "滚轮" in variant:
                    continue
                signature = shortcut_signature(variant)
                if signature is None:
                    continue
                item = (signature, label)
                if item not in seen:
                    seen.add(item)
                    entries.append(item)

        # 固定绑定由 Tk 的具体 sequence 优先匹配。
        for label, value in FIXED_SHORTCUTS:
            add(label, value)
        editable_labels = dict(EDITABLE_ACTIONS)
        for action_id in normalize_shortcuts(self.shortcuts):
            add(editable_labels.get(action_id, action_id), self.shortcuts[action_id])

        groups: Dict[Tuple[Tuple[str, ...], str], List[str]] = {}
        for signature, label in entries:
            groups.setdefault(signature, []).append(label)

        lines = []
        for labels in groups.values():
            if len(labels) < 2:
                continue
            winner = labels[0]
            for other in labels[1:]:
                lines.append(f"{winner}与{other}冲突，只会触发{winner}")
        return lines

    def _ok(self) -> None:
        self._commit_capture()
        conflicts = self._conflict_lines()
        if conflicts:
            confirmed = messagebox.askyesno(
                "快捷键冲突",
                "现有以下按键冲突：\n"
                + "\n".join(conflicts)
                + "\n确定要保存吗？",
                parent=self,
            )
            if not confirmed:
                return
        self.result = {key: normalize_shortcut(value) for key, value in self.shortcuts.items()}
        self.destroy()

    def _cancel(self) -> None:
        self._active_action = None
        self._captured_modifiers.clear()
        self.result = None
        self.destroy()


# ---------------------------------------------------------------- 演示视图
class DemoViewDialog(BaseDialog):
    """其他设置菜单「演示视图设置…」。

    只包含与画面有关的演示渲染参数（物件厚度 / 流速 / 侧轨高亮）；
    演示模式固定用软件渲染，所以这里不提供渲染后端选择。

    该对话框编辑已弃用演示视图的现有参数，界面上会注明弃用状态。
    """

    title_text = "演示视图设置"

    def __init__(self, parent: tk.Misc, theme: Theme, settings: EditorSettings) -> None:
        self.settings = EditorSettings(
            demo_note_thickness=settings.demo_note_thickness,
            demo_multi_thickness=settings.demo_multi_thickness,
            demo_slide_thickness=settings.demo_slide_thickness,
            demo_speed=settings.demo_speed,
            demo_side_lane_highlight=settings.demo_side_lane_highlight,
        )
        self._vars: Dict[str, tk.DoubleVar] = {}
        self._labels: Dict[str, ttk.Label] = {}
        super().__init__(parent, theme)

    def _build(self) -> None:
        body = self.body
        ttk.Label(
            body,
            text="演示视图已弃用：该预览不再维护，后续版本可能移除，这里仅保留既有参数。",
            style="Warn.TLabel",
            wraplength=420,
        ).pack(anchor="w", pady=(0, 8))
        backend = ttk.LabelFrame(body, text="渲染后端", padding=8)
        backend.pack(fill="x")
        ttk.Label(backend, text="软件渲染", style="Dim.TLabel").pack(anchor="w")
        thickness = ttk.LabelFrame(body, text="物件厚度（世界单位：鼠标合法区宽 = 1）", padding=8)
        thickness.pack(fill="x", pady=(8, 0))
        thickness.columnconfigure(1, weight=1)
        note_low, note_high, note_step = config.DEMO_NOTE_THICKNESS_RANGE
        pattern_low, pattern_high, pattern_step = config.DEMO_PATTERN_THICKNESS_RANGE
        rows = (
            ("demo_note_thickness", "单键", note_low, note_high, note_step, 2),
            ("demo_multi_thickness", "多轨单键", note_low, note_high, note_step, 2),
            ("demo_slide_thickness", "滑键图案", pattern_low, pattern_high, pattern_step, 3),
        )
        for row, (key, text, low, high, step, digits) in enumerate(rows):
            self._add_slider(thickness, row, key, text, low, high, step, digits)

        speed_box = ttk.LabelFrame(body, text="流速", padding=8)
        speed_box.pack(fill="x", pady=(8, 0))
        speed_box.columnconfigure(1, weight=1)
        low, high, step = config.DEMO_SPEED_RANGE
        self._add_slider(speed_box, 0, "demo_speed", "流速", low, high, step, 1)
        self.speed_hint = ttk.Label(speed_box, text="", style="Dim.TLabel")
        self.speed_hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.var_side = tk.BooleanVar(value=bool(self.settings.demo_side_lane_highlight))
        ttk.Checkbutton(
            body, text="侧轨高亮", variable=self.var_side
        ).pack(anchor="w", pady=(8, 0))
        self._update_speed_hint()

    def _add_slider(
        self,
        box: ttk.LabelFrame,
        row: int,
        key: str,
        text: str,
        low: float,
        high: float,
        step: float,
        digits: int,
    ) -> None:
        ttk.Label(box, text=text).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
        var = tk.DoubleVar(value=float(getattr(self.settings, key)))
        ttk.Scale(
            box,
            from_=low,
            to=high,
            orient="horizontal",
            variable=var,
            command=lambda _v, k=key: self._on_slider(k),
        ).grid(row=row, column=1, sticky="we", pady=3)
        label = ttk.Label(box, text=self._format(key, float(var.get())), width=7, anchor="e")
        label.grid(row=row, column=2, sticky="e", pady=3)
        self._vars[key] = var
        self._labels[key] = label

    @staticmethod
    def _snap(value: float, step: float) -> float:
        return round(round(value / step) * step, 6)

    def _format(self, key: str, value: float) -> str:
        digits = 1 if key == "demo_speed" else (3 if "slide" in key else 2)
        return f"{value:.{digits}f}"

    def _on_slider(self, key: str) -> None:
        step = (
            config.DEMO_SPEED_RANGE[2]
            if key == "demo_speed"
            else (
                config.DEMO_PATTERN_THICKNESS_RANGE[2]
                if "slide" in key
                else config.DEMO_NOTE_THICKNESS_RANGE[2]
            )
        )
        value = self._snap(float(self._vars[key].get()), step)
        self._labels[key].configure(text=self._format(key, value))
        if key == "demo_speed":
            self._update_speed_hint(value)

    def _update_speed_hint(self, value: Optional[float] = None) -> None:
        speed = float(self._vars["demo_speed"].get()) if value is None else float(value)
        self.speed_hint.configure(text=f"可视时长 {1500.0 / max(0.1, speed):.0f} 毫秒")

    def _ok(self) -> None:
        def value(key: str, step: float) -> float:
            return self._snap(float(self._vars[key].get()), step)

        self.result = {
            "demo_note_thickness": value(
                "demo_note_thickness", config.DEMO_NOTE_THICKNESS_RANGE[2]
            ),
            "demo_multi_thickness": value(
                "demo_multi_thickness", config.DEMO_NOTE_THICKNESS_RANGE[2]
            ),
            "demo_slide_thickness": value(
                "demo_slide_thickness", config.DEMO_PATTERN_THICKNESS_RANGE[2]
            ),
            "demo_speed": value("demo_speed", config.DEMO_SPEED_RANGE[2]),
            "demo_side_lane_highlight": bool(self.var_side.get()),
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

        ttk.Label(right, text="流速列表", style="Head.TLabel").pack(anchor="w")
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
        name = run_dialog(TextInputDialog, self, self.theme, "重命名速度组", "组名称：", group.name)
        if name:
            self.document.apply("重命名速度组", lambda: setattr(group, "name", name))
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

    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        note: Note,
        beat_hint: str = "",
        speed_groups: Sequence[SpeedGroup] | None = None,
    ) -> None:
        self.note = note
        self.beat_hint = beat_hint
        groups = list(speed_groups or [SpeedGroup.default()])
        self.speed_groups = sorted(groups, key=lambda group: group.id)
        super().__init__(parent, theme)

    def _group_label(self, group: SpeedGroup) -> str:
        suffix = "（默认）" if group.is_default else ""
        return f"{group.id} · {group.name}{suffix}"

    def _select_current_group(self) -> None:
        labels = [self._group_label(group) for group in self.speed_groups]
        index = next(
            (
                position
                for position, group in enumerate(self.speed_groups)
                if group.id == self.note.group
            ),
            0,
        )
        self.c_group.configure(values=labels)
        self.c_group.current(index)

    def _selected_group_id(self) -> int:
        index = self.c_group.current()
        if 0 <= index < len(self.speed_groups):
            return self.speed_groups[index].id
        return self.speed_groups[0].id

    def _build(self) -> None:
        body = self.body
        body.columnconfigure(1, weight=1)
        note = self.note
        if isinstance(note, LaneNote):
            ttk.Label(body, text=f"{note.id} · 6K 下落式物件", style="Head.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
            )
            self.e_lane_start = labeled_spin(body, 1, "开始轨道", note.lane_start + 1, 1, 6)
            self.e_lane_end = labeled_spin(body, 2, "结束轨道", note.lane_end + 1, 1, 6)
            self.e_beat_start = labeled_entry(body, 3, "开始拍", f"{note.beat_start:g}", width=14)
            self.e_beat_end = labeled_entry(body, 4, "结束拍", f"{note.beat_end:g}", width=14)
            group_row = 5
        elif isinstance(note, MouseRegion):
            label = "禁区" if isinstance(note, ForbiddenRegion) else "空域"
            ttk.Label(body, text=f"{note.id} · {label}", style="Head.TLabel").grid(
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
                text="位置以合法区域最左侧为 0、最右侧为 1，允许超出。",
                style="Dim.TLabel",
                wraplength=420,
            ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(4, 0))
            group_row = 10
        elif isinstance(note, MouseSlide):
            ttk.Label(body, text=f"{note.id} · 鼠标滑键", style="Head.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 6)
            )
            self.e_beat = labeled_entry(body, 1, "判定拍", f"{note.beat:g}", width=14)
            self.e_x_start = labeled_entry(body, 2, "起始位置", f"{note.x_start:g}", width=14)
            self.e_x_end = labeled_entry(body, 3, "结束位置", f"{note.x_end:g}", width=14)
            group_row = 4
        else:  # pragma: no cover
            ttk.Label(body, text="未知物件类型").grid(row=0, column=0)
            group_row = 1
        self.c_group = labeled_scroll_combo(
            body,
            group_row,
            "所属时间组",
            [],
            "",
            width=28,
            max_visible=5,
        )
        self._select_current_group()
        if self.beat_hint:
            ttk.Label(body, text=self.beat_hint, style="Dim.TLabel").grid(
                row=30, column=0, columnspan=4, sticky="w", pady=(8, 0)
            )

    def _ok(self) -> None:
        note = self.note
        ease_by_label = config.EASE_LABEL_TO_VALUE
        group_id = self._selected_group_id()
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
                "group": group_id,
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
                "group": group_id,
            }
        elif isinstance(note, MouseSlide):
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
                "group": group_id,
            }
        else:  # pragma: no cover - 未知类型
            self.result = {}
        self.destroy()


# ---------------------------------------------------------------- 帮助
class HelpDialog(BaseDialog):
    """纯文本帮助窗口。"""

    title_text = "帮助"
    ok_text = "关闭"

    def __init__(self, parent: tk.Misc, theme: Theme, title: str = "帮助",
                 content: str = "") -> None:
        self.title_text = title
        self.content = content or ""
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


SHORTCUTS_TEXT = """【工具切换】
1  选择 / 区域选择
2  单轨按键
3  多轨按键
4  长按
5  空域
6  禁区
7  左右滑键
Esc  取消当前放置流程，演示模式下退出演示

【编辑】
Ctrl X  剪切选中物件
Ctrl C  复制选中物件
Ctrl V  进入粘贴模式，点击目标时间完成粘贴
Ctrl Z  撤销
Ctrl Y / Ctrl Shift Z  重做
Ctrl A  全选
M  沿游玩区域中轴线镜像
E  编辑选中物件
Delete / Backspace  删除选中物件

【文件】
Ctrl N  新建谱面
Ctrl O  打开谱面
Ctrl Shift O  快速打开上次谱面
Ctrl S  保存
Ctrl Shift S  另存为

【视图与播放】
滚轮  时间轴前进 / 后退一格，自动吸附网格
Shift + 滚轮  一次滚动 1 拍
Ctrl + 滚轮  缩放
+  放大
-  缩小
← / →  按网格步进
PageUp / PageDown  半屏滚动
空格  播放 / 暂停
F10  最大化 / 还原窗口
F11  无边框全屏切换

可在「其他设置 → 修改快捷键」中修改工具切换、镜像、编辑物件、缩放、播放、
时间轴步进、上下翻页、演示视图、最大化与全屏；固定快捷键只显示，不能修改。
"""

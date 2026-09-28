"""编辑器主程序：窗口、工具栏、画布、播放循环与全部命令。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional, Tuple

from . import config
from .audio import AudioPlayer, audio_backend_available, audio_backend_name
from .io.chart_io import (
    chart_audio_path,
    copy_audio_into_chart_dir,
    load_chart,
    save_chart as write_chart,
)
from .model.chart import Chart, EditorSettings
from .model.document import Document
from .model.notes import BoundaryCurve, MouseRegion, Note
from .model.timing import TimingMap
from .ui.dialogs import (
    BpmDialog,
    HelpDialog,
    MetadataDialog,
    NoteEditDialog,
    SettingsDialog,
    SpeedGroupDialog,
    run_dialog,
)
from .ui.interactions import ToolController
from .ui.renderer import PendingPreview, RenderState, Renderer
from .ui.scene import Scene
from .ui.style import apply_theme
from .ui.theme import DARK
from .ui.toolbar import Toolbar
from .ui.view import ViewState

CTRL_MASK = 0x0004
SHIFT_MASK = 0x0001


class EditorApp:
    def __init__(self, chart_path: Optional[str] = None, demo: bool = False,
                 width: int = 1360, height: int = 860,
                 maximized: bool = True, fullscreen: bool = False) -> None:
        self.theme = DARK
        self.maximized = bool(maximized)
        self.fullscreen = bool(fullscreen)
        self.root = tk.Tk()
        self.root.title(f"{config.APP_TITLE} {config.VERSION}")
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(1000, 640)
        self.root.configure(background=self.theme.bg)
        apply_theme(self.root, self.theme)

        self.document = Document()
        self.view = ViewState(self.document.chart)
        self.render_state = RenderState()
        self.audio = AudioPlayer()
        self.demo_mode = False
        self._last_message = ""
        self._last_info = ""
        self._last_title = ""
        self._pending_frames = 0
        self._closed = False
        self._tick_id: Optional[str] = None

        self.scene = Scene(tk.Canvas(self.root, background=self.theme.play_bg, highlightthickness=0))
        self.renderer = Renderer(self.scene, self.theme)
        self.controller = ToolController(
            self.document, self.view, self.render_state, self._region_editor, self.status
        )

        self._build_layout()
        self._bind_events()
        self.status(f"欢迎使用 {config.APP_SUBTITLE}")
        self.status(
            f"音频后端：{audio_backend_name()}"
            + ("" if audio_backend_available() else "（未检测到 pygame，播放为静音时钟，编辑不受影响）")
        )

        if chart_path:
            self.load_chart_file(chart_path)
        if demo:
            self.set_demo_mode(True)
        if self.fullscreen:
            self.set_fullscreen(True)
        elif self.maximized:
            self.maximize_window()
        self._sync_ui()
        self._tick_id = self.root.after(16, self._tick)
        self.root.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------------ 布局
    def _build_layout(self) -> None:
        self.toolbar = Toolbar(self.root, self, self.theme)
        self.toolbar.pack(side="top", fill="x")
        self.canvas = self.scene.canvas
        self.canvas.pack(side="top", fill="both", expand=True)
        bar = ttk.Frame(self.root, padding=(8, 4))
        bar.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="")
        self.info_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        ttk.Label(bar, textvariable=self.info_var, style="Dim.TLabel").pack(side="right")
        self.status_bar = bar

    def _bind_events(self) -> None:
        root = self.root
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion_drag)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._wheel_notches(1, e))
        self.canvas.bind("<Button-5>", lambda e: self._wheel_notches(-1, e))
        self.canvas.focus_set()

        root.bind("<space>", lambda e: self._shortcut(self.toggle_play))
        root.bind("<Escape>", lambda e: self._on_escape())
        root.bind("<Tab>", lambda e: self.toggle_demo())
        root.bind("<Left>", lambda e: self._scroll_steps(-1))
        root.bind("<Right>", lambda e: self._scroll_steps(1))
        root.bind("<Prior>", lambda e: self._scroll_page(1))
        root.bind("<Next>", lambda e: self._scroll_page(-1))
        root.bind("<Home>", lambda e: self.jump(0.0))
        root.bind("<End>", lambda e: self.jump(self.document.chart.beat_bounds()[1]))
        root.bind("<plus>", lambda e: self.zoom(1 / config.ZOOM_STEP))
        root.bind("<equal>", lambda e: self.zoom(1 / config.ZOOM_STEP))
        root.bind("<minus>", lambda e: self.zoom(config.ZOOM_STEP))
        root.bind("<Delete>", lambda e: self.delete_selection())
        root.bind("<BackSpace>", lambda e: self.delete_selection())
        root.bind("<Control-n>", lambda e: self.new_chart())
        root.bind("<Control-o>", lambda e: self.open_chart())
        root.bind("<Control-s>", lambda e: self.save_chart(False))
        root.bind("<Control-Shift-S>", lambda e: self.save_chart(True))
        root.bind("<Control-z>", lambda e: self.undo())
        root.bind("<Control-y>", lambda e: self.redo())
        root.bind("<Control-Shift-Z>", lambda e: self.redo())
        root.bind("<Control-c>", lambda e: self.copy_selection())
        root.bind("<Control-v>", lambda e: self.begin_paste())
        root.bind("<Control-a>", lambda e: self.select_all())
        root.bind("<Control-m>", lambda e: self.mirror_selection())
        root.bind("<Control-e>", lambda e: self.edit_selected())
        root.bind("<Control-g>", lambda e: self.edit_settings())
        root.bind("<F11>", lambda e: self.toggle_fullscreen())
        root.bind("<F10>", lambda e: self.toggle_maximize())
        root.bind("<KeyPress>", lambda e: self.toolbar.mark_interaction(), add="+")
        root.bind("<ButtonRelease-1>", lambda e: self.toolbar.mark_menu_closed(), add="+")
        root.bind("m", lambda e: self._shortcut(self.mirror_selection))
        root.bind("e", lambda e: self._shortcut(self.edit_selected))
        root.bind("g", lambda e: self._shortcut(self.toggle_horizontal_grid))
        for index, tool in enumerate(config.TOOL_ORDER, start=1):
            root.bind(str(index), lambda e, t=tool: self._shortcut(lambda: self.set_tool(t)))
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _canvas_focused(self) -> bool:
        """只在画布拥有焦点时响应单键快捷键，避免干扰工具栏控件。"""
        try:
            return self.root.focus_get() is self.canvas
        except (KeyError, tk.TclError):  # pragma: no cover
            return True

    def _shortcut(self, func) -> None:
        if self._canvas_focused():
            func()

    # ------------------------------------------------------------------ 循环
    def run(self) -> None:
        self.root.mainloop()

    def _tick(self) -> None:
        if self._closed:
            return
        self._advance_playback()
        # 二级菜单展开时暂停画布重绘：否则画布每帧重画会把弹出的菜单"闪"掉
        if self.toolbar.is_menu_posted():
            self._tick_id = self.root.after(16, self._tick)
            return
        state = self.render_state
        state.selection = set(self.document.selection)
        state.demo = self.demo_mode
        state.playing = self.audio.is_playing
        state.pending = self.controller.state.pending
        self.renderer.draw(self.document.chart, self.view, state)
        self._sync_ui()
        self._tick_id = self.root.after(16, self._tick)

    def _on_destroy(self, event=None) -> None:
        """窗口销毁时取消待执行的重绘回调，避免 Tk 报 invalid command name。"""
        if event is not None and getattr(event, "widget", None) is not self.root:
            return
        self._closed = True
        if self._tick_id is not None:
            try:
                self.root.after_cancel(self._tick_id)
            except (tk.TclError, ValueError):  # pragma: no cover
                pass
            self._tick_id = None

    def _advance_playback(self) -> None:
        if not self.audio.is_playing:
            return
        timing = self.document.chart.timing
        song_ms = self.audio.heard_position_ms()
        beat = timing.song_ms_to_beat(song_ms)
        self.view.current_beat = beat
        _, high = self.document.chart.beat_bounds()
        backend = getattr(self.audio, "backend", None)
        ended = backend is not None and not getattr(backend, "is_playing", True)
        if ended or beat > high + 8.0:
            self.pause()
            self.status("播放结束")

    def _sync_ui(self) -> None:
        self.toolbar.sync()
        counts = self.document.chart.note_counts()
        layout_name = "分栏（6K / 鼠标）" if self.view.split_layout else "叠加（演示）"
        info = (
            f"布局：{layout_name}"
            f"　|　物件：6K {counts['lane']} · 区域 {counts['region']} · 滑键 {counts['slide']}"
            f"　|　判定线：第 {self.view.current_beat:.3f} 拍"
            f"　|　{self.controller.selection_summary()}"
            f"　|　{self.audio.status_text}"
        )
        if info != self._last_info:
            self.info_var.set(info)
            self._last_info = info
        # 窗口标题每帧重设会让 Windows 反复重画非客户区（全屏时会让菜单抖动）
        title = f"{self.document.title} — {config.APP_TITLE}"
        if title != self._last_title:
            self.root.title(title)
            self._last_title = title

    # ------------------------------------------------------------------ 状态
    def status(self, text: str) -> None:
        self._last_message = text
        if hasattr(self, "status_var") and self.status_var.get() != text:
            self.status_var.set(text)

    def _hover_status(self, x: float, y: float) -> None:
        if y >= self.view.layout.play_bottom:
            self.status("总览条：点击可跳转到对应时间")
            return
        layout = self.view.layout
        beat = self.view.y_to_beat(y)
        lane = layout.lane_at(x)
        if lane is not None:
            where = f"6K 轨道 {lane + 1}"
        elif layout.in_mouse_pane(x):
            where = f"鼠标平面 · 横向 {self.view.x_to_norm(x):+.3f}"
        else:
            where = "区域外"
        self.status(f"光标：{where} · 第 {beat:.3f} 拍（吸附 {self.view.snap_beat(beat):g}）· {self.view.time_label(beat)}")

    # ------------------------------------------------------------------ 事件
    def _on_configure(self, event) -> None:
        self.view.resize(event.width, event.height)

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        self.toolbar.mark_interaction()
        ctrl = bool(event.state & CTRL_MASK)
        shift = bool(event.state & SHIFT_MASK)
        if self.demo_mode:
            if event.y >= self.view.layout.play_bottom:
                self.controller.on_press(event.x, event.y, ctrl, shift)
            return
        self.controller.on_press(event.x, event.y, ctrl, shift)

    def _on_motion(self, event) -> None:
        self.toolbar.mark_interaction()
        self.controller.on_motion(event.x, event.y)
        self._hover_status(event.x, event.y)

    def _on_motion_drag(self, event) -> None:
        self.toolbar.mark_interaction()
        self.controller.on_motion(event.x, event.y)

    def _on_release(self, event) -> None:
        self.controller.on_release(event.x, event.y, bool(event.state & CTRL_MASK))

    def _wheel_notches(self, notches: float, event) -> None:
        self.toolbar.mark_interaction()
        ctrl = bool(event.state & CTRL_MASK)
        shift = bool(event.state & SHIFT_MASK)
        self.controller.on_wheel(float(notches), ctrl, shift)

    def _on_wheel(self, event) -> None:
        delta = event.delta
        notches = delta / 120.0 if abs(delta) >= 120 else float(delta)
        self._wheel_notches(notches, event)

    def _on_escape(self) -> None:
        if self.controller.has_pending():
            self.controller.cancel_pending()
            self.status("已取消当前操作")
        elif self.demo_mode:
            self.set_demo_mode(False)

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self.fullscreen)
        if self.fullscreen:
            self.maximized = False
        self.status(
            "已进入全屏（F11 退出）" if self.fullscreen else "已退出全屏"
        )

    def set_fullscreen(self, enabled: bool) -> None:
        self.fullscreen = bool(enabled)
        try:
            self.root.attributes("-fullscreen", self.fullscreen)
        except tk.TclError:  # pragma: no cover
            pass

    def maximize_window(self) -> bool:
        """把窗口最大化（默认启动状态：保留标题栏与任务栏）。"""
        root = self.root
        try:
            root.state("zoomed")           # Windows
        except tk.TclError:
            try:
                root.attributes("-zoomed", True)   # X11
            except tk.TclError:
                try:
                    width = root.winfo_screenwidth()
                    height = root.winfo_screenheight()
                    root.geometry(f"{width}x{height}+0+0")
                except tk.TclError:        # pragma: no cover
                    return False
        self.maximized = True
        return True

    def restore_window(self) -> None:
        root = self.root
        try:
            root.state("normal")
        except tk.TclError:  # pragma: no cover
            try:
                root.attributes("-zoomed", False)
            except tk.TclError:
                pass
        self.maximized = False

    def toggle_maximize(self) -> None:
        if self.fullscreen:
            self.set_fullscreen(False)
        if self.maximized:
            self.restore_window()
            self.status("窗口已还原（F10 可再次最大化）")
        else:
            self.maximize_window()
            self.status("窗口已最大化（F10 可还原，F11 为无边框全屏）")

    def reset_zoom(self) -> None:
        self.document.chart.settings.zoom_seconds = config.DEFAULT_ZOOM_SECONDS
        self.status(f"缩放已重置为 {config.DEFAULT_ZOOM_SECONDS:g} 秒")

    def cancel_pending(self) -> None:
        self.controller.cancel_pending()
        self.status("已取消当前放置流程")

    def clear_selection(self) -> None:
        self.document.clear_selection()
        self.status("已清空选择")

    def _scroll_steps(self, direction: int) -> None:
        self.view.scroll_beats(direction * self.view.grid_step())
        self.status(f"判定线：第 {self.view.current_beat:.3f} 拍")

    def _scroll_page(self, direction: int) -> None:
        self.view.scroll_beats(direction * self.view.visible_beats() * 0.5)
        self.status(f"判定线：第 {self.view.current_beat:.3f} 拍")

    def jump(self, beat: float) -> None:
        self.view.set_beat(beat)
        self.status(f"跳转到第 {self.view.current_beat:.3f} 拍")

    # ------------------------------------------------------------------ 文件
    def new_chart(self) -> None:
        if not self._confirm_discard():
            return
        self.document.set_chart(Chart())
        self.view.set_chart(self.document.chart)
        self.view.set_beat(0.0)
        self.audio.load(None)
        self.controller.cancel_pending()
        self.status("已新建谱面")

    def open_chart(self) -> None:
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="打开谱面",
            filetypes=[("In Verus 谱面", "*.ivchart"), ("JSON", "*.json"), ("全部文件", "*.*")],
        )
        if not path:
            return
        self.load_chart_file(path)

    def load_chart_file(self, path: str) -> None:
        try:
            chart, _ = load_chart(path)
        except Exception as exc:  # pragma: no cover - 依赖外部文件
            messagebox.showerror("打开失败", f"无法读取谱面：\n{exc}", parent=self.root)
            return
        self.document.set_chart(chart, path)
        self.view.set_chart(chart)
        self.view.set_beat(0.0)
        self.controller.cancel_pending()
        self._load_audio()
        self.status(f"已打开谱面：{os.path.basename(path)}")

    def save_chart(self, save_as: bool = False) -> bool:
        path = self.document.path
        if save_as or not path:
            path = filedialog.asksaveasfilename(
                title="保存谱面",
                defaultextension=config.CHART_EXTENSIONS[0],
                filetypes=[("In Verus 谱面", "*.ivchart"), ("JSON", "*.json")],
                initialfile=(os.path.basename(self.document.path) if self.document.path else "chart.ivchart"),
            )
            if not path:
                return False
        try:
            written = write_chart(self.document.chart, path)
        except Exception as exc:  # pragma: no cover - 依赖外部文件
            messagebox.showerror("保存失败", f"无法写入谱面：\n{exc}", parent=self.root)
            return False
        self.document.path = written
        self.document.dirty = False
        self.status(f"已保存：{written}")
        self._load_audio()
        return True

    def _confirm_discard(self) -> bool:
        if not self.document.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "未保存的修改", "当前谱面有未保存的修改，是否保存？", parent=self.root
        )
        if answer is None:
            return False
        if answer:
            return self.save_chart(False)
        return True

    def on_close(self) -> None:
        if self._confirm_discard():
            self.root.destroy()

    # ------------------------------------------------------------------ 音频
    def _load_audio(self) -> None:
        path = chart_audio_path(self.document.path, self.document.chart)
        ok = self.audio.load(path)
        self.audio.latency_ms = self.document.chart.settings.audio_latency_ms
        if path is None and self.document.chart.metadata.audio_file:
            self.status(f"未找到音频文件：{self.document.chart.metadata.audio_file}")
        elif path and not ok:
            self.status(f"音频载入失败（后端 {audio_backend_name()}）：{os.path.basename(path)}")

    def toggle_play(self) -> None:
        if self.audio.is_playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        if self.audio.path is None:
            self._load_audio()
        timing = self.document.chart.timing
        self.audio.latency_ms = self.document.chart.settings.audio_latency_ms
        song_ms = timing.beat_to_song_ms(self.view.current_beat)
        self.audio.play(song_ms)
        note = "" if self.audio.usable_audio else "（静音播放：未安装 pygame）"
        self.status(f"从第 {self.view.current_beat:.3f} 拍开始播放{note}")

    def pause(self) -> None:
        timing = self.document.chart.timing
        position = self.audio.position_ms()
        heard = position - self.audio.latency_ms if self.audio.usable_audio else position
        beat = self.view.snap_beat(timing.song_ms_to_beat(heard))
        self.audio.pause()
        self.view.set_beat(beat)
        self.audio.seek_ms(timing.beat_to_song_ms(self.view.current_beat))
        self.status(f"已暂停：判定线吸附到第 {self.view.current_beat:.3f} 拍")

    # ------------------------------------------------------------------ 面板
    def edit_metadata(self) -> None:
        result = run_dialog(
            MetadataDialog, self.root, self.theme, self.document.chart.metadata, self.document.path
        )
        if not result:
            return
        audio_source = result.pop("audio_source", None)
        metadata = self.document.chart.metadata

        def _apply() -> None:
            for key, value in result.items():
                setattr(metadata, key, value)

        self.document.apply("修改元数据", _apply)
        if audio_source:
            if not self.document.path:
                if messagebox.askyesno(
                    "先保存谱面",
                    "音频需要复制到谱面文件所在文件夹。\n是否先保存谱面？",
                    parent=self.root,
                ):
                    self.save_chart(True)
            try:
                relative = copy_audio_into_chart_dir(audio_source, self.document.path or "")
            except Exception as exc:  # pragma: no cover
                messagebox.showerror("复制音频失败", str(exc), parent=self.root)
            else:
                self.document.chart.metadata.audio_file = relative
                self.document.dirty = True
                self.status(f"已复制音频到：{relative}")
        self._load_audio()

    def edit_timing(self) -> None:
        timing: Optional[TimingMap] = run_dialog(
            BpmDialog, self.root, self.theme, self.document.chart.timing, self._beat_hint()
        )
        if timing is None:
            return
        self.document.apply("修改 BPM/偏移", lambda: setattr(self.document.chart, "timing", timing))
        self.view.set_beat(self.view.current_beat)
        self.status(f"BPM 列表已更新（{len(timing.segments)} 项），偏移 {timing.offset_ms:g} ms")

    def edit_settings(self) -> None:
        result = run_dialog(SettingsDialog, self.root, self.theme, self.document.chart)
        if not result:
            return

        def _apply() -> None:
            settings = self.document.chart.settings
            settings.zoom_seconds = float(result["zoom_seconds"])
            settings.grid_division = int(result["grid_division"])
            settings.horizontal_divisions = int(result["horizontal_divisions"])
            settings.audio_latency_ms = float(result["audio_latency_ms"])
            settings.normalize()

        self.document.apply("修改编辑器设置", _apply)
        self.audio.latency_ms = self.document.chart.settings.audio_latency_ms
        self.view.set_beat(self.view.current_beat)
        self.status(
            f"设置已更新：可视 {self.document.chart.settings.zoom_seconds:g} 秒 · "
            f"网格 1/{self.document.chart.settings.grid_division} 拍 · "
            f"横向 {self.document.chart.settings.horizontal_divisions} 等分 · "
            f"延迟 {self.document.chart.settings.audio_latency_ms:g} ms"
        )

    def edit_speed_groups(self) -> None:
        changed = run_dialog(
            SpeedGroupDialog, self.root, self.theme, self.document, self._beat_hint()
        )
        if changed:
            self.status("速度组已更新")

    def _beat_hint(self) -> str:
        """给数据面板用的提示：当前判定线在第几拍（便于照着填数据）。"""
        timing = self.document.chart.timing
        beat = self.view.current_beat
        bar, in_bar = timing.bar_index_at(beat)
        return (
            f"当前判定线：第 {beat:.3f} 拍"
            f"（小节 {bar + 1} 第 {in_bar + 1:.2f} 拍 · {self.view.time_label(beat)}）"
        )

    def show_guide(self) -> None:
        """操作指南（与快捷键拆成两个独立窗口）。"""
        from .ui.dialogs import GUIDE_TEXT

        run_dialog(HelpDialog, self.root, self.theme, "操作指南", GUIDE_TEXT)

    def show_shortcuts(self) -> None:
        from .ui.dialogs import SHORTCUTS_TEXT

        run_dialog(HelpDialog, self.root, self.theme, "快捷键", SHORTCUTS_TEXT)

    def _region_editor(
        self,
        beat_start: float,
        beat_end: float,
        left: Tuple[float, float],
        right: Tuple[float, float],
    ) -> Optional[Dict]:
        """空域：时间与左右边界首尾位置点完后，用对话框确认线型（也可以微调位置）。"""
        template = MouseRegion(
            "temp",
            beat_start,
            beat_end,
            BoundaryCurve(float(left[0]), float(left[1])),
            BoundaryCurve(float(right[0]), float(right[1])),
        )
        return run_dialog(NoteEditDialog, self.root, self.theme, template, self._beat_hint())

    def edit_selected(self) -> None:
        note = self.document.single_selected()
        if note is None:
            self.status("编辑功能需要只选择 1 个物件")
            return
        data = run_dialog(NoteEditDialog, self.root, self.theme, note, self._beat_hint())
        if not data:
            return
        self.document.replace_note(note.id, data)
        self.status(f"已更新 {note.id}")

    # ------------------------------------------------------------------ 编辑命令
    def set_tool(self, tool: str) -> None:
        self.controller.set_tool(tool)

    def copy_selection(self) -> None:
        count = self.document.copy_selection()
        self.status(f"已复制 {count} 个物件" if count else "未选择任何物件")

    def begin_paste(self) -> None:
        self.controller.begin_paste()

    def mirror_selection(self) -> None:
        if not self.document.selection:
            self.status("请先选择要镜像的物件")
            return
        count = self.document.mirror_notes(self.document.selection)
        self.status(f"已镜像 {count} 个物件")

    def delete_selection(self) -> None:
        if not self.document.selection:
            return
        count = len(self.document.selection)
        self.document.delete_notes(list(self.document.selection))
        self.status(f"已删除 {count} 个物件")

    def select_all(self) -> None:
        self.document.select_all()
        self.status(f"已选择 {len(self.document.selection)} 个物件")

    def undo(self) -> None:
        label = self.document.undo_label()
        if self.document.undo():
            self.status(f"已撤销：{label}")

    def redo(self) -> None:
        label = self.document.redo_label()
        if self.document.redo():
            self.status(f"已重做：{label}")

    def zoom(self, factor: float) -> None:
        self.view.zoom_by(factor)
        self.status(f"缩放：可视 {self.view.zoom_seconds:.3g} 秒 ≈ {self.view.visible_beats():.2f} 拍")

    def set_grid_division(self, text: str) -> None:
        try:
            value = int(str(text))
        except ValueError:
            return
        settings = self.document.chart.settings
        settings.grid_division = max(config.GRID_DIVISION_MIN, min(config.GRID_DIVISION_MAX, value))
        self.document.dirty = True
        self.view.set_beat(self.view.current_beat)
        self.status(f"时间网格：一拍细分为 {settings.grid_division} 份")

    def set_horizontal_divisions(self, text: str) -> None:
        text = str(text).strip()
        value = 0 if text in ("", "关") else int(float(text))
        settings = self.document.chart.settings
        settings.horizontal_divisions = max(0, min(config.HORIZONTAL_DIVISION_MAX, value))
        self.document.dirty = True
        self.status(
            "横向辅助网格已关闭"
            if settings.horizontal_divisions == 0
            else f"横向辅助网格：0..1 合法区域 {settings.horizontal_divisions} 等分（滑键吸附到网格线）"
        )

    def toggle_horizontal_grid(self) -> None:
        self.view.show_horizontal_grid = not self.view.show_horizontal_grid
        self.status("显示横向辅助网格" if self.view.show_horizontal_grid else "隐藏横向辅助网格")

    # ------------------------------------------------------------------ 演示
    def set_demo_mode(self, enabled: bool) -> None:
        if enabled == self.demo_mode:
            return
        self.demo_mode = enabled
        self.render_state.demo = enabled
        # 演示模式：鼠标平面叠加到 6K 的中间四条轨道；编辑器：左右两个独立区域
        self.view.set_layout_mode(config.LAYOUT_OVERLAY if enabled else config.LAYOUT_SPLIT)
        self.controller.cancel_pending()
        if enabled:
            self.document.clear_selection()
            self.toolbar.pack_forget()
            self.status_bar.pack_forget()
            self.render_state.message = "演示模式：Tab 或 Esc 返回编辑 · 滚轮滚动 · Ctrl+滚轮缩放 · 空格播放"
        else:
            self.toolbar.pack(side="top", fill="x")
            self.status_bar.pack(side="bottom", fill="x")
            self.canvas.pack_forget()
            self.canvas.pack(side="top", fill="both", expand=True)
            self.render_state.message = ""
        self.status("进入演示模式" if enabled else "退出演示模式")

    def toggle_demo(self) -> None:
        self.set_demo_mode(not self.demo_mode)


def main(argv: Optional[list] = None) -> int:  # pragma: no cover - GUI 入口
    import argparse

    parser = argparse.ArgumentParser(description=f"{config.APP_TITLE} {config.VERSION}")
    parser.add_argument("chart", nargs="?", help="要打开的谱面文件（.ivchart）")
    parser.add_argument("--demo", action="store_true", help="直接进入演示模式")
    parser.add_argument("--make-demo", action="store_true", help="生成示例谱面后退出")
    parser.add_argument("--windowed", action="store_true", help="以普通窗口启动（默认最大化）")
    parser.add_argument("--fullscreen", action="store_true", help="以无边框全屏启动（F11 同样可切）")
    args = parser.parse_args(argv)

    if args.make_demo:
        from .demo import write_demo_chart

        path = write_demo_chart()
        print(f"示例谱面已生成：{path}")
        return 0

    app = EditorApp(
        args.chart,
        demo=args.demo,
        maximized=not (args.windowed or args.fullscreen),
        fullscreen=args.fullscreen,
    )
    app.run()
    return 0

"""编辑器主程序：窗口、工具栏、画布、播放循环与全部命令。"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, Optional, Tuple

import config
from audio import (
    AudioPlayer,
    audio_backend_name,
    audio_setup_hint,
    audio_silent_reason,
    write_startup_log,
)
from chart_io import (
    chart_audio_path,
    copy_audio_into_chart_dir,
    load_chart,
    save_chart as write_chart,
)
from model.chart import Chart
from model.document import Document
from model.notes import BoundaryCurve, ForbiddenRegion, MouseRegion
from model.timing import TimingMap
from model.validate import format_report, validate_chart
from global_settings import (
    adopt_legacy_settings,
    load_settings,
    save_settings,
)
from metronome import LABELS as METRONOME_LABELS
from metronome import MODE_BAR, MODE_OFF, effect_events, effect_samples
from ui.dialogs import (
    BpmDialog,
    DemoViewDialog,
    HelpDialog,
    MetadataDialog,
    NoteEditDialog,
    SaveChangesDialog,
    SettingsDialog,
    SpeedGroupDialog,
    TextInputDialog,
    VolumeDialog,
    run_dialog,
)
from ui.interactions import ToolController
from ui.renderer import RenderState, Renderer
from ui.scene import Scene
from ui.style import apply_theme
from ui.theme import DARK
from ui.toolbar import Toolbar
from ui.view import ViewState

CTRL_MASK = 0x0004
SHIFT_MASK = 0x0001


def should_stop_playback(
    now_seconds: float,
    chart_end_seconds: float,
    music_end_seconds: Optional[float],
    margin: float = config.PLAY_END_MARGIN_SECONDS,
) -> bool:
    """自动停止判据：**音乐与谱面都播完 ``margin`` 秒** 才停。

    * ``music_end_seconds`` 是音乐播完时的播放位置（秒；还没有音乐结束时为 ``None``）；
      ``None`` 表示音乐永远不会结束 —— 例如没有载入歌曲时的静音时钟，
      这时不会自动停止，可以一直放着当“节拍器”用。
    * 谱面结束时刻取最后一个物件的判定时刻（空谱面取第 1 拍）换算成的秒数。
    * 两者都超出 ``margin`` 秒才真正停下，所以短的那一边不会提前把播放掐掉。
    """
    if music_end_seconds is None:
        return False
    return (
        now_seconds > music_end_seconds + margin
        and now_seconds > chart_end_seconds + margin
    )


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
        # 视图 / 音效 / 编辑器设置是**全局**的：不随谱面文件走
        self.editor_settings = load_settings()
        self.document.chart.settings = self.editor_settings
        self._settings_snapshot = self.editor_settings.to_dict()
        self.view = ViewState(self.document.chart)
        self.render_state = RenderState()
        self.audio = AudioPlayer()
        # 音效（节拍音 / 打击音）走“预先算好时刻 + 混进音频输出”的路径
        self.audio.enable_effects(effect_samples)
        # 时刻表两半各自缓存（键相同就不重算）：节拍音只看时间轴 / 模式，
        # 打击音还要看谱面内容（Document.revision）
        self._effects_clicks: list = []
        self._effects_hits: list = []
        self._effects_merged: list = []
        self._effects_clicks_key = None
        self._effects_hits_key = None
        self._effects_scope_key = None
        self._effects_bucket = 0
        self.demo_mode = False
        self._last_message = ""
        self._last_info = ""
        self._last_title = ""
        # 脏帧判断：画面指纹 + 上次重建选择集的版本号
        self._last_render_key: Optional[tuple] = None
        self._selection_revision_seen = -1
        self._pending_frames = 0
        self._closed = False
        self._tick_id: Optional[str] = None
        # 音乐播完时的播放位置（秒；None = 还没播完 / 没有音乐）
        self._music_end_seconds: Optional[float] = None
        # 拖动分界线：正在拖动 + 当前光标样式
        self._divider_drag = False
        self._divider_cursor = ""
        self.hit_volume = 1.0
        # 演示模式：画布上贴的是演示视图软件渲染的离屏帧（见 preview/softview.py）
        self._demo_view = None                 # 离屏软渲染视图（懒创建）
        self._demo_photo: Optional[tk.PhotoImage] = None
        self._demo_image_id: Optional[int] = None
        self._demo_hint_id: Optional[int] = None
        self._demo_size: Tuple[int, int] = (0, 0)
        self._demo_dirty = True
        self._demo_next_frame = 0.0

        # 画布区容器（右侧放竖向滚动条），画布是它的子控件
        self.body = ttk.Frame(self.root)
        self.canvas = tk.Canvas(self.body, background=self.theme.play_bg, highlightthickness=0)
        self.scene = Scene(self.canvas)
        self.renderer = Renderer(self.scene, self.theme)
        self.controller = ToolController(
            self.document, self.view, self.render_state, self._region_editor, self.status
        )

        self._build_layout()
        self._bind_events()
        self.status(f"欢迎使用 {config.APP_SUBTITLE}")
        hint = audio_setup_hint()
        if hint:
            # 明确告诉用户是哪个解释器缺依赖、怎么修（启动器一般已经自动切到 .venv）
            self.status(f"音频后端：{audio_backend_name()} · {hint}")
        else:
            self.status(f"音频后端：{audio_backend_name()}")
        self.status(f"解释器：{sys.executable}")
        write_startup_log()
        self._apply_volumes()          # 音乐 / 节拍音 / 打击音 音量按谱面设置生效

        if chart_path:
            self.load_chart_file(chart_path)
        if demo:
            self.set_demo_mode(True)
        if self.fullscreen:
            self.set_fullscreen(True)
        elif self.maximized:
            self.maximize_window()
        self._sync_ui()
        self._schedule_next_tick()
        self.root.bind("<Destroy>", self._on_destroy)

    # ------------------------------------------------------------------ 布局
    def _build_layout(self) -> None:
        self.toolbar = Toolbar(self.root, self, self.theme)
        self.toolbar.pack(side="top", fill="x")
        # 画布区：右侧是一条朴素的竖向滚动条
        self.vscroll = ttk.Scrollbar(
            self.body,
            orient="vertical",
            command=self._on_scroll,
            style="White.Vertical.TScrollbar",
        )
        self.vscroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self._scroll_key: Optional[Tuple[float, float]] = None
        bar = ttk.Frame(self.root, padding=(8, 4))
        bar.pack(side="bottom", fill="x")
        self.status_var = tk.StringVar(value="")
        self.info_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        ttk.Label(bar, textvariable=self.info_var, style="Dim.TLabel").pack(side="right")
        self.status_bar = bar
        # 打包顺序：工具栏(上) → 状态栏(下) → 画布区(占满中间)
        self.body.pack(side="top", fill="both", expand=True)

    def _bind_events(self) -> None:
        root = self.root
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
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
        root.bind("<Control-x>", lambda e: self.cut_selection())
        root.bind("<Control-v>", lambda e: self.begin_paste())
        root.bind("<Control-a>", lambda e: self.select_all())
        root.bind("<Control-m>", lambda e: self.mirror_selection())
        root.bind("<Control-e>", lambda e: self.edit_selected())
        root.bind("<Control-g>", lambda e: self.edit_settings())
        root.bind("<F11>", lambda e: self.toggle_fullscreen())
        root.bind("<F10>", lambda e: self.toggle_maximize())
        root.bind("<KeyPress>", lambda e: self.toolbar.mark_interaction(), add="+")
        # 点击菜单 / 分组按钮以外的地方 -> 自动收起菜单
        root.bind("<ButtonPress-1>", lambda e: self.toolbar.handle_outside_click(e.widget), add="+")
        root.bind("m", lambda e: self._shortcut(self.mirror_selection))
        root.bind("e", lambda e: self._shortcut(self.edit_selected))
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
        self._refresh_effects()         # 谱面 / 音效设置变化时重算时刻表
        self._advance_playback()
        self.audio.poll()          # 变速后端：提前准备后续音频窗口
        # 二级菜单展开时暂停画布重绘：否则画布每帧重画会把弹出的菜单"闪"掉
        if self.toolbar.is_menu_posted():
            self._schedule_next_tick()
            return
        if self.demo_mode and self._draw_demo_frame():
            self._sync_ui()
            self._maybe_save_settings()
            self._schedule_next_tick()
            return
        state = self.render_state
        state.demo = self.demo_mode
        state.playing = self.audio.is_playing
        state.divider_active = self._divider_drag or self._divider_cursor != ""
        state.pending = self.controller.state.pending
        # 选择集只在版本号变化时重建（大选择集不必每帧复制一遍）
        selection_revision = self.document.selection_revision
        if selection_revision != self._selection_revision_seen:
            state.selection = set(self.document.selection)
            self._selection_revision_seen = selection_revision
        # 画面指纹不变时跳过整帧重绘：空闲状态几乎零开销
        render_key = self._render_key()
        if render_key != self._last_render_key:
            self._last_render_key = render_key
            self.renderer.draw(self.document.chart, self.view, state)
        self._sync_ui()
        self._maybe_save_settings()
        self._schedule_next_tick()

    def _render_key(self) -> tuple:
        """本帧画面是否变化的指纹（只比较廉价的键，不复制选择集 / 剪贴板）。"""
        state = self.render_state
        layout = self.view.layout
        settings = self.document.chart.settings
        pending = state.pending
        return (
            self.document.revision,
            self.document.selection_revision,
            self.document.chart.revision,
            self.document.chart.timing.revision,
            settings.zoom_seconds,
            settings.grid_division,
            settings.horizontal_divisions,
            bool(settings.side_lane_highlight),
            bool(settings.show_past_notes),
            round(float(self.view.current_beat), 6),
            int(layout.width),
            int(layout.height),
            layout.mode,
            round(float(layout.divider_x), 3),
            bool(state.demo),
            bool(state.playing),
            bool(state.divider_active),
            state.message,
            state.rubber_band,
            state.hover,
            pending.tool,
            pending.step,
            pending.lane_start,
            pending.lane_end,
            pending.beat_start,
            pending.beat_end,
            pending.x_start,
            pending.x_end,
            pending.left.to_dict() if pending.left is not None else None,
            pending.right.to_dict() if pending.right is not None else None,
            pending.hint,
            id(pending.clipboard),
            len(pending.clipboard),
            pending.clipboard_beat,
        )

    def _maybe_save_settings(self) -> None:
        """设置一变就把全局配置写回磁盘（视图 / 音效 / 编辑器设置都是全局的）。

        用快照比较，避免每帧都写文件；撤销 / 重做改了设置也会被写到。
        """
        data = self.editor_settings.to_dict()
        if data != self._settings_snapshot:
            self._settings_snapshot = data
            save_settings(self.editor_settings)

    def _schedule_next_tick(self) -> None:
        """重新安排下一帧（保证同一时刻只有一个待执行回调，退出时不会残留）。"""
        if self._closed:
            return
        if self._tick_id is not None:
            try:
                self.root.after_cancel(self._tick_id)
            except (tk.TclError, ValueError):  # pragma: no cover
                pass
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
        backend = getattr(self.audio, "backend", None)
        if backend is not None and not getattr(backend, "is_playing", True):
            # 音乐刚播完：记下此刻的播放位置（秒），之后还要再走 margin 秒才考虑停止
            if self._music_end_seconds is None:
                self._music_end_seconds = song_ms / 1000.0
        _, chart_end_beat = self.document.chart.beat_bounds()
        chart_end_seconds = timing.beat_to_song_ms(chart_end_beat) / 1000.0
        if should_stop_playback(
            song_ms / 1000.0, chart_end_seconds, self._music_end_seconds
        ):
            self.pause()
            self.status(
                f"播放结束（音乐与谱面都已播完 {config.PLAY_END_MARGIN_SECONDS:g} 秒）"
            )

    def _refresh_effects(
        self, refilter: bool = False, from_ms: Optional[float] = None
    ) -> None:
        """把节拍音 / 打击音的**播放时刻预先算好**交给音频层混进输出。

        **增量维护**（不做全量重算）：

        * 节拍音只由 BPM / 每小节拍数 / 偏移 / 模式 / 覆盖范围决定；
        * 打击音由物件（与时间轴）决定，用 ``Document.revision`` 判断有没有改过；
        * 编辑物件只重算打击音，改音量完全不重算（只更新混音音量）；
        * **倍速不参与时刻表** —— 时刻表永远是歌曲毫秒，倍速只在混音时决定
          音效落在输出的哪个位置（``输出位置 = (时刻 − 窗口起点) / 倍速``），
          所以调速 / 慢放不需要重建时刻表。

        覆盖范围按 30 秒取整，小幅编辑不会改变范围、缓存可以复用。

        每帧都会调用：**没有变化时只比较几个键**；``beat_bounds()``（要遍历
        全部物件）也只在谱面版本 / 音频长度变化时才重算。

        ``from_ms`` 指定“从哪个歌曲毫秒起播”：裁剪过去的条目必须按**新的
        起播位置**算。默认用当前音频位置；起播时要显式传入目标位置，否则
        暂停后往回滚再播放会把中间那一段的音效一起裁掉（打击音不响）。
        """
        chart = self.document.chart
        settings = chart.settings
        timing = chart.timing
        length_ms = self.audio.length_ms
        scope_key = (self.document.revision, int(length_ms // 30000.0))
        if scope_key != self._effects_scope_key:
            _, end_beat = chart.beat_bounds()
            end_ms = max(length_ms, timing.beat_to_song_ms(end_beat) + 4000.0)
            self._effects_bucket = int(end_ms // 30000.0)
            self._effects_scope_key = scope_key
        bucket = self._effects_bucket
        until_ms = (bucket + 1) * 30000.0
        base_key = (
            timing.offset_ms,
            tuple((seg.start_beat, seg.bpm, seg.beats_per_bar) for seg in timing.segments),
            bucket,
        )
        clicks_key = (settings.metronome, base_key)
        hits_key = (self.document.revision, bool(settings.hit_enabled), base_key)
        changed = False
        if clicks_key != self._effects_clicks_key:
            self._effects_clicks = effect_events(chart, settings.metronome, False, until_ms)
            self._effects_clicks_key = clicks_key
            changed = True
        if hits_key != self._effects_hits_key:
            self._effects_hits = effect_events(chart, MODE_OFF, bool(settings.hit_enabled), until_ms)
            self._effects_hits_key = hits_key
            changed = True
        if changed:
            # 两个已排序列表的合并（Timsort 走 C 层，比重新逐条计算快得多）
            self._effects_merged = sorted(self._effects_clicks + self._effects_hits)
        if not changed and not refilter:
            return
        start_ms = self.audio.position_ms() if from_ms is None else float(from_ms)
        self.audio.set_effects(
            [item for item in self._effects_merged if item[0] > start_ms + 1e-6]
        )

    def set_metronome(self, mode: str) -> None:
        """视图菜单：关闭 / 小节音 / 节拍音。"""
        if mode not in METRONOME_LABELS:
            return
        # 编辑器设置与谱面无关：直接改值，不进撤销栈
        self.document.chart.settings.metronome = mode
        self._refresh_effects()                 # 只重算节拍音那一半
        if mode == MODE_OFF:
            self.status("节拍音已关闭")
            return
        detail = "每小节响一声" if mode == MODE_BAR else "每拍响一声"
        if not self.audio.usable_audio:
            self.status(f"节拍音已开启（{detail}），但当前音频设备不可用，暂时听不到声音")
        else:
            self.status(f"节拍音已开启：{detail}（小节音比节拍音高一点）")

    def _sync_ui(self) -> None:
        self.toolbar.sync()
        counts = self.document.chart.note_counts()
        layout_name = "分栏（6K / 鼠标）" if self.view.split_layout else "叠加（演示）"
        info = (
            f"布局：{layout_name}"
            f"　|　倍速 {config.format_rate(self.audio.rate)}"
            f"　|　物件：6K {counts['lane']} · 区域 {counts['region']} · 滑键 {counts['slide']}"
            f" · 禁区 {counts['forbidden']}"
            f"　|　判定线：第 {self.view.current_beat:.3f} 拍"
            f"　|　{self.controller.selection_summary()}"
            f"　|　{self.audio.status_text}"
        )
        if info != self._last_info:
            self.info_var.set(info)
            self._last_info = info
        self._sync_scrollbar()
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
            beat = self.view.y_to_beat(y)
            self.status(
                f"画布下方空白（用右侧滚动条移动时间轴）· 第 {beat:.3f} 拍 · "
                f"{self.view.time_label(beat)}"
            )
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
        self._demo_dirty = True

    # ------------------------------------------------------------ 竖向滚动条
    def _on_scroll(self, *args) -> None:
        """ttk 滚动条回调：拖动滑块 / 点箭头 / 翻页。"""
        if not args:
            return
        if args[0] == "moveto":
            self.view.set_from_fraction(float(args[1]))
            self.status(f"时间轴：第 {self.view.current_beat:.3f} 拍")
        elif args[0] == "scroll":
            # 滚动条方向是反的（往下 = 往回走），所以这里的符号也要反过来
            number = -int(args[1])
            what = args[2] if len(args) > 2 else "units"
            if what == "pages":
                self.view.scroll_beats(number * self.view.visible_beats() * 0.9)
            else:
                self.view.scroll_beats(number * self.view.grid_step())
        self._sync_scrollbar(force=True)

    def _sync_scrollbar(self, force: bool = False) -> None:
        """把当前可视时间窗口映射到滚动条滑块上。"""
        first, last = self.view.scroll_fractions()
        key = (round(first, 4), round(last, 4))
        if force or key != self._scroll_key:
            self.vscroll.set(first, last)
            self._scroll_key = key

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        self.toolbar.mark_interaction()
        ctrl = bool(event.state & CTRL_MASK)
        shift = bool(event.state & SHIFT_MASK)
        # 分界线拖动：优先于放置工具，拖的时候不会误放物件
        if not self.demo_mode and self.view.divider_at(event.x):
            self._divider_drag = True
            self.canvas.configure(cursor="sb_h_double_arrow")
            self.status("拖动分界线：左右两栏宽度实时调整（双击分界线可恢复 1:3）")
            return
        if self.demo_mode:
            if event.y >= self.view.layout.play_bottom:
                self.controller.on_press(event.x, event.y, ctrl, shift)
            return
        self.controller.on_press(event.x, event.y, ctrl, shift)

    def _on_motion(self, event) -> None:
        self.toolbar.mark_interaction()
        if self.demo_mode:
            # 演示视图不画鼠标指针：画布上的鼠标移动不触发重绘
            return
        self.controller.on_motion(event.x, event.y)
        self._hover_status(event.x, event.y)
        self._sync_divider_cursor(event.x)

    def _on_motion_drag(self, event) -> None:
        self.toolbar.mark_interaction()
        if self._divider_drag:
            self._drag_divider(event.x)
            return
        self.controller.on_motion(event.x, event.y)

    def _on_release(self, event) -> None:
        if self._divider_drag:
            self._divider_drag = False
            self._drag_divider(event.x)
            self._sync_divider_cursor(event.x)
            ratio = self.view.layout.split_ratio
            self.status(f"分栏宽度已调整：6K {ratio * 100:.0f}% / 鼠标 {(1 - ratio) * 100:.0f}%")
            return
        self.controller.on_release(event.x, event.y, bool(event.state & CTRL_MASK))

    # ---------------------------------------------------------- 分界线拖动
    def _request_redraw(self) -> None:
        """马上安排下一帧（拖动分界线等交互后画面立即跟上）。"""
        if not self._closed:
            self._schedule_next_tick()

    def _on_double_click(self, event) -> None:
        """双击分界线：恢复默认 1:3 分栏。"""
        if self.demo_mode or not self.view.divider_at(event.x):
            return
        self.reset_split_ratio()

    def _sync_divider_cursor(self, x: float) -> None:
        """悬停在分界线上时换成左右拖动光标。"""
        if self._divider_drag:
            return
        want = "sb_h_double_arrow" if self.view.divider_at(x) else ""
        if want != self._divider_cursor:
            self._divider_cursor = want
            try:
                self.canvas.configure(cursor=want)
            except tk.TclError:  # pragma: no cover - 主题不支持时忽略
                pass

    def _drag_divider(self, x: float) -> None:
        self.view.set_split_ratio(self.view.layout.ratio_from_divider(x))
        self._sync_ui()
        self._request_redraw()

    def reset_split_ratio(self) -> None:
        """双击分界线（或菜单）恢复默认的 1:3 分栏。"""
        self.view.set_split_ratio(config.SPLIT_6K_RATIO)
        self._sync_ui()
        self._request_redraw()
        self.status(f"分栏已恢复默认：6K {config.SPLIT_6K_RATIO * 100:.0f}% / 鼠标 {(1 - config.SPLIT_6K_RATIO) * 100:.0f}%")

    def _wheel_notches(self, notches: float, event) -> None:
        self.toolbar.mark_interaction()
        self.toolbar.close_if_open()
        ctrl = bool(event.state & CTRL_MASK)
        shift = bool(event.state & SHIFT_MASK)
        self.controller.on_wheel(float(notches), ctrl, shift)

    def _on_wheel(self, event) -> None:
        delta = event.delta
        notches = delta / 120.0 if abs(delta) >= 120 else float(delta)
        self._wheel_notches(notches, event)

    def _on_escape(self) -> None:
        if self.toolbar.close_if_open():
            self.status("已收起菜单")
        elif self.controller.has_pending():
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

    # ------------------------------------------------------------------ 倍速
    def set_playback_rate(self, rate: float, announce: bool = True) -> None:
        """播放倍速（0.01 ~ 1.0x）：≥0.1x 每 0.1 一档，0.1x 以下每 0.01 一档。

        倍速同时改变谱面推进速度与音频速度（慢放音调随之变低）。
        """
        rate = config.quantize_rate(rate)
        if abs(rate - self.audio.rate) < 1e-9:
            self.toolbar.sync()
            return
        self.audio.set_rate(rate)
        paused = self.audio.paused_by_rate_change
        if announce:
            tone = "（变调：重采样）" if self.audio.stretch_active else ""
            extra = "" if self.audio.usable_audio else "（静音时钟）"
            note = "，已自动暂停（按空格继续）" if paused else ""
            self.status(f"播放倍速：{config.format_rate(rate)}{tone}{extra}{note}")
        self.toolbar.sync()

    def increase_playback_rate(self) -> None:
        self.set_playback_rate(config.step_rate(self.audio.rate, 1))

    def decrease_playback_rate(self) -> None:
        self.set_playback_rate(config.step_rate(self.audio.rate, -1))

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
        chart = Chart()
        chart.settings = self.editor_settings      # 编辑器设置是全局的
        self.document.set_chart(chart)
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
            filetypes=[
                ("JSON 谱面", "*.json"),
                ("全部文件", "*.*"),
            ],
        )
        if not path:
            return
        self.load_chart_file(path)

    def load_chart_file(self, path: str) -> None:
        try:
            chart, raw = load_chart(path)
        except Exception as exc:  # pragma: no cover - 依赖外部文件
            messagebox.showerror("打开失败", f"无法读取谱面：\n{exc}", parent=self.root)
            return
        # 老谱面里可能还留着 editor 段：全局文件还没建立时迁移一次
        legacy = raw.get("editor") if isinstance(raw, dict) else None
        if adopt_legacy_settings(self.editor_settings, legacy):
            save_settings(self.editor_settings)
            self._apply_volumes()
        chart.settings = self.editor_settings
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
                defaultextension=config.CHART_EXTENSION,
                filetypes=[("JSON 谱面", "*.json"), ("全部文件", "*.*")],
                initialfile=(os.path.basename(self.document.path) if self.document.path else "chart.json"),
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
        answer = run_dialog(SaveChangesDialog, self.root, self.theme)
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
        # 重新开始播放：清掉上一轮的“音乐结束位置”
        self._music_end_seconds = None
        timing = self.document.chart.timing
        self.audio.latency_ms = self.document.chart.settings.audio_latency_ms
        song_ms = timing.beat_to_song_ms(self.view.current_beat)
        # 按**新的起播位置**裁剪（不能用旧暂停位置：往回滚再播放会把中间
        # 整段音效裁掉，听起来就是“有时打击音不响”）
        self._refresh_effects(refilter=True, from_ms=song_ms)
        self.audio.play(song_ms)
        reason = audio_silent_reason()
        notes = []
        if self.audio.path is None:
            notes.append("未载入歌曲：静音时钟推进，不会自动停止")
        if reason:
            notes.append(f"静音播放：{reason}")
        note = f"（{'；'.join(notes)}）" if notes else ""
        self.status(f"从第 {self.view.current_beat:.3f} 拍开始播放{note}")

    def pause(self) -> None:
        timing = self.document.chart.timing
        heard = self.audio.heard_position_ms()
        if self.demo_mode:
            # 演示模式按播放器的方式暂停：停在当前时刻，不吸附网格
            self.audio.pause()
            self.view.current_beat = timing.song_ms_to_beat(heard)
            self._demo_dirty = True
            self.status("已暂停（演示模式：空格继续播放）")
            return
        # 用听到的位置（已按倍速缩放延迟），保证暂停时的判定线与刚刚听到的一致
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

    def retry_audio_backend(self) -> bool:
        """重新初始化音频设备（插好耳机 / 释放独占声卡后不必重启编辑器）。"""
        ok = self.audio.retry_backend()
        hint = audio_setup_hint()
        if ok:
            self.status(f"音频后端已启用：{audio_backend_name()}")
        else:
            self.status(f"音频后端仍不可用：{hint or audio_backend_name()}")
        self._sync_ui()
        return ok

    def edit_settings(self) -> None:
        result = run_dialog(
            SettingsDialog,
            self.root,
            self.theme,
            self.document.chart,
            self.retry_audio_backend,
        )
        if not result:
            return

        def _apply() -> None:
            settings = self.document.chart.settings
            settings.zoom_seconds = float(result["zoom_seconds"])
            settings.audio_latency_ms = float(result["audio_latency_ms"])
            settings.music_volume = float(result.get("music_volume", settings.music_volume))
            settings.metronome_volume = float(
                result.get("metronome_volume", settings.metronome_volume)
            )
            settings.hit_volume = float(result.get("hit_volume", settings.hit_volume))
            settings.normalize()

        _apply()          # 编辑器设置：不进撤销栈
        self.audio.latency_ms = self.document.chart.settings.audio_latency_ms
        self._apply_volumes()
        self.view.set_beat(self.view.current_beat)
        self.status(
            f"设置已更新：可视 {self.document.chart.settings.zoom_seconds:g} 秒 · "
            f"延迟 {self.document.chart.settings.audio_latency_ms:g} ms · "
            f"音量 音乐 {self.document.chart.settings.music_volume * 100:.0f}% / "
            f"节拍 {self.document.chart.settings.metronome_volume * 100:.0f}% / "
            f"打击 {self.document.chart.settings.hit_volume * 100:.0f}%"
        )

    def edit_demo_view_settings(self) -> None:
        """编辑器设置菜单「演示视图设置…」：演示视图的画面参数。"""
        result = run_dialog(DemoViewDialog, self.root, self.theme, self.editor_settings)
        if not result:
            return
        settings = self.editor_settings

        def _apply() -> None:
            for key, value in result.items():
                setattr(settings, key, value)
            settings.normalize()

        _apply()          # 编辑器设置：不进撤销栈
        self._refresh_demo_view_settings()
        self.status(
            "演示视图已更新："
            f"单键 {settings.demo_note_thickness:.2f} · "
            f"滑键 {settings.demo_slide_thickness:.3f} · "
            f"流速 {settings.demo_speed:g}（可视 {1500.0 / max(0.1, settings.demo_speed):.0f} ms）"
            + (" · 侧轨高亮" if settings.demo_side_lane_highlight else "")
        )

    def _refresh_demo_view_settings(self) -> None:
        """把演示视图设置套到已创建的离屏视图上（下次绘制就用新参数）。"""
        if self._demo_view is None:
            return
        try:
            from preview import softview

            self._demo_view.settings = softview.view_settings_for(self.editor_settings)
        except Exception:  # pragma: no cover - 没有 pygame / 贴图缺失
            return
        self._demo_dirty = True

    def _apply_volumes(self) -> None:
        """把设置里的三个音量同步给音频后端（一次设置，混音只重合成一次窗口）。"""
        settings = self.document.chart.settings
        self.audio.set_mix(
            settings.music_volume,
            {
                "bar": settings.metronome_volume,
                "beat": settings.metronome_volume,
                "hit": settings.hit_volume,
            },
        )
        self.hit_volume = settings.hit_volume
        # 音量只更新混音增益（键没变就不会重算时刻表）；开关打击音会改键
        self._refresh_effects()

    def toggle_hit_sound(self) -> None:
        """音效菜单：开关打击音效。"""
        chart = self.document.chart
        value = not chart.settings.hit_enabled
        chart.settings.hit_enabled = value          # 编辑器设置：不进撤销栈
        self._apply_volumes()
        self.status("打击音效：" + ("开启" if value else "关闭"))
        self.toolbar.sync()

    def toggle_side_lane_highlight(self) -> None:
        """视图菜单：第 1 / 第 6 轨背景高亮（暗紫色）。"""
        chart = self.document.chart
        value = not chart.settings.side_lane_highlight
        chart.settings.side_lane_highlight = value   # 视图设置：不进撤销栈
        self.status("侧轨高亮：" + ("开启（第 1 / 6 轨暗紫）" if value else "关闭"))

    def toggle_show_past_notes(self) -> None:
        """视图菜单：判定时刻已经过去的物件是否继续绘制（判定线下方）。"""
        chart = self.document.chart
        value = not chart.settings.show_past_notes
        chart.settings.show_past_notes = value       # 视图设置：不进撤销栈
        self.status(
            "显示更早的物件：开启（判定线下方继续绘制）"
            if value
            else "显示更早的物件：关闭（到判定时刻即消失）"
        )
        self.toolbar.sync()

    def edit_volumes(self) -> None:
        """音效菜单「音量…」：调音乐 / 节拍音 / 打击音。"""
        result = run_dialog(VolumeDialog, self.root, self.theme, self.document.chart)
        if not result:
            return

        def _apply() -> None:
            settings = self.document.chart.settings
            settings.music_volume = float(result["music_volume"])
            settings.metronome_volume = float(result["metronome_volume"])
            settings.hit_volume = float(result["hit_volume"])
            settings.normalize()

        _apply()          # 编辑器设置：不进撤销栈
        self._apply_volumes()
        settings = self.document.chart.settings
        self.status(
            f"音量：音乐 {settings.music_volume * 100:.0f}% · "
            f"节拍音 {settings.metronome_volume * 100:.0f}% · "
            f"打击音 {settings.hit_volume * 100:.0f}%"
        )
        self.toolbar.sync()

    def set_grid_division_custom(self) -> None:
        """视图菜单「时间网格细分 → 自定义…」：输入一拍细分多少份。"""
        settings = self.document.chart.settings
        value = run_dialog(
            TextInputDialog,
            self.root,
            self.theme,
            "自定义时间网格",
            f"一拍细分为多少个音（不小于 {config.GRID_DIVISION_MIN}）：",
            str(settings.grid_division),
            kind="int",
            minimum=config.GRID_DIVISION_MIN,
        )
        if value is None:
            self.status("已取消自定义时间网格")
            return
        self.set_grid_division(str(value))

    def set_horizontal_divisions_custom(self) -> None:
        """视图菜单「横向辅助网格 → 自定义…」：输入 0..1 合法区域几等分。"""
        settings = self.document.chart.settings
        value = run_dialog(
            TextInputDialog,
            self.root,
            self.theme,
            "自定义横向辅助网格",
            "0..1 合法区域几等分（0 = 关闭）：",
            str(settings.horizontal_divisions),
            kind="int",
            minimum=0,
        )
        if value is None:
            self.status("已取消自定义横向辅助网格")
            return
        self.set_horizontal_divisions(str(value))

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
        bars_note = "" if timing.has_bars_at(beat) else "段内小节线已关闭 · "
        return (
            f"当前判定线：第 {beat:.3f} 拍"
            f"（小节 {bar + 1} 第 {in_bar + 1:.2f} 拍 · {bars_note}"
            f"{self.view.time_label(beat)}）"
        )

    def show_guide(self) -> None:
        """操作指南（与快捷键拆成两个独立窗口）。"""
        from ui.dialogs import GUIDE_TEXT

        run_dialog(HelpDialog, self.root, self.theme, "操作指南", GUIDE_TEXT)

    def show_shortcuts(self) -> None:
        from ui.dialogs import SHORTCUTS_TEXT

        run_dialog(HelpDialog, self.root, self.theme, "快捷键", SHORTCUTS_TEXT)

    def auto_check_issues(self):
        """当前的自动检查结果（纯数据，便于测试）。"""
        return validate_chart(self.document.chart)

    def run_auto_check(self) -> None:
        """自动检查谱面：列出问题并选中相关物件。"""
        from ui.dialogs import HelpDialog

        issues = self.auto_check_issues()
        if issues:
            note_ids = [nid for issue in issues for nid in issue.note_ids]
            self.document.select_only(note_ids)
            self.status(f"自动检查：发现 {len(issues)} 处问题（相关物件已选中）")
        else:
            self.document.clear_selection()
            self.status("自动检查：未发现问题")
        report = format_report(self.document.chart, issues)
        run_dialog(HelpDialog, self.root, self.theme, "自动检查报告", report)

    def _region_editor(
        self,
        beat_start: float,
        beat_end: float,
        left: Tuple[float, float],
        right: Tuple[float, float],
    ) -> Optional[Dict]:
        """空域 / 禁区：时间与左右边界首尾位置点完后，用对话框确认线型（也可以微调位置）。"""
        template_class = (
            ForbiddenRegion
            if self.controller.tool == config.TOOL_MOUSE_FORBIDDEN
            else MouseRegion
        )
        template = template_class(
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

    def cut_selection(self) -> None:
        """剪切：复制到剪贴板后删除，整体只产生一步撤销。"""
        if not self.document.selection:
            self.status("未选择任何物件")
            return
        count = self.document.cut_selection()
        if self.controller.paste_mode:
            # 剪贴板内容已更新：退出已进入的粘贴模式，避免预览与实际粘贴不一致
            self.controller.cancel_pending()
        self.status(f"已剪切 {count} 个物件" if count else "未选择任何物件")

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
        settings.grid_division = max(config.GRID_DIVISION_MIN, value)
        self.view.set_beat(self.view.current_beat)
        self.status(f"时间网格：一拍细分为 {settings.grid_division} 份")

    def set_horizontal_divisions(self, text: str) -> None:
        text = str(text).strip()
        value = 0 if text in ("", "关") else int(float(text))
        settings = self.document.chart.settings
        settings.horizontal_divisions = max(0, value)
        self.status(
            "横向辅助网格已关闭"
            if settings.horizontal_divisions == 0
            else f"横向辅助网格：0..1 合法区域 {settings.horizontal_divisions} 等分（滑键吸附到网格线）"
        )

    # ------------------------------------------------------------------ 演示
    def set_demo_mode(self, enabled: bool) -> None:
        if enabled == self.demo_mode:
            return
        self.demo_mode = enabled
        self.render_state.demo = enabled
        # 演示模式在画布上直接贴演示视图的软件渲染画面；LAYOUT_OVERLAY 保留给
        # “没有 pygame”时的退路（编辑器自己的叠加绘制）
        self.view.set_layout_mode(config.LAYOUT_OVERLAY if enabled else config.LAYOUT_SPLIT)
        self.controller.cancel_pending()
        if enabled:
            self.document.clear_selection()
            self.toolbar.pack_forget()
            self.status_bar.pack_forget()
            self.vscroll.pack_forget()          # 播放器视图没有时间轴滚动条
            self._start_demo_view()
            self.render_state.message = ""
        else:
            self._stop_demo_view()
            self.toolbar.pack(side="top", fill="x")
            self.status_bar.pack(side="bottom", fill="x")
            self.vscroll.pack(side="right", fill="y", before=self.canvas)
            self.body.pack_forget()
            self.body.pack(side="top", fill="both", expand=True)
            self.render_state.message = ""
        # 演示视图 / 编辑画布共用同一个画布控件：切换后把键盘焦点交给它
        try:
            self.canvas.focus_set()
        except tk.TclError:                     # pragma: no cover - 窗口正在销毁
            pass
        self.status("进入演示模式（演示视图软件渲染）" if enabled else "退出演示模式")

    def toggle_demo(self) -> None:
        self.set_demo_mode(not self.demo_mode)

    # ---------------------------------------------------------- 演示渲染
    def _start_demo_view(self) -> None:
        """进入演示模式：清空编辑画面，准备贴演示视图的软件渲染帧。"""
        self.scene.clear()
        self.canvas.delete("demo")
        self._demo_photo = None
        self._demo_image_id = None
        self._demo_hint_id = None
        self._demo_dirty = True
        self._demo_next_frame = 0.0

    def _stop_demo_view(self) -> None:
        """退出演示模式：删掉演示画面，交回编辑器渲染。"""
        self.canvas.delete("demo")
        self._demo_photo = None
        self._demo_image_id = None
        self._demo_hint_id = None
        self.scene.clear()          # 画布上已没有编辑图元，场景缓存同步清空
        self._demo_dirty = True

    def _draw_demo_frame(self) -> bool:
        """演示模式：把演示视图软件渲染的一帧贴到画布上。

        复用 ``preview/softview.py``（软件渲染），只画场景本身：不画分数 /
        判定 / 元数据等 HUD，也不画鼠标指针；渲染参数来自「演示视图设置」。
        没有 pygame 时返回 ``False``，调用方回退到编辑器自己的叠加绘制。
        """
        try:
            from preview import softview
        except Exception as exc:  # pragma: no cover - 缺少 pygame
            self.status(f"演示模式需要 pygame：{exc}")
            return False
        if not softview.available():
            self.status("演示模式需要 pygame-ce（pip install -r requirements.txt）")
            return False
        width = max(2, int(self.view.layout.width))
        height = max(2, int(self.view.layout.height))
        now = time.perf_counter()
        playing = self.audio.is_playing
        if not self._demo_dirty and not playing and now < self._demo_next_frame:
            return True                      # 暂停时复用上一帧
        if self._demo_view is None or self._demo_size != (width, height):
            try:
                if self._demo_view is None:
                    self._demo_view = softview.create_software_view(
                        width,
                        height,
                        view_settings=softview.view_settings_for(self.editor_settings),
                    )
                else:
                    softview.resize(self._demo_view, width, height)
            except Exception as exc:  # pragma: no cover - 贴图缺失 / 无 pygame
                self.status(f"演示模式初始化失败：{exc}")
                self._demo_view = None
                return False
            self._demo_size = (width, height)
            self._demo_photo = None
            self._demo_dirty = True
        started = time.perf_counter()
        frame = softview.draw_frame(
            self._demo_view,
            self.document.chart,
            self.audio.heard_position_ms(),
            rate=self.audio.rate,
        )
        self._present_demo_frame(frame)
        cost = time.perf_counter() - started
        # 自适应帧率：一帧很快时按 60fps，慢时降到 30fps，保证界面不被拖住
        self._demo_next_frame = now + (1.0 / 60.0 if cost < 0.010 else 1.0 / 30.0)
        self._demo_dirty = False
        return True

    def _present_demo_frame(self, frame) -> None:
        """把 pygame 画面转成 Tk 图像贴到画布（PPM 由 Tk 直接读取）。"""
        import pygame

        width, height = frame.get_size()
        raw = pygame.image.tobytes(frame, "RGB")
        ppm = b"P6\n%d %d\n255\n" % (width, height) + raw
        if self._demo_photo is None:
            self._demo_photo = tk.PhotoImage(
                master=self.root, width=width, height=height
            )
            self._demo_image_id = self.canvas.create_image(
                0, 0, anchor="nw", image=self._demo_photo, tags=("demo",)
            )
        # put 直接写入像素，比 configure(data=) 快近一倍（同一块图像内存）
        self._demo_photo.put(ppm, to=(0, 0))
        self.canvas.tag_raise(self._demo_image_id)
        self._update_demo_hint(width, height)

    def _update_demo_hint(self, width: int, height: int) -> None:
        """左上角的小提示（属于编辑器外壳，不在演示画面里）。"""
        text = (
            "演示模式 · 空格 暂停 · Tab / Esc 返回编辑"
            if self.audio.is_playing
            else "演示模式 · 空格 播放 · Tab / Esc 返回编辑"
        )
        if self._demo_hint_id is None:
            self._demo_hint_id = self.canvas.create_text(
                12,
                10,
                anchor="nw",
                text=text,
                fill="#c8c8d8",
                font=self.theme.font(self.theme.font_small),
                tags=("demo",),
            )
        else:
            self.canvas.itemconfigure(self._demo_hint_id, text=text)
        self.canvas.tag_raise(self._demo_hint_id)


def main(argv: Optional[list] = None) -> int:  # pragma: no cover - GUI 入口
    import argparse

    parser = argparse.ArgumentParser(description=f"{config.APP_TITLE} {config.VERSION}")
    parser.add_argument("chart", nargs="?", help="要打开的谱面文件（JSON，.json）")
    parser.add_argument("--demo", action="store_true", help="直接进入演示模式")
    parser.add_argument("--make-demo", action="store_true", help="生成示例谱面后退出")
    parser.add_argument("--windowed", action="store_true", help="以普通窗口启动（默认最大化）")
    parser.add_argument("--fullscreen", action="store_true", help="以无边框全屏启动（F11 同样可切）")
    args = parser.parse_args(argv)

    if args.make_demo:
        from demo import write_demo_chart

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

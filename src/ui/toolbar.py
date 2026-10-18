"""顶部工具栏：把相似的工具合并成分组，具体功能收进二级菜单。

顶栏只保留几个分组入口 + 网格 / 倍速输入 + 播放（全部是黑底，二级菜单也是黑底）：

* 文件   —— 新建 / 打开 / 保存 / 另存为
* 编辑   —— 撤销 / 重做 / 剪切 / 复制 / 粘贴 / 镜像 / 删除 / 全选（编辑物件是顶栏独立按钮）
* 放置   —— 选择、单轨、多轨、长按、空域、禁区、左右滑键（二级菜单里可勾选）
* 视图   —— 缩放、时间网格细分、横向辅助网格、跳转、全屏
* 谱面信息 —— 元数据、BPM 与偏移、速度组
* 其他设置 —— 设置、演示设置、修改快捷键

不保留系统菜单栏；演示模式下工具栏会隐藏，此时可用 Esc 返回编辑。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

import config
from metronome import LABELS as METRONOME_LABELS
from metronome import MODE_BAR, MODE_BEAT, MODE_OFF
from shortcuts import normalize_shortcut
from .theme import Theme


@dataclass
class Entry:
    """一个菜单项（命令 / 勾选项 / 子菜单 / 分隔线）。"""

    label: str = ""
    command: Optional[Callable[[], None]] = None
    accelerator: str = ""
    accelerator_fn: Optional[Callable[[], str]] = None
    enabled: Optional[Callable[[], bool]] = None
    checked: Optional[Callable[[], bool]] = None
    separator: bool = False
    options: Optional[List["Entry"]] = None
    # 动态标题：菜单每次同步时重新求值（例如显示当前自定义值）
    label_fn: Optional[Callable[[], str]] = None


@dataclass
class Group:
    """一组相似功能（对应一个二级菜单）。"""

    label: str
    entries: List[Entry] = field(default_factory=list)
    # 有些分组只有一个动作：工具栏上直接做成按钮，菜单栏里仍是一个单项菜单
    direct_command: Optional[Callable[[], None]] = None
    enabled: Optional[Callable[[], bool]] = None


class Toolbar(ttk.Frame):
    def __init__(self, parent, app, theme: Theme) -> None:
        super().__init__(parent, padding=(6, 3, 6, 3), style="TFrame")
        self.app = app
        self.theme = theme
        self.root: tk.Misc = app.root
        self._registered: List[Tuple[tk.Menu, int, Entry, Optional[tk.Variable], object]] = []
        # 菜单项 -> (菜单, 条目下标)：撤销 / 重做要按动作名动态改文字
        self._entry_positions: Dict[int, Tuple[tk.Menu, int]] = {}
        self._vars: Dict[str, tk.Variable] = {}
        self._group_buttons: Dict[str, tk.Menubutton] = {}
        self._direct_buttons: List[Tuple[ttk.Button, Optional[Callable[[], bool]]]] = []
        self._last_direct_state: List[Optional[str]] = []
        self._menus: Dict[str, tk.Menu] = {}
        self._menu_buttons: Dict[str, ttk.Button] = {}
        self._open_group: Optional[str] = None
        # 缓存上一次真正写入菜单/按钮的值：每帧无脑 entryconfigure 会导致菜单频闪
        self._last_entry_state: List[Optional[Tuple[str, Optional[bool]]]] = []
        self._last_entry_labels: List[Optional[str]] = []
        self._last_entry_accelerators: List[Optional[str]] = []
        self._last_labels: Dict[int, str] = {}
        self._last_play_text: Optional[str] = None
        self._last_rate_text: Optional[str] = None
        self._rate_editing = False
        self._grid_editing = False
        # 原生弹出菜单期间暂停画布重绘（见 is_menu_posted 的说明）
        self._menu_open = False
        self._groups = self._build_groups()
        self._build_widgets()

    # ------------------------------------------------------------------ 菜单定义
    def _build_groups(self) -> List[Group]:
        app = self.app
        not_demo = lambda: not app.demo_mode  # noqa: E731
        has_selection = lambda: bool(app.document.selection) and not app.demo_mode  # noqa: E731
        single_selection = (
            lambda: app.document.single_selected() is not None and not app.demo_mode  # noqa: E731
        )
        can_undo = lambda: app.document.can_undo() and not app.demo_mode  # noqa: E731
        can_redo = lambda: app.document.can_redo() and not app.demo_mode  # noqa: E731
        has_clipboard = lambda: bool(app.document.clipboard) and not app.demo_mode  # noqa: E731

        # 撤销 / 重做保留引用：菜单文字随动作名动态更新（见 sync / _set_entry_label）
        self._undo_entry = Entry(
            "撤销", app.undo, accelerator_fn=lambda: app.shortcut_text("undo"),
            enabled=can_undo,
        )
        self._redo_entry = Entry(
            "重做", app.redo, accelerator_fn=lambda: app.shortcut_text("redo"),
            enabled=can_redo,
        )

        # 「文件」与「编辑」整合成一个一级菜单
        document_group = Group("文件 / 编辑", [
            Entry(
                "新建谱面", app.new_chart,
                accelerator_fn=lambda: app.shortcut_text("new"),
            ),
            Entry(
                "打开谱面…", app.open_chart,
                accelerator_fn=lambda: app.shortcut_text("open"),
            ),
            Entry(
                "快速打开上次谱面", app.open_last_chart,
                accelerator_fn=lambda: app.shortcut_text("open_last"),
            ),
            Entry(separator=True),
            Entry(
                "保存", lambda: app.save_chart(False),
                accelerator_fn=lambda: app.shortcut_text("save"),
            ),
            Entry(
                "另存为…", lambda: app.save_chart(True),
                accelerator_fn=lambda: app.shortcut_text("save_as"),
            ),
            Entry(separator=True),
            self._undo_entry,
            self._redo_entry,
            Entry(separator=True),
            Entry(
                "剪切", app.cut_selection,
                accelerator_fn=lambda: app.shortcut_text("cut"),
                enabled=has_selection,
            ),
            Entry(
                "复制", app.copy_selection,
                accelerator_fn=lambda: app.shortcut_text("copy"),
                enabled=has_selection,
            ),
            Entry(
                "粘贴", app.begin_paste,
                accelerator_fn=lambda: app.shortcut_text("paste"),
                enabled=has_clipboard,
            ),
            Entry(
                "沿中轴线镜像",
                app.mirror_selection,
                enabled=has_selection,
                accelerator_fn=lambda: app.shortcut_text("mirror"),
            ),
            Entry(
                "编辑选中物件",
                app.edit_selected,
                enabled=single_selection,
                accelerator_fn=lambda: app.shortcut_text("edit_selected"),
            ),
            Entry(separator=True),
            Entry(
                "删除", app.delete_selection,
                accelerator_fn=lambda: app.shortcut_text("delete"),
                enabled=has_selection,
            ),
            Entry(
                "全选", app.select_all,
                accelerator_fn=lambda: app.shortcut_text("select_all"),
                enabled=not_demo,
            ),
            Entry(separator=True),
            Entry("自动检查…", app.run_auto_check, enabled=not_demo),
            Entry(separator=True),
            Entry("退出", app.on_close),
        ])

        place_group = Group("放置物件", [
            Entry(
                config.TOOL_LABELS[tool],
                (lambda t=tool: app.set_tool(t)),
                enabled=not_demo,
                checked=(lambda t=tool: app.controller.tool == t),
                accelerator_fn=lambda t=tool: app.shortcut_text(f"tool:{t}"),
            )
            for tool in config.TOOL_ORDER
        ] + [
            Entry(separator=True),
            Entry(
                "取消当前放置流程", app.cancel_pending,
                accelerator_fn=lambda: app.shortcut_text("escape"),
            ),
            Entry("清空选择", app.clear_selection),
        ])

        zoom_options = [
            Entry(
                "放大",
                lambda: app.zoom(1 / config.ZOOM_STEP),
                accelerator_fn=lambda: app.shortcut_text("zoom_in"),
            ),
            Entry(
                "缩小",
                lambda: app.zoom(config.ZOOM_STEP),
                accelerator_fn=lambda: app.shortcut_text("zoom_out"),
            ),
            Entry("重置缩放", app.reset_zoom),
        ]
        grid_choices = config.GRID_DIVISION_CHOICES
        grid_options = [
            Entry(
                f"1/{value}",
                (lambda v=value: app.set_grid_division(str(v))),
                checked=(lambda v=value: app.editor_settings.grid_division == v),
            )
            for value in grid_choices
        ] + [
            Entry(separator=True),
            Entry(
                "",
                app.set_grid_division_custom,
                checked=lambda: app.editor_settings.grid_division not in grid_choices,
                label_fn=lambda: (
                    f"自定义… · 当前 1/{app.editor_settings.grid_division}"
                ),
            ),
        ]
        hgrid_options = [Entry("关闭", lambda: app.set_horizontal_divisions("0"),
                               checked=lambda: app.editor_settings.horizontal_divisions == 0)]
        hgrid_options += [
            Entry(
                f"{value} 等分",
                (lambda v=value: app.set_horizontal_divisions(str(v))),
                checked=(lambda v=value: app.editor_settings.horizontal_divisions == v),
            )
            for value in (2, 3, 4, 6, 8, 10, 12, 16)
        ]
        hgrid_options += [
            Entry(separator=True),
            Entry(
                "自定义…",
                app.set_horizontal_divisions_custom,
            ),
        ]
        view_group = Group("视图", [
            Entry("缩放", options=zoom_options),
            Entry(separator=True),
            Entry("时间网格细分", options=grid_options),
            Entry("横向辅助网格", options=hgrid_options),
            Entry(
                "侧轨高亮",
                app.toggle_side_lane_highlight,
                checked=lambda: app.editor_settings.side_lane_highlight,
            ),
            Entry(
                "显示更早的物件",
                app.toggle_show_past_notes,
                checked=lambda: app.editor_settings.show_past_notes,
            ),
            Entry(
                "忽略速度组",
                app.toggle_ignore_speed_groups,
                checked=lambda: app.editor_settings.ignore_speed_groups,
            ),
            Entry(separator=True),
            Entry(
                "播放 / 暂停",
                app.toggle_play,
                label_fn=lambda: "暂停" if app.audio.is_playing else "播放",
                accelerator_fn=lambda: app.shortcut_text("play"),
            ),
            Entry(
                "演示模式（已弃用）",
                app.toggle_demo,
                checked=lambda: app.demo_mode,
                accelerator_fn=lambda: app.shortcut_text("demo"),
            ),
            Entry(separator=True),
            Entry(
                "最大化 / 还原窗口",
                app.toggle_maximize,
                checked=lambda: app.maximized,
                accelerator_fn=lambda: app.shortcut_text("maximize"),
            ),
            Entry(
                "全屏切换",
                app.toggle_fullscreen,
                checked=lambda: app.fullscreen,
                accelerator_fn=lambda: app.shortcut_text("fullscreen"),
            ),
        ])

        # 顶栏顺序：文件/编辑 → 谱面信息 → 放置物件 → 编辑物件 → 视图
        #           → 音效 → 其他设置；播放与数值输入固定在右端。
        edit_note_group = Group("编辑物件", direct_command=app.edit_selected, enabled=single_selection)
        chart_group = Group("谱面信息", [
            Entry("元数据设置…", app.edit_metadata, enabled=not_demo),
            Entry("BPM 与偏移…", app.edit_timing, enabled=not_demo),
            Entry("速度组设置…", app.edit_speed_groups, enabled=not_demo),
        ])
        sound_group = Group("音效", [
            Entry(
                "打击音效",
                app.toggle_hit_sound,
                checked=lambda: app.editor_settings.hit_enabled,
            ),
            Entry(
                "节拍音",
                options=[
                    Entry(
                        METRONOME_LABELS[mode],
                        (lambda m=mode: app.set_metronome(m)),
                        checked=(lambda m=mode: app.editor_settings.metronome == m),
                    )
                    for mode in (MODE_OFF, MODE_BAR, MODE_BEAT)
                ],
            ),
            Entry(separator=True),
            Entry("音量…", app.edit_volumes),
        ])
        editor_group = Group("其他设置", [
            Entry("编辑器设置…", app.edit_settings),
            Entry("演示视图设置…（已弃用）", app.edit_demo_view_settings),
            Entry("修改快捷键…", app.edit_shortcuts),
        ])

        return [
            document_group,
            chart_group,
            place_group,
            edit_note_group,
            view_group,
            sound_group,
            editor_group,
        ]

    # ------------------------------------------------------------------ 构建
    def _build_widgets(self) -> None:
        app = self.app
        # 右侧固定区（不参与横向滚动）：时间网格 / 倍速 / 播放。
        self._right = ttk.Frame(self)
        self._right.pack(side="right", fill="y")
        # 播放按钮固定在工具栏最右端
        self.btn_play = ttk.Button(
            self._right, text="▶ 播放", style="Toolbar.Accent.TButton",
            command=self._with_focus(app.toggle_play),
        )
        self.btn_play.pack(side="right", padx=(8, 0))
        # 播放倍速：［−］［当前倍速输入］x［＋］
        ttk.Button(
            self._right, text="+", width=2, style="Tool.TButton",
            command=self._with_focus(app.increase_playback_rate),
        ).pack(side="right", padx=(4, 0))
        ttk.Label(
            self._right, text="x", font=self.theme.font(self.theme.font_small)
        ).pack(side="right")
        self.entry_rate = tk.Entry(
            self._right,
            width=4,
            justify="center",
            relief="flat",
            bd=0,
            highlightthickness=0,
            background="#000000",
            foreground=self.theme.text,
            insertbackground=self.theme.text,
            font=self.theme.font(self.theme.font_small),
        )
        self.entry_rate.pack(side="right", padx=2)
        self._rate_var = tk.StringVar(value=config.format_rate_value(config.DEFAULT_RATE))
        self.entry_rate.configure(textvariable=self._rate_var)
        self.entry_rate.bind("<FocusIn>", lambda _e: setattr(self, "_rate_editing", True))
        self.entry_rate.bind("<FocusOut>", self._commit_rate_entry)
        self.entry_rate.bind("<Return>", self._submit_rate_entry)
        ttk.Button(
            self._right, text="−", width=2, style="Tool.TButton",
            command=self._with_focus(app.decrease_playback_rate),
        ).pack(side="right")

        # 时间网格：［−］1/［输入］［＋］，整体位于倍速左侧。
        ttk.Button(
            self._right,
            text="+",
            width=2,
            style="Tool.TButton",
            command=self._with_focus(app.increase_grid_division),
        ).pack(side="right", padx=(8, 0))
        self.entry_grid = tk.Entry(
            self._right,
            width=4,
            justify="center",
            relief="flat",
            bd=0,
            highlightthickness=0,
            background="#000000",
            foreground=self.theme.text,
            insertbackground=self.theme.text,
            font=self.theme.font(self.theme.font_small),
        )
        self.entry_grid.pack(side="right", padx=1)
        self._grid_var = tk.StringVar(
            value=str(app.editor_settings.grid_division)
        )
        self.entry_grid.configure(textvariable=self._grid_var)
        self.entry_grid.bind("<FocusIn>", lambda _e: setattr(self, "_grid_editing", True))
        self.entry_grid.bind("<FocusOut>", self._commit_grid_entry)
        self.entry_grid.bind("<Return>", self._submit_grid_entry)
        ttk.Label(
            self._right, text="1/", font=self.theme.font(self.theme.font_small)
        ).pack(side="right")
        ttk.Button(
            self._right,
            text="−",
            width=2,
            style="Tool.TButton",
            command=self._with_focus(app.decrease_grid_division),
        ).pack(side="right")

        # 左侧分组放在可横向滚动的视口里：窗口宽度不足时自动出现横向滚动条
        self._strip_canvas = tk.Canvas(
            self, height=30, highlightthickness=0, background=self.theme.panel, bd=0
        )
        self._strip_canvas.pack(side="top", fill="x", expand=True)
        self._strip = ttk.Frame(self._strip_canvas)
        self._strip_window = self._strip_canvas.create_window((0, 0), window=self._strip, anchor="nw")
        self._hscroll = ttk.Scrollbar(
            self,
            orient="horizontal",
            command=self._strip_canvas.xview,
            style="White.Horizontal.TScrollbar",
        )
        self._strip_canvas.configure(xscrollcommand=self._hscroll.set, xscrollincrement=40)
        self._hscroll_visible = False
        self._updating_scroll = False
        self._strip_canvas.bind("<Configure>", lambda _e: self._update_scrollbar())
        self._strip.bind("<Configure>", lambda _e: self._update_scrollbar())

        for group in self._groups:
            if group.direct_command is not None:
                # 与能展开菜单的按钮用同一套样式，保证高度一致
                button = ttk.Button(
                    self._strip,
                    text=group.label,
                    style="Tool.TButton",
                    command=self._with_focus(group.direct_command),
                )
                self._direct_buttons.append((button, group.enabled))
            else:
                button = self._make_menubutton(self._strip, group)
                self._group_buttons[group.label] = button
            button.pack(side="left", padx=(0, 4))
        self._bind_toolbar_wheel(self)

    # ------------------------------------------------------------------ 滚动
    def _on_strip_wheel(self, event) -> None:
        delta = event.delta
        notches = delta / 120.0 if abs(delta) >= 120 else float(delta)
        self._strip_canvas.xview_scroll(int(-notches), "units")

    def _on_strip_wheel_linux(self, event, direction: int) -> str:
        self._strip_canvas.xview_scroll(direction, "units")
        return "break"

    def _bind_toolbar_wheel(self, widget: tk.Misc) -> None:
        """工具栏任意子控件上滚轮都驱动横向滑条。"""
        widget.bind("<MouseWheel>", self._on_strip_wheel, add="+")
        widget.bind(
            "<Button-4>",
            lambda event: self._on_strip_wheel_linux(event, -1),
            add="+",
        )
        widget.bind(
            "<Button-5>",
            lambda event: self._on_strip_wheel_linux(event, 1),
            add="+",
        )
        for child in widget.winfo_children():
            self._bind_toolbar_wheel(child)

    def _commit_rate_entry(self, _event=None) -> None:
        self._rate_editing = False
        try:
            value = float(self._rate_var.get().strip())
        except ValueError:
            self._rate_var.set(config.format_rate_value(self.app.audio.rate))
            return
        self.app.set_playback_rate(value)
        self.sync()

    def _submit_rate_entry(self, event=None) -> str:
        self._commit_rate_entry(event)
        self.focus_canvas()
        return "break"

    def _commit_grid_entry(self, _event=None) -> None:
        self._grid_editing = False
        self.app.set_grid_division(self._grid_var.get())
        self.sync()

    def _submit_grid_entry(self, event=None) -> str:
        self._commit_grid_entry(event)
        self.focus_canvas()
        return "break"

    def _update_scrollbar(self) -> None:
        """内容比视口宽时显示横向滚动条（窗口变窄时自动出现）。"""
        if getattr(self, "_updating_scroll", False) or not hasattr(self, "_strip_canvas"):
            return
        self._updating_scroll = True
        try:
            self._strip.update_idletasks()
            content_w = self._strip.winfo_reqwidth()
            content_h = self._strip.winfo_reqheight()
            view_w = self._strip_canvas.winfo_width()
            width = max(content_w, view_w)
            self._strip_canvas.itemconfigure(self._strip_window, width=width, height=content_h)
            self._strip_canvas.configure(scrollregion=(0, 0, width, content_h))
            self._strip_canvas.configure(height=max(28, content_h))
            need = content_w > view_w + 2
            if need and not self._hscroll_visible:
                self._hscroll.pack(side="bottom", fill="x")
                self._hscroll_visible = True
            elif not need and self._hscroll_visible:
                self._hscroll.pack_forget()
                self._hscroll_visible = False
                self._strip_canvas.xview_moveto(0.0)
        finally:
            self._updating_scroll = False

    def _make_menubutton(self, parent: tk.Misc, group: Group) -> ttk.Button:
        """分组的入口按钮：用普通按钮 + 手动 tk_popup，避免 Menubutton 自带的“再次弹出”。"""
        menu = tk.Menu(
            parent,
            tearoff=0,
            background=self.theme.panel_alt,
            foreground=self.theme.text,
            activebackground=self.theme.accent,
            activeforeground=self.theme.bg,
            disabledforeground=self.theme.text_dim,
            font=self.theme.font(self.theme.font_small),
            borderwidth=1,
            relief="flat",
        )
        self._fill_menu(menu, group.entries)
        self._menus[group.label] = menu
        button = ttk.Button(
            parent,
            text=f"{group.label} ▾",
            style="Tool.TButton",
            command=lambda label=group.label: self.toggle_menu(label),
        )
        self._menu_buttons[group.label] = button
        menu.bind("<Unmap>", self.mark_menu_closed, add="+")
        menu.bind("<Motion>", self.mark_menu_open, add="+")
        return button

    def _fill_menu(self, menu: tk.Menu, entries: List[Entry]) -> None:
        for entry in entries:
            if entry.separator:
                menu.add_separator()
                continue
            if entry.options:
                submenu = tk.Menu(
                    menu,
                    tearoff=0,
                    background=self.theme.panel_alt,
                    foreground=self.theme.text,
                    activebackground=self.theme.accent,
                    activeforeground=self.theme.bg,
                    disabledforeground=self.theme.text_dim,
                    font=self.theme.font(self.theme.font_small),
                )
                self._fill_menu(submenu, entry.options)
                menu.add_cascade(label=entry.label, menu=submenu)
                continue
            label = entry.label_fn() if entry.label_fn is not None else entry.label
            accelerator = self._entry_accelerator(entry) or None
            if entry.checked is not None:
                var = tk.BooleanVar(value=bool(entry.checked()))
                index = menu.index("end")
                position = (index + 1) if index is not None else 0
                menu.add_checkbutton(
                    label=label,
                    command=self._wrap_command(entry.command),
                    variable=var,
                    accelerator=accelerator,
                )
                self._registered.append((menu, position, entry, var, True))
            else:
                index = menu.index("end")
                position = (index + 1) if index is not None else 0
                menu.add_command(
                    label=label,
                    command=self._wrap_command(entry.command),
                    accelerator=accelerator,
                )
                self._registered.append((menu, position, entry, None, False))
            self._entry_positions[id(entry)] = (menu, position)

    @staticmethod
    def _entry_accelerator(entry: Entry) -> str:
        if entry.accelerator_fn is not None:
            return normalize_shortcut(entry.accelerator_fn())
        return normalize_shortcut(entry.accelerator)

    def _wrap_command(self, command: Optional[Callable[[], None]]):
        """菜单项被点击时先收起菜单（并记录状态），再执行动作。"""

        def runner() -> None:
            self.close_open_menu()
            if command is not None:
                command()
            self.focus_canvas()

        return runner

    def _with_focus(self, command: Optional[Callable[[], None]]):
        """工具栏按钮：执行完把键盘焦点交还画布，空格 / 方向键不会被按钮吃掉。"""
        if command is None:
            return None

        def runner() -> None:
            command()
            self.focus_canvas()

        return runner

    def is_playback_control(self, widget) -> bool:
        """播放 / 倍速控件（工具栏右侧固定区）本身不触发“点击即停止播放”。

        点击「播放 / 暂停」按钮时若先被自动停止，紧接着的命令会把状态又切回播放，
        结果就是按了暂停却停不下来。
        """
        cluster = str(self._right)
        path = str(widget)
        return path == cluster or path.startswith(cluster + ".")

    def is_menu_bar_button(self, widget) -> bool:
        """点击的是菜单栏（顶栏）上的按钮吗（分组入口 / 直接命令按钮）。

        只有按钮才算“点击菜单栏”：点顶栏空白处、分隔条或顶栏里的滚动条都不算。
        右侧播放 / 倍速控件另有语义，不在此列。
        """
        if not isinstance(widget, (ttk.Button, tk.Button)):
            return False
        if self.is_playback_control(widget):
            return False
        toolbar_path = str(self)
        path = str(widget)
        return path == toolbar_path or path.startswith(toolbar_path + ".")

    def focus_canvas(self) -> None:
        """把键盘焦点移回画布（演示模式下画布就是演示视图）。"""
        canvas = getattr(self.app, "canvas", None)
        if canvas is None:                      # pragma: no cover - 初始化早期
            return
        try:
            canvas.focus_set()
        except tk.TclError:                     # pragma: no cover - 窗口正在销毁
            pass

    # ------------------------------------------------------------------ 同步
    def mark_menu_open(self, _event=None) -> None:
        """记录“刚展开了菜单”。"""
        self._menu_open = True

    def mark_menu_closed(self, _event=None) -> None:
        self._menu_open = False

    def mark_interaction(self, _event=None) -> None:
        """用户继续操作（鼠标 / 键盘事件）时立即解除绘制保护，没有任何保护时间。"""
        self._menu_open = False

    def is_menu_posted(self) -> bool:
        """是否有二级菜单正在展开（用于暂停画布重绘，避免菜单频闪）。

        Tk 在 Windows 上使用的是原生弹出菜单：展开时既不触发 <Map>，
        winfo_ismapped() 也一直是 0，因此这里只能按事件判定：
        * 按下分组按钮 / 菜单栏 -> mark_menu_open；
        * 菜单 <Unmap>、或画布/窗口重新收到鼠标、滚轮、键盘事件
          （说明菜单已经关闭）-> mark_interaction 立即解除。

        这里不设任何保护时长：菜单关闭后第一个用户事件就会恢复重绘。
        """
        return self._menu_open

    # -------------------------------------------------------------- 菜单开合
    def toggle_menu(self, label: str) -> None:
        """展开 / 收起某个分组菜单：再次点击同一个按钮就是收起。"""
        menu = self._menus.get(label)
        button = self._menu_buttons.get(label)
        if menu is None or button is None:
            return
        if self._open_group == label:
            # 同一个按钮再次点击 -> 收起（_open_group 才是真正的开合状态）
            self.close_open_menu()
            return
        self.close_open_menu()
        self._open_group = label
        self.mark_menu_open()
        self._post_menu(menu, button)
        if not self._menu_is_posted(menu):
            # 阻塞式平台（Windows 原生菜单）在 tk_popup 返回时菜单已经被关闭
            # （那次“再次点击”被菜单自己吞掉了），这里把状态复位即可。
            self._open_group = None
            self.mark_menu_closed()
            # 菜单收起（选中某项或点了外面）后把焦点还给画布
            self.focus_canvas()
        # 注意：不要释放 grab。tk_popup 自带的 grab 会让“再次点击同一个按钮”
        # 由菜单自己接收并收起；一旦释放，这次点击会落到按钮上，反而把菜单重新打开。

    def _menu_is_posted(self, menu: tk.Menu) -> bool:
        """菜单当前是否仍处于展开状态（原生菜单下不可靠，见 toggle_menu 的说明）。"""
        try:
            return bool(menu.winfo_ismapped())
        except tk.TclError:  # pragma: no cover
            return False

    def _post_menu(self, menu: tk.Menu, button: ttk.Button) -> None:
        """弹出菜单（单独抽出来便于测试替换，tk_popup 在部分平台会进入嵌套事件循环）。"""
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        except tk.TclError:  # pragma: no cover - 无显示环境
            self._open_group = None
            self.mark_menu_closed()

    def close_open_menu(self) -> None:
        """收起当前展开的菜单（没有则什么都不做）。"""
        label, self._open_group = self._open_group, None
        if label is not None:
            menu = self._menus.get(label)
            if menu is not None:
                try:
                    menu.unpost()
                except tk.TclError:  # pragma: no cover
                    pass
        self.mark_menu_closed()

    def close_if_open(self) -> bool:
        """有菜单展开时收起并返回 True（供 Esc 使用）。"""
        if self._menu_open and self._open_group is not None:
            self.close_open_menu()
            return True
        return False

    def handle_outside_click(self, widget) -> None:
        """点击菜单 / 分组按钮之外的地方时自动收起。"""
        if not self._menu_open:
            return
        if widget is self._menus.get(self._open_group):
            return
        if widget in self._menu_buttons.values():
            return
        self.close_open_menu()

    def sync(self) -> None:
        app = self.app
        document = app.document
        view = app.view
        if len(self._last_entry_state) != len(self._registered):
            self._last_entry_state = [None] * len(self._registered)
        if len(self._last_entry_labels) != len(self._registered):
            self._last_entry_labels = [None] * len(self._registered)
        if len(self._last_entry_accelerators) != len(self._registered):
            self._last_entry_accelerators = [None] * len(self._registered)
        for position, (menu, index, entry, var, is_check) in enumerate(self._registered):
            state = "normal"
            if entry.enabled is not None and not entry.enabled():
                state = "disabled"
            checked = bool(entry.checked()) if (is_check and entry.checked is not None) else None
            if entry.label_fn is not None:
                text = entry.label_fn()
                if self._last_entry_labels[position] != text:
                    menu.entryconfigure(index, label=text)
                    self._last_entry_labels[position] = text
            accelerator = self._entry_accelerator(entry)
            if self._last_entry_accelerators[position] != accelerator:
                menu.entryconfigure(index, accelerator=accelerator)
                self._last_entry_accelerators[position] = accelerator
            desired = (state, checked)
            previous = self._last_entry_state[position]
            if previous == desired:
                continue  # 值没变就不碰菜单，避免展开中的菜单被反复重绘
            if entry.enabled is not None and (previous is None or previous[0] != state):
                menu.entryconfigure(index, state=state)
            if checked is not None and var is not None and (previous is None or previous[1] != checked):
                var.set(checked)
            self._last_entry_state[position] = desired

        # 撤销 / 重做 显示具体动作名
        self._set_entry_label(
            self._undo_entry,
            f"撤销 {document.undo_label()}".strip() if document.can_undo() else "撤销",
        )
        self._set_entry_label(
            self._redo_entry,
            f"重做 {document.redo_label()}".strip() if document.can_redo() else "重做",
        )

        if hasattr(self, "btn_play"):
            text = "⏸ 暂停" if app.audio.is_playing else "▶ 播放"
            if text != self._last_play_text:
                self.btn_play.configure(text=text)
                self._last_play_text = text
        rate_text = config.format_rate_value(app.audio.rate)
        if rate_text != self._last_rate_text:
            if not self._rate_editing:
                self._rate_var.set(rate_text)
            self._last_rate_text = rate_text
        if not self._grid_editing:
            grid_text = str(app.editor_settings.grid_division)
            if self._grid_var.get() != grid_text:
                self._grid_var.set(grid_text)
        if len(self._last_direct_state) != len(self._direct_buttons):
            self._last_direct_state = [None] * len(self._direct_buttons)
        for position, (button, enabled) in enumerate(self._direct_buttons):
            state = "normal" if (enabled is None or enabled()) else "disabled"
            if state != self._last_direct_state[position]:
                button.configure(state=state)
                self._last_direct_state[position] = state

    def _set_entry_label(self, entry: Entry, text: str) -> None:
        """修改某个菜单项的文字（撤销 / 重做显示具体动作名）。"""
        key = id(entry)
        if self._last_labels.get(key) == text:
            return
        target = self._entry_positions.get(key)
        if target is None:
            return
        menu, index = target
        try:
            menu.entryconfigure(index, label=text)
        except tk.TclError:  # pragma: no cover
            return
        self._last_labels[key] = text

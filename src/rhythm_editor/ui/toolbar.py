"""顶部工具栏：把相似的工具合并成分组，具体功能收进二级菜单。

顶栏只保留 **几个分组入口 + 播放/暂停 + 快速缩放**（全部是黑底按钮，弹出的二级菜单也是黑底）：

* 文件   —— 新建 / 打开 / 保存 / 另存为
* 编辑   —— 撤销 / 重做 / 复制 / 粘贴 / 镜像 / 删除 / 全选（编辑物件是顶栏独立按钮）
* 放置   —— 选择、单轨、多轨、长按（含多轨）、空域、左右滑键（二级菜单里可勾选）
* 视图   —— 缩放、时间网格细分、横向辅助网格、跳转、全屏
* 谱面设置 —— 元数据、BPM 与偏移
* 速度组 / 编辑器设置 —— 单击直接打开对应面板
* 帮助   —— 操作指南、快捷键

不保留系统菜单栏；演示模式下工具栏会隐藏，此时可用 `Tab` / `Esc` 返回编辑。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

from .. import config
from ..metronome import LABELS as METRONOME_LABELS
from ..metronome import MODE_BAR, MODE_BEAT, MODE_OFF
from .theme import Theme


@dataclass
class Entry:
    """一个菜单项（命令 / 勾选项 / 子菜单 / 分隔线）。"""

    label: str = ""
    command: Optional[Callable[[], None]] = None
    accelerator: str = ""
    enabled: Optional[Callable[[], bool]] = None
    checked: Optional[Callable[[], bool]] = None
    separator: bool = False
    options: Optional[List["Entry"]] = None


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
        super().__init__(parent, padding=(8, 6, 8, 6), style="TFrame")
        self.app = app
        self.theme = theme
        self.root: tk.Misc = app.root
        self._registered: List[Tuple[tk.Menu, int, Entry, Optional[tk.Variable], object]] = []
        self._vars: Dict[str, tk.Variable] = {}
        self._group_buttons: Dict[str, tk.Menubutton] = {}
        self._direct_buttons: List[Tuple[ttk.Button, Optional[Callable[[], bool]]]] = []
        self._last_direct_state: List[Optional[str]] = []
        self._menus: Dict[str, tk.Menu] = {}
        self._menu_buttons: Dict[str, ttk.Button] = {}
        self._open_group: Optional[str] = None
        # 缓存上一次真正写入菜单/按钮的值：每帧无脑 entryconfigure 会导致菜单频闪
        self._last_entry_state: List[Optional[Tuple[str, Optional[bool]]]] = []
        self._last_labels: Dict[Tuple[str, int], str] = {}
        self._last_play_text: Optional[str] = None
        self._last_place_text: Optional[str] = None
        self._last_rate_text: Optional[str] = None
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

        # 「文件」与「编辑」整合成一个一级菜单
        document_group = Group("文件 / 编辑", [
            Entry("新建谱面", app.new_chart, "Ctrl+N"),
            Entry("打开谱面…", app.open_chart, "Ctrl+O"),
            Entry(separator=True),
            Entry("保存", lambda: app.save_chart(False), "Ctrl+S"),
            Entry("另存为…", lambda: app.save_chart(True), "Ctrl+Shift+S"),
            Entry(separator=True),
            Entry("退出", app.on_close),
            Entry(separator=True),
            Entry("撤销", app.undo, "Ctrl+Z", enabled=can_undo),
            Entry("重做", app.redo, "Ctrl+Y", enabled=can_redo),
            Entry(separator=True),
            Entry("复制", app.copy_selection, "Ctrl+C", enabled=has_selection),
            Entry("粘贴（点击目标时间）", app.begin_paste, "Ctrl+V", enabled=has_clipboard),
            Entry("沿中轴线镜像", app.mirror_selection, "M", enabled=has_selection),
            Entry(separator=True),
            Entry("删除", app.delete_selection, "Delete", enabled=has_selection),
            Entry("全选", app.select_all, "Ctrl+A", enabled=not_demo),
            Entry(separator=True),
            Entry("自动检查（谱面合法性）…", app.run_auto_check, enabled=not_demo),
        ])

        place_group = Group("放置物件", [
            Entry(
                config.TOOL_LABELS[tool],
                (lambda t=tool: app.set_tool(t)),
                str(index),
                enabled=not_demo,
                checked=(lambda t=tool: app.controller.tool == t),
            )
            for index, tool in enumerate(config.TOOL_ORDER, start=1)
        ] + [
            Entry(separator=True),
            Entry("取消当前放置流程（Esc）", app.cancel_pending, "Esc"),
            Entry("清空选择", app.clear_selection),
        ])

        zoom_options = [
            Entry("放大", lambda: app.zoom(1 / config.ZOOM_STEP), "+"),
            Entry("缩小", lambda: app.zoom(config.ZOOM_STEP), "-"),
            Entry("重置缩放", app.reset_zoom),
        ]
        grid_options = [
            Entry(
                f"1/{value}",
                (lambda v=value: app.set_grid_division(str(v))),
                checked=(lambda v=value: app.document.chart.settings.grid_division == v),
            )
            for value in (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64)
        ] + [
            Entry(separator=True),
            Entry(
                f"自定义…（当前 1/{app.document.chart.settings.grid_division}）",
                app.set_grid_division_custom,
            ),
        ]
        hgrid_options = [Entry("关闭", lambda: app.set_horizontal_divisions("0"),
                               checked=lambda: app.document.chart.settings.horizontal_divisions == 0)]
        hgrid_options += [
            Entry(
                f"{value} 等分",
                (lambda v=value: app.set_horizontal_divisions(str(v))),
                checked=(lambda v=value: app.document.chart.settings.horizontal_divisions == v),
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
            Entry(separator=True),
            Entry("跳到开头", lambda: app.jump(0.0), "Home"),
            Entry("跳到结尾", lambda: app.jump(app.document.chart.beat_bounds()[1]), "End"),
            Entry(separator=True),
            Entry("播放 / 暂停", app.toggle_play, "空格"),
            Entry("演示模式（叠加显示 · 只读）", app.toggle_demo, "Tab",
                  checked=lambda: app.demo_mode),
            Entry(separator=True),
            Entry("最大化 / 还原窗口", app.toggle_maximize, "F10",
                  checked=lambda: app.maximized),
            Entry("全屏切换", app.toggle_fullscreen, "F11",
                  checked=lambda: app.fullscreen),
        ])

        # 顶栏顺序：文件/编辑 → 谱面设置 → 放置物件 → 编辑物件 → 视图 → 速度组
        #           → 音效 → 编辑器设置（帮助收在这里）→ 播放（固定在右端）
        edit_note_group = Group("编辑物件", direct_command=app.edit_selected, enabled=single_selection)
        chart_group = Group("谱面设置", [
            Entry("元数据设置…", app.edit_metadata, enabled=not_demo),
            Entry("BPM 与偏移…", app.edit_timing, enabled=not_demo),
        ])
        speed_group = Group("速度组", direct_command=app.edit_speed_groups, enabled=not_demo)
        sound_group = Group("音效", [
            Entry(
                "打击音效（物件到达判定线）",
                app.toggle_hit_sound,
                checked=lambda: app.document.chart.settings.hit_enabled,
            ),
            Entry(
                "节拍音",
                options=[
                    Entry(
                        METRONOME_LABELS[mode],
                        (lambda m=mode: app.set_metronome(m)),
                        checked=(lambda m=mode: app.document.chart.settings.metronome == m),
                    )
                    for mode in (MODE_OFF, MODE_BAR, MODE_BEAT)
                ],
            ),
            Entry(separator=True),
            Entry("音量…", app.edit_volumes),
        ])
        # 帮助（操作指南 / 快捷键）收进「编辑器设置」菜单里
        editor_group = Group("编辑器设置", [
            Entry("编辑器设置…", app.edit_settings),
            Entry(separator=True),
            Entry("操作指南…", app.show_guide),
            Entry("快捷键…", app.show_shortcuts),
        ])

        return [
            document_group,
            chart_group,
            place_group,
            edit_note_group,
            view_group,
            speed_group,
            sound_group,
            editor_group,
        ]

    # ------------------------------------------------------------------ 构建
    def _build_widgets(self) -> None:
        app = self.app
        # 右侧固定区（不参与横向滚动）：快速缩放 + 帮助，保证窗口再窄也不会被挤掉
        self._right = ttk.Frame(self)
        self._right.pack(side="right", fill="y")
        # 播放按钮固定在工具栏最右端（原来「帮助」的位置）
        self.btn_play = ttk.Button(
            self._right, text="▶ 播放", style="Accent.TButton", command=app.toggle_play
        )
        self.btn_play.pack(side="right", padx=(8, 0))
        # 播放倍速：［−］［当前倍速］［＋］，0.1x 一档，范围 0.1 ~ 1.0x（慢放音调变低）
        ttk.Button(
            self._right, text="+", width=3, command=app.increase_playback_rate
        ).pack(side="right", padx=(4, 0))
        self.lbl_rate = ttk.Label(
            self._right,
            text=f"{config.DEFAULT_RATE:.1f}x",
            width=5,
            anchor="center",
        )
        self.lbl_rate.pack(side="right", padx=4)
        ttk.Button(
            self._right, text="−", width=3, command=app.decrease_playback_rate
        ).pack(side="right")

        # 左侧分组放在可横向滚动的视口里：窗口宽度不足时自动出现横向滚动条
        self._strip_canvas = tk.Canvas(
            self, height=34, highlightthickness=0, background=self.theme.panel, bd=0
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
        self._strip_canvas.bind("<MouseWheel>", self._on_strip_wheel)
        self._strip_canvas.bind("<Button-4>", lambda _e: self._strip_canvas.xview_scroll(-1, "units"))
        self._strip_canvas.bind("<Button-5>", lambda _e: self._strip_canvas.xview_scroll(1, "units"))

        for group in self._groups:
            if group.direct_command is not None:
                # 与能展开菜单的按钮用同一套样式，保证高度一致
                button = ttk.Button(
                    self._strip,
                    text=group.label,
                    style="Tool.TButton",
                    command=group.direct_command,
                )
                self._direct_buttons.append((button, group.enabled))
            else:
                button = self._make_menubutton(self._strip, group)
                self._group_buttons[group.label] = button
            button.pack(side="left", padx=(0, 4))

    # ------------------------------------------------------------------ 滚动
    def _on_strip_wheel(self, event) -> None:
        delta = event.delta
        notches = delta / 120.0 if abs(delta) >= 120 else float(delta)
        self._strip_canvas.xview_scroll(int(-notches), "units")

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
            font=self.theme.font(self.theme.font_normal),
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
            accelerator = entry.accelerator or None
            if entry.checked is not None:
                var = tk.BooleanVar(value=bool(entry.checked()))
                index = menu.index("end")
                menu.add_checkbutton(
                    label=entry.label,
                    command=self._wrap_command(entry.command),
                    variable=var,
                    accelerator=accelerator,
                )
                self._registered.append((menu, (index + 1) if index is not None else 0, entry, var, True))
            else:
                index = menu.index("end")
                menu.add_command(
                    label=entry.label,
                    command=self._wrap_command(entry.command),
                    accelerator=accelerator,
                )
                self._registered.append((menu, (index + 1) if index is not None else 0, entry, None, False))

    def _wrap_command(self, command: Optional[Callable[[], None]]):
        """菜单项被点击时先收起菜单（并记录状态），再执行动作。"""

        def runner() -> None:
            self.close_open_menu()
            if command is not None:
                command()

        return runner

    # ------------------------------------------------------------------ 同步
    def mark_menu_open(self, _event=None) -> None:
        """记录“刚展开了菜单”。"""
        self._menu_open = True

    def mark_menu_closed(self, _event=None) -> None:
        self._menu_open = False

    def mark_interaction(self, _event=None) -> None:
        """用户继续操作（鼠标 / 键盘事件）时**立即**解除绘制保护，没有任何保护时间。"""
        self._menu_open = False

    def is_menu_posted(self) -> bool:
        """是否有二级菜单正在展开（用于暂停画布重绘，避免菜单频闪）。

        Tk 在 Windows 上使用的是原生弹出菜单：展开时既不触发 ``<Map>``，
        ``winfo_ismapped()`` 也一直是 0，因此这里只能按事件判定：
        * 按下分组按钮 / 菜单栏 -> ``mark_menu_open``；
        * 菜单 ``<Unmap>``、或画布/窗口重新收到鼠标、滚轮、键盘事件
          （说明菜单已经关闭）-> ``mark_interaction`` 立即解除。

        这里**不设任何保护时长**：菜单关闭后第一个用户事件就会恢复重绘。
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
        # 注意：**不要**释放 grab。tk_popup 自带的 grab 会让“再次点击同一个按钮”
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
        for position, (menu, index, entry, var, is_check) in enumerate(self._registered):
            state = "normal"
            if entry.enabled is not None and not entry.enabled():
                state = "disabled"
            checked = bool(entry.checked()) if (is_check and entry.checked is not None) else None
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
        self._set_label("编辑", 0, f"撤销 {document.undo_label()}".strip() if document.can_undo() else "撤销")
        self._set_label("编辑", 1, f"重做 {document.redo_label()}".strip() if document.can_redo() else "重做")

        place_button = self._group_buttons.get("放置")
        if place_button is not None:
            text = f"放置：{config.TOOL_LABELS.get(app.controller.tool, '')} ▾"
            if text != self._last_place_text:
                place_button.configure(text=text)
                self._last_place_text = text

        if hasattr(self, "btn_play"):
            text = "⏸ 暂停" if app.audio.is_playing else "▶ 播放"
            if text != self._last_play_text:
                self.btn_play.configure(text=text)
                self._last_play_text = text
        rate_text = f"{app.audio.rate:.1f}x"
        if rate_text != self._last_rate_text:
            self.lbl_rate.configure(text=rate_text)
            self._last_rate_text = rate_text
        if len(self._last_direct_state) != len(self._direct_buttons):
            self._last_direct_state = [None] * len(self._direct_buttons)
        for position, (button, enabled) in enumerate(self._direct_buttons):
            state = "normal" if (enabled is None or enabled()) else "disabled"
            if state != self._last_direct_state[position]:
                button.configure(state=state)
                self._last_direct_state[position] = state

    def _set_label(self, group_label: str, command_index: int, text: str) -> None:
        """按命令序号修改某个分组菜单（含菜单栏副本）的条目文字。"""
        key = (group_label, command_index)
        if self._last_labels.get(key) == text:
            return
        self._last_labels[key] = text
        for menu in (self._menus.get(group_label),):
            if menu is None:
                continue
            try:
                menu.entryconfigure(command_index, label=text)
            except tk.TclError:  # pragma: no cover
                pass

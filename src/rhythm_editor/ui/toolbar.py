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
        # 缓存上一次真正写入菜单/按钮的值：每帧无脑 entryconfigure 会导致菜单频闪
        self._last_entry_state: List[Optional[Tuple[str, Optional[bool]]]] = []
        self._last_labels: Dict[Tuple[str, int], str] = {}
        self._last_play_text: Optional[str] = None
        self._last_place_text: Optional[str] = None
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

        file_group = Group("文件", [
            Entry("新建谱面", app.new_chart, "Ctrl+N"),
            Entry("打开谱面…", app.open_chart, "Ctrl+O"),
            Entry(separator=True),
            Entry("保存", lambda: app.save_chart(False), "Ctrl+S"),
            Entry("另存为…", lambda: app.save_chart(True), "Ctrl+Shift+S"),
            Entry(separator=True),
            Entry("退出", app.on_close),
        ])

        edit_group = Group("编辑", [
            Entry("撤销", app.undo, "Ctrl+Z", enabled=can_undo),
            Entry("重做", app.redo, "Ctrl+Y", enabled=can_redo),
            Entry(separator=True),
            Entry("复制", app.copy_selection, "Ctrl+C", enabled=has_selection),
            Entry("粘贴（点击目标时间）", app.begin_paste, "Ctrl+V", enabled=has_clipboard),
            Entry("沿中轴线镜像", app.mirror_selection, "M", enabled=has_selection),
            Entry(separator=True),
            Entry("删除", app.delete_selection, "Delete", enabled=has_selection),
            Entry("全选", app.select_all, "Ctrl+A", enabled=not_demo),
        ])

        place_group = Group("放置", [
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
        view_group = Group("视图", [
            Entry("缩放", options=zoom_options),
            Entry(separator=True),
            Entry("时间网格细分", options=grid_options),
            Entry("横向辅助网格", options=hgrid_options),
            Entry("显示横向辅助网格", app.toggle_horizontal_grid, "G",
                  checked=lambda: app.view.show_horizontal_grid),
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

        # 原「面板」拆成三栏：谱面设置（元数据 / 偏移）、速度组、编辑器设置
        # 「编辑物件」从二级菜单移到顶栏：紧跟在「编辑」分组后面
        edit_note_group = Group("编辑物件", direct_command=app.edit_selected, enabled=single_selection)
        chart_group = Group("谱面设置", [
            Entry("元数据设置…", app.edit_metadata, enabled=not_demo),
            Entry("BPM 与偏移…", app.edit_timing, enabled=not_demo),
        ])
        speed_group = Group("速度组", direct_command=app.edit_speed_groups, enabled=not_demo)
        editor_group = Group("编辑器设置", direct_command=app.edit_settings)

        help_group = Group("帮助", [
            Entry("操作指南…", app.show_guide),
            Entry("快捷键…", app.show_shortcuts),
        ])

        return [
            file_group,
            edit_group,
            edit_note_group,
            chart_group,
            place_group,
            view_group,
            speed_group,
            editor_group,
            help_group,
        ]

    # ------------------------------------------------------------------ 构建
    def _build_widgets(self) -> None:
        app = self.app
        # 右侧固定区（不参与横向滚动）：快速缩放 + 帮助，保证窗口再窄也不会被挤掉
        self._right = ttk.Frame(self)
        self._right.pack(side="right", fill="y")
        self._group_buttons["帮助"] = self._make_menubutton(self._right, self._groups[-1])
        self._group_buttons["帮助"].pack(side="right", padx=(8, 0))
        # 「+」= 放大（屏幕内时间变少、物件变大），与直觉一致；滚轮方向保持不变
        ttk.Button(
            self._right, text="+", width=3, command=lambda: app.zoom(1 / config.ZOOM_STEP)
        ).pack(side="right", padx=(4, 0))
        ttk.Button(
            self._right, text="−", width=3, command=lambda: app.zoom(config.ZOOM_STEP)
        ).pack(side="right")

        # 左侧分组放在可横向滚动的视口里：窗口宽度不足时自动出现横向滚动条
        self._strip_canvas = tk.Canvas(
            self, height=34, highlightthickness=0, background=self.theme.panel, bd=0
        )
        self._strip_canvas.pack(side="top", fill="x", expand=True)
        self._strip = ttk.Frame(self._strip_canvas)
        self._strip_window = self._strip_canvas.create_window((0, 0), window=self._strip, anchor="nw")
        self._hscroll = ttk.Scrollbar(self, orient="horizontal", command=self._strip_canvas.xview)
        self._strip_canvas.configure(xscrollcommand=self._hscroll.set, xscrollincrement=40)
        self._hscroll_visible = False
        self._updating_scroll = False
        self._strip_canvas.bind("<Configure>", lambda _e: self._update_scrollbar())
        self._strip.bind("<Configure>", lambda _e: self._update_scrollbar())
        self._strip_canvas.bind("<MouseWheel>", self._on_strip_wheel)
        self._strip_canvas.bind("<Button-4>", lambda _e: self._strip_canvas.xview_scroll(-1, "units"))
        self._strip_canvas.bind("<Button-5>", lambda _e: self._strip_canvas.xview_scroll(1, "units"))

        for group in self._groups[:-1]:
            if group.direct_command is not None:
                button = ttk.Button(self._strip, text=group.label, command=group.direct_command)
                self._direct_buttons.append((button, group.enabled))
            else:
                button = self._make_menubutton(self._strip, group)
                self._group_buttons[group.label] = button
            button.pack(side="left", padx=(0, 4))
            if group.label == "放置":
                # 播放放在放置菜单之后，作为最常用的动作（演示模式只在「视图」菜单里）
                self.btn_play = ttk.Button(
                    self._strip, text="▶ 播放", style="Accent.TButton", command=app.toggle_play
                )
                self.btn_play.pack(side="left", padx=(6, 4))

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

    def _make_menubutton(self, parent: tk.Misc, group: Group) -> tk.Menubutton:
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
        button = ttk.Menubutton(parent, text=f"{group.label} ▾", menu=menu, style="Tool.TMenubutton")
        button.bind("<ButtonPress-1>", self.mark_menu_open, add="+")
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
                    command=entry.command,
                    variable=var,
                    accelerator=accelerator,
                )
                self._registered.append((menu, (index + 1) if index is not None else 0, entry, var, True))
            else:
                index = menu.index("end")
                menu.add_command(label=entry.label, command=entry.command, accelerator=accelerator)
                self._registered.append((menu, (index + 1) if index is not None else 0, entry, None, False))

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

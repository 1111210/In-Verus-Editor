"""工具栏与菜单栏：把相似的工具合并成分组，具体功能收进二级菜单。

顶栏只保留 **几个分组入口 + 播放/暂停 + 演示模式 + 快速缩放**：

* 文件   —— 新建 / 打开 / 保存 / 另存为
* 编辑   —— 撤销 / 重做 / 复制 / 粘贴 / 镜像 / 编辑物件 / 删除 / 全选
* 放置   —— 选择、单轨、多轨、长按、停留区域、左右滑键（二级菜单里可勾选）
* 视图   —— 缩放、时间网格细分、横向辅助网格、跳转、全屏
* 面板   —— 元数据 / BPM 与偏移 / 编辑器设置 / 速度组

同样的分组也会挂到窗口菜单栏上（演示模式下工具栏会隐藏，菜单栏仍然可用）。
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


class Toolbar(ttk.Frame):
    def __init__(self, parent, app, theme: Theme) -> None:
        super().__init__(parent, padding=(8, 6, 8, 6), style="TFrame")
        self.app = app
        self.theme = theme
        self.root: tk.Misc = app.root
        self._registered: List[Tuple[tk.Menu, int, Entry, Optional[tk.Variable], object]] = []
        self._vars: Dict[str, tk.Variable] = {}
        self._group_buttons: Dict[str, tk.Menubutton] = {}
        self._menus: Dict[str, tk.Menu] = {}
        self._menubar_menus: Dict[str, tk.Menu] = {}
        self._groups = self._build_groups()
        self._build_widgets()
        self._build_menubar()

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
            Entry("编辑选中物件…", app.edit_selected, "E", enabled=single_selection),
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
            Entry("全屏切换", app.toggle_fullscreen, "F11",
                  checked=lambda: app.fullscreen),
        ])

        panel_group = Group("面板", [
            Entry("元数据设置…", app.edit_metadata, enabled=not_demo),
            Entry("BPM 与偏移…", app.edit_timing, enabled=not_demo),
            Entry(separator=True),
            Entry("编辑器设置…", app.edit_settings),
            Entry("速度组…", app.edit_speed_groups, enabled=not_demo),
        ])

        help_group = Group("帮助", [
            Entry("操作指南 / 快捷键…", app.show_help),
            Entry("关于", app.show_about),
        ])

        return [file_group, edit_group, place_group, view_group, panel_group, help_group]

    # ------------------------------------------------------------------ 构建
    def _build_widgets(self) -> None:
        app = self.app
        # 先把右侧「快速缩放 + 帮助」打包，保证窗口变窄时 + 按钮也不会被挤掉
        right = ttk.Frame(self)
        right.pack(side="right")
        self._group_buttons["帮助"] = self._make_menubutton(right, self._groups[-1])
        self._group_buttons["帮助"].pack(side="right", padx=(8, 0))
        ttk.Button(right, text="+", width=3, command=lambda: app.zoom(config.ZOOM_STEP)).pack(
            side="right", padx=(4, 0)
        )
        self.lbl_zoom = ttk.Label(right, text="", width=14, anchor="center")
        self.lbl_zoom.pack(side="right", padx=4)
        ttk.Button(right, text="−", width=3, command=lambda: app.zoom(1 / config.ZOOM_STEP)).pack(
            side="right"
        )
        ttk.Label(right, text="缩放").pack(side="right", padx=(0, 6))

        for group in self._groups[:-1]:
            button = self._make_menubutton(self, group)
            self._group_buttons[group.label] = button
            button.pack(side="left", padx=(0, 4))
            if group.label == "放置":
                # 播放 / 演示 放在放置菜单之后，作为最常用的两个动作
                self.btn_play = ttk.Button(
                    self, text="▶ 播放", style="Accent.TButton", command=app.toggle_play
                )
                self.btn_play.pack(side="left", padx=(6, 4))
                self.btn_demo = ttk.Button(self, text="演示模式", command=app.toggle_demo)
                self.btn_demo.pack(side="left", padx=(0, 4))
            if group.label != "视图":
                continue

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

    def _build_menubar(self) -> None:
        """窗口菜单栏：与工具栏分组一一对应（演示模式下仍然可用）。"""
        menubar = tk.Menu(
            self.root,
            tearoff=0,
            background=self.theme.panel_alt,
            foreground=self.theme.text,
            activebackground=self.theme.accent,
            activeforeground=self.theme.bg,
            font=self.theme.font(self.theme.font_normal),
        )
        for group in self._groups:
            menu = tk.Menu(
                menubar,
                tearoff=0,
                background=self.theme.panel_alt,
                foreground=self.theme.text,
                activebackground=self.theme.accent,
                activeforeground=self.theme.bg,
                disabledforeground=self.theme.text_dim,
                font=self.theme.font(self.theme.font_normal),
            )
            self._fill_menu(menu, group.entries)
            self._menubar_menus[group.label] = menu
            menubar.add_cascade(label=group.label, menu=menu)
        try:
            self.root.configure(menu=menubar)
        except tk.TclError:  # pragma: no cover
            pass
        self.menubar = menubar

    # ------------------------------------------------------------------ 同步
    def sync(self) -> None:
        app = self.app
        document = app.document
        view = app.view
        for menu, index, entry, var, is_check in self._registered:
            if entry.enabled is not None:
                state = "normal" if entry.enabled() else "disabled"
                menu.entryconfigure(index, state=state)
            if is_check and entry.checked is not None and var is not None:
                var.set(bool(entry.checked()))

        # 撤销 / 重做 显示具体动作名
        self._set_label("编辑", 0, f"撤销 {document.undo_label()}".strip() if document.can_undo() else "撤销")
        self._set_label("编辑", 1, f"重做 {document.redo_label()}".strip() if document.can_redo() else "重做")

        place_button = self._group_buttons.get("放置")
        if place_button is not None:
            place_button.configure(text=f"放置：{config.TOOL_LABELS.get(app.controller.tool, '')} ▾")

        if hasattr(self, "btn_play"):
            self.btn_play.configure(text="⏸ 暂停" if app.audio.is_playing else "▶ 播放")
        if hasattr(self, "btn_demo"):
            self.btn_demo.configure(
                text="退出演示" if app.demo_mode else "演示模式",
                style="Accent.TButton" if app.demo_mode else "TButton",
            )
        layout_name = "分栏（编辑）" if view.split_layout else "叠加（演示）"
        self.lbl_zoom.configure(
            text=f"{view.zoom_seconds:.3g} 秒 · {layout_name}"
        )

    def _set_label(self, group_label: str, command_index: int, text: str) -> None:
        """按命令序号修改某个分组菜单（含菜单栏副本）的条目文字。"""
        for menu in (self._menus.get(group_label), self._menubar_menus.get(group_label)):
            if menu is None:
                continue
            try:
                menu.entryconfigure(command_index, label=text)
            except tk.TclError:  # pragma: no cover
                pass

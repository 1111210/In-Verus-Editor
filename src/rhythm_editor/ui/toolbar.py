"""工具栏：文件、编辑、元数据、BPM、设置、速度组、工具、播放与演示。"""

from __future__ import annotations

from tkinter import ttk
from typing import Callable, Dict, List, Optional

from .. import config
from .theme import Theme

GRID_CHOICES = ("1", "2", "3", "4", "5", "6", "8", "12", "16", "24", "32", "48", "64")
HGRID_CHOICES = ("关", "2", "3", "4", "5", "6", "8", "10", "12", "16", "20", "24", "32")


class Toolbar(ttk.Frame):
    def __init__(self, parent, app, theme: Theme) -> None:
        super().__init__(parent, padding=(8, 6, 8, 4), style="TFrame")
        self.app = app
        self.theme = theme
        self._tool_buttons: Dict[str, ttk.Button] = {}
        self._build()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        app = self.app
        row1 = ttk.Frame(self)
        row1.pack(fill="x")
        row2 = ttk.Frame(self)
        row2.pack(fill="x", pady=(6, 0))

        # ---------------- 第一行：文件 / 编辑 / 面板
        for text, command in (
            ("新建", app.new_chart),
            ("打开", app.open_chart),
            ("保存", lambda: app.save_chart(False)),
            ("另存为", lambda: app.save_chart(True)),
        ):
            ttk.Button(row1, text=text, command=command).pack(side="left", padx=(0, 4))
        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=6)
        self.btn_undo = ttk.Button(row1, text="撤销", command=app.undo)
        self.btn_undo.pack(side="left", padx=(0, 4))
        self.btn_redo = ttk.Button(row1, text="重做", command=app.redo)
        self.btn_redo.pack(side="left", padx=(0, 4))
        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=6)
        for text, command in (
            ("元数据", app.edit_metadata),
            ("BPM / 偏移", app.edit_timing),
            ("编辑器设置", app.edit_settings),
            ("速度组", app.edit_speed_groups),
        ):
            ttk.Button(row1, text=text, command=command).pack(side="left", padx=(0, 4))
        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=6)
        self.btn_play = ttk.Button(row1, text="▶ 播放", style="Accent.TButton", command=app.toggle_play)
        self.btn_play.pack(side="left", padx=(0, 4))
        self.btn_demo = ttk.Button(row1, text="演示模式", command=app.toggle_demo)
        self.btn_demo.pack(side="left", padx=(0, 4))
        ttk.Button(row1, text="帮助", command=app.show_help).pack(side="right")

        # ---------------- 第二行：工具与设置
        for tool in config.TOOL_ORDER:
            button = ttk.Button(
                row2,
                text=config.TOOL_LABELS[tool],
                style="Tool.TButton",
                command=lambda t=tool: app.set_tool(t),
            )
            button.pack(side="left", padx=(0, 4))
            self._tool_buttons[tool] = button

        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=6)
        for text, command in (
            ("复制", app.copy_selection),
            ("粘贴", app.begin_paste),
            ("镜像", app.mirror_selection),
            ("编辑", app.edit_selected),
            ("删除", app.delete_selection),
        ):
            ttk.Button(row2, text=text, command=command).pack(side="left", padx=(0, 4))

        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Label(row2, text="缩放").pack(side="left", padx=(0, 4))
        ttk.Button(row2, text="−", width=3, command=lambda: app.zoom(1 / config.ZOOM_STEP)).pack(side="left")
        self.lbl_zoom = ttk.Label(row2, text="", width=16)
        self.lbl_zoom.pack(side="left", padx=4)
        ttk.Button(row2, text="+", width=3, command=lambda: app.zoom(config.ZOOM_STEP)).pack(side="left")

        ttk.Label(row2, text="每拍细分").pack(side="left", padx=(10, 4))
        self.var_grid = ttk.Combobox(row2, values=list(GRID_CHOICES), width=4, state="readonly")
        self.var_grid.pack(side="left")
        self.var_grid.bind("<<ComboboxSelected>>", lambda _e: app.set_grid_division(self.var_grid.get()))

        ttk.Label(row2, text="横向辅助").pack(side="left", padx=(10, 4))
        self.var_hgrid = ttk.Combobox(row2, values=list(HGRID_CHOICES), width=4, state="readonly")
        self.var_hgrid.pack(side="left")
        self.var_hgrid.bind(
            "<<ComboboxSelected>>", lambda _e: app.set_horizontal_divisions(self.var_hgrid.get())
        )
        self.var_show_hgrid = ttk.Checkbutton(
            row2, text="显示", command=app.toggle_horizontal_grid
        )
        self.var_show_hgrid.pack(side="left", padx=(6, 0))
        self.var_show_hgrid.state(["selected"])

    # ------------------------------------------------------------------ 同步
    def sync(self) -> None:
        app = self.app
        controller = app.controller
        document = app.document
        view = app.view

        for tool, button in self._tool_buttons.items():
            active = tool == controller.tool
            button.configure(style="Active.Tool.TButton" if active else "Tool.TButton")

        self.btn_undo.configure(
            state="normal" if document.can_undo() else "disabled",
            text=f"撤销 {document.undo_label()}".strip() if document.can_undo() else "撤销",
        )
        self.btn_redo.configure(
            state="normal" if document.can_redo() else "disabled",
            text=f"重做 {document.redo_label()}".strip() if document.can_redo() else "重做",
        )
        self.btn_play.configure(text="⏸ 暂停" if app.audio.is_playing else "▶ 播放")
        self.btn_demo.configure(
            text="退出演示" if app.demo_mode else "演示模式",
            style="Accent.TButton" if app.demo_mode else "TButton",
        )
        self.lbl_zoom.configure(
            text=f"{view.zoom_seconds:.3g} 秒 / {view.visible_beats():.1f} 拍"
        )
        settings = document.chart.settings
        self.var_grid.set(str(settings.grid_division))
        self.var_hgrid.set(
            str(settings.horizontal_divisions) if settings.horizontal_divisions > 0 else "关"
        )
        if view.show_horizontal_grid:
            self.var_show_hgrid.state(["selected"])
        else:
            self.var_show_hgrid.state(["!selected"])

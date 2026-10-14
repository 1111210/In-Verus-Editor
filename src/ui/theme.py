"""主题：配色、字体，以及把它们应用到 ttk 控件的样式表。

两部分：
* Theme —— 纯数据的配色 / 字号 / 字体族（画布与自绘控件用）；
* apply_theme —— 把 Theme 灌进 ttk 样式表（工具栏、对话框等标准控件用）。

Tk 画布不支持真正的 alpha 通道，半透明效果用 stipple（点阵填充）来模拟。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

import config


@dataclass(frozen=True)
class Theme:
    # 基础
    bg: str = "#000000"
    panel: str = "#000000"
    panel_alt: str = "#141414"
    play_bg: str = "#000000"
    pane_bg: str = "#050505"
    lane_alt: str = "#000000"
    lane_alt2: str = "#0a0a0a"
    lane_border: str = "#2f2f2f"
    lane_border_strong: str = "#4d4d4d"
    lane_side_highlight: str = "#3a2352"   # 侧轨高亮：第 1 / 第 6 轨背景（暗紫）

    grid_sub: str = "#1a1a1a"
    grid_beat: str = "#2b2b2b"
    grid_bar: str = "#4f4f4f"
    grid_label: str = "#808080"
    hgrid: str = "#333333"
    legal_border: str = "#7fa8ff"

    judge_line: str = "#ffffff"
    judge_glow: str = "#7aa2ff"

    note_white: str = "#ffffff"
    note_border: str = "#b9c6da"
    note_blue: str = "#4da6ff"        # 按键的亮蓝色环 / 长按两侧边线
    note_line_width: int = 3          # 白色横线 / 竖线粗细（单键与长按一致）
    hold_pink: str = "#ff8ec7"
    hold_border: str = "#ffc0e0"

    region_fill: str = "#2f6df6"
    region_edge: str = "#3f8bff"
    # 禁区：几何与空域相同，画成红色（半透明红底 + 红色边线）
    forbidden_fill: str = "#e0392b"
    forbidden_edge: str = "#ff5140"
    slide_fill: str = "#25c06b"
    slide_edge: str = "#25e07f"
    # 向左滑键：半透明黄色（与向右的绿色区分开）
    slide_left_fill: str = "#e8c24a"
    slide_left_edge: str = "#ffd666"

    select: str = "#ffd166"
    pending: str = config.PENDING_COLOR
    rubber: str = "#8fb4ff"
    text: str = "#e8e8e8"
    text_dim: str = "#909090"
    text_warn: str = "#ffb454"
    accent: str = "#59b0ff"

    # 半透明（点阵）
    stipple_fill: str = "gray50"
    stipple_soft: str = "gray25"
    stipple_strong: str = "gray75"

    # 字号
    font_family: str = "Microsoft YaHei UI"
    font_small: int = 9
    font_normal: int = 10
    font_large: int = 12
    font_title: int = 14

    def font(self, size: int | None = None, bold: bool = False):
        """返回 Tk 字体元组（纯数据，任何 Tk 组件都能直接用）。"""
        size = size or self.font_normal
        family = self.font_family
        return (family, size, "bold") if bold else (family, size)


DARK = Theme()


def apply_theme(root: tk.Misc, theme: Theme) -> ttk.Style:
    """把主题应用到 root 下的 ttk 控件，返回配置好的 Style。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:  # pragma: no cover
        pass
    font = theme.font(theme.font_normal)
    font_small = theme.font(theme.font_small)

    root.option_add("*Font", font)
    root.option_add("*TCombobox*Listbox.background", theme.panel_alt)
    root.option_add("*TCombobox*Listbox.foreground", theme.text)
    root.option_add("*TCombobox*Listbox.selectBackground", theme.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", theme.bg)

    style.configure(".", background=theme.panel, foreground=theme.text, font=font)
    style.configure("TFrame", background=theme.panel)
    style.configure("Card.TFrame", background=theme.panel_alt)
    style.configure("TLabel", background=theme.panel, foreground=theme.text, font=font)
    style.configure("Dim.TLabel", background=theme.panel, foreground=theme.text_dim, font=font_small)
    style.configure("Warn.TLabel", background=theme.panel, foreground=theme.text_warn, font=font_small)
    style.configure("Head.TLabel", background=theme.panel, foreground=theme.accent,
                    font=theme.font(theme.font_normal, bold=True))
    style.configure("Title.TLabel", background=theme.panel, foreground=theme.text,
                    font=theme.font(theme.font_title, bold=True))
    style.configure(
        "TButton",
        background=theme.panel_alt,
        foreground=theme.text,
        bordercolor=theme.lane_border,
        focuscolor=theme.accent,
        padding=(8, 4),
        font=font,
    )
    style.map(
        "TButton",
        background=[("active", theme.lane_border), ("pressed", theme.accent), ("disabled", theme.panel)],
        foreground=[("disabled", theme.text_dim)],
    )
    style.configure(
        "Tool.TButton",
        background=theme.panel_alt,
        foreground=theme.text,
        padding=(10, 6),
        font=font,
    )
    style.map(
        "Tool.TButton",
        background=[("active", theme.lane_border)],
    )
    style.configure(
        "Active.Tool.TButton",
        background=theme.accent,
        foreground=theme.bg,
        padding=(10, 6),
        font=theme.font(theme.font_normal, bold=True),
    )
    style.map("Active.Tool.TButton", background=[("active", theme.judge_glow)])
    style.configure(
        "Tool.TMenubutton",
        background=theme.panel_alt,
        foreground=theme.text,
        bordercolor=theme.lane_border,
        arrowcolor=theme.text,
        padding=(10, 5),
        font=font,
    )
    style.map(
        "Tool.TMenubutton",
        background=[("active", theme.lane_border)],
        foreground=[("disabled", theme.text_dim)],
    )
    style.configure(
        "TMenubutton",
        background=theme.panel_alt,
        foreground=theme.text,
        padding=(8, 4),
        font=font,
    )
    style.configure(
        "Accent.TButton",
        background=theme.accent,
        foreground=theme.bg,
        padding=(10, 5),
        font=theme.font(theme.font_normal, bold=True),
    )
    style.map("Accent.TButton", background=[("active", theme.judge_glow)])
    style.configure(
        "TEntry",
        fieldbackground=theme.bg,
        foreground=theme.text,
        insertcolor=theme.text,
        bordercolor=theme.lane_border,
        padding=3,
    )
    style.configure(
        "TCombobox",
        fieldbackground=theme.bg,
        background=theme.panel_alt,
        foreground=theme.text,
        arrowcolor=theme.text,
        bordercolor=theme.lane_border,
        padding=3,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", theme.bg)],
        foreground=[("readonly", theme.text)],
    )
    style.configure(
        "TSpinbox",
        fieldbackground=theme.bg,
        foreground=theme.text,
        background=theme.panel_alt,
        arrowcolor=theme.text,
        bordercolor=theme.lane_border,
        padding=3,
    )
    style.configure(
        "Treeview",
        background=theme.bg,
        fieldbackground=theme.bg,
        foreground=theme.text,
        bordercolor=theme.lane_border,
        rowheight=22,
        font=font,
    )
    style.configure(
        "Treeview.Heading",
        background=theme.panel_alt,
        foreground=theme.text,
        font=theme.font(theme.font_small, bold=True),
    )
    style.map("Treeview", background=[("selected", theme.accent)], foreground=[("selected", theme.bg)])
    style.configure("TLabelframe", background=theme.panel, bordercolor=theme.lane_border, foreground=theme.accent)
    style.configure("TLabelframe.Label", background=theme.panel, foreground=theme.accent, font=font)
    style.configure("TCheckbutton", background=theme.panel, foreground=theme.text, font=font)
    style.map("TCheckbutton", background=[("active", theme.panel)])
    style.configure("TSeparator", background=theme.lane_border)
    # 滚动条：滑条体白色、背景（槽）纯黑
    for name in ("White.Vertical.TScrollbar", "White.Horizontal.TScrollbar"):
        style.configure(
            name,
            background="#ffffff",
            troughcolor=theme.bg,
            bordercolor=theme.bg,
            darkcolor="#ffffff",
            lightcolor="#ffffff",
            arrowcolor="#ffffff",
            relief="flat",
            gripcount=0,
            arrowsize=12,
        )
        style.map(
            name,
            background=[("pressed", "#c8c8c8"), ("active", "#f0f0f0")],
            arrowcolor=[("pressed", theme.bg), ("active", "#ffffff")],
        )
    style.configure("TNotebook", background=theme.panel, bordercolor=theme.lane_border)
    style.configure("TNotebook.Tab", background=theme.panel_alt, foreground=theme.text,
                    padding=(10, 4), font=font)
    style.map("TNotebook.Tab", background=[("selected", theme.accent)], foreground=[("selected", theme.bg)])
    return style

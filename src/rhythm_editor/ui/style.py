"""ttk 深色样式（让工具栏 / 对话框与画布风格一致）。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .theme import Theme


def apply_theme(root: tk.Misc, theme: Theme) -> ttk.Style:
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

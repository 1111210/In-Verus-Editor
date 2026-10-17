"""可配置快捷键的统一定义、归一化与事件转换。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import config


@dataclass(frozen=True)
class EditableCommand:
    action_id: str
    label: str
    default: str


# 顺序也是快捷键设置窗口中的显示顺序。标签和默认键只在这里维护。
EDITABLE_COMMANDS: Tuple[EditableCommand, ...] = tuple(
    EditableCommand(
        f"tool:{tool}",
        f"放置：{config.TOOL_LABELS[tool]}",
        str(index),
    )
    for index, tool in enumerate(config.TOOL_ORDER, start=1)
) + (
    EditableCommand("mirror", "沿中轴镜像", "M"),
    EditableCommand("edit_selected", "编辑选中物件", "E"),
    EditableCommand("zoom_in", "放大", "+"),
    EditableCommand("zoom_out", "缩小", "-"),
    EditableCommand("scroll_back", "时间轴步进：向回", "←"),
    EditableCommand("scroll_forward", "时间轴步进：向前", "→"),
    EditableCommand("page_up", "向上翻页", "PageUp"),
    EditableCommand("page_down", "向下翻页", "PageDown"),
    EditableCommand("play", "播放 / 暂停", "Space"),
    EditableCommand("demo", "演示视图", ""),
    EditableCommand("maximize", "最大化 / 还原窗口", "F10"),
    EditableCommand("fullscreen", "全屏切换", "F11"),
)

EDITABLE_ACTIONS: Tuple[Tuple[str, str], ...] = tuple(
    (command.action_id, command.label) for command in EDITABLE_COMMANDS
)

DEFAULT_SHORTCUTS: Dict[str, str] = {
    command.action_id: command.default for command in EDITABLE_COMMANDS
}


@dataclass(frozen=True)
class FixedCommand:
    """不开放修改的键盘命令。

    ``bindings`` 同时驱动 Tk 绑定、菜单提示和快捷键冲突检查，避免三套表
    各写一遍。只有滚轮等画布事件使用 ``display``，不会生成 root 绑定。
    """

    action_id: str
    label: str
    bindings: Tuple[str, ...] = ()
    display: str = ""
    show_in_help: bool = True

    @property
    def help_text(self) -> str:
        return self.display or " / ".join(self.bindings)

    @property
    def menu_text(self) -> str:
        return self.display or (self.bindings[0] if self.bindings else "")


# 顺序也是帮助与冲突提示中的显示顺序。
FIXED_COMMANDS: Tuple[FixedCommand, ...] = (
    FixedCommand("new", "新建谱面", ("Ctrl N",)),
    FixedCommand("open", "打开谱面", ("Ctrl O",)),
    FixedCommand("open_last", "快速打开上次谱面", ("Ctrl Shift O",)),
    FixedCommand("save", "保存", ("Ctrl S",)),
    FixedCommand("save_as", "另存为", ("Ctrl Shift S",)),
    FixedCommand("undo", "撤销", ("Ctrl Z",)),
    FixedCommand("redo", "重做", ("Ctrl Y", "Ctrl Shift Z")),
    FixedCommand("cut", "剪切", ("Ctrl X",)),
    FixedCommand("copy", "复制", ("Ctrl C",)),
    FixedCommand("paste", "粘贴", ("Ctrl V",)),
    FixedCommand("select_all", "全选", ("Ctrl A",)),
    FixedCommand("delete", "删除", ("Delete", "Backspace")),
    FixedCommand("escape", "取消当前放置流程", ("Esc",)),
    FixedCommand("settings", "编辑器设置", ("Ctrl G",), show_in_help=False),
    FixedCommand("scroll", "时间轴滚动", display="滚轮"),
    FixedCommand("fast_scroll", "快速时间轴滚动", display="Shift 滚轮"),
    FixedCommand("fast_zoom", "快速缩放", display="Ctrl 滚轮"),
)

FIXED_COMMAND_BY_ID: Dict[str, FixedCommand] = {
    command.action_id: command for command in FIXED_COMMANDS
}

# 设置窗口使用 (标签, 显示文本) 形式展示固定快捷键。
FIXED_SHORTCUTS: Tuple[Tuple[str, str], ...] = tuple(
    (command.label, command.help_text)
    for command in FIXED_COMMANDS
    if command.show_in_help
)

_MODIFIER_ORDER = ("Ctrl", "Shift", "Alt")
MODIFIER_TOKENS = frozenset(_MODIFIER_ORDER)
_MODIFIER_ALIASES = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "control_l": "Ctrl",
    "control_r": "Ctrl",
    "shift": "Shift",
    "shift_l": "Shift",
    "shift_r": "Shift",
    "alt": "Alt",
    "alt_l": "Alt",
    "alt_r": "Alt",
    "meta": "Alt",
    "meta_l": "Alt",
    "meta_r": "Alt",
}
_KEY_ALIASES = {
    "space": "Space",
    "plus": "+",
    "kp_add": "+",
    "equal": "=",
    "minus": "-",
    "minus_l": "-",
    "kp_subtract": "-",
    "return": "Enter",
    "kp_enter": "Enter",
    "backspace": "Backspace",
    "delete": "Delete",
    "escape": "Esc",
    "prior": "PageUp",
    "next": "PageDown",
    "left": "←",
    "right": "→",
    "up": "↑",
    "down": "↓",
}


def _canonical_token(value: str) -> str:
    token = str(value).strip()
    if not token:
        return ""
    lowered = token.lower()
    if lowered in _MODIFIER_ALIASES:
        return _MODIFIER_ALIASES[lowered]
    if lowered in _KEY_ALIASES:
        return _KEY_ALIASES[lowered]
    if len(token) == 1 and token.isalpha():
        return token.upper()
    if token.startswith("F") and token[1:].isdigit():
        return token.upper()
    if token.startswith("KP_") and token[3:].isdigit():
        return token[3:]
    return token


def normalize_tokens(tokens: Iterable[str]) -> Tuple[str, ...]:
    """去重、规范修饰键顺序，并把字母键统一成大写。"""
    modifiers: List[str] = []
    keys: List[str] = []
    for raw in tokens:
        token = _canonical_token(raw)
        if not token:
            continue
        target = modifiers if token in _MODIFIER_ORDER else keys
        if token not in target:
            target.append(token)
    modifiers.sort(key=_MODIFIER_ORDER.index)
    return tuple(modifiers + keys)


def normalize_shortcut(value: object) -> str:
    """把空格分隔的快捷键字符串规范化。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return " ".join(normalize_tokens(text.split()))


def normalize_shortcuts(data: Optional[Mapping[str, object]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    source = data or {}
    for action_id, default in DEFAULT_SHORTCUTS.items():
        result[action_id] = normalize_shortcut(source.get(action_id, default))
    return result


def shortcut_keys(value: str) -> Tuple[str, ...]:
    return normalize_tokens(normalize_shortcut(value).split())


def split_shortcut(value: object) -> Tuple[Tuple[str, ...], Optional[str]]:
    """拆成规范化修饰键和唯一主键；主键数量不是 1 时返回 None。"""
    tokens = shortcut_keys(str(value or ""))
    modifiers = tuple(token for token in tokens if token in MODIFIER_TOKENS)
    mains = tuple(token for token in tokens if token not in MODIFIER_TOKENS)
    return modifiers, mains[0] if len(mains) == 1 else None


def shortcut_signature(value: object) -> Optional[Tuple[Tuple[str, ...], str]]:
    """用于精确比较的 (修饰键集合, 主键)；无效配置返回 None。"""
    modifiers, main = split_shortcut(value)
    return (modifiers, main) if main is not None else None


def format_modifiers(modifiers: Iterable[str]) -> str:
    values = [token for token in modifiers if token in MODIFIER_TOKENS]
    values = list(dict.fromkeys(values))
    values.sort(key=_MODIFIER_ORDER.index)
    return " ".join(values)


def event_token(keysym: str) -> str:
    return _canonical_token(keysym)


_TK_MODIFIERS = {"Ctrl": "Control", "Shift": "Shift", "Alt": "Alt"}
_TK_KEYSYMS = {
    "Esc": "Escape",
    "Backspace": "BackSpace",
    "Enter": "Return",
    "PageUp": "Prior",
    "PageDown": "Next",
    "←": "Left",
    "→": "Right",
    "↑": "Up",
    "↓": "Down",
}


def tk_binding(value: object) -> Optional[str]:
    """把规范快捷键转换为 Tk ``bind`` 使用的事件序列。"""

    modifiers, main = split_shortcut(value)
    if main is None:
        return None
    if len(main) == 1 and main.isalpha() and "Shift" not in modifiers:
        main = main.lower()
    parts = [_TK_MODIFIERS[modifier] for modifier in modifiers]
    parts.append(_TK_KEYSYMS.get(main, main))
    return "<" + "-".join(parts) + ">"

"""全局编辑器设置：**不写进谱面文件**，而是存在用户配置目录里。

「视图 / 音效 / 编辑器设置」这些偏好（缩放、网格、延迟、节拍音与打击音开关、音量、
侧轨高亮……）属于**使用者**而不是某一张谱面，所以保存在独立的
``settings.json`` 里，换谱面 / 新建谱面都沿用同一份。

位置（可用环境变量 ``VERUS_SETTINGS`` 覆盖，测试里会用到）::

* Windows：``%APPDATA%/InVerus/settings.json``
* macOS / Linux：``$XDG_CONFIG_HOME/inverus/settings.json`` 或 ``~/.config/inverus/settings.json``
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .model.chart import EditorSettings

ENV_SETTINGS_PATH = "VERUS_SETTINGS"


def settings_path() -> str:
    """全局设置文件路径（不保证已存在）。"""
    override = os.environ.get(ENV_SETTINGS_PATH)
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "InVerus", "settings.json")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "inverus", "settings.json")


def load_settings(path: Optional[str] = None) -> EditorSettings:
    """读取全局设置；文件不存在或损坏时返回默认值（绝不抛异常）。"""
    target = path or settings_path()
    data: Dict[str, Any] = {}
    try:
        with open(target, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded.get("editor", loaded)  # 兼容两种写法
            if not isinstance(data, dict):
                data = {}
    except (OSError, ValueError):
        data = {}
    return EditorSettings.from_dict(data)


def save_settings(settings: EditorSettings, path: Optional[str] = None) -> str:
    """把全局设置写回磁盘（尽力而为，失败也不影响使用）。"""
    target = path or settings_path()
    settings.normalize()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(
                {"format": "inverus.settings", "version": 1, "editor": settings.to_dict()},
                handle,
                ensure_ascii=False,
                indent=2,
            )
    except OSError:  # pragma: no cover - 只读目录等
        pass
    return target


def adopt_legacy_settings(settings: EditorSettings, legacy: Optional[Dict[str, Any]]) -> bool:
    """把老谱面里的 ``editor`` 段并入全局设置（只在全局文件还没建立时迁移一次）。

    返回是否真的做了迁移。
    """
    if not legacy or os.path.exists(settings_path()):
        return False
    merged = EditorSettings.from_dict(legacy)
    for field in (
        "zoom_seconds",
        "grid_division",
        "horizontal_divisions",
        "audio_latency_ms",
        "metronome",
        "hit_enabled",
        "music_volume",
        "metronome_volume",
        "hit_volume",
        "side_lane_highlight",
        "show_past_notes",
    ):
        setattr(settings, field, getattr(merged, field))
    settings.normalize()
    return True

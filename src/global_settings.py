"""全局编辑器设置：**不写进谱面文件**，而是存在用户配置目录里。

「视图 / 音效 / 编辑器设置」这些偏好（缩放、网格、延迟、节拍音与打击音开关、音量、
侧轨高亮……）属于**使用者**，与具体谱面无关，所以保存在独立的
``settings.json`` 里，换谱面 / 新建谱面都沿用同一份。

位置（可用环境变量 ``INFALSUS_EDITOR_SETTINGS`` 覆盖，测试里会用到）::

* Windows：``%APPDATA%/InFalsusEditor/settings.json``
* macOS / Linux：``$XDG_CONFIG_HOME/infalsus-editor/settings.json`` 或
  ``~/.config/infalsus-editor/settings.json``
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from model.chart import EditorSettings
from settings_store import read_json_object, user_config_dir, write_json_object

ENV_SETTINGS_PATH = "INFALSUS_EDITOR_SETTINGS"


def settings_path() -> str:
    """全局设置文件路径（不保证已存在）。"""
    override = os.environ.get(ENV_SETTINGS_PATH)
    if override:
        return override
    return os.path.join(user_config_dir(), "settings.json")


def load_settings(path: Optional[str] = None) -> EditorSettings:
    """读取全局设置；文件不存在或损坏时返回默认值（绝不抛异常）。"""
    loaded = read_json_object(path or settings_path())
    data = loaded.get("editor", loaded)  # 兼容两种写法
    if not isinstance(data, dict):
        data = {}
    return EditorSettings.from_dict(data)


def save_settings(settings: EditorSettings, path: Optional[str] = None) -> str:
    """把全局设置写回磁盘（尽力而为，失败也不影响使用）。"""
    target = path or settings_path()
    settings.normalize()
    write_json_object(
        target,
        {"format": "infalsus.settings", "version": 1, "editor": settings.to_dict()},
    )
    return target


def adopt_chart_settings(settings: EditorSettings, data: Optional[Dict[str, Any]]) -> bool:
    """把谱面 ``editor`` 段里的偏好并入全局设置（仅在全局文件尚未建立时）。

    返回是否真的写入了设置。
    """
    if not data or os.path.exists(settings_path()):
        return False
    merged = EditorSettings.from_dict(data)
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

"""全局编辑器设置：不写进谱面文件，而是存在程序文件夹里的 settings.json。

「视图 / 音效 / 编辑器设置」这些偏好（缩放、网格、延迟、节拍音与打击音开关、音量、
侧轨高亮……）属于使用者，与具体谱面无关，所以保存在独立的
settings.json 里，换谱面 / 新建谱面都沿用同一份。

位置（可用环境变量 INFALSUS_EDITOR_SETTINGS 覆盖，测试里会用到）：

* 源码运行：<仓库根目录>/settings.json
* 打包运行：<exe 所在目录>/settings.json

整个文件夹（连同设置）可以随意拷贝 / 搬迁。程序目录只读、写不进去时，读取与保存
都退回旧版位置 %APPDATA%/InFalsusEditor/settings.json（macOS / Linux 为
~/.config/infalsus-editor/settings.json），保证编辑器仍然可用。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from model.chart import EditorSettings
from settings_store import app_dir, read_json_object, user_config_dir, write_json_object

ENV_SETTINGS_PATH = "INFALSUS_EDITOR_SETTINGS"
SETTINGS_FILENAME = "settings.json"


def settings_path() -> str:
    """全局设置文件路径：程序 / 项目文件夹里的 settings.json（不保证已存在）。"""
    override = os.environ.get(ENV_SETTINGS_PATH)
    if override:
        return override
    return os.path.join(app_dir(), SETTINGS_FILENAME)


def legacy_settings_path() -> str:
    """旧版设置路径：用户配置目录（只在程序目录不可用时读取 / 写入）。"""
    return os.path.join(user_config_dir(), SETTINGS_FILENAME)


def _existing_settings_file() -> Optional[str]:
    """实际存在的设置文件：优先程序目录，其次旧版用户配置目录。"""
    for candidate in (settings_path(), legacy_settings_path()):
        if os.path.exists(candidate):
            return candidate
    return None


def load_settings(path: Optional[str] = None) -> EditorSettings:
    """读取全局设置；文件不存在或损坏时返回默认值（绝不抛异常）。"""
    source = path or _existing_settings_file() or settings_path()
    loaded = read_json_object(source)
    data = loaded.get("editor", loaded)  # 兼容两种写法
    if not isinstance(data, dict):
        data = {}
    return EditorSettings.from_dict(data)


def save_settings(settings: EditorSettings, path: Optional[str] = None) -> str:
    """把全局设置写回磁盘，返回实际写入的位置（尽力而为，失败也不影响使用）。

    程序目录写不进去时（例如 exe 放在只读位置）退回旧版用户配置目录；显式传入的
    path 与 INFALSUS_EDITOR_SETTINGS 覆盖不做兜底，免得写到自己没指定的地方。
    """
    target = path or settings_path()
    settings.normalize()
    data = {"format": "infalsus.settings", "version": 1, "editor": settings.to_dict()}
    if write_json_object(target, data):
        return target
    fallback = legacy_settings_path()
    if path is None and not os.environ.get(ENV_SETTINGS_PATH):
        if fallback != target and write_json_object(fallback, data):
            return fallback
    return target


def adopt_chart_settings(settings: EditorSettings, data: Optional[Dict[str, Any]]) -> bool:
    """把谱面 editor 段里的偏好并入全局设置（仅在全局文件尚未建立时）。

    返回是否真的写入了设置。
    """
    if not data or _existing_settings_file() is not None:
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
        "ignore_speed_groups",
    ):
        setattr(settings, field, getattr(merged, field))
    settings.normalize()
    return True

"""全局“其他设置”：不写进谱面文件，而是存在程序文件夹里的 settings.json。

「视图 / 音效 / 其他设置」这些偏好（缩放、网格、延迟、节拍音与打击音开关、音量、
可配置快捷键、上次谱面绝对路径……）属于使用者，与具体谱面无关，所以保存在独立的
settings.json 里，换谱面 / 新建谱面都沿用同一份。

位置（可用环境变量 INFALSUS_EDITOR_SETTINGS 覆盖，测试里会用到）：

* 源码运行：<仓库根目录>/settings.json
* 打包运行：<exe 所在目录>/settings.json

整个文件夹（连同设置）可以随意拷贝 / 搬迁。
"""

from __future__ import annotations

import os
from typing import Optional

from editor_settings import EditorSettings
from settings_store import app_dir, read_json_object, write_json_object

ENV_SETTINGS_PATH = "INFALSUS_EDITOR_SETTINGS"
SETTINGS_FILENAME = "settings.json"


def settings_path() -> str:
    """全局设置文件路径：程序 / 项目文件夹里的 settings.json（不保证已存在）。"""
    override = os.environ.get(ENV_SETTINGS_PATH)
    if override:
        return override
    return os.path.join(app_dir(), SETTINGS_FILENAME)


def load_settings(path: Optional[str] = None) -> EditorSettings:
    """读取全局设置；文件不存在或损坏时返回默认值（绝不抛异常）。"""
    source = path or settings_path()
    loaded = read_json_object(source)
    data = loaded.get("editor")
    if not isinstance(data, dict):
        data = {}
    return EditorSettings.from_dict(data)


def save_settings(settings: EditorSettings, path: Optional[str] = None) -> str:
    """把全局设置写回磁盘，返回实际写入的位置（尽力而为，失败也不影响使用）。

    写入失败时仍返回目标路径。
    """
    target = path or settings_path()
    settings.normalize()
    data = {
        "format": "infalsus.settings",
        "version": 1,
        "editor": settings.to_dict(),
    }
    write_json_object(target, data)
    return target

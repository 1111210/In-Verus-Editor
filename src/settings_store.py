"""用户配置目录下的 JSON 设置文件：路径解析与容错读写。

编辑器的 settings.json 存在用户目录，损坏 / 缺失 / 只读时一律退回默认值，
绝不能影响启动。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict


def user_config_dir() -> str:
    """用户配置目录：Windows 为 %APPDATA%/InFalsusEditor，其它系统为 ~/.config/infalsus-editor。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming"
        )
        return os.path.join(base, "InFalsusEditor")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "infalsus-editor")


def read_json_object(path: str) -> Dict[str, Any]:
    """读取 JSON 对象；文件缺失、损坏或根节点不是对象时返回空字典。"""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json_object(path: str, data: Dict[str, Any]) -> bool:
    """写出 JSON 对象；目录不存在时自动创建，失败返回 False。"""
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
    except OSError:  # pragma: no cover - 只读目录等
        return False
    return True

"""可写 JSON 设置文件：路径解析与容错读写。

编辑器的 settings.json 放在程序 / 项目文件夹里（见 app_dir），整个文件夹拷贝到
别处也能带着设置一起走。文件损坏 / 缺失 / 只读时一律退回默认值，
绝不能影响启动。
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict


def app_dir() -> str:
    """程序所在目录：源码运行时是仓库根目录，打包运行时是 exe 所在目录。

    用来放跟随程序移动的可写文件（settings.json 等）。该位置与打包资源目录
    分开，始终是可写、可持久化的位置。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))       # src/
    root = os.path.dirname(here)                            # 仓库根目录
    if os.path.isdir(os.path.join(root, "src")):
        return root
    return here                                             # 安装成包时没有 src/ 兄弟目录


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

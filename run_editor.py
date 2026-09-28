#!/usr/bin/env python
"""便捷启动脚本：不需要安装包，直接把 ``src`` 加入搜索路径后启动编辑器。

用法::

    python run_editor.py                  # 空白谱面（默认最大化，F10 还原 / F11 全屏）
    python run_editor.py charts/demo/demo.json
    python run_editor.py charts/demo/demo.json --demo      # 叠加演示模式
    python run_editor.py charts/demo/demo.json --windowed  # 普通窗口
    python run_editor.py charts/demo/demo.json --fullscreen # 无边框全屏
    python run_editor.py --make-demo      # 只生成示例谱面

编辑器**只在项目 ``.venv`` 里运行**：用别的解释器启动时，脚本会自动改用 ``.venv``
里的解释器重新启动自己（``.venv`` 不存在时会打印创建命令并退出，不会去动系统 Python）。
想用别的解释器调试，设 ``VERUS_ALLOW_ANY_PYTHON=1``。
"""

from __future__ import annotations

import os
import sys


def _venv_python(root: str) -> str:
    """项目虚拟环境里的解释器路径（不存在时返回空串）。"""
    src = os.path.join(root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from rhythm_editor.venv_guard import venv_python

    return venv_python(root)


def _has_pygame() -> bool:
    try:
        import pygame  # noqa: F401
    except Exception:
        return False
    return True


def _bootstrap() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(root, "src")

    if src not in sys.path:
        sys.path.insert(0, src)

    # 只用项目 .venv 跑（找不到 .venv 会给出创建命令并退出）
    from rhythm_editor.venv_guard import ensure_project_venv

    ensure_project_venv(entry=os.path.abspath(__file__))


def main() -> int:
    _bootstrap()
    from rhythm_editor.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())

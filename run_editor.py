#!/usr/bin/env python
"""便捷启动脚本：不需要安装包，直接把 ``src`` 加入搜索路径后启动编辑器。

用法::

    python run_editor.py                  # 空白谱面（默认最大化，F10 还原 / F11 全屏）
    python run_editor.py charts/demo/demo.json
    python run_editor.py charts/demo/demo.json --demo      # 叠加演示模式
    python run_editor.py charts/demo/demo.json --windowed  # 普通窗口
    python run_editor.py charts/demo/demo.json --fullscreen # 无边框全屏
    python run_editor.py --make-demo      # 只生成示例谱面

如果当前解释器没有装 ``pygame``，而项目里存在 ``.venv``，脚本会**自动改用
``.venv`` 里的解释器重新启动自己**（音频后端需要它；设 ``VERUS_NO_VENV=1`` 可关闭）。
"""

from __future__ import annotations

import os
import subprocess
import sys


def _venv_python(root: str) -> str:
    """项目虚拟环境里的解释器路径（不存在时返回空串）。"""
    if sys.platform == "win32":
        candidate = os.path.join(root, ".venv", "Scripts", "python.exe")
    else:
        candidate = os.path.join(root, ".venv", "bin", "python")
    return candidate if os.path.isfile(candidate) else ""


def _restart_with_venv(root: str) -> bool:
    """当前解释器没有 pygame 时，改用项目 ``.venv`` 重新启动。

    返回 ``True`` 表示已经交给子进程（调用方应当直接退出）。
    """
    if os.environ.get("VERUS_NO_VENV") or os.environ.get("VERUS_VENV_REEXEC"):
        return False
    interpreter = _venv_python(root)
    if not interpreter:
        return False
    try:
        if os.path.samefile(interpreter, sys.executable):
            return False
    except OSError:  # pragma: no cover - 极端情况下拿不到信息
        pass
    if _has_pygame():
        return False
    env = dict(os.environ, VERUS_VENV_REEXEC="1")
    print(
        f"[run_editor] 当前解释器 {sys.executable} 里没有 pygame，"
        f"改用项目虚拟环境：{interpreter}",
        file=sys.stderr,
    )
    try:
        completed = subprocess.run([interpreter, os.path.abspath(__file__), *sys.argv[1:]], env=env)
    except OSError as exc:  # pragma: no cover - 虚拟环境损坏
        print(f"[run_editor] 启动虚拟环境失败（{exc}），继续使用当前解释器。", file=sys.stderr)
        return False
    raise SystemExit(completed.returncode)


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

    _restart_with_venv(root)


def main() -> int:
    _bootstrap()
    from rhythm_editor.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())

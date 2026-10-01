"""``tools/`` 下脚本共用的引导与环境设置。

统一处理三件事：

* 把 ``src`` 与 ``tools`` 加进搜索路径 —— 脚本无需安装包即可运行；
* 无界面脚本使用 SDL 的 dummy 驱动，并把玩家设置写到临时文件，
  不读写用户目录、不弹窗口；
* Windows 控制台输出统一为 UTF-8（默认 GBK 打印 ``−`` 等符号会崩溃）。
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
TOOLS = os.path.dirname(os.path.abspath(__file__))


def add_paths() -> None:
    """把 ``src`` / ``tools`` 加进 ``sys.path``（可重复调用）。"""
    if SRC not in sys.path:
        sys.path.insert(0, SRC)
    if TOOLS not in sys.path:
        sys.path.append(TOOLS)


def use_utf8_console() -> None:
    """把标准输出 / 错误输出切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):  # pragma: no cover - 非文本流 / 老 Python
            pass


def use_dummy_video() -> None:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


def use_dummy_audio() -> None:
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def use_isolated_player_settings(name: str = "tool") -> str:
    """把游玩器设置指向临时文件，返回该路径。"""
    path = os.path.join(tempfile.gettempdir(), f"inverus_{name}.json")
    os.environ.setdefault("VERUS_PLAYER_SETTINGS", path)
    return path


def setup_offscreen(name: str = "tool") -> None:
    """无界面脚本的标准初始化：路径 + UTF-8 + dummy 视频 / 音频 + 隔离设置。"""
    add_paths()
    use_utf8_console()
    use_dummy_video()
    use_dummy_audio()
    use_isolated_player_settings(name)

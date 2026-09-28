"""In Verus —— 双平面音游谱面编辑器（6K 下落式 + 鼠标平面）。

本项目当前只实现 **编辑器**（不含播放器 / 游玩器）。
核心包只依赖 Python 标准库；安装了 ``pygame-ce`` 时会自动启用真实音频后端。
"""

from __future__ import annotations

__all__ = ["__version__", "APP_NAME"]

APP_NAME = "In Verus"
__version__ = "0.1.0"

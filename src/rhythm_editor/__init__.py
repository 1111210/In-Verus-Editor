"""In Verus —— 双平面音游的谱面编辑器与 3D 游玩器（6K 下落式 + 鼠标平面）。

编辑器核心只依赖 Python 标准库；安装了 ``pygame-ce`` 时自动启用真实音频后端，
游玩器使用 pygame 绘制，并可用 Panda3D / OpenGL 加速。
"""

from __future__ import annotations

__all__ = ["__version__", "APP_NAME"]

APP_NAME = "In Verus"
__version__ = "0.1.0"

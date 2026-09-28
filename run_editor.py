#!/usr/bin/env python
"""便捷启动脚本：不需要安装包，直接把 ``src`` 加入搜索路径后启动编辑器。

用法::

    python run_editor.py                  # 空白谱面（默认全屏，F11 切换）
    python run_editor.py charts/demo/demo.ivchart
    python run_editor.py charts/demo/demo.ivchart --demo      # 叠加演示模式
    python run_editor.py charts/demo/demo.ivchart --windowed  # 窗口模式
    python run_editor.py --make-demo      # 只生成示例谱面
"""

from __future__ import annotations

import os
import sys


def _bootstrap() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def main() -> int:
    _bootstrap()
    from rhythm_editor.app import main as app_main

    return app_main()


if __name__ == "__main__":
    raise SystemExit(main())

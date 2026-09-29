#!/usr/bin/env python
"""启动**游玩器**（3D 视角）。用法::

    python run_player.py                          # 列出 charts/ 里的谱面
    python run_player.py charts/demo/demo.json    # 直接进主界面并选中它

主界面：↑↓ 选谱面 · Enter 开始 · S 设置 · Esc 退出。
设置保存在用户目录的 `player.json`（全局，不写进谱面）。
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    root = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(root, "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from rhythm_editor.game.app import main as player_main

    return player_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

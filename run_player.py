#!/usr/bin/env python
"""启动**游玩器**（3D 视角）。用法::

    python run_player.py                          # 列出 charts/ 里的谱面
    python run_player.py charts/demo              # 直接选中该谱面文件夹
    python run_player.py charts/demo/demo.json    # 直接选中某个谱面文件

charts/ 下的**每个含 json 的文件夹**算一张谱面（文件夹里有多个 json 时取主谱面）。
主界面：↑↓ 选择 · Enter 确认 · O 打开其他路径的 JSON · S 设置 · Esc 退出。
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

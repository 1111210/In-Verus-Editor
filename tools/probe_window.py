"""探测「Panda 渲染时窗口尺寸变化」：打印建 3D 前后、跑帧过程中的窗口尺寸。

用法:: python tools/probe_window.py [谱面路径]
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault(
    "VERUS_PLAYER_SETTINGS", os.path.join(tempfile.gettempdir(), "inverus_probe.json")
)

import pygame  # noqa: E402

from rhythm_editor.game.app import PlayerApp  # noqa: E402


def report(app, tag: str) -> None:
    win = pygame.display.get_window_size()
    surface = pygame.display.get_surface()
    flags = surface.get_flags() if surface is not None else 0
    print(f"[{tag}] 窗口 {win} · screen {app.screen.get_size()} · SDL 标志 0x{flags:x}",
          flush=True)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chart = args[0] if args else os.path.join(ROOT, "charts", "demo", "demo.json")
    app = PlayerApp([chart])
    report(app, "启动")
    if "--max" in sys.argv:                        # 先最大化，再看建 3D 会不会把它打回去
        app._maximize_window()
        for _ in range(20):
            app._events()
            time.sleep(0.01)
        report(app, "F10 最大化后")
    app._begin_chart(chart)
    app._start_play()
    app.timer = 9.0
    report(app, "开局（还没建 3D）")
    app._draw()                       # 第一次画：会创建 Panda 场景
    report(app, "建了 3D 之后")
    base = getattr(app.scene3d, "base", None)
    if base is not None:
        print("    Panda 窗口数 =", base.graphicsEngine.getNumWindows(),
              "· buffer", app.scene3d.buffer.getXSize(), "x", app.scene3d.buffer.getYSize(),
              flush=True)
    for index in range(120):
        app._events()
        app._update(1 / 60.0)
        app._draw()
        app._present()
        if index % 30 == 29:
            report(app, f"第 {index + 1} 帧")
        time.sleep(0.002)
    if "--switch" in sys.argv:                     # F2 轮换渲染后端时窗口不该变
        for _ in range(3):
            app._switch_renderer()
            for _ in range(3):
                app._events()
                app._draw()
                app._present()
            report(app, f"切到 {app.renderer_name}")
    app.audio.unload()
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

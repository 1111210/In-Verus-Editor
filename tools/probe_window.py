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
    print(f"[{tag}] 窗口 {win} · screen {app.screen.get_size()} · SDL 标志 0x{flags:x}"
          f" · 位置 {window_rect()}", flush=True)


def window_rect():
    """Windows 下取窗口的 (left, top, width, height)，其它平台返回 None。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        hwnd = pygame.display.get_wm_info().get("window")
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(rect))
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:                              # pragma: no cover - 依赖系统
        return None


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chart = args[0] if args else os.path.join(ROOT, "charts", "demo", "demo.json")
    app = PlayerApp([chart])
    report(app, "启动（应已按屏幕尺寸最大化）")
    print(f"    屏幕尺寸 {app.screen_size} · 基准尺寸 {app.base_size} · "
          f"最大化={app.maximized}", flush=True)
    if "--max" in sys.argv:                        # 先最大化，再看建 3D 会不会把它打回去
        app._maximize_window()
        for _ in range(20):
            app._events()
            time.sleep(0.01)
        report(app, "F10 最大化后")
    app._begin_chart(chart)
    report(app, "进入加载页（还没建 3D）")
    for index in range(150):          # 走真实状态机：加载页 → 游玩
        app._events()
        app._update(1 / 60.0)
        app._draw()
        app._present()
        if index % 10 == 0 or index in (1, 2, 3):
            report(app, f"第 {index:3d} 帧 · state={app.state}")
            if app.state == "play" and index > 90:
                break
    report(app, "进入游玩后")
    if "--poke" in sys.argv:                       # 模拟"外部程序"悄悄改窗口
        import ctypes

        hwnd = int(pygame.display.get_wm_info().get("window") or 0)
        rect = window_rect()
        if hwnd and rect:
            ctypes.windll.user32.SetWindowPos(hwnd, 0, rect[0] + 40, rect[1] + 40,
                                              rect[2] - 200, rect[3] - 120, 0x0014)
            report(app, "被人偷偷改小后（还没跑帧）")
            for _ in range(30):
                app._events()
                app._update(1 / 60.0)
                app._draw()
                app._present()
            report(app, "跑 30 帧后（应当已还原）")
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

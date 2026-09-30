"""用真实谱面 + 用户设置渲染若干帧（人眼检查条纹 / 消失 / 空域）。

用法::

    python tools/shot_panda_frames.py [输出目录] [时间毫秒 ...]
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault(
    "VERUS_PLAYER_SETTINGS", os.path.join(tempfile.gettempdir(), "inverus_shot.json")
)

import pygame  # noqa: E402

from rhythm_editor.game import render3d  # noqa: E402
from rhythm_editor.game.app import PlayerApp  # noqa: E402
from rhythm_editor.io.chart_io import load_chart  # noqa: E402
from rhythm_editor.model.chart import Chart  # noqa: E402
from rhythm_editor.model.notes import MouseSlide  # noqa: E402


def slide_demo(out_dir: str) -> int:
    """滑键贴图放大检查：一条向右、一条向左，厚度调大，贴近判定线。"""
    os.makedirs(out_dir, exist_ok=True)
    app = PlayerApp([])
    app.screen = pygame.display.set_mode((1280, 744))
    app.scene3d = render3d.Scene3D(app.w, app.h, app.texture_dir)
    app.chart = Chart()
    app.chart.mouse_notes = [
        MouseSlide("M1", 0.0, 0.15, 0.85),      # 向右
        MouseSlide("M2", 0.5, 0.85, 0.15),      # 向左
    ]
    app.chart.invalidate_order()
    app.settings.slide_thickness = 0.35
    app.settings.rate = 1.0
    try:
        for name, ms in (("slide_near", -60.0), ("slide_mid", -300.0)):
            path = os.path.join(out_dir, f"{name}.png")
            pygame.image.save(app._render_scene3d(ms), path)
            print("saved", path)
    finally:
        app.scene3d.close()
    return 0


def main() -> int:
    out_dir = (sys.argv[1] if len(sys.argv) > 1
               else os.path.join(tempfile.gettempdir(), "verus_shots"))
    if len(sys.argv) > 2 and sys.argv[2] == "slide":
        return slide_demo(out_dir)
    times = [float(v) for v in sys.argv[2:]] or [
        14000.0, 14300.0, 14600.0, 15000.0, 15500.0, 16000.0, 30000.0, 45000.0,
    ]
    os.makedirs(out_dir, exist_ok=True)
    app = PlayerApp([])
    app.screen = pygame.display.set_mode((1280, 744))
    app.scene3d = render3d.Scene3D(app.w, app.h, app.texture_dir)
    app.chart, _ = load_chart(os.path.join(ROOT, "charts", "test", "chart.json"))
    app.chart.invalidate_order()
    s = app.settings
    s.rate, s.speed, s.autoplay = 0.4, 2.6, True
    s.side_lane_highlight, s.note_thickness, s.multi_thickness = True, 0.3, 0.3
    s.slide_thickness, s.click_thickness = 0.1, 0.1
    try:
        for now in times:
            path = os.path.join(out_dir, f"play_{now:07.0f}.png")
            pygame.image.save(app._render_scene3d(now), path)
            print("saved", path)
    finally:
        app.scene3d.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""启动游玩器、脚本化操作并截图（用于验收与文档配图）。

用法::

    python tools/screenshot_player.py [输出目录]

生成：``player_menu.png`` / ``player_settings.png`` / ``player_play.png`` / ``player_result.png``
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault(
    "VERUS_PLAYER_SETTINGS", os.path.join(tempfile.gettempdir(), "inverus_player_shot.json")
)

import pygame  # noqa: E402

from rhythm_editor.game.app import PlayerApp  # noqa: E402
from winshot import capture_to_png_smart  # noqa: E402


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "images")
    os.makedirs(out_dir, exist_ok=True)
    chart = os.path.join(ROOT, "charts", "demo", "demo.json")
    app = PlayerApp([chart])
    hwnd = pygame.display.get_wm_info().get("window")
    taken = []

    def shot(name: str) -> None:
        if hwnd:
            path = os.path.join(out_dir, name)
            capture_to_png_smart(path, hwnd)
            taken.append(name)

    def pump(seconds: float, draw) -> None:
        end = time.time() + seconds
        while time.time() < end:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    app.running = False
            app.screen.fill((10, 10, 14))
            draw()
            pygame.display.flip()
            time.sleep(1 / 60)

    pump(0.6, app._draw_menu)
    shot("player_menu.png")
    app.state = "settings"
    pump(0.6, app._draw_settings)
    shot("player_settings.png")
    app._enter_loading()
    app._start_play()
    app.timer = 2.0
    pump(1.5, app._draw_play)
    shot("player_play.png")
    app._finish()
    pump(0.6, app._draw_result)
    shot("player_result.png")
    app.audio.unload()
    pygame.quit()
    print("[done]", ", ".join(taken))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

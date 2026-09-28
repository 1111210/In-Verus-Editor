"""启动编辑器、执行脚本化操作并截图（用于验收与文档配图）。

用法::

    python tools/screenshot_editor.py [输出目录]

会在输出目录生成：

* ``editor_overview.png``  —— 编辑器主界面（左右分栏、分组工具栏）
* ``placement.png``        —— 放置预览（长按的幽灵物件与提示）
* ``demo_mode.png``        —— 演示模式（两个平面叠加）
* ``maximized_editor.png`` —— 默认启动状态（窗口最大化）
* ``fullscreen_editor.png``—— 无边框全屏（F11）
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rhythm_editor import config  # noqa: E402
from rhythm_editor.app import EditorApp  # noqa: E402
from rhythm_editor.demo import write_demo_chart  # noqa: E402
from winshot import capture_to_png_smart  # noqa: E402


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "images")
    os.makedirs(out_dir, exist_ok=True)
    chart_path = write_demo_chart()

    # 截图用窗口模式，尺寸固定，方便对比；最大化 / 全屏状态在后面单独截图
    app = EditorApp(
        chart_path, demo=False, width=1280, height=800, maximized=False, fullscreen=False
    )
    root = app.root
    root.geometry("1280x800+60+30")
    app.document.chart.settings.zoom_seconds = 6.0
    app.view.set_beat(20.0)
    taken = []

    def shot(name: str) -> None:
        # 截图前把窗口抬到最前，避免被其它窗口遮挡
        try:
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.focus_force()
        except Exception:  # pragma: no cover - 依赖窗口管理器
            pass
        for _ in range(12):
            root.update()
            time.sleep(0.02)
        root.update_idletasks()
        root.update()
        try:
            width, height, method = capture_to_png_smart(os.path.join(out_dir, name), root.winfo_id())
            print(f"[shot] {name}: {width}x{height} via {method}")
        except Exception as exc:  # pragma: no cover - 依赖桌面会话
            print(f"[shot] {name} 失败：{exc}")
        taken.append(name)

    def step_overview() -> None:
        app.set_tool(config.TOOL_LANE_TAP)
        app.document.select_only([n.id for n in app.document.chart.mouse_notes][:1])
        root.after(250, lambda: (shot("editor_overview.png"), root.after(200, step_placement)))

    def step_placement() -> None:
        layout = app.view.layout
        app.set_tool(config.TOOL_LANE_HOLD)
        app.controller.on_press(layout.lane_center(2), app.view.beat_to_y(22.0), False, False)
        app.controller.on_motion(layout.lane_center(2), app.view.beat_to_y(17.5))
        root.after(250, lambda: (shot("placement.png"), root.after(200, step_demo)))

    def step_demo() -> None:
        app.controller.cancel_pending()
        app.set_tool(config.TOOL_SELECT)
        app.set_demo_mode(True)
        root.after(250, lambda: (shot("demo_mode.png"), root.after(200, step_fullscreen)))

    def step_fullscreen() -> None:
        app.set_demo_mode(False)
        app.toggle_maximize()
        root.after(500, lambda: (shot("maximized_editor.png"), root.after(200, step_true_fullscreen)))

    def step_true_fullscreen() -> None:
        app.toggle_fullscreen()
        root.after(400, lambda: (shot("fullscreen_editor.png"), root.after(200, finish)))

    def finish() -> None:
        try:
            root.attributes("-topmost", False)
        except Exception:  # pragma: no cover
            pass
        print("[done] " + ", ".join(taken))
        root.destroy()

    root.after(600, step_overview)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

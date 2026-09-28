"""验证菜单频闪修复（全屏/最大化时展开二级菜单不再抖动）。

用法::

    python tools/check_menu_flicker.py

频闪的两个根因与对应修复：

1. **每帧都在改写菜单项**（`entryconfigure` / 复选变量）—— 展开中的菜单被反复重绘；
   现在 `Toolbar.sync()` 只在状态真的变化时才写入。
2. **菜单展开时画布仍在重绘**（还会每帧重设窗口标题）—— Windows 上 Tk 用的是原生
   弹出菜单，画布/窗口的重绘会破坏菜单显示；现在菜单展开期间暂停画布重绘，
   **没有任何保护时长**：菜单一关闭（或用户继续操作）立刻恢复绘制。

脚本打开一个真实窗口（默认最大化）并模拟上述两种状态，检查：
菜单项不会被每帧改写、菜单展开期间帧数不增长、继续操作后恢复绘制。
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor.app import EditorApp  # noqa: E402
from rhythm_editor.demo import write_demo_chart  # noqa: E402


def main() -> int:
    chart = write_demo_chart()
    app = EditorApp(chart, maximized=True, fullscreen=False)
    root = app.root
    result: dict = {}
    toolbar = app.toolbar
    menu = toolbar._menus["视图"]
    result["entry_calls"] = 0
    original_entryconfigure = menu.entryconfigure

    def counting_entryconfigure(*args, **kwargs):
        result["entry_calls"] += 1
        return original_entryconfigure(*args, **kwargs)

    menu.entryconfigure = counting_entryconfigure  # type: ignore[assignment]

    def post_menu() -> None:
        result["frames_before"] = app.scene.frames
        toolbar.mark_menu_open()          # 等价于用户点开二级菜单
        root.update()
        result["posted_flag"] = toolbar.is_menu_posted()
        result["frames_at_open"] = app.scene.frames
        result["calls_at_open"] = result["entry_calls"]
        root.after(800, close_menu)

    def close_menu() -> None:
        result["frames_during"] = app.scene.frames
        result["calls_during"] = result["entry_calls"]
        time.sleep(0.6)
        toolbar.mark_interaction()        # 等价于用户回到画布继续操作
        root.after(500, finish)

    def finish() -> None:
        result["frames_after"] = app.scene.frames
        root.destroy()

    root.after(800, post_menu)
    app.run()

    checks = [
        ("菜单展开状态可被检测", bool(result.get("posted_flag")), "is_menu_posted() = True"),
        (
            "展开期间画布不再重绘",
            result.get("frames_during") == result.get("frames_at_open"),
            f"{result.get('frames_at_open')} -> {result.get('frames_during')} 帧",
        ),
        (
            "菜单项不会被每帧改写",
            (result.get("calls_during") or 0) == (result.get("calls_at_open") or 0),
            f"entryconfigure 调用 {result.get('calls_at_open')} -> {result.get('calls_during')}",
        ),
        (
            "关闭菜单后恢复绘制",
            (result.get("frames_after") or 0) > (result.get("frames_at_open") or 0),
            f"{result.get('frames_at_open')} -> {result.get('frames_after')} 帧",
        ),
    ]
    menu.entryconfigure = original_entryconfigure  # type: ignore[assignment]
    failures = []
    for text, ok, detail in checks:
        print(f"[{'OK ' if ok else 'NG '}] {text} -- {detail}")
        if not ok:
            failures.append(text)
    print()
    print("结论：" + ("全部通过" if not failures else f"未通过 {len(failures)} 项"))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

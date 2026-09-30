"""校验游玩器渲染层现状（Panda3D 3D + pygame HUD）。

用法::

    python tools/verify_player_render.py [宽 高]

打印：画面尺寸、非背景像素占比、两条判定线的屏幕行与软件投影的对比、
世界→屏幕投影是否可用、鼠标指针是否真的进了场景。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def brightest_row(surface, width: int, height: int, predicate) -> int:
    best_row, best_count = -1, -1
    for y in range(height):
        count = sum(
            1 for x in range(0, width, 4) if predicate(surface.get_at((x, y)))
        )
        if count > best_count:
            best_row, best_count = y, count
    return best_row


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 2 else 1280
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 744
    from rhythm_editor.game import render3d, scene as soft_scene

    print("== 渲染层现状校验 ==")
    print(f"Panda3D 可用: {render3d.available()} {render3d.import_error()}")
    if not render3d.available():
        return 1
    texture_dir = os.path.join(ROOT, "assets", "textures", "player")
    scene3d = render3d.Scene3D(width, height, texture_dir)
    try:
        scene3d.begin()
        scene3d.quad("tap_1", 0.0, 4.0, 0.25, 0.9, 0.0)          # 远处一个单键
        base = scene3d.render()
        print(f"离屏画面尺寸: {base.get_size()}（期望 {(width, height)}）")

        def not_background(pixel) -> bool:
            return abs(pixel[0] - 10) + abs(pixel[1] - 10) + abs(pixel[2] - 14) > 18

        total = sum(1 for _ in range(0, width * height, 97))
        lit = sum(
            1
            for index in range(0, width * height, 97)
            if not_background(base.get_at((index % width, index // width)))
        )
        print(f"非背景像素占比: {lit / max(1, total) * 100:.1f}%")

        k6_row = brightest_row(base, width, height,
                               lambda p: p[0] > 200 and p[1] > 200 and p[2] > 200)
        mouse_row = brightest_row(base, width, height,
                                  lambda p: p[2] > 180 and p[0] < 160 and p[1] > 120)
        soft = soft_scene.PlayerScene()
        soft_lines = soft.judgment_lines(width, height)
        print(f"6K 判定线：Panda 行 {k6_row} / 软件 {soft_lines['k6']:.0f}")
        print(f"鼠标判定线：Panda 行 {mouse_row} / 软件 {soft_lines['mouse']:.0f}")

        point = scene3d.project(0.0, 0.4, 0.0)
        print(f"世界→屏幕投影: {'OK ' + str(tuple(round(v, 1) for v in point)) if point else '不可用（HUD 锚点会退化到屏幕中心）'}")

        scene3d.begin()
        without = scene3d.render()
        scene3d.begin()
        scene3d.mouse_marker(0.5, 0.4, 0.03)
        with_marker = scene3d.render()
        diff = 0
        for index in range(0, width * height, 37):
            px, py = index % width, index // width
            if without.get_at((px, py)) != with_marker.get_at((px, py)):
                diff += 1
        print(f"鼠标指针（场景内元素）生效: {'是' if diff else '否'}（差异采样点 {diff}）")
    finally:
        scene3d.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""标定 Panda3D 相机：让渲染出来的两条判定线行号与软件版一致。

用法::

    python tools/calibrate_panda_camera.py [宽 高]

软件版（已验证的目标）：6K 判定线行 734、鼠标判定线行 506（1280x744）。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def detect_lines(surface, width: int, height: int):
    """返回 (白线行, 青线行)：分别是白色 / 青色像素最多的行。"""
    white_best = (-1, -1)
    cyan_best = (-1, -1)
    for y in range(height):
        white = cyan = 0
        for x in range(0, width, 3):
            r, g, b, *_ = surface.get_at((x, y))
            if r > 200 and g > 200 and b > 200:
                white += 1
            elif r < 170 and g > 160 and b > 190:
                cyan += 1
        if white > white_best[1]:
            white_best = (y, white)
        if cyan > cyan_best[1]:
            cyan_best = (y, cyan)
    return white_best[0], cyan_best[0]


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 2 else 1280
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 744
    from rhythm_editor.game import render3d, scene as soft

    targets = soft.PlayerScene().judgment_lines(width, height)
    target_k6 = round(targets["k6"])
    target_mouse = round(targets["mouse"])
    print(f"目标（软件版）：6K 行 {target_k6} / 鼠标行 {target_mouse}")
    texture_dir = os.path.join(ROOT, "assets", "textures", "player")
    scene3d = render3d.Scene3D(width, height, texture_dir, decorations=False)
    best = None
    first = True
    try:
        for fov_v in (40, 55, 70, 85):
            for pitch in (14, 18, 22):
                for back in (1.7, 2.2):
                    scene3d.set_camera(fov=fov_v, pitch=pitch, back=back, height=1.15)
                    scene3d.begin()
                    frame = scene3d.render()
                    k6_row, mouse_row = detect_lines(frame, width, height)
                    pred_k6 = scene3d.project(0.0, 0.0, 0.0)
                    pred_mu = scene3d.project(0.0, 0.4, 0.0)
                    if first:
                        print(f'  预测（自己的公式）：6K {pred_k6[1]:.0f} / 鼠标 {pred_mu[1]:.0f}')
                        first = False
                    if k6_row < 0 or mouse_row < 0:
                        continue
                    err = abs(k6_row - target_k6) + abs(mouse_row - target_mouse)
                    if best is None or err < best[0]:
                        best = (err, fov_v, pitch, back, k6_row, mouse_row)
                        print(f"fov={fov_v} pitch={pitch} back={back} -> "
                              f"6K {k6_row} / 鼠标 {mouse_row}（误差 {err}）")
    finally:
        scene3d.close()
    if best is None:
        print("[FAIL] 没有找到可用组合")
        return 1
    print(f"[OK ] 最佳：fov={best[1]} pitch={best[2]} back={best[3]} "
          f"-> 6K {best[4]} / 鼠标 {best[5]}（误差 {best[0]}px）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

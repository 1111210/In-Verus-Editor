"""按“画面构图贴近软件版”标定 Panda3D 相机。

做法：用软件投影算出 6K 平面 / 鼠标平面各 4 个角点的**目标屏幕坐标**，
再在 Panda 渲染里放 8 个醒目标记方块、检测它们的实际屏幕坐标，搜索相机参数最小化误差。
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 2 else 1280
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 744
    from rhythm_editor.game import render3d, scene as soft

    scene_soft = soft.PlayerScene()
    plane_width = soft.LANE_COUNT * soft.LANE_WIDTH if hasattr(soft, "LANE_COUNT") else 1.5
    references = [
        (-plane_width / 2, 0.0, soft.START_Z), (plane_width / 2, 0.0, soft.START_Z),
        (-plane_width / 2, 0.0, 0.0), (plane_width / 2, 0.0, 0.0),
        (soft.MOUSE_LEFT, soft.MOUSE_PLANE_HEIGHT, soft.START_Z),
        (soft.MOUSE_RIGHT, soft.MOUSE_PLANE_HEIGHT, soft.START_Z),
        (soft.MOUSE_LEFT, soft.MOUSE_PLANE_HEIGHT, 0.0),
        (soft.MOUSE_RIGHT, soft.MOUSE_PLANE_HEIGHT, 0.0),
    ]
    targets = [scene_soft.point(x, y, z, width, height)[:2] for x, y, z in references]

    texture_dir = os.path.join(ROOT, "assets", "textures", "player")
    scene = render3d.Scene3D(width, height, texture_dir, decorations=False)
    marker_size = 0.16
    try:
        def measure():
            """放 8 个品红标记方块 -> 渲染 -> 检测质心。"""
            scene.begin()
            for x, y, z in references:
                scene.quad("", x, z, marker_size, marker_size, y, (1.0, 0.0, 1.0, 1.0))
            frame = scene.render()
            found = []
            for index in range(len(references)):
                found.append([0.0, 0.0, 0])
            for py in range(height):
                for px in range(0, width, 2):
                    r, g, b, *_ = frame.get_at((px, py))
                    if r > 150 and b > 150 and g < 100:
                        # 归到最近的参考点（用粗略位置区分：z=0 的行更靠下）
                        best = None
                        for i, (x, y, z) in enumerate(references):
                            tx, ty = targets[i]
                            d = abs(px - tx) + abs(py - ty)
                            if best is None or d < best[0]:
                                best = (d, i)
                        i = best[1]
                        found[i][0] += px
                        found[i][1] += py
                        found[i][2] += 1
            result = []
            for fx, fy, count in found:
                result.append(None if count == 0 else (fx / count, fy / count))
            return result

        best = None
        for fov in (30, 36, 42, 48, 54):
            for pitch in (6, 12, 18, 24, 30):
                for back in (1.0, 1.4, 1.8, 2.2):
                    for cam_height in (0.8, 1.15, 1.5, 2.0):
                        scene.set_camera(fov=fov, pitch=pitch, back=back, height=cam_height)
                        found = measure()
                        if any(point is None for point in found):
                            continue
                        err = sum(
                            abs(fx - tx) + abs(fy - ty)
                            for (fx, fy), (tx, ty) in zip(found, targets)
                        ) / len(targets)
                        if best is None or err < best[0]:
                            best = (err, fov, pitch, back, cam_height)
                            print(f"  fov={fov} pitch={pitch} back={back} height={cam_height}"
                                  f" -> 平均误差 {err:.1f}px")
        print("最佳（平均误差, fov, pitch, back, height）:", best)
    finally:
        scene.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""把 Panda 相机重新标定到软件版构图（解析法，不渲染）。

做法：软件投影给出 8 个角点的目标屏幕坐标；Panda 侧直接用
``Scene3D.project()``（内部走官方 ``Lens.project``，与渲染实测差 <1px）
算同一点的屏幕坐标，搜索相机参数最小化平均误差。

用法::  python tools/fit_panda_composition2.py [宽 高]
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1920
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 991
    from rhythm_editor.game import render3d, scene as soft

    scene_soft = soft.PlayerScene()
    plane_width = soft.LANE_COUNT * soft.LANE_WIDTH
    references = [
        (-plane_width / 2, 0.0, soft.START_Z), (plane_width / 2, 0.0, soft.START_Z),
        (-plane_width / 2, 0.0, 0.0), (plane_width / 2, 0.0, 0.0),
        (soft.MOUSE_LEFT, soft.MOUSE_PLANE_HEIGHT, soft.START_Z),
        (soft.MOUSE_RIGHT, soft.MOUSE_PLANE_HEIGHT, soft.START_Z),
        (soft.MOUSE_LEFT, soft.MOUSE_PLANE_HEIGHT, 0.0),
        (soft.MOUSE_RIGHT, soft.MOUSE_PLANE_HEIGHT, 0.0),
        # 两条判定线（6K / 鼠标）也要对上，并且**必须留在屏幕内**
        (0.0, 0.0, 0.0),
        (0.0, soft.MOUSE_PLANE_HEIGHT, 0.0),
    ]
    targets = [scene_soft.point(x, y, z, width, height)[:2] for x, y, z in references]

    texture_dir = os.path.join(ROOT, "assets", "textures", "player")
    scene = render3d.Scene3D(width, height, texture_dir, decorations=False)
    try:
        def error(fov: float, pitch: float, back: float, cam_height: float):
            scene.set_camera(fov=fov, pitch=pitch, back=back, height=cam_height)
            total = 0.0
            for (x, y, z), (tx, ty) in zip(references, targets):
                got = scene.project(x, y, z)
                if got is None:
                    return None
                # 判定线 / 角点都不能跑出屏幕（留 6px 边距）
                if not (6.0 <= got[1] <= height - 6.0):
                    return None
                total += abs(got[0] - tx) + abs(got[1] - ty)
            return total / len(references)

        best = None
        for fov in range(24, 61, 3):
            for pitch in range(10, 31, 2):
                for back in [1.4 + 0.3 * i for i in range(9)]:
                    for cam_height in [1.0 + 0.2 * i for i in range(8)]:
                        err = error(float(fov), float(pitch), back, cam_height)
                        if err is None:
                            continue
                        if best is None or err < best[0]:
                            best = (err, float(fov), float(pitch), back, cam_height)
        print("粗搜最佳（误差, fov, pitch, back, height）:", best)
        # 细化
        err, fov, pitch, back, cam_height = best
        for _round in range(4):
            step = (2.0, 1.0, 0.15, 0.1)
            improved = False
            for df in (-1, 0, 1):
                for dp in (-1, 0, 1):
                    for db in (-1, 0, 1):
                        for dh in (-1, 0, 1):
                            cand = (fov + df * step[0], pitch + dp * step[1],
                                    back + db * step[2], cam_height + dh * step[3])
                            if min(cand[0], cand[1], cand[2], cand[3]) <= 0:
                                continue
                            got = error(*cand)
                            if got is not None and got < err:
                                err, (fov, pitch, back, cam_height) = got, cand
                                improved = True
            if not improved:
                break
        print(f"细化最佳：fov={fov:.1f} pitch={pitch:.1f} back={back:.2f} "
              f"height={cam_height:.2f} -> 平均误差 {err:.2f}px")
    finally:
        scene.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

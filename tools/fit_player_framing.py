"""按需求挑软件路径的相机与轨道长度（纯数学搜索，不渲染）。

目标：

* **近端尺寸不变**（判定线附近的按键看上去与现在差不多）→ 单轨按键在 z=0 的屏幕宽度；
* **远端更小**（刚出现时显得小）→ 同一段宽度在 z=START_Z 的屏幕宽度，目标约为现在的 0.6；
* **两条判定线更近**（鼠标平面与 6K 平面看上去距离更近）→ 目标约为现在间距的 0.7；
* 相机比现在更靠后更高（back↑ height↑），轨道更长（START_Z ≥ 18）。

用法::  python tools/fit_player_framing.py [宽 高]
"""

from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor.game import scene as S  # noqa: E402


def measure(camera: "S.Camera", start_z: float, width: int, height: int,
            x_scale: float = 1.0) -> dict:
    """给定相机/轨道长度，量出关心的几个屏幕尺寸。"""
    saved = S.START_Z
    saved_x = (S.LANE_WIDTH, S.K6_WIDTH, S.K6_LEFT, S.MOUSE_LEFT, S.MOUSE_RIGHT,
               S.MOUSE_LEGAL_WIDTH)
    S.START_Z = start_z
    if x_scale != 1.0:                              # 把"轨道宽度"整体放大 x_scale 倍
        S.LANE_WIDTH *= x_scale
        S.K6_WIDTH *= x_scale
        S.K6_LEFT *= x_scale
        S.MOUSE_LEFT *= x_scale
        S.MOUSE_RIGHT *= x_scale
        S.MOUSE_LEGAL_WIDTH *= x_scale
    try:
        player = S.PlayerScene(camera)
        lane_left = S.K6_LEFT
        lane_right = S.K6_LEFT + S.LANE_WIDTH
        near = (player.point(lane_left, 0.0, 0.0, width, height)[0],
                player.point(lane_right, 0.0, 0.0, width, height)[0])
        far = (player.point(lane_left, 0.0, start_z, width, height)[0],
               player.point(lane_right, 0.0, start_z, width, height)[0])
        mouse_near = (player.point(S.MOUSE_LEFT, S.MOUSE_PLANE_HEIGHT, 0.0, width, height)[0],
                      player.point(S.MOUSE_RIGHT, S.MOUSE_PLANE_HEIGHT, 0.0, width, height)[0])
        lines = player.judgment_lines(width, height)
        return {
            "near_note": abs(near[1] - near[0]),
            "far_note": abs(far[1] - far[0]),
            "mouse_near": abs(mouse_near[1] - mouse_near[0]),
            "k6_line": lines["k6"],
            "mouse_line": lines["mouse"],
            "separation": lines["k6"] - lines["mouse"],
        }
    finally:
        S.START_Z = saved
        (S.LANE_WIDTH, S.K6_WIDTH, S.K6_LEFT, S.MOUSE_LEFT, S.MOUSE_RIGHT,
         S.MOUSE_LEGAL_WIDTH) = saved_x


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1920
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 991
    base_cam = S.Camera()
    base = measure(base_cam, S.START_Z, width, height)
    print(f"当前：{width}x{height} 近端按键 {base['near_note']:.1f}px "
          f"远端 {base['far_note']:.1f}px 鼠标区宽 {base['mouse_near']:.1f}px "
          f"判定线间距 {base['separation']:.1f}px（6K {base['k6_line']:.0f} / "
          f"鼠标 {base['mouse_line']:.0f}）")
    best = []
    for cam_h in [round(1.4 + 0.2 * i, 2) for i in range(9)]:
        for back in [round(2.2 + 0.4 * i, 2) for i in range(8)]:
            for pitch in range(10, 25, 2):
                for fov in range(20, 45, 2):
                    for start_z in (18.0, 20.0, 22.0, 24.0, 26.0, 28.0):
                        camera = S.Camera(y=cam_h, z=-back, pitch_deg=float(pitch),
                                          fov_deg=float(fov))
                        raw = measure(camera, start_z, width, height)
                        # 轨道宽度放大到让"近端按键"回到现在的屏幕宽度
                        x_scale = min(2.6, max(1.0, base["near_note"] / raw["near_note"]))
                        got = measure(camera, start_z, width, height, x_scale)
                        near_ratio = got["near_note"] / base["near_note"]
                        if abs(near_ratio - 1.0) > 0.04:        # 近端保持
                            continue
                        far_ratio = got["far_note"] / base["far_note"]
                        sep_ratio = got["separation"] / base["separation"]
                        if not (0.40 <= far_ratio <= 0.80):      # 远端明显更小
                            continue
                        if not (0.45 <= sep_ratio <= 0.90):      # 两条线更近
                            continue
                        cost = (2.0 * abs(near_ratio - 1.0)
                                + abs(far_ratio - 0.62)
                                + abs(sep_ratio - 0.70))
                        best.append((cost, cam_h, back, pitch, fov, start_z, near_ratio,
                                     far_ratio, sep_ratio, got, x_scale))
    best.sort(key=lambda item: item[0])
    print(f"可行解 {len(best)} 个；最好的几个：")
    for item in best[:8]:
        cost, cam_h, back, pitch, fov, start_z, nr, fr, sr, got, x_scale = item
        print(f"  高 {cam_h:4.2f} 后退 {back:4.2f} 俯角 {pitch:2d}° FOV {fov:2d}° "
              f"START_Z {start_z:4.1f} 轨宽×{x_scale:.2f} | 近端 ×{nr:.3f} "
              f"远端 ×{fr:.3f} 间距 ×{sr:.3f}"
              f"（远端 {got['far_note']:.1f}px，间距 {got['separation']:.0f}px）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

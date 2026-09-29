"""3D 布局与透视投影（软件渲染，不依赖 OpenGL）。

坐标约定（右手系，单位为“判定单位”）
------------------------------------
* ``x``：横向，向右为正，``0`` 在 6K 中央四条轨的中线。
* ``y``：竖直向上，6K 平面在 ``y = 0``，鼠标平面在 ``y = MOUSE_PLANE_HEIGHT``。
* ``z``：向**远处**为正；判定平面在 ``z = 0``，物件从 ``z = START_Z`` 向 ``z = 0`` 运动。

尺寸（与需求一致）
------------------
* **1 单位 = 鼠标合法区域宽度 = 6K 中间四条轨道的宽度**；
* 6K 六条轨道总宽 = 1.5 单位（每条 0.25），中间四条 = 1 单位，左右各多 0.25；
* 鼠标平面在 6K 平面上方 ``0.4`` 单位；
* 两条判定线都在判定平面上：6K 线在 ``y = 0``、鼠标线在 ``y = 0.4``；
* 摄像头在判定平面**后方**（``z < 0``）、鼠标平面**上方**（``y > 0.4``），
  因此鼠标平面离镜头更近、看上去更宽（近大远小）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Tuple

UNIT = 1.0
MOUSE_PLANE_HEIGHT = 0.4        # 鼠标平面在 6K 平面上方 0.4 单位
LANE_COUNT = 6
LANE_WIDTH = 1.0 / 4            # 中间四条轨道合计 1 单位 -> 每条 0.25
K6_WIDTH = LANE_COUNT * LANE_WIDTH          # 1.5 单位
K6_LEFT = -K6_WIDTH / 2.0
MOUSE_LEFT = -0.25 / 2 - 0.5 + 0.25         # 中间四条轨道左边界 = -0.5
MOUSE_LEFT = -0.5
MOUSE_RIGHT = 0.5
MOUSE_LEGAL_WIDTH = MOUSE_RIGHT - MOUSE_LEFT   # = 1 单位
START_Z = 12.0                  # 物件起点：离判定平面多远（两个平面相同）
NOTE_HEIGHT_UNIT = 0.03         # 滑键 / 点击按键图案高度


@dataclass
class Camera:
    """针孔相机：位于判定平面后方、鼠标平面上方。"""

    x: float = 0.0
    y: float = 1.15             # 在鼠标平面（0.4）上方
    z: float = -1.7             # 判定平面后方
    pitch_deg: float = 20.0     # 略微向下俯视
    fov_deg: float = 55.0

    def project(self, point: Tuple[float, float, float], width: int, height: int) -> Tuple[float, float, float]:
        """世界坐标 -> 屏幕坐标 + 透视缩放系数（越远越小）。"""
        px, py, pz = point
        dx, dy, dz = px - self.x, py - self.y, pz - self.z
        # 绕 X 轴俯仰
        pitch = math.radians(self.pitch_deg)
        cy = dy * math.cos(pitch) + dz * math.sin(pitch)
        cz = -dy * math.sin(pitch) + dz * math.cos(pitch)
        depth = max(0.05, cz)
        focal = (height / 2.0) / math.tan(math.radians(self.fov_deg) / 2.0)
        scale = focal / depth
        return width / 2.0 + dx * scale, height / 2.0 - cy * scale, scale


class PlayerScene:
    """负责“物件位置 -> 屏幕坐标”的换算，渲染器只画不算。"""

    def __init__(self, camera: Camera | None = None) -> None:
        self.camera = camera or Camera()
        self._fit_key: Tuple[int, int] = (0, 0)
        self._fit_value: Tuple[float, float, float] = (1.0, 0.0, 0.0)

    # ------------------------------------------------------------ 局部坐标
    @staticmethod
    def lane_span(lane_start: int, lane_end: int) -> Tuple[float, float]:
        low, high = min(lane_start, lane_end), max(lane_start, lane_end)
        return K6_LEFT + low * LANE_WIDTH, K6_LEFT + (high + 1) * LANE_WIDTH

    @staticmethod
    def lane_center(lane: int) -> float:
        return K6_LEFT + (lane + 0.5) * LANE_WIDTH

    @staticmethod
    def mouse_x(norm_x: float) -> float:
        """归一化横坐标（0..1 合法区域）-> 世界 x（鼠标平面）。"""
        return MOUSE_LEFT + norm_x * MOUSE_LEGAL_WIDTH

    @staticmethod
    def note_z(progress: float) -> float:
        """``progress``：0 = 起点（远处），1 = 判定平面。"""
        return START_Z * (1.0 - max(0.0, min(1.0, progress)))

    # ------------------------------------------------------------ 投影
    # 铺满窗口：把“整个游玩区”（两个平面的远近两端）缩放平移到尽可能占满窗口。
    FIT_MARGIN_X = 10.0          # 左右留白（像素）
    FIT_MARGIN_TOP = 44.0        # 顶部留给 HUD
    FIT_MARGIN_BOTTOM = 10.0

    def _field_corners(self):
        """游玩区的极值点：两个平面 × 远近两端 × 左右边界（含鼠标物件高度）。"""
        top = MOUSE_PLANE_HEIGHT + NOTE_HEIGHT_UNIT
        for z in (0.0, START_Z):
            for y in (0.0, top):
                for x in (K6_LEFT, -K6_LEFT):
                    yield (x, y, z)

    def _fit_transform(self, width: int, height: int) -> Tuple[float, float, float]:
        """返回 (scale, dx, dy)：把相机投影结果整体缩放平移到铺满窗口。"""
        key = (int(width), int(height))
        if key == self._fit_key:
            return self._fit_value
        xs, ys = [], []
        for corner in self._field_corners():
            sx, sy, _ = self.camera.project(corner, width, height)
            xs.append(sx)
            ys.append(sy)
        span_x = max(1.0, max(xs) - min(xs))
        span_y = max(1.0, max(ys) - min(ys))
        usable_w = max(80.0, width - 2 * self.FIT_MARGIN_X)
        usable_h = max(80.0, height - self.FIT_MARGIN_TOP - self.FIT_MARGIN_BOTTOM)
        scale = min(usable_w / span_x, usable_h / span_y)
        dx = (width - span_x * scale) / 2.0 - min(xs) * scale
        dy = self.FIT_MARGIN_TOP - min(ys) * scale
        self._fit_key = key
        self._fit_value = (scale, dx, dy)
        return self._fit_value

    def point(self, x: float, y: float, z: float, width: int, height: int):
        sx, sy, view_scale = self.camera.project((x, y, z), width, height)
        scale, dx, dy = self._fit_transform(width, height)
        return sx * scale + dx, sy * scale + dy, view_scale * scale

    def judgment_lines(self, width: int, height: int) -> Dict[str, Tuple[float, float]]:
        """两条判定线的屏幕 y 坐标（鼠标线应当比 6K 线更靠上）。"""
        _, y6, _ = self.point(0.0, 0.0, 0.0, width, height)
        _, ym, _ = self.point(0.0, MOUSE_PLANE_HEIGHT, 0.0, width, height)
        return {"k6": y6, "mouse": ym}

    def visible_width(self, y: float, width: int, height: int) -> float:
        """同一段世界宽度在某个平面上的屏幕宽度（用于验证“近大远小”）。"""
        x0 = self.mouse_x(0.0)
        x1 = self.mouse_x(1.0)
        left, _, _ = self.point(x0, y, 0.0, width, height)
        right, _, _ = self.point(x1, y, 0.0, width, height)
        return abs(right - left)

"""Panda3D 渲染层：把 3D 场景（轨道 / 物件 / 判定线）离屏渲染成一张图。

坐标映射：我们的世界 ``(x, y 向上, z 向远处)`` → Panda3D ``(X, Y=z, Z=y)``，
所以「水平面」正好是 Panda 的 XY 平面，`CardMaker` 生成的卡片不需要旋转；
平行于判定平面的图案（滑键 / 鼠标点击）绕 X 轴转 90°。

每帧：``begin()`` → 若干 ``quad()`` → ``render()`` 取回 ``pygame.Surface``。
UI（菜单 / HUD / 飘字）仍由 pygame 画在这张图之上。
"""

from __future__ import annotations

import os
import math
from typing import Dict, List, Optional, Tuple

_IMPORT_ERROR: Optional[str] = None
try:  # pragma: no cover - 依赖可选依赖
    import panda3d  # noqa: F401
    from direct.showbase.ShowBase import ShowBase
    from panda3d.core import (
        CardMaker,
        Filename,
        FrameBufferProperties,
        GraphicsOutput,
        GraphicsPipe,
        NodePath,
        Texture,
        TransparencyAttrib,
        WindowProperties,
        loadPrcFileData,
    )

    _AVAILABLE = True
except Exception as exc:  # pragma: no cover
    _IMPORT_ERROR = str(exc)
    _AVAILABLE = False
    ShowBase = object  # type: ignore[assignment,misc]


def available() -> bool:
    return _AVAILABLE


def import_error() -> str:
    return _IMPORT_ERROR or ""


def make_gradient_texture(direction: str = "area") -> "Texture":  # pragma: no cover - 需要 GL
    """生成鼠标平面 / 空域用的渐变贴图（贴图版不好表达，直接给一张）。"""
    import numpy as np

    height, width = 8, 256
    top = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]
    bottom = 1.0 - top
    alpha = np.clip(bottom ** 1.15 * 0.42 if direction == "area" else (bottom ** 1.6) * 0.55, 0, 1)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 70, 130, 225
    rgba[..., 3] = (alpha * 255).astype(np.uint8)
    texture = Texture("generated-gradient")
    texture.setup2dTexture(width, height, Texture.TUnsignedByte, Texture.FRgba)
    texture.setRamImageAs(bytes(rgba.tobytes()), "RGBA")
    return texture


class Scene3D:
    """离屏 Panda3D 场景：静态轨道 + 每帧物件。"""

    def __init__(self, width: int, height: int, texture_dir: str, lane_count: int = 6,
                 lane_width: float = 0.25, start_z: float = 12.0,
                 camera_height: float = 1.15, camera_back: float = 1.7,
                 pitch: float = 20.0, fov: float = 55.0) -> None:
        if not _AVAILABLE:  # pragma: no cover - 由调用方先检查
            raise RuntimeError(f"没有可用的 Panda3D：{import_error()}")
        loadPrcFileData("", "window-type offscreen")
        loadPrcFileData("", "audio-library-name null")
        loadPrcFileData("", "notify-level-display error")
        self.base = ShowBase()
        self.texture_dir = texture_dir
        self.lane_count = lane_count
        self.lane_width = lane_width
        self.start_z = start_z
        self.camera_height = camera_height
        self.camera_back = camera_back
        self.pitch = pitch
        self.fov = fov
        self.width = width
        self.height = height
        self._textures: Dict[str, "Texture"] = {}
        self._pool: List["NodePath"] = []
        self._used = 0
        self.card = CardMaker("quad")
        self.card.setFrame(-0.5, 0.5, -0.5, 0.5)          # 单位卡片，缩放即尺寸
        self._make_buffer(width, height)
        self._make_camera()
        self.root = self.base.render.attachNewNode("world")
        self.static_root = self.root.attachNewNode("static")
        self.dynamic_root = self.root.attachNewNode("dynamic")
        self.build_static()

    # ------------------------------------------------------------------ 设置
    def _make_buffer(self, width: int, height: int) -> None:
        props = FrameBufferProperties()
        props.setRgbaBits(8, 8, 8, 8)
        props.setDepthBits(24)
        win_props = WindowProperties.size(width, height)
        self.buffer = self.base.graphicsEngine.make_output(
            self.base.pipe, "verus-3d", -1, props, win_props,
            GraphicsPipe.BFRefuseWindow, self.base.win.getGsg(), self.base.win,
        )
        if self.buffer is None:  # pragma: no cover - 依赖驱动
            raise RuntimeError("Panda3D 离屏缓冲创建失败")
        self.frame_texture = Texture("verus-frame")
        self.frame_texture.setFormat(Texture.FRgba8)
        self.buffer.setClearColor((0.04, 0.04, 0.055, 1.0))
        self.buffer.add_render_texture(self.frame_texture, GraphicsOutput.RTMCopyRam)

    def _make_camera(self) -> None:
        self.camera = self.base.makeCamera(self.buffer)
        self.camera.setPos(0.0, -self.camera_back, self.camera_height)
        self.camera.setP(-self.pitch)
        # Panda 的 setFov 是**水平** FOV；我们按竖直 FOV 换算，画面比例才和软件渲染一致
        aspect = max(0.2, float(width) / float(height))
        vertical = math.radians(self.fov)
        horizontal = 2.0 * math.atan(math.tan(vertical / 2.0) * aspect)
        self.base.camLens.setFov(math.degrees(horizontal))
        self.base.camLens.setNearFar(0.05, 200.0)
        self.base.setBackgroundColor(0.04, 0.04, 0.055, 1.0)

    def texture(self, name: str) -> Optional["Texture"]:
        cached = self._textures.get(name)
        if cached is not None:
            return cached
        path = os.path.join(self.texture_dir, f"{name}.png")
        if not os.path.isfile(path):
            return None
        try:
            texture = self.base.loader.loadTexture(Filename.fromOsSpecific(path))
        except Exception:                              # pragma: no cover - 坏文件
            return None
        if texture is not None:
            texture.setFormat(Texture.FRgba)
        self._textures[name] = texture
        return texture

    # ------------------------------------------------------------------ 静态
    def build_static(self) -> None:
        width = self.lane_count * self.lane_width
        self.static_root.node().removeAllChildren()
        # 6K 底面
        self._static_quad("", -width / 2 + width / 2, self.start_z / 2, width, self.start_z,
                          0.0, (0.09, 0.10, 0.14, 1.0))
        for lane in range(self.lane_count):
            x = -width / 2 + (lane + 0.5) * self.lane_width
            self._static_quad(f"lane_{lane + 1}_default", x, self.start_z / 2,
                              self.lane_width, self.start_z, 0.0, (1, 1, 1, 1))
        # 判定线（6K 白 / 鼠标青）
        judge_y = 0.4
        self._static_quad("", 0.0, 0.02, width, 0.04, 0.0, (1, 1, 1, 1))
        self._static_quad("", 0.0, 0.02, 1.0, 0.04, judge_y, (0.35, 0.86, 1.0, 1.0))
        # 鼠标平面渐变 + 合法区域边线
        gradient = getattr(self, "_gradient", None)
        if gradient is None:
            gradient = make_gradient_texture("mouse")
            self._gradient = gradient
        self._static_quad("", 0.0, self.start_z / 2, width, self.start_z, judge_y,
                          (1, 1, 1, 1), texture=gradient)
        for edge in (-0.5, 0.5):
            self._static_quad("", edge, self.start_z / 2, 0.012, self.start_z, judge_y,
                              (0.35, 0.86, 1.0, 0.8))

    def _static_quad(self, name: str, x: float, z: float, sx: float, sz: float, y: float,
                     color, texture=None) -> None:
        node = NodePath(self.card.generate())
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setPos(x, z, y)
        node.setScale(sx, sz, 1.0)
        node.setColor(*color)
        if texture is not None:
            node.setTexture(texture)
        elif name:
            loaded = self.texture(name)
            if loaded is not None:
                node.setTexture(loaded)
        node.reparentTo(self.static_root)

    # ------------------------------------------------------------------ 每帧
    def begin(self) -> None:
        self._used = 0

    def quad(self, name: str, x: float, z: float, sx: float, sz: float, y: float,
             color=(1, 1, 1, 1), vertical: bool = False, angle: float = 0.0) -> None:
        """放一张卡片：水平面（默认）或平行判定平面（``vertical=True``）。"""
        node = self._next_node()
        node.clearTexture()
        loaded = self.texture(name)
        if loaded is not None:
            node.setTexture(loaded)
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setColor(*color)
        node.setPos(x, z, y)
        node.setHpr(0.0, 90.0 if vertical else 0.0, angle)
        node.setScale(sx, sz, 1.0)
        node.show()

    def _next_node(self) -> "NodePath":
        if self._used < len(self._pool):
            node = self._pool[self._used]
        else:
            node = NodePath(self.card.generate())
            node.reparentTo(self.dynamic_root)
            self._pool.append(node)
        self._used += 1
        return node

    def render(self):
        """渲染一帧并返回 ``pygame.Surface``（RGB）。"""
        import pygame

        for index in range(self._used, len(self._pool)):
            self._pool[index].hide()
        self.base.graphicsEngine.render_frame()
        raw = bytes(self.frame_texture.getRamImageAs("RGB"))
        surface = pygame.image.fromstring(raw, (self.width, self.height), "RGB")
        return pygame.transform.flip(surface, False, True)   # Panda 的行序是自下而上

    def resize(self, width: int, height: int) -> None:
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self.buffer.set_size(width, height)

    def close(self) -> None:  # pragma: no cover - 退出时调用
        try:
            self.base.destroy()
        except Exception:
            pass

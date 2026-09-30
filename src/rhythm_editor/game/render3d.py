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
        PerspectiveLens,
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
                 pitch: float = 20.0, fov: float = 55.0, decorations: bool = True) -> None:
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
        self.decorations = decorations
        # 投影公式按渲染实测校准的线性系数（见 project() 的说明）
        self.row_scale = 0.9215
        self.row_offset = 60.3
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
        # 取景标定：以**构图贴近软件版**为目标（两个平面的 8 个角点平均误差 31px），
        # 由 tools/fit_panda_composition.py 搜索得到。
        self.set_camera(pitch=18.0, fov=36.0, back=1.8, height=1.15)

    # ------------------------------------------------------------------ 设置
    def _make_buffer(self, width: int, height: int) -> None:
        """创建**独立**离屏缓冲（文档：makeOutput + BFRefuseWindow + 不共享 GSG）。

        文档在 low-level-render-to-texture 里明确提醒：**parasite buffer 是"先渲染到主窗口
        再拷贝进纹理"**，那样读到的其实是主窗口的画面（用 base.cam 的镜头），
        表现就是"怎么调我们自己的镜头都对不上"。所以这里传 gsg=None / host=None，
        强制建立一个不依赖窗口的独立缓冲。
        """
        props = FrameBufferProperties()
        props.setRgbaBits(8, 8, 8, 8)
        props.setDepthBits(24)
        win_props = WindowProperties.size(width, height)
        self.buffer = self.base.graphicsEngine.make_output(
            self.base.pipe, "verus-3d", -100, props, win_props,
            GraphicsPipe.BFRefuseWindow, None, None,
        )
        if self.buffer is None:  # pragma: no cover - 依赖驱动
            raise RuntimeError("Panda3D 独立离屏缓冲创建失败")
        self.buffer.setClearColor((0.04, 0.04, 0.055, 1.0))
        self.frame_texture = Texture("verus-frame")
        self.frame_texture.setFormat(Texture.FRgba8)
        self.buffer.add_render_texture(self.frame_texture, GraphicsOutput.RTMCopyRam)

    def _make_camera(self) -> None:
        # 文档配方：相机由 base.makeCamera(buffer) 创建，并挂到自己的场景根
        self.camera = self.base.makeCamera(self.buffer)
        self.root = self.base.render.attachNewNode("world")
        self.camera.reparentTo(self.root)
        self.static_root = self.root.attachNewNode("static")
        self.dynamic_root = self.root.attachNewNode("dynamic")
        self.set_camera()

    def set_camera(self, pitch: float = None, fov: float = None,
                   height: float = None, back: float = None) -> None:
        """调整相机参数（取景对齐用）。"""
        if pitch is not None:
            self.pitch = pitch
        if fov is not None:
            self.fov = fov
        if height is not None:
            self.camera_height = height
        if back is not None:
            self.camera_back = back
        self.camera.setPos(0.0, -self.camera_back, self.camera_height)
        self.camera.setP(-self.pitch)
        aspect = max(0.2, float(self.width) / float(self.height))
        # 实测：改 makeCamera 自带的镜头不生效（画面完全不动），必须**换一个新镜头对象**
        lens = PerspectiveLens()
        # 依据 Panda3D 文档（Lens.setFov）：
        #   单参数 setFov(fov) = **水平** FOV；两参数 setFov(hfov, vfov) = 同时指定水平与竖直。
        # 我们按**竖直** FOV 定义取景，所以要把水平 FOV 一起算出来传两个参数。
        vertical_deg = float(self.fov)
        horizontal_deg = math.degrees(
            2.0 * math.atan(math.tan(math.radians(vertical_deg) / 2.0) * aspect)
        )
        lens.setFov(horizontal_deg, vertical_deg)
        lens.setNearFar(0.05, 200.0)
        self.camera.node().setLens(lens)

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
        if not self.decorations:
            # 标定用：只画两条判定线（白 6K / 青鼠标），其余装饰都省掉
            self._static_quad("", 0.0, 0.02, width, 0.04, 0.0, (1, 1, 1, 1))
            self._static_quad("", 0.0, 0.02, 1.0, 0.04, 0.4, (0.35, 0.86, 1.0, 1.0))
            return
        # 6K 底面
        self._static_quad("", -width / 2 + width / 2, self.start_z / 2, width, self.start_z,
                          0.0, (0.09, 0.10, 0.14, 1.0))
        for lane in range(self.lane_count):
            x = -width / 2 + (lane + 0.5) * self.lane_width
            self._static_quad(f"lane_{lane + 1}_default", x, self.start_z / 2,
                              self.lane_width, self.start_z, 0.0, (1, 1, 1, 1))
        # 判定线（6K 白 / 鼠标青）
        judge_y = 0.4
        # 判定线：深度做得很薄（0.008），否则在这个取景下会变成一条 26px 宽的白带
        self._static_quad("", 0.0, 0.004, width, 0.008, 0.0, (1, 1, 1, 1))
        self._static_quad("", 0.0, 0.004, 1.0, 0.008, judge_y, (0.35, 0.86, 1.0, 1.0))
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
        node.setScale(sx, 1.0, sz)   # 卡片宽高在局部 X / Z 上
        # 实测：CardMaker 的卡片默认立在 XZ 平面（竖着），P=-90 才躺进水平面
        node.setHpr(0.0, -90.0, 0.0)
        node.setColor(*color)
        if texture is not None:
            node.setTexture(texture)
        elif name:
            loaded = self.texture(name)
            if loaded is not None:
                node.setTexture(loaded)
        node.reparentTo(self.static_root)

    # ------------------------------------------------------------------ 每帧
    def project(self, x: float, y: float, z: float):
        """世界坐标 -> 屏幕像素。

        用相机参数自己算透视（文档 API `Lens.project` 在标定后的取景下会判 false，
        用不了；这里的公式是按**渲染实测**校准过的：同参数下公式行 992.6/787.9
        对应渲染行 731/512，故做一次线性修正 row_渲染 ≈ 1.07 × row_公式 − 331）。
        """
        pitch = math.radians(self.pitch)
        dx = x
        dy = y - self.camera_height
        dz = z + self.camera_back
        depth = dy * (-math.sin(pitch)) + dz * math.cos(pitch)
        if depth <= 1e-4:
            return None
        cam_y = dy * math.cos(pitch) + dz * math.sin(pitch)
        vertical = math.tan(math.radians(self.fov) / 2.0)
        aspect = self.width / max(1.0, self.height)
        ndc_x = (dx / depth) / (vertical * aspect)
        ndc_y = (cam_y / depth) / vertical
        row = (1.0 - ndc_y) * 0.5 * self.height
        row = self.row_scale * row + self.row_offset          # 渲染实测校准
        return ((ndc_x + 1.0) * 0.5 * self.width, row)

    def judge_line_y(self, plane_y: float = 0.4) -> float:
        """判定线（某个平面在 z=0 处）的屏幕 y 坐标。"""
        point = self.project(0.0, plane_y, 0.0)
        return point[1] if point else self.height * 0.5

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
        # 水平面（轨道/平面/长条）= P -90；平行判定平面（滑键/点击）= P 0
        node.setHpr(0.0, 0.0 if vertical else -90.0, angle)
        node.setScale(sx, 1.0, sz)   # 卡片宽高在局部 X / Z 上
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

    def mouse_marker(self, norm_x: float, plane_y: float, thickness: float,
                     outline_x: float = None) -> None:
        """鼠标指针（场景内元素）：躺在水平面里的矮胖三角形。

        `outline_x` 给定时，额外用边线画一个“实际位置”的指针（关闭无界鼠标且出界时）。
        """
        from .scene import MOUSE_LEFT, MOUSE_LEGAL_WIDTH
        # 比例实测：0.26 宽 × 0.16 长 时在取景下读起来才是清晰的正三角形
        length = 0.16
        width = MOUSE_LEGAL_WIDTH * 0.26
        x = MOUSE_LEFT + norm_x * MOUSE_LEGAL_WIDTH
        # 贴图“上”= 顶点；卡片铺在水平面里、绕法线转 180° 让顶点指向远处（屏幕上方）。
        # 只抬 0.004 单位：既仍算“在物件运动的水平面内”，又避免和平面共面闪烁。
        lifted = plane_y + 0.004
        self.quad("mouse_marker_solid", x, length * 0.5, width, length, lifted,
                  (1, 1, 1, 1), angle=180.0)
        if outline_x is not None:
            ox = MOUSE_LEFT + outline_x * MOUSE_LEGAL_WIDTH
            self.quad("mouse_marker_outline", ox, length * 0.5, width, length, lifted,
                      (1, 1, 1, 1), angle=180.0)

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

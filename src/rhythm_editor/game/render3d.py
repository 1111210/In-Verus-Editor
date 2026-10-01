"""Panda3D 渲染层：把 3D 场景（轨道 / 物件 / 判定线）离屏渲染成一张图。

坐标映射：我们的世界 ``(x, y 向上, z 向远处)`` → Panda3D ``(X, Y=z, Z=y)``，
所以「水平面」正好是 Panda 的 XY 平面；卡片要躺进水平面得绕 X 轴转 -90°
（``setHpr(0, -90, 0)``），平行于判定平面的图案（滑键 / 鼠标点击）不转。

绘制方式与软件渲染器保持一致：**关掉深度测试，用 render bin 显式定层**（画家
算法）。所有东西都是同一两个平面上的卡片，深度缓冲只会带来共面闪烁（条纹）
和互相遮挡（物件消失），没有任何好处。

每帧：``begin()`` → 若干 ``quad()`` / ``region_shape()`` → ``render()`` 取回
``pygame.Surface``。UI（菜单 / HUD / 飘字）仍由 pygame 画在这张图之上。
"""

from __future__ import annotations

import os
import math
import struct
from typing import Dict, List, Optional, Tuple

_IMPORT_ERROR: Optional[str] = None
try:  # pragma: no cover - 依赖可选依赖
    import panda3d  # noqa: F401
    from direct.showbase.ShowBase import ShowBase
    from panda3d.core import (
        CardMaker,
        Filename,
        FrameBufferProperties,
        Geom,
        GeomNode,
        GeomTriangles,
        GeomVertexData,
        GeomVertexFormat,
        GeomVertexWriter,
        GraphicsOutput,
        GraphicsPipe,
        NodePath,
        Point3,
        PerspectiveLens,
        SamplerState,
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
    """生成鼠标平面用的渐变贴图：**靠判定线那一侧最不透明**。

    注意缓冲区行序：``setRamImageAs`` 的数据**第一行对应 v=0**，而水平卡片上 v=0 就是
    z=0（判定线 / 近端）那一侧。所以第 0 行要给最大 alpha，越往后（越远）越透明。
    （PNG 加载的贴图是另一套约定：文件最后一行 = v=0，别混。）
    """
    import numpy as np

    height, width = 256, 256
    top = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]
    bottom = 1.0 - top
    # row 0 = 判定线一侧 -> 用 top（1 → 0）做衰减；峰值对齐软件渲染器（≈0.38）
    alpha = np.clip(top ** 1.15 * 0.32 if direction == "area" else (top ** 1.6) * 0.38, 0, 1)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 70, 130, 225
    rgba[..., 3] = (alpha * 255).astype(np.uint8)
    texture = Texture("generated-gradient")
    texture.setup2dTexture(width, height, Texture.TUnsignedByte, Texture.FRgba)
    texture.setRamImageAs(bytes(rgba.tobytes()), "RGBA")
    return texture


def quad_corners(x: float, z: float, sx: float, sz: float, y: float,
                 vertical: bool, angle: float):
    """一张卡片的 4 个角（Panda 世界坐标 ``(X, Y, Z)``）——和 NodePath 变换等价。

    顺序对应 UV ``(0,0) (1,0) (1,1) (0,1)``。水平卡片（``vertical=False``）落在
    ``X-Y`` 平面（纵深是 Y），平行判定平面的卡片落在 ``X-Z`` 平面；``angle`` 绕卡片
    法线旋转（Roll）。
    """
    radians = math.radians(angle)
    cos_a, sin_a = math.cos(radians), math.sin(radians)
    corners = []
    for u, v in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
        local_x = (u - 0.5) * sx
        local_z = (v - 0.5) * sz
        rot_x = local_x * cos_a + local_z * sin_a
        rot_z = -local_x * sin_a + local_z * cos_a
        if vertical:                       # 平行判定平面：横向 X、竖向 Z
            corners.append((x + rot_x, z, y + rot_z))
        else:                              # 水平面：横向 X、纵深 Y
            corners.append((x + rot_x, z + rot_z, y))
    return corners


class QuadBatch:
    """一帧里"同图层 + 同贴图 + 同颜色"的四边形合成**一个动态 Geom**。

    每个四边形写 6 个顶点（两个三角形，不用索引），整块 ``setData()`` 一次性上传：
    每帧的 Panda API 调用从"每个物件十几次"降到"每组几次"。
    顶点布局用 ``GeomVertexFormat.getV3t2()``（stride 20：3 个 float 位置 + 2 个 float UV），
    和 numpy / struct 打包出来的字节完全一致。
    """

    _FORMAT = GeomVertexFormat.getV3t2()

    def __init__(self, parent: "NodePath", name: str, texture, color, draw_order: int) -> None:
        self.vdata = GeomVertexData(name, self._FORMAT, Geom.UHDynamic)
        self.vdata.setNumRows(0)
        self.primitive = GeomTriangles(Geom.UHDynamic)
        self.geom = Geom(self.vdata)
        self.geom.addPrimitive(self.primitive)
        self.node = GeomNode(name)
        self.node.addGeom(self.geom)
        self.path = NodePath(self.node)
        self.path.setTransparency(TransparencyAttrib.MAlpha)
        self.path.setDepthTest(False)
        self.path.setDepthWrite(False)
        self.path.setBin("fixed", draw_order)
        self.path.setColor(*color)
        if texture is not None:
            self.path.setTexture(texture)
        self.path.reparentTo(parent)
        self.path.hide()
        self.parts: List[bytes] = []
        self.count = 0

    # ------------------------------------------------------------------ 每帧
    def reset(self) -> None:
        self.parts.clear()
        self.count = 0

    def add_quad(self, x: float, z: float, sx: float, sz: float, y: float,
                 vertical: bool, angle: float) -> None:
        """把一张卡片写进本组的顶点列表。

        坐标直接用**分析式**算（不再走 NodePath 变换）：先取卡片局部 ``(lx, lz)``，
        绕法线转 ``angle``，再按卡片朝向落到世界坐标 —— 水平卡片 ``(x, z 远)``、
        平行判定平面的卡片 ``(x, y 上)``；最后换成 Panda 的 ``(X=x, Y=z, Z=y)``。
        """
        corners = quad_corners(x, z, sx, sz, y, vertical, angle)
        values = []
        for index, (u, v) in ((0, (0.0, 0.0)), (1, (1.0, 0.0)), (2, (1.0, 1.0)),
                              (0, (0.0, 0.0)), (2, (1.0, 1.0)), (3, (0.0, 1.0))):
            px, py, pz = corners[index]
            values.extend((px, py, pz, u, v))
        self.parts.append(struct.pack("<30f", *values))
        self.count += 1

    def flush(self) -> None:
        """把这一帧攒的四边形整块传上去；本帧没用到就藏起来。"""
        if not self.count:
            self.path.hide()
            return
        data = b"".join(self.parts)
        rows = self.count * 6
        self.vdata.setNumRows(rows)
        self.vdata.modifyArray(0).modifyHandle().setData(data)
        self.primitive.clearVertices()
        self.primitive.addConsecutiveVertices(0, rows)
        self.primitive.closePrimitive()
        self.path.show()


class Scene3D:
    """离屏 Panda3D 场景：静态轨道 + 每帧物件。"""

    # 图层顺序 = 绘制顺序（画家算法）。每层一个**独立的父节点**，Panda 按父节点
    # 的创建顺序遍历子节点，所以层序稳定可靠 —— 实测 "fixed" render bin 的
    # draw_order 在这里不生效（池节点与新建节点都只按加入顺序画），不能依赖它。
    LAYER_SEQUENCE = (
        "base",          # 6K 底面
        "lane",          # 轨道
        "side",          # 侧轨高亮
        "pressed",       # 按下轨道
        "bar",           # 小节线
        "mouse_plane",   # 鼠标平面蓝底渐变
        "region",        # 空域底色
        "region_top",    # 空域横纹与左右边线
        "note0", "note1", "note2", "note3", "note4", "note5", "note6",
        "marker",        # 鼠标指针
        "line",          # 判定线
    )

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
        try:
            self._setup(width, height, texture_dir, lane_count, lane_width, start_z,
                        camera_height, camera_back, pitch, fov, decorations)
        except Exception:
            # 构造中途失败也要把 ShowBase 关掉，否则下次再建会报
            # "Attempt to spawn multiple ShowBase instances!"
            self.close()
            raise

    def _setup(self, width: int, height: int, texture_dir: str, lane_count: int,
               lane_width: float, start_z: float, camera_height: float,
               camera_back: float, pitch: float, fov: float, decorations: bool) -> None:
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
        self._textures: Dict[str, "Texture"] = {}
        self._batches: Dict[tuple, "QuadBatch"] = {}
        self._extra: List["NodePath"] = []      # 每帧重建的网格（空域）
        self._static_nodes: List["NodePath"] = []
        self.card = CardMaker("quad")
        self.card.setFrame(-0.5, 0.5, -0.5, 0.5)          # 单位卡片，缩放即尺寸
        self._make_buffer(width, height)
        # ShowBase 会顺手开一个"主窗口"（离屏时也是隐藏窗口），我们根本不用它：
        # 留着它只会在某些机器上影响前台窗口 / 尺寸。直接关掉，只留自己的离屏缓冲。
        default_win = getattr(self.base, "win", None)
        if default_win is not None:
            try:
                self.base.closeWindow(default_win)
            except Exception:                  # pragma: no cover - 依赖驱动
                pass
        self.root = self.base.render.attachNewNode("world")
        # 每层一个父节点：父节点的创建顺序就是绘制顺序（见 LAYER_SEQUENCE）
        self.layers: Dict[str, "NodePath"] = {
            name: self.root.attachNewNode(f"layer_{name}") for name in self.LAYER_SEQUENCE
        }
        self._make_camera()
        # 画家算法：整棵树关掉深度测试，层序完全由图层父节点的顺序决定
        self.root.setDepthTest(False)
        self.root.setDepthWrite(False)
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
            GraphicsPipe.BFRefuseWindow | GraphicsPipe.BFResizeable, None, None,
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
        self.camera.reparentTo(self.root)
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
            # 远处被强烈缩小 -> 没有 mipmap 会出现摩尔纹（看起来就是"条纹"）
            texture.setMinfilter(SamplerState.FTLinearMipmapLinear)
            texture.setMagfilter(SamplerState.FTLinear)
            texture.setAnisotropicDegree(4)
            try:                       # NPOT 贴图也把 mip 链补全（各向异性过滤需要）
                texture.generateRamMipmapImages()
            except Exception:          # pragma: no cover - 老驱动
                pass
        self._textures[name] = texture
        return texture

    # ------------------------------------------------------------------ 静态
    def build_static(self) -> None:
        width = self.lane_count * self.lane_width
        self._clear_static()
        self._lane_state = None
        if not self.decorations:
            # 标定用：只画两条判定线（白 6K / 青鼠标），其余装饰都省掉
            self._static_quad("", 0.0, 0.02, width, 0.04, 0.0, (1, 1, 1, 1), layer="line")
            self._static_quad("", 0.0, 0.02, 1.0, 0.04, 0.4, (0.35, 0.86, 1.0, 1.0),
                              layer="line")
            return
        # 6K 底面
        self._static_quad("", -width / 2 + width / 2, self.start_z / 2, width, self.start_z,
                          0.0, (0.09, 0.10, 0.14, 1.0), layer="base")
        # 轨道贴图 / 侧轨高亮 / 按下高亮 / 边界贴图都由 set_lane_state() 建
        self.set_lane_state(frozenset(), False)
        # 判定线（6K 白 / 鼠标青）
        judge_y = 0.4
        self._static_quad("", 0.0, 0.004, width, 0.008, 0.004, (1, 1, 1, 1), layer="line")
        self._static_quad("", 0.0, 0.004, 1.0, 0.008, judge_y + 0.004,
                          (0.35, 0.86, 1.0, 1.0), layer="line")
        # 鼠标平面渐变 + 合法区域边线
        gradient = getattr(self, "_gradient", None)
        if gradient is None:
            gradient = make_gradient_texture("mouse")
            self._gradient = gradient
        self._static_quad("", 0.0, self.start_z / 2, width, self.start_z, judge_y + 0.002,
                          (1, 1, 1, 1), texture=gradient, layer="mouse_plane")

    def set_lane_state(self, pressed, side_highlight: bool = False,
                       side_color=(58 / 255, 35 / 255, 82 / 255, 1.0)) -> None:
        """建/重建轨道卡片：默认或按下贴图 + 侧轨高亮叠加 + 左右边界高亮。

        按下高亮与侧轨高亮都由贴图/纯色卡片完成，且**都在鼠标平面下方**
        （bin 12~16 < 鼠标平面 20），和软件渲染器的层序一致。
        """
        pressed = frozenset(int(lane) for lane in pressed)
        if getattr(self, "_lane_state", None) == (pressed, bool(side_highlight)):
            return
        self._lane_state = (pressed, bool(side_highlight))
        for node in list(getattr(self, "_lane_nodes", [])):
            node.removeNode()
            if node in self._static_nodes:      # 别让重建把已删节点留在静态列表里
                self._static_nodes.remove(node)
        self._lane_nodes = []
        width = self.lane_count * self.lane_width
        for lane in range(self.lane_count):
            x = -width / 2 + (lane + 0.5) * self.lane_width
            name = (f"lane_{lane + 1}_pressed" if lane in pressed
                    else f"lane_{lane + 1}_default")
            self._lane_nodes.append(self._static_quad(
                name, x, self.start_z / 2, self.lane_width, self.start_z, 0.0,
                (1, 1, 1, 1), layer="pressed" if lane in pressed else "lane"))
        if side_highlight:
            for lane in (0, self.lane_count - 1):
                x = -width / 2 + (lane + 0.5) * self.lane_width
                self._lane_nodes.append(self._static_quad(
                    "", x, self.start_z / 2, self.lane_width, self.start_z, 0.0,
                    side_color, layer="side"))
        # 左右边界：高亮贴图（按下时换成 _pressed 版本）
        suffix = "_pressed" if pressed else ""
        for side, x in (("left", -width / 2), ("right", width / 2)):
            name = f"lane_edge_{side}_highlight{suffix}"
            if self.texture(name) is None:
                name = f"lane_edge_{side}_highlight"
            self._lane_nodes.append(self._static_quad(
                name, x, self.start_z / 2, 0.02, self.start_z, 0.0,
                (1, 1, 1, 1), layer="pressed" if pressed else "lane"))

    def _layer_of(self, layer: str) -> str:
        """``layer`` 支持 ``"note3"`` 这种带图层序号的写法（0=多轨长按 … 6=点击）。"""
        if layer in self.layers:
            return layer
        if layer.startswith("note") and layer[4:].isdigit():
            return f"note{min(6, int(layer[4:]))}"
        return "note3"

    def _apply_layer(self, node, layer: str) -> None:
        """画家算法：关掉深度测试，并用 **fixed bin 的 draw_order** 显式定层。

        实测（tools/diag_panda_render.py 的 "绘制顺序检查"）：
        * 不加 bin 时，带透明的物体会被 Panda 按**深度**排序（近的后画），
          图层结构会被无视；
        * ``setBin("fixed", n)`` 的 draw_order 是生效的（n 大的后画，压在上面）；
        * 同一 draw_order 内按场景图顺序画（所以每层还各有一个父节点兜底）。
        """
        name = self._layer_of(layer)
        node.setBin("fixed", (self.LAYER_SEQUENCE.index(name) + 1) * 10)
        node.setDepthTest(False)
        node.setDepthWrite(False)

    def _layer_root(self, layer: str) -> "NodePath":
        return self.layers[self._layer_of(layer)]

    def _clear_static(self) -> None:
        for node in self._static_nodes:
            node.removeNode()
        self._static_nodes = []

    def _static_quad(self, name: str, x: float, z: float, sx: float, sz: float, y: float,
                     color, texture=None, layer: str = "base") -> "NodePath":
        node = NodePath(self.card.generate())
        node.setTransparency(TransparencyAttrib.MAlpha)
        node.setPos(x, z, y)
        node.setScale(sx, 1.0, sz)   # 卡片宽高在局部 X / Z 上
        # 实测：CardMaker 的卡片默认立在 XZ 平面（竖着），P=-90 才躺进水平面
        node.setHpr(0.0, -90.0, 0.0)
        self._apply_layer(node, layer)
        node.setColor(*color)
        if texture is not None:
            node.setTexture(texture)
        elif name:
            loaded = self.texture(name)
            if loaded is not None:
                node.setTexture(loaded)
        node.reparentTo(self._layer_root(layer))
        self._static_nodes.append(node)
        return node

    # ------------------------------------------------------------------ 每帧
    def project(self, x: float, y: float, z: float):
        """世界坐标 -> 屏幕像素。

        **直接问 Panda 的镜头**（`Lens.project`，输入相机空间点，输出 (-1,1) 的
        NDC，(-1,-1) 是左下角）。实测这条路径与渲染画面相差 < 1px
        （tools/diag_panda_render.py 的 "官方 Lens.project 校验"），比手写
        投影公式 + 线性校准可靠得多：镜头参数（FOV / 纵横比 / 俯角）怎么改都跟得上。
        """
        if self.camera is None:                      # pragma: no cover - 未初始化
            return None
        # 世界 (x, y 向上, z 向远) -> Panda (X=x, Y=z, Z=y)
        cam_point = self.camera.getRelativePoint(self.base.render, Point3(x, z, y))
        ndc = Point3()
        ok = self.camera.node().getLens().project(cam_point, ndc)
        if not ok and cam_point.get_y() <= 0.0:      # 在镜头后面（投影无意义）
            return None
        return ((ndc.get_x() + 1.0) * 0.5 * self.width,
                (1.0 - ndc.get_y()) * 0.5 * self.height)

    def judge_line_y(self, plane_y: float = 0.4) -> float:
        """判定线（某个平面在 z=0 处）的屏幕 y 坐标。"""
        point = self.project(0.0, plane_y, 0.0)
        return point[1] if point else self.height * 0.5

    def begin(self) -> None:
        for group in self._batches.values():
            group.reset()
        for node in self._extra:                # 上一帧的空域网格整体丢掉重建
            node.removeNode()
        self._extra.clear()

    def bar_lines(self, zs, color=(70, 72, 84), half: float = 0.006) -> None:
        """6K 平面上的小节线：一批薄卡片（**只画小节线，不画节拍线**）。"""
        width = self.lane_count * self.lane_width
        for z in zs:
            if 0.0 <= z <= self.start_z:
                self.quad("", 0.0, z, width, half * 2.0, 0.001,
                          (color[0] / 255, color[1] / 255, color[2] / 255, 1.0),
                          layer="bar")

    def region_shape(self, left_points, right_points, y: float,
                     texture_name: str = "region_area", color=(1.0, 1.0, 1.0, 1.0),
                     layer: str = "region") -> None:
        """空域：按左右边界采样点铺一条**透视正确的三角带**（贴图自带横纹与渐变）。

        ``left_points`` / ``right_points`` 是 ``(x, z)`` 列表，逐点对应；
        贴图 v 轴沿着空域长度（0 = 靠判定线的一端 = 贴图更不透明的底边）。
        """
        count = min(len(left_points), len(right_points))
        if count < 2:
            return
        vdata = GeomVertexData("region", GeomVertexFormat.getV3t2(), Geom.UHStatic)
        vdata.setNumRows(count * 2)
        vertex = GeomVertexWriter(vdata, "vertex")
        texcoord = GeomVertexWriter(vdata, "texcoord")
        for index in range(count):
            v = index / float(count - 1)
            for point, u in ((left_points[index], 0.0), (right_points[index], 1.0)):
                vertex.addData3(float(point[0]), float(point[1]), float(y))
                texcoord.addData2(u, v)
        triangles = GeomTriangles(Geom.UHStatic)
        for index in range(count - 1):
            a = index * 2
            triangles.addVertices(a, a + 1, a + 2)
            triangles.addVertices(a + 1, a + 3, a + 2)
        triangles.closePrimitive()
        geom = Geom(vdata)
        geom.addPrimitive(triangles)
        node = GeomNode("region")
        node.addGeom(geom)
        path = NodePath(node)
        path.setTransparency(TransparencyAttrib.MAlpha)
        path.setColor(*color)
        loaded = self.texture(texture_name)
        if loaded is not None:
            path.setTexture(loaded)
        self._apply_layer(path, layer)
        path.reparentTo(self._layer_root(layer))
        self._extra.append(path)

    def ribbon(self, points, y: float, half: float, color=(1.0, 1.0, 1.0, 1.0),
               texture_name: str = "", layer: str = "region_top") -> None:
        """沿一串 ``(x, z)`` 点铺一条**竖直窄带**（空域左右边线用），x 方向宽 2*half。"""
        if len(points) < 2:
            return
        vdata = GeomVertexData("ribbon", GeomVertexFormat.getV3t2(), Geom.UHStatic)
        vdata.setNumRows(len(points) * 2)
        vertex = GeomVertexWriter(vdata, "vertex")
        texcoord = GeomVertexWriter(vdata, "texcoord")
        for index, point in enumerate(points):
            v = index / float(len(points) - 1)
            for x, u in ((point[0] - half, 0.0), (point[0] + half, 1.0)):
                vertex.addData3(float(x), float(point[1]), float(y))
                texcoord.addData2(u, v)
        triangles = GeomTriangles(Geom.UHStatic)
        for index in range(len(points) - 1):
            a = index * 2
            triangles.addVertices(a, a + 1, a + 2)
            triangles.addVertices(a + 1, a + 3, a + 2)
        triangles.closePrimitive()
        geom = Geom(vdata)
        geom.addPrimitive(triangles)
        node = GeomNode("ribbon")
        node.addGeom(geom)
        path = NodePath(node)
        path.setTransparency(TransparencyAttrib.MAlpha)
        path.setColor(*color)
        loaded = self.texture(texture_name) if texture_name else None
        if loaded is not None:
            path.setTexture(loaded)
        self._apply_layer(path, layer)
        path.reparentTo(self._layer_root(layer))
        self._extra.append(path)

    def quad(self, name: str, x: float, z: float, sx: float, sz: float, y: float,
             color=(1, 1, 1, 1), vertical: bool = False, angle: float = 0.0,
             layer: str = "note") -> None:
        """放一张卡片：水平面（默认）或平行判定平面（``vertical=True``）。

        等价于旧版的"一张卡片一个 NodePath + 十几次 setXXX"，但会按
        （图层, 贴图, 颜色）归组，交给 :class:`QuadBatch` 一次性上传顶点缓冲。
        """
        self._batch(layer, name, color).add_quad(x, z, sx, sz, y, vertical, angle)

    def _batch(self, layer: str, name: str, color) -> "QuadBatch":
        layer = self._layer_of(layer)
        key = (layer, name, tuple(round(float(channel), 4) for channel in color))
        group = self._batches.get(key)
        if group is None:
            group = QuadBatch(self.layers[layer],
                              f"batch_{layer}_{name or 'flat'}",
                              self.texture(name) if name else None, key[2],
                              (self.LAYER_SEQUENCE.index(layer) + 1) * 10)
            self._batches[key] = group
        return group

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
                  (1, 1, 1, 1), angle=180.0, layer="marker")
        if outline_x is not None:
            ox = MOUSE_LEFT + outline_x * MOUSE_LEGAL_WIDTH
            self.quad("mouse_marker_outline", ox, length * 0.5, width, length, lifted,
                      (1, 1, 1, 1), angle=180.0, layer="marker")

    def render(self):
        """渲染一帧并返回 ``pygame.Surface``（RGB）。"""
        import pygame

        for group in self._batches.values():     # 每帧只更新用到的组，没用到的藏起来
            group.flush()
        self.base.graphicsEngine.render_frame()
        # 零拷贝建表面：frombuffer 共享内存（比 fromstring 少一次 3MB 的整帧复制），
        # 代价是必须自己保持缓冲区引用不被回收
        raw = bytes(self.frame_texture.getRamImageAs("RGB"))
        self._raw_keepalive = raw
        surface = pygame.image.frombuffer(raw, (self.width, self.height), "RGB")
        return pygame.transform.flip(surface, False, True)   # Panda 的行序是自下而上

    def resize(self, width: int, height: int) -> None:
        """窗口尺寸变化：缓冲、回读纹理、镜头纵横比一起更新。"""
        width, height = max(320, int(width)), max(240, int(height))
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self.buffer.set_size(width, height)
        # 回读纹理要按新尺寸重建，否则 getRamImageAs 还是旧尺寸
        try:                                    # 去掉旧的渲染纹理，避免重复 resize 越积越多
            self.buffer.remove_render_texture(self.frame_texture)
        except Exception:
            pass
        self.frame_texture = Texture("verus-frame")
        self.frame_texture.setFormat(Texture.FRgba8)
        self.buffer.add_render_texture(self.frame_texture, GraphicsOutput.RTMCopyRam)
        self.set_camera()                       # 重设纵横比

    def close(self) -> None:  # pragma: no cover - 退出时调用
        try:
            self.base.destroy()
        except Exception:
            pass

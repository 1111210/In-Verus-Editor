"""演示视图的软件渲染（编辑器「演示模式」使用）。

把 3D 世界（preview.scene / preview.frame）
用 pygame 的软件透视投影画到离屏 Surface 上：不创建窗口、不初始化音频，也不含
任何判定 / 计分 / 输入逻辑。编辑器演示模式把这里画出的帧贴到画布上（见
EditorApp.set_demo_mode / softview.draw_frame）。

渲染参数来自编辑器自己的「演示视图设置」（EditorSettings.demo_*）：物件厚度、
流速、侧轨高亮，与谱面文件互相独立；渲染后端固定为软件渲染。
"""

from __future__ import annotations

import math
import os
import sys
from typing import List, Optional, Tuple

import pygame

from model.chart import Chart
from model.notes import ForbiddenRegion, LaneNote, MouseRegion, MouseSlide, Note
from model.speed import SpeedGroup
from . import frame, textures
from .scene import (
    K6_LEFT,
    LANE_COUNT,
    LANE_WIDTH,
    MOUSE_LEFT,
    MOUSE_LEGAL_WIDTH,
    MOUSE_PLANE_HEIGHT,
    NOTE_HEIGHT_UNIT,
    START_Z,
    UNIT_SCALE,
    Camera,
    PreviewScene,
)
from .settings import ViewSettings

def _resource_root() -> str:
    """仓库根目录（开发时）或打包后的资源解包目录（PyInstaller 的 _MEIPASS）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(  # src/preview/softview.py → 仓库根目录
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


ROOT = _resource_root()

BG = (10, 10, 14)
WHITE = (240, 240, 245)
MOUSE_TINT = (90, 220, 255)
FORBIDDEN_TINT = (255, 90, 70)
K6_TINT = (120, 160, 255)
SIDE_TINT = (58, 35, 82)
LANE_PRESS_TINT = (44, 86, 150)     # 按下轨道时的高亮底色
# 空域左右边线的半宽（世界单位）：按 REGION_EDGE_HALF_WORLD × 透视缩放绘制，
# 远近粗细与贴图一致。0.006 是与 scene.UNIT_SCALE 配套的基准比例。
REGION_EDGE_HALF_WORLD = 0.006 * UNIT_SCALE
DEFAULT_TEXTURE_DIR = os.path.join(ROOT, "assets")


class SoftwareView:
    """离屏软件渲染器（只画场景，不画 HUD / 鼠标指针 / 飘字）。"""

    REGION_EDGE_MIN_HALF_PX = 0.35     # 再远也留一点，别整条看不见

    def __init__(
        self,
        width: int,
        height: int,
        *,
        texture_dir: Optional[str] = None,
        view_settings: Optional[ViewSettings] = None,
    ) -> None:
        self.screen = pygame.Surface((max(1, int(width)), max(1, int(height))))
        self.settings = view_settings or ViewSettings()
        self.settings.normalize()
        self.chart = Chart()
        self.scene = PreviewScene(Camera())
        self.texture_dir = texture_dir or DEFAULT_TEXTURE_DIR
        self.textures = {}
        self.texture_avg = {}
        self._tex_cache = {}
        self._quad_cache = {}
        self.pressed_lanes = set()
        self._plane_strips_key = None
        self._plane_strips = []
        self._overlay = None
        self._mouse_plane_key = None
        self._mouse_plane_value = None
        self._load_textures()

    def resize(self, width: int, height: int) -> None:
        """调整离屏画面尺寸（同时清掉依赖尺寸的缓存）。"""
        width, height = max(1, int(width)), max(1, int(height))
        if self.screen.get_size() == (width, height):
            return
        self.screen = pygame.Surface((width, height))
        self._plane_strips_key = None
        self._plane_strips = []
        self._mouse_plane_key = None
        self._mouse_plane_value = None

    @property
    def w(self) -> int:
        return self.screen.get_width()

    @property
    def h(self) -> int:
        return self.screen.get_height()

    def _load_textures(self) -> None:
        """读入并规格化预设贴图（创建视图时一次性完成）。"""
        try:
            self.textures = textures.load_textures(self.texture_dir)
        except Exception as exc:                      # pragma: no cover - 缺文件
            print(f"[社区编辑器] 贴图加载失败（{exc}），使用占位图", flush=True)
            self.textures = {}
        self._tex_cache.clear()
        self._quad_cache.clear()
        self._compute_texture_averages()          # 软件路径的长条 / 轨道要用平均色

    def _compute_texture_averages(self) -> None:
        """算好"平均色"贴图（软件绘制长条 / 轨道 / 边界时用它们填透视正确的多边形）。

        在加载阶段一次性算好（_load_textures 里调用），渲染时直接取用；
        缺图的情况才即时计算。
        """
        self.texture_avg = {}
        surface = self.textures.get("region_area")
        if surface is not None:
            self.texture_avg["region_area"] = textures.average_color(surface)
        for lane in range(1, LANE_COUNT + 1):
            surface = self.textures.get(f"hold_body_{lane}")
            if surface is not None:
                self.texture_avg[f"hold_body_{lane}"] = textures.average_color(surface)
        for lane in range(1, LANE_COUNT + 1):
            for suffix in ("default", "pressed"):
                key = f"lane_{lane}_{suffix}"
                surface = self.textures.get(key)
                if surface is not None:
                    self.texture_avg[key] = textures.average_color(surface)
        for name in (
            "lane_edge_left_highlight",
            "lane_edge_left_highlight_pressed",
            "lane_edge_right_highlight",
            "lane_edge_right_highlight_pressed",
        ):
            surface = self.textures.get(name)
            if surface is not None:
                self.texture_avg[name] = textures.average_color(surface)

    def _tex(self, name: str):
        """取一张贴图（没加载过就先加载一次）。"""
        if not self.textures:
            self._load_textures()
        return self.textures.get(name)

    def _blit_tex_quad(self, name: str, corners, flip_x: bool = False) -> bool:
        """按仿射把贴图铺到投影后的四边形上（不是拉伸到外接矩形）。

        corners 顺序与贴图一致：[左上, 右上, 右下, 左下]。
        用“上边向量”定旋转角与宽度、“左边向量”定高度，再以四边形中心对齐 blit；
        6K 平面里再配合“按屏幕高度切片”，透视就不会被抹平成方块。
        """
        surface = self._tex(name)
        if surface is None or len(corners) != 4:
            return False
        return self._blit_surface_quad(surface, corners, flip_x, key_prefix=name)

    def _blit_surface_quad(self, surface, corners, flip_x: bool = False,
                           key_prefix: str = "") -> bool:
        """仿射贴图的实际实现（允许直接给 surface，供分片透视用）。"""
        if surface is None or len(corners) != 4:
            return False
        points = [(float(p[0]), float(p[1])) for p in corners]
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
        edge_x = (x1 - x0, y1 - y0)
        edge_y = (x3 - x0, y3 - y0)
        width = max(1.0, math.hypot(*edge_x))
        height = max(1.0, math.hypot(*edge_y))
        angle = -math.degrees(math.atan2(edge_x[1], edge_x[0]))
        key = (key_prefix or id(surface), int(round(width)), int(round(height)),
               round(angle * 2) / 2.0, flip_x)
        cached = self._quad_cache.get(key)
        if cached is None:
            base = pygame.transform.flip(surface, True, False) if flip_x else surface
            scaled = pygame.transform.smoothscale(base, (int(round(width)), int(round(height))))
            if abs(angle) > 0.05:
                scaled = pygame.transform.rotate(scaled, angle)
            if len(self._quad_cache) > 512:
                self._quad_cache.clear()
            cached = scaled
            self._quad_cache[key] = cached
        center_x = (x0 + x1 + x2 + x3) / 4.0
        center_y = (y0 + y1 + y2 + y3) / 4.0
        self.screen.blit(
            cached,
            (center_x - cached.get_width() / 2.0, center_y - cached.get_height() / 2.0),
        )
        return True

    def _blit_lane_tex(self, name: str, x0: float, x1: float, z_near: float, z_far: float,
                       slices: int = 0, flip_x: bool = False) -> bool:
        """把贴图按透视正确的方式铺在 6K 平面的一块区域里。

        直接 _blit_tex_quad 只能做仿射（旋转 + 缩放），而轨道平面上的矩形在屏幕上
        是梯形：左右边应该跟着轨道收敛。只按“上边角度 + 左边长度”贴，就会画成一个
        竖直矩形 —— 看起来像按键竖在平面里。这里沿 z 分片，每片再取贴图对应的
        子块（v 从近端的 0 到远端的 1），逐片仿射，误差只有片内那一点点。
        """
        surface = self._tex(name)
        if surface is None or z_far <= z_near:
            return False
        tex_w, tex_h = surface.get_size()
        if tex_w < 1 or tex_h < 1:
            return False
        if slices <= 0:
            # 分片数按屏幕高度自适应：每片约 5px，片内透视误差 <1px（不会出现锯齿）
            height_px = abs(self._proj(x0, 0.0, z_far)[1] - self._proj(x0, 0.0, z_near)[1])
            slices = int(max(2, min(40, round(height_px / 5.0))))
        zs = [z_near + (z_far - z_near) * index / slices for index in range(slices + 1)]
        for index, (za, zb, source, dest) in enumerate(
                self._lane_tex_pieces(x0, x1, zs, tex_w, tex_h)):
            self._blit_surface_rect(
                surface.subsurface(source), dest, flip_x,
                key_prefix=f"{name}#{index}/{slices}",
            )
        return True

    def _lane_tex_pieces(self, x0: float, x1: float, zs, tex_w: int, tex_h: int):
        """把 6K 平面里的一块区域按 zs（近 -> 远）切片。

        返回 [(z_near, z_far, 源矩形, 目标矩形), ...]。相邻分片的目标矩形边界
        整像素对齐（上一片的下边界 == 下一片的上边界）：同一个 z 的屏幕 y 与 x 无关，
        所以只要按同一条 y 取整，片与片之间既不会留缝也不会重叠。

        所有分片共用同一条 y 取整，因此片与片之间既不会漏出下层颜色，也不会重叠。
        """
        pieces = []
        count = max(1, len(zs) - 1)
        for index in range(count):
            za, zb = zs[index], zs[index + 1]
            if zb <= za:
                continue
            # 贴图 v：0 = 近端（判定线一侧，图像的下边），1 = 远端
            v0, v1 = index / count, (index + 1) / count
            y1 = int(round((1.0 - v1) * tex_h))
            y0 = int(round((1.0 - v0) * tex_h))
            source = pygame.Rect(0, y1, tex_w, max(1, y0 - y1))
            far_left = self._proj(x0, 0.0, zb)
            far_right = self._proj(x1, 0.0, zb)
            near_left = self._proj(x0, 0.0, za)
            near_right = self._proj(x1, 0.0, za)
            top = int(round((far_left[1] + far_right[1]) / 2.0))
            bottom = int(round((near_left[1] + near_right[1]) / 2.0))
            width = max(1, int(round(
                ((far_right[0] - far_left[0]) + (near_right[0] - near_left[0])) / 2.0
            )))
            center_x = (far_left[0] + far_right[0] + near_left[0] + near_right[0]) / 4.0
            left = int(round(center_x - width / 2.0))
            pieces.append((za, zb, source,
                           pygame.Rect(left, top, width, max(1, bottom - top))))
        return pieces

    def _blit_surface_rect(self, surface, rect, flip_x: bool = False,
                           key_prefix: str = "") -> bool:
        """把一片贴图缩放到整数矩形上（分片贴图用；带缓存）。"""
        if surface is None:
            return False
        width = max(1, int(rect[2]))
        height = max(1, int(rect[3]))
        left, top = int(rect[0]), int(rect[1])
        key = (key_prefix, width, height, 0.0, flip_x)
        cached = self._quad_cache.get(key)
        if cached is None:
            base = pygame.transform.flip(surface, True, False) if flip_x else surface
            if base.get_size() != (width, height):
                base = pygame.transform.smoothscale(base, (width, height))
            if len(self._quad_cache) > 512:
                self._quad_cache.clear()
            cached = base
            self._quad_cache[key] = cached
        self.screen.blit(cached, (left, top))
        return True

    def _proj(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        return self.scene.point(x, y, z, self.w, self.h)

    def _quad(self, points, color) -> None:
        pts = [(p[0], p[1]) for p in points]
        if len(pts) >= 3:
            pygame.draw.polygon(self.screen, color, pts)

    @staticmethod
    def _spread_quad_x(quad, pixels: float = 1.0):
        """把 [远左, 远右, 近右, 近左] 四边形的左右两条边各向外挪 pixels 像素。

        车道高亮是填充多边形，而 pygame 的填充不含右 / 下边界像素：它正好压在
        车道分界线上时，会漏出一条 1px 的深色线（贴着轨道边界的那条斜线）。
        """
        far_left, far_right, near_right, near_left = quad
        return [
            (far_left[0] - pixels, far_left[1]),
            (far_right[0] + pixels, far_right[1]),
            (near_right[0] + pixels, near_right[1]),
            (near_left[0] - pixels, near_left[1]),
        ]

    def _alpha_quad(self, points, color, alpha: int) -> None:
        """半透明填充：鼠标平面在更靠前的位置，必须能透过它看到下面的 6K 物件。"""
        pts = [(p[0], p[1]) for p in points]
        if len(pts) < 3:
            return
        if self._overlay is None or self._overlay.get_size() != (self.w, self.h):
            self._overlay = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
        overlay = self._overlay
        # 只清理 / 回贴多边形自己的包围盒：整屏 fill + blit 太贵（弱机上是主要开销）
        xs = [x for x, _y in pts]
        ys = [y for _x, y in pts]
        left = max(0, int(min(xs)) - 2)
        top = max(0, int(min(ys)) - 2)
        right = min(self.w, int(max(xs)) + 3)
        bottom = min(self.h, int(max(ys)) + 3)
        if right <= left or bottom <= top:
            return
        box = pygame.Rect(left, top, right - left, bottom - top)
        overlay.fill((0, 0, 0, 0), box)
        pygame.draw.polygon(
            overlay, (color[0], color[1], color[2], max(0, min(255, alpha))), pts
        )
        self.screen.blit(overlay, (left, top), box)

    def _line(self, a, b, color, width=2) -> None:
        pygame.draw.line(self.screen, color, (a[0], a[1]), (b[0], b[1]), width)

    def _line_scaled(self, a, b, color, width, scale: float) -> None:
        """按透视缩放线宽：远处的线更细（近大远小），免得远看反而更粗。"""
        ref = self._proj(0.0, 0.0, 0.0)[2]          # 判定线处的缩放
        if ref > 1e-6 and scale > 0.0:
            width = max(1, int(round(width * scale / ref)))
        self._line(a, b, color, max(1, int(width)))

    REGION_EDGE_MIN_HALF_PX = 0.35     # 再远也留一点，别整条看不见

    def _region_edge_style(self, scale: float):
        """空域边线在某个透视缩放下的 (半宽像素, 不透明度系数)。

        半宽 = REGION_EDGE_HALF_WORLD（世界单位）× 该处的透视缩放，所以远近
        粗细自然变化；再按同样的比例降一点不透明度 —— pygame 的多边形填充最小
        也要占满 1 像素，只缩宽度的话远看还是跟近处一样实。
        """
        ref = self._proj(0.0, MOUSE_PLANE_HEIGHT, 0.0)[2]
        ratio = 1.0 if ref <= 1e-6 else max(0.0, scale) / ref
        half = max(self.REGION_EDGE_MIN_HALF_PX,
                   REGION_EDGE_HALF_WORLD * max(0.0, scale))
        fade = min(1.0, 0.3 + 0.7 * ratio)
        return half, fade

    def _plane_strip_z(self, strips: int, world_y: float = 0.0) -> List[float]:
        """某个水平面上按屏幕高度均匀切出来的 z 分界。

        直接按 z 均匀切的话，靠近判定线的一条会覆盖半屏（透视压缩），
        远处又挤成一条；贴图会被拉得又宽又斜。按屏幕高度切就均匀了。
        """
        key = (self.w, self.h, strips, round(world_y, 3))
        if self._plane_strips_key == key:
            return self._plane_strips
        samples = 240
        zs = [START_Z * index / samples for index in range(samples + 1)]
        ys = [self._proj(K6_LEFT, world_y, z)[1] for z in zs]
        y_near, y_far = ys[0], ys[-1]
        boundaries: List[float] = []
        for index in range(strips + 1):
            target = y_near + (y_far - y_near) * index / strips
            best = min(range(len(zs)), key=lambda i: abs(ys[i] - target))
            boundaries.append(zs[best])
        self._plane_strips_key = key
        self._plane_strips = boundaries
        return boundaries

    def _visible_span_ms(self) -> float:
        """可视毫秒（物件从起点走到判定线所需的歌曲毫秒）。

        「流速」应该是视觉速度：倍速只改变歌曲推进的快慢，不该让物件在屏幕上
        慢慢爬或飞过去。判定时钟按倍速推进，所以这里的跨度也要乘倍速，
        这样走完全程始终是 可视毫秒 / 倍速 的同一段真实时间。
        """
        return max(1.0, self.settings.scroll_ms * self.settings.rate)

    def _note_z(self, judge_ms: float, now_ms: float,
                start_z: Optional[float] = None,
                group: Optional[SpeedGroup] = None) -> float:
        """歌曲毫秒位置 → z。

        group 给定时按该速度组积分（与编辑器的 ViewState.note_y 同一套
        语义）；默认 1x 组等价于按时间差映射。
        """
        span = self._visible_span_ms()
        if group is None or group.is_identity:
            distance_ms = judge_ms - now_ms
        else:
            assert self.chart is not None
            distance_ms = frame.scroll_ms(
                self.chart, group, now_ms, self.chart.timing.song_ms_to_beat(judge_ms)
            )
        return frame.z_from_distance(distance_ms, span, start_z)

    def _note_group(self, note: Note) -> SpeedGroup:
        """物件所属速度组（渲染位置 / 剔除都按它的 ∫v 反推）。"""
        assert self.chart is not None
        return self.chart.group_of_note(note)

    def _plane_z_near(self, plane_y: float) -> float:
        """该平面在画面里的最近 z（负值：判定平面与镜头之间）。

        相机在判定平面后方，所以反向运动的物件会走到判定线之前；只要它还在这条
        下界之内就应当画出来（PreviewScene.visible_z_near 按窗口尺寸估算）。
        """
        return self.scene.visible_z_near(plane_y, self.w, self.h)

    def _plane_near_distance_ms(self, plane_y: float, span_ms: float) -> float:
        """该平面可视下界对应的滚动距离（毫秒，负值）。"""
        return frame.distance_from_z(self._plane_z_near(plane_y), span_ms)

    def _draw_planes(self, now_ms: float) -> None:
        assert self.chart is not None
        width = LANE_COUNT * LANE_WIDTH
        # 6K 平面：底色 + 左右边线
        far_left = self._proj(K6_LEFT, 0.0, START_Z)
        far_right = self._proj(K6_LEFT + width, 0.0, START_Z)
        near_left = self._proj(K6_LEFT, 0.0, 0.0)
        near_right = self._proj(K6_LEFT + width, 0.0, 0.0)
        # 每条轨道用贴图的颜色按透视正确的四边形铺（轨道贴图是平色块，切片反而会出台阶）
        lane_textured = all(
            f"lane_{index}_default" in self.texture_avg for index in range(1, LANE_COUNT + 1)
        )
        if lane_textured:
            for lane in range(LANE_COUNT):
                name = (
                    f"lane_{lane + 1}_pressed"
                    if lane in self.pressed_lanes
                    else f"lane_{lane + 1}_default"
                )
                color = self.texture_avg[name]
                x0 = K6_LEFT + lane * LANE_WIDTH
                x1 = x0 + LANE_WIDTH
                quad = [
                    self._proj(x0, 0.0, START_Z),
                    self._proj(x1, 0.0, START_Z),
                    self._proj(x1, 0.0, 0.0),
                    self._proj(x0, 0.0, 0.0),
                ]
                if color[3] >= 250:
                    self._quad(quad, color[:3])
                else:
                    self._alpha_quad(quad, color[:3], color[3])
            # 轨道之间的分界线（默认贴图里带的边线）
            for lane in range(LANE_COUNT + 1):
                x = K6_LEFT + lane * LANE_WIDTH
                self._line(
                    self._proj(x, 0.0, START_Z),
                    self._proj(x, 0.0, 0.0),
                    (46, 48, 58),
                    1,
                )
        else:
            self._quad([far_left, far_right, near_right, near_left], (24, 26, 36))
            self._line(far_left, near_left, K6_TINT, 1)
            self._line(far_right, near_right, K6_TINT, 1)
        # 先画完 6K 平面自己的内容（车道线 / 侧轨高亮 / 按下高亮 / 小节线），
        # 它们都在鼠标平面下方，最后再让鼠标平面的蓝底渐变盖上去
        if not lane_textured:
            for lane in range(LANE_COUNT + 1):
                x = K6_LEFT + lane * LANE_WIDTH
                self._line(self._proj(x, 0.0, START_Z), self._proj(x, 0.0, 0.0), (46, 48, 58), 1)
        if self.settings.side_lane_highlight:
            # 侧轨高亮：第 1 / 第 6 轨背景铺暗紫
            for lane in (0, LANE_COUNT - 1):
                x0 = K6_LEFT + lane * LANE_WIDTH
                x1 = x0 + LANE_WIDTH
                self._quad(
                    self._spread_quad_x(
                        [
                            self._proj(x0, 0.0, START_Z),
                            self._proj(x1, 0.0, START_Z),
                            self._proj(x1, 0.0, 0.0),
                            self._proj(x0, 0.0, 0.0),
                        ]
                    ),
                    SIDE_TINT,
                )
        if not lane_textured:
            # 按下轨道时对应轨道高亮（按住期间一直亮）——贴图版已用 lane_x_pressed
            for lane in sorted(self.pressed_lanes):
                if not 0 <= lane < LANE_COUNT:
                    continue
                x0 = K6_LEFT + lane * LANE_WIDTH
                x1 = x0 + LANE_WIDTH
                self._quad(
                    self._spread_quad_x(
                        [
                            self._proj(x0, 0.0, START_Z),
                            self._proj(x1, 0.0, START_Z),
                            self._proj(x1, 0.0, 0.0),
                            self._proj(x0, 0.0, 0.0),
                        ]
                    ),
                    LANE_PRESS_TINT,
                )
        # 边界：高亮 / 高亮按下（左右各一条贴图）
        edge_names = {}
        for side in ("left", "right"):
            pressed = any(0 <= lane < LANE_COUNT for lane in self.pressed_lanes)
            key = f"lane_edge_{side}_highlight" + ("_pressed" if pressed else "")
            if self._tex(key) is None:
                key = f"lane_edge_{side}_highlight"
            edge_names[side] = key
        for side, x in (("left", K6_LEFT), ("right", K6_LEFT + width)):
            name = edge_names[side]
            color = self.texture_avg.get(name)
            if color is None:
                continue
            edge_half = 0.008 * UNIT_SCALE          # 轨道边界贴图的半宽（世界单位）
            # 边界是一条等宽的斜窄条：整条就是一个梯形，一个多边形画完。
            # 颜色取自贴图平均色，本来就是平的，不需要切片。
            quad = [
                self._proj(x - edge_half, 0.0, START_Z),
                self._proj(x + edge_half, 0.0, START_Z),
                self._proj(x + edge_half, 0.0, 0.0),
                self._proj(x - edge_half, 0.0, 0.0),
            ]
            if color[3] >= 250:
                self._quad(quad, color[:3])
            else:
                self._alpha_quad(quad, color[:3], color[3])
        # 6K 平面只画小节线（不画更细的节拍线）
        timing = self.chart.timing
        beat = timing.song_ms_to_beat(max(0.0, now_ms))
        span_beats = max(0.5, self._visible_span_ms() / 1000.0 * timing.bpm_at(beat) / 60.0)
        for line in timing.bar_lines(beat, beat + span_beats):
            z = self._note_z(timing.beat_to_song_ms(line), now_ms)
            if 0.0 <= z <= START_Z:
                self._line(self._proj(K6_LEFT, 0.0, z), self._proj(K6_LEFT + width, 0.0, z), (70, 72, 84), 1)
        # 鼠标平面（在上方、且更靠镜头）：靠近判定线处略微不透明的蓝色，
        # 向轨道尽头渐变为完全透明（不画边线）。整层压在 6K 平面内容之上。
        self._draw_mouse_plane_gradient()
        # 判定平面：两条判定线（鼠标线在 6K 线上方 MOUSE_PLANE_HEIGHT 处）
        k6_left = self._proj(K6_LEFT, 0.0, 0.0)
        k6_right = self._proj(K6_LEFT + width, 0.0, 0.0)
        m_left = self._proj(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0)
        m_right = self._proj(MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0)
        self._line(k6_left, k6_right, WHITE, 3)
        self._line(m_left, m_right, MOUSE_TINT, 3)
        self._line(k6_left, m_left, (60, 62, 74), 1)
        self._line(k6_right, m_right, (60, 62, 74), 1)

    def _draw_mouse_plane_gradient(self) -> None:
        """鼠标平面的蓝底渐变（靠近判定线略蓝 → 轨道尽头完全透明）。

        按"屏幕行"烘一张一维渐变，再用梯形掩码裁形，一次性贴上去：既没有横向接缝，
        也不用每帧重画几十个多边形（同一窗口尺寸只烘一次，之后每帧一次 blit）。
        """
        overlay = self._mouse_plane_overlay
        if overlay is None:
            return
        surface, rect = overlay
        self.screen.blit(surface, rect)

    @property
    def _mouse_plane_overlay(self):
        """烘好的鼠标平面蓝底（含梯形掩码）；窗口尺寸不变时复用。"""
        width = LANE_COUNT * LANE_WIDTH
        far_z = START_Z                      # 鼠标平面远端在 6K 轨道远端之上
        left_far = self._proj(K6_LEFT, MOUSE_PLANE_HEIGHT, far_z)
        right_far = self._proj(K6_LEFT + width, MOUSE_PLANE_HEIGHT, far_z)
        left_near = self._proj(K6_LEFT, MOUSE_PLANE_HEIGHT, 0.0)
        right_near = self._proj(K6_LEFT + width, MOUSE_PLANE_HEIGHT, 0.0)
        xs = (left_far[0], right_far[0], left_near[0], right_near[0])
        ys = (left_far[1], right_far[1], left_near[1], right_near[1])
        left = max(0, int(min(xs)) - 2)
        top = max(0, int(min(ys)) - 2)
        right = min(self.w, int(max(xs)) + 3)
        bottom = min(self.h, int(max(ys)) + 3)
        if right - left < 2 or bottom - top < 2:
            return None
        key = (self.w, self.h, left, top, right, bottom)
        if getattr(self, "_mouse_plane_key", None) == key:
            return getattr(self, "_mouse_plane_value", None)
        rect = pygame.Rect(left, top, right - left, bottom - top)
        # 一维渐变（按屏幕行）：底部 = 判定线一侧最实，向上到轨道尽头渐隐
        column = pygame.Surface((1, rect.height), pygame.SRCALPHA)
        span = max(1, rect.height - 1)
        for row in range(rect.height):
            from_bottom = (span - row) / span                 # 0 = 判定线，1 = 尽头
            # 远端保留一点底透明度（26/255）：否则在黑色背景下"渐隐到看不见"，
            # 看上去像只画到 6K 轨道远端那一行
            alpha = int(round(26 + 70 * (1.0 - from_bottom)))
            column.set_at((0, row), (58, 108, 200, max(0, alpha)))
        surface = pygame.transform.smoothscale(column, (rect.width, rect.height))
        mask = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        mask.fill((255, 255, 255, 0))
        pygame.draw.polygon(mask, (255, 255, 255, 255), [
            (left_near[0] - left, left_near[1] - top),
            (right_near[0] - left, right_near[1] - top),
            (right_far[0] - left, right_far[1] - top),
            (left_far[0] - left, left_far[1] - top),
        ])
        surface.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        self._mouse_plane_key = key
        self._mouse_plane_value = (surface, rect)
        return self._mouse_plane_value

    def _head_ms(self, note: Note) -> float:
        assert self.chart is not None
        return frame.head_ms(self.chart.timing, note)

    def _draw_notes(self, now_ms: float) -> None:
        assert self.chart is not None
        for note in frame.visible_notes(self.chart, now_ms, self._visible_span_ms()):
            if isinstance(note, LaneNote):
                self._draw_lane_note(note, now_ms)
            elif isinstance(note, MouseRegion):
                self._draw_region(note, now_ms)
            elif isinstance(note, MouseSlide):
                self._draw_pattern(note, now_ms)

    def _draw_lane_note(self, note: LaneNote, now_ms: float) -> None:
        """6K 单键 / 长按：用预设贴图绘制（贴图底端在判定边，到达判定线即判定）。"""
        assert self.chart is not None
        group = self._note_group(note)         # 位置按所属速度组的 ∫v 反推
        z_near = self._plane_z_near(0.0)       # 相机对 6K 平面的可视下界（反向物件用）
        span = self._visible_span_ms()
        # 时间（判定未结束）+ 位置（z 范围与可视范围相交）两个条件都要满足
        if not frame.note_in_view(self.chart, note, now_ms, span, z_near):
            return
        head_z = self._note_z(self._head_ms(note), now_ms, group=group)
        head_z = min(START_Z, max(z_near, head_z))
        shape = frame.lane_shape(note)
        tex_index, x0, x1 = shape.texture_index, shape.x0, shape.x1
        if note.is_hold:
            # 时间已经过去的部分不画：只画「当前时刻 ~ 长按结尾」这一截
            body_span = frame.note_z_span(self.chart, note, now_ms, span)
            assert body_span is not None
            z_near_body = min(START_Z, max(z_near, body_span[0]))
            z_far_body = min(START_Z, max(z_near, body_span[1]))
            body = f"hold_body_{tex_index}"
            body_color = self.texture_avg.get(body)
            if body_color is not None:
                # 长条用透视正确的多边形填（pygame 无法做透视 warp，逐片贴图会出台阶）
                quad = [
                    self._proj(x0, 0.0, z_near_body),
                    self._proj(x1, 0.0, z_near_body),
                    self._proj(x1, 0.0, z_far_body),
                    self._proj(x0, 0.0, z_far_body),
                ]
                if body_color[3] >= 250:
                    self._quad(quad, body_color[:3])
                else:
                    self._alpha_quad(quad, body_color[:3], body_color[3])
                if not self._blit_hold_center_line(x0, x1, z_near_body, z_far_body):
                    pass
            elif not self._blit_strip(body, x0, x1, z_near_body, z_far_body):
                near, near2 = self._proj(x0, 0.0, z_near_body), self._proj(x1, 0.0, z_near_body)
                far, far2 = self._proj(x0, 0.0, z_far_body), self._proj(x1, 0.0, z_far_body)
                self._quad([near, near2, far2, far], (60, 120, 220))
                mid_near = self._proj((x0 + x1) / 2, 0.0, z_near_body)
                mid_far = self._proj((x0 + x1) / 2, 0.0, z_far_body)
                self._line_scaled(mid_near, mid_far, WHITE, 2.4, min(near[2], far[2]))
            # 开头盖在主体上面（有意让一部分主体被开头覆盖）；开头时间已过就不画
            now_beat = self.chart.timing.song_ms_to_beat(now_ms)
            if now_beat <= float(note.head_beat) + 1e-9:
                head_top = head_z + frame.hold_head_height(self.settings.note_thickness)
                if not self._blit_lane_tex(f"hold_head_{tex_index}", x0, x1,
                                           head_z, min(START_Z, head_top)):
                    self._quad(
                        [
                            self._proj(x0, 0.0, head_z),
                            self._proj(x1, 0.0, head_z),
                            self._proj(x1, 0.0, min(START_Z, head_top)),
                            self._proj(x0, 0.0, min(START_Z, head_top)),
                        ],
                        (70, 140, 240),
                    )
        else:
            # 单键 / 多轨单键分别用各自的厚度
            thickness = (
                self.settings.multi_thickness
                if note.is_multi
                else self.settings.note_thickness
            )
            far_z = min(START_Z, head_z + thickness)
            # 用分片透视贴（左右边界跟着轨道收敛），否则仿射会把按键画成竖直矩形
            if self._blit_lane_tex(f"tap_{tex_index}", x0, x1,
                                   head_z, far_z):
                return
            near, near2 = self._proj(x0, 0.0, head_z), self._proj(x1, 0.0, head_z)
            far, far2 = self._proj(x0, 0.0, far_z), self._proj(x1, 0.0, far_z)
            self._quad([near, near2, far2, far], (70, 140, 240))
            # 白色实线就在“判定时刻与判定线重合”的那条边上，粗细随透视变细
            self._line_scaled(near, near2, WHITE, 2.4, near[2])

    def _blit_strip(self, name: str, x0: float, x1: float, z0: float, z1: float, slices: int = 28) -> bool:
        """铺一条贴图（6K 平面内）：按屏幕高度切片，保证每片都是窄条、透视正确。

        分片之间用 _lane_tex_pieces 的整数边界对齐，不会漏出下层颜色。
        """
        surface = self._tex(name)
        if surface is None or z1 <= z0:
            return False
        boundaries = [z for z in self._plane_strip_z(max(4, slices), 0.0) if z0 - 1e-9 <= z <= z1 + 1e-9]
        if len(boundaries) < 2:
            boundaries = [z0, z1]
        tex_w, tex_h = surface.get_size()
        for index, (_za, _zb, source, dest) in enumerate(
                self._lane_tex_pieces(x0, x1, boundaries, tex_w, tex_h)):
            # 贴图的“上”对应远处：分片按 v 从近端 0 到远端 1 取子块
            self._blit_surface_rect(
                surface.subsurface(source), dest, key_prefix=f"{name}#{index}",
            )
        return True

    def _blit_hold_center_line(self, x0: float, x1: float, head_z: float, tail_z: float) -> bool:
        """长按主体中间那条纯白竖线（占键宽 = 贴图的 HOLD_LINE_FRACTION，与开头一致）。

        画成透视正确的梯形（近端宽、远端窄），与开头贴图的线宽一致。
        head_z / tail_z 已由调用方夹进可视范围（可以小于 0 = 判定线之前）。
        """
        center = (x0 + x1) / 2.0
        half = (x1 - x0) * textures.HOLD_LINE_FRACTION / 2.0
        near_z, far_z = min(head_z, tail_z), max(head_z, tail_z)
        self._quad(
            [
                self._proj(center - half, 0.0, near_z),
                self._proj(center + half, 0.0, near_z),
                self._proj(center + half, 0.0, far_z),
                self._proj(center - half, 0.0, far_z),
            ],
            WHITE,
        )
        return True

    def _draw_region(self, note: MouseRegion, now_ms: float) -> None:
        """空域 / 禁区：画在鼠标物件运动的水平面内（半透明底 + 左右两条边线）。

        位置由所属速度组的 ∫v 反推；只采样滚动距离在可视跨度内的那几段
        （frame.region_samples）：超出轨道尽头的点不采样，否则会全挤在
        z = START_Z 上相互重叠，叠出的 alpha 更实，看起来像堆在平面远端。
        禁区与空域的几何完全一致，只是换一组红色贴图。
        """
        assert self.chart is not None
        if not frame.note_visible(self.chart.timing, note, now_ms):
            return                      # 判定已结束：整个区域（含判定线高亮）都不画
        forbidden = isinstance(note, ForbiddenRegion)
        area_name = "forbidden_area" if forbidden else "region_area"
        edge_name = "forbidden_edge" if forbidden else "region_edge"
        line_name = "forbidden_line" if forbidden else "region_line"
        span = self._visible_span_ms()
        # 底图：整片同一种颜色的半透明底（避免横向条纹的接缝）
        area_color = self.texture_avg.get(area_name)
        if area_color is None:
            surface = self._tex(area_name)
            if surface is not None:
                area_color = textures.average_color(surface)
        # 左右边界：单像素边界图（多像素时由加载阶段取平均）
        edge_color = self.texture_avg.get(edge_name)
        if edge_color is None:
            surface = self._tex(edge_name)
            if surface is not None:
                edge_color = textures.average_color(surface)
        near_ms = self._plane_near_distance_ms(MOUSE_PLANE_HEIGHT, span)   # 判定线之前也画
        runs = frame.region_samples(self.chart, note, now_ms, span, near_ms=near_ms)
        for run in runs:
            left_points: List[Tuple[float, float, float]] = []
            right_points: List[Tuple[float, float, float]] = []
            for beat, distance in run:
                z = frame.z_from_distance(distance, span)
                left_x, right_x = frame.region_bounds(note, beat)
                left_points.append(self._proj(left_x, MOUSE_PLANE_HEIGHT, z))
                right_points.append(self._proj(right_x, MOUSE_PLANE_HEIGHT, z))
            if area_color is not None:
                self._alpha_quad(
                    left_points + list(reversed(right_points)),
                    area_color[:3],
                    min(255, area_color[3] + 40),
                )
            else:
                # 半透明：区域在鼠标平面上，挡住下面的 6K 物件就没法看谱了
                self._alpha_quad(
                    left_points + list(reversed(right_points)),
                    (200, 60, 50) if forbidden else (60, 110, 200),
                    104,
                )
            # 边线的屏幕线宽按透视缩放：判定线处最宽、远端最细。
            for index in range(len(left_points) - 1):
                for points in (left_points, right_points):
                    a, b = points[index], points[index + 1]
                    scale = (a[2] + b[2]) / 2.0             # 这一段的透视缩放
                    half, fade = self._region_edge_style(scale)
                    if edge_color is not None:
                        dx, dy = b[0] - a[0], b[1] - a[1]
                        length = max(1.0, math.hypot(dx, dy))
                        nx, ny = -dy / length * half, dx / length * half   # 法线方向 × 半宽
                        quad = [
                            (a[0] + nx, a[1] + ny),
                            (b[0] + nx, b[1] + ny),
                            (b[0] - nx, b[1] - ny),
                            (a[0] - nx, a[1] - ny),
                        ]
                        self._alpha_quad(quad, edge_color[:3], int(round(edge_color[3] * fade)))
                    else:
                        self._line_scaled(
                            a,
                            b,
                            FORBIDDEN_TINT if forbidden else MOUSE_TINT,
                            int(max(1, round(4 * fade))),
                            scale,
                        )
        # 判定线高亮：只在这段区域确实压在判定线上（可见段跨过距离 0）时画 ——
        # 时间没到 / 已经结束的区域不该在判定线上留一条亮线
        if not frame.runs_cross_line(runs):
            return
        line_color = self.texture_avg.get(line_name)
        if line_color is None:
            surface = self._tex(line_name)
            if surface is not None:
                line_color = textures.average_color(surface)
        if line_color is not None:
            a = self._proj(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0)
            b = self._proj(MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0)
            self._alpha_quad(
                [
                    (a[0], a[1] - 1.5),
                    (b[0], b[1] - 1.5),
                    (b[0], b[1] + 1.5),
                    (a[0], a[1] + 1.5),
                ],
                line_color[:3],
                line_color[3],
            )
        else:
            self._line(
                self._proj(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0),
                self._proj(MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0),
                FORBIDDEN_TINT if forbidden else MOUSE_TINT,
                3,
            )

    def _draw_pattern(self, note: Note, now_ms: float) -> None:
        """滑键：图案平行于判定平面，使用预设贴图（缺贴图时程序化绘制兜底）。"""
        z_near = self._plane_z_near(MOUSE_PLANE_HEIGHT)   # 反向时图案会走到判定线之前
        span = self._visible_span_ms()
        # 时间（判定未结束）+ 位置（z 在可视范围内）两个条件都要满足
        if not frame.note_in_view(self.chart, note, now_ms, span, z_near):
            return
        z = min(
            START_Z,
            max(z_near, self._note_z(self._head_ms(note), now_ms, group=self._note_group(note))),
        )
        if self.textures or self._tex("slide_body_right") is not None:
            if self._draw_pattern_textured(note, z):
                return
        self._draw_pattern_fallback(note, z)

    def _draw_pattern_textured(self, note: Note, z: float) -> bool:
        """贴图版滑键（缺贴图时返回 False，交给程序化绘制兜底）。"""
        assert isinstance(note, MouseSlide)
        shape = frame.slide_shape(note)
        direction = shape.direction
        name = "right" if direction > 0 else "left"
        height = frame.pattern_height(self.settings.slide_thickness)
        # 贴图是双倍高（上半楔形 / 下半透明）：所见楔形仍在平面上方
        low, high = frame.slide_span(self.settings.slide_thickness)
        start_x, end_x = shape.start_x, shape.end_x
        # 贴图的左边界要绑屏幕左边（不是"起点"）：左滑键的起点在右、
        # 终点在左，若按起点绑就会把整张贴图左右镜像（贴图按中心 + 宽度摆放，
        # 本来就是绑屏幕左边的，与编辑器里的画法一致）
        left_x, right_x = shape.left_x, shape.right_x
        body_quad = [
            self._proj(left_x, high, z),
            self._proj(right_x, high, z),
            self._proj(right_x, low, z),
            self._proj(left_x, low, z),
        ]
        if not self._blit_tex_quad(f"slide_body_{name}", body_quad):
            return False
        # 箭头：宽度不拉伸（用贴图原宽），高度按用户设置拉伸；贴在朝向侧顶点
        arrow = self._tex(f"slide_arrow_{name}")
        if arrow is not None and arrow.get_width() > 0:
            inner = end_x - height * direction       # 箭头朝内延伸，宽度 = 厚度（不拉伸）
            inner_left, inner_right = min(inner, end_x), max(inner, end_x)
            arrow_quad = [
                self._proj(inner_left, high, z),
                self._proj(inner_right, high, z),
                self._proj(inner_right, low, z),
                self._proj(inner_left, low, z),
            ]
            # 左 / 右箭头是各自方向的贴图（与主体一样镜像成对），无需再翻转
            self._blit_tex_quad(f"slide_arrow_{name}", arrow_quad)
        return True

    def _draw_pattern_fallback(self, note: Note, z: float) -> None:
        """程序绘制的样式（缺贴图时的兜底）。"""
        if isinstance(note, MouseSlide):
            color = (110, 230, 160) if note.direction >= 0 else (235, 210, 110)
            height = self.settings.slide_thickness
            # 滑动朝向的一侧更厚、不透明度更高：起点 -> 终点 厚度与透明度都渐变
            shape = frame.slide_shape(note)
            start_x, end_x = shape.start_x, shape.end_x
            thin = max(0.004 * UNIT_SCALE, height * 0.25)
            # 切成细条逐条插值出真正的渐变；条数按屏幕宽度取，宽的时候不会看到色带
            span_px = abs(
                self._proj(end_x, MOUSE_PLANE_HEIGHT, z)[0]
                - self._proj(start_x, MOUSE_PLANE_HEIGHT, z)[0]
            )
            steps = max(12, min(64, int(span_px / 3.0) + 1))
            alpha_near, alpha_far = 70, 236
            for index in range(steps):
                t0 = index / steps
                t1 = (index + 1) / steps
                xa = start_x + (end_x - start_x) * t0
                xb = start_x + (end_x - start_x) * t1
                ha = thin + (height - thin) * t0
                hb = thin + (height - thin) * t1
                alpha = int(round(alpha_near + (alpha_far - alpha_near) * (t0 + t1) / 2.0))
                # 缺贴图时的兜底：与贴图版可见效果一致 —— 楔形底边贴平面、向上收尖
                self._alpha_quad(
                    [
                        self._proj(xa, MOUSE_PLANE_HEIGHT, z),
                        self._proj(xb, MOUSE_PLANE_HEIGHT, z),
                        self._proj(xb, MOUSE_PLANE_HEIGHT + hb, z),
                        self._proj(xa, MOUSE_PLANE_HEIGHT + ha, z),
                    ],
                    color,
                    alpha,
                )
            return


def available() -> bool:
    """演示视图是否可用（只需要 pygame）。"""
    return True


def view_settings_for(editor_settings) -> ViewSettings:
    """编辑器「演示视图设置」→ 演示渲染参数（只取与画面有关的字段）。"""
    view = ViewSettings()
    view.note_thickness = float(editor_settings.demo_note_thickness)
    view.multi_thickness = float(editor_settings.demo_multi_thickness)
    view.slide_thickness = float(editor_settings.demo_slide_thickness)
    view.speed = float(editor_settings.demo_speed)
    view.side_lane_highlight = bool(editor_settings.demo_side_lane_highlight)
    view.normalize()
    return view


def create_software_view(
    width: int,
    height: int,
    texture_dir: Optional[str] = None,
    view_settings: Optional[ViewSettings] = None,
) -> SoftwareView:
    """创建离屏软件渲染视图（不建窗口、不初始化音频与输入）。"""
    return SoftwareView(
        width, height, texture_dir=texture_dir, view_settings=view_settings
    )


def resize(view: SoftwareView, width: int, height: int) -> None:
    """调整离屏画面尺寸（同时清掉依赖尺寸的缓存）。"""
    view.resize(width, height)


def draw_frame(view: SoftwareView, chart: Chart, now_ms: float, *, rate: float = 1.0):
    """画一帧“演示视图的 3D 部分”（不含 HUD / 判定飘字 / 菜单 / 鼠标指针）。"""
    view.chart = chart
    view.settings.rate = float(rate)
    view.screen.fill(BG)
    view._draw_planes(now_ms)
    view._draw_notes(now_ms)
    return view.screen

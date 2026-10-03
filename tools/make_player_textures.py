"""生成游玩器用的预设贴图（PNG）。

用法::

    python tools/make_player_textures.py [输出目录]

默认输出到 ``assets/textures/player/``。贴图规格见 ``docs/PLAYER.md``：

* 单键 6 张（1~6 轨）：最下端白色，向上渐变为亮蓝色并变为适中透明，形成尾焰；
  两端的透明速度比中间慢。
* 长按 12 张：开头 6 张（单键的画法但不透明、中部纯白竖线）+ 主体 6 张
  （中间纯白竖线、两侧淡蓝、无渐变、**完全不透明**）。开头与主体共用
  ``HOLD_BLUE``，接缝处颜色一致。
* 空域：边界 / 填充 / 判定线各一张单像素图，另有一张带横向条纹的大图（区域底）。
* 滑键：左右各一张箭头图（当前为全透明，保留接口）与一张主体图
  （直角三角形：**水平直角边贴着判定线**（贴图底边）且最不透明，直角顶点在前进方向
  的下角，斜边向上渐隐）。
* 鼠标点击：位置标识菱形、范围标识（中间红线、上下透明）、左边界标识。
* 轨道：每条轨道默认图 / 按下图，另有 4 张边界高亮图。
"""

from __future__ import annotations

import os
import sys

import numpy as np

from _common import ROOT, use_dummy_audio, use_dummy_video, use_utf8_console

use_utf8_console()
use_dummy_video()
use_dummy_audio()

import pygame  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "assets", "textures", "player")

# ---------------------------------------------------------------- 基本参数
WHITE = np.array([255, 255, 255], dtype=np.float32)
BRIGHT_BLUE = np.array([70, 160, 255], dtype=np.float32)
# 长按共用的一种蓝：**开头与主体必须是同一个蓝**，否则接缝处颜色会突变
# （开头是"白 -> 蓝"的渐变，它的顶端正好接在主体的底边上）。
HOLD_BLUE = np.array([150, 200, 255], dtype=np.float32)
PALE_BLUE = HOLD_BLUE                 # 旧名字，含义相同
# 长按中间那条纯白竖线占键宽的比例：**开头与主体必须一致**，否则接缝处会一粗一细
# （游戏侧同名的常量在 ``game/textures.py``，软渲染手绘白线时用它）。
HOLD_LINE_FRACTION = 1.0 / 8.0
DARK_FILL = np.array([24, 26, 36], dtype=np.float32)
PRESS_FILL = np.array([44, 86, 150], dtype=np.float32)
EDGE_DIM = np.array([46, 48, 58], dtype=np.float32)
EDGE_HIGHLIGHT = np.array([120, 90, 200], dtype=np.float32)
RED = np.array([235, 60, 50], dtype=np.float32)


def _save(name: str, rgba: np.ndarray, out_dir: str) -> str:
    """把 (h, w, 4) 的 uint8 数组存成 PNG。"""
    surface = pygame.image.frombuffer(
        np.ascontiguousarray(rgba).tobytes(), (rgba.shape[1], rgba.shape[0]), "RGBA"
    )
    path = os.path.join(out_dir, name)
    pygame.image.save(surface, path)
    return path


def _mix(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
    """按权重在两条颜色之间插值（``t`` 可以是 (h, 1) / (h, w, 1)）。"""
    if t.ndim == 2:
        t = t[..., None]
    return a * (1.0 - t) + b * t


def _smoothstep(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def _center_line_mask(width: int) -> np.ndarray:
    """长按中间白线的列掩码：宽 ``HOLD_LINE_FRACTION``、居中。"""
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)
    return np.abs(xs - 0.5) < HOLD_LINE_FRACTION / 2.0


def tap_lane(width: int = 96, height: int = 160) -> np.ndarray:
    """单键：底部白色 -> 向上亮蓝 + 适中透明，两端比中间慢一点变透明。"""
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    top = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]     # 1 = 图顶, 0 = 图底
    bottom = 1.0 - top
    edge = np.abs(xs - 0.5) * 2.0                                     # 0 = 中间, 1 = 两端
    mid_weight = _smoothstep((edge - 0.30) / 0.50)                    # 中间 0 -> 两端 1
    fade_mid = bottom ** 1.9                                          # 中间掉得快
    fade_edge = bottom ** 0.9                                         # 两端掉得慢
    fade = fade_mid * (1.0 - mid_weight) + fade_edge * mid_weight
    side = 1.0 - _smoothstep((edge - 0.94) / 0.06)                    # 横向收边（尽量铺满轨道）
    alpha = (0.30 + 0.70 * fade) * side                               # 顶部也留 0.30（适中透明）
    color = _mix(WHITE[None, None, :], BRIGHT_BLUE[None, None, :],
                 np.clip(top * 1.5, 0.0, 1.0))                        # 底白 -> 顶亮蓝
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[..., :3] = color
    rgba[..., 3] = np.clip(alpha, 0.0, 1.0) * 255.0
    # 最下端一条纯白不透明，保证“底端到达判定线”时看得清
    rgba[-2:, :, :3] = WHITE
    rgba[-2:, :, 3] = 255.0
    return rgba.astype(np.uint8)


def hold_head_lane(width: int = 96, height: int = 96) -> np.ndarray:
    """长按开头：单键的画法（底部纯白、向上渐变），但**完全不透明**，且蓝色与主体一致。

    顶端正好接在长按主体的底边上（``_mix(WHITE, HOLD_BLUE, 1.0) == HOLD_BLUE``），
    所以接缝处没有色差；中部有纯白竖线接主体。
    """
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    top = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]
    color = _mix(WHITE[None, None, :], HOLD_BLUE[None, None, :], np.clip(top * 1.5, 0.0, 1.0))
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[..., :3] = color
    rgba[..., 3] = 255.0
    rgba[:, _center_line_mask(width), :3] = WHITE                       # 中部纯白竖线（与主体同宽）
    rgba[-2:, :, :3] = WHITE
    return rgba.astype(np.uint8)


def hold_body_lane(width: int = 32, height: int = 128) -> np.ndarray:
    """长按主体：中间纯白竖线，两侧纯淡蓝背景，无渐变，**背景也完全不透明**。"""
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[..., :3] = HOLD_BLUE[None, None, :]
    rgba[..., 3] = 255.0                      # 主体背景不透明（用户要求）
    rgba[:, _center_line_mask(width), :3] = WHITE     # 中部纯白竖线（与开头同宽）
    return rgba.astype(np.uint8)


def region_area(width: int = 256, height: int = 256) -> np.ndarray:
    """空域底图：底边略不透明淡蓝，越往上越透明；横纹很淡（重了远处会出现摩尔纹）。"""
    top = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]     # 1 = 图顶
    bottom = 1.0 - top
    alpha = (bottom ** 1.15) * 0.5
    stripe = 1.0 + 0.08 * np.sin(bottom * np.pi * 6.0)
    alpha = np.clip(alpha * stripe, 0.0, 1.0)
    color = np.broadcast_to(np.array([90, 150, 235], dtype=np.float32), (height, 1, 3))
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[..., :3] = color
    rgba[..., 3] = alpha * 255.0
    return rgba.astype(np.uint8)


def slide_body(direction: int, width: int = 256, height: int = 128) -> np.ndarray:
    """滑键主体：直角三角形（direction=+1 右滑），**两倍高**贴图。

    **上半 = 楔形**：水平直角边贴着贴图中线（= 判定线）、最不透明；宽的一侧朝向
    滑动方向（直角顶点在那一侧），斜面朝相反方向落下；左滑镜像。
    **下半 = 完全透明**。渲染时按“图片中心对齐鼠标运动平面”摆放，于是可见的
    楔形依旧整条落在平面上方（底边贴平面），与旧版视觉一致。
    """
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    top = np.linspace(1.0, 0.0, height, dtype=np.float32)[:, None]      # 1 = 图顶，0 = 判定线
    thick = xs if direction >= 0 else 1.0 - xs                          # 朝向侧 = 1
    # 底边（判定线）整行填满，向上收成尖；宽的一侧 = 滑动方向那一侧
    inside = top <= thick
    span = np.maximum(thick, 1e-3)
    # 底边（判定线）最实，斜边最虚；朝向侧整体更实一点
    alpha = np.where(inside, 1.0 - 0.85 * (top / span), 0.0)
    alpha = np.clip(alpha, 0.0, 1.0) * (0.85 + 0.15 * thick)
    color = np.broadcast_to(
        (np.array([110, 230, 160], dtype=np.float32) if direction >= 0
         else np.array([235, 210, 110], dtype=np.float32)),
        (height, width, 3),
    )
    # 两倍高：上半放楔形，下半完全透明（贴图中心 = 判定线）
    rgba = np.zeros((height * 2, width, 4), dtype=np.float32)
    rgba[:height, ..., :3] = color
    rgba[:height, ..., 3] = alpha * 255.0
    return rgba.astype(np.uint8)


def click_diamond(size: int = 64) -> np.ndarray:
    """鼠标点击的位置标识：实心红色菱形。"""
    xs = np.linspace(-1.0, 1.0, size, dtype=np.float32)[None, :]
    ys = np.linspace(-1.0, 1.0, size, dtype=np.float32)[:, None]
    inside = np.abs(xs) + np.abs(ys) <= 1.0
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., :3] = np.array([255, 60, 50], dtype=np.float32)
    rgba[..., 3] = np.where(inside, 255.0, 0.0)
    return rgba.astype(np.uint8)


def click_range(width: int = 128, height: int = 64) -> np.ndarray:
    """鼠标点击的范围标识：中间一条红线，上下透明（无渐变）。"""
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    mid0 = max(0, height // 2 - max(1, height // 16))
    mid1 = min(height, height // 2 + max(1, height // 16))
    rgba[mid0:mid1, :, :3] = RED
    rgba[mid0:mid1, :, 3] = 255.0
    return rgba.astype(np.uint8)


def click_edge(size: int = 64) -> np.ndarray:
    """鼠标点击的左边界标识：位置菱形竖直切开后的**右半**。"""
    diamond = click_diamond(size)
    half = size // 2
    return np.ascontiguousarray(diamond[:, half:])


def lane_texture(pressed: bool, index: int = 1, width: int = 128, height: int = 512) -> np.ndarray:
    """轨道图：默认 / 按下两种。"""
    base = PRESS_FILL if pressed else DARK_FILL
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[..., :3] = base[None, None, :]
    rgba[..., 3] = 255.0 if pressed else 235.0
    # 轨道两端的竖线（暗色边线），中间略暗一点，方便看出轨道
    rgba[:, :2, :3] = EDGE_DIM
    rgba[:, -2:, :3] = EDGE_DIM
    shade = 1.0 - 0.06 * (index % 2)
    rgba[..., :3] *= shade
    return rgba.astype(np.uint8)


def lane_edge_texture(highlight: bool, pressed: bool) -> np.ndarray:
    """轨道边界：高亮 / 高亮按下。"""
    color = (EDGE_HIGHLIGHT if highlight else EDGE_DIM).copy()
    alpha = 235.0 if highlight else 200.0
    if pressed:
        color = np.clip(color * 1.25, 0, 255)
        alpha = 255.0
    width, height = 8, 512
    rgba = np.zeros((height, width, 4), dtype=np.float32)
    rgba[..., :3] = color[None, None, :]
    rgba[..., 3] = alpha
    return rgba.astype(np.uint8)


def single_pixel(color, alpha: float) -> np.ndarray:
    rgba = np.zeros((1, 1, 4), dtype=np.float32)
    rgba[0, 0, :3] = color
    rgba[0, 0, 3] = alpha
    return np.ascontiguousarray(rgba.astype(np.uint8))


def slide_arrow(size: int = 48) -> np.ndarray:
    """滑键箭头（**尚未实装**，当前是全透明占位）。

    与 :func:`slide_body` 同一套约定：两倍高，可见箭头画在**上半**、下半完全透明，
    渲染时按“图片中心对齐鼠标运动平面”摆放；左 / 右是各自方向的镜像贴图
    （渲染时不再二次翻转）。
    """
    return np.zeros((size * 2, size, 4), dtype=np.uint8)


def mouse_marker(filled: bool, size: int = 96) -> np.ndarray:
    """鼠标指针（场景内元素）：躺在鼠标平面里的矮胖三角形，可实心 / 只画边线。

    形状只有三角形：底边最宽、顶点朝远处。
    """
    xs = np.linspace(-1.0, 1.0, size, dtype=np.float32)[None, :]
    ys = np.linspace(-1.0, 1.0, size, dtype=np.float32)[:, None]   # -1 = 图顶（远处）
    # 三角形：底边贴着图顶（最宽），顶点在图底中点（渲染时卡片转 180°，顶点朝远处）
    inside = np.abs(xs) <= (0.9 - ys) / 1.8
    edge = inside & ~(np.abs(xs) < (0.88 - ys) / 1.8 - 0.06)
    rgba = np.zeros((size, size, 4), dtype=np.float32)
    rgba[..., 0], rgba[..., 1], rgba[..., 2] = 90, 220, 255
    if filled:
        rgba[..., 3] = np.where(inside, 130.0, 0.0)
    else:
        rgba[..., 3] = np.where(edge, 255.0, 0.0)
    return rgba.astype(np.uint8)


def build_all(out_dir: str) -> list:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for lane in range(1, 7):
        written.append(_save(f"tap_{lane}.png", tap_lane(), out_dir))
        written.append(_save(f"hold_head_{lane}.png", hold_head_lane(), out_dir))
        written.append(_save(f"hold_body_{lane}.png", hold_body_lane(), out_dir))
        written.append(_save(f"lane_{lane}_default.png", lane_texture(False, lane), out_dir))
        written.append(_save(f"lane_{lane}_pressed.png", lane_texture(True, lane), out_dir))
    written.append(_save("region_fill.png", single_pixel(np.array([60, 110, 200], dtype=np.float32), 104), out_dir))
    written.append(_save("region_edge.png", single_pixel(np.array([90, 220, 255], dtype=np.float32), 255), out_dir))
    written.append(_save("region_line.png", single_pixel(np.array([90, 220, 255], dtype=np.float32), 255), out_dir))
    written.append(_save("region_area.png", region_area(), out_dir))
    for direction, name in ((1, "right"), (-1, "left")):
        written.append(_save(f"slide_arrow_{name}.png", slide_arrow(), out_dir))
        written.append(_save(f"slide_body_{name}.png", slide_body(direction), out_dir))
    written.append(_save("click_diamond.png", click_diamond(), out_dir))
    written.append(_save("click_range.png", click_range(), out_dir))
    written.append(_save("click_edge.png", click_edge(), out_dir))
    written.append(_save("mouse_marker_solid.png", mouse_marker(True), out_dir))
    written.append(_save("mouse_marker_outline.png", mouse_marker(False), out_dir))
    for side in ("left", "right"):
        # 边界：高亮图 + 高亮按下图（默认边界直接用轨道贴图自带的边线）
        written.append(
            _save(f"lane_edge_{side}_highlight.png", lane_edge_texture(True, False), out_dir)
        )
        written.append(
            _save(
                f"lane_edge_{side}_highlight_pressed.png",
                lane_edge_texture(True, True),
                out_dir,
            )
        )
    return written


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUT
    pygame.init()
    try:
        files = build_all(out_dir)
    finally:
        pygame.quit()
    print(f"[done] 生成 {len(files)} 张贴图 -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

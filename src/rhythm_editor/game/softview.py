"""玩家软件渲染的离屏视图（编辑器演示模式使用）。

复用 :class:`~rhythm_editor.game.app.PlayerApp` 的软件绘制方法，但**不创建窗口、
音频、判定与输入状态**：只准备软件渲染需要的属性。于是编辑器演示模式与游玩器的
软件渲染走的是同一份代码，画面完全一致（演示模式只是不画 HUD / 判定飘字 / 菜单）。
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

try:  # pragma: no cover - 依赖可选依赖
    import pygame

    _AVAILABLE = True
except Exception:  # pragma: no cover
    pygame = None  # type: ignore[assignment]
    _AVAILABLE = False

from ..model.chart import Chart
from . import settings as psettings
from .app import BG, ROOT, PlayerApp
from .scene import (
    MOUSE_LEFT,
    MOUSE_LEGAL_WIDTH,
    MOUSE_PLANE_HEIGHT,
    Camera,
    PlayerScene,
)


def available() -> bool:
    """是否可用（需要 pygame）。"""
    return _AVAILABLE


def create_software_view(width: int, height: int, texture_dir: Optional[str] = None) -> PlayerApp:
    """创建离屏的游玩器软件渲染视图（不建窗口 / 不初始化音频与判定）。"""
    if not _AVAILABLE:  # pragma: no cover - 调用方先检查
        raise RuntimeError("没有可用的 pygame")
    view = PlayerApp.__new__(PlayerApp)      # 跳过 __init__：它要建窗口、音频与判定
    view.screen = pygame.Surface((max(1, int(width)), max(1, int(height))))
    try:      # 演示画面跟随玩家自己的设置（物件厚度 / 流速 / 侧轨高亮…）
        view.settings = psettings.load_settings()
    except Exception:  # pragma: no cover - 配置损坏
        view.settings = psettings.PlayerSettings()
    view.settings.normalize()
    view.chart = Chart()
    view.scene = PlayerScene(Camera())
    view.texture_dir = texture_dir or os.path.join(ROOT, "assets", "textures", "player")
    view.textures = {}
    view.texture_avg = {}
    view._tex_cache = {}
    view._quad_cache = {}
    view.pressed_lanes = set()
    view.mouse_norm = 0.5
    view.mouse_raw = 0.5
    view._plane_strips_key = None
    view._plane_strips = []
    view._overlay = None
    view.scene3d = None
    view._load_textures()
    return view


def resize(view: PlayerApp, width: int, height: int) -> None:
    """调整离屏画面尺寸（同时清掉依赖尺寸的缓存）。"""
    width, height = max(1, int(width)), max(1, int(height))
    if view.screen.get_size() == (width, height):
        return
    view.screen = pygame.Surface((width, height))
    view._plane_strips_key = None
    view._plane_strips = []
    view._mouse_plane_key = None
    view._mouse_plane_value = None


def mouse_norm_at(view: PlayerApp, x: float) -> Tuple[float, float]:
    """屏幕横坐标 → 鼠标平面归一化位置（与 ``PlayerApp._mouse_norm`` 同一套规则）。"""
    left, _, _ = view.scene.point(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0, view.w, view.h)
    right, _, _ = view.scene.point(
        MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0, view.w, view.h
    )
    raw = (float(x) - left) / max(1.0, right - left)
    value = 0.5 + (raw - 0.5) * view.settings.mouse_sensitivity
    if not view.settings.unbounded_mouse:
        value = min(1.0, max(0.0, value))
    return float(value), float(raw)


def draw_frame(
    view: PlayerApp,
    chart: Chart,
    now_ms: float,
    *,
    rate: float = 1.0,
    mouse_norm: float = 0.5,
    mouse_raw: Optional[float] = None,
):
    """画一帧“游玩器软件渲染的 3D 部分”（不含 HUD / 判定飘字 / 菜单）。"""
    view.chart = chart
    view.settings.rate = float(rate)
    view.mouse_norm = float(mouse_norm)
    view.mouse_raw = float(mouse_norm if mouse_raw is None else mouse_raw)
    view.screen.fill(BG)
    view._draw_planes(now_ms)
    view._draw_notes(now_ms)
    view._draw_mouse_marker()
    return view.screen

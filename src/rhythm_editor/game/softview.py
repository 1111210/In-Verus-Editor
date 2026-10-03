"""玩家软件渲染的离屏视图（编辑器演示模式使用）。

复用 :class:`~rhythm_editor.game.app.PlayerApp` 的软件绘制方法，但**不创建窗口、
音频、判定与输入状态**：只准备软件渲染需要的属性。于是编辑器演示模式与游玩器的
软件渲染走的是同一份代码，画面完全一致（演示模式只是不画 HUD / 判定飘字 / 菜单，
也不画鼠标指针）。

渲染参数来自编辑器自己的「演示视图设置」（``EditorSettings.demo_*``），与游玩器的
``player.json`` 互相独立；渲染后端固定为软件渲染。
"""

from __future__ import annotations

import os
from typing import Optional

try:  # pragma: no cover - 依赖可选依赖
    import pygame

    _AVAILABLE = True
except Exception:  # pragma: no cover
    pygame = None  # type: ignore[assignment]
    _AVAILABLE = False

from ..model.chart import Chart
from . import settings as psettings
from .app import BG, ROOT, PlayerApp
from .scene import Camera, PlayerScene


def available() -> bool:
    """是否可用（需要 pygame）。"""
    return _AVAILABLE


def view_settings_for(editor_settings) -> psettings.PlayerSettings:
    """编辑器「演示视图设置」→ 游玩器绘制用的设置对象（软件渲染）。

    只取与**画面**有关的参数：物件厚度、流速、侧轨高亮；后端固定 software。
    游玩器自己的 ``player.json``（键位 / 判定 / 窗口等）完全不参与。
    """
    view = psettings.PlayerSettings()
    view.note_thickness = float(editor_settings.demo_note_thickness)
    view.multi_thickness = float(editor_settings.demo_multi_thickness)
    view.slide_thickness = float(editor_settings.demo_slide_thickness)
    view.click_thickness = float(editor_settings.demo_click_thickness)
    view.speed = float(editor_settings.demo_speed)
    view.side_lane_highlight = bool(editor_settings.demo_side_lane_highlight)
    view.renderer = "software"
    view.normalize()
    return view


def create_software_view(
    width: int,
    height: int,
    texture_dir: Optional[str] = None,
    view_settings: Optional[psettings.PlayerSettings] = None,
) -> PlayerApp:
    """创建离屏的游玩器软件渲染视图（不建窗口 / 不初始化音频与判定）。

    ``view_settings`` 为空时退回读取游玩器设置（直接调用 / 测试用）；编辑器
    演示模式会传入自己的「演示视图设置」。
    """
    if not _AVAILABLE:  # pragma: no cover - 调用方先检查
        raise RuntimeError("没有可用的 pygame")
    view = PlayerApp.__new__(PlayerApp)      # 跳过 __init__：它要建窗口、音频与判定
    view.screen = pygame.Surface((max(1, int(width)), max(1, int(height))))
    if view_settings is not None:
        view.settings = view_settings
    else:
        try:      # 没给设置时沿用游玩器那一份（物件厚度 / 流速 / 侧轨高亮…）
            view.settings = psettings.load_settings()
        except Exception:  # pragma: no cover - 配置损坏
            view.settings = psettings.PlayerSettings()
    view.settings.normalize()
    view.settings.renderer = "software"      # 演示视图只支持软件渲染
    view.chart = Chart()
    view.scene = PlayerScene(Camera())
    view.texture_dir = texture_dir or os.path.join(ROOT, "assets", "textures", "player")
    view.textures = {}
    view.texture_avg = {}
    view._tex_cache = {}
    view._quad_cache = {}
    view.pressed_lanes = set()
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


def draw_frame(
    view: PlayerApp,
    chart: Chart,
    now_ms: float,
    *,
    rate: float = 1.0,
):
    """画一帧“游玩器软件渲染的 3D 部分”（不含 HUD / 判定飘字 / 菜单 / 鼠标指针）。"""
    view.chart = chart
    view.settings.rate = float(rate)
    view.screen.fill(BG)
    view._draw_planes(now_ms)
    view._draw_notes(now_ms)
    return view.screen

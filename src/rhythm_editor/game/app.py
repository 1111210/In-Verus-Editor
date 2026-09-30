"""游玩器 GUI（pygame）：主界面 / 设置 / 加载 / 游玩（3D）/ 结算。

运行：``python run_player.py [谱面.json …]``

* 软件透视投影（:mod:`rhythm_editor.game.scene`），需要时可换成 OpenGL；
* 判定核心 :class:`~rhythm_editor.game.judge.Judge`；音频复用编辑器的 ``AudioPlayer``
  （倍速 / 系统音频延迟补偿都已在编辑器里验证过）；
* 鼠标平面在 6K 平面上方 0.4 单位、离镜头更近，看起来更宽；
* 第 0 拍在进入游玩 1.5 秒后到达判定平面（``LEAD_MS``）。
"""

from __future__ import annotations

import os
import math
import subprocess
import sys
import time
from typing import List, Optional, Tuple

import pygame

from .. import config
from ..audio import AudioPlayer
from ..io.chart_io import chart_audio_path, load_chart
from ..model.chart import Chart
from ..model.notes import LaneNote, MouseClick, MouseRegion, MouseSlide, Note
from ..metronome import Metronome
from . import settings as psettings
from . import glview
from . import textures
from . import render3d
from .judge import BREAK, EXACT, EXACT_MIXED, GRADES, NEAR, Judge
from .scene import (
    K6_LEFT,
    LANE_COUNT,
    LANE_WIDTH,
    MOUSE_LEFT,
    MOUSE_LEGAL_WIDTH,
    MOUSE_PLANE_HEIGHT,
    NOTE_HEIGHT_UNIT,
    START_Z,
    Camera,
    PlayerScene,
)

# 让窗口居中创建（部分机器上偏出屏幕外会看起来像“黑屏”）
os.environ.setdefault("SDL_VIDEO_CENTERED", "1")

SCREEN_W, SCREEN_H = 1280, 800
LEAD_MS = 1500.0
END_DELAY_S = 3.0                   # 谱面结束后至少等这么久再进结算
REGION_PAD = 0.01
MENU_WINDOW = 8                     # 主界面谱面列表一次显示多少行（其余滚动显示）
ROOT = os.path.dirname(                       # 仓库根目录（src/rhythm_editor/game/app.py → ../..）
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

BG = (10, 10, 14)
PANEL = (22, 22, 30)
WHITE = (240, 240, 245)
DIM = (150, 150, 165)
ACCENT = (90, 170, 255)
MOUSE_TINT = (90, 220, 255)
K6_TINT = (120, 160, 255)
SIDE_TINT = (58, 35, 82)
LANE_PRESS_TINT = (44, 86, 150)     # 按下轨道时的高亮底色
GRADE_COLORS = {
    EXACT: (255, 226, 120),
    EXACT_MIXED: (140, 235, 160),
    NEAR: (140, 190, 255),
    BREAK: (240, 120, 120),
}


def main_chart_in(directory: str, names: List[str]) -> str:
    """谱面文件夹里的“主谱面”：优先 ``chart.json``，其次与文件夹同名，最后按名字排序。"""
    lowered = {name.lower(): name for name in names}
    if "chart.json" in lowered:
        return os.path.join(directory, lowered["chart.json"])
    folder = os.path.basename(os.path.normpath(directory)).lower()
    for name in names:
        if os.path.splitext(name)[0].lower() == folder:
            return os.path.join(directory, name)
    return os.path.join(directory, names[0])


def chart_files(root: str, folder: str = "charts") -> List[str]:
    """``charts/`` 下**每个含 json 的文件夹**算一张谱面（不再是一个 json 一行）。

    一个文件夹里有多个 json 时取其中的“主谱面”（见 :func:`main_chart_in`）；
    ``_`` 开头的文件（临时 / 自动保存）忽略；不含 json 的文件夹不算谱面。
    """
    base = os.path.join(root, folder)
    if not os.path.isdir(base):
        return []
    found: List[str] = []
    for directory, _dirs, names in os.walk(base):
        jsons = sorted(
            name
            for name in names
            if name.lower().endswith(".json") and not name.startswith("_")
        )
        if jsons:
            found.append(main_chart_in(directory, jsons))
    return sorted(found)


def key_code(name: str) -> int:
    """键位名 -> pygame 键值（支持 shift / left shift / space / a …）。"""
    name = (name or "").strip().lower()
    special = {
        "shift": pygame.K_LSHIFT,
        "left shift": pygame.K_LSHIFT,
        "right shift": pygame.K_RSHIFT,
        "space": pygame.K_SPACE,
        "ctrl": pygame.K_LCTRL,
        "left ctrl": pygame.K_LCTRL,
        "right ctrl": pygame.K_RCTRL,
        "alt": pygame.K_LALT,
        "enter": pygame.K_RETURN,
        "tab": pygame.K_TAB,
    }
    if name in special:
        return special[name]
    for candidate in (name, name.replace("left ", "").replace("right ", "")):
        try:
            code = pygame.key.key_code(candidate)
        except Exception:
            code = pygame.K_UNKNOWN
        if code != pygame.K_UNKNOWN:
            return code
    return pygame.K_UNKNOWN


def _fmt_num(value: float) -> str:
    """数字显示：去掉多余的 0（1.0 → ``1``、1.37 → ``1.37``、0.5 → ``0.5``）。"""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return text or "0"


class PlayerApp:
    """游玩器主程序（状态机 + 渲染 + 判定）。"""

    def __init__(self, paths: Optional[List[str]] = None, renderer: Optional[str] = None) -> None:
        pygame.init()
        pygame.display.set_caption(f"In Verus 游玩器 · {config.VERSION}")
        self.settings = psettings.load_settings()
        if renderer in ("software", "opengl"):
            self.settings.renderer = renderer
            psettings.save_settings(self.settings)
        self.gl_presenter = None
        self.renderer_name = "software"
        self.screen = self._make_display(*self._fit_to_desktop(SCREEN_W, SCREEN_H))
        self.clock = pygame.time.Clock()
        font = "microsoftyaheiui,arial"
        self.f_big = pygame.font.SysFont(font, 40, bold=True)
        self.f_mid = pygame.font.SysFont(font, 24, bold=True)
        self.f_small = pygame.font.SysFont(font, 18)
        self.scene = PlayerScene(Camera())
        self.audio = AudioPlayer()
        self.root = ROOT
        self.paths = list(paths or []) or chart_files(self.root)
        self._info_cache: dict = {}
        self._rebuild_entries()
        self.selected = 0
        self.state = "menu"
        self.paused = False
        self.running = True
        self.timer = 0.0
        self.chart: Optional[Chart] = None
        self.chart_path = ""
        self.judge: Optional[Judge] = None
        self.hits = Metronome()          # 打击音效（白噪声，复用编辑器那套）
        self.popups: List[Tuple[str, str, float]] = []   # (等级, 文字, 剩余秒数)
        self.pressed_lanes = set()
        self.mouse_norm = 0.5
        self.mouse_raw = 0.5               # 鼠标实际位置（关闭无界鼠标时可能出界）
        self.message = ""
        self.status_hint = ""
        self.settings_row = 0
        # 引导时间（首拍 1.5 秒）：offset 不够时先用挂钟静音等待，保证音乐和物件同步
        self.audio_started = True
        self.play_wall = 0.0
        self.pause_wall = 0.0
        self.audio_start_ms = 0.0
        self.lead_start_ms = 0.0           # 进入游玩那一刻的歌曲毫秒（可能为负）
        self.lead_wait_real_ms = 0.0       # 需要静音等待的**真实**毫秒（音频还没到 0）
        self.idle_seconds = 0.0            # 音频连续停多久（用来判“真的播完了”）
        self.ending = False                # 谱面已结束、正在等 END_DELAY_S
        self.end_timer = 0.0
        # 鼠标 UI 的命中区域（每帧绘制时写入）与半透明叠加层
        self.ui: dict = {}
        self.hover = ""
        self._overlay: Optional[pygame.Surface] = None
        self._plane_strips_key: Optional[Tuple[int, int, int]] = None
        self._plane_strips: List[float] = []
        # 预设贴图（在“加载中”阶段一次性读入 + 规格化，游戏内只做缩放缓存）
        self.texture_dir = os.path.join(self.root, "assets", "textures", "player")
        self.textures: dict = {}
        self.texture_avg: dict = {}          # 边界等窄条用平均色画正梯形
        self._tex_cache: dict = {}
        self._quad_cache: dict = {}          # 仿射（旋转 + 缩放）贴图缓存
        self.scene3d = None                  # Panda3D 渲染层（游戏画面）
        self.gl_cost = 0.0                 # OpenGL 呈现耗时（指数平滑，太慢就回退）
        self.gl_frames = 0
        self.display_mode = "windowed"     # windowed / borderless / fullscreen
        self.windowed_size: Optional[Tuple[int, int]] = None
        self.maximized = False             # F10 最大化状态（非 Windows 下的退路用）
        self._prime_window()
        try:
            desktop = pygame.display.get_desktop_sizes()[0]
        except Exception:                              # pragma: no cover - 依赖驱动
            desktop = (0, 0)
        if os.environ.get("SDL_VIDEODRIVER", "").lower() != "dummy":     # 测试环境不用刷屏
            print(
                f"[In Verus 游玩器] 渲染后端：{self.renderer_name} · 窗口 {self.w}x{self.h}"
                f" · 桌面 {desktop[0]}x{desktop[1]} · F2 可切换渲染后端",
                flush=True,
            )

    # ------------------------------------------------------------ 显示后端
    @staticmethod
    def _fit_to_desktop(width: int, height: int) -> Tuple[int, int]:
        """窗口不要比桌面还大（否则可能只露出一部分，或干脆一直黑屏）。"""
        try:
            desktop_w, desktop_h = pygame.display.get_desktop_sizes()[0]
        except Exception:                                  # pragma: no cover - 依赖驱动
            return width, height
        if desktop_w <= 0 or desktop_h <= 0:
            return width, height
        return min(width, max(960, desktop_w - 80)), min(height, max(600, desktop_h - 120))

    def _gl_self_check(self, surface: pygame.Surface) -> bool:
        """GL 自检帧：中央画白块后回读像素，全黑或 GL 报错就认为 GL 不能正常显示。"""
        surface.fill(BG)
        pygame.draw.rect(
            surface,
            WHITE,
            (surface.get_width() // 2 - 24, surface.get_height() // 2 - 8, 48, 16),
        )
        try:
            return bool(self.gl_presenter.present(surface, verify=True))
        except Exception:                                  # pragma: no cover - 依赖驱动
            return False

    def _make_display(
        self,
        width: int,
        height: int,
        verify: bool = True,
        mode: str = "windowed",
    ):
        """按设置创建显示：opengl 成功**且自检通过**才用 GL，否则回退软件渲染。

        ``mode``：``windowed`` 可调窗口 / ``borderless`` 无边框全屏 / ``fullscreen`` 独占全屏。
        """
        if mode in ("borderless", "fullscreen"):
            try:                                       # 全屏用桌面分辨率
                width, height = pygame.display.get_desktop_sizes()[0]
            except Exception:                          # pragma: no cover - 依赖驱动
                pass
        else:
            width, height = self._fit_to_desktop(width, height)
        if mode == "borderless":
            window_flags = pygame.NOFRAME
        elif mode == "fullscreen":
            window_flags = pygame.FULLSCREEN
        else:
            window_flags = pygame.RESIZABLE
        if self.settings.renderer == "opengl" and glview.available():
            try:
                pygame.display.set_mode(
                    (width, height), pygame.OPENGL | pygame.DOUBLEBUF | window_flags
                )
                self.gl_presenter = glview.GLPresenter()
                size = pygame.display.get_window_size() or (width, height)
                surface = pygame.Surface(size)             # 离屏绘制目标
                if verify and not self._gl_self_check(surface):
                    raise RuntimeError("OpenGL 自检失败（画面全黑 / GL 报错）")
                self.renderer_name = "opengl"
                return surface
            except Exception as exc:                       # pragma: no cover - 依赖驱动
                print(f"[In Verus 游玩器] OpenGL 不可用（{exc}），改用软件渲染", flush=True)
                self.gl_presenter = None
                self.renderer_name = "software"
                self.settings.renderer = "software"
                psettings.save_settings(self.settings)
        self.gl_presenter = None
        self.renderer_name = "software"
        return pygame.display.set_mode((width, height), window_flags)

    def _prime_window(self) -> None:
        """建好窗口后立刻刷一帧：有些驱动会“一直黑到被 resize”，先自己刷两次。"""
        self.screen.fill(BG)
        self.center_text("In Verus 游玩器 · 正在启动…", self.h / 2 - 12, self.f_mid, WHITE)
        self._present()
        pygame.event.pump()
        self._present()

    def _present(self, verify: bool = False) -> None:
        """把这一帧送到屏幕（GL 时上传纹理后交换缓冲）。"""
        if self.gl_presenter is not None:
            started = time.perf_counter()
            try:
                if not self.gl_presenter.present(self.screen, verify=verify):
                    raise RuntimeError("GL 呈现自检失败")
                pygame.display.flip()
            except Exception as exc:                       # pragma: no cover - 依赖驱动
                self._fallback_to_software(f"OpenGL 呈现失败（{exc}）")
                return
            cost = time.perf_counter() - started
            self.gl_cost = self.gl_cost * 0.9 + cost * 0.1 if self.gl_cost else cost
            self.gl_frames += 1
            # OpenGL 在这台机器上慢到没法玩（<25fps）时自动回退，别让输入也跟着卡住
            if self.gl_frames > 180 and self.gl_cost > 0.04:
                self._fallback_to_software("OpenGL 呈现过慢")
            return
        pygame.display.flip()

    def _fallback_to_software(self, why: str) -> None:
        self.settings.renderer = "software"
        psettings.save_settings(self.settings)
        self.gl_presenter = None
        self.gl_frames = 0
        self.gl_cost = 0.0
        self.screen = self._make_display(self.w, self.h)
        self.message = f"{why}，已自动切回软件渲染"
        print(f"[In Verus 游玩器] {self.message}", flush=True)

    # ------------------------------------------------------------ 通用
    @property
    def w(self) -> int:
        return self.screen.get_width()

    @property
    def h(self) -> int:
        return self.screen.get_height()

    def text(self, content: str, pos, font=None, color=WHITE) -> None:
        self.screen.blit((font or self.f_small).render(content, True, color), pos)

    def center_text(self, content: str, y: float, font=None, color=WHITE) -> None:
        surface = (font or self.f_small).render(content, True, color)
        self.screen.blit(surface, (self.w / 2 - surface.get_width() / 2, y))

    def run(self) -> int:
        while self.running:
            dt = self.clock.tick(120) / 1000.0
            self._events()
            self._update(dt)
            self._draw()
            self._present()
        self.audio.unload()
        pygame.quit()
        return 0

    def _events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEOEXPOSE:
                self._draw()                     # 窗口重新暴露：立刻重画一帧，别留黑屏
                self._present()
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F2:
                self._switch_renderer()
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F10:
                self._maximize_window()
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                self._toggle_fullscreen()
            elif event.type == pygame.VIDEORESIZE:
                # 只跟随窗口尺寸（**不要**再 set_mode，否则最大化会被立刻打回原大小）
                self._sync_display_size(event.w, event.h)
            elif self.state == "menu":
                self._event_menu(event)
            elif self.state == "settings":
                self._event_settings(event)
            elif self.state == "loading":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.state = "menu"
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                        self._start_play()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    self._start_play()
            elif self.state == "play":
                self._event_play(event)
            elif self.state == "result":
                self._event_result(event)

    def _switch_renderer(self) -> None:
        """F2 / 设置里轮换：Panda3D → OpenGL → 软件（存进 player.json）。"""
        order = ("panda3d", "opengl", "software")
        current = self.settings.renderer if self.settings.renderer in order else "panda3d"
        target = order[(order.index(current) + 1) % len(order)]
        self.settings.renderer = target
        psettings.save_settings(self.settings)
        self.gl_presenter = None
        self.gl_frames = 0
        self.gl_cost = 0.0
        self.scene3d = None
        self.screen = self._make_display(self.w, self.h)
        if target == "panda3d":
            self._ensure_scene3d()
        self.renderer_name = (
            "panda3d" if self.scene3d is not None
            else ("opengl" if self.gl_presenter is not None else "software")
        )
        self.message = f"渲染后端：{self.renderer_name}（F2 轮换）"
        print(f"[In Verus 游玩器] 渲染后端：{self.renderer_name} · 窗口 {self.w}x{self.h}", flush=True)

    # ------------------------------------------------------ 窗口尺寸 / 全屏
    def _sync_display_size(self, width: Optional[int] = None, height: Optional[int] = None) -> None:
        """窗口被最大化 / 拉大 / 全屏后跟上尺寸。

        软件渲染直接用 SDL 的显示表面（SDL 自己已经改好尺寸），GL 模式要重建离屏表面。
        **不要再 set_mode**：那会把刚最大化的窗口打回原大小。
        """
        if width and height:
            size = (int(width), int(height))
        else:
            try:
                size = pygame.display.get_window_size()
            except Exception:                          # pragma: no cover - 依赖驱动
                return
        if size[0] <= 0 or size[1] <= 0:
            return
        if self.gl_presenter is not None:
            self.screen = pygame.Surface(size)
            return
        surface = pygame.display.get_surface()
        if surface is not None:
            self.screen = surface

    def _maximize_window(self) -> None:
        """F10：最大化 / 还原窗口（Windows 交给系统；其它平台退化成“铺满桌面”）。"""
        self.maximized = not self.maximized
        if os.name == "nt":
            try:
                import ctypes

                hwnd = pygame.display.get_wm_info().get("window")
                if hwnd:
                    # 3 = SW_MAXIMIZE，9 = SW_RESTORE
                    ctypes.windll.user32.ShowWindow(int(hwnd), 3 if self.maximized else 9)
                    self.message = "已最大化窗口（F10 还原）" if self.maximized else "已还原窗口（F10）"
                    return
            except Exception:                          # pragma: no cover - 依赖系统
                pass
        try:
            desktop_w, desktop_h = pygame.display.get_desktop_sizes()[0]
        except Exception:                              # pragma: no cover - 依赖驱动
            desktop_w, desktop_h = SCREEN_W, SCREEN_H
        if self.maximized:
            self.screen = self._make_display(desktop_w, max(600, desktop_h - 96))
            self.message = "已铺满桌面（F10 还原）"
        else:
            self.screen = self._make_display(*(self.windowed_size or (SCREEN_W, SCREEN_H)))
            self.message = "已还原窗口（F10）"

    def _toggle_fullscreen(self) -> None:
        """F11：窗口 → 无边框全屏 → 独占全屏 → 窗口 循环切换。"""
        order = ("windowed", "borderless", "fullscreen")
        if self.display_mode not in order:
            self.display_mode = "windowed"
        if self.display_mode == "windowed":
            self.windowed_size = (self.w, self.h)
        self.display_mode = order[(order.index(self.display_mode) + 1) % len(order)]
        if self.display_mode == "windowed":
            width, height = self.windowed_size or (SCREEN_W, SCREEN_H)
            self.screen = self._make_display(width, height, mode="windowed")
            self.message = "已回到窗口（F11 无边框全屏）"
        else:
            self.screen = self._make_display(
                self.w, self.h, mode=self.display_mode
            )
            self.message = (
                "已开启无边框全屏（F11 独占全屏）"
                if self.display_mode == "borderless"
                else "已开启独占全屏（F11 回到窗口）"
            )

    # ------------------------------------------------------------ 菜单
    def _event_menu(self, event) -> None:
        if event.type == pygame.MOUSEWHEEL:
            self.selected = (self.selected - event.y) % self._menu_rows()
            return
        if event.type == pygame.MOUSEMOTION:
            self.hover = self._hit_ui(event.pos) or ""
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.hover = self._hit_ui(event.pos) or ""
            self._click_menu(self.hover)
            return
        if event.type != pygame.KEYDOWN:
            return
        rows = self._menu_rows()
        if event.key == pygame.K_ESCAPE:
            self.running = False
        elif event.key in (pygame.K_DOWN, pygame.K_UP):
            step = 1 if event.key == pygame.K_DOWN else -1
            self.selected = (self.selected + step) % rows
        elif event.key in (pygame.K_HOME, pygame.K_END):
            self.selected = 0 if event.key == pygame.K_HOME else rows - 1
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._activate_selected()
        elif event.key == pygame.K_o:
            self.open_external_chart()
        elif event.key == pygame.K_s:
            self.state = "settings"

    def _hit_ui(self, pos) -> Optional[str]:
        """鼠标位置命中的 UI 元素名（每帧绘制时写入 ``self.ui``）。

        命中多个时取**面积最小**的那个，这样行里的 ◀ ▶ 按钮优先于整行。
        """
        best: Optional[Tuple[int, str]] = None
        for key, rect in self.ui.items():
            if rect is None or not rect.collidepoint(pos):
                continue
            area = rect.width * rect.height
            if best is None or area < best[0]:
                best = (area, key)
        return best[1] if best is not None else None
    def _click_menu(self, key: Optional[str]) -> None:
        if not key:
            return
        kind, _, raw = key.partition(":")
        if kind == "chart":
            index = int(raw)
            if index == self.selected:          # 再点一次 = 开始（等价于回车）
                self._enter_loading()
            else:
                self.selected = index
        elif kind == "action":
            offset = int(raw)
            self.selected = len(self.paths) + offset
            self._activate_selected()

    def _menu_rows(self) -> int:
        """主界面的可选项数：谱面列表 + 「打开其他路径」 + 「设置」。"""
        return len(self.paths) + 2

    def _activate_selected(self) -> None:
        """Enter：谱面开始 / 打开其他路径 / 进入设置。"""
        if self.selected < len(self.paths):
            self._enter_loading()
        elif self.selected == len(self.paths):
            self.open_external_chart()
        else:
            self.state = "settings"

    def _enter_loading(self) -> None:
        self._load_textures()
        self._begin_chart(self.paths[self.selected])

    def _load_textures(self) -> None:
        """读入并规格化预设贴图（在加载页完成，避免游戏内卡顿）。"""
        try:
            self.textures = textures.load_textures(self.texture_dir)
        except Exception as exc:                      # pragma: no cover - 缺文件
            print(f"[In Verus 游玩器] 贴图加载失败（{exc}），使用占位图", flush=True)
            self.textures = {}
        self._tex_cache.clear()
        self._quad_cache.clear()

    def _ensure_scene3d(self) -> bool:
        """准备 Panda3D 渲染层（离屏 3D；游戏画面由它渲染）。"""
        if self.scene3d is not None:
            return True
        if not render3d.available():
            self.message = f"缺少 Panda3D 渲染层：{render3d.import_error()}"
            return False
        if os.environ.get("SDL_VIDEODRIVER", "").lower() == "dummy":
            return False                          # 测试环境不建 GL 上下文
        if self.settings.renderer != "panda3d":
            return False
        if not render3d.available():
            self._fallback_renderer(f"缺少 Panda3D：{render3d.import_error()}")
            return False
        if os.environ.get("VERUS_SOFTWARE_3D"):
            return False
        try:
            self.scene3d = render3d.Scene3D(self.w, self.h, self.texture_dir)
        except Exception as exc:                  # pragma: no cover - 依赖驱动
            self._fallback_renderer(f"Panda3D 初始化失败：{exc}")
            return False
        self.renderer_name = "panda3d"
        return True

    def _fallback_renderer(self, why: str) -> None:
        """Panda3D 不可用 -> OpenGL；OpenGL 也不可用 -> 软件（用户要求的回退链）。"""
        target = "opengl" if glview.available() else "software"
        self.settings.renderer = target
        psettings.save_settings(self.settings)
        self.scene3d = None
        self.screen = self._make_display(self.w, self.h)
        self.renderer_name = "opengl" if self.gl_presenter is not None else "software"
        self.message = (
            f"{why}，已回退到{'OpenGL' if self.renderer_name == 'opengl' else '软件'}渲染"
        )
        print(f"[In Verus 游玩器] {self.message}", flush=True)
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

    def _tex_scaled(self, name: str, width: int, height: int):
        """按目标尺寸缩放贴图，带缓存（游戏内不再重复缩放）。"""
        width = max(1, int(width))
        height = max(1, int(height))
        key = (name, width, height)
        cached = self._tex_cache.get(key)
        if cached is not None:
            return cached
        surface = self._tex(name)
        if surface is None:
            return None
        if surface.get_size() != (width, height):
            surface = pygame.transform.smoothscale(surface, (width, height))
        if len(self._tex_cache) > 512:                # 防止尺寸组合过多
            self._tex_cache.clear()
        self._tex_cache[key] = surface
        return surface

    def _blit_tex(self, name: str, rect, flip_x: bool = False, alpha: int = 255) -> bool:
        """把贴图铺到屏幕上的矩形区域（返回是否真的画了）。"""
        surface = self._tex_scaled(name, rect[2], rect[3])
        if surface is None:
            return False
        if flip_x:
            surface = pygame.transform.flip(surface, True, False)
        if alpha < 255:
            surface = surface.copy()
            surface.set_alpha(alpha)
        self.screen.blit(surface, (rect[0], rect[1]))
        return True

    def _blit_tex_quad(self, name: str, corners, flip_x: bool = False) -> bool:
        """按**仿射**把贴图铺到投影后的四边形上（不是拉伸到外接矩形）。

        ``corners`` 顺序与贴图一致：``[左上, 右上, 右下, 左下]``。
        用“上边向量”定旋转角与宽度、“左边向量”定高度，再以四边形中心对齐 blit；
        6K 平面里再配合“按屏幕高度切片”，透视就不会被抹平成方块。
        """
        surface = self._tex(name)
        if surface is None or len(corners) != 4:
            return False
        points = [(float(p[0]), float(p[1])) for p in corners]
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
        edge_x = (x1 - x0, y1 - y0)
        edge_y = (x3 - x0, y3 - y0)
        width = max(1.0, math.hypot(*edge_x))
        height = max(1.0, math.hypot(*edge_y))
        angle = -math.degrees(math.atan2(edge_x[1], edge_x[0]))
        key = (name, int(round(width)), int(round(height)), round(angle * 2) / 2.0, flip_x)
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

    @staticmethod
    def _bbox(points):
        """一组屏幕点 -> 外接矩形 (x, y, w, h)。"""
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        left, top = min(xs), min(ys)
        return (left, top, max(1.0, max(xs) - left), max(1.0, max(ys) - top))

    def _begin_chart(self, path: str) -> bool:
        """读取谱面并进加载页；读不出来就留在主界面并给出提示。"""
        try:
            chart, _ = load_chart(path)
        except Exception as exc:                     # pragma: no cover - 依赖外部文件
            self.message = f"读取失败：{exc}"
            return False
        self.chart = chart
        self.chart_path = path
        self._remember_recent(self.chart_path)
        self.state = "loading"
        self.timer = 0.0
        self.message = ""
        return True

    # ------------------------------------------------------ 打开其他路径
    def open_external_chart(self, path: Optional[str] = None) -> bool:
        """打开 ``charts/`` 之外的谱面：先弹系统文件对话框，不可用时窗口内输入路径。"""
        if path is None:
            path = self._ask_chart_path()
        if not path:
            return False
        path = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
        if not os.path.isfile(path):
            self.message = f"找不到文件：{path}"
            return False
        if path not in self.paths:
            self.paths.append(path)
        self.selected = self.paths.index(path)
        return self._begin_chart(path)

    def _ask_chart_path(self) -> Optional[str]:
        """要一个谱面 JSON 路径：优先系统对话框，退化到窗口内输入（Esc 取消）。"""
        picked = self._native_open_dialog()
        if picked:
            return picked
        return self._prompt("谱面 JSON 路径", "")

    def _native_open_dialog(self) -> Optional[str]:
        """子进程里用 tkinter 弹“打开文件”对话框（无 tkinter / 无显示时返回 None）。"""
        if os.environ.get("VERUS_NO_DIALOG"):
            return None
        if os.environ.get("SDL_VIDEODRIVER", "").lower() == "dummy":
            return None
        initial = os.path.join(self.root, "charts")
        if not os.path.isdir(initial):
            initial = self.root
        script = "\n".join(
            (
                "import tkinter as tk",
                "from tkinter import filedialog",
                "root = tk.Tk()",
                "root.withdraw()",
                "root.attributes('-topmost', True)",
                "print(filedialog.askopenfilename(",
                f"    initialdir={initial!r}, title='选择谱面 JSON',",
                "    filetypes=[('谱面 JSON', '*.json'), ('所有文件', '*.*')]) or '')",
            )
        )
        try:
            done = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=1800,
            )
        except Exception:
            return None
        if done.returncode != 0:
            return None
        return (done.stdout or "").strip() or None

    # ------------------------------------------------------------ 谱面列表
    def _chart_info(self, path: str):
        """谱面的 (标题, 艺术家, 难度名+等级, 初始 BPM)；读不出来就退回文件名。"""
        info = self._info_cache.get(path)
        if info is not None:
            return info
        try:
            chart, _raw = load_chart(path)
            meta = chart.metadata
            info = (
                meta.title or os.path.basename(path),
                meta.artist or "未知艺术家",
                f"{meta.difficulty_name} {meta.difficulty_level}".strip(),
                chart.timing.bpm_at(0.0),
            )
        except Exception:                       # pragma: no cover - 依赖外部文件
            info = (os.path.basename(path), "（无法读取）", "-", 0.0)
        self._info_cache[path] = info
        return info

    def _rebuild_entries(self) -> None:
        """最近打开的谱面排在前面，其余按路径排序。"""
        recent = [p for p in self.settings.recent if p in self.paths]
        rest = [p for p in self.paths if p not in recent]
        self.paths = recent + rest
        self.selected = max(0, min(int(getattr(self, "selected", 0)), self._menu_rows() - 1))

    def _remember_recent(self, path: str) -> None:
        recent = [p for p in self.settings.recent if p != path]
        recent.insert(0, path)
        self.settings.recent = recent[:8]
        psettings.save_settings(self.settings)
        chosen = self.paths[self.selected] if self.selected < len(self.paths) else None
        self._rebuild_entries()
        if chosen is not None and chosen in self.paths:
            self.selected = self.paths.index(chosen)

    def _display_path(self, path: str) -> str:
        """谱面显示用路径：仓库内的用相对路径，仓库外的（打开其他路径）用绝对路径。"""
        try:
            rel = os.path.relpath(path, self.root)
        except ValueError:                          # pragma: no cover - 跨盘符
            return path
        return path if rel.startswith("..") else rel

    # ------------------------------------------------------------ 设置
    SETTING_ROWS = (
        ("player_name", "玩家名称（回车输入）"),
        ("keys", "6K 键位（回车逐轨输入）"),
        ("note_thickness", "单键厚度"),
        ("multi_thickness", "多轨单键厚度"),
        ("slide_thickness", "滑键厚度"),
        ("click_thickness", "鼠标点击厚度"),
        ("audio_latency_ms", "系统音频延迟(ms)"),
        ("speed", "流速（可视毫秒 = 1500 / 流速）"),
        ("mouse_sensitivity", "鼠标灵敏度"),
        ("side_lane_highlight", "侧轨高亮"),
        ("unbounded_mouse", "无界鼠标模式"),
        ("music_volume", "音乐音量"),
        ("hit_volume", "打击音效音量"),
        ("renderer", "渲染后端（Panda3D / OpenGL / 软件）"),
        ("rate", "倍速（0.1~1.5x）"),
        ("autoplay", "自动播放（Autoplay）"),
    )
    # 数字类选项（有 − / ● / + 三个按钮；点 ● 可以自定义输入）：字段名 -> (最小, 最大, 步进)
    NUMERIC_ROWS = {
        "note_thickness": (0.05, 1.0, 0.05),
        "multi_thickness": (0.05, 1.0, 0.05),
        "slide_thickness": (0.005, 0.3, 0.005),
        "click_thickness": (0.005, 0.3, 0.005),
        "audio_latency_ms": (-5000.0, 5000.0, 5.0),
        "speed": (psettings.SPEED_MIN, psettings.SPEED_MAX, psettings.SPEED_STEP),
        "mouse_sensitivity": (0.1, 10.0, 0.1),
        "music_volume": (0.0, 1.0, 0.05),
        "hit_volume": (0.0, 1.0, 0.05),
        "rate": (psettings.RATE_MIN, psettings.RATE_MAX, psettings.RATE_STEP),
    }
    TOGGLE_ROWS = ("side_lane_highlight", "unbounded_mouse", "autoplay")

    def _row_key(self, index: Optional[int] = None) -> str:
        return self.SETTING_ROWS[self.settings_row if index is None else index][0]

    def _row_label(self, index: Optional[int] = None) -> str:
        return self.SETTING_ROWS[self.settings_row if index is None else index][1]

    def _row_index(self, key: str) -> int:
        """字段名 -> 行号（测试 / 内部跳转用）。"""
        for index, (name, _label) in enumerate(self.SETTING_ROWS):
            if name == key:
                return index
        raise KeyError(key)

    def _setting_value_text(self, key: str) -> str:
        s = self.settings
        if key == "player_name":
            return s.player_name
        if key == "keys":
            return " ".join(s.keys)
        if key == "note_thickness":
            return f"{s.note_thickness:.2f}"
        if key == "multi_thickness":
            return f"{s.multi_thickness:.2f}"
        if key == "slide_thickness":
            return f"{s.slide_thickness:.3f}"
        if key == "click_thickness":
            return f"{s.click_thickness:.3f}"
        if key == "audio_latency_ms":
            return f"{s.audio_latency_ms:.0f} ms"
        if key == "speed":
            return f"{s.speed:.1f}（可视 {s.scroll_ms:.0f} ms）"
        if key == "mouse_sensitivity":
            return f"{s.mouse_sensitivity:.2f}x"
        if key == "side_lane_highlight":
            return "开" if s.side_lane_highlight else "关"
        if key == "unbounded_mouse":
            return "开" if s.unbounded_mouse else "关"
        if key == "music_volume":
            return f"{s.music_volume * 100:.0f}%"
        if key == "hit_volume":
            return f"{s.hit_volume * 100:.0f}%"
        if key == "renderer":
            return {"panda3d": "Panda3D（3D 场景）", "opengl": "OpenGL（软件画面经 GL 呈现）",
                    "software": "软件"}[s.renderer]
        if key == "autoplay":
            return "开（判定全 EXACT · 分数倍率 ×-倍速^1.5）" if s.autoplay else "关"
        return f"{_fmt_num(s.rate)}x（得分 ×{s.rate ** 1.5:.2f}）"

    def _event_settings(self, event) -> None:
        if event.type == pygame.MOUSEWHEEL:
            self.settings_row = (self.settings_row - event.y) % len(self.SETTING_ROWS)
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._click_settings(event.pos)
            return
        if event.type != pygame.KEYDOWN:
            return
        s = self.settings
        if event.key == pygame.K_ESCAPE:
            psettings.save_settings(s)
            self.state = "menu"
        elif event.key in (pygame.K_DOWN, pygame.K_UP):
            step = 1 if event.key == pygame.K_DOWN else -1
            self.settings_row = (self.settings_row + step) % len(self.SETTING_ROWS)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._edit_setting_text()
            psettings.save_settings(s)
        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            self._adjust_setting(1 if event.key == pygame.K_RIGHT else -1)
            psettings.save_settings(s)

    def _click_settings(self, pos) -> None:
        """鼠标点设置行：选中；再点一次那一行 = 编辑 / 切换；数字行的 − ● + 直接操作。"""
        key = self._hit_ui(pos)
        if not key:
            return
        kind, _, raw = key.partition(":")
        index = int(raw)
        if kind == "set_minus":
            self.settings_row = index
            self._adjust_setting(-1)
        elif kind == "set_plus":
            self.settings_row = index
            self._adjust_setting(1)
        elif kind == "set_dot":
            self.settings_row = index
            self._prompt_number(self._row_key())
        elif kind == "set":
            if index == self.settings_row:
                self._edit_setting_text()
            else:
                self.settings_row = index
        psettings.save_settings(self.settings)

    def _edit_setting_text(self) -> None:
        s = self.settings
        key = self._row_key()
        if key == "player_name":
            entered = self._prompt("玩家名称", s.player_name)
            if entered is not None:
                s.player_name = entered
        elif key == "keys":
            for index, name in enumerate(s.keys):
                picked = self._prompt_key(f"第 {index + 1} 轨（现在是 {name}）")
                if not picked or picked == name:
                    continue
                # 别的轨道占着这个键就交换，避免两条轨道共用一个键（会互相抢判）
                if picked in s.keys:
                    other = s.keys.index(picked)
                    s.keys[other] = name
                s.keys[index] = picked
        elif key in self.NUMERIC_ROWS:
            self._prompt_number(key)
        elif key in self.TOGGLE_ROWS:
            setattr(s, key, not getattr(s, key))
        elif key == "renderer":
            self._switch_renderer()
        s.normalize()

    def _setting_number(self, key: str) -> float:
        return float(getattr(self.settings, key))

    def _set_setting_number(self, key: str, value: float) -> None:
        s = self.settings
        setattr(s, key, value)
        if key == "music_volume" and self.state == "play":
            self.audio.set_volume(s.music_volume)
        elif key == "hit_volume" and self.state == "play":
            self.hits.set_volume(s.hit_volume)
        elif key == "rate":
            if self.judge is not None:
                self.judge.rate = s.rate
            self.audio.set_rate(s.rate)
        s.normalize()

    def _prompt_number(self, key: str) -> None:
        """数字类选项的自定义输入（点 ● 或在选中行按回车）。

        **手输的数值不受档位影响**：只做范围夹取，不按步长对齐
        （步长只决定 − / + 按钮每次动多少）。
        """
        low, high, step = self.NUMERIC_ROWS[key]
        current = self._setting_number(key)
        entered = self._prompt(
            f"{self._row_label()}：{low:g} ~ {high:g}（− / + 步长 {step:g}）",
            f"{current:g}",
            select_all=True,
        )
        if entered is None:
            return
        text = entered.strip().replace("，", ".").replace(",", ".")
        if text.endswith("x"):
            text = text[:-1]
        try:
            value = float(text)
        except ValueError:
            self.message = f"“{entered}”不是数字"
            return
        self._set_setting_number(key, round(min(high, max(low, value)), 3))
        self.message = ""

    def _adjust_setting(self, step: int) -> None:
        key = self._row_key()
        if key in self.NUMERIC_ROWS:
            low, high, delta = self.NUMERIC_ROWS[key]
            value = min(high, max(low, self._setting_number(key) + delta * step))
            self._set_setting_number(key, round(value, 3))
        elif key in ("player_name", "keys"):
            return                                    # 文本类：回车 / 点行才输入
        else:
            self._edit_setting_text()                 # 开关 / 后端：左右键等价于切换

    def _prompt(self, label: str, initial: str, select_all: bool = False) -> Optional[str]:
        """窗口内文本输入（回车确认 / Esc 取消）。

        用 ``TEXTINPUT`` 事件收字（**中文 IME 也能输入**），并且走和主循环一样的
        ``_present()`` 呈现路径，所以 OpenGL 后端下也能看到自己打的字。
        ``select_all=True`` 时第一次输入 / 退格会**替换**原文本（数字类选项用）。
        """
        entered = initial or ""
        caret = len(entered)
        replacing = bool(select_all)
        text_input = True
        try:
            pygame.key.start_text_input()
        except Exception:                        # pragma: no cover - 老版本 pygame
            text_input = False
        try:
            pygame.key.set_repeat(400, 40)       # 退格 / 方向键可以按住
        except Exception:                        # pragma: no cover
            pass
        result: Optional[str] = None
        done = False
        while self.running and not done:
            self._draw_input_overlay(label, entered, caret)
            self._present()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break
                if event.type == pygame.TEXTINPUT:
                    if replacing:
                        entered = event.text
                        caret = len(entered)
                        replacing = False
                    else:
                        entered = entered[:caret] + event.text + entered[caret:]
                        caret += len(event.text)
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        result = entered
                        done = True
                        break
                    if event.key == pygame.K_ESCAPE:
                        done = True
                        break
                    if event.key == pygame.K_BACKSPACE:
                        if replacing:
                            entered = ""
                            caret = 0
                            replacing = False
                        elif caret > 0:
                            entered = entered[: caret - 1] + entered[caret:]
                            caret -= 1
                    elif event.key == pygame.K_DELETE:
                        if caret < len(entered):
                            entered = entered[:caret] + entered[caret + 1:]
                    elif event.key == pygame.K_LEFT:
                        caret = max(0, caret - 1)
                    elif event.key == pygame.K_RIGHT:
                        caret = min(len(entered), caret + 1)
                    elif event.key == pygame.K_HOME:
                        caret = 0
                    elif event.key == pygame.K_END:
                        caret = len(entered)
                    elif not text_input and event.unicode and event.unicode.isprintable():
                        entered = entered[:caret] + event.unicode + entered[caret:]
                        caret += len(event.unicode)
        try:
            pygame.key.stop_text_input()
        except Exception:                        # pragma: no cover
            pass
        try:
            pygame.key.set_repeat()
        except Exception:                        # pragma: no cover
            pass
        return result

    def _draw_input_overlay(self, label: str, text: str, caret: int) -> None:
        self.screen.fill(BG)
        self.center_text("输入", 110, self.f_mid, DIM)
        self.text(label, (120, 180), self.f_mid, WHITE)
        box = pygame.Rect(120, 220, self.w - 240, 56)
        pygame.draw.rect(self.screen, PANEL, box, border_radius=6)
        pygame.draw.rect(self.screen, ACCENT, box, 2, border_radius=6)
        shown = text if len(text) <= 48 else "…" + text[-47:]
        self.text(shown, (box.x + 12, box.y + 12), self.f_mid, WHITE)
        prefix = self.f_mid.size(text[:caret])[0]
        cursor_x = box.x + 12 + prefix
        if len(text) > 48:                       # 太长的文本只滚末尾，光标跟着走
            cursor_x = box.x + 12 + self.f_mid.size(shown)[0]
        pygame.draw.line(
            self.screen, ACCENT, (cursor_x, box.y + 10), (cursor_x, box.y + 44), 2
        )
        self.text("回车 确认 · Esc 取消 · 支持中文输入", (120, 300), self.f_small, DIM)

    def _prompt_key(self, label: str) -> Optional[str]:
        """按键位：**直接按想设的那个键**（Esc 取消），避免手打键名出错。"""
        result: Optional[str] = None
        done = False
        while self.running and not done:
            self.screen.fill(BG)
            self.center_text("设定键位", 140, self.f_mid, DIM)
            self.center_text(label, 210, self.f_big, WHITE)
            self.center_text("直接按下要用的键（Esc 取消）", 290, self.f_small, ACCENT)
            self._present()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    break
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        done = True
                        break
                    name = pygame.key.name(event.key)
                    if name:
                        result = name
                    done = True
                    break
        return result

    # ------------------------------------------------------------ 游玩
    def _start_play(self) -> None:
        assert self.chart is not None
        self.judge = Judge(
            self.chart, rate=self.settings.rate, autoplay=self.settings.autoplay
        )
        self.pressed_lanes.clear()
        self.paused = False
        self.ending = False
        self.end_timer = 0.0
        self.idle_seconds = 0.0
        self.message = ""
        self.audio.latency_ms = self.settings.audio_latency_ms
        self.audio.set_volume(self.settings.music_volume)
        self.hits.set_volume(self.settings.hit_volume)
        self.hits.set_enabled(True)
        self.popups.clear()
        # 首拍引导：**永远是 1.5 秒真实时间**，且音乐与物件同步。
        # 判定时钟按倍速推进，所以“进入游玩时的歌曲毫秒”要退到 `offset − 倍速 × 1.5s`：
        # 倍速再高，第 0 拍也恰好 1.5 秒后才到判定平面；如果这个位置已经是负数
        # （谱面 offset 很短），先静音等待歌曲位置走到 0，再从 0 开始放音 ——
        # 中间不会跳时间，所以不会出现“谱面还没开始物件就已经过线”。
        rate = self.settings.rate
        offset_ms = self.chart.timing.beat_to_song_ms(0.0)
        self.lead_start_ms = offset_ms - LEAD_MS * rate
        self.audio_start_ms = max(0.0, self.lead_start_ms)
        self.audio_started = self.lead_start_ms >= 0.0
        self.lead_wait_real_ms = (
            0.0 if self.audio_started else (-self.lead_start_ms) / max(1e-6, rate)
        )
        self.play_wall = time.perf_counter()
        self.pause_wall = self.play_wall
        self.audio.set_rate(self.settings.rate)
        self.audio.load(chart_audio_path(self.chart_path, self.chart))
        if self.audio_started:
            self.audio.play(self.audio_start_ms)
        self.state = "play"
        self.timer = 0.0

    def _now_ms(self) -> float:
        """判定用的歌曲毫秒：**就是听到的音频位置**（所以音乐和物件天然同步）。

        首拍引导（**1.5 秒真实时间**，与倍速无关）靠“进入游玩时的歌曲毫秒 =
        `第 0 拍 − 倍速 × 1.5s`”实现；音频还没到 0 时先用挂钟按倍速推进，
        走到 0 再起播，因此不会出现时间跳跃。
        """
        if self.audio_started:
            return self.audio.heard_position_ms()
        now = time.perf_counter()
        if self.paused:                     # 暂停时引导时间也停住
            now = self.pause_wall
        elapsed_real_ms = (now - self.play_wall) * 1000.0
        return self.lead_start_ms + elapsed_real_ms * self.settings.rate

    def _begin_audio(self) -> None:
        """引导时间结束：真正开始播放（此后判定时钟就是音频位置）。"""
        self.audio_started = True
        self.audio.play(self.audio_start_ms)

    def _event_play(self, event) -> None:
        assert self.judge is not None
        if self.paused:
            # 暂停时鼠标可以点「继续 / 重试 / 回主界面」
            if event.type == pygame.MOUSEMOTION:
                self.hover = self._hit_ui(event.pos) or ""
                return
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                key = self._hit_ui(event.pos)
                if key == "resume":
                    self._toggle_pause()
                elif key == "retry":
                    self.paused = False
                    self._start_play()
                elif key == "back":
                    self.paused = False
                    self.state = "menu"
                return
        if event.type == pygame.KEYDOWN:
            if self.paused:
                # 暂停中的快捷键（空格 / 回车继续、R 重试、Esc 回主界面）
                if event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._toggle_pause()
                elif event.key == pygame.K_r:
                    self.paused = False
                    self._start_play()
                elif event.key == pygame.K_ESCAPE:
                    self.paused = False
                    self.state = "menu"
                return
            # **先看是不是轨道键**：默认第 6 轨就是空格，别让暂停快捷键抢掉它
            matched = [
                lane
                for lane, name in enumerate(self.settings.keys)
                if event.key == key_code(name)
            ]
            if matched:
                # 同一个键绑了多条轨道时都算按下（自动播放时只用于轨道高亮）
                self.pressed_lanes.update(matched)
                if self.settings.autoplay:
                    return                    # 自动播放：输入不参与判定
                now = self._now_ms()
                for lane in matched:
                    result = self.judge.on_press(now, lane=lane)
                    if result is not None:
                        self._register(result)
                        break
                return
            if event.key in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_EQUALS, pygame.K_PLUS):
                step = -1 if event.key in (pygame.K_MINUS, pygame.K_KP_MINUS) else 1
                self._adjust_rate(step)
                return
            if event.key == pygame.K_SPACE:
                self._toggle_pause()
                return
            if event.key == pygame.K_ESCAPE:
                self._toggle_pause()
                return
        elif event.type == pygame.KEYUP:
            for lane, name in enumerate(self.settings.keys):
                if event.key == key_code(name):
                    self.pressed_lanes.discard(lane)
        elif event.type == pygame.MOUSEBUTTONDOWN:
            # 鼠标点击按键：**左右键都可以**（滚轮 4/5 不算）
            if event.button in (1, 3) and not self.settings.autoplay:
                self._register(self.judge.on_press(self._now_ms(), x=self.mouse_norm))

    def _register(self, result) -> None:
        """记录一条判定：命中音效 + 在**被判定的那个物件处**飘字。"""
        if result is None:
            return
        if result.grade != BREAK:
            self.hits.play_hit()
        note = None
        if self.judge is not None:
            note = self.judge.notes.get(getattr(result, "note_id", ""))
        x, y = self._popup_anchor(note)
        self.popups.append((result.grade, result.grade, 0.7, x, y))

    def _player_label(self) -> str:
        """玩家名：开启自动播放时显示为 ``Autoplay``。"""
        return "Autoplay" if self.settings.autoplay else self.settings.player_name

    def _screen_point(self, world_x: float, world_y: float, world_z: float = 0.0):
        """世界点 -> 屏幕像素：Panda3D 路径用 Panda 相机，软件路径用软件投影。"""
        if self.scene3d is not None:
            point = self.scene3d.project(world_x, world_y, world_z)
            if point is not None:
                return point
        projected = self._proj(world_x, world_y, world_z)
        return projected[0], projected[1]

    def _judge_line_screen_y(self, plane_y: float) -> float:
        """判定线的屏幕 y（连击数字等的锚点）。"""
        if self.scene3d is not None:
            return self.scene3d.judge_line_y(plane_y)
        return self.scene.judgment_lines(self.w, self.h)[
            "mouse" if plane_y > 0.0 else "k6"
        ]

    def _popup_anchor(self, note: Optional[Note]) -> Tuple[float, float]:
        """判定飘字锚点：单键 / 多轨在 6K 线上，鼠标物件在鼠标线上（瞬时物件也要有字符）。"""
        if isinstance(note, LaneNote):
            low, high = min(note.lane_start, note.lane_end), max(note.lane_start, note.lane_end)
            world_x = K6_LEFT + (low + high + 1) / 2.0 * LANE_WIDTH
            world_y = 0.0
        else:
            world_x, world_y = 0.0, MOUSE_PLANE_HEIGHT
            if note is not None:
                try:
                    lo, hi = note.x_range()
                except Exception:                    # pragma: no cover - 兜底
                    lo, hi = 0.5, 0.5
                world_x = MOUSE_LEFT + (min(lo, hi) + max(lo, hi)) / 2.0 * MOUSE_LEGAL_WIDTH
        return self._screen_point(world_x, world_y, 0.0)

    def _update(self, dt: float) -> None:
        # 慢放后端（重采样）需要每帧喂下一段窗口；不喂的话低倍速时只会播第一段，
        # 谱面时钟会跟着卡住、音乐随后消失
        self.audio.poll()
        if self.state == "loading":
            self.timer += dt
            if self.timer >= 1.2:
                self._start_play()
        elif self.state == "play":
            if not self.audio_started and not self.paused:
                if (time.perf_counter() - self.play_wall) * 1000.0 >= self.lead_wait_real_ms:
                    self._begin_audio()
            if self.paused:
                self.timer += 0.0
                return
            self.timer += dt
            self.mouse_norm = self._mouse_norm()
            assert self.judge is not None
            now_ms = self._now_ms()
            # 滑键是在鼠标移动时判定的：返回值必须登记，否则滑键没有判定文字 / 音效
            if not self.settings.autoplay:
                self._register(self.judge.on_move(now_ms, self.mouse_norm))
            for result in self.judge.update(
                now_ms, inside_hold=self._inside_region(now_ms), held=set(self.pressed_lanes)
            ):
                self._register(result)
            self.popups = [
                (grade, text, left - dt, x, y)
                for grade, text, left, x, y in self.popups
                if left > dt
            ]
            if self.ending:
                # 谱面结束后至少再等 END_DELAY_S（让最后的判定 / 音乐收尾）
                self.end_timer -= dt
                if self.end_timer <= 0:
                    self._finish()
            elif self.judge.judged_notes >= self.judge.total_notes and now_ms > 0:
                self._begin_ending()
            elif not self.audio.is_playing and now_ms > 0:
                # 换窗口的瞬间可能短暂“没在播”，多等一会儿再判定结束
                self.idle_seconds += dt
                if self.idle_seconds > 0.5:
                    self._begin_ending()
            else:
                self.idle_seconds = 0.0

    def _begin_ending(self) -> None:
        """开始“谱面结束”的等待：至少 END_DELAY_S 秒后才进结算。"""
        if self.ending:
            return
        self.ending = True
        self.end_timer = END_DELAY_S

    def _finish(self) -> None:
        self.audio.pause()
        self.paused = False
        self.ending = False
        self.end_timer = 0.0
        self.state = "result"

    def _toggle_pause(self) -> None:
        """空格暂停 / 继续（暂停时音乐也停下）。"""
        self.paused = not self.paused
        if self.paused:
            self.pause_wall = time.perf_counter()
            if self.audio_started:
                self.audio.pause()
        else:
            self.status_hint = ""
            if self.audio_started:
                self.audio.set_rate(self.audio.rate)     # 保持当前倍速
                self.audio.play(self.audio.position_ms())
            else:
                # 引导时间内暂停：把起点往后挪，剩余的引导时间不受影响
                self.play_wall += time.perf_counter() - self.pause_wall

    def _adjust_rate(self, step: int) -> None:
        """游玩中调倍速（与编辑器一致：换后端容易出问题，因此自动暂停）。"""
        old = self.settings.rate
        self.settings.rate = round(
            min(psettings.RATE_MAX, max(psettings.RATE_MIN, old + psettings.RATE_STEP * step)), 1
        )
        if abs(self.settings.rate - old) < 1e-9:
            return
        psettings.save_settings(self.settings)
        if self.judge is not None:
            self.judge.rate = self.settings.rate
        self.audio.set_rate(self.settings.rate)
        if not self.paused:
            self._toggle_pause()
        self.status_hint = (
            f"倍速 {self.settings.rate:.1f}x（得分 ×{self.settings.rate ** 1.5:.2f}）"
            f" · 已自动暂停，按空格继续"
        )

    def _inside_region(self, now_ms: float) -> Optional[Note]:
        """当前鼠标是否在某个空域内（判定范围两边各放宽 0.01）。"""
        assert self.chart is not None
        beat = self.chart.timing.song_ms_to_beat(now_ms)
        for note in self.chart.mouse_notes:
            if not isinstance(note, MouseRegion):
                continue
            if not (note.beat_start - 1e-6 <= beat <= note.beat_end + 1e-6):
                continue
            low, high = note.left_at(beat), note.right_at(beat)
            lo, hi = min(low, high) - REGION_PAD, max(low, high) + REGION_PAD
            if lo <= self.mouse_norm <= hi:
                return note
        return None

    def _mouse_norm(self) -> float:
        """屏幕鼠标 -> 归一化横坐标（含灵敏度；关闭无界鼠标时夹在 0..1）。"""
        mx, _ = pygame.mouse.get_pos()
        left, _, _ = self.scene.point(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0, self.w, self.h)
        right, _, _ = self.scene.point(
            MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0, self.w, self.h
        )
        raw = (mx - left) / max(1.0, right - left)
        value = 0.5 + (raw - 0.5) * self.settings.mouse_sensitivity
        self.mouse_raw = value            # 实际位置（可能出界，用于画“边线”指针）
        if not self.settings.unbounded_mouse:
            value = min(1.0, max(0.0, value))
        return value

    # ------------------------------------------------------------ 绘制
    def _draw(self) -> None:
        self.ui.clear()
        self.screen.fill(BG)
        if self.state == "menu":
            self._draw_menu()
        elif self.state == "settings":
            self._draw_settings()
        elif self.state == "loading":
            self._draw_loading()
        elif self.state == "play":
            self._draw_play()
        elif self.state == "result":
            self._draw_result()
        if self.state in ("menu", "settings", "result") and not pygame.key.get_focused():
            self.center_text(
                "窗口没有键盘焦点：先点一下窗口再敲键",
                self.h - 24,
                self.f_small,
                (240, 176, 120),
            )

    def _draw_menu(self) -> None:
        self.center_text("In Verus 游玩器", 64, self.f_big, WHITE)
        self.center_text(
            "↑↓ 选择 · Enter 确认 · O 打开其他 JSON · S 设置 · F2 渲染后端 · F10 最大化 · F11 窗口/无边框/全屏 · Esc 退出",
            122,
            self.f_small,
            DIM,
        )
        if not self.paths:
            self.center_text(
                "没有找到谱面：charts/ 下每个含 .json 的文件夹算一张谱面",
                166,
                self.f_small,
                (214, 176, 120),
            )
        # 只显示选中项附近的若干行（谱面很多时翻页显示）
        charts = len(self.paths)
        window = max(1, min(MENU_WINDOW, (self.h - 330) // 50))
        focus = min(self.selected, max(0, charts - 1))
        start = max(0, min(focus - window // 2, max(0, charts - window)))
        for offset, index in enumerate(range(start, min(charts, start + window))):
            path = self.paths[index]
            title, artist, difficulty, bpm = self._chart_info(path)
            chosen = index == self.selected
            color = ACCENT if chosen else DIM
            marker = "> " if chosen else "  "
            star = "* " if path in self.settings.recent else ""
            y = 192 + offset * 50
            row = pygame.Rect(self.w * 0.10, y - 6, self.w * 0.8, 46)
            self.ui[f"chart:{index}"] = row
            if chosen or self.hover == f"chart:{index}":
                pygame.draw.rect(self.screen, PANEL, row, border_radius=6)
            self.text(
                f"{marker}{star}{title} · {artist} · 难度 {difficulty} · {bpm:.0f}BPM",
                (self.w * 0.12, y),
                self.f_mid,
                color,
            )
            self.text(
                self._display_path(path),
                (self.w * 0.12 + 28, y + 26),
                self.f_small,
                (110, 110, 124) if chosen else (86, 86, 96),
            )
        # 固定在窗口底部的两行：打开其他路径 / 设置（没有谱面时也能进设置）
        actions = (
            ("＋ 打开其他路径的 JSON…", "在任何位置选一张谱面（Esc 取消）"),
            ("设置", "玩家名 / 键位 / 延迟 / 倍速 / 音量 …"),
        )
        row_h = 56
        action_top = self.h - 40 - row_h * len(actions)
        for offset, (label, hint) in enumerate(actions):
            index = charts + offset
            chosen = index == self.selected
            y = action_top + offset * row_h
            row = pygame.Rect(self.w * 0.10, y - 6, self.w * 0.8, row_h - 6)
            self.ui[f"action:{offset}"] = row
            hovered = self.hover == f"action:{offset}"
            pygame.draw.rect(
                self.screen,
                (44, 44, 58) if hovered and not chosen else PANEL,
                row,
                border_radius=6,
            )
            self.text(
                f"{'> ' if chosen else '  '}{label}",
                (self.w * 0.12, y),
                self.f_mid,
                ACCENT if chosen else WHITE,
            )
            self.text(hint, (self.w * 0.12 + 28, y + 26), self.f_small, (120, 120, 134))
        if self.message:
            self.center_text(
                self.message, action_top - 34, self.f_small, (240, 120, 120)
            )

    def _draw_settings(self, extra: str = "") -> None:
        self.text(
            "设置（↑↓ / 滚轮选择 · ←→ 或 − + 调整 · ● 自定义输入 · 回车输入/切换 · Esc 返回）",
            (40, 26),
            self.f_mid,
            WHITE,
        )
        # 窗口矮的时候自动压缩行距，保证 12 行 + 底部说明都能放下
        top = 96
        pitch = min(44, max(30, (self.h - top - 80) // len(self.SETTING_ROWS)))
        for index, (_key, label) in enumerate(self.SETTING_ROWS):
            y = top + index * pitch
            chosen = index == self.settings_row
            row = pygame.Rect(40, y - 6, self.w - 80, 40)
            self.ui[f"set:{index}"] = row
            if chosen:
                pygame.draw.rect(self.screen, PANEL, row, border_radius=6)
            label_text = ("> " if chosen else "  ") + label
            self.text(label_text, (60, y), self.f_mid, ACCENT if chosen else DIM)
            row_key = _key
            numeric = row_key in self.NUMERIC_ROWS
            value_right = self.w - 60
            if numeric:                       # 数字类：− ● +（● = 自定义输入）
                minus = pygame.Rect(self.w - 196, y - 6, 34, 38)
                dot = pygame.Rect(self.w - 150, y - 6, 34, 38)
                plus = pygame.Rect(self.w - 104, y - 6, 34, 38)
                self.ui[f"set_minus:{index}"] = minus
                self.ui[f"set_dot:{index}"] = dot
                self.ui[f"set_plus:{index}"] = plus
                for rect, glyph in ((minus, "-"), (plus, "+")):
                    pygame.draw.rect(self.screen, (44, 44, 58), rect, border_radius=4)
                    glyph_surface = self.f_mid.render(glyph, True, WHITE)
                    self.screen.blit(
                        glyph_surface,
                        (
                            rect.centerx - glyph_surface.get_width() / 2,
                            rect.centery - glyph_surface.get_height() / 2,
                        ),
                    )
                # 中间是**圆点按钮**：点它就能自定义输入数值
                pygame.draw.rect(self.screen, (44, 44, 58), dot, border_radius=4)
                pygame.draw.circle(
                    self.screen,
                    ACCENT if self.hover == f"set_dot:{index}" else WHITE,
                    dot.center,
                    9,
                )
                value_right = minus.x - 16
            label_width = self.f_mid.size(label_text)[0]
            text = self._fit_text(
                self._setting_value_text(row_key), self.f_mid, value_right - (60 + label_width + 24)
            )
            surface = self.f_mid.render(text, True, WHITE)
            self.screen.blit(surface, (value_right - surface.get_width(), y))
        if extra:
            self.text(extra, (60, self.h - 62), self.f_small, (255, 226, 120))
        self.text("设置保存在 player.json（全局，不写进谱面）", (60, self.h - 30), self.f_small, DIM)

    @staticmethod
    def _fit_text(text: str, font, max_width: int) -> str:
        """把文字裁到 ``max_width`` 内（超出部分用 …），免得压到别的控件上。"""
        if max_width <= 12:
            return ""
        if font.size(text)[0] <= max_width:
            return text
        trimmed = text
        while trimmed and font.size(trimmed + "…")[0] > max_width:
            trimmed = trimmed[:-1]
        return trimmed + "…"

    def _draw_loading(self) -> None:
        assert self.chart is not None
        meta = self.chart.metadata
        self.center_text("谱面加载中…", 150, self.f_big, WHITE)
        self.center_text(f"{meta.title} · {meta.artist}", 240, self.f_mid, WHITE)
        self.center_text(f"难度：{meta.difficulty_name} {meta.difficulty_level}", 280, self.f_small, DIM)
        self.center_text(f"初始 BPM：{self.chart.timing.bpm_at(0.0):.2f}", 310, self.f_small, DIM)
        self.center_text(f"玩家：{self._player_label()}", 340, self.f_small, ACCENT)
        self.center_text("第 0 拍将在 1.5 秒后到达判定平面", 400, self.f_small, DIM)

    # -------------------------------------------------- 3D 世界与物件
    def _proj(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        return self.scene.point(x, y, z, self.w, self.h)

    def _quad(self, points, color) -> None:
        pts = [(p[0], p[1]) for p in points]
        if len(pts) >= 3:
            pygame.draw.polygon(self.screen, color, pts)

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

    def _mouse_plane_strip_z(self, strips: int) -> List[float]:
        """鼠标平面渐变用的 z 分界（按屏幕高度均匀切，等价于 ``world_y=0.4``）。"""
        return self._plane_strip_z(strips, MOUSE_PLANE_HEIGHT)

    def _plane_strip_z(self, strips: int, world_y: float = 0.0) -> List[float]:
        """某个水平面上按**屏幕高度**均匀切出来的 z 分界。

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
        """可视毫秒（物件从起点走到判定线所需的**歌曲毫秒**）。

        「流速」应该是**视觉速度**：倍速只改变歌曲推进的快慢，不该让物件在屏幕上
        慢慢爬或飞过去。判定时钟按倍速推进，所以这里的跨度也要乘倍速，
        这样走完全程始终是 `可视毫秒 / 倍速` 的**同一段真实时间**。
        """
        return max(1.0, self.settings.scroll_ms * self.settings.rate)

    def _note_z(self, judge_ms: float, now_ms: float) -> float:
        span = self._visible_span_ms()
        return PlayerScene.note_z(1.0 - (judge_ms - now_ms) / span)

    def _draw_play(self) -> None:
        assert self.chart is not None and self.judge is not None
        now_ms = self._now_ms()
        if self._ensure_scene3d() and self.scene3d is not None:
            # 3D 场景交给 Panda3D 渲染，UI（HUD / 飘字 / 暂停）再画在它上面
            self.scene3d.resize(self.w, self.h)
            frame = self._render_scene3d(now_ms)
            if frame is not None:
                self.screen.blit(frame, (0, 0))
            else:
                self._draw_planes(now_ms)
                self._draw_notes(now_ms)
        else:
            self._draw_planes(now_ms)
            self._draw_notes(now_ms)
        if self.scene3d is None:
            self._draw_mouse_marker()       # Panda3D 路径下指针在场景里
        self._draw_popups()
        self._draw_hud(now_ms)
        if now_ms < -LEAD_MS + 400:
            self.center_text("准备…", self.h * 0.66, self.f_big, (255, 226, 120))
        elif self.ending:
            self.center_text(
                f"谱面结束 · {max(0.0, self.end_timer):.1f}s 后进入结算",
                self.h * 0.66,
                self.f_mid,
                (255, 226, 120),
            )
        if self.paused:
            pygame.draw.rect(self.screen, (0, 0, 0), (0, self.h * 0.32, self.w, 220))
            self.center_text("已暂停", self.h * 0.36, self.f_big, WHITE)
            buttons = (
                ("resume", "空格 继续", 170),
                ("retry", "R 重试", 150),
                ("back", "Esc 回主界面", 210),
            )
            total = sum(width + 20 for _key, _label, width in buttons) - 20
            x = self.w / 2 - total / 2
            for key, label, width in buttons:
                rect = pygame.Rect(x, self.h * 0.43, width, 46)
                self.ui[key] = rect
                hovered = self.hover == key
                pygame.draw.rect(self.screen, (44, 44, 58) if hovered else PANEL, rect, border_radius=6)
                pygame.draw.rect(self.screen, ACCENT, rect, 2, border_radius=6)
                surface = self.f_mid.render(label, True, ACCENT)
                self.screen.blit(
                    surface,
                    (rect.centerx - surface.get_width() / 2, rect.centery - surface.get_height() / 2),
                )
                x += width + 20
            if self.status_hint:
                self.center_text(self.status_hint, self.h * 0.52, self.f_small, DIM)

    def _draw_planes(self, now_ms: float) -> None:
        assert self.chart is not None
        width = LANE_COUNT * LANE_WIDTH
        # 6K 平面：底色 + 左右边线
        far_left = self._proj(K6_LEFT, 0.0, START_Z)
        far_right = self._proj(K6_LEFT + width, 0.0, START_Z)
        near_left = self._proj(K6_LEFT, 0.0, 0.0)
        near_right = self._proj(K6_LEFT + width, 0.0, 0.0)
        # 每条轨道用贴图的颜色按**透视正确的四边形**铺（轨道贴图是平色块，切片反而会出台阶）
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
        # 它们都在**鼠标平面下方**，最后再让鼠标平面的蓝底渐变盖上去
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
                    [
                        self._proj(x0, 0.0, START_Z),
                        self._proj(x1, 0.0, START_Z),
                        self._proj(x1, 0.0, 0.0),
                        self._proj(x0, 0.0, 0.0),
                    ],
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
                    [
                        self._proj(x0, 0.0, START_Z),
                        self._proj(x1, 0.0, START_Z),
                        self._proj(x1, 0.0, 0.0),
                        self._proj(x0, 0.0, 0.0),
                    ],
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
            # 边界是斜的窄条：用贴图的平均色按屏幕高度切片画正梯形（贴图窄条直接拉伸会糊成块）
            boundaries = self._plane_strip_z(10, 0.0)
            for za, zb in zip(boundaries, boundaries[1:]):
                quad = [
                    self._proj(x - 0.008, 0.0, za),
                    self._proj(x + 0.008, 0.0, za),
                    self._proj(x + 0.008, 0.0, zb),
                    self._proj(x - 0.008, 0.0, zb),
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
        # 判定平面：两条判定线（鼠标线在 6K 线上方 0.4 单位）
        k6_left = self._proj(K6_LEFT, 0.0, 0.0)
        k6_right = self._proj(K6_LEFT + width, 0.0, 0.0)
        m_left = self._proj(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0)
        m_right = self._proj(MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0)
        self._line(k6_left, k6_right, WHITE, 3)
        self._line(m_left, m_right, MOUSE_TINT, 3)
        self._line(k6_left, m_left, (60, 62, 74), 1)
        self._line(k6_right, m_right, (60, 62, 74), 1)

    def _draw_mouse_plane_gradient(self) -> None:
        """鼠标平面的蓝底渐变（靠近判定线略蓝 → 轨道尽头完全透明）。"""
        width = LANE_COUNT * LANE_WIDTH
        boundaries = self._mouse_plane_strip_z(48)
        steps = len(boundaries) - 1
        for index in range(steps):
            z_near, z_far = boundaries[index], boundaries[index + 1]
            alpha = int(round(96 * (1.0 - (index + 0.5) / steps)))
            if alpha <= 0:
                continue
            self._alpha_quad(
                [
                    self._proj(K6_LEFT, MOUSE_PLANE_HEIGHT, z_far),
                    self._proj(K6_LEFT + width, MOUSE_PLANE_HEIGHT, z_far),
                    self._proj(K6_LEFT + width, MOUSE_PLANE_HEIGHT, z_near),
                    self._proj(K6_LEFT, MOUSE_PLANE_HEIGHT, z_near),
                ],
                (58, 108, 200),
                alpha,
            )

    @staticmethod
    def _layer_of(note: Note) -> int:
        """与编辑器一致的图层顺序（小 = 先画 = 在下层）。"""
        if isinstance(note, LaneNote):
            if note.is_hold:
                return 0 if note.is_multi else 1
            return 2 if note.is_multi else 3
        if isinstance(note, MouseRegion):
            return 4
        if isinstance(note, MouseClick):
            return 5
        return 6

    def _head_ms(self, note: Note) -> float:
        assert self.chart is not None
        return self.chart.timing.beat_to_song_ms(note.head_beat)

    def _note_visible(self, note: Note, now_ms: float) -> bool:
        """判定时刻已过的物件不再显示（长按 / 空域画到它们的末尾过去为止）。"""
        assert self.chart is not None
        if isinstance(note, LaneNote) and note.is_hold:
            return now_ms <= self.chart.timing.beat_to_song_ms(note.tail_beat) + 1e-6
        if isinstance(note, MouseRegion):
            return now_ms <= self.chart.timing.beat_to_song_ms(note.beat_end) + 1e-6
        return now_ms <= self._head_ms(note) + 1e-6

    def _note_ahead(self, note: Note, now_ms: float) -> bool:
        """物件是否已经进入可视范围。

        比 ``scroll_ms`` 更远的物件必须**不画**：``note_z`` 会把进度夹在 0~1，
        否则它们会全部贴在起点（屏幕最上方）堆成一排。

        阈值必须和 :meth:`_note_z` 用**同一个**跨度（``可视毫秒 × 倍速``）：
        用未缩放的 ``scroll_ms`` 时，低倍速下 750~1500ms 的物件会被画出来并贴在起点，
        高倍速下物件则要等到半程才出现（不是从轨道尽头出现）。
        """
        return self._head_ms(note) - now_ms <= self._visible_span_ms()

    def _draw_notes(self, now_ms: float) -> None:
        assert self.chart is not None
        visible = [
            n
            for n in self.chart.all_notes()
            if self._note_ahead(n, now_ms) and self._note_visible(n, now_ms)
        ]
        visible.sort(key=lambda n: (self._layer_of(n), n.head_beat))
        for note in visible:
            if isinstance(note, LaneNote):
                self._draw_lane_note(note, now_ms)
            elif isinstance(note, MouseRegion):
                self._draw_region(note, now_ms)
            elif isinstance(note, MouseSlide):
                self._draw_pattern(note, now_ms)
            elif isinstance(note, MouseClick):
                self._draw_pattern(note, now_ms)

    def _scene_marker(self) -> None:
        """鼠标指针是**场景内元素**：交给 Panda3D 画在水平面里。

        无界鼠标永远实心；关闭时界内实心、出界则判定位置实心 + 实际位置边线。
        """
        if self.scene3d is None:
            return
        inside = 0.0 <= self.mouse_raw <= 1.0
        outline = None if (self.settings.unbounded_mouse or inside) else self.mouse_raw
        self.scene3d.mouse_marker(
            self.mouse_norm, MOUSE_PLANE_HEIGHT, 0.03, outline_x=outline
        )

    def _render_scene3d(self, now_ms: float):
        """用 Panda3D 渲染一帧 3D 场景（轨道 / 判定线 / 物件），返回 pygame 画面。"""
        assert self.chart is not None and self.scene3d is not None
        scene = self.scene3d
        scene.begin()
        timing = self.chart.timing
        # 轨道状态（按下高亮 / 侧轨高亮）与小节线：和软件渲染器同一套规则
        scene.set_lane_state(
            self.pressed_lanes,
            self.settings.side_lane_highlight,
            (*[channel / 255.0 for channel in SIDE_TINT], 1.0),
        )
        beat = timing.song_ms_to_beat(max(0.0, now_ms))
        span_beats = max(
            0.5, self._visible_span_ms() / 1000.0 * timing.bpm_at(beat) / 60.0
        )
        scene.bar_lines([
            self._note_z(timing.beat_to_song_ms(line), now_ms)
            for line in timing.bar_lines(beat, beat + span_beats)
        ])
        visible = [
            note for note in self.chart.all_notes()
            if self._note_ahead(note, now_ms) and self._note_visible(note, now_ms)
        ]
        # 图层顺序与编辑器一致（多轨长按 < 单轨长按 < 多轨单键 < 单键 < 空域 < 滑键 < 点击）
        visible.sort(key=lambda note: (self._layer_of(note), note.head_beat))
        for note in visible:
            layer = f"note{self._layer_of(note)}"
            if isinstance(note, LaneNote):
                head = self._note_z(self._head_ms(note), now_ms)
                if head < -0.4:
                    continue
                low, high = min(note.lane_start, note.lane_end), max(note.lane_start, note.lane_end)
                x0 = K6_LEFT + low * LANE_WIDTH + LANE_WIDTH * 0.18
                x1 = K6_LEFT + (high + 1) * LANE_WIDTH - LANE_WIDTH * 0.18
                lane = min(LANE_COUNT, max(1, low + 1))
                center_x = (x0 + x1) / 2.0
                if note.is_hold:
                    tail = self._note_z(timing.beat_to_song_ms(note.tail_beat), now_ms)
                    body_center = (max(0.0, head) + max(0.0, tail)) / 2.0
                    scene.quad(f"hold_body_{lane}", center_x, body_center, x1 - x0,
                               max(0.2, abs(tail - head)), 0.004, (1, 1, 1, 0.75),
                               layer=layer)
                    head_size = max(0.25, self.settings.note_thickness)
                    scene.quad(f"hold_head_{lane}", center_x, max(0.0, head) + head_size / 2.0,
                               x1 - x0, head_size, 0.006, layer=layer)
                else:
                    thickness = (self.settings.multi_thickness if note.is_multi
                                 else self.settings.note_thickness)
                    scene.quad(f"tap_{lane}", center_x, max(0.0, head) + thickness / 2.0,
                               x1 - x0, thickness, 0.006, layer=layer)
            elif isinstance(note, MouseRegion):
                self._draw_region3d(note, now_ms)
            elif isinstance(note, MouseSlide):
                z = max(0.0, self._note_z(self._head_ms(note), now_ms))
                direction = 1 if note.direction >= 0 else -1
                name = "right" if direction > 0 else "left"
                x_start = MOUSE_LEFT + note.x_start * MOUSE_LEGAL_WIDTH
                x_end = MOUSE_LEFT + note.x_end * MOUSE_LEGAL_WIDTH
                thick = self.settings.slide_thickness
                center_y = MOUSE_PLANE_HEIGHT + thick / 2.0
                scene.quad(f"slide_body_{name}", (x_start + x_end) / 2.0, z,
                           abs(x_end - x_start), thick, center_y, (1, 1, 1, 1),
                           vertical=True, layer=layer)
                scene.quad(f"slide_arrow_{name}", x_end, z, thick, thick, center_y,
                           (1, 1, 1, 1), vertical=True, layer=layer)
            elif isinstance(note, MouseClick):
                z = max(0.0, self._note_z(self._head_ms(note), now_ms))
                lo, hi = note.x_range()
                x0 = MOUSE_LEFT + min(lo, hi) * MOUSE_LEGAL_WIDTH
                x1 = MOUSE_LEFT + max(lo, hi) * MOUSE_LEGAL_WIDTH
                height = self.settings.click_thickness
                center_y = MOUSE_PLANE_HEIGHT + height / 2.0
                scene.quad("click_range", (x0 + x1) / 2.0, z, x1 - x0, height, center_y,
                           (1, 1, 1, 1), vertical=True, layer=layer)
                cx = MOUSE_LEFT + note.center * MOUSE_LEGAL_WIDTH
                scene.quad("click_diamond", cx, z, height, height, center_y,
                           (1, 1, 1, 1), vertical=True, angle=45.0, layer=layer)
                scene.quad("click_edge", x0, z, height / 2.0, height, center_y,
                           (1, 1, 1, 1), vertical=True, layer=layer)
                scene.quad("click_edge", x1, z, height / 2.0, height, center_y,
                           (1, 1, 1, 1), vertical=True, angle=180.0, layer=layer)
        self._scene_marker()
        return scene.render()

    def _draw_region3d(self, note: MouseRegion, now_ms: float) -> None:
        """Panda 路径的空域：按左右边界曲线采样，铺成**透视正确的梯形/弯边界**。

        贴图与软件渲染器同一套（``region_fill`` 底色 + ``region_area`` 横纹渐变 +
        ``region_edge`` 左右边线）；这里是 3D 网格，透视天然正确，也不会出现
        「两端都被夹到远端、只剩一条小色块」的老问题。
        """
        assert self.chart is not None and self.scene3d is not None
        scene = self.scene3d
        timing = self.chart.timing
        span = note.beat_end - note.beat_start
        samples = 36
        left: List[Tuple[float, float]] = []
        right: List[Tuple[float, float]] = []
        for index in range(samples + 1):
            beat = note.beat_start + span * index / samples
            z = min(START_Z, max(0.0, self._note_z(timing.beat_to_song_ms(beat), now_ms)))
            left.append((MOUSE_LEFT + note.left_at(beat) * MOUSE_LEGAL_WIDTH, z))
            right.append((MOUSE_LEFT + note.right_at(beat) * MOUSE_LEGAL_WIDTH, z))
        if len(left) < 2:
            return
        y = MOUSE_PLANE_HEIGHT + 0.002
        # 底图用 region_area 自带的「底边略不透明 + 越往上越透明 + 横向条纹」，
        # alpha 再压一点，和软件渲染器的观感（约 0.4）保持一致
        scene.region_shape(left, right, y, "region_area", (1.0, 1.0, 1.0, 0.9),
                           layer="region_top")
        edge = self.texture_avg.get("region_edge") or (90, 220, 255, 255)
        for points in (left, right):
            scene.ribbon(points, y + 0.002, 0.006,
                         (edge[0] / 255.0, edge[1] / 255.0, edge[2] / 255.0, 0.85),
                         layer="region_top")

    def _draw_lane_note(self, note: LaneNote, now_ms: float) -> None:
        """6K 单键 / 长按：用预设贴图绘制（贴图**底端**在判定边，到达判定线即判定）。"""
        assert self.chart is not None
        head_z = self._note_z(self._head_ms(note), now_ms)
        if head_z < -0.4:
            return
        low, high = min(note.lane_start, note.lane_end), max(note.lane_start, note.lane_end)
        tex_index = min(LANE_COUNT, max(1, low + 1))
        x0 = K6_LEFT + low * LANE_WIDTH + LANE_WIDTH * 0.18
        x1 = K6_LEFT + (high + 1) * LANE_WIDTH - LANE_WIDTH * 0.18
        if note.is_hold:
            tail_z = self._note_z(self.chart.timing.beat_to_song_ms(note.tail_beat), now_ms)
            body = f"hold_body_{tex_index}"
            body_color = self.texture_avg.get(body)
            if body_color is not None:
                # 长条用透视正确的多边形填（pygame 无法做透视 warp，逐片贴图会出台阶）
                quad = [
                    self._proj(x0, 0.0, max(0.0, tail_z)),
                    self._proj(x1, 0.0, max(0.0, tail_z)),
                    self._proj(x1, 0.0, max(0.0, head_z)),
                    self._proj(x0, 0.0, max(0.0, head_z)),
                ]
                if body_color[3] >= 250:
                    self._quad(quad, body_color[:3])
                else:
                    self._alpha_quad(quad, body_color[:3], body_color[3])
                if not self._blit_hold_center_line(x0, x1, head_z, tail_z):
                    pass
            elif not self._blit_strip(body, x0, x1, max(0.0, head_z), max(0.0, tail_z)):
                near, near2 = self._proj(x0, 0.0, max(0.0, head_z)), self._proj(x1, 0.0, max(0.0, head_z))
                far, far2 = self._proj(x0, 0.0, max(0.0, tail_z)), self._proj(x1, 0.0, max(0.0, tail_z))
                self._quad([near, near2, far2, far], (60, 120, 220))
                mid_near = self._proj((x0 + x1) / 2, 0.0, max(0.0, head_z))
                mid_far = self._proj((x0 + x1) / 2, 0.0, max(0.0, tail_z))
                self._line_scaled(mid_near, mid_far, WHITE, 2.4, min(near[2], far[2]))
            # 开头盖在主体上面（有意让一部分主体被开头覆盖）
            head_top = max(0.0, head_z) + max(0.4, self.settings.note_thickness)
            head_quad = [
                self._proj(x0, 0.0, min(START_Z, head_top)),
                self._proj(x1, 0.0, min(START_Z, head_top)),
                self._proj(x1, 0.0, max(0.0, head_z)),
                self._proj(x0, 0.0, max(0.0, head_z)),
            ]
            if not self._blit_tex_quad(f"hold_head_{tex_index}", head_quad):
                self._quad(
                    [
                        self._proj(x0, 0.0, max(0.0, head_z)),
                        self._proj(x1, 0.0, max(0.0, head_z)),
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
            far_z = min(START_Z, max(0.0, head_z) + thickness)
            tap_quad = [
                self._proj(x0, 0.0, far_z),
                self._proj(x1, 0.0, far_z),
                self._proj(x1, 0.0, max(0.0, head_z)),
                self._proj(x0, 0.0, max(0.0, head_z)),
            ]
            if self._blit_tex_quad(f"tap_{tex_index}", tap_quad):
                return
            near, near2 = self._proj(x0, 0.0, max(0.0, head_z)), self._proj(x1, 0.0, max(0.0, head_z))
            far, far2 = self._proj(x0, 0.0, far_z), self._proj(x1, 0.0, far_z)
            self._quad([near, near2, far2, far], (70, 140, 240))
            # 白色实线就在“判定时刻与判定线重合”的那条边上，粗细随透视变细
            self._line_scaled(near, near2, WHITE, 2.4, near[2])

    def _blit_strip(self, name: str, x0: float, x1: float, z0: float, z1: float, slices: int = 28) -> bool:
        """铺一条贴图（6K 平面内）：**按屏幕高度切片**，保证每片都是窄条、透视正确。"""
        if self._tex(name) is None or z1 <= z0:
            return False
        boundaries = [z for z in self._plane_strip_z(max(4, slices), 0.0) if z0 - 1e-9 <= z <= z1 + 1e-9]
        if len(boundaries) < 2:
            boundaries = [z0, z1]
        for za, zb in zip(boundaries, boundaries[1:]):
            if zb - za < 1e-6:
                continue
            near_left = self._proj(x0, 0.0, za)
            near_right = self._proj(x1, 0.0, za)
            far_left = self._proj(x0, 0.0, zb)
            far_right = self._proj(x1, 0.0, zb)
            # 贴图的“上”对应远处：左上 / 右上 / 右下 / 左下
            self._blit_tex_quad(name, [far_left, far_right, near_right, near_left])
        return True

    def _blit_hold_center_line(self, x0: float, x1: float, head_z: float, tail_z: float) -> bool:
        """长按主体中间那条纯白竖线（宽度取自贴图：约 1/6）。"""
        center = (x0 + x1) / 2.0
        half = (x1 - x0) / 12.0
        near = self._proj(center, 0.0, max(0.0, head_z))
        far = self._proj(center, 0.0, max(0.0, tail_z))
        self._line_scaled(near, far, WHITE, max(2.0, abs(near[0] - self._proj(center - half, 0.0, max(0.0, head_z))[0]) * 2.0), near[2])
        return True

    def _draw_region(self, note: MouseRegion, now_ms: float) -> None:
        """空域：画在鼠标物件运动的水平面内（半透明蓝底 + 左右两条边线）。"""
        assert self.chart is not None
        timing = self.chart.timing
        if self._note_z(timing.beat_to_song_ms(note.beat_start), now_ms) > START_Z:
            return
        samples = 28
        left_points: List[Tuple[float, float, float]] = []
        right_points: List[Tuple[float, float, float]] = []
        for index in range(samples + 1):
            beat = note.beat_start + (note.beat_end - note.beat_start) * index / samples
            z = self._note_z(timing.beat_to_song_ms(beat), now_ms)
            if z > START_Z or z < -0.4:
                continue
            z = max(0.0, z)
            left_x = MOUSE_LEFT + note.left_at(beat) * MOUSE_LEGAL_WIDTH
            right_x = MOUSE_LEFT + note.right_at(beat) * MOUSE_LEGAL_WIDTH
            left_points.append(self._proj(left_x, MOUSE_PLANE_HEIGHT, z))
            right_points.append(self._proj(right_x, MOUSE_PLANE_HEIGHT, z))
        if len(left_points) < 2:
            return
        # 底图：一整张带横向条纹的淡蓝图（底边贴着判定线，越往上越透明）
        area_color = self.texture_avg.get("region_area")
        if area_color is None:
            surface = self._tex("region_area")
            if surface is not None:
                area_color = textures.average_color(surface)
        if area_color is not None:
            # 半透明底：长条用透视正确的多边形填（逐片贴图会出台阶）
            self._alpha_quad(
                left_points + list(reversed(right_points)),
                area_color[:3],
                min(255, area_color[3] + 40),
            )
            for index in range(0, len(left_points) - 1, 2):     # 横向细条纹（近似贴图里的横纹）
                a_l, a_r = left_points[index], right_points[index]
                b_l, b_r = left_points[index + 1], right_points[index + 1]
                mid_l = ((a_l[0] + b_l[0]) / 2.0, (a_l[1] + b_l[1]) / 2.0)
                mid_r = ((a_r[0] + b_r[0]) / 2.0, (a_r[1] + b_r[1]) / 2.0)
                self._alpha_quad(
                    [
                        (mid_l[0], mid_l[1] - 1.0),
                        (mid_r[0], mid_r[1] - 1.0),
                        (mid_r[0], mid_r[1] + 1.0),
                        (mid_l[0], mid_l[1] + 1.0),
                    ],
                    area_color[:3],
                    min(255, area_color[3] + 70),
                )
        else:
            # 半透明：空域在鼠标平面上，挡住下面的 6K 物件就没法看谱了
            self._alpha_quad(left_points + list(reversed(right_points)), (60, 110, 200), 104)
        # 左右边界：单像素边界图（多像素时由加载阶段取平均）
        edge_color = self.texture_avg.get("region_edge")
        if edge_color is None:
            surface = self._tex("region_edge")
            if surface is not None:
                edge_color = textures.average_color(surface)
        for index in range(len(left_points) - 1):
            for points in (left_points, right_points):
                if edge_color is not None:
                    a, b = points[index], points[index + 1]
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    length = max(1.0, math.hypot(dx, dy))
                    nx, ny = -dy / length, dx / length      # 法线方向
                    quad = [
                        (a[0] + nx, a[1] + ny),
                        (b[0] + nx, b[1] + ny),
                        (b[0] - nx, b[1] - ny),
                        (a[0] - nx, a[1] - ny),
                    ]
                    self._alpha_quad(quad, edge_color[:3], edge_color[3])
                else:
                    self._line(points[index], points[index + 1], MOUSE_TINT, 2)
        # 空域判定线：单像素图铺在判定线处
        line_color = self.texture_avg.get("region_line")
        if line_color is None:
            surface = self._tex("region_line")
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
                MOUSE_TINT,
                3,
            )

    def _draw_pattern(self, note: Note, now_ms: float) -> None:
        """滑键 / 鼠标点击按键：图案平行于判定平面，改用预设贴图。"""
        z = self._note_z(self._head_ms(note), now_ms)
        if z < -0.4 or z > START_Z:
            return
        z = max(0.0, z)
        if self.textures or self._tex("click_diamond") is not None:
            if self._draw_pattern_textured(note, z):
                return
        self._draw_pattern_legacy(note, z)

    def _draw_pattern_textured(self, note: Note, z: float) -> bool:
        """贴图版滑键 / 点击按键（缺贴图时返回 False，走旧的程序绘制）。"""
        if isinstance(note, MouseSlide):
            direction = 1 if note.direction >= 0 else -1
            name = "right" if direction > 0 else "left"
            height = max(0.006, self.settings.slide_thickness)
            start_x = MOUSE_LEFT + note.x_start * MOUSE_LEGAL_WIDTH
            end_x = MOUSE_LEFT + note.x_end * MOUSE_LEGAL_WIDTH
            body_quad = [
                self._proj(start_x, MOUSE_PLANE_HEIGHT + height, z),
                self._proj(end_x, MOUSE_PLANE_HEIGHT + height, z),
                self._proj(end_x, MOUSE_PLANE_HEIGHT, z),
                self._proj(start_x, MOUSE_PLANE_HEIGHT, z),
            ]
            if not self._blit_tex_quad(f"slide_body_{name}", body_quad):
                return False
            # 箭头：宽度不拉伸（用贴图原宽），高度按用户设置拉伸；贴在朝向侧顶点
            arrow = self._tex(f"slide_arrow_{name}")
            if arrow is not None and arrow.get_width() > 0:
                inner = end_x - height * direction       # 箭头朝内延伸，宽度 = 厚度（不拉伸）
                arrow_quad = [
                    self._proj(max(inner, end_x), MOUSE_PLANE_HEIGHT + height, z),
                    self._proj(min(inner, end_x), MOUSE_PLANE_HEIGHT + height, z),
                    self._proj(min(inner, end_x), MOUSE_PLANE_HEIGHT, z),
                    self._proj(max(inner, end_x), MOUSE_PLANE_HEIGHT, z),
                ]
                self._blit_tex_quad(f"slide_arrow_{name}", arrow_quad, flip_x=direction < 0)
            return True
        assert isinstance(note, MouseClick)
        height = max(0.006, self.settings.click_thickness)
        x0 = MOUSE_LEFT + min(note.x_range()) * MOUSE_LEGAL_WIDTH
        x1 = MOUSE_LEFT + max(note.x_range()) * MOUSE_LEGAL_WIDTH
        middle = MOUSE_PLANE_HEIGHT + height / 2.0
        range_quad = [
            self._proj(x0, MOUSE_PLANE_HEIGHT + height, z),
            self._proj(x1, MOUSE_PLANE_HEIGHT + height, z),
            self._proj(x1, MOUSE_PLANE_HEIGHT, z),
            self._proj(x0, MOUSE_PLANE_HEIGHT, z),
        ]
        if not self._blit_tex_quad("click_range", range_quad):
            return False
        d = height / 2.0                              # 菱形 / 边界的半高
        cx = MOUSE_LEFT + note.center * MOUSE_LEGAL_WIDTH
        self._blit_tex_quad(
            "click_diamond",
            [
                self._proj(cx - d, middle + d, z),
                self._proj(cx + d, middle + d, z),
                self._proj(cx + d, middle - d, z),
                self._proj(cx - d, middle - d, z),
            ],
        )
        # 边界标识 = 菱形竖切的右半：左边界朝内（向右），右边界镜像
        self._blit_tex_quad(
            "click_edge",
            [
                self._proj(x0, middle + d, z),
                self._proj(x0 + d, middle + d, z),
                self._proj(x0 + d, middle - d, z),
                self._proj(x0, middle - d, z),
            ],
        )
        self._blit_tex_quad(
            "click_edge",
            [
                self._proj(x1 - d, middle + d, z),
                self._proj(x1, middle + d, z),
                self._proj(x1, middle - d, z),
                self._proj(x1 - d, middle - d, z),
            ],
            flip_x=True,
        )
        return True

    def _draw_pattern_legacy(self, note: Note, z: float) -> None:
        """程序绘制的旧版样式（缺贴图时的兜底）。"""
        if isinstance(note, MouseSlide):
            color = (110, 230, 160) if note.direction >= 0 else (235, 210, 110)
            height = self.settings.slide_thickness
            # 滑动朝向的一侧更厚、不透明度更高：起点 -> 终点 **厚度与透明度都渐变**
            start_x = MOUSE_LEFT + note.x_start * MOUSE_LEGAL_WIDTH
            end_x = MOUSE_LEFT + note.x_end * MOUSE_LEGAL_WIDTH
            thin = max(0.004, height * 0.25)
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
        assert isinstance(note, MouseClick)
        lo, hi = note.x_range()
        color = (235, 90, 80)
        height = self.settings.click_thickness
        x0 = MOUSE_LEFT + min(lo, hi) * MOUSE_LEGAL_WIDTH
        x1 = MOUSE_LEFT + max(lo, hi) * MOUSE_LEGAL_WIDTH
        middle = MOUSE_PLANE_HEIGHT + height / 2.0
        # 形状是**中间厚、两边薄的菱形**，并且透明度一起渐变（中间实、两端淡）
        span_px = abs(
            self._proj(x1, MOUSE_PLANE_HEIGHT, z)[0]
            - self._proj(x0, MOUSE_PLANE_HEIGHT, z)[0]
        )
        steps = max(12, min(64, int(span_px / 3.0) + 1))
        alpha_edge, alpha_mid = 40, 226
        for index in range(steps):
            t0 = index / steps
            t1 = (index + 1) / steps
            xa = x0 + (x1 - x0) * t0
            xb = x0 + (x1 - x0) * t1
            la = 1.0 - abs(2.0 * t0 - 1.0)        # 中点 1、两端 0 -> 菱形轮廓
            lb = 1.0 - abs(2.0 * t1 - 1.0)
            half_a = max(0.0004, height / 2.0 * la)
            half_b = max(0.0004, height / 2.0 * lb)
            alpha = int(round(alpha_edge + (alpha_mid - alpha_edge) * (la + lb) / 2.0))
            self._alpha_quad(
                [
                    self._proj(xa, middle - half_a, z),
                    self._proj(xb, middle - half_b, z),
                    self._proj(xb, middle + half_b, z),
                    self._proj(xa, middle + half_a, z),
                ],
                color,
                alpha,
            )
        # 中间横线：与键盘点击物件的横线一致（红色）
        self._line(
            self._proj(x0, middle, z), self._proj(x1, middle, z), (255, 70, 60), 2
        )
        # 中央实心菱形标记（固定大小，不随按键尺寸变化）
        cx = MOUSE_LEFT + note.center * MOUSE_LEGAL_WIDTH
        d = NOTE_HEIGHT_UNIT / 2
        self._quad(
            [
                self._proj(cx - d, middle, z),
                self._proj(cx, middle + d, z),
                self._proj(cx + d, middle, z),
                self._proj(cx, middle - d, z),
            ],
            (255, 60, 50),
        )

    def _mouse_marker_points(self, norm_x: float):
        """指针三角形的三个点（躺在鼠标平面内、矮胖）。"""
        x = MOUSE_LEFT + norm_x * MOUSE_LEGAL_WIDTH
        length = 0.16                      # 沿 z 的长度（矮）
        half_w = 0.13                      # 横向半宽（胖，是长度的 1.6 倍）
        return (
            self._proj(x, MOUSE_PLANE_HEIGHT, length),
            self._proj(x - half_w, MOUSE_PLANE_HEIGHT, 0.0),
            self._proj(x + half_w, MOUSE_PLANE_HEIGHT, 0.0),
        )

    def _draw_marker_shape(self, points, filled: bool) -> None:
        """画一个指针三角形：``filled`` 时是**半透明实心**，否则只画边线。"""
        if filled:
            self._alpha_quad(list(points), MOUSE_TINT, 130)
        pygame.draw.polygon(
            self.screen, MOUSE_TINT, [(p[0], p[1]) for p in points], 2
        )

    def _draw_mouse_marker(self) -> None:
        """鼠标指针画法。

        * 开启**无界鼠标**：永远按实际位置画半透明实心；
        * 关闭且鼠标在合法区内：判定位置 = 实际位置，画半透明实心；
        * 关闭且鼠标出界：**判定位置**（夹在边界上）画实心，
          **实际位置**只画边线，两者都能看到。
        """
        inside = 0.0 <= self.mouse_raw <= 1.0
        # 判定位置永远是半透明实心
        self._draw_marker_shape(self._mouse_marker_points(self.mouse_norm), filled=True)
        if not self.settings.unbounded_mouse and not inside:
            # 出界时把**实际位置**也用边线标出来
            self._draw_marker_shape(self._mouse_marker_points(self.mouse_raw), filled=False)

    def _draw_popups(self) -> None:
        """判定飘字：在**被判定的物件处**向上飘并淡出。"""
        if not self.popups:
            return
        for grade, text, left, x, y in self.popups[-8:]:
            surface = self.f_mid.render(text, True, GRADE_COLORS.get(grade, WHITE))
            alpha = max(0.0, min(1.0, left / 0.7))
            surface.set_alpha(int(255 * alpha))
            rise = (0.7 - left) * 48.0
            self.screen.blit(
                surface,
                (x - surface.get_width() / 2, y - 38 - rise),
            )

    def _draw_hud(self, now_ms: float) -> None:
        assert self.chart is not None and self.judge is not None
        meta = self.chart.metadata
        beat = self.chart.timing.song_ms_to_beat(max(0.0, now_ms))
        left_lines = (
            f"{meta.title} · {meta.artist}",
            f"难度 {meta.difficulty_name} {meta.difficulty_level} · BPM {self.chart.timing.bpm_at(beat):.2f}",
            f"玩家 {self._player_label()}",
        )
        for index, line in enumerate(left_lines):
            self.text(line, (24, 16 + index * 26), self.f_small, WHITE if index == 0 else DIM)
        right_lines = self._hud_right_lines()
        for index, line in enumerate(right_lines):
            surface = self.f_small.render(line, True, WHITE if index == 0 else DIM)
            self.screen.blit(surface, (self.w - surface.get_width() - 24, 16 + index * 26))
        # 连击数醒目显示在判定面上方
        if self.judge.combo >= 5:
            base_y = self._judge_line_screen_y(MOUSE_PLANE_HEIGHT)
            self.center_text(f"{self.judge.combo}", base_y - 170, self.f_big, (255, 226, 120))

    def _hud_right_lines(self) -> Tuple[str, ...]:
        """HUD 右上角四行：准度/倍速、分数、连击、各判定数量。

        游戏内的分数**还是总分**（附加分不单独拆出来），拆分只在结算页展示。
        """
        assert self.judge is not None
        counts = self.judge.counts
        return (
            f"准度 {self.judge.accuracy * 100:.3f}% · 倍速 {_fmt_num(self.settings.rate)}x",
            f"分数 {self.judge.score}（×{self.judge.score_multiplier:.2f}）",
            f"连击 {self.judge.combo}（最大 {self.judge.max_combo}）",
            "  ".join(f"{grade} {counts[grade]}" for grade in GRADES),
        )

    def _draw_result(self) -> None:
        assert self.chart is not None and self.judge is not None
        meta = self.chart.metadata
        # 左 1/3（元数据 / 玩家）、右 2/3（分数与判定）
        split = self._result_split()
        left_w = split
        pygame.draw.rect(self.screen, PANEL, (16, 16, left_w - 24, self.h - 32))
        pygame.draw.rect(
            self.screen, PANEL, (split + 8, 16, self.w - split - 24, self.h - 32)
        )
        self.text("结算", (40, 40), self.f_big, WHITE)
        left_lines = (
            meta.title,
            meta.artist,
            f"{meta.difficulty_name} {meta.difficulty_level}",
            f"玩家：{self._player_label()}",
        )
        for index, line in enumerate(left_lines):
            self.text(line, (40, 140 + index * 34), self.f_mid, WHITE if index == 0 else DIM)
        counts = self.judge.counts
        total = max(1, self.judge.total_notes)
        # 顺序：分数 -> 准度 -> 最大连击 / 总判定数 -> 倍速
        y = 140
        for index, (line, is_score) in enumerate(self._result_lines()):
            self.text(
                line,
                (split + 40, y),
                self.f_big if is_score else self.f_mid,
                (255, 226, 120) if is_score else (WHITE if index == 2 else ACCENT),
            )
            y += 46 if is_score else 30       # 分数那行是大字，多留一点
        # 判定明细横着排（右栏更宽，够放下 4 列）
        column_w = max(120, (self.w - split - 80) // 4)
        grades_y = y + 14
        for index, grade in enumerate(GRADES):
            share = counts[grade] / total * 100.0
            self.text(
                f"{grade}：{counts[grade]}（{share:.1f}%）",
                (split + 40 + index % 2 * (column_w * 2), grades_y + index // 2 * 40),
                self.f_mid,
                GRADE_COLORS[grade],
            )
        retry = pygame.Rect(46, self.h - 92, 180, 48)
        back = pygame.Rect(self.w - 300, self.h - 92, 240, 48)
        self.ui["retry"] = retry
        self.ui["back"] = back
        for rect, label, hovered in (
            (retry, "R 重试", self.hover == "retry"),
            (back, "Esc 回到主界面", self.hover == "back"),
        ):
            pygame.draw.rect(self.screen, (44, 44, 58) if hovered else PANEL, rect, border_radius=6)
            pygame.draw.rect(self.screen, ACCENT, rect, 2, border_radius=6)
            surface = self.f_mid.render(label, True, ACCENT)
            self.screen.blit(
                surface,
                (rect.centerx - surface.get_width() / 2, rect.centery - surface.get_height() / 2),
            )

    def _result_split(self) -> int:
        """结算页左右分栏位置：左栏 1/3、右栏 2/3。"""
        return int(self.w / 3)

    def _result_score_line(self) -> str:
        """结算页分数行：「准度部分 × 倍率」 ＋/－ 附加分（倍率为负时是额外扣分）。"""
        assert self.judge is not None
        adjustment = self.judge.exact_adjustment
        sign = "+" if adjustment >= 0 else "-"      # 负倍率：额外扣分
        return f"分数：{self.judge.scaled_score} {sign} {abs(adjustment)}"

    def _result_combo_line(self) -> str:
        """最大连击按“最大连击 / 总判定数”显示。"""
        assert self.judge is not None
        return f"最大连击：{self.judge.max_combo} / {self.judge.total_notes}"

    def _result_rate_line(self) -> str:
        """倍速（自动播放时注明分数倍率 = -倍速^1.5）—— 放在最后一行。"""
        if self.settings.autoplay:
            assert self.judge is not None
            return (
                f"倍速：{_fmt_num(self.settings.rate)}x"
                f"（Autoplay · 分数倍率 ×{self.judge.score_multiplier:.2f}）"
            )
        return f"倍速：{_fmt_num(self.settings.rate)}x"

    def _result_accuracy_line(self) -> str:
        """准度（取代原来的“判定数 a / 总数”）。"""
        assert self.judge is not None
        return f"准度：{self.judge.accuracy * 100:.3f}%"

    def _result_lines(self) -> List[Tuple[str, bool]]:
        """结算页右栏的行（顺序：分数 → 准度 → 最大连击/总判定数 → 倍速）。"""
        return [
            (self._result_score_line(), True),
            (self._result_accuracy_line(), False),
            (self._result_combo_line(), False),
            (self._result_rate_line(), False),
        ]

    def _event_result(self, event) -> None:
        if event.type == pygame.MOUSEMOTION:
            self.hover = self._hit_ui(event.pos) or ""
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            key = self._hit_ui(event.pos)
            if key == "retry":
                self._start_play()
            elif key == "back":
                self.state = "menu"
            return
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_r:
            self._start_play()
        elif event.key == pygame.K_ESCAPE:
            self.state = "menu"


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - GUI 入口
    args = list(sys.argv[1:] if argv is None else argv)
    forced: Optional[str] = None
    for arg in args:
        if arg == "--software":
            forced = "software"
        elif arg == "--opengl":
            forced = "opengl"
    paths: List[str] = []
    for arg in args:
        if arg.startswith("--"):
            continue
        if os.path.isdir(arg):                      # 直接给谱面文件夹也可以
            names = sorted(
                name
                for name in os.listdir(arg)
                if name.lower().endswith(".json") and not name.startswith("_")
            )
            if names:
                paths.append(main_chart_in(arg, names))
        elif os.path.isfile(arg):
            paths.append(arg)
    return PlayerApp(paths, renderer=forced).run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

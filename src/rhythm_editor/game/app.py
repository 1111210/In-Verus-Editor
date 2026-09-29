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
        self.message = ""
        self.status_hint = ""
        self.settings_row = 0
        # 引导时间（首拍 1.5 秒）：offset 不够时先用挂钟静音等待，保证音乐和物件同步
        self.audio_started = True
        self.play_wall = 0.0
        self.pause_wall = 0.0
        self.audio_start_ms = 0.0
        self.lead_wait_ms = 0.0
        # 鼠标 UI 的命中区域（每帧绘制时写入）与半透明叠加层
        self.ui: dict = {}
        self.hover = ""
        self._overlay: Optional[pygame.Surface] = None
        self.gl_cost = 0.0                 # OpenGL 呈现耗时（指数平滑，太慢就回退）
        self.gl_frames = 0
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

    def _make_display(self, width: int, height: int, verify: bool = True):
        """按设置创建显示：opengl 成功**且自检通过**才用 GL，否则回退软件渲染。"""
        width, height = self._fit_to_desktop(width, height)
        if self.settings.renderer == "opengl" and glview.available():
            try:
                pygame.display.set_mode(
                    (width, height), pygame.OPENGL | pygame.DOUBLEBUF | pygame.RESIZABLE
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
        return pygame.display.set_mode((width, height), pygame.RESIZABLE)

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
            elif event.type == pygame.VIDEORESIZE:
                self.screen = self._make_display(max(800, event.w), max(500, event.h))
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
        """F2 / 设置里切换软件 ↔ OpenGL（存进 player.json，下次仍生效）。"""
        target = "opengl" if self.settings.renderer == "software" else "software"
        self.settings.renderer = target
        psettings.save_settings(self.settings)
        self.gl_presenter = None
        self.gl_frames = 0
        self.gl_cost = 0.0
        self.screen = self._make_display(self.w, self.h)
        self.message = (
            f"渲染后端：{'OpenGL' if self.renderer_name == 'opengl' else '软件渲染'}"
            "（F2 切换）"
        )
        print(f"[In Verus 游玩器] 渲染后端：{self.renderer_name} · 窗口 {self.w}x{self.h}", flush=True)

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
        self._begin_chart(self.paths[self.selected])

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
        "玩家名称（回车输入）",
        "6K 键位（回车逐轨输入）",
        "物件厚度",
        "系统音频延迟(ms)",
        "1x 可视毫秒",
        "鼠标灵敏度",
        "侧轨高亮",
        "无界鼠标模式",
        "音乐音量",
        "打击音效音量",
        "渲染后端（软件 / OpenGL）",
        "倍速（0.1~1.0x）",
    )

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
        """鼠标点设置行：选中；再点一次那一行 = 编辑 / 切换；◀ ▶ 直接调整。"""
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
        elif kind == "set":
            if index == self.settings_row:
                self._edit_setting_text()
            else:
                self.settings_row = index
        psettings.save_settings(self.settings)

    def _edit_setting_text(self) -> None:
        s = self.settings
        if self.settings_row == 0:
            entered = self._prompt("玩家名称", s.player_name)
            if entered is not None:
                s.player_name = entered
        elif self.settings_row == 1:
            for index, name in enumerate(s.keys):
                picked = self._prompt_key(f"第 {index + 1} 轨（现在是 {name}）")
                if not picked or picked == name:
                    continue
                # 别的轨道占着这个键就交换，避免两条轨道共用一个键（会互相抢判）
                if picked in s.keys:
                    other = s.keys.index(picked)
                    s.keys[other] = name
                s.keys[index] = picked
        else:
            self._adjust_setting(1)
        s.normalize()

    def _adjust_setting(self, step: int) -> None:
        s = self.settings
        row = self.settings_row
        if row == 2:
            s.note_thickness += 0.05 * step
        elif row == 3:
            s.audio_latency_ms += 5.0 * step
        elif row == 4:
            s.scroll_ms += 100.0 * step
        elif row == 5:
            s.mouse_sensitivity = round(s.mouse_sensitivity + 0.1 * step, 2)
        elif row == 6:
            s.side_lane_highlight = not s.side_lane_highlight
        elif row == 7:
            s.unbounded_mouse = not s.unbounded_mouse
        elif row == 8:
            s.music_volume = round(s.music_volume + 0.05 * step, 2)
        elif row == 9:
            s.hit_volume = round(s.hit_volume + 0.05 * step, 2)
        elif row == 10:
            self.settings_row = row
            self._switch_renderer()
            return
        elif row == 11:
            s.rate = round(min(1.0, max(0.1, s.rate + 0.1 * step)), 1)
            if self.judge is not None:
                self.judge.rate = s.rate
            self.audio.set_rate(s.rate)
        s.normalize()
        if row in (8, 9) and self.state == "play":
            self.audio.set_volume(s.music_volume)
            self.hits.set_volume(s.hit_volume)

    def _prompt(self, label: str, initial: str) -> Optional[str]:
        """窗口内文本输入（回车确认 / Esc 取消）。

        用 ``TEXTINPUT`` 事件收字（**中文 IME 也能输入**），并且走和主循环一样的
        ``_present()`` 呈现路径，所以 OpenGL 后端下也能看到自己打的字。
        """
        entered = initial or ""
        caret = len(entered)
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
                        if caret > 0:
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
        self.judge = Judge(self.chart, rate=self.settings.rate)
        self.pressed_lanes.clear()
        self.paused = False
        self.message = ""
        self.audio.latency_ms = self.settings.audio_latency_ms
        self.audio.set_volume(self.settings.music_volume)
        self.hits.set_volume(self.settings.hit_volume)
        self.hits.set_enabled(True)
        self.popups.clear()
        # 首拍引导 1.5 秒，同时保证**音乐与物件同步**：
        # 音频从“第 0 拍前 1.5 秒”起播；谱面 offset 不足 1.5 秒时先静音等待补齐。
        offset_ms = self.chart.timing.beat_to_song_ms(0.0)
        self.lead_wait_ms = max(0.0, LEAD_MS - offset_ms)
        self.audio_start_ms = max(0.0, offset_ms - LEAD_MS)
        self.play_wall = time.perf_counter()
        self.pause_wall = self.play_wall
        self.audio_started = self.lead_wait_ms <= 0.0
        self.audio.set_rate(self.settings.rate)
        self.audio.load(chart_audio_path(self.chart_path, self.chart))
        if self.audio_started:
            self.audio.play(self.audio_start_ms)
        self.state = "play"
        self.timer = 0.0

    def _now_ms(self) -> float:
        """判定用的歌曲毫秒：**就是听到的音频位置**（所以音乐和物件天然同步）。

        首拍引导（1.5 秒）靠“音频从第 0 拍前 1.5 秒起播”实现；谱面 offset 不够时，
        起播前先静音等待，此时用挂钟推进，第 0 拍仍在进入游玩 1.5 秒后到判定平面。
        """
        if self.audio_started:
            return self.audio.heard_position_ms()
        now = time.perf_counter()
        if self.paused:                     # 暂停时引导时间也停住
            now = self.pause_wall
        elapsed_ms = (now - self.play_wall) * 1000.0 * self.settings.rate
        return elapsed_ms - self.lead_wait_ms

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
                # 同一个键绑了多条轨道时都算按下，但**一次按键最多判一个**物件
                self.pressed_lanes.update(matched)
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
            self._register(self.judge.on_press(self._now_ms(), x=self.mouse_norm))

    def _register(self, result) -> None:
        """记录一条判定：命中音效 + 飘字。"""
        if result is None:
            return
        if result.grade != BREAK:
            self.hits.play_hit()
        self.popups.append((result.grade, result.grade, 0.7))

    def _update(self, dt: float) -> None:
        if self.state == "loading":
            self.timer += dt
            if self.timer >= 1.2:
                self._start_play()
        elif self.state == "play":
            if not self.audio_started and not self.paused:
                if (time.perf_counter() - self.play_wall) * 1000.0 >= self.lead_wait_ms:
                    self._begin_audio()
            if self.paused:
                self.timer += 0.0
                return
            self.timer += dt
            self.mouse_norm = self._mouse_norm()
            assert self.judge is not None
            now_ms = self._now_ms()
            self.judge.on_move(now_ms, self.mouse_norm)
            for result in self.judge.update(
                now_ms, inside_hold=self._inside_region(now_ms), held=set(self.pressed_lanes)
            ):
                self._register(result)
            self.popups = [
                (grade, text, left - dt) for grade, text, left in self.popups if left > dt
            ]
            if self.judge.judged_notes >= self.judge.total_notes and now_ms > 0:
                self._finish()
            elif not self.audio.is_playing and now_ms > 0:
                self._finish()

    def _finish(self) -> None:
        self.audio.pause()
        self.paused = False
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
        self.settings.rate = round(min(1.0, max(0.1, old + 0.1 * step)), 1)
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
            "↑↓ 选择（Home/End 跳首尾） · Enter 确认 · O 打开其他 JSON · S 设置 · Esc 退出",
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
            "设置（↑↓ 或滚轮选择 · ←→ 或点按钮调整 · 回车输入/切换 · Esc 保存并返回）",
            (40, 26),
            self.f_mid,
            WHITE,
        )
        s = self.settings
        values = [
            s.player_name,
            " ".join(s.keys),
            f"{s.note_thickness:.2f}",
            f"{s.audio_latency_ms:.0f} ms",
            f"{s.scroll_ms:.0f} ms",
            f"{s.mouse_sensitivity:.2f}x",
            "开" if s.side_lane_highlight else "关",
            "开" if s.unbounded_mouse else "关",
            f"{s.music_volume * 100:.0f}%",
            f"{s.hit_volume * 100:.0f}%",
            "OpenGL（需重启/切换即生效）" if s.renderer == "opengl" else "软件",
            f"{s.rate:.1f}x（得分 ×{s.rate ** 1.5:.2f}）",
        ]
        for index, label in enumerate(self.SETTING_ROWS):
            y = 100 + index * 44
            chosen = index == self.settings_row
            row = pygame.Rect(40, y - 6, self.w - 80, 40)
            self.ui[f"set:{index}"] = row
            if chosen:
                pygame.draw.rect(self.screen, PANEL, row, border_radius=6)
            self.text(("> " if chosen else "  ") + label, (60, y), self.f_mid, ACCENT if chosen else DIM)
            self.text(values[index], (640, y), self.f_mid, WHITE)
            minus = pygame.Rect(self.w - 150, y - 6, 34, 38)
            plus = pygame.Rect(self.w - 60, y - 6, 34, 38)
            self.ui[f"set_minus:{index}"] = minus
            self.ui[f"set_plus:{index}"] = plus
            pygame.draw.rect(self.screen, (44, 44, 58), minus, border_radius=4)
            pygame.draw.rect(self.screen, (44, 44, 58), plus, border_radius=4)
            self.text("-", (minus.x + 12, y - 2), self.f_mid, WHITE)
            self.text("+", (plus.x + 11, y - 2), self.f_mid, WHITE)
        if extra:
            self.text(extra, (60, self.h - 80), self.f_mid, (255, 226, 120))
        self.text("设置保存在 player.json（全局，不写进谱面）", (60, self.h - 40), self.f_small, DIM)

    def _draw_loading(self) -> None:
        assert self.chart is not None
        meta = self.chart.metadata
        self.center_text("谱面加载中…", 150, self.f_big, WHITE)
        self.center_text(f"{meta.title} · {meta.artist}", 240, self.f_mid, WHITE)
        self.center_text(f"难度：{meta.difficulty_name} {meta.difficulty_level}", 280, self.f_small, DIM)
        self.center_text(f"初始 BPM：{self.chart.timing.bpm_at(0.0):.2f}", 310, self.f_small, DIM)
        self.center_text(f"玩家：{self.settings.player_name}", 340, self.f_small, ACCENT)
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

    def _note_z(self, judge_ms: float, now_ms: float) -> float:
        span = max(1.0, self.settings.scroll_ms)
        return PlayerScene.note_z(1.0 - (judge_ms - now_ms) / span)

    def _draw_play(self) -> None:
        assert self.chart is not None and self.judge is not None
        now_ms = self._now_ms()
        self._draw_planes(now_ms)
        self._draw_notes(now_ms)
        self._draw_mouse_marker()
        self._draw_popups()
        self._draw_hud(now_ms)
        if now_ms < -LEAD_MS + 400:
            self.center_text("准备…", self.h * 0.66, self.f_big, (255, 226, 120))
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
        for y, tint in ((0.0, K6_TINT), (MOUSE_PLANE_HEIGHT, MOUSE_TINT)):
            far_left = self._proj(K6_LEFT, y, START_Z)
            far_right = self._proj(K6_LEFT + width, y, START_Z)
            near_left = self._proj(K6_LEFT, y, 0.0)
            near_right = self._proj(K6_LEFT + width, y, 0.0)
            if y > 0.0:
                # 鼠标平面在上方且更靠近镜头：半透明，免得挡住 6K 平面的远景
                self._alpha_quad([far_left, far_right, near_right, near_left], (34, 40, 58), 170)
            else:
                self._quad([far_left, far_right, near_right, near_left], (24, 26, 36))
            self._line(far_left, near_left, tint, 1)
            self._line(far_right, near_right, tint, 1)
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
        # 按下轨道时对应轨道高亮（按住期间一直亮）
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
        # 6K 平面只画小节线（不画更细的节拍线）
        timing = self.chart.timing
        beat = timing.song_ms_to_beat(max(0.0, now_ms))
        span_beats = max(0.5, self.settings.scroll_ms / 1000.0 * timing.bpm_at(beat) / 60.0)
        for line in timing.bar_lines(beat, beat + span_beats):
            z = self._note_z(timing.beat_to_song_ms(line), now_ms)
            if 0.0 <= z <= START_Z:
                self._line(self._proj(K6_LEFT, 0.0, z), self._proj(K6_LEFT + width, 0.0, z), (70, 72, 84), 1)
        # 判定平面：两条判定线（鼠标线在 6K 线上方 0.4 单位）
        k6_left = self._proj(K6_LEFT, 0.0, 0.0)
        k6_right = self._proj(K6_LEFT + width, 0.0, 0.0)
        m_left = self._proj(MOUSE_LEFT, MOUSE_PLANE_HEIGHT, 0.0)
        m_right = self._proj(MOUSE_LEFT + MOUSE_LEGAL_WIDTH, MOUSE_PLANE_HEIGHT, 0.0)
        self._line(k6_left, k6_right, WHITE, 3)
        self._line(m_left, m_right, MOUSE_TINT, 3)
        self._line(k6_left, m_left, (60, 62, 74), 1)
        self._line(k6_right, m_right, (60, 62, 74), 1)

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

    def _draw_notes(self, now_ms: float) -> None:
        assert self.chart is not None
        visible = [
            n
            for n in self.chart.all_notes()
            if self._note_z(self._head_ms(n), now_ms) <= START_Z and self._note_visible(n, now_ms)
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

    def _draw_lane_note(self, note: LaneNote, now_ms: float) -> None:
        assert self.chart is not None
        head_z = self._note_z(self._head_ms(note), now_ms)
        if head_z < -0.4:
            return
        x0 = K6_LEFT + min(note.lane_start, note.lane_end) * LANE_WIDTH + LANE_WIDTH * 0.18
        x1 = K6_LEFT + (max(note.lane_start, note.lane_end) + 1) * LANE_WIDTH - LANE_WIDTH * 0.18
        if note.is_hold:
            tail_z = self._note_z(self.chart.timing.beat_to_song_ms(note.tail_beat), now_ms)
            near, near2 = self._proj(x0, 0.0, max(0.0, head_z)), self._proj(x1, 0.0, max(0.0, head_z))
            far, far2 = self._proj(x0, 0.0, max(0.0, tail_z)), self._proj(x1, 0.0, max(0.0, tail_z))
            self._quad([near, near2, far2, far], (60, 120, 220))
            self._line(
                self._proj((x0 + x1) / 2, 0.0, max(0.0, head_z)),
                self._proj((x0 + x1) / 2, 0.0, max(0.0, tail_z)),
                WHITE,
                2,
            )
        else:
            thickness = self.settings.note_thickness
            near, near2 = self._proj(x0, 0.0, max(0.0, head_z)), self._proj(x1, 0.0, max(0.0, head_z))
            far_z = min(START_Z, max(0.0, head_z) + thickness)
            far, far2 = self._proj(x0, 0.0, far_z), self._proj(x1, 0.0, far_z)
            self._quad([near, near2, far2, far], (70, 140, 240))
            self._line(near, near2, WHITE, 2)

    def _draw_region(self, note: MouseRegion, now_ms: float) -> None:
        """空域：画在鼠标物件运动的水平面内（半透明蓝底 + 左右两条边线）。"""
        assert self.chart is not None
        timing = self.chart.timing
        if self._note_z(timing.beat_to_song_ms(note.beat_start), now_ms) > START_Z:
            return
        samples = 12
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
        # 半透明：空域在鼠标平面上，挡住下面的 6K 物件就没法看谱了
        self._alpha_quad(left_points + list(reversed(right_points)), (60, 110, 200), 104)
        for index in range(len(left_points) - 1):
            self._line(left_points[index], left_points[index + 1], MOUSE_TINT, 2)
            self._line(right_points[index], right_points[index + 1], MOUSE_TINT, 2)

    def _draw_pattern(self, note: Note, now_ms: float) -> None:
        """滑键 / 点击按键：图案平行于判定平面，高度 0.03 单位。"""
        z = self._note_z(self._head_ms(note), now_ms)
        if z < -0.4 or z > START_Z:
            return
        z = max(0.0, z)
        if isinstance(note, MouseSlide):
            lo, hi = note.x_range()
            color = (110, 230, 160) if note.direction >= 0 else (235, 210, 110)
        else:
            assert isinstance(note, MouseClick)
            lo, hi = note.x_range()
            color = (235, 90, 80)
        x0 = MOUSE_LEFT + min(lo, hi) * MOUSE_LEGAL_WIDTH
        x1 = MOUSE_LEFT + max(lo, hi) * MOUSE_LEGAL_WIDTH
        top = MOUSE_PLANE_HEIGHT + NOTE_HEIGHT_UNIT
        middle = (MOUSE_PLANE_HEIGHT + top) / 2
        self._alpha_quad(
            [
                self._proj(x0, MOUSE_PLANE_HEIGHT, z),
                self._proj(x1, MOUSE_PLANE_HEIGHT, z),
                self._proj(x1, top, z),
                self._proj(x0, top, z),
            ],
            color,
            132,
        )
        self._line(self._proj(x0, middle, z), self._proj(x1, middle, z), WHITE, 2)
        if isinstance(note, MouseClick):
            # 两侧竖线 + 中央菱形，高度都是 0.03 单位
            self._line(self._proj(x0, MOUSE_PLANE_HEIGHT, z), self._proj(x0, top, z), WHITE, 2)
            self._line(self._proj(x1, MOUSE_PLANE_HEIGHT, z), self._proj(x1, top, z), WHITE, 2)
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

    def _draw_mouse_marker(self) -> None:
        """鼠标判定线后方的三角形标识（只画边线）。"""
        x = MOUSE_LEFT + self.mouse_norm * MOUSE_LEGAL_WIDTH
        tip = self._proj(x, MOUSE_PLANE_HEIGHT + 0.18, 0.0)
        left = self._proj(x - 0.07, MOUSE_PLANE_HEIGHT + 0.02, 0.0)
        right = self._proj(x + 0.07, MOUSE_PLANE_HEIGHT + 0.02, 0.0)
        pygame.draw.polygon(
            self.screen, MOUSE_TINT,
            [(tip[0], tip[1]), (left[0], left[1]), (right[0], right[1])], 2,
        )

    def _draw_popups(self) -> None:
        """判定飘字：在判定平面中央向上飘并淡出。"""
        if not self.popups:
            return
        base_y = self.scene.judgment_lines(self.w, self.h)["mouse"]
        for index, (grade, text, left) in enumerate(self.popups[-6:]):
            surface = self.f_mid.render(text, True, GRADE_COLORS.get(grade, WHITE))
            alpha = max(0.0, min(1.0, left / 0.7))
            surface.set_alpha(int(255 * alpha))
            offset = (0.7 - left) * 60.0
            self.screen.blit(
                surface,
                (self.w / 2 - surface.get_width() / 2 + index * 6, base_y - 90 - offset),
            )

    def _draw_hud(self, now_ms: float) -> None:
        assert self.chart is not None and self.judge is not None
        meta = self.chart.metadata
        beat = self.chart.timing.song_ms_to_beat(max(0.0, now_ms))
        left_lines = (
            f"{meta.title} · {meta.artist}",
            f"难度 {meta.difficulty_name} {meta.difficulty_level} · BPM {self.chart.timing.bpm_at(beat):.2f}",
            f"玩家 {self.settings.player_name}",
        )
        for index, line in enumerate(left_lines):
            self.text(line, (24, 16 + index * 26), self.f_small, WHITE if index == 0 else DIM)
        counts = self.judge.counts
        right_lines = (
            f"准度 {self.judge.accuracy * 100:.1f}% · 倍速 {self.settings.rate:.1f}x",
            f"分数 {self.judge.score}（×{self.settings.rate ** 1.5:.2f}）",
            f"连击 {self.judge.combo}（最大 {self.judge.max_combo}）",
            "  ".join(f"{grade} {counts[grade]}" for grade in GRADES),
        )
        for index, line in enumerate(right_lines):
            surface = self.f_small.render(line, True, WHITE if index == 0 else DIM)
            self.screen.blit(surface, (self.w - surface.get_width() - 24, 16 + index * 26))
        # 连击数醒目显示在判定面上方
        if self.judge.combo >= 5:
            base_y = self.scene.judgment_lines(self.w, self.h)["mouse"]
            self.center_text(f"{self.judge.combo}", base_y - 170, self.f_big, (255, 226, 120))

    def _draw_result(self) -> None:
        assert self.chart is not None and self.judge is not None
        meta = self.chart.metadata
        half = self.w // 2
        pygame.draw.rect(self.screen, PANEL, (16, 16, half - 32, self.h - 32))
        pygame.draw.rect(self.screen, PANEL, (half + 16, 16, half - 32, self.h - 32))
        self.text("结算", (46, 40), self.f_big, WHITE)
        left_lines = (
            meta.title,
            meta.artist,
            f"{meta.difficulty_name} {meta.difficulty_level}",
            f"玩家：{self.settings.player_name}",
        )
        for index, line in enumerate(left_lines):
            self.text(line, (46, 140 + index * 34), self.f_mid, WHITE if index == 0 else DIM)
        counts = self.judge.counts
        self.text(
            f"分数：{self.judge.score}（倍速 {self.settings.rate:.1f}x，×{self.settings.rate ** 1.5:.2f}）",
            (half + 46, 140),
            self.f_big,
            (255, 226, 120),
        )
        self.text(f"最大连击：{self.judge.max_combo}", (half + 46, 196), self.f_mid, WHITE)
        total = max(1, self.judge.total_notes)
        self.text(
            f"判定数：{self.judge.judged_notes} / {self.judge.total_notes}",
            (half + 46, 226),
            self.f_small,
            DIM,
        )
        for index, grade in enumerate(GRADES):
            share = counts[grade] / total * 100.0
            self.text(
                f"{grade}：{counts[grade]}（{share:.1f}%）",
                (half + 46, 258 + index * 34),
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

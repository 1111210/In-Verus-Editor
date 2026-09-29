"""游玩器 GUI（pygame）：主界面 / 设置 / 加载 / 游玩（3D）/ 结算。

运行：``python run_player.py [谱面.json …]``

* 软件透视投影（:mod:`rhythm_editor.game.scene`），需要时可换成 OpenGL；
* 判定核心 :class:`~rhythm_editor.game.judge.Judge`；音频复用编辑器的 ``AudioPlayer``
  （倍速 / 系统音频延迟补偿都已在编辑器里验证过）；
* 鼠标平面在 6K 平面上方 0.4 单位、离镜头更近，看起来更宽；
* 第 0 拍在进入游玩 1.5 秒后到达判定平面（``LEAD_MS``）。
"""

from __future__ import annotations

import glob
import os
import sys
from typing import List, Optional, Tuple

import pygame

from .. import config
from ..audio import AudioPlayer
from ..io.chart_io import chart_audio_path, load_chart
from ..model.chart import Chart
from ..model.notes import LaneNote, MouseClick, MouseRegion, MouseSlide, Note
from ..metronome import Metronome
from . import settings as psettings
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

SCREEN_W, SCREEN_H = 1280, 800
LEAD_MS = 1500.0
REGION_PAD = 0.01

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


def chart_files(root: str) -> List[str]:
    """默认列出的谱面：``charts/**/*.json``（跳过下划线开头的临时文件）。"""
    found = sorted(glob.glob(os.path.join(root, "charts", "**", "*.json"), recursive=True))
    return [p for p in found if not os.path.basename(p).startswith("_")]


def key_code(name: str) -> int:
    """键位名 -> pygame 键值（支持 shift / space / a …）。"""
    name = (name or "").strip().lower()
    special = {
        "shift": pygame.K_LSHIFT,
        "space": pygame.K_SPACE,
        "ctrl": pygame.K_LCTRL,
        "alt": pygame.K_LALT,
        "enter": pygame.K_RETURN,
        "tab": pygame.K_TAB,
    }
    if name in special:
        return special[name]
    try:
        return pygame.key.key_code(name)
    except Exception:
        return pygame.K_UNKNOWN


class PlayerApp:
    """游玩器主程序（状态机 + 渲染 + 判定）。"""

    def __init__(self, paths: Optional[List[str]] = None) -> None:
        pygame.init()
        pygame.display.set_caption(f"In Verus 游玩器 · {config.VERSION}")
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        font = "microsoftyaheiui,arial"
        self.f_big = pygame.font.SysFont(font, 40, bold=True)
        self.f_mid = pygame.font.SysFont(font, 24, bold=True)
        self.f_small = pygame.font.SysFont(font, 18)
        self.settings = psettings.load_settings()
        self.scene = PlayerScene(Camera())
        self.audio = AudioPlayer()
        self.root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.paths = list(paths or []) or chart_files(self.root)
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
        self.settings_row = 0

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
            pygame.display.flip()
        self.audio.unload()
        pygame.quit()
        return 0

    def _events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                size = (max(800, event.w), max(500, event.h))
                self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
            elif self.state == "menu":
                self._event_menu(event)
            elif self.state == "settings":
                self._event_settings(event)
            elif self.state == "loading":
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    self.state = "menu"
            elif self.state == "play":
                self._event_play(event)
            elif self.state == "result":
                self._event_result(event)

    # ------------------------------------------------------------ 菜单
    def _event_menu(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_ESCAPE:
            self.running = False
        elif event.key in (pygame.K_DOWN, pygame.K_UP) and self.paths:
            step = 1 if event.key == pygame.K_DOWN else -1
            self.selected = (self.selected + step) % len(self.paths)
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.paths:
            self._enter_loading()
        elif event.key == pygame.K_s:
            self.state = "settings"

    def _enter_loading(self) -> None:
        try:
            chart, _ = load_chart(self.paths[self.selected])
        except Exception as exc:                     # pragma: no cover - 依赖外部文件
            self.message = f"读取失败：{exc}"
            return
        self.chart = chart
        self.chart_path = self.paths[self.selected]
        self.state = "loading"
        self.timer = 0.0
        self.message = ""

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
    )

    def _event_settings(self, event) -> None:
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

    def _edit_setting_text(self) -> None:
        s = self.settings
        if self.settings_row == 0:
            s.player_name = self._prompt("玩家名称", s.player_name)
        elif self.settings_row == 1:
            for index, name in enumerate(s.keys):
                s.keys[index] = self._prompt(f"第 {index + 1} 轨键位", name)
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
        s.normalize()
        if row in (8, 9) and self.state == "play":
            self.audio.set_volume(s.music_volume)
            self.hits.set_volume(s.hit_volume)

    def _prompt(self, label: str, initial: str) -> str:
        """极简文本输入（回车确认 / Esc 取消）。"""
        entered = initial
        while self.running:
            self._draw_settings(extra=f"{label}：{entered}_")
            pygame.display.flip()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    return entered
                if event.type != pygame.KEYDOWN:
                    continue
                if event.key == pygame.K_RETURN:
                    return entered
                if event.key == pygame.K_ESCAPE:
                    return initial
                if event.key == pygame.K_BACKSPACE:
                    entered = entered[:-1]
                elif event.unicode and event.unicode.isprintable():
                    entered += event.unicode
        return entered

    # ------------------------------------------------------------ 游玩
    def _start_play(self) -> None:
        assert self.chart is not None
        self.judge = Judge(self.chart)
        self.pressed_lanes.clear()
        self.message = ""
        self.audio.latency_ms = self.settings.audio_latency_ms
        self.audio.set_volume(self.settings.music_volume)
        self.hits.set_volume(self.settings.hit_volume)
        self.hits.set_enabled(True)
        self.popups.clear()
        self.audio.load(chart_audio_path(self.chart_path, self.chart))
        self.audio.play(self.chart.timing.beat_to_song_ms(0.0))     # 从第 0 拍起播
        self.state = "play"
        self.timer = 0.0

    def _now_ms(self) -> float:
        """判定用的歌曲毫秒：减去“进入游玩 -> 第 0 拍”的 1.5 秒准备时间。"""
        assert self.chart is not None
        return self.audio.heard_position_ms() - self.chart.timing.beat_to_song_ms(0.0) - LEAD_MS

    def _event_play(self, event) -> None:
        assert self.judge is not None
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_SPACE:
                self._toggle_pause()
                return
            if event.key == pygame.K_ESCAPE:
                if self.paused:
                    self.state = "menu"        # 暂停中再按 Esc：回主界面
                    self.paused = False
                else:
                    self._toggle_pause()
                return
            if event.key == pygame.K_r and self.paused:
                self.paused = False
                self._start_play()
                return
            if self.paused:
                return                          # 暂停时不接受游玩输入
            for lane, name in enumerate(self.settings.keys):
                if event.key == key_code(name):
                    self.pressed_lanes.add(lane)
                    self._register(self.judge.on_press(self._now_ms(), lane=lane))
                    break
        elif event.type == pygame.KEYUP:
            for lane, name in enumerate(self.settings.keys):
                if event.key == key_code(name):
                    self.pressed_lanes.discard(lane)
                    break
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
            self.audio.pause()
        else:
            self.audio.set_rate(self.audio.rate)     # 保持当前倍速
            self.audio.play(self.audio.position_ms())

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

    def _draw_menu(self) -> None:
        self.center_text("In Verus 游玩器", 90, self.f_big, WHITE)
        self.center_text("↑↓ 选择谱面 · Enter 开始 · S 设置 · Esc 退出", 150, self.f_small, DIM)
        if not self.paths:
            self.center_text("没有找到谱面（先往 charts/ 放一张 .json）", 260, self.f_mid, DIM)
        for index, path in enumerate(self.paths[:14]):
            name = os.path.relpath(path, self.root)
            chosen = index == self.selected
            label = ("[ " + name + " ]") if chosen else ("  " + name + "  ")
            self.center_text(label, 210 + index * 30, self.f_mid, ACCENT if chosen else DIM)
        if self.message:
            self.center_text(self.message, self.h - 70, self.f_small, (240, 120, 120))

    def _draw_settings(self, extra: str = "") -> None:
        self.text("设置（↑↓ 选择 · ←→ 调整 · 回车输入/切换 · Esc 保存并返回）", (40, 26), self.f_mid, WHITE)
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
        ]
        for index, label in enumerate(self.SETTING_ROWS):
            y = 100 + index * 44
            chosen = index == self.settings_row
            self.text(("▶ " if chosen else "  ") + label, (60, y), self.f_mid, ACCENT if chosen else DIM)
            self.text(values[index], (620, y), self.f_mid, WHITE)
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
            self.center_text("空格 继续 · R 重试 · Esc 回主界面", self.h * 0.45, self.f_mid, ACCENT)

    def _draw_planes(self, now_ms: float) -> None:
        assert self.chart is not None
        width = LANE_COUNT * LANE_WIDTH
        for y, tint in ((0.0, K6_TINT), (MOUSE_PLANE_HEIGHT, MOUSE_TINT)):
            far_left = self._proj(K6_LEFT, y, START_Z)
            far_right = self._proj(K6_LEFT + width, y, START_Z)
            near_left = self._proj(K6_LEFT, y, 0.0)
            near_right = self._proj(K6_LEFT + width, y, 0.0)
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

    def _draw_notes(self, now_ms: float) -> None:
        assert self.chart is not None
        visible = [n for n in self.chart.all_notes() if self._note_z(self._head_ms(n), now_ms) <= START_Z]
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
        self._quad(left_points + list(reversed(right_points)), (40, 70, 140))
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
        self._quad(
            [
                self._proj(x0, MOUSE_PLANE_HEIGHT, z),
                self._proj(x1, MOUSE_PLANE_HEIGHT, z),
                self._proj(x1, top, z),
                self._proj(x0, top, z),
            ],
            color,
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
            f"准度 {self.judge.accuracy * 100:.1f}%",
            f"分数 {self.judge.score}",
            "  ".join(f"{grade} {counts[grade]}" for grade in GRADES),
        )
        for index, line in enumerate(right_lines):
            surface = self.f_small.render(line, True, WHITE if index == 0 else DIM)
            self.screen.blit(surface, (self.w - surface.get_width() - 24, 16 + index * 26))

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
        self.text(f"分数：{self.judge.score}", (half + 46, 140), self.f_big, (255, 226, 120))
        for index, grade in enumerate(GRADES):
            self.text(
                f"{grade}：{counts[grade]}",
                (half + 46, 230 + index * 34),
                self.f_mid,
                GRADE_COLORS[grade],
            )
        self.text("R 重试", (46, self.h - 80), self.f_mid, ACCENT)
        self.text("Esc 回到主界面", (self.w - 280, self.h - 80), self.f_mid, ACCENT)

    def _event_result(self, event) -> None:
        if event.type != pygame.KEYDOWN:
            return
        if event.key == pygame.K_r:
            self._start_play()
        elif event.key == pygame.K_ESCAPE:
            self.state = "menu"


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - GUI 入口
    args = list(sys.argv[1:] if argv is None else argv)
    paths = [p for p in args if os.path.isfile(p)]
    return PlayerApp(paths).run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

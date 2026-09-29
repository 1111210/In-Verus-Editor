"""游玩器设置（全局保存，与编辑器的设置分开）。

存在 ``%APPDATA%/InVerus/player.json``（其他系统 ``~/.config/inverus/player.json``，
可用 ``VERUS_PLAYER_SETTINGS`` 覆盖），**不写进谱面**。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

ENV_PLAYER_SETTINGS = "VERUS_PLAYER_SETTINGS"

DEFAULT_KEYS: List[str] = ["shift", "a", "s", "d", "f", "space"]
SPEED_MIN = 1.0
SPEED_MAX = 10.0
SPEED_STEP = 0.1
BASE_SCROLL_MS = 1500.0        # 流速 1.0 对应的可视毫秒；n 流速 = 1500 / n 毫秒
RATE_MIN = 0.1                 # 游玩倍速：0.1 ~ 3.0（0.1 一档）
RATE_MAX = 3.0
RATE_STEP = 0.1


@dataclass
class PlayerSettings:
    """玩家偏好。"""

    player_name: str = "Player"
    keys: List[str] = field(default_factory=lambda: list(DEFAULT_KEYS))
    note_thickness: float = 0.35          # 物件厚度（单位，除空域外）
    audio_latency_ms: float = 0.0         # 系统音频延迟（毫秒，可为负）
    speed: float = 1.0                    # 流速 1.0 ~ 10.0（0.1 一档）；可视毫秒 = 1500 / 流速
    side_lane_highlight: bool = False     # 6K 第 1 / 第 6 轨高亮
    mouse_sensitivity: float = 1.0        # 0.1 ~ 10
    unbounded_mouse: bool = False         # 无界鼠标：实际位置即判定位置
    renderer: str = "software"            # software / opengl
    rate: float = 1.0                     # 游玩倍速 0.1 ~ 3.0x（每 0.1 一档）
    music_volume: float = 1.0
    hit_volume: float = 0.6
    recent: List[str] = field(default_factory=list)     # 最近打开的谱面（最多 8 条）

    def normalize(self) -> None:
        self.keys = [
            (k or "").strip().lower() or DEFAULT_KEYS[i % len(DEFAULT_KEYS)]
            for i, k in enumerate(self.keys[:6] or DEFAULT_KEYS)
        ]
        while len(self.keys) < 6:
            self.keys.append(DEFAULT_KEYS[len(self.keys)])
        self.note_thickness = min(1.0, max(0.05, float(self.note_thickness)))
        self.audio_latency_ms = min(5000.0, max(-5000.0, float(self.audio_latency_ms)))
        self.speed = round(min(SPEED_MAX, max(SPEED_MIN, float(self.speed))), 1)
        self.mouse_sensitivity = min(10.0, max(0.1, float(self.mouse_sensitivity)))
        self.rate = round(min(RATE_MAX, max(RATE_MIN, float(self.rate))), 1)
        self.music_volume = min(1.0, max(0.0, float(self.music_volume)))
        self.hit_volume = min(1.0, max(0.0, float(self.hit_volume)))
        self.player_name = (self.player_name or "Player")[:24]
        if self.renderer not in ("software", "opengl"):
            self.renderer = "software"
        unique: List[str] = []
        for path in self.recent:
            text = str(path)
            if text and text not in unique:
                unique.append(text)
        self.recent = unique[:8]

    def to_dict(self) -> Dict[str, object]:
        self.normalize()
        return asdict(self)

    @property
    def scroll_ms(self) -> float:
        """1x 下“同时可见”的毫秒数（由流速推出：1500 / speed）。"""
        speed = min(SPEED_MAX, max(SPEED_MIN, float(self.speed or SPEED_MIN)))
        return BASE_SCROLL_MS / speed

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, object]]) -> "PlayerSettings":
        data = data or {}
        obj = cls(
            player_name=str(data.get("player_name", "Player")),
            keys=list(data.get("keys", DEFAULT_KEYS)),  # type: ignore[arg-type]
            note_thickness=float(data.get("note_thickness", 0.35)),
            audio_latency_ms=float(data.get("audio_latency_ms", 0.0)),
            speed=float(data.get("speed", _speed_from_legacy(data.get("scroll_ms")))),
            side_lane_highlight=bool(data.get("side_lane_highlight", False)),
            mouse_sensitivity=float(data.get("mouse_sensitivity", 1.0)),
            unbounded_mouse=bool(data.get("unbounded_mouse", False)),
            renderer=str(data.get("renderer", "software")),
            rate=float(data.get("rate", 1.0)),
            music_volume=float(data.get("music_volume", 1.0)),
            hit_volume=float(data.get("hit_volume", 0.6)),
            recent=list(data.get("recent", [])),  # type: ignore[arg-type]
        )
        obj.normalize()
        return obj


def _speed_from_legacy(scroll_ms: object) -> float:
    """老存档里只有 ``scroll_ms``：换算成流速（1500 / 毫秒），没有就用 1.0。"""
    try:
        value = float(scroll_ms)          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if value <= 0.0:
        return 1.0
    return min(SPEED_MAX, max(SPEED_MIN, BASE_SCROLL_MS / value))


def settings_path() -> str:
    override = os.environ.get(ENV_PLAYER_SETTINGS)
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "InVerus", "player.json")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "inverus", "player.json")


def load_settings(path: Optional[str] = None) -> PlayerSettings:
    target = path or settings_path()
    try:
        with open(target, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    return PlayerSettings.from_dict(data if isinstance(data, dict) else {})


def save_settings(settings: PlayerSettings, path: Optional[str] = None) -> str:
    target = path or settings_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
    except OSError:  # pragma: no cover
        pass
    return target

"""编辑器全局设置：视图、音效、快捷键与演示视图参数。

这些设置随程序保存，独立于谱面文件，不属于谱面的可序列化数据模型。
``Chart.settings`` 可以引用同一份设置对象；应用内部通过
``EditorApp.editor_settings`` 或 ``ViewState.settings`` 访问。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import config
from metronome import METRONOME_MODES, MODE_OFF
from shortcuts import normalize_shortcuts


@dataclass
class EditorSettings:
    """全局“其他设置”。"""

    zoom_seconds: float = config.DEFAULT_ZOOM_SECONDS
    grid_division: int = config.DEFAULT_GRID_DIVISION
    horizontal_divisions: int = config.DEFAULT_HORIZONTAL_DIVISIONS
    audio_latency_ms: float = config.DEFAULT_AUDIO_LATENCY_MS
    metronome: str = "off"
    hit_enabled: bool = True
    side_lane_highlight: bool = False
    show_past_notes: bool = False
    ignore_speed_groups: bool = False
    invert_wheel_scroll: bool = False
    invert_wheel_zoom: bool = False
    music_volume: float = 1.0
    metronome_volume: float = 0.6
    hit_volume: float = 0.6
    demo_note_thickness: float = config.DEMO_NOTE_THICKNESS
    demo_multi_thickness: float = config.DEMO_MULTI_THICKNESS
    demo_slide_thickness: float = config.DEMO_SLIDE_THICKNESS
    demo_speed: float = config.DEMO_SPEED
    demo_side_lane_highlight: bool = False
    shortcuts: Dict[str, str] = field(default_factory=lambda: normalize_shortcuts(None))
    last_chart_path: str = ""

    def normalize(self) -> None:
        self.zoom_seconds = min(
            config.ZOOM_MAX_SECONDS, max(config.ZOOM_MIN_SECONDS, float(self.zoom_seconds))
        )
        self.grid_division = max(config.GRID_DIVISION_MIN, int(self.grid_division))
        self.horizontal_divisions = max(0, int(self.horizontal_divisions))
        self.audio_latency_ms = min(
            config.AUDIO_LATENCY_MAX_MS,
            max(config.AUDIO_LATENCY_MIN_MS, float(self.audio_latency_ms)),
        )
        if self.metronome not in METRONOME_MODES:
            self.metronome = MODE_OFF
        self.hit_enabled = bool(self.hit_enabled)
        self.side_lane_highlight = bool(self.side_lane_highlight)
        self.show_past_notes = bool(self.show_past_notes)
        self.ignore_speed_groups = bool(self.ignore_speed_groups)
        self.invert_wheel_scroll = bool(self.invert_wheel_scroll)
        self.invert_wheel_zoom = bool(self.invert_wheel_zoom)
        self.music_volume = max(0.0, min(1.0, float(self.music_volume)))
        self.metronome_volume = max(0.0, min(1.0, float(self.metronome_volume)))
        self.hit_volume = max(0.0, min(1.0, float(self.hit_volume)))
        note_low, note_high, _ = config.DEMO_NOTE_THICKNESS_RANGE
        pattern_low, pattern_high, _ = config.DEMO_PATTERN_THICKNESS_RANGE
        speed_low, speed_high, _ = config.DEMO_SPEED_RANGE
        self.demo_note_thickness = min(
            note_high, max(note_low, float(self.demo_note_thickness))
        )
        self.demo_multi_thickness = min(
            note_high, max(note_low, float(self.demo_multi_thickness))
        )
        self.demo_slide_thickness = min(
            pattern_high, max(pattern_low, float(self.demo_slide_thickness))
        )
        self.demo_speed = round(min(speed_high, max(speed_low, float(self.demo_speed))), 3)
        self.demo_side_lane_highlight = bool(self.demo_side_lane_highlight)
        self.shortcuts = normalize_shortcuts(self.shortcuts)
        self.last_chart_path = str(self.last_chart_path or "").strip()

    @property
    def horizontal_grid_enabled(self) -> bool:
        return self.horizontal_divisions > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zoom_seconds": self.zoom_seconds,
            "grid_division": self.grid_division,
            "horizontal_divisions": self.horizontal_divisions,
            "audio_latency_ms": self.audio_latency_ms,
            "metronome": self.metronome,
            "hit_enabled": bool(self.hit_enabled),
            "side_lane_highlight": bool(self.side_lane_highlight),
            "show_past_notes": bool(self.show_past_notes),
            "ignore_speed_groups": bool(self.ignore_speed_groups),
            "invert_wheel_scroll": bool(self.invert_wheel_scroll),
            "invert_wheel_zoom": bool(self.invert_wheel_zoom),
            "music_volume": self.music_volume,
            "metronome_volume": self.metronome_volume,
            "hit_volume": self.hit_volume,
            "demo_note_thickness": self.demo_note_thickness,
            "demo_multi_thickness": self.demo_multi_thickness,
            "demo_slide_thickness": self.demo_slide_thickness,
            "demo_speed": self.demo_speed,
            "demo_side_lane_highlight": self.demo_side_lane_highlight,
            "shortcuts": dict(self.shortcuts),
            "last_chart_path": self.last_chart_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "EditorSettings":
        data = data or {}
        obj = cls(
            zoom_seconds=float(data.get("zoom_seconds", config.DEFAULT_ZOOM_SECONDS)),
            grid_division=int(data.get("grid_division", config.DEFAULT_GRID_DIVISION)),
            horizontal_divisions=int(
                data.get("horizontal_divisions", config.DEFAULT_HORIZONTAL_DIVISIONS)
            ),
            audio_latency_ms=float(
                data.get("audio_latency_ms", config.DEFAULT_AUDIO_LATENCY_MS)
            ),
            metronome=str(data.get("metronome", "off")),
            hit_enabled=bool(data.get("hit_enabled", True)),
            side_lane_highlight=bool(data.get("side_lane_highlight", False)),
            show_past_notes=bool(data.get("show_past_notes", False)),
            ignore_speed_groups=bool(data.get("ignore_speed_groups", False)),
            invert_wheel_scroll=bool(data.get("invert_wheel_scroll", False)),
            invert_wheel_zoom=bool(data.get("invert_wheel_zoom", False)),
            music_volume=float(data.get("music_volume", 1.0)),
            metronome_volume=float(data.get("metronome_volume", 0.6)),
            hit_volume=float(data.get("hit_volume", 0.6)),
            demo_note_thickness=float(
                data.get("demo_note_thickness", config.DEMO_NOTE_THICKNESS)
            ),
            demo_multi_thickness=float(
                data.get("demo_multi_thickness", config.DEMO_MULTI_THICKNESS)
            ),
            demo_slide_thickness=float(
                data.get("demo_slide_thickness", config.DEMO_SLIDE_THICKNESS)
            ),
            demo_speed=float(data.get("demo_speed", config.DEMO_SPEED)),
            demo_side_lane_highlight=bool(data.get("demo_side_lane_highlight", False)),
            shortcuts=normalize_shortcuts(
                data.get("shortcuts") if isinstance(data.get("shortcuts"), dict) else None
            ),
            last_chart_path=str(data.get("last_chart_path", "")),
        )
        obj.normalize()
        return obj

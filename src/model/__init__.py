"""谱面数据模型：时间轴、物件、速度组、谱面与文档（撤销栈）。"""

from __future__ import annotations

from .timing import BpmSegment, TimingMap
from .notes import (
    BoundaryCurve,
    LaneNote,
    LAYER_CLICK,
    LAYER_MAX,
    LAYER_MULTI_HOLD,
    LAYER_MULTI_TAP,
    LAYER_REGION,
    LAYER_SINGLE_HOLD,
    LAYER_SINGLE_TAP,
    LAYER_SLIDE,
    MouseClick,
    MouseNote,
    MouseRegion,
    MouseSlide,
    NOTE_LANE,
    NOTE_CLICK,
    NOTE_REGION,
    NOTE_SLIDE,
    layer_rank,
)
from .speed import SpeedGroup, SpeedSegment
from .chart import Chart, EditorSettings, Metadata
from .document import Document

__all__ = [
    "BpmSegment",
    "TimingMap",
    "BoundaryCurve",
    "LaneNote",
    "LAYER_CLICK",
    "LAYER_MAX",
    "LAYER_MULTI_HOLD",
    "LAYER_MULTI_TAP",
    "LAYER_REGION",
    "LAYER_SINGLE_HOLD",
    "LAYER_SINGLE_TAP",
    "LAYER_SLIDE",
    "MouseClick",
    "MouseNote",
    "MouseRegion",
    "MouseSlide",
    "NOTE_LANE",
    "NOTE_CLICK",
    "NOTE_REGION",
    "NOTE_SLIDE",
    "layer_rank",
    "SpeedGroup",
    "SpeedSegment",
    "Chart",
    "EditorSettings",
    "Metadata",
    "Document",
]

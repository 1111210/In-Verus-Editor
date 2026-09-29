"""节拍音（打拍器）：播放时按小节或按节拍发出“滴”声。

* :func:`clicks_between` —— 纯函数，算出这一段时间里应该响几声（便于单测）；
* :class:`Metronome`    —— 用 pygame 合成一小段正弦音并播放，缺依赖时静默降级。

声音是**独立于歌曲**的（pygame 的 Sound 走单独通道），所以开着节拍音不影响播放。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional

from . import config

if TYPE_CHECKING:  # 只用于类型标注，避免和 model 包循环导入
    from .model.timing import TimingMap

MODE_OFF = "off"
MODE_BAR = "bar"
MODE_BEAT = "beat"
METRONOME_MODES = (MODE_OFF, MODE_BAR, MODE_BEAT)

LABELS = {
    MODE_OFF: "关闭（默认）",
    MODE_BAR: "小节音（每小节响一声）",
    MODE_BEAT: "节拍音（每拍响一声）",
}

# 声音参数
SAMPLE_RATE = 44100
CLICK_MS = 45.0            # 短促一声，避免盖住音乐
FADE_MS = 12.0             # 尾部渐隐，消除爆音
BAR_FREQ = 1320.0          # 小节音高一点（更容易和节拍音区分）
BEAT_FREQ = 880.0
VOLUME = 0.20


def clicks_between(
    timing: "TimingMap", mode: str, prev_beat: float, now_beat: float
) -> List[float]:
    """返回 ``(prev_beat, now_beat]`` 之间该响的拍位置（最多一条）。

    * ``MODE_BAR``：小节线（每段起点重新起算，BPM 变更处一定有线）；
    * ``MODE_BEAT``：整数拍；
    * ``MODE_OFF`` / 时间没有前进 / 跳转太远（seek、卡顿）都返回空列表，
      不会一口气补发一串“滴”声。
    """
    prev = float(prev_beat)
    now = float(now_beat)
    if mode not in (MODE_BAR, MODE_BEAT) or now <= prev:
        return []
    if now - prev > config.METRONOME_MAX_JUMP_BEATS:
        return []
    if mode == MODE_BAR:
        lines = timing.bar_lines(prev, now)
        return [b for b in lines if prev < b <= now + 1e-9][:1]
    first = math.floor(prev) + 1
    if first <= now + 1e-9:
        return [float(first)]
    return []


def click_samples(freq: float, sample_rate: int = SAMPLE_RATE, channels: int = 1):
    """合成一声“滴”（长度固定为 ``CLICK_MS`` 毫秒，与播放倍速无关）。

    返回 ``numpy`` 数组（int16）。**播放倍速只影响歌曲**：节拍音走独立的
    ``pygame.mixer.Sound`` 通道，不做时间伸缩，所以慢放时这一声依然是这么长。
    """
    import numpy as np

    total = max(1, int(sample_rate * CLICK_MS / 1000.0))
    fade = max(1, int(sample_rate * FADE_MS / 1000.0))
    phase = np.arange(total, dtype=np.float32) / float(sample_rate)
    wave = np.sin(2.0 * np.pi * freq * phase).astype(np.float32)
    tail = np.ones(total, dtype=np.float32)
    fade_len = min(fade, total)
    tail[total - fade_len:] = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
    data = (wave * tail * 32767.0 * VOLUME).astype(np.int16)
    if channels > 1:
        data = np.repeat(data.reshape(-1, 1), channels, axis=1)
    return np.ascontiguousarray(data)


class Metronome:
    """合成并播放“滴”声（没有 pygame / 没有设备时静默失效）。"""

    def __init__(self) -> None:
        self._sounds: dict = {}
        self._failed = False
        self._format: Optional[tuple] = None

    def _sound(self, freq: float):
        if self._failed:
            return None
        try:  # pragma: no cover - 依赖音频设备
            import numpy as np
            import pygame

            from .audio import _ensure_mixer

            rate, _size, channels = _ensure_mixer()
            # 混音器格式（采样率 / 声道数）变了就重建缓存：
            # 这样“这一声有多长”始终等于 CLICK_MS，不受播放倍速或换歌影响
            fmt = (int(rate), int(channels))
            if fmt != self._format:
                self._sounds.clear()
                self._format = fmt
            if freq in self._sounds:
                return self._sounds[freq]
            data = click_samples(freq, rate, channels)
            if channels == 1:
                data = data.reshape(-1, 1)
            sound = pygame.sndarray.make_sound(np.ascontiguousarray(data))
        except Exception:
            self._failed = True
            return None
        self._sounds[freq] = sound
        return sound

    def play(self, mode: str) -> bool:
        """响一声（``mode`` 决定音高）；返回是否真的发声。"""
        freq = BAR_FREQ if mode == MODE_BAR else BEAT_FREQ
        sound = self._sound(freq)
        if sound is None:
            return False
        try:  # pragma: no cover - 依赖音频设备
            sound.play()
        except Exception:
            return False
        return True

    @property
    def available(self) -> bool:
        return not self._failed

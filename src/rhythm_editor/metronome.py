"""播放音效：节拍音（打拍器）与打击音效。

* :func:`clicks_between` —— 纯函数，算出这一段时间里应该响几声（便于单测）；
* :func:`hits_between`  —— 算出这段时间里有哪些物件正好到达判定线（打击音）；
* :class:`Metronome`    —— 用 pygame 合成音效并播放（正弦“滴”声 / 白噪声打击声），
  缺依赖时静默降级。

声音是**独立于歌曲**的（pygame 的 Sound 走单独通道），所以开着音效不影响播放；
音量由「编辑器设置 → 音量」分别控制（音乐 / 节拍音 / 打击音）。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional

from . import config

if TYPE_CHECKING:  # 只用于类型标注，避免和 model 包循环导入
    from .model.chart import Chart
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
# 打击音：白噪声（与节拍音同长度、同包络、同基准音量）
HIT_NOISE_SEED = 20240929


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


def hit_samples(sample_rate: int = SAMPLE_RATE, channels: int = 1):
    """合成一声“打击音”：**白噪声**，长度 / 包络 / 基准音量都与节拍音一致。"""
    import numpy as np

    total = max(1, int(sample_rate * CLICK_MS / 1000.0))
    fade = max(1, int(sample_rate * FADE_MS / 1000.0))
    rng = np.random.default_rng(HIT_NOISE_SEED)
    wave = rng.uniform(-1.0, 1.0, size=total).astype(np.float32)
    tail = np.ones(total, dtype=np.float32)
    fade_len = min(fade, total)
    tail[total - fade_len:] = np.linspace(1.0, 0.0, fade_len, dtype=np.float32)
    data = (wave * tail * 32767.0 * VOLUME).astype(np.int16)
    if channels > 1:
        data = np.repeat(data.reshape(-1, 1), channels, axis=1)
    return np.ascontiguousarray(data)


def hits_between(chart: "Chart", prev_beat: float, now_beat: float) -> List[str]:
    """返回 ``(prev_beat, now_beat]`` 之间**到达判定线**的物件 id。

    * 按键 / 多轨按键 / 滑键 / 鼠标点击按键：判定时刻就是它们的 ``beat``；
    * 长按、空域：取**开头**（``beat_start``）；
    * 一次 tick 最多返回一个（同一拍的和弦只响一声，避免叠成噪音）；
      倒放或跳跃（> ``METRONOME_MAX_JUMP_BEATS`` 拍）不补发。
    """
    prev = float(prev_beat)
    now = float(now_beat)
    if now <= prev or now - prev > config.METRONOME_MAX_JUMP_BEATS:
        return []
    for note in chart.sorted_notes():          # 已按 (head_beat, tail_beat, id) 排序
        head = float(note.head_beat)
        if head <= prev:
            continue
        if head <= now + 1e-9:
            return [note.id]
        break
    return []


class Metronome:
    """合成并播放音效（没有 pygame / 没有设备时静默失效）。"""

    def __init__(self) -> None:
        self._sounds: dict = {}
        self._failed = False
        self._format: Optional[tuple] = None
        self.volume = 1.0          # 界面上可调（0..1），乘在基准音量之上
        self.enabled = True

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, float(volume)))
        for sound in self._sounds.values():
            try:
                sound.set_volume(self.volume)
            except Exception:  # pragma: no cover
                pass

    def set_enabled(self, enabled: bool) -> None:
        """临时开关（关闭后不再合成 / 播放，音量设置保留）。"""
        self.enabled = bool(enabled)

    def _sound(self, key, factory):
        if self._failed or not self.enabled:
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
            if key in self._sounds:
                return self._sounds[key]
            data = factory(rate, channels)
            if channels == 1:
                data = data.reshape(-1, 1)
            sound = pygame.sndarray.make_sound(np.ascontiguousarray(data))
        except Exception:
            self._failed = True
            return None
        try:
            sound.set_volume(self.volume)
        except Exception:  # pragma: no cover
            pass
        self._sounds[key] = sound
        return sound

    def play(self, mode: str) -> bool:
        """响一声（``mode`` 决定音高）；返回是否真的发声。"""
        freq = BAR_FREQ if mode == MODE_BAR else BEAT_FREQ
        sound = self._sound(freq, lambda rate, ch: click_samples(freq, rate, ch))
        if sound is None:
            return False
        try:  # pragma: no cover - 依赖音频设备
            sound.play()
        except Exception:
            return False
        return True

    def play_hit(self) -> bool:
        """打击音效：物件到达判定线时响一声白噪声。"""
        sound = self._sound("hit", lambda rate, ch: hit_samples(rate, ch))
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

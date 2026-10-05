"""播放音效：节拍音（打拍器）与打击音效。

* :func:`effect_events` —— 纯函数，**预先算出整条时间轴上所有音效的时刻**
  （歌曲毫秒），播放时由音频层直接混进输出流，无需逐帧判断；
* :func:`effect_samples` —— 按种类合成波形（正弦“滴”声 / 白噪声打击声）。

走的是“预计算 + 混音”：音效在音频窗口合成时按时刻叠加，所以极高 BPM / 掉帧
也不会漏响、迟到；音效各自带音量（音乐 / 节拍音 / 打击音三个音量互相独立）。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:  # 只用于类型标注，避免和 model 包循环导入
    from model.chart import Chart

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


def effect_samples(kind: str, sample_rate: int = SAMPLE_RATE, channels: int = 1):
    """按音效种类合成波形（供音频层直接混进输出）。"""
    if kind == "hit":
        return hit_samples(sample_rate, channels)
    freq = BAR_FREQ if kind == "bar" else BEAT_FREQ
    return click_samples(freq, sample_rate, channels)


def _chart_end_ms(chart: "Chart") -> float:
    """谱面最后一个物件的结束时刻（歌曲毫秒；没有物件时为 0）。"""
    end_beat = 0.0
    for note in chart.all_notes():
        end_beat = max(end_beat, float(note.tail_beat))
    return chart.timing.beat_to_song_ms(end_beat)


def effect_events(
    chart: "Chart",
    mode: str = MODE_OFF,
    hit_enabled: bool = True,
    until_ms: Optional[float] = None,
) -> List[tuple]:
    """预先算出整条时间轴上所有音效的**歌曲毫秒时刻**。

    * 节拍音：``MODE_BAR`` 取小节线（BPM 变更处一定有），``MODE_BEAT`` 取整数拍；
    * 打击音：每个物件的**开头判定时刻**（长按 / 空域取开头），
      同一时刻的和弦只响一声；
    * 返回按时间排序的 ``[(song_ms, kind)]``，``kind`` ∈ ``bar`` / ``beat`` / ``hit``。

    音频层按这张表把音效混进输出流；起播位置之前的条目不会被混（等于
    “跳转 / 暂停恢复不补发一串滴声”）。
    """
    timing = chart.timing
    events: List[tuple] = []
    end_ms = float(until_ms) if until_ms else _chart_end_ms(chart)
    if mode in (MODE_BAR, MODE_BEAT):
        end_beat = timing.song_ms_to_beat(end_ms)
        if mode == MODE_BAR:
            for beat in timing.bar_lines(0.0, end_beat):
                events.append((timing.beat_to_song_ms(float(beat)), "bar"))
        else:
            last = int(math.floor(end_beat)) + 1
            for beat in range(0, max(0, last)):
                events.append((timing.beat_to_song_ms(float(beat)), "beat"))
    if hit_enabled:
        previous_ms: Optional[float] = None
        for note in chart.sorted_notes():      # 已按 (head_beat, tail_beat, id) 排序
            ms = timing.beat_to_song_ms(note.head_beat)
            if ms > end_ms:
                break
            if previous_ms is not None and abs(ms - previous_ms) < 1e-6:
                continue                       # 同一拍的和弦只响一声
            events.append((ms, "hit"))
            previous_ms = ms
    events.sort(key=lambda item: (item[0], item[1]))
    return events

"""音频播放后端与倍速（变速不变调）。

后端
----
* ``PygameBackend``            —— ``pygame.mixer.music`` 流式播放（倍速 = 1.0x 时使用）
* ``StretchedSoundBackend``    —— 用 Hann 窗重叠相加（OLA）做**时间伸缩**，
  播放拉伸后的 PCM（倍速 < 1.0x 时使用，音调不变）
* ``SilentBackend``            —— 静音时钟（没有 pygame / 没有音频文件时的兜底）

所有后端的 ``position_ms()`` 都返回**歌曲原始时间轴**上的位置，
因此上层（谱面/判定线）的换算完全不用区分后端。

延迟补偿
--------
``audio_latency_ms`` 表示“软件执行播放操作 -> 电脑硬件放出声音”的间隔。
播放时用 ``后端位置 - 延迟`` 作为“耳朵听到的位置”，再换算成判定线所在拍。
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional, Tuple

from . import config


def _fmt_rate(rate: float) -> str:
    return f"{rate:.1f}x"


_PYGAME = None
_PYGAME_ERROR: Optional[str] = None
_MIXER_ERROR: Optional[str] = None
"""pygame 装了但音频设备打不开时的原因（用于区分解释器问题与设备问题）。"""
try:  # pragma: no cover - 依赖运行环境
    import pygame  # type: ignore

    _PYGAME = pygame
except Exception as exc:  # pragma: no cover
    _PYGAME_ERROR = str(exc)
    _PYGAME = None

_NP = None
try:  # pragma: no cover - 依赖运行环境
    import numpy as _numpy  # type: ignore

    _NP = _numpy
except Exception:  # pragma: no cover
    _NP = None

STRETCH_AVAILABLE = _PYGAME is not None and _NP is not None

# 时间伸缩参数
STRETCH_FRAME = 2048          # 每帧样本数（约 46ms @44.1kHz）
STRETCH_OVERLAP = 4           # 75% 重叠
STRETCH_WINDOW_MS = 20_000.0  # 一次处理 20 秒原始音频（控制内存）
STRETCH_QUEUE_AHEAD_MS = 5_000.0


def audio_backend_name() -> str:
    if _PYGAME is None:
        return "silent"
    if STRETCH_AVAILABLE:
        return "pygame.mixer + OLA"
    return "pygame.mixer"


def audio_setup_hint() -> str:
    """音频不可用时给用户一条能照着做的提示（正常情况返回空串）。"""
    if _PYGAME is None:
        detail = f"（{_PYGAME_ERROR}）" if _PYGAME_ERROR else ""
        return (
            f"未检测到 pygame{detail}：用 run_editor.bat 启动（会自动使用项目里的 .venv），"
            f"或执行 pip install -r requirements.txt；"
            f"当前解释器：{sys.executable}"
        )
    if _MIXER_ERROR:
        return (
            f"pygame 已安装，但音频设备打不开（{_MIXER_ERROR}）："
            f"检查系统输出设备 / 关掉独占声卡的软件后重试，"
            f"或在「编辑器设置 → 重新初始化音频设备」重试；"
            f"当前解释器：{sys.executable}"
        )
    return ""


def audio_backend_state() -> str:
    """音频状态：``ok`` / ``missing``（没 pygame）/ ``device``（设备打不开）。"""
    if _PYGAME is None:
        return "missing"
    if _MIXER_ERROR:
        return "device"
    return "ok"


def _silent_reason() -> str:
    """静音回退的原因（给人看的一句话）。"""
    state = audio_backend_state()
    if state == "missing":
        return "未安装 pygame"
    if state == "device":
        return "音频设备不可用"
    return "未使用音频后端"


def audio_silent_reason() -> str:
    """对外：静音时的一句话原因（可用时为空串）。"""
    return "" if audio_backend_state() == "ok" else _silent_reason()


def write_startup_log() -> str:
    """把音频自检写进 ``_diag/audio_startup.log``（排障用，失败也不影响运行）。

    内容：时间、解释器、pygame / numpy 状态、失败原因、最终后端。
    启动时都会写一次，用户报“没有声音”时直接看这个文件即可。
    """
    import datetime

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    target = os.path.join(root, "_diag", "audio_startup.log")
    lines = [
        f"----- {datetime.datetime.now().isoformat(timespec='seconds')} -----",
        f"解释器：{sys.executable}",
        f"Python：{sys.version.split()[0]}",
        f"PYTHONHOME：{os.environ.get('PYTHONHOME', '(未设置)')}",
        f"PYTHONPATH：{os.environ.get('PYTHONPATH', '(未设置)')}",
        f"pygame：{'已安装 ' + _PYGAME.version.ver if _PYGAME is not None else '未安装'}"
        + (f"（导入失败：{_PYGAME_ERROR}）" if _PYGAME_ERROR else ""),
        f"numpy：{'已安装' if _NP is not None else '未安装'}",
        f"音频设备：{_MIXER_ERROR or '正常'}",
        f"状态：{audio_backend_state()} · 后端：{audio_backend_name()}",
        "",
    ]
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError:  # pragma: no cover - 只读目录等
        pass
    return target


def audio_backend_available() -> bool:
    return _PYGAME is not None


def time_stretch(
    samples,
    rate: float,
    frame: int = STRETCH_FRAME,
    overlap: int = STRETCH_OVERLAP,
    search: int = 64,
    probe: int = 256,
):
    """把 PCM 样本按 ``rate`` 伸缩（rate < 1 变慢），**音调不变**。

    采用 WSOLA（波形相似重叠相加）：输出每次前进 ``hop``、输入每次前进 ``hop * rate``，
    并在 ``±search`` 范围内搜索“与上一帧自然延续最相似”的位置，再重叠相加。
    搜索保证帧与帧之间相位连贯（否则会出现周期性的相位跳变杂音），
    而播放速率始终是原速，因此**频率（音调）不变**。
    """
    if _NP is None:  # pragma: no cover - 无 numpy 时直接原样返回
        return samples
    np = _NP
    rate = max(0.05, float(rate))
    if abs(rate - 1.0) < 1e-6:
        return samples
    data = samples
    squeeze = False
    if data.ndim == 1:
        data = data.reshape(-1, 1)
        squeeze = True
    channels = data.shape[1]
    total = data.shape[0]
    if total <= frame:
        return samples
    hop_out = max(1, frame // max(1, overlap))
    hop_in = hop_out * rate
    n_frames = int((total - frame) / hop_in) + 1
    out_len = n_frames * hop_out + frame
    out = np.zeros((out_len, channels), dtype=np.float32)
    norm = np.zeros((out_len, 1), dtype=np.float32)
    window = np.hanning(frame + 1)[:frame].astype(np.float32)
    probe = max(32, min(int(probe), frame - hop_out))
    pos = 0.0
    previous_start = 0
    for index in range(n_frames):
        nominal = int(pos)
        start = nominal
        if index > 0:
            max_start = total - frame
            target_start = min(previous_start + hop_out, max_start)
            target = data[target_start:target_start + probe].astype(np.float32)
            if target.shape[0] == probe:
                low = max(0, nominal - search)
                high = min(max_start, nominal + search)
                if high > low:
                    window_view = np.lib.stride_tricks.sliding_window_view(
                        data[low:high + probe], probe, axis=0
                    )
                    candidates = window_view.astype(np.float32)
                    # 归一化互相关（避免选到能量更大的无关片段）
                    target_norm = float(np.linalg.norm(target)) + 1e-6
                    candidate_norm = np.linalg.norm(candidates, axis=1) + 1e-6
                    scores = (candidates @ target) / (candidate_norm * target_norm)
                    start = low + int(np.argmax(scores))
            start = max(0, min(max_start, start))
        segment = data[start:start + frame]
        if segment.shape[0] < frame:  # pragma: no cover - 收尾
            break
        offset = index * hop_out
        out[offset:offset + frame] += segment.astype(np.float32) * window[:, None]
        norm[offset:offset + frame, 0] += window
        previous_start = start
        pos += hop_in
    np.maximum(norm, 1e-3, out=norm)
    out /= norm
    result = out[:, 0] if squeeze else out
    if samples.dtype == np.int16:
        return np.clip(result, -32768, 32767).astype(np.int16)
    return result.astype(samples.dtype)


class SilentBackend:
    """不发声的时钟后端：保证没有音频依赖时编辑器依然可用（支持倍速）。"""

    name = "silent"
    supports_seek = True

    def __init__(self) -> None:
        self._start = 0.0
        self._base_ms = 0.0
        self._playing = False
        self._length_ms = 300_000.0
        self.rate = 1.0

    def load(self, path: str) -> bool:
        self._length_ms = max(60_000.0, 300_000.0)
        return True

    def unload(self) -> None:
        self.stop()

    def set_rate(self, rate: float) -> None:
        # 变速时保持“当前位置”不变
        self._base_ms = self.position_ms()
        self._start = time.perf_counter()
        self.rate = max(0.05, float(rate))

    def play(self, start_ms: float) -> None:
        self._base_ms = float(start_ms)
        self._start = time.perf_counter()
        self._playing = True

    def stop(self) -> None:
        self._playing = False
        self._base_ms = 0.0

    def pause(self) -> None:
        if self._playing:
            self._base_ms = self.position_ms()
        self._playing = False

    def position_ms(self) -> float:
        if self._playing:
            return self._base_ms + (time.perf_counter() - self._start) * 1000.0 * self.rate
        return self._base_ms

    def poll(self) -> None:
        return

    def set_volume(self, volume: float) -> None:
        return

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def length_ms(self) -> float:
        return self._length_ms


def _ensure_mixer() -> Tuple[int, int, int]:
    """确保 mixer 用固定格式初始化（sndarray 需要已知格式）。"""
    assert _PYGAME is not None
    info = _PYGAME.mixer.get_init()
    if info is None:
        try:
            _PYGAME.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        except Exception:
            _PYGAME.mixer.quit()
            _PYGAME.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
        info = _PYGAME.mixer.get_init()
    return int(info[0]), int(info[1]), int(info[2])


class PygameBackend:  # pragma: no cover - 依赖 pygame
    """流式播放后端（倍速 1.0x）。"""

    name = "pygame.mixer"
    supports_seek = True

    def __init__(self) -> None:
        assert _PYGAME is not None
        _ensure_mixer()
        self._loaded: Optional[str] = None
        self._volume = 1.0
        self._seek_base_ms = 0.0
        self.rate = 1.0

    def load(self, path: str) -> bool:
        if not os.path.isfile(path):
            return False
        _PYGAME.mixer.music.load(path)
        self._loaded = path
        return True

    def unload(self) -> None:
        try:
            _PYGAME.mixer.music.unload()
        except Exception:
            pass
        self._loaded = None

    def set_rate(self, rate: float) -> None:
        # 流式后端不支持变速；倍速由 StretchedSoundBackend 负责
        self._seek_base_ms = self.position_ms()
        self.rate = max(0.05, float(rate))

    def play(self, start_ms: float) -> None:
        self._seek_base_ms = max(0.0, float(start_ms))
        _PYGAME.mixer.music.play(start=self._seek_base_ms / 1000.0)

    def stop(self) -> None:
        _PYGAME.mixer.music.stop()

    def pause(self) -> None:
        _PYGAME.mixer.music.pause()

    def poll(self) -> None:
        return

    def position_ms(self) -> float:
        pos = _PYGAME.mixer.music.get_pos()
        if pos is None or pos < 0:
            return self._seek_base_ms
        return self._seek_base_ms + float(pos)

    def set_volume(self, volume: float) -> None:
        self._volume = float(volume)
        _PYGAME.mixer.music.set_volume(max(0.0, min(1.0, float(volume))))

    @property
    def is_playing(self) -> bool:
        try:
            return bool(_PYGAME.mixer.music.get_busy())
        except Exception:
            return False

    @property
    def length_ms(self) -> float:
        return 0.0


class StretchedSoundBackend:  # pragma: no cover - 依赖 pygame + numpy
    """时间伸缩播放后端：把 PCM 分窗口拉伸（音调不变）后用 Sound 播放。"""

    name = "pygame+OLA"
    supports_seek = True

    def __init__(self) -> None:
        assert _PYGAME is not None and _NP is not None
        self.freq, self.size, self.channels = _ensure_mixer()
        self._samples = None
        self._loaded: Optional[str] = None
        self._volume = 1.0
        self.rate = 1.0
        self._channel = None
        self._windows: List[Tuple[object, float, float]] = []
        self._playing = False
        self._start_source_ms = 0.0
        self._start_time = 0.0
        self._paused_ms = 0.0
        self._length_ms = 0.0

    # ------------------------------------------------------------------ 载入
    def load(self, path: str) -> bool:
        if not os.path.isfile(path):
            return False
        sound = _PYGAME.mixer.Sound(path)
        array = _PYGAME.sndarray.array(sound)
        self._samples = _NP.ascontiguousarray(array)
        self._loaded = path
        self._length_ms = float(self._samples.shape[0]) / self.freq * 1000.0
        self._windows.clear()
        return True

    def unload(self) -> None:
        self.stop()
        self._samples = None
        self._loaded = None
        self._windows.clear()

    def set_rate(self, rate: float) -> None:
        self._paused_ms = self.position_ms()
        self.rate = max(0.05, float(rate))
        self._windows.clear()

    # ------------------------------------------------------------------ 播放
    def _make_window(self, start_ms: float):
        if self._samples is None:
            return None
        start = max(0, int(start_ms / 1000.0 * self.freq))
        stop = min(
            int(self._samples.shape[0]),
            start + int(STRETCH_WINDOW_MS / 1000.0 * self.freq),
        )
        if stop - start < STRETCH_FRAME * 2:
            return None
        chunk = self._samples[start:stop]
        stretched = time_stretch(chunk, self.rate)
        sound = _PYGAME.sndarray.make_sound(_NP.ascontiguousarray(stretched))
        sound.set_volume(max(0.0, min(1.0, self._volume)))
        return sound, start / self.freq * 1000.0, stop / self.freq * 1000.0

    def play(self, start_ms: float) -> None:
        if self._samples is None:
            return
        self.stop()
        first = self._make_window(start_ms)
        if first is None:
            return
        self._channel = _PYGAME.mixer.Channel(0)
        self._channel.play(first[0])
        self._windows = [first]
        self._start_source_ms = max(0.0, float(start_ms))
        self._start_time = time.perf_counter()
        self._playing = True

    def poll(self) -> None:
        """播放过程中提前准备下一个窗口并排队，保证连续播放。"""
        if not self._playing or self._channel is None or not self._windows:
            return
        position = self.position_ms()
        last = self._windows[-1]
        if position > last[1]:
            # 已经进入后面的窗口，丢掉前面的
            self._windows = [w for w in self._windows if w[2] > position]
            last = self._windows[-1] if self._windows else last
        if last[2] - position <= STRETCH_QUEUE_AHEAD_MS:
            nxt = self._make_window(last[2])
            if nxt is not None and (not self._windows or self._windows[-1][2] < nxt[2]):
                self._channel.queue(nxt[0])
                self._windows.append(nxt)

    def stop(self) -> None:
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:
                pass
        self._channel = None
        self._windows.clear()
        self._playing = False
        self._paused_ms = 0.0

    def pause(self) -> None:
        self._paused_ms = self.position_ms()
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:
                pass
        self._channel = None
        self._windows.clear()
        self._playing = False

    def position_ms(self) -> float:
        if not self._playing:
            return self._paused_ms
        elapsed = (time.perf_counter() - self._start_time) * 1000.0 * self.rate
        return min(self._start_source_ms + elapsed, self._length_ms)

    def set_volume(self, volume: float) -> None:
        self._volume = float(volume)
        for window in self._windows:
            try:
                window[0].set_volume(max(0.0, min(1.0, self._volume)))
            except Exception:
                pass

    @property
    def is_playing(self) -> bool:
        if not self._playing:
            return False
        if self._channel is None:
            return False
        try:
            return bool(self._channel.get_busy())
        except Exception:
            return self._playing

    @property
    def length_ms(self) -> float:
        return self._length_ms


class AudioPlayer:
    """对上层暴露的音频接口：以“歌曲原始毫秒位置”为单位。"""

    def __init__(self) -> None:
        self.rate = config.DEFAULT_RATE
        self.path: Optional[str] = None
        self.latency_ms = 0.0
        self._playing = False
        self._paused_at_ms = 0.0
        # 没有音频文件时用静音时钟推进（pygame.music 没有载入文件时不会走时间）
        self._silent_clock = SilentBackend()
        self._plain_backend = self._create_plain_backend()
        self._stretch_backend = None
        self.backend = self._plain_backend

    # ------------------------------------------------------------------ 后端
    @staticmethod
    def _create_plain_backend():
        global _MIXER_ERROR
        if _PYGAME is not None:
            try:
                backend = PygameBackend()
            except Exception as exc:  # pragma: no cover - 依赖音频设备
                _MIXER_ERROR = f"{type(exc).__name__}: {exc}".strip()
            else:
                _MIXER_ERROR = None
                return backend
        return SilentBackend()

    def _select_backend(self):
        want_stretch = self.rate < config.RATE_MAX - 1e-9
        if want_stretch and STRETCH_AVAILABLE:
            if self._stretch_backend is None:
                try:
                    self._stretch_backend = StretchedSoundBackend()
                except Exception:  # pragma: no cover - 无音频设备
                    self._stretch_backend = None
            if self._stretch_backend is not None:
                return self._stretch_backend
        return self._plain_backend

    @property
    def stretch_active(self) -> bool:
        return isinstance(self.backend, StretchedSoundBackend)

    # ------------------------------------------------------------------ 属性
    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def usable_audio(self) -> bool:
        return self.backend.name != "silent"

    @property
    def status_text(self) -> str:
        if self.path is None:
            if not self.usable_audio:
                if audio_backend_state() == "ok":
                    return (
                        f"音频后端：{audio_backend_name()}"
                        f"（未载入歌曲，播放为静音时钟）· {_fmt_rate(self.rate)}"
                    )
                return f"音频后端：silent（{_silent_reason()}，静音）· {_fmt_rate(self.rate)}"
            return f"音频后端：{self.backend.name}（未载入歌曲）· {_fmt_rate(self.rate)}"
        base = os.path.basename(self.path)
        if not self.usable_audio:
            return f"音频后端：silent（{_silent_reason()}，静音播放）· {base} · {_fmt_rate(self.rate)}"
        tone = "· 不变调" if self.stretch_active else ""
        return f"音频后端：{self.backend.name} · {base} · {_fmt_rate(self.rate)}{tone}"

    # ------------------------------------------------------------------ 载入
    def load(self, path: Optional[str]) -> bool:
        self.stop()
        self.path = None
        self.backend = self._select_backend()
        if not path or not os.path.isfile(path):
            # 未载入歌曲：用静音时钟，播放时判定线照常前进（不会一点播放就立刻“结束”）
            self.backend = self._silent_clock
            self.backend.set_rate(self.rate)
            return False
        try:
            ok = self.backend.load(path)
        except Exception:
            ok = False
        if not ok and self.backend is not self._plain_backend:
            # 拉伸后端失败时退回普通后端
            self.backend = self._plain_backend
            try:
                ok = self.backend.load(path)
            except Exception:
                ok = False
        if ok:
            self.path = path
            self.backend.set_rate(self.rate)
        return ok

    def unload(self) -> None:
        """停止并释放当前音频文件（Windows 下会解除文件占用）。"""
        self.stop()
        for backend in (self._plain_backend, self._stretch_backend):
            if backend is None:
                continue
            try:
                backend.unload()
            except Exception:
                pass
        self.path = None

    def retry_backend(self) -> bool:
        """重新初始化音频设备（插好耳机 / 关掉独占声卡的软件后可以重试）。

        返回重试后是否可用；原来载入的歌曲会自动重新载入。
        """
        path = self.path
        rate = self.rate
        self.unload()
        self._plain_backend = self._create_plain_backend()
        self._stretch_backend = None
        self.backend = self._plain_backend
        self.rate = rate
        if path:
            return self.load(path)
        return self.usable_audio

    # ------------------------------------------------------------------ 倍速
    def set_rate(self, rate: float) -> None:
        """设置播放倍速（0.1 ~ 1.0x，步进 0.1），变速不变调。"""
        rate = round(max(config.RATE_MIN, min(config.RATE_MAX, float(rate))), 2)
        if abs(rate - self.rate) < 1e-9:
            return
        was_playing = self._playing
        position = self.position_ms()
        self.rate = rate
        backend = self._select_backend()
        if backend is not self.backend:
            self.backend = backend
            if self.path:
                try:
                    if not backend.load(self.path):  # pragma: no cover
                        self.backend = self._plain_backend
                except Exception:  # pragma: no cover
                    self.backend = self._plain_backend
        try:
            self.backend.set_rate(self.rate)
        except Exception:  # pragma: no cover
            pass
        self._paused_at_ms = position
        self._playing = False
        if was_playing and self.path:
            self.play(position)

    # ------------------------------------------------------------------ 控制
    def play(self, song_ms: float) -> None:
        start = max(0.0, float(song_ms))
        try:
            self.backend.play(start)
        except Exception:  # pragma: no cover - 后端异常时退回静音时钟
            self.backend = SilentBackend()
            self.backend.set_rate(self.rate)
            self.backend.play(start)
        self._playing = True
        self._paused_at_ms = start

    def pause(self) -> float:
        """暂停并返回当前歌曲位置（毫秒）。"""
        position = self.position_ms()
        try:
            self.backend.pause()
        except Exception:
            pass
        self._playing = False
        self._paused_at_ms = position
        return position

    def stop(self) -> None:
        try:
            self.backend.stop()
        except Exception:
            pass
        self._playing = False
        self._paused_at_ms = 0.0

    def poll(self) -> None:
        """每帧调用：拉伸后端用它提前准备后续窗口。"""
        try:
            self.backend.poll()
        except Exception:  # pragma: no cover
            pass

    def set_volume(self, volume: float) -> None:
        try:
            self.backend.set_volume(volume)
        except Exception:
            pass

    def seek_ms(self, song_ms: float) -> None:
        """把播放位置移动到指定毫秒处（暂停状态下也生效）。"""
        position = max(0.0, float(song_ms))
        was_playing = self._playing
        self._paused_at_ms = position
        if was_playing:
            self.play(position)

    # ------------------------------------------------------------------ 位置
    def position_ms(self) -> float:
        if not self._playing:
            return self._paused_at_ms
        try:
            raw = float(self.backend.position_ms())
        except Exception:
            raw = self._paused_at_ms
        return max(0.0, raw)

    def heard_position_ms(self) -> float:
        """耳朵实际听到的位置（考虑系统音频延迟）。"""
        position = self.position_ms()
        if self.usable_audio:
            position -= self.latency_ms
        return max(0.0, position)

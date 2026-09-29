"""音频播放后端与倍速（变速变调：慢放会同时降低音调）。

后端
----
* ``PygameBackend``            —— ``pygame.mixer.music`` 流式播放（倍速 = 1.0x 时使用）
* ``StretchedSoundBackend``    —— 用**线性插值重采样**把样本拉长后播放
  （倍速 < 1.0x 时使用；慢放 = 降采样，所以音调随之变低，等价于“降低采样率”）
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
# 一次伸缩处理多少**输出**音频（原始窗口 = 这个值 × rate）：
# 太小会频繁合成、太大则一次合成就要几百毫秒，导致画面/声音卡顿
STRETCH_TARGET_OUTPUT_MS = 4_000.0
STRETCH_WINDOW_MIN_MS = 400.0
STRETCH_WINDOW_MAX_MS = 6_000.0
STRETCH_QUEUE_AHEAD_MS = 8_000.0   # 原始音频还剩这么多毫秒就去准备下一个窗口
MAX_QUEUED_WINDOWS = 4             # 通道里最多排几个窗口（每个 ≈ 4 秒输出）


def audio_backend_name() -> str:
    if _PYGAME is None:
        return "silent"
    if STRETCH_AVAILABLE:
        return "pygame.mixer + resample"
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
    """把 PCM 样本按 1/rate 拉长（rate < 1 变慢、音调随之变低）。

    实现是线性插值重采样 —— 等价于“把采样率按 rate 倍降低后再按原速播放”：
    慢放 0.5x 时长度翻倍、频率减半。比 WSOLA 便宜得多（一次 O(n) 查表），
    单个窗口的合成开销从几十毫秒降到几毫秒，也不再需要相似度搜索。
    """
    if _NP is None:  # pragma: no cover - 无 numpy 时直接原样返回
        return samples
    np = _NP
    rate = max(0.05, float(rate))
    if abs(rate - 1.0) < 1e-6:
        return samples
    data = np.asarray(samples)
    squeeze = False
    if data.ndim == 1:
        data = data.reshape(-1, 1)
        squeeze = True
    total = data.shape[0]
    if total < 2:
        return samples
    # 采样位置 = 0, rate, 2*rate, ...（rate<1 时样本被拉开 -> 变慢变低）
    out_len = max(1, int(round(total / rate)))
    index = np.arange(out_len, dtype=np.float32) * rate
    low = np.floor(index).astype(np.int64)
    frac = (index - low)[:, None].astype(np.float32)
    np.clip(low, 0, total - 2, out=low)
    left = data[low].astype(np.float32)
    right = data[low + 1].astype(np.float32)
    out = left + (right - left) * frac
    result = out[:, 0] if squeeze else out
    if data.dtype == np.int16:
        return np.clip(result, -32768, 32767).astype(np.int16)
    return result.astype(data.dtype)


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
        try:
            # 0 号通道留给歌曲（拉伸后端固定用它），节拍音等自动选空闲通道，
            # 避免“滴”声把正在播放的歌曲顶掉
            _PYGAME.mixer.set_reserved(1)
        except Exception:  # pragma: no cover - 老版本 pygame
            pass
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

    name = "pygame+resample"
    supports_seek = True

    def __init__(self) -> None:
        assert _PYGAME is not None and _NP is not None
        self.freq, self.size, self.channels = _ensure_mixer()
        self._samples = None
        self._loaded: Optional[str] = None
        self._volume = 1.0
        self.rate = 1.0
        self._channel = None
        # 播放窗口队列 + 下一个窗口的源起点（严格单调，见 poll）
        self._queue: List[Tuple[object, float, float]] = []
        self._next_source_ms = 0.0
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
        self._queue.clear()
        self._next_source_ms = 0.0
        return True

    def unload(self) -> None:
        self.stop()
        self._samples = None
        self._loaded = None
        self._queue.clear()
        self._next_source_ms = 0.0

    def set_rate(self, rate: float) -> None:
        self._paused_ms = self.position_ms()
        self.rate = max(0.05, float(rate))
        self._queue.clear()
        self._next_source_ms = 0.0

    # ------------------------------------------------------------------ 播放
    def _make_window(self, start_ms: float):
        if self._samples is None:
            return None
        start = max(0, int(round(start_ms / 1000.0 * self.freq)))
        if start >= self._samples.shape[0]:
            return None
        # 原始窗口大小随倍速缩放：输出窗口长度大致固定，单次合成开销可控
        window_ms = min(
            STRETCH_WINDOW_MAX_MS,
            max(STRETCH_WINDOW_MIN_MS, STRETCH_TARGET_OUTPUT_MS * self.rate),
        )
        stop = min(
            int(self._samples.shape[0]),
            start + int(round(window_ms / 1000.0 * self.freq)),
        )
        if stop - start < STRETCH_FRAME:
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
        self._queue = [first]
        self._next_source_ms = first[2]          # 下一个窗口的源起点（严格单调）
        self._start_source_ms = max(0.0, float(start_ms))
        self._start_time = time.perf_counter()
        self._playing = True
        self.poll()

    def poll(self) -> None:
        """每帧调用：按**严格单调**的源位置补窗口。

        关键点：窗口起点只用 ``_next_source_ms``（上一个窗口的末端）推进，
        不再从会被裁剪的队列里反推 —— 之前位置跑得比音频快时会跳过窗口，
        或者把已经播过的窗口再排一次，听感就是片段丢失 / 重叠。
        """
        if not self._playing or self._channel is None:
            return
        self._prune()
        guard = 0
        # 一次 tick 最多补两个窗口：避免在极慢倍速下一口气合成几十秒音频卡住界面
        while guard < 2 and len(self._queue) < MAX_QUEUED_WINDOWS:
            guard += 1
            position = self.position_ms()
            if self._next_source_ms - position > STRETCH_QUEUE_AHEAD_MS:
                break
            nxt = self._make_window(self._next_source_ms)
            if nxt is None:
                break
            try:
                busy = bool(self._channel.get_busy())
            except Exception:  # pragma: no cover
                busy = True
            if busy:
                self._channel.queue(nxt[0])
            else:
                # 通道已经空了：只能直接播（对空通道 queue() 会被丢掉）
                self._channel.play(nxt[0])
                self._start_source_ms = nxt[1]
                self._start_time = time.perf_counter()
            self._queue.append(nxt)
            self._next_source_ms = nxt[2]

    def _prune(self) -> None:
        """丢掉确实已经播完的窗口（保留队尾，供音量同步等使用）。"""
        position = self.position_ms()
        while len(self._queue) > 2 and self._queue[0][2] <= position:
            self._queue.pop(0)

    def stop(self) -> None:
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:
                pass
        self._channel = None
        self._queue = []
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
        self._queue = []
        self._playing = False

    def position_ms(self) -> float:
        if not self._playing:
            return self._paused_ms
        elapsed = (time.perf_counter() - self._start_time) * 1000.0 * self.rate
        position = self._start_source_ms + elapsed
        # 不超过“已经排上去的音频”末端，否则调度会跑到音频前面
        return min(position, self._next_source_ms, self._length_ms)

    def set_volume(self, volume: float) -> None:
        self._volume = float(volume)
        for window in self._queue:
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
        # 出问题时的说明（界面会显示）
        self.last_error = ""
        # 上次改倍速时是否因为正在播放而自动暂停
        self.paused_by_rate_change = False
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
        if self.path is None:
            # 没有载入歌曲：一律用静音时钟（改倍速后也要保持，
            # 否则会切到需要样本的重采样后端，一点播放就不动了）
            return self._silent_clock
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
            text = f"音频后端：{self.backend.name}（未载入歌曲）· {_fmt_rate(self.rate)}"
            return f"{text} · {self.last_error}" if self.last_error else text
        base = os.path.basename(self.path)
        if not self.usable_audio:
            return f"音频后端：silent（{_silent_reason()}，静音播放）· {base} · {_fmt_rate(self.rate)}"
        tone = "· 变调" if self.stretch_active else ""
        text = f"音频后端：{self.backend.name} · {base} · {_fmt_rate(self.rate)}{tone}"
        return f"{text} · {self.last_error}" if self.last_error else text

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
        """设置播放倍速（0.1 ~ 1.0x，步进 0.1）：慢放通过重采样实现，音调随之变低。"""
        rate = round(max(config.RATE_MIN, min(config.RATE_MAX, float(rate))), 2)
        if abs(rate - self.rate) < 1e-9:
            return
        was_playing = self._playing
        position = self.position_ms()
        self.rate = rate
        # 切换后端前先把旧的停掉（否则被换下的后端会继续出声，听起来像“停不下来”）
        self._stop_all_backends()
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
        # 播放中改倍速会换成另一个后端（普通 ↔ 重采样），无缝接力很容易出现
        # 片段重叠 / 丢失，所以这里**一律自动暂停**，停在当前位置：
        # 按空格即可用新倍速继续（界面会提示）。
        self.paused_by_rate_change = bool(was_playing)

    # ------------------------------------------------------------------ 控制
    def play(self, song_ms: float) -> None:
        start = max(0.0, float(song_ms))
        try:
            self.backend.play(start)
        except Exception as exc:  # pragma: no cover - 后端异常
            # 拉伸合成失败时退回**普通播放**（听得见，只是不慢放），
            # 而不是直接变成静音 —— 以前这里会静默切成静音时钟，音乐就“没了”
            self._fallback_plain(f"慢放合成失败（{exc}），已回到 1.0x 播放")
            try:
                self.backend.play(start)
            except Exception:
                self.backend = SilentBackend()
                self.backend.set_rate(self.rate)
                self.backend.play(start)
        self._playing = True
        self._paused_at_ms = start

    def pause(self) -> float:
        """暂停并返回当前歌曲位置（毫秒）。"""
        position = self.position_ms()
        self._stop_all_backends()
        self._playing = False
        self._paused_at_ms = position
        return position

    def stop(self) -> None:
        self._stop_all_backends()
        self._playing = False
        self._paused_at_ms = 0.0

    def _stop_all_backends(self) -> None:
        """把**所有**后端都停掉。

        以前只停当前后端：切换倍速（拉伸后端 ↔ 普通后端）时，被换下的那个还在出声，
        听感就是“按了暂停/停止以后声音还响一会儿”。这里连同队列里的窗口一起清掉，
        并且直接把混音器里所有通道停掉，确保立刻静音。
        """
        for backend in (self.backend, self._plain_backend, self._stretch_backend):
            if backend is None:
                continue
            try:
                backend.stop()
            except Exception:
                pass
        if _PYGAME is not None:
            try:
                _PYGAME.mixer.stop()      # 连排队等待播放的窗口一起清空
            except Exception:  # pragma: no cover
                pass

    def poll(self) -> None:
        """每帧调用：拉伸后端用它提前准备后续窗口。"""
        try:
            self.backend.poll()
        except Exception as exc:  # pragma: no cover
            self._fallback_plain(f"慢放合成失败（{exc}），已回到 1.0x 播放")

    def _fallback_plain(self, reason: str) -> None:
        """拉伸后端出问题时退回普通后端（保持可听，并把原因告诉界面）。"""
        if self.backend is self._plain_backend:
            return
        was_playing = self._playing
        position = self.position_ms()
        self.rate = config.RATE_MAX
        self.backend = self._plain_backend
        if self.path:
            try:
                self._plain_backend.load(self.path)
                self._plain_backend.set_rate(1.0)
            except Exception:
                pass
        self.last_error = reason
        self._playing = False
        if was_playing and self.path:
            try:
                self.play(position)
            except Exception:
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

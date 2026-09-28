"""音频播放后端（可选依赖）与延迟补偿时钟。

* 安装了 ``pygame-ce`` / ``pygame`` 时使用 ``pygame.mixer.music``，支持 mp3/ogg/wav 等，
  并提供播放位置查询与 seek。
* 否则退化为“静音时钟”后端：播放/暂停/滚动/编辑仍然完全可用，只是没有声音。

延迟补偿
--------
``audio_latency_ms`` 表示“软件执行播放操作 -> 电脑硬件放出声音”的间隔。
播放时我们用 ``后端位置 - 延迟`` 作为“耳朵听到的位置”，再换算成判定线所在拍，
这样画面与听到的声音是对齐的。
"""

from __future__ import annotations

import os
import time
from typing import Optional

from . import config

_PYGAME = None
_PYGAME_ERROR: Optional[str] = None

try:  # pragma: no cover - 依赖运行环境
    import pygame  # type: ignore

    _PYGAME = pygame
except Exception as exc:  # pragma: no cover
    _PYGAME_ERROR = str(exc)
    _PYGAME = None


def audio_backend_name() -> str:
    return "pygame.mixer" if _PYGAME is not None else "silent"


def audio_backend_available() -> bool:
    return _PYGAME is not None


class SilentBackend:
    """不发声的时钟后端：保证没有音频依赖时编辑器依然可用。"""

    name = "silent"
    supports_seek = True

    def __init__(self) -> None:
        self._start = 0.0
        self._base_ms = 0.0
        self._playing = False
        self._length_ms = 0.0
        self._volume = 1.0

    def load(self, path: str) -> bool:
        self._length_ms = max(60_000.0, 300_000.0)
        return True

    def unload(self) -> None:
        self.stop()

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
            return self._base_ms + (time.perf_counter() - self._start) * 1000.0
        return self._base_ms

    def set_volume(self, volume: float) -> None:
        self._volume = float(volume)

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def length_ms(self) -> float:
        return self._length_ms


class PygameBackend:  # pragma: no cover - 依赖 pygame
    name = "pygame.mixer"
    supports_seek = True

    def __init__(self) -> None:
        assert _PYGAME is not None
        try:
            _PYGAME.mixer.init()
        except Exception:
            try:
                _PYGAME.mixer.quit()
                _PYGAME.mixer.init()
            except Exception:
                raise
        self._loaded: Optional[str] = None
        self._volume = 1.0
        self._seek_base_ms = 0.0

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

    def play(self, start_ms: float) -> None:
        _PYGAME.mixer.music.play(start=max(0.0, float(start_ms) / 1000.0))

    def stop(self) -> None:
        _PYGAME.mixer.music.stop()

    def pause(self) -> None:
        _PYGAME.mixer.music.pause()

    def unpause(self) -> None:
        _PYGAME.mixer.music.unpause()

    def position_ms(self) -> float:
        pos = _PYGAME.mixer.music.get_pos()
        if pos is None or pos < 0:
            return 0.0
        base = getattr(self, "_seek_base_ms", 0.0)
        return base + float(pos)

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
        try:
            sound = _PYGAME.mixer.Sound(self._loaded) if self._loaded else None
            return float(sound.get_length() * 1000.0) if sound else 0.0
        except Exception:
            return 0.0

    def play_seek(self, start_ms: float) -> None:
        self._seek_base_ms = max(0.0, float(start_ms))
        self.play(0.0)


class AudioPlayer:
    """对上层暴露的音频接口：以“歌曲毫秒位置”为单位。"""

    def __init__(self) -> None:
        self.backend = self._create_backend()
        self.path: Optional[str] = None
        self.latency_ms = 0.0
        self._playing = False
        self._paused_at_ms = 0.0

    @staticmethod
    def _create_backend():
        if _PYGAME is not None:
            try:
                return PygameBackend()
            except Exception:
                pass
        return SilentBackend()

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
            return f"音频后端：{self.backend.name}（未载入歌曲）"
        base = os.path.basename(self.path)
        if not self.usable_audio:
            return f"音频后端：silent（无 pygame，静音播放）· {base}"
        return f"音频后端：{self.backend.name} · {base}"

    # ------------------------------------------------------------------ 载入
    def load(self, path: Optional[str]) -> bool:
        self.stop()
        self.path = None
        if not path or not os.path.isfile(path):
            return False
        try:
            ok = self.backend.load(path)
        except Exception:
            ok = False
        if ok:
            self.path = path
        return ok

    def unload(self) -> None:
        """停止并释放当前音频文件（Windows 下会解除文件占用）。"""
        self.stop()
        try:
            self.backend.unload()
        except Exception:
            pass
        self.path = None

    # ------------------------------------------------------------------ 控制
    def play(self, song_ms: float) -> None:
        start = max(0.0, float(song_ms))
        try:
            if isinstance(self.backend, PygameBackend):
                self.backend.play_seek(start)
            else:
                self.backend.play(start)
        except Exception:
            self.backend = SilentBackend()
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

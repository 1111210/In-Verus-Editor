"""倍速（变速不变调）与负延迟的单元测试。"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import config
from rhythm_editor.audio import (
    STRETCH_AVAILABLE,
    AudioPlayer,
    SilentBackend,
    time_stretch,
)
from rhythm_editor.model.chart import EditorSettings


class RateSettingsTests(unittest.TestCase):
    def test_latency_allows_negative(self) -> None:
        settings = EditorSettings(audio_latency_ms=-80.0)
        settings.normalize()
        self.assertAlmostEqual(settings.audio_latency_ms, -80.0)

    def test_latency_range_clamped(self) -> None:
        settings = EditorSettings(audio_latency_ms=-99999.0)
        settings.normalize()
        self.assertAlmostEqual(settings.audio_latency_ms, config.AUDIO_LATENCY_MIN_MS)
        settings = EditorSettings(audio_latency_ms=99999.0)
        settings.normalize()
        self.assertAlmostEqual(settings.audio_latency_ms, config.AUDIO_LATENCY_MAX_MS)

    def test_rate_constants(self) -> None:
        self.assertAlmostEqual(config.RATE_MIN, 0.1)
        self.assertAlmostEqual(config.RATE_MAX, 1.0)
        self.assertAlmostEqual(config.RATE_STEP, 0.1)


class SilentClockRateTests(unittest.TestCase):
    def test_silent_clock_follows_rate(self) -> None:
        backend = SilentBackend()
        backend.set_rate(0.5)
        backend.play(1000.0)
        time.sleep(0.25)
        advanced = backend.position_ms() - 1000.0
        # 0.5x：0.25 秒真实时间应该推进约 125ms（给出宽松容差避免抖动）
        self.assertGreater(advanced, 60.0)
        self.assertLess(advanced, 220.0)
        backend.stop()

    def test_set_rate_keeps_position(self) -> None:
        backend = SilentBackend()
        backend.play(2000.0)
        time.sleep(0.05)
        before = backend.position_ms()
        backend.set_rate(0.2)
        self.assertAlmostEqual(backend.position_ms(), before, delta=60.0)
        backend.stop()


class AudioPlayerRateTests(unittest.TestCase):
    def test_rate_step_and_clamp(self) -> None:
        player = AudioPlayer()
        player.set_rate(0.7)
        self.assertAlmostEqual(player.rate, 0.7)
        player.set_rate(0.04)          # 低于下限 -> 夹到 0.1
        self.assertAlmostEqual(player.rate, 0.1)
        player.set_rate(2.0)           # 加速：>1x 也走重采样后端
        self.assertAlmostEqual(player.rate, 2.0)
        player.set_rate(9.0)           # 高于上限 -> 夹到 RATE_LIMIT_MAX = 3.0
        self.assertAlmostEqual(player.rate, config.RATE_LIMIT_MAX)
        self.assertAlmostEqual(player.rate, 3.0)
        player.set_rate(1.0)
        self.assertFalse(player.stretch_active)

    def test_heard_position_applies_signed_latency(self) -> None:
        """延迟可以为负：heard = position - latency（负延迟会加回来）。"""
        player = AudioPlayer()

        class Stub:
            name = "stub"
            supports_seek = True

            def position_ms(self) -> float:
                return 1000.0

            def poll(self) -> None:  # pragma: no cover
                return

            is_playing = True

        player.backend = Stub()
        player._playing = True
        player.latency_ms = 60.0
        self.assertAlmostEqual(player.heard_position_ms(), 940.0)
        player.latency_ms = -60.0
        self.assertAlmostEqual(player.heard_position_ms(), 1060.0)

    def test_latency_scales_with_playback_rate(self) -> None:
        """延迟是真实时间：慢放时只推进 rate 倍的歌曲时间，补偿要按倍速缩放。"""
        player = AudioPlayer()

        class Stub:
            name = "pygame.mixer"
            supports_seek = True
            is_playing = True

            def position_ms(self) -> float:
                return 1000.0

            def poll(self) -> None:  # pragma: no cover
                return

        player.backend = Stub()
        player._playing = True
        player.latency_ms = 200.0
        player.rate = 1.0
        self.assertAlmostEqual(player.heard_position_ms(), 800.0)
        player.rate = 0.5           # 慢放：真实 200ms 只相当于歌曲 100ms
        self.assertAlmostEqual(player.heard_position_ms(), 900.0)
        player.rate = 0.1
        self.assertAlmostEqual(player.heard_position_ms(), 980.0)

    def test_stretch_available_flag_matches_dependencies(self) -> None:
        # 当前环境装了 pygame-ce 与 numpy，因此变速不变调可用
        self.assertTrue(STRETCH_AVAILABLE)

    def test_rate_change_while_playing_pauses(self) -> None:
        """播放中改倍速会**自动暂停**并停在当前位置（避免换后端造成片段错乱）。"""
        player = AudioPlayer()
        player.load(None)            # 无歌曲：静音时钟也遵守同一规则
        player.play(500.0)
        time.sleep(0.1)
        before = player.position_ms()
        player.set_rate(0.5)
        self.assertFalse(player.is_playing, "改倍速后应当已暂停")
        self.assertTrue(player.paused_by_rate_change)
        self.assertAlmostEqual(player.position_ms(), before, delta=80.0)
        player.set_rate(0.8)         # 已经暂停：不再重复标记
        self.assertFalse(player.paused_by_rate_change)
        player.play(player.position_ms())
        time.sleep(0.15)
        self.assertGreater(player.position_ms(), 0.0)
        player.pause()

    def test_first_play_after_opening_uses_real_backend(self) -> None:
        """回归：刚打开谱面（还没动过倍速）时第一次播放必须有声音。

        载入后第一次播放必须走真实后端（load() 不能在选后端之前清空 self.path）。
        """
        import tempfile
        import wave

        import numpy as np

        from rhythm_editor.audio import audio_backend_state

        if audio_backend_state() != "ok":
            self.skipTest("本机音频设备不可用，跳过")
        freq = 44100
        t = np.arange(freq * 2) / freq
        tone = (np.sin(2 * np.pi * 440 * t) * 4000).astype(np.int16)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tone.wav")
            with wave.open(path, "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(freq)
                handle.writeframes(np.repeat(tone.reshape(-1, 1), 2, axis=1).tobytes())
            player = AudioPlayer()
            self.assertTrue(player.load(path))
            self.assertTrue(player.usable_audio, "载入歌曲后不能退化为静音时钟")
            self.assertNotEqual(player.backend.name, "silent")
            player.play(0.0)
            time.sleep(0.2)
            player.poll()
            self.assertTrue(player.is_playing)
            self.assertGreater(player.position_ms(), 50.0)
            player.pause()
            player.unload()


class _FakeChannel:
    """只记录 play / queue 的最小通道模型（模拟 SDL：排队的块要等当前块放完才播）。"""

    def __init__(self) -> None:
        self.playing = None
        self.pending = None
        self.replaced = []          # 被顶掉的排队块（这些内容就再也听不到了）

    def play(self, sound) -> None:
        self.playing = sound
        self.pending = None

    def queue(self, sound) -> None:
        if self.pending is not None:
            self.replaced.append(self.pending)
        self.pending = sound

    def finish(self) -> None:
        """当前块放完：排队的块开始播。"""
        self.playing = self.pending
        self.pending = None

    def stop(self) -> None:
        self.playing = None
        self.pending = None

    def get_busy(self) -> bool:
        return self.playing is not None

    def get_sound(self):
        return self.playing


class SlowBackendWindowTests(unittest.TestCase):
    def test_poll_never_replaces_a_queued_window(self) -> None:
        """慢放后端必须等排队段**真的开始播**再排下一段，否则那一段会被顶掉（漏片段）。"""
        from rhythm_editor import audio as A

        backend = A.StretchedSoundBackend.__new__(A.StretchedSoundBackend)
        channel = _FakeChannel()
        clock = [0.0]

        def make_window(start_ms: float):
            return (object(), start_ms, start_ms + 2000.0)

        backend._samples = object()
        backend._channel = None
        backend._anchor = None
        backend._queued = None
        backend._handed_end_ms = 0.0
        backend._playing = False
        backend._paused_ms = 0.0
        backend._volume = 1.0
        backend._length_ms = 60_000.0
        backend.rate = 0.5
        backend._anchor_time = 0.0
        backend._make_window = make_window

        original_perf = A.time.perf_counter
        original_channel = A._PYGAME.mixer.Channel
        A.time.perf_counter = lambda: clock[0]
        A._PYGAME.mixer.Channel = lambda index=0: channel
        try:
            backend.play(0.0)
            self.assertIs(channel.playing, backend._anchor[0])
            self.assertIs(channel.pending, backend._queued[0])
            for _ in range(4):
                clock[0] += 4.0                      # 2 秒源 × 0.5x = 4 秒输出
                channel.finish()                     # SDL：排队段开始播
                backend.poll()
                self.assertEqual(channel.replaced, [], "排队段开始播之前不能再排队")
                self.assertIsNotNone(backend._queued)
                self.assertIs(channel.pending, backend._queued[0])
                self.assertIs(channel.playing, backend._anchor[0])
            # 源时间线必须连续推进（不重不漏）
            # play() 交了第 1 段 + 排了第 2 段，之后每轮再排一段 -> 共 6 段
            self.assertAlmostEqual(backend._handed_end_ms, 2000.0 * 6, places=6)
        finally:
            A.time.perf_counter = original_perf
            A._PYGAME.mixer.Channel = original_channel


@unittest.skipUnless(STRETCH_AVAILABLE, "需要 numpy + pygame 才能做时间伸缩")
class TimeStretchTests(unittest.TestCase):
    def _tone(self, seconds: float = 1.0, freq: float = 440.0, rate: int = 44100):
        import numpy as np

        t = np.arange(int(seconds * rate)) / rate
        return (np.sin(2 * np.pi * freq * t) * 12000).astype(np.int16)

    def test_length_scales_with_rate(self) -> None:
        tone = self._tone(1.0)
        slow = time_stretch(tone, 0.5)
        fast = time_stretch(tone, 0.8)
        self.assertAlmostEqual(len(slow) / len(tone), 2.0, delta=0.05)
        self.assertAlmostEqual(len(fast) / len(tone), 1.25, delta=0.05)
        self.assertEqual(slow.dtype.name, "int16")

    def test_pitch_drops_with_rate(self) -> None:
        """变速变调（重采样）：0.5x 慢放时频率减半（过零率随之减半）。"""
        import numpy as np

        tone = self._tone(1.0, freq=440.0)

        def zero_cross_per_second(signal) -> float:
            sign = np.signbit(signal.astype(np.float32))
            crossings = int(np.count_nonzero(sign[1:] != sign[:-1]))
            seconds = len(signal) / 44100.0
            return crossings / 2.0 / seconds

        base = zero_cross_per_second(tone)
        self.assertGreater(base, 400.0)
        self.assertLess(base, 480.0)
        slowed = zero_cross_per_second(time_stretch(tone, 0.5))
        self.assertAlmostEqual(slowed, base * 0.5, delta=15.0)

    def test_rate_one_returns_input(self) -> None:
        tone = self._tone(0.2)
        same = time_stretch(tone, 1.0)
        self.assertEqual(len(same), len(tone))

    def test_stereo_music_can_be_slowed(self) -> None:
        """回归：立体声（真实音乐文件）慢放不能崩。"""
        import numpy as np

        mono = self._tone(1.0, freq=440.0)
        stereo = np.repeat(mono.reshape(-1, 1), 2, axis=1)
        slowed = time_stretch(stereo, 0.5)
        self.assertEqual(slowed.shape[1], 2, "必须保持双声道")
        self.assertAlmostEqual(slowed.shape[0] / stereo.shape[0], 2.0, delta=0.05)
        # 左右声道内容一致（同一段正弦）时，两声道应当仍然基本一致
        self.assertGreater(float(np.corrcoef(slowed[:, 0], slowed[:, 1])[0, 1]), 0.99)

    def test_windows_are_contiguous(self) -> None:
        """回归：分窗口播放必须首尾相接 —— 不能重叠、也不能漏掉一段。"""
        import numpy as np

        from rhythm_editor.audio import StretchedSoundBackend

        freq = 44100
        t = np.arange(freq * 10) / freq
        tone = (np.sin(2 * np.pi * 440 * t) * 4000).astype(np.int16)
        backend = StretchedSoundBackend()
        backend._samples = np.ascontiguousarray(np.repeat(tone.reshape(-1, 1), 2, axis=1))
        backend._length_ms = 10_000.0
        for rate in (0.9, 0.5, 0.1):
            backend.rate = rate
            cursor = 0.0
            played = 0.0
            for _ in range(30):
                window = backend._make_window(cursor)
                if window is None:
                    break
                self.assertAlmostEqual(window[1], cursor, delta=0.05, msg="窗口起点必须接上一段末端")
                played += window[0].get_length()
                cursor = window[2]
            self.assertAlmostEqual(played * 1000.0, cursor / rate, delta=5.0)

    def test_scheduler_keeps_audio_continuous(self) -> None:
        """回归：pygame 通道只认**一个**排队块，调度必须一次只排一个、连续补上。

        把窗口缩短到 0.5 秒，2 秒里跨 4 个窗口边界，检查中途不断音、
        且“已交给通道的音频末端”单调递增（既不丢段也不重复）。
        """
        import numpy as np

        from rhythm_editor import audio as audio_module
        from rhythm_editor.audio import StretchedSoundBackend

        old = (
            audio_module.STRETCH_TARGET_OUTPUT_MS,
            audio_module.STRETCH_WINDOW_MIN_MS,
            audio_module.STRETCH_WINDOW_MAX_MS,
        )
        # 每段约 2 秒输出：测试跑满负载时也不会因为“主线程被饿死”而假报断音
        audio_module.STRETCH_TARGET_OUTPUT_MS = 2000.0
        audio_module.STRETCH_WINDOW_MIN_MS = 1000.0
        audio_module.STRETCH_WINDOW_MAX_MS = 2000.0
        try:
            freq = 44100
            t = np.arange(freq * 6) / freq
            tone = (np.sin(2 * np.pi * 440 * t) * 4000).astype(np.int16)
            backend = StretchedSoundBackend()
            backend._samples = np.ascontiguousarray(np.repeat(tone.reshape(-1, 1), 2, axis=1))
            backend._length_ms = 6_000.0
            backend.rate = 0.5
            backend.play(0.0)
            duration = 4.2                       # 每段 2 秒输出，跑过至少一个窗口边界
            worst_gap = 0.0
            silent_since = None
            handed = []
            start = time.perf_counter()
            while time.perf_counter() - start < duration:
                backend.poll()
                now = time.perf_counter() - start
                if backend.is_playing:
                    if silent_since is not None:
                        worst_gap = max(worst_gap, now - silent_since)
                        silent_since = None
                elif silent_since is None:
                    silent_since = now
                handed.append(backend._handed_end_ms)
                time.sleep(0.01)
            if silent_since is not None:
                worst_gap = max(worst_gap, duration - silent_since)
            backend.stop()
            # 允许混音回调级别的小抖动（满载时可能有一两次“空一拍”），
            # 但真正的丢段是**整段**（几百毫秒以上）的静音，这里必须抓出来
            self.assertLess(worst_gap, 0.1, "窗口边界不能丢段（>100ms 的静音就是漏片段）")
            self.assertTrue(all(a <= b for a, b in zip(handed, handed[1:])), "交付末端必须单调")
            # 4.2 秒 × 0.5 倍速 ≈ 2.1 秒素材，窗口 1 秒 -> 至少跨过 1 个边界
            self.assertGreaterEqual(len(set(handed)), 2)
        finally:
            (
                audio_module.STRETCH_TARGET_OUTPUT_MS,
                audio_module.STRETCH_WINDOW_MIN_MS,
                audio_module.STRETCH_WINDOW_MAX_MS,
            ) = old


if __name__ == "__main__":
    unittest.main()

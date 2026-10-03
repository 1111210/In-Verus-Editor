"""音频：倍速档位、延迟补偿、时间伸缩与节拍 / 打击音的时刻表和混音。"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import config
from rhythm_editor import audio as A
from rhythm_editor.audio import (
    STRETCH_AVAILABLE,
    AudioPlayer,
    SilentBackend,
    time_stretch,
)
from rhythm_editor.metronome import (
    CLICK_MS,
    MODE_BAR,
    MODE_BEAT,
    MODE_OFF,
    click_samples,
    effect_events,
    effect_samples,
)
from rhythm_editor.model.chart import Chart, EditorSettings
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseClick, MouseRegion, MouseSlide
from rhythm_editor.model.timing import BpmSegment, TimingMap


class RateTests(unittest.TestCase):
    def test_fine_steps_format_and_clamp(self) -> None:
        rate = 0.2
        for expected in (0.1, 0.09):
            rate = config.step_rate(rate, -1)
            self.assertAlmostEqual(rate, expected)
        for _ in range(8):
            rate = config.step_rate(rate, -1)
        self.assertAlmostEqual(rate, config.RATE_MIN)
        self.assertAlmostEqual(config.step_rate(rate, -1), config.RATE_MIN)
        self.assertEqual(config.format_rate(0.09), "0.09x")
        self.assertEqual(config.format_rate(1.0), "1.0x")
        player = AudioPlayer()
        player.set_rate(0.04)
        self.assertAlmostEqual(player.rate, 0.04)
        player.set_rate(9.0)
        self.assertAlmostEqual(player.rate, config.RATE_LIMIT_MAX)
        player.set_rate(1.0)
        self.assertFalse(player.stretch_active)

    def test_stretch_window_bounds_output(self) -> None:
        self.assertAlmostEqual(A.stretch_window_ms(1.0), A.STRETCH_WINDOW_MAX_MS)
        self.assertAlmostEqual(A.stretch_window_ms(0.01), A.STRETCH_SOURCE_MIN_MS)
        for rate in (0.01, 0.05, 0.25, 1.0):
            self.assertLessEqual(A.stretch_window_ms(rate) / 1000.0 / rate, 30.0)

    def test_latency_is_signed_and_scales_with_rate(self) -> None:
        player = AudioPlayer()

        class Stub:
            name = "stub"
            supports_seek = True
            is_playing = True

            def position_ms(self) -> float:
                return 1000.0

            def poll(self) -> None:
                return

        player.backend = Stub()
        player._playing = True
        player.latency_ms = 60.0
        self.assertAlmostEqual(player.heard_position_ms(), 940.0)
        player.latency_ms = -60.0
        self.assertAlmostEqual(player.heard_position_ms(), 1060.0)
        player.latency_ms = 200.0
        player.rate = 0.5
        self.assertAlmostEqual(player.heard_position_ms(), 900.0)

    def test_rate_change_pauses_in_place(self) -> None:
        player = AudioPlayer()
        player.load(None)
        player.play(500.0)
        time.sleep(0.1)
        before = player.position_ms()
        player.set_rate(0.5)
        self.assertFalse(player.is_playing)
        self.assertTrue(player.paused_by_rate_change)
        self.assertAlmostEqual(player.position_ms(), before, delta=80.0)

    def test_silent_clock_follows_rate(self) -> None:
        backend = SilentBackend()
        backend.set_rate(0.5)
        backend.play(1000.0)
        time.sleep(0.25)
        advanced = backend.position_ms() - 1000.0
        self.assertGreater(advanced, 60.0)
        self.assertLess(advanced, 220.0)
        before = backend.position_ms()
        backend.set_rate(0.2)
        self.assertAlmostEqual(backend.position_ms(), before, delta=60.0)
        backend.stop()


@unittest.skipUnless(STRETCH_AVAILABLE, "需要 numpy + pygame 才能做时间伸缩")
class TimeStretchTests(unittest.TestCase):
    def _tone(self, seconds: float = 1.0, freq: float = 440.0, rate: int = 44100):
        import numpy as np

        t = np.arange(int(seconds * rate)) / rate
        return (np.sin(2 * np.pi * freq * t) * 12000).astype(np.int16)

    def test_length_pitch_and_stereo(self) -> None:
        import numpy as np

        tone = self._tone(1.0)
        self.assertEqual(len(time_stretch(tone, 1.0)), len(tone))
        slowed = time_stretch(tone, 0.5)
        self.assertAlmostEqual(len(slowed) / len(tone), 2.0, delta=0.05)

        def zero_cross_per_second(signal) -> float:
            sign = np.signbit(signal.astype(np.float32))
            crossings = int(np.count_nonzero(sign[1:] != sign[:-1]))
            return crossings / 2.0 / (len(signal) / 44100.0)

        self.assertAlmostEqual(zero_cross_per_second(slowed), zero_cross_per_second(tone) * 0.5, delta=15.0)
        stereo = np.repeat(tone.reshape(-1, 1), 2, axis=1)
        slowed_stereo = time_stretch(stereo, 0.5)
        self.assertEqual(slowed_stereo.shape[1], 2)
        self.assertGreater(float(np.corrcoef(slowed_stereo[:, 0], slowed_stereo[:, 1])[0, 1]), 0.99)

    def test_scheduler_never_drops_a_queued_window(self) -> None:
        backend = A.StretchedSoundBackend.__new__(A.StretchedSoundBackend)
        channel = _FakeChannel()
        clock = [0.0]
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
        backend._make_window = lambda start: (object(), start, start + 2000.0)
        original_perf, original_channel = A.time.perf_counter, A._PYGAME.mixer.Channel
        A.time.perf_counter = lambda: clock[0]
        A._PYGAME.mixer.Channel = lambda index=0: channel
        try:
            backend.play(0.0)
            for _ in range(4):
                clock[0] += 4.0                # 2 秒源 × 0.5x = 4 秒输出
                channel.finish()
                backend.poll()
                self.assertEqual(channel.replaced, [], "排队段开始播之前不能再排队")
            self.assertAlmostEqual(backend._handed_end_ms, 2000.0 * 6, places=6)
        finally:
            A.time.perf_counter = original_perf
            A._PYGAME.mixer.Channel = original_channel


class _FakeChannel:
    """只记录 play / queue 的最小通道模型（排队块要等当前块放完才播）。"""

    def __init__(self) -> None:
        self.playing = None
        self.pending = None
        self.replaced = []

    def play(self, sound) -> None:
        self.playing, self.pending = sound, None

    def queue(self, sound) -> None:
        if self.pending is not None:
            self.replaced.append(self.pending)
        self.pending = sound

    def finish(self) -> None:
        self.playing, self.pending = self.pending, None

    def stop(self) -> None:
        self.playing = self.pending = None

    def get_busy(self) -> bool:
        return self.playing is not None

    def get_sound(self):
        return self.playing


class EffectScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 120.0, 3)])

    def _chart(self, notes=()) -> Chart:
        chart = Chart()
        chart.timing = self.timing
        for note in notes:
            chart.add(note)
        return chart

    def test_modes_and_extreme_bpm(self) -> None:
        chart = self._chart()
        self.assertEqual(EditorSettings().metronome, MODE_OFF)
        self.assertEqual(effect_events(chart, MODE_OFF, hit_enabled=False, until_ms=5000.0), [])
        beats = effect_events(chart, MODE_BEAT, hit_enabled=False, until_ms=2000.0)
        self.assertEqual([round(t) for t, _kind in beats], [0, 500, 1000, 1500, 2000])
        bars = effect_events(chart, MODE_BAR, hit_enabled=False, until_ms=5200.0)
        self.assertEqual([round(t) for t, _kind in bars], [0, 2000, 4000, 5000])
        fast = Chart(); fast.timing = TimingMap([BpmSegment(0.0, 6000.0, 4)])
        events = effect_events(fast, MODE_BEAT, hit_enabled=False, until_ms=1000.0)
        self.assertEqual(len(events), 101, "极高 BPM 也不能漏拍")

    def test_hits_take_note_head_and_dedupe_chords(self) -> None:
        chart = self._chart([
            LaneNote("L1", 0, 0, 2.0),
            LaneNote("L2", 1, 3, 5.0, 7.0),
            MouseRegion("R1", 4.0, 6.0, BoundaryCurve(0.0, 0.0), BoundaryCurve(1.0, 1.0)),
            MouseSlide("S1", 2.0, 0.2, 0.6),
            MouseClick("C1", 8.0, 0.5, 0.2),
        ])
        events = effect_events(chart, MODE_OFF, hit_enabled=True)
        self.assertEqual([round(t) for t, _kind in events], [1000, 2000, 2500, 4000])
        self.assertTrue(all(kind == "hit" for _t, kind in events))
        self.assertEqual(events, sorted(events))

    def test_samples_and_click_length(self) -> None:
        import numpy as np

        bar = effect_samples("bar", 44100, 1).astype(np.float32)
        beat = effect_samples("beat", 44100, 1).astype(np.float32)
        hit = effect_samples("hit", 44100, 1).astype(np.float32)
        for data in (bar, beat, hit):
            self.assertAlmostEqual(len(data) / 44100 * 1000.0, CLICK_MS, delta=1.0)
        self.assertFalse(np.array_equal(bar, beat))
        noise = hit[:, 0] if hit.ndim > 1 else hit
        self.assertLess(abs(float(np.corrcoef(noise[:-1], noise[1:])[0, 1])), 0.3, "打击音应是白噪声")
        for sample_rate in (22050, 44100, 48000):
            data = click_samples(880.0, sample_rate, 2)
            self.assertAlmostEqual(data.shape[0] / sample_rate * 1000.0, CLICK_MS, delta=1.0)


@unittest.skipUnless(STRETCH_AVAILABLE, "需要 numpy + pygame")
class EffectMixTests(unittest.TestCase):
    def _backend(self, seconds: float = 2.0):
        import numpy as np

        try:
            backend = A.StretchedSoundBackend()
        except Exception:  # pragma: no cover - 无音频设备
            self.skipTest("本机音频设备不可用")
        samples = int(backend.freq * seconds)
        shape = (samples, backend.channels) if backend.channels > 1 else (samples,)
        backend._samples = np.zeros(shape, dtype=np.int16)
        backend._length_ms = seconds * 1000.0
        return backend

    def test_effect_mixed_at_scaled_position_without_stretching(self) -> None:
        import numpy as np
        import pygame

        backend = self._backend()
        pulse_ms = 10.0

        def factory(kind, rate, channels):
            data = np.full(int(rate * pulse_ms / 1000.0), 20000, dtype=np.int16)
            return np.repeat(data.reshape(-1, 1), channels, axis=1) if channels > 1 else data

        backend.set_rate(0.5)
        backend.set_effects([(500.0, "beat")], factory)
        backend.set_effect_gains({"beat": 1.0})
        sound, start_ms, _stop_ms = backend._make_window(0.0)
        self.assertAlmostEqual(start_ms, 0.0, places=6)
        data = pygame.sndarray.array(sound).astype(np.int32)
        mono = np.abs(data).max(axis=1) if data.ndim > 1 else np.abs(data)
        index = np.flatnonzero(mono >= 10000)
        self.assertTrue(index.size)
        self.assertAlmostEqual(index[0] / backend.freq * 1000.0, 1000.0, delta=2.0)  # 500ms / 0.5
        self.assertAlmostEqual(
            (index[-1] - index[0] + 1) / backend.freq * 1000.0, pulse_ms, delta=1.0
        )

    def test_change_ahead_of_playhead_rebuilds_current_window(self) -> None:
        import numpy as np
        import pygame

        backend = self._backend(17.0)

        def factory(kind, rate, channels):
            data = np.full(int(rate * 5.0 / 1000.0), 20000, dtype=np.int16)
            return np.repeat(data.reshape(-1, 1), channels, axis=1) if channels > 1 else data

        class StubChannel:
            def __init__(self) -> None:
                self.playing = None
                self.queued = None
                self.plays = 0

            def play(self, sound):
                self.playing, self.queued, self.plays = sound, None, self.plays + 1

            def queue(self, sound):
                self.queued = sound

            def get_sound(self):
                return self.playing

            def get_busy(self) -> bool:
                return True

            def stop(self) -> None:
                return

        def pulses(sound):
            data = pygame.sndarray.array(sound).astype(np.int32)
            mono = np.abs(data[:, 0] if data.ndim > 1 else data)
            return np.flatnonzero(mono >= 10000)

        backend.set_rate(1.0)
        backend.set_effects([], factory)
        backend.set_effect_gains({"beat": 1.0})
        channel = StubChannel()
        backend._channel = channel
        backend._playing = True
        backend._anchor_time = time.perf_counter()
        backend._anchor = backend._make_window(0.0)
        backend._queued = backend._make_window(backend._anchor[2])
        channel.play(backend._anchor[0])
        channel.queue(backend._queued[0])
        backend.set_effects([(900.0, "beat")], factory)
        self.assertEqual(channel.plays, 2, "改动落在当前窗口内时要重合成")
        index = pulses(backend._anchor[0])
        self.assertTrue(index.size)
        placed_ms = backend._anchor[1] + index[0] / backend.freq * 1000.0
        self.assertAlmostEqual(placed_ms, 900.0, delta=3.0)
        plays = channel.plays
        behind = backend._anchor[2] + 500.0
        backend.set_effects([(900.0, "beat"), (behind, "beat")], factory)
        self.assertEqual(channel.plays, plays, "只改到窗口之后时不必重播当前窗口")


if __name__ == "__main__":
    unittest.main()

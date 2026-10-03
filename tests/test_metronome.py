"""节拍音 / 打击音：预先算出的时刻表、波形与混音。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.metronome import (
    CLICK_MS,
    MODE_BAR,
    MODE_BEAT,
    MODE_OFF,
    click_samples,
    effect_events,
    effect_samples,
    hit_samples,
)
from rhythm_editor.model.chart import Chart, EditorSettings
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseClick, MouseRegion, MouseSlide
from rhythm_editor.model.timing import BpmSegment, TimingMap


def make_chart(notes=(), timing=None) -> Chart:
    chart = Chart()
    chart.timing = timing or TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 120.0, 3)])
    for note in notes:
        chart.add(note)
    return chart


class EffectScheduleTests(unittest.TestCase):
    """``effect_events``：把整条时间轴上的音效时刻一次算出来。"""

    def setUp(self) -> None:
        self.timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 120.0, 3)])

    def test_default_is_off(self) -> None:
        self.assertEqual(EditorSettings().metronome, MODE_OFF)
        chart = make_chart(timing=self.timing)
        self.assertEqual(effect_events(chart, MODE_OFF, hit_enabled=False, until_ms=5000.0), [])

    def test_beat_mode_lists_every_integer_beat(self) -> None:
        """每一整拍一个时刻（120BPM = 500ms/拍）；第 0 拍也排上，由起播位置裁剪。"""
        chart = make_chart(timing=self.timing)
        events = effect_events(chart, MODE_BEAT, hit_enabled=False, until_ms=2000.0)
        self.assertEqual([round(t) for t, _kind in events], [0, 500, 1000, 1500, 2000])
        self.assertTrue(all(kind == "beat" for _t, kind in events))

    def test_bar_mode_lists_bar_lines_only(self) -> None:
        """小节音：4 拍一小节（500ms/拍）→ 0 / 2000 / 4000ms；
        第 10 拍换了 BPM 段（每小节 3 拍），变更处一定新开一小节 → 5000ms。"""
        chart = make_chart(timing=self.timing)
        events = effect_events(chart, MODE_BAR, hit_enabled=False, until_ms=5200.0)
        self.assertEqual([round(t) for t, _kind in events], [0, 2000, 4000, 5000])

    def test_hits_use_note_head_times_and_dedupe_chords(self) -> None:
        """打击音：取每个物件的开头判定时刻；同一时刻的和弦只响一声。"""
        chart = make_chart(
            [
                LaneNote("L1", 0, 0, 2.0),                       # 单键
                LaneNote("L2", 1, 3, 5.0, 7.0),                  # 长按（取开头）
                MouseRegion("R1", 4.0, 6.0, BoundaryCurve(0.0, 0.0), BoundaryCurve(1.0, 1.0)),
                MouseSlide("S1", 2.0, 0.2, 0.6),                 # 与 L1 同拍：和弦只响一声
                MouseClick("C1", 8.0, 0.5, 0.2),
            ],
            timing=self.timing,
        )
        events = effect_events(chart, MODE_OFF, hit_enabled=True)
        self.assertEqual([round(t) for t, _kind in events], [1000, 2000, 2500, 4000])
        self.assertTrue(all(kind == "hit" for _t, kind in events))

    def test_extreme_bpm_keeps_every_click(self) -> None:
        """极高 BPM：所有拍点都在表里（旧的逐帧实现一次只能补一声，会漏）。"""
        timing = TimingMap([BpmSegment(0.0, 6000.0, 4)])
        chart = make_chart(timing=timing)
        events = effect_events(chart, MODE_BEAT, hit_enabled=False, until_ms=1000.0)
        self.assertEqual(len(events), 101)                 # 6000BPM = 100 拍/秒 → 第 0..100 拍
        self.assertEqual([round(t) for t, _kind in events[:3]], [0, 10, 20])

    def test_events_are_sorted(self) -> None:
        """时刻表按时间排序（混音按窗口二分查找依赖这一点）。"""
        chart = make_chart(
            [LaneNote("L1", 0, 0, 3.0), MouseSlide("S1", 1.0, 0.2, 0.6)],
            timing=self.timing,
        )
        events = effect_events(chart, MODE_BEAT, hit_enabled=True, until_ms=3000.0)
        self.assertEqual(events, sorted(events))

    def test_settings_roundtrip_and_repair(self) -> None:
        settings = EditorSettings(metronome=MODE_BAR)
        self.assertEqual(EditorSettings.from_dict(settings.to_dict()).metronome, MODE_BAR)
        broken = EditorSettings.from_dict({"metronome": "??? "})   # 非法值回落到关闭
        self.assertEqual(broken.metronome, MODE_OFF)


class EffectSampleTests(unittest.TestCase):
    def test_effect_samples_dispatch_by_kind(self) -> None:
        """波形工厂：bar / beat 是不同音高的正弦，hit 是白噪声。"""
        import numpy as np

        bar = effect_samples("bar", 44100, 1).astype(np.float32)
        beat = effect_samples("beat", 44100, 1).astype(np.float32)
        hit = effect_samples("hit", 44100, 1).astype(np.float32)
        for data in (bar, beat, hit):
            self.assertAlmostEqual(len(data) / 44100 * 1000.0, CLICK_MS, delta=1.0)
        self.assertFalse(np.array_equal(bar, beat), "小节音与节拍音音高不同")
        noise = hit[:, 0] if hit.ndim > 1 else hit
        corr = float(np.corrcoef(noise[:-1], noise[1:])[0, 1])
        self.assertLess(abs(corr), 0.3, "打击音应当是白噪声")

    def test_hit_sound_is_white_noise_with_same_length(self) -> None:
        """打击音是白噪声（不是纯音），长度与节拍音一致。"""
        import numpy as np

        data = hit_samples(44100, 2)
        self.assertAlmostEqual(data.shape[0] / 44100 * 1000.0, CLICK_MS, delta=1.0)
        mono = data[:, 0].astype(np.float32)
        corr = float(np.corrcoef(mono[:-1], mono[1:])[0, 1])
        self.assertLess(abs(corr), 0.3, "白噪声的相邻样本不应强相关")
        self.assertGreater(float(np.std(mono)), 100.0)

    def test_volume_settings_roundtrip(self) -> None:
        settings = EditorSettings(music_volume=0.5, metronome_volume=0.3, hit_volume=0.8)
        restored = EditorSettings.from_dict(settings.to_dict())
        self.assertAlmostEqual(restored.music_volume, 0.5)
        self.assertAlmostEqual(restored.metronome_volume, 0.3)
        self.assertAlmostEqual(restored.hit_volume, 0.8)
        clamped = EditorSettings(music_volume=5.0, hit_volume=-2.0)
        clamped.normalize()
        self.assertAlmostEqual(clamped.music_volume, 1.0)
        self.assertAlmostEqual(clamped.hit_volume, 0.0)

    def test_click_length_does_not_depend_on_playback_rate(self) -> None:
        """慢放不改变“滴”声长度：合成长度固定 45ms，且不经过时间伸缩。"""
        for sample_rate in (22050, 44100, 48000):
            data = click_samples(880.0, sample_rate, 2)
            self.assertAlmostEqual(data.shape[0] / sample_rate * 1000.0, CLICK_MS, delta=1.0)
        from rhythm_editor.audio import STRETCH_AVAILABLE, AudioPlayer
        from rhythm_editor.metronome import Metronome

        if not STRETCH_AVAILABLE:
            self.skipTest("没有 pygame + numpy，跳过")
        metronome = Metronome()          # 游玩器的即时打击音仍走这条路
        before = metronome._sound(880.0, lambda rate, ch: click_samples(880.0, rate, ch))
        if before is None:
            self.skipTest("本机音频设备不可用，跳过")
        player = AudioPlayer()
        player.set_rate(0.5)             # 慢放：只影响歌曲（重采样），节拍音不受影响
        after = metronome._sound(880.0, lambda rate, ch: click_samples(880.0, rate, ch))
        self.assertIs(before, after, "慢放不应重建节拍音")
        self.assertAlmostEqual(before.get_length() * 1000.0, CLICK_MS, places=1)


class EffectMixTests(unittest.TestCase):
    """音频层把预计算时刻的音效混进输出（长度不随倍速变化）。"""

    def setUp(self) -> None:
        from rhythm_editor.audio import STRETCH_AVAILABLE

        if not STRETCH_AVAILABLE:
            self.skipTest("没有 pygame + numpy，跳过")

    def test_effect_is_mixed_at_scaled_position_without_stretching(self) -> None:
        import numpy as np
        import pygame

        from rhythm_editor.audio import StretchedSoundBackend

        try:
            backend = StretchedSoundBackend()
        except Exception:  # pragma: no cover - 无音频设备
            self.skipTest("本机音频设备不可用，跳过")
        shape = (
            (backend.freq * 2, backend.channels)
            if backend.channels > 1
            else (backend.freq * 2,)
        )
        backend._samples = np.zeros(shape, dtype=np.int16)
        pulse_ms = 10.0

        def factory(kind, rate, channels):
            data = np.full(int(rate * pulse_ms / 1000.0), 20000, dtype=np.int16)
            if channels > 1:
                data = np.repeat(data.reshape(-1, 1), channels, axis=1)
            return data

        backend.set_rate(0.5)                    # 半速：输出位置 = 歌曲位置 / 0.5
        backend.set_effects([(500.0, "beat")], factory)
        backend.set_effect_gains({"beat": 1.0})
        window = backend._make_window(0.0)
        self.assertIsNotNone(window)
        sound, start_ms, _stop_ms = window
        self.assertAlmostEqual(start_ms, 0.0, places=6)
        data = pygame.sndarray.array(sound).astype(np.int32)
        mono = np.abs(data).max(axis=1) if data.ndim > 1 else np.abs(data)
        index = np.flatnonzero(mono >= 10000)
        self.assertTrue(index.size, "音效应当被混进窗口")
        first_ms = index[0] / backend.freq * 1000.0
        length_ms = (index[-1] - index[0] + 1) / backend.freq * 1000.0
        self.assertAlmostEqual(first_ms, 1000.0, delta=2.0)     # 500ms / 0.5 = 1000ms
        self.assertAlmostEqual(length_ms, pulse_ms, delta=1.0)  # 长度不随倍速拉伸

    def test_effect_tail_continues_across_window_boundary(self) -> None:
        """窗口起点前开始的音效：尾巴要接着混完，不能在边界被截断。"""
        import numpy as np
        import pygame

        from rhythm_editor.audio import StretchedSoundBackend

        try:
            backend = StretchedSoundBackend()
        except Exception:  # pragma: no cover - 无音频设备
            self.skipTest("本机音频设备不可用，跳过")
        shape = (
            (backend.freq * 2, backend.channels)
            if backend.channels > 1
            else (backend.freq * 2,)
        )
        backend._samples = np.zeros(shape, dtype=np.int16)
        length = 1000                            # 音效样本数（约 23ms）

        def ramp_factory(kind, rate, channels):
            # 逐样本递增：用来分辨“从头播”还是“接着尾巴播”
            data = (np.arange(1, length + 1, dtype=np.int32) * 20).astype(np.int16)
            if channels > 1:
                data = np.repeat(data.reshape(-1, 1), channels, axis=1)
            return data

        backend.set_rate(1.0)
        backend.set_effects([(0.0, "beat")], ramp_factory)
        backend.set_effect_gains({"beat": 1.0})
        window = backend._make_window(10.0)      # 窗口从 10ms 开始，音效已经响了 10ms
        self.assertIsNotNone(window)
        data = pygame.sndarray.array(window[0]).astype(np.int32)
        mono = data[:, 0] if data.ndim > 1 else data
        skip = int(round(10.0 * backend.freq / 1000.0))
        self.assertEqual(int(mono[0]), (skip + 1) * 20, "应从尾巴（第 skip 个样本）接着混")
        self.assertEqual(int(mono[length - skip - 1]), length * 20)
        self.assertEqual(int(mono[length - skip]), 0, "音效放完就应当静音")
        self.assertEqual(int(np.count_nonzero(mono)), length - skip)

    def test_queued_window_is_rebuilt_when_effects_change(self) -> None:
        """播放中改时刻表：重合成“排队窗口”，改动在一个窗口内生效。"""
        import numpy as np
        import pygame

        from rhythm_editor.audio import StretchedSoundBackend

        try:
            backend = StretchedSoundBackend()
        except Exception:  # pragma: no cover - 无音频设备
            self.skipTest("本机音频设备不可用，跳过")
        shape = (
            (backend.freq * 17, backend.channels)
            if backend.channels > 1
            else (backend.freq * 17,)
        )
        backend._samples = np.zeros(shape, dtype=np.int16)

        def pulse_factory(kind, rate, channels):
            data = np.full(int(rate * 5.0 / 1000.0), 20000, dtype=np.int16)
            if channels > 1:
                data = np.repeat(data.reshape(-1, 1), channels, axis=1)
            return data

        class StubChannel:
            def __init__(self) -> None:
                self.playing = None
                self.queued = None

            def play(self, sound):
                self.playing = sound

            def queue(self, sound):
                self.queued = sound

            def get_sound(self):
                return self.playing

            def get_busy(self) -> bool:
                return True

            def stop(self) -> None:
                return

        backend.set_rate(1.0)
        backend.set_effects([(8_100.0, "beat")], pulse_factory)
        channel = StubChannel()
        backend._channel = channel
        backend._playing = True
        backend._anchor = backend._make_window(0.0)
        backend._queued = backend._make_window(backend._anchor[2])
        old = backend._queued[0]
        channel.queue(old)
        channel.play(backend._anchor[0])          # 模拟：锚点窗口正在播

        backend.set_effects([(9_000.0, "beat")], pulse_factory)
        self.assertIs(backend._queued[0], channel.queued, "排队窗口应当已被替换")
        self.assertIsNot(backend._queued[0], old)
        start_ms = backend._queued[1]
        data = pygame.sndarray.array(backend._queued[0]).astype(np.int32)
        mono = np.abs(data.max(axis=1) if data.ndim > 1 else data)
        index = np.flatnonzero(mono >= 10000)
        self.assertTrue(index.size, "新时刻表的音效应当在新排队窗口里")
        first_ms = start_ms + index[0] / backend.freq * 1000.0
        self.assertAlmostEqual(first_ms, 9_000.0, delta=2.0)

        # 排队窗口已经开始播：只滚动锚点，不能替换（否则会重复 / 断流）
        channel.play(backend._queued[0])
        current = backend._queued[0]
        backend.set_effects([(10_000.0, "beat")], pulse_factory)
        self.assertIsNone(backend._queued)
        self.assertIs(backend._anchor[0], current)
        self.assertIs(channel.queued, current, "通道里的排队窗口不能被换掉")


if __name__ == "__main__":
    unittest.main()

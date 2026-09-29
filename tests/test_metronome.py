"""节拍音：响点判定 + 设置项。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import config
from rhythm_editor.metronome import (
    CLICK_MS,
    MODE_BAR,
    MODE_BEAT,
    MODE_OFF,
    click_samples,
    clicks_between,
    hit_samples,
    hits_between,
)
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseClick, MouseRegion, MouseSlide
from rhythm_editor.model.chart import EditorSettings
from rhythm_editor.model.timing import BpmSegment, TimingMap


class ClickTimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(10.0, 120.0, 3)])

    def test_default_is_off(self) -> None:
        self.assertEqual(EditorSettings().metronome, MODE_OFF)
        self.assertEqual(clicks_between(self.timing, MODE_OFF, 0.0, 9.9), [])

    def test_beat_mode_fires_on_every_integer_beat(self) -> None:
        self.assertEqual(clicks_between(self.timing, MODE_BEAT, 2.9, 3.1), [3.0])
        # 一小段里没跨过整数拍 -> 不响
        self.assertEqual(clicks_between(self.timing, MODE_BEAT, 3.1, 3.9), [])

    def test_bar_mode_fires_on_bar_lines_only(self) -> None:
        # 0/4/8 是 4 拍一小节的小节线；第 10 拍起每小节 3 拍
        self.assertEqual(clicks_between(self.timing, MODE_BAR, 3.5, 4.2), [4.0])
        self.assertEqual(clicks_between(self.timing, MODE_BAR, 4.2, 7.9), [])
        self.assertEqual(clicks_between(self.timing, MODE_BAR, 9.5, 10.2), [10.0])
        self.assertEqual(clicks_between(self.timing, MODE_BAR, 10.5, 12.9), [])
        self.assertEqual(clicks_between(self.timing, MODE_BAR, 12.5, 13.2), [13.0])

    def test_no_catch_up_on_jumps(self) -> None:
        """跳转 / 卡顿后不补发一串“滴”声。"""
        jump = config.METRONOME_MAX_JUMP_BEATS + 1.0
        self.assertEqual(clicks_between(self.timing, MODE_BEAT, 1.0, 1.0 + jump), [])
        # 倒放或暂停恢复（时间没前进）不响
        self.assertEqual(clicks_between(self.timing, MODE_BEAT, 5.0, 4.0), [])
        self.assertEqual(clicks_between(self.timing, MODE_BEAT, 5.0, 5.0), [])

    def test_settings_roundtrip_and_repair(self) -> None:
        settings = EditorSettings(metronome=MODE_BAR)
        self.assertEqual(EditorSettings.from_dict(settings.to_dict()).metronome, MODE_BAR)
        # 非法值回落到关闭
        broken = EditorSettings.from_dict({"metronome": "??? "})
        self.assertEqual(broken.metronome, MODE_OFF)

    # ---------------------------------------------------- 打击音效（白噪声）
    def test_hits_fire_on_head_beats(self) -> None:
        """单键 / 长按开头 / 空域开头 / 滑键 / 点击按键到达判定线时各响一次。"""
        from rhythm_editor.model.chart import Chart

        chart = Chart()
        chart.add(LaneNote("L1", 0, 0, 2.0))                 # 单键
        chart.add(LaneNote("L2", 1, 3, 5.0, 7.0))            # 长按（取开头）
        chart.add(MouseRegion("R1", 4.0, 6.0, BoundaryCurve(0, 0), BoundaryCurve(1, 1)))
        chart.add(MouseSlide("S1", 6.5, 0.2, 0.6))
        chart.add(MouseClick("C1", 8.0, 0.5, 0.2))
        cases = ((1.9, 2.1, "L1"), (4.9, 5.1, "L2"), (3.9, 4.1, "R1"),
                 (6.4, 6.6, "S1"), (7.9, 8.1, "C1"))
        for low, high, expected in cases:
            self.assertEqual(hits_between(chart, low, high), [expected], f"{low}~{high}")
        # 没有物件到达判定线的区间 -> 不响；长按中段不会重复响
        self.assertEqual(hits_between(chart, 2.2, 3.9), [])
        self.assertEqual(hits_between(chart, 5.2, 6.3), [])
        # 倒放 / 跳转不补发
        self.assertEqual(hits_between(chart, 6.0, 5.0), [])
        self.assertEqual(
            hits_between(chart, 1.0, 1.0 + config.METRONOME_MAX_JUMP_BEATS + 1.0), []
        )

    def test_hit_sound_is_white_noise_with_same_length(self) -> None:
        """打击音是白噪声（不是纯音），长度与节拍音一致。"""
        import numpy as np

        data = hit_samples(44100, 2)
        self.assertAlmostEqual(data.shape[0] / 44100 * 1000.0, CLICK_MS, delta=1.0)
        mono = data[:, 0].astype(np.float32)
        # 白噪声：相邻样本相关性接近 0（纯音会接近 1）
        corr = float(np.corrcoef(mono[:-1], mono[1:])[0, 1])
        self.assertLess(abs(corr), 0.3, "白噪声的相邻样本不应强相关")
        self.assertGreater(float(np.std(mono)), 100.0)

    def test_volume_settings_roundtrip(self) -> None:
        settings = EditorSettings(music_volume=0.5, metronome_volume=0.3, hit_volume=0.8)
        restored = EditorSettings.from_dict(settings.to_dict())
        self.assertAlmostEqual(restored.music_volume, 0.5)
        self.assertAlmostEqual(restored.metronome_volume, 0.3)
        self.assertAlmostEqual(restored.hit_volume, 0.8)
        # 越界会被夹回 0..1
        clamped = EditorSettings(music_volume=5.0, hit_volume=-2.0)
        clamped.normalize()
        self.assertAlmostEqual(clamped.music_volume, 1.0)
        self.assertAlmostEqual(clamped.hit_volume, 0.0)

    def test_click_length_does_not_depend_on_playback_rate(self) -> None:
        """慢放不改变“滴”声长度：合成长度固定 45ms，且不经过时间伸缩。"""
        for sample_rate in (22050, 44100, 48000):
            data = click_samples(880.0, sample_rate, 2)
            # 采样点取整会带来一点点误差（半个采样点以内）
            self.assertAlmostEqual(
                data.shape[0] / sample_rate * 1000.0, CLICK_MS, delta=1.0
            )
        from rhythm_editor.audio import STRETCH_AVAILABLE, AudioPlayer
        from rhythm_editor.metronome import Metronome

        if not STRETCH_AVAILABLE:
            self.skipTest("没有 pygame + numpy，跳过")
        metronome = Metronome()
        before = metronome._sound(880.0, lambda rate, ch: click_samples(880.0, rate, ch))
        if before is None:
            self.skipTest("本机音频设备不可用，跳过")
        player = AudioPlayer()
        player.set_rate(0.5)          # 慢放：只影响歌曲（重采样），节拍音不受影响
        after = metronome._sound(880.0, lambda rate, ch: click_samples(880.0, rate, ch))
        self.assertIs(before, after, "慢放不应重建节拍音")
        self.assertAlmostEqual(before.get_length() * 1000.0, CLICK_MS, places=1)


if __name__ == "__main__":
    unittest.main()

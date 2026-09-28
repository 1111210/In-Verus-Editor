"""启动脚本、启动日志与音频后端提示的自检。"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor.audio import (  # noqa: E402
    _PYGAME,
    audio_backend_name,
    audio_backend_state,
    audio_setup_hint,
)
from rhythm_editor import audio as audio_module  # noqa: E402


def _load_launcher():
    """以模块方式加载 run_editor.py（它不在包内，需要手动 import）。"""
    spec = importlib.util.spec_from_file_location(
        "run_editor_launcher", os.path.join(ROOT, "run_editor.py")
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.launcher = _load_launcher()

    def test_bootstrap_adds_src_to_path(self) -> None:
        """启动脚本只把 src 加进搜索路径（不检测 / 不切换解释器）。"""
        self.launcher._bootstrap()
        self.assertIn(os.path.join(ROOT, "src"), sys.path)


class StartupLogTests(unittest.TestCase):
    """启动日志：排障时要能看到到底用的哪个 Python。"""

    def test_log_records_interpreter_and_backend(self) -> None:
        from rhythm_editor.audio import audio_backend_name, write_startup_log

        with tempfile.TemporaryDirectory() as tmp:
            # write_startup_log 固定写到仓库 _diag/，这里验证内容形态
            path = write_startup_log()
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("解释器：", text)
        self.assertIn(sys.executable, text)
        self.assertIn(f"后端：{audio_backend_name()}", text)
        self.assertIn("pygame：", text)


class AudioHintTests(unittest.TestCase):

    def test_stop_rule_needs_both_music_and_chart_ended(self) -> None:
        """音乐与谱面都播完 3 秒才停；只满足一边不停，没有音乐永远不停。"""
        from rhythm_editor import config
        from rhythm_editor.app import should_stop_playback

        margin = config.PLAY_END_MARGIN_SECONDS
        self.assertEqual(margin, 3.0)
        # 音乐还没播完 -> 不停
        self.assertFalse(should_stop_playback(100.0, 10.0, None))
        # 只有音乐播完 3 秒、谱面还没播完 -> 不停
        self.assertFalse(should_stop_playback(20.0, 30.0, 10.0))
        # 只有谱面播完 3 秒、音乐还没播完 -> 不停
        self.assertFalse(should_stop_playback(20.0, 10.0, 30.0))
        # 两边都过了 3 秒 -> 停
        self.assertTrue(should_stop_playback(40.0, 10.0, 20.0))
        # 恰好等于边界时不提前停
        self.assertFalse(should_stop_playback(13.0, 0.0, 10.0))
        self.assertTrue(should_stop_playback(13.01, 0.0, 10.0))
    def test_device_failure_is_reported_as_such(self) -> None:
        """pygame 装了但设备打不开时：状态与提示都不能说成“未安装 pygame”。"""
        from rhythm_editor.audio import AudioPlayer

        original_error = audio_module._MIXER_ERROR
        # 注意：要通过 __dict__ 取，否则会把 staticmethod 还原成普通方法
        original_factory = AudioPlayer.__dict__["_create_plain_backend"]
        audio_module._MIXER_ERROR = "error: Audio target 'x' not available"
        # 用真实的静音后端模拟“设备打不开”的回退结果
        AudioPlayer._create_plain_backend = staticmethod(  # type: ignore[assignment]
            lambda: audio_module.SilentBackend()
        )
        try:
            self.assertEqual(audio_backend_state(), "device")
            hint = audio_setup_hint()
            self.assertIn("音频设备", hint)
            self.assertIn(sys.executable, hint)
            self.assertNotIn("未检测到 pygame", hint)
            text = AudioPlayer().status_text
            self.assertIn("音频设备不可用", text)
            self.assertNotIn("未安装 pygame", text)
        finally:
            AudioPlayer._create_plain_backend = original_factory  # type: ignore[assignment]
            audio_module._MIXER_ERROR = original_error

    def test_retry_backend_restores_audio_when_possible(self) -> None:
        """retry_backend() 重建设备后应当恢复可用（本机 .venv 里可发声）。"""
        from rhythm_editor.audio import AudioPlayer

        player = AudioPlayer()
        was_usable = player.usable_audio
        ok = player.retry_backend()
        self.assertEqual(ok, player.usable_audio)
        if was_usable:
            self.assertTrue(ok, "原本可用时重试后必须仍然可用")


if __name__ == "__main__":
    unittest.main()

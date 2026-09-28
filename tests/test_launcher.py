"""启动脚本（run_editor.py）与音频后端提示的自检。"""

from __future__ import annotations

import importlib.util
import os
import sys
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

    def test_finds_project_venv(self) -> None:
        """项目里的 .venv 解释器能被找到（本仓库确实存在）。"""
        interpreter = self.launcher._venv_python(ROOT)
        if not os.path.isfile(os.path.join(ROOT, ".venv", "Scripts", "python.exe")):
            self.skipTest("本机没有 .venv，跳过")
        self.assertTrue(os.path.isfile(interpreter))

    def test_venv_python_missing_returns_empty(self) -> None:
        self.assertEqual(self.launcher._venv_python(os.path.join(ROOT, "no-such-dir")), "")

    def test_running_in_venv_is_detected(self) -> None:
        """测试本身跑在 .venv 里：venv_guard 必须认为「已经在虚拟环境中」。"""
        from rhythm_editor.venv_guard import (
            ensure_project_venv,
            running_in_project_venv,
        )

        if not self.launcher._venv_python(ROOT):
            self.skipTest("本机没有 .venv，跳过")
        if not running_in_project_venv(ROOT):
            self.skipTest("当前测试不是用 .venv 解释器运行的")
        # 已经在虚拟环境里：直接返回，不重启、不抛异常
        self.assertIsNone(ensure_project_venv(argv=[]))

    def test_allow_any_python_skips_guard(self) -> None:
        """VERUS_ALLOW_ANY_PYTHON=1 时不做任何切换（方便在别的解释器里调试）。"""
        from rhythm_editor.venv_guard import ENV_ALLOW_ANY, ensure_project_venv

        os.environ[ENV_ALLOW_ANY] = "1"
        try:
            self.assertIsNone(ensure_project_venv(argv=[]))
        finally:
            del os.environ[ENV_ALLOW_ANY]

    def test_missing_venv_gives_install_hint(self) -> None:
        """没有 .venv 时：不装依赖、不报缺 pygame，而是给出创建命令并退出。"""
        from rhythm_editor import venv_guard

        with mock.patch.object(venv_guard, "project_root", lambda: os.path.join(ROOT, "no-such-dir")):
            os.environ.pop(venv_guard.ENV_ALLOW_ANY, None)
            with self.assertRaises(SystemExit) as ctx:
                venv_guard.ensure_project_venv(argv=[])
            self.assertEqual(ctx.exception.code, 2)
        hint = venv_guard.venv_install_hint(ROOT)
        self.assertIn(".venv", hint)
        self.assertIn("requirements.txt", hint)

    def test_pygame_probe_matches_audio_module(self) -> None:
        self.assertEqual(self.launcher._has_pygame(), _PYGAME is not None)


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
    def test_backend_name_and_hint_are_consistent(self) -> None:
        name = audio_backend_name()
        hint = audio_setup_hint()
        if name == "silent":
            self.assertIn("pygame", hint)
            self.assertIn(sys.executable, hint)
        else:
            self.assertEqual(hint, "")

    def test_silent_status_text_mentions_pygame(self) -> None:
        from rhythm_editor.audio import AudioPlayer

        player = AudioPlayer()
        if player.usable_audio:
            self.assertIn("pygame", player.status_text)
        else:
            # 静音时要说清原因：缺依赖 / 设备打不开（不能一律写成“未安装 pygame”）
            self.assertTrue(
                "未安装 pygame" in player.status_text
                or "音频设备不可用" in player.status_text,
                player.status_text,
            )

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

    def test_missing_pygame_reports_interpreter(self) -> None:
        original = audio_module._PYGAME
        audio_module._PYGAME = None
        try:
            self.assertEqual(audio_backend_state(), "missing")
            hint = audio_setup_hint()
            self.assertIn("未检测到 pygame", hint)
            self.assertIn(sys.executable, hint)
        finally:
            audio_module._PYGAME = original

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

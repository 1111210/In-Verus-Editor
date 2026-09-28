"""启动脚本（run_editor.py）与音频后端提示的自检。"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest

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

    def test_no_restart_when_disabled_or_already_restarted(self) -> None:
        """VERUS_NO_VENV / 已经在虚拟环境里时不再重启，避免死循环。"""
        os.environ["VERUS_NO_VENV"] = "1"
        try:
            self.assertFalse(self.launcher._restart_with_venv(ROOT))
        finally:
            del os.environ["VERUS_NO_VENV"]
        os.environ["VERUS_VENV_REEXEC"] = "1"
        try:
            self.assertFalse(self.launcher._restart_with_venv(ROOT))
        finally:
            del os.environ["VERUS_VENV_REEXEC"]

    def test_current_venv_run_is_not_restarted(self) -> None:
        """已经在 .venv 里运行时（本测试就是）不应再次重启。"""
        interpreter = self.launcher._venv_python(ROOT)
        if not interpreter:
            self.skipTest("本机没有 .venv，跳过")
        try:
            same = os.path.samefile(interpreter, sys.executable)
        except OSError:
            self.skipTest("无法比较解释器路径")
        if not same:
            self.skipTest("当前测试不是用 .venv 解释器运行的")
        self.assertFalse(self.launcher._restart_with_venv(ROOT))

    def test_pygame_probe_matches_audio_module(self) -> None:
        self.assertEqual(self.launcher._has_pygame(), _PYGAME is not None)


class AudioHintTests(unittest.TestCase):
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

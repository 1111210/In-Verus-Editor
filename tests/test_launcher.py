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
    audio_setup_hint,
)


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
            self.assertIn("未安装 pygame", player.status_text)


if __name__ == "__main__":
    unittest.main()

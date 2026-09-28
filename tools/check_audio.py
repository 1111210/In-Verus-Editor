"""校验音频后端与播放 / 暂停 / 延迟补偿 / seek（不需要图形界面）。

用法::

    python tools/check_audio.py

会生成一段 2 秒的正弦波 WAV，然后：

1. 载入并播放，检查播放位置随时间前进；
2. 检查 ``heard_position_ms`` = 后端位置 − 系统延迟；
3. 暂停后位置保持不变，``seek_ms`` 能跳到指定位置；
4. 若环境里没有可用音频设备，会自动退化到静音时钟后端（也算通过，但会明确标注）。
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# 输出里带有 − (U+2212) 等符号，Windows 控制台默认 GBK 会直接抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - 极老的 Python / 非文本流
        pass

from rhythm_editor.audio import AudioPlayer, audio_backend_name  # noqa: E402


def make_wav(path: str, seconds: float = 2.0, rate: int = 44100) -> str:
    frames = int(seconds * rate)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        data = bytearray()
        for index in range(frames):
            value = int(12000 * math.sin(2 * math.pi * 440 * index / rate))
            data += struct.pack("<h", value)
        handle.writeframes(bytes(data))
    return path


def main() -> int:
    failures = []

    def check(text: str, ok: bool, detail: str = "") -> None:
        print(f"[{'OK ' if ok else 'NG '}] {text}" + (f" -- {detail}" if detail else ""))
        if not ok:
            failures.append(text)

    with tempfile.TemporaryDirectory() as tmp:
        wav = make_wav(os.path.join(tmp, "tone.wav"))
        player = AudioPlayer()
        player.latency_ms = 60.0
        check(f"音频后端：{audio_backend_name()}", True,
              "真实播放" if player.usable_audio else "无可用音频设备/pygame，退化为静音时钟")

        loaded = player.load(wav)
        # 这个检查会真的发声（2 秒 440Hz 测试音）：默认音量压到很低，避免调试时吵到人。
        # 想听清楚可以设环境变量 VERUS_AUDIO_CHECK_VOLUME=0.6
        player.set_volume(float(os.environ.get("VERUS_AUDIO_CHECK_VOLUME", "0.08")))
        check("载入 WAV", loaded, os.path.basename(wav))

        player.play(200.0)
        time.sleep(0.4)
        raw_before = player.position_ms()
        heard = player.heard_position_ms()
        raw_after = player.position_ms()
        check("播放位置随时间前进", raw_after > 200.0, f"{raw_after:.1f} ms")
        latency = player.latency_ms if player.usable_audio else 0.0
        low = max(0.0, raw_before - latency - 5.0)
        high = max(0.0, raw_after - latency + 5.0)
        check("耳朵听到的位置 = 后端位置 − 系统延迟", low <= heard <= high,
              f"heard={heard:.1f} ms, raw={raw_before:.1f}~{raw_after:.1f} ms, "
              f"latency={player.latency_ms:g} ms")

        paused_at = player.pause()
        time.sleep(0.15)
        check("暂停后不再前进", abs(player.position_ms() - paused_at) < 1.0,
              f"{paused_at:.1f} ms")
        check("暂停状态标记正确", not player.is_playing)

        player.seek_ms(1000.0)
        check("seek 到指定位置", abs(player.position_ms() - 1000.0) < 1.0,
              f"{player.position_ms():.1f} ms")

        player.play(1000.0)
        time.sleep(0.25)
        check("从任意位置继续播放", player.position_ms() > 1000.0,
              f"{player.position_ms():.1f} ms")
        player.stop()
        check("停止后位置归零", abs(player.position_ms()) < 1.0)
        # 释放文件占用，否则 Windows 下临时目录无法删除
        player.unload()

    print()
    print("结论：" + ("全部通过" if not failures else f"未通过 {len(failures)} 项：{failures}"))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""音频播放后端与倍速（变速变调：慢放会同时降低音调）。

后端：
* PygameBackend            —— pygame.mixer.music 流式播放（1.0x 且不需要混音时使用）
* StretchedSoundBackend    —— 用线性插值重采样把样本拉长后播放
  （有倍速或需要混入音效时使用；慢放 = 降采样，音调随之变低）
* SilentBackend            —— 静音时钟（没有 pygame / 没有音频文件时的兜底）

所有后端的 position_ms() 都返回歌曲原始时间轴上的位置，
因此上层（谱面/判定线）的换算完全不用区分后端。

采样率：
载入时按音频文件的原始采样率初始化 mixer（见 audio_file_sample_rate），
避免 SDL 在 48k↔44.1k 之间重采样时改变歌曲时长，导致长歌逐渐音画偏移。

延迟补偿：
audio_latency_ms 表示“软件执行播放操作 -> 电脑硬件放出声音”的间隔。
播放时用 后端位置 - 延迟 作为“耳朵听到的位置”，再换算成判定线所在拍。
"""

from __future__ import annotations

import os
import sys
import time
import bisect
import struct
from typing import List, Optional, Tuple

import config

_PYGAME = None
_PYGAME_ERROR: Optional[str] = None
_MIXER_ERROR: Optional[str] = None
"""pygame 装了但音频设备打不开时的原因（用于区分解释器问题与设备问题）。"""
try:  # pragma: no cover - 依赖运行环境
    import pygame  # type: ignore

    _PYGAME = pygame
except Exception as exc:  # pragma: no cover
    _PYGAME_ERROR = str(exc)
    _PYGAME = None

_NP = None
try:  # pragma: no cover - 依赖运行环境
    import numpy as _numpy  # type: ignore

    _NP = _numpy
except Exception:  # pragma: no cover
    _NP = None

STRETCH_AVAILABLE = _PYGAME is not None and _NP is not None

# 时间伸缩参数
STRETCH_FRAME = 2048          # 每帧样本数（约 46ms @44.1kHz）
STRETCH_OVERLAP = 4           # 75% 重叠
# 一个窗口覆盖多少输出音频（原始窗口 = 这个值 × rate）。
# 通道只支持一个排队块（见 StretchedSoundBackend.poll），所以窗口取长一点：
# 8 秒的余量足够 16ms 的 tick 从容补窗口，单次重采样也只有 ~15ms。
STRETCH_TARGET_OUTPUT_MS = 8_000.0
# 源窗口下限：极慢倍速下也至少给插值这么多样本（否则一次插值的跨度太大）
STRETCH_SOURCE_MIN_MS = 250.0
STRETCH_WINDOW_MAX_MS = 8_000.0   # 源窗口上限

# 音效混音时向窗口起点左侧回看的余量（毫秒）：音效不参与时间伸缩，
# 长度固定（编辑器当前是 45ms 的“滴”声），取 100ms 留足余量，
# 保证跨窗口边界的音效尾巴也能接着混完（见 StretchedSoundBackend._mix_effects）
EFFECT_TAIL_MS = 100.0


def stretch_window_ms(rate: float) -> float:
    """一个窗口覆盖多少源音频：按“输出约 8 秒”换算，再取源窗口下限。"""
    return min(
        STRETCH_WINDOW_MAX_MS,
        max(STRETCH_SOURCE_MIN_MS, STRETCH_TARGET_OUTPUT_MS * max(0.0, float(rate))),
    )


def _events_in_range(events, start_ms: float, stop_ms: float) -> list:
    """取出 (start_ms, stop_ms) 之间的音效（events 已按时刻排序）。"""
    result = []
    first = bisect.bisect_right(events, start_ms, key=lambda item: item[0])
    for index in range(first, len(events)):
        item = events[index]
        if item[0] >= stop_ms:
            break
        result.append(item)
    return result


def audio_backend_name() -> str:
    if _PYGAME is None:
        return "silent"
    if STRETCH_AVAILABLE:
        return "pygame.mixer + resample"
    return "pygame.mixer"


def audio_setup_hint() -> str:
    """音频不可用时给用户一条能照着做的提示（正常情况返回空串）。"""
    if _PYGAME is None:
        detail = f"（{_PYGAME_ERROR}）" if _PYGAME_ERROR else ""
        return (
            f"未检测到 pygame{detail}：执行 pip install -r requirements.txt；"
            f"当前解释器：{sys.executable}"
        )
    if _MIXER_ERROR:
        return (
            f"pygame 已安装，但音频设备打不开（{_MIXER_ERROR}）："
            f"请检查系统输出设备，或关闭独占声卡的软件；"
            f"当前解释器：{sys.executable}"
        )
    return ""


def audio_backend_state() -> str:
    """音频状态：ok / missing（没 pygame）/ device（设备打不开）。"""
    if _PYGAME is None:
        return "missing"
    if _MIXER_ERROR:
        return "device"
    return "ok"


def _silent_reason() -> str:
    """静音回退的原因（给人看的一句话）。"""
    state = audio_backend_state()
    if state == "missing":
        return "未安装 pygame"
    if state == "device":
        return "音频设备不可用"
    return "未使用音频后端"


def audio_silent_reason() -> str:
    """对外：静音时的一句话原因（可用时为空串）。"""
    return "" if audio_backend_state() == "ok" else _silent_reason()


# ---------------------------------------------------------------- 采样率探测
_AUDIO_RATE_MIN = 7350
_AUDIO_RATE_MAX = 192000


def _valid_rate(value: int) -> Optional[int]:
    return value if _AUDIO_RATE_MIN <= value <= _AUDIO_RATE_MAX else None


def _id3_size(head: bytes) -> Optional[int]:
    """ID3v2 头里的标签总长（含 10 字节头）；没有 ID3 返回 None。"""
    if len(head) < 10 or head[:3] != b"ID3":
        return None
    return 10 + (
        ((head[6] & 0x7F) << 21)
        | ((head[7] & 0x7F) << 14)
        | ((head[8] & 0x7F) << 7)
        | (head[9] & 0x7F)
    )


def _mp3_sample_rate(path: str) -> Optional[int]:
    """读取 MP3 第一个有效帧的采样率；失败返回 None。"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(10)
            offset = _id3_size(head) or 0
            handle.seek(offset)
            data = handle.read(1 << 16)
    except OSError:
        return None
    table = {
        3: (44100, 48000, 32000),
        2: (22050, 24000, 16000),
        0: (11025, 12000, 8000),
    }
    for index in range(max(0, len(data) - 3)):
        if data[index] != 0xFF or (data[index + 1] & 0xE0) != 0xE0:
            continue
        version = (data[index + 1] >> 3) & 0x03
        layer = (data[index + 1] >> 1) & 0x03
        rate_index = (data[index + 2] >> 2) & 0x03
        if version == 1 or layer == 0 or rate_index == 3:
            continue
        return table[version][rate_index]
    return None


def _ogg_sample_rate(path: str) -> Optional[int]:
    """读取 OGG Vorbis 识别头里的采样率；失败返回 None。"""
    try:
        with open(path, "rb") as handle:
            data = handle.read(1 << 16)
    except OSError:
        return None
    index = data.find(b"\x01vorbis")
    if index >= 0 and index + 16 <= len(data):
        value = struct.unpack_from("<I", data, index + 12)[0]
        return _valid_rate(value)
    # Ogg Opus 固定解码为 48kHz；识别头里的原始采样率不参与输出
    if data.find(b"OpusHead") >= 0:
        return 48000
    return None


def _wav_sample_rate(path: str) -> Optional[int]:
    """读取 WAV/RF64 的 fmt 块采样率；失败返回 None。"""
    try:
        with open(path, "rb") as handle:
            data = handle.read(1 << 16)
    except OSError:
        return None
    if len(data) < 12 or data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        return None
    position = 12
    while position + 8 <= len(data):
        chunk = data[position:position + 4]
        size = struct.unpack_from("<I", data, position + 4)[0]
        if chunk == b"fmt " and position + 12 <= len(data):
            value = struct.unpack_from("<I", data, position + 12)[0]
            return _valid_rate(value)
        position += 8 + size + (size & 1)
    return None


def _flac_sample_rate(path: str) -> Optional[int]:
    """读取 FLAC STREAMINFO 的采样率；失败返回 None。"""
    try:
        with open(path, "rb") as handle:
            data = handle.read(1 << 16)
    except OSError:
        return None
    if len(data) < 4 + 4 + 34 or data[:4] != b"fLaC":
        return None
    position = 4
    if (data[position] & 0x7F) != 0:          # 第一个元数据块不是 STREAMINFO
        return None
    block = data[position + 4:position + 4 + 34]
    value = (block[10] << 12) | (block[11] << 4) | (block[12] >> 4)
    return _valid_rate(value)


# ------------------------------------------------------------ AAC / M4A 采样率
# ADTS / AudioSpecificConfig 的 samplingFrequencyIndex 表
_AAC_SAMPLE_RATES = (
    96000, 88200, 64000, 48000, 44100, 32000, 24000,
    22050, 16000, 12000, 11025, 8000, 7350,
)


def _aac_adts_sample_rate(path: str) -> Optional[int]:
    """读取裸 AAC（ADTS）第一个帧头的采样率；失败返回 None。"""
    try:
        with open(path, "rb") as handle:
            head = handle.read(10)
            offset = _id3_size(head) or 0
            handle.seek(offset)
            data = handle.read(1 << 16)
    except OSError:
        return None
    for index in range(max(0, len(data) - 7)):
        if data[index] != 0xFF or (data[index + 1] & 0xF6) != 0xF0:
            continue
        rate_index = (data[index + 2] >> 2) & 0x0F
        if rate_index < len(_AAC_SAMPLE_RATES):
            return _AAC_SAMPLE_RATES[rate_index]
    return None


def _mp4_boxes(data: bytes, start: int, end: int):
    """遍历 MP4 容器中的盒：产出 (类型, 负载起点, 负载终点)。"""
    position = start
    while position + 8 <= end:
        size = struct.unpack_from(">I", data, position)[0]
        kind = data[position + 4:position + 8]
        header = 8
        if size == 1:
            if position + 16 > end:
                break
            size = struct.unpack_from(">Q", data, position + 8)[0]
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            break
        yield kind, position + header, position + size
        position += size


_MP4_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"wave"}


def _mp4_find(data: bytes, start: int, end: int, kind: bytes, depth: int = 0):
    """在容器里递归查找指定盒，返回负载区间；找不到返回 None。"""
    if depth > 10:
        return None
    for box_kind, payload_start, payload_end in _mp4_boxes(data, start, end):
        if box_kind == kind:
            return payload_start, payload_end
        if box_kind in _MP4_CONTAINERS:
            found = _mp4_find(data, payload_start, payload_end, kind, depth + 1)
            if found:
                return found
        elif box_kind == b"stsd":
            # FullBox：version/flags(4) + entry_count(4)，后面才是样本条目
            found = _mp4_find(data, payload_start + 8, payload_end, kind, depth + 1)
            if found:
                return found
    return None


def _mp4_mdhd_timescale(data: bytes, start: int, end: int) -> Optional[int]:
    """mdhd 的 media timescale；音频轨通常等于采样率。"""
    if start + 12 > end:
        return None
    version = data[start]
    if version == 1:
        if start + 24 > end:
            return None
        value = struct.unpack_from(">I", data, start + 20)[0]
    else:
        if start + 16 > end:
            return None
        value = struct.unpack_from(">I", data, start + 12)[0]
    return _valid_rate(value)


def _mp4_mp4a_rate(data: bytes, start: int, end: int) -> Optional[int]:
    """mp4a 样本条目的 16.16 采样率场。

    该场最大只能表示 65535Hz；96k/192k 等文件可能把这里写成 0，
    实际采样率由 mdhd 或 esds 里的 ASC 给出。
    """
    if start + 28 > end:
        return None
    value = struct.unpack_from(">I", data, start + 24)[0] >> 16
    return _valid_rate(value)


def _descriptor_length(data: bytes, position: int, end: int) -> tuple:
    """读取 MPEG-4 描述符长度；返回 (长度, 长度字段占用的字节数)。"""
    if position >= end:
        return 0, 0
    first = data[position]
    if not (first & 0x80):
        return first, 1
    count = 0
    while count < 3 and first & (0x80 >> count):
        count += 1
    if count == 0 or count > 3 or position + 1 + count > end:
        return 0, 0
    value = first & (0x7F >> count)
    for offset in range(1, count + 1):
        value = (value << 8) | data[position + offset]
    return value, count + 1


def _aac_asc_rate(asc: bytes) -> Optional[int]:
    """从 AudioSpecificConfig 读取解码输出采样率（显式 SBR/PS 取扩展采样率）。

    隐式 SBR 无法只从 ASC 判断，按基础采样率返回。
    """
    if not asc:
        return None
    bit_count = len(asc) * 8
    bits = 0
    for byte in asc:
        bits = (bits << 8) | byte

    def read(count: int):
        nonlocal bits, bit_count
        if count <= 0 or count > bit_count:
            raise ValueError
        value = (bits >> (bit_count - count)) & ((1 << count) - 1)
        bit_count -= count
        return value

    try:
        audio_object_type = read(5)
        if audio_object_type == 31:
            audio_object_type = 32 + read(6)
        rate_index = read(4)
        if rate_index == 15:
            rate = read(24)
        elif rate_index < len(_AAC_SAMPLE_RATES):
            rate = _AAC_SAMPLE_RATES[rate_index]
        else:
            return None
        read(4)  # channelConfiguration
        if audio_object_type in (5, 29):
            extension_index = read(4)
            if extension_index == 15:
                extension = read(24)
            elif extension_index < len(_AAC_SAMPLE_RATES):
                extension = _AAC_SAMPLE_RATES[extension_index]
            else:
                extension = 0
            if extension:
                rate = extension
    except (ValueError, IndexError):
        return None
    return _valid_rate(rate)


def _mp4_esds_asc(data: bytes, start: int, end: int) -> Optional[bytes]:
    """在 esds 负载里找 DecoderSpecificInfo（tag 0x05）并返回 ASC 字节。"""
    position = start + 4                     # FullBox version/flags
    while position < end:
        if data[position] != 0x05:
            position += 1
            continue
        length, count = _descriptor_length(data, position + 1, end)
        content = position + 1 + count
        if length and content + length <= end:
            asc = data[content:content + length]
            if _aac_asc_rate(asc):
                return asc
        position += 1
    return None


_MP4_TOP_LEVEL = {
    b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide",
    b"styp", b"sidx", b"moof", b"uuid", b"pdin", b"junk", b"pnot",
}


def _looks_like_mp4(head: bytes, file_size: int) -> bool:
    """只凭文件头判断是否像 ISO BMFF（M4A/MP4）；不依赖扩展名。"""
    if len(head) < 8 or file_size < 8:
        return False
    size = struct.unpack_from(">I", head, 0)[0]
    kind = head[4:8]
    if kind not in _MP4_TOP_LEVEL:
        return False
    if size == 0:
        return True
    if size == 1:
        return len(head) >= 16
    return 8 <= size <= file_size


def _mp4_sample_rate(path: str) -> Optional[int]:
    """读取 M4A/MP4 音频轨的采样率：ASC 输出率 > mdhd（>65535）> mp4a 场。"""
    try:
        size_total = os.path.getsize(path)
        moov = b""
        with open(path, "rb") as handle:
            position = 0
            while position + 8 <= size_total:
                handle.seek(position)
                header = handle.read(16)
                if len(header) < 8:
                    break
                size = struct.unpack_from(">I", header, 0)[0]
                kind = header[4:8]
                box_header = 8
                if size == 1:
                    if len(header) < 16:
                        break
                    size = struct.unpack_from(">Q", header, 8)[0]
                    box_header = 16
                elif size == 0:
                    size = size_total - position
                if size < box_header or position + size > size_total:
                    break
                if kind == b"moov":
                    handle.seek(position + box_header)
                    moov = handle.read(size - box_header)
                    break
                position += size
            else:
                return None
    except OSError:
        return None
    if not moov:
        return None

    for kind, track_start, track_end in _mp4_boxes(moov, 0, len(moov)):
        if kind != b"trak":
            continue
        mp4a = _mp4_find(moov, track_start, track_end, b"mp4a")
        if mp4a is None:
            continue
        mp4a_rate = _mp4_mp4a_rate(moov, mp4a[0], mp4a[1])
        esds = _mp4_find(moov, mp4a[0] + 28, mp4a[1], b"esds")
        asc_rate = None
        if esds is not None:
            asc = _mp4_esds_asc(moov, esds[0], esds[1])
            if asc is not None:
                asc_rate = _aac_asc_rate(asc)
        mdhd_rate = None
        mdhd = _mp4_find(moov, track_start, track_end, b"mdhd")
        if mdhd is not None:
            mdhd_rate = _mp4_mdhd_timescale(moov, mdhd[0], mdhd[1])
        # ASC 给了输出采样率时最可靠；mp4a 的 16.16 场放不下 >65535Hz，
        # 高采样率文件看 mdhd；其余情况 mp4a 的采样率场优先。
        if asc_rate:
            return asc_rate
        if mdhd_rate and mdhd_rate > 65535:
            return mdhd_rate
        if mp4a_rate:
            return mp4a_rate
        if mdhd_rate:
            return mdhd_rate
    return None


def audio_file_sample_rate(path: str) -> Optional[int]:
    """探测常见音频文件的原始采样率（Hz）；未知格式返回 None。

    只解析文件头/容器结构，不依赖文件扩展名，也不依赖 pygame 的 mixer
    格式。支持的格式：MP3、裸 AAC（ADTS）、M4A/MP4（ftyp/moov/mdat/free
    等顶层盒 + mp4a/ASC/mdhd）、OGG Vorbis、Opus、WAV/RF64、FLAC。
    返回值可能是 22050、32000、88200、96000、192000 等常见采样率；
    未知格式返回 None，调用方沿用当前 mixer 采样率。
    """
    try:
        file_size = os.path.getsize(path)
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return None
    try:
        if head[:4] == b"fLaC":
            return _flac_sample_rate(path)
        if head[:4] == b"OggS":
            return _ogg_sample_rate(path)
        if head[:4] in (b"RIFF", b"RF64"):
            return _wav_sample_rate(path)
        if _looks_like_mp4(head, file_size):
            rate = _mp4_sample_rate(path)
            if rate:
                return rate

        id3 = _id3_size(head)
        if id3 is not None:
            # ID3v2 后面可能是 MP3，也可能是带 ID3 的裸 AAC；按实际音频帧判断
            try:
                with open(path, "rb") as handle:
                    handle.seek(id3)
                    probe = handle.read(8)
            except OSError:
                return None
            if (
                len(probe) >= 2
                and probe[0] == 0xFF
                and (probe[1] & 0xF6) == 0xF0
            ):
                rate = _aac_adts_sample_rate(path)
                if rate:
                    return rate
            rate = _mp3_sample_rate(path)
            if rate:
                return rate
            return _aac_adts_sample_rate(path)

        if len(head) >= 2 and head[0] == 0xFF:
            if (head[1] & 0xF6) == 0xF0:
                return _aac_adts_sample_rate(path)
            return _mp3_sample_rate(path)
    except Exception:  # pragma: no cover - 损坏文件
        return None
    return None


def time_stretch(
    samples,
    rate: float,
    frame: int = STRETCH_FRAME,
    overlap: int = STRETCH_OVERLAP,
    search: int = 64,
    probe: int = 256,
):
    """把 PCM 样本按 1/rate 拉长（rate < 1 变慢、音调随之变低）。

    实现是线性插值重采样 —— 等价于“把采样率按 rate 倍降低后再按原速播放”：
    慢放 0.5x 时长度翻倍、频率减半。比 WSOLA 便宜得多（一次 O(n) 查表），
    单个窗口的合成开销为几毫秒，不需要相似度搜索。
    """
    if _NP is None:  # pragma: no cover - 无 numpy 时直接原样返回
        return samples
    np = _NP
    rate = max(config.RATE_MIN, float(rate))
    if abs(rate - 1.0) < 1e-6:
        return samples
    data = np.asarray(samples)
    squeeze = False
    if data.ndim == 1:
        data = data.reshape(-1, 1)
        squeeze = True
    total = data.shape[0]
    if total < 2:
        return samples
    # 采样位置 = 0, rate, 2*rate, ...（rate<1 时样本被拉开 -> 变慢变低）
    out_len = max(1, int(round(total / rate)))
    index = np.arange(out_len, dtype=np.float32) * rate
    low = np.floor(index).astype(np.int64)
    frac = (index - low)[:, None].astype(np.float32)
    np.clip(low, 0, total - 2, out=low)
    left = data[low].astype(np.float32)
    right = data[low + 1].astype(np.float32)
    out = left + (right - left) * frac
    result = out[:, 0] if squeeze else out
    if data.dtype == np.int16:
        return np.clip(result, -32768, 32767).astype(np.int16)
    return result.astype(data.dtype)


class SilentBackend:
    """不发声的时钟后端：保证没有音频依赖时编辑器依然可用（支持倍速）。"""

    name = "silent"
    supports_seek = True

    def __init__(self) -> None:
        self._start = 0.0
        self._base_ms = 0.0
        self._playing = False
        self._length_ms = 300_000.0
        self.rate = 1.0

    def load(self, path: str) -> bool:
        self._length_ms = max(60_000.0, 300_000.0)
        return True

    def unload(self) -> None:
        self.stop()

    def set_rate(self, rate: float) -> None:
        # 变速时保持“当前位置”不变
        self._base_ms = self.position_ms()
        self._start = time.perf_counter()
        self.rate = max(config.RATE_MIN, float(rate))

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
            return self._base_ms + (time.perf_counter() - self._start) * 1000.0 * self.rate
        return self._base_ms

    def poll(self) -> None:
        return

    def set_volume(self, volume: float) -> None:
        return

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def length_ms(self) -> float:
        return self._length_ms


def _init_mixer(rate: int) -> Tuple[int, int, int]:
    """按指定采样率重新初始化 mixer；返回实际格式（Hz、位深、声道）。"""
    assert _PYGAME is not None
    _PYGAME.mixer.quit()
    _PYGAME.mixer.init(frequency=int(rate), size=-16, channels=2, buffer=1024)
    info = _PYGAME.mixer.get_init()
    if info is None:
        raise RuntimeError("pygame.mixer 初始化失败")
    try:
        # 0 号通道留给歌曲（拉伸后端固定用它），节拍音等自动选空闲通道，
        # 避免“滴”声把正在播放的歌曲顶掉
        _PYGAME.mixer.set_reserved(1)
    except Exception:  # pragma: no cover - 老版本 pygame
        pass
    return int(info[0]), int(info[1]), int(info[2])


def _ensure_mixer() -> Tuple[int, int, int]:
    """确保 mixer 已初始化（sndarray 需要已知格式）。"""
    assert _PYGAME is not None
    info = _PYGAME.mixer.get_init()
    if info is None:
        try:
            return _init_mixer(44100)
        except Exception:
            _PYGAME.mixer.quit()
            return _init_mixer(44100)
    return int(info[0]), int(info[1]), int(info[2])


class PygameBackend:  # pragma: no cover - 依赖 pygame
    """流式播放后端（倍速 1.0x）。"""

    name = "pygame.mixer"
    supports_seek = True

    def __init__(self) -> None:
        assert _PYGAME is not None
        _ensure_mixer()
        self._loaded: Optional[str] = None
        self._volume = 1.0
        self._seek_base_ms = 0.0
        self.rate = 1.0

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

    def set_rate(self, rate: float) -> None:
        # 流式后端不支持变速；倍速由 StretchedSoundBackend 负责
        self._seek_base_ms = self.position_ms()
        self.rate = max(config.RATE_MIN, float(rate))

    def play(self, start_ms: float) -> None:
        self._seek_base_ms = max(0.0, float(start_ms))
        _PYGAME.mixer.music.play(start=self._seek_base_ms / 1000.0)

    def stop(self) -> None:
        _PYGAME.mixer.music.stop()

    def pause(self) -> None:
        _PYGAME.mixer.music.pause()

    def poll(self) -> None:
        return

    def position_ms(self) -> float:
        pos = _PYGAME.mixer.music.get_pos()
        if pos is None or pos < 0:
            return self._seek_base_ms
        return self._seek_base_ms + float(pos)

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
        return 0.0


class StretchedSoundBackend:  # pragma: no cover - 依赖 pygame + numpy
    """重采样播放后端：把 PCM 分窗口按倍速拉伸后用 Sound 播放（慢放音调随之变低）。"""

    name = "pygame+resample"
    supports_seek = True

    def __init__(self) -> None:
        assert _PYGAME is not None and _NP is not None
        self.freq, self.size, self.channels = _ensure_mixer()
        self._samples = None
        self._loaded: Optional[str] = None
        self._volume = 1.0
        self.rate = 1.0
        self._channel = None
        # 播放窗口：当前窗口 + 一个排队窗口（见 poll）
        self._anchor: Optional[Tuple[object, float, float]] = None
        self._anchor_time = 0.0
        self._queued: Optional[Tuple[object, float, float]] = None
        self._handed_end_ms = 0.0
        self._playing = False
        self._start_source_ms = 0.0
        self._start_time = 0.0
        self._paused_ms = 0.0
        self._length_ms = 0.0
        # 预计算的音效轨道（歌曲毫秒 + 种类）：合成窗口时直接混进输出
        self._effects: List[Tuple[float, str]] = []
        self._effect_gains: dict = {}
        self._effect_factory = None
        self._effect_cache: dict = {}
        # 窗口起点往左回看多少：把上一个窗口里开始的音效尾巴接着混完（见 _mix_effects）
        self._effect_margin_ms = EFFECT_TAIL_MS

    # ------------------------------------------------------------------ 载入
    def load(self, path: str) -> bool:
        if not os.path.isfile(path):
            return False
        sound = _PYGAME.mixer.Sound(path)
        array = _PYGAME.sndarray.array(sound)
        self._samples = _NP.ascontiguousarray(array)
        self._loaded = path
        self._length_ms = float(self._samples.shape[0]) / self.freq * 1000.0
        self._queued = None
        self._handed_end_ms = 0.0
        return True

    def unload(self) -> None:
        self.stop()
        self._samples = None
        self._loaded = None
        self._queued = None
        self._handed_end_ms = 0.0

    def set_rate(self, rate: float) -> None:
        self._paused_ms = self.position_ms()
        self.rate = max(config.RATE_MIN, float(rate))
        self._queued = None
        self._handed_end_ms = 0.0

    # ------------------------------------------------------------------ 音效
    def set_effects(self, events, factory=None) -> None:
        """设置预先算好、且已按时间排序的音效轨道：events = [(歌曲毫秒, 种类)]。

        只做一次浅拷贝（不排序、不重建时间数组）——时刻表由上层增量维护，
        避免每次编辑都全量重算。factory(kind, rate, channels) 合成波形。
        """
        previous = self._effects
        self._effects = list(events)
        if factory is not None and factory is not self._effect_factory:
            self._effect_factory = factory
            self._effect_cache.clear()      # 换工厂才需要重合成
            self._effect_margin_ms = EFFECT_TAIL_MS
        if self._playing and self._channel is not None and self._anchor is not None:
            # 变化落在当前窗口还没播到的那一段（例如播放中在判定线前方
            # 放了物件）时，光换排队窗口不够：这一段缓冲里永远不会有它
            position = self.position_ms()
            stop = self._anchor[2]
            if _events_in_range(previous, position, stop) != _events_in_range(
                self._effects, position, stop
            ):
                self._apply_now()           # 变化落在当前窗口：立刻重合成
                return
        self._refresh_queued()

    def set_effect_gains(self, gains) -> None:
        """每种音效的音量（0..1）：与歌曲音量互相独立。"""
        self.set_mix(self._volume, gains)

    def set_mix(self, volume: float, gains) -> None:
        """一次设置歌曲音量 + 各音效音量，混音模式下只重合成一次。

        混音模式把歌曲音量烘进缓冲、音效各自带增益，所以两者只有在下次
        合成窗口时才会变 —— 要立刻生效就得重合成正在播的窗口。
        合并成一个入口是为了“同时改音乐与音效音量”时只重合成一次。
        """
        volume = max(0.0, min(1.0, float(volume)))
        normalized = {str(k): float(v) for k, v in gains.items()}
        if abs(volume - self._volume) < 1e-9 and normalized == self._effect_gains:
            return
        self._volume = volume
        self._effect_gains = normalized
        if self._effect_factory is not None:
            self._apply_now()                   # 混音音量：重合成当前窗口
            return
        # 没有混音：音量直接作用于 Sound 通道，立即生效，不需要重合成
        for window in [w for w in (self._anchor, self._queued) if w is not None]:
            try:
                window[0].set_volume(volume)
            except Exception:
                pass

    def _apply_now(self) -> None:
        """让音量 / 时刻表变化立刻生效：重合成正在播的窗口，再补排队窗口。"""
        if self._playing and self._channel is not None and self._anchor is not None:
            if self._restart_current(self.position_ms()):
                self.poll()                     # 立刻补上下一个排队窗口
                return
        self._refresh_queued()

    def _refresh_queued(self) -> None:
        """用当前的时刻表 / 音量重新合成已排队的窗口。

        正在播的窗口已经交给通道、改不了；排队窗口是马上要播的那一段，
        重新合成它就能让改设置 / 编辑在下一个窗口生效，不必等两个窗口
        （约 16 秒）。改动不频繁，一次重合成的代价可以接受。

        只有确认“锚点窗口还在播、排队窗口还没开始”时才替换：排队窗口一旦
        已经开始播，替换会让同一段音乐播两遍（或整段断流），此时按 poll()
        的规则把它滚动成锚点，改动留到下一个窗口。
        """
        if not self._playing or self._queued is None or self._channel is None:
            return
        try:
            playing = self._channel.get_sound()
        except Exception:  # pragma: no cover - 依赖驱动
            return
        if playing is self._queued[0]:
            due = self._anchor_time + (
                (self._queued[1] - self._anchor[1]) / 1000.0 / max(1e-6, self.rate)
            )
            self._anchor = self._queued
            self._anchor_time = due
            self._queued = None
            return
        if self._anchor is None or playing is not self._anchor[0]:
            return                              # 正在切换：这一轮不替换
        window = self._make_window(self._queued[1])
        if window is None:
            return
        try:
            self._channel.queue(window[0])      # 用当前窗口替换被动队列
        except Exception:  # pragma: no cover - 依赖驱动
            return
        self._queued = window

    def _restart_current(self, position_ms: float) -> bool:
        """从当前位置重新合成正在播的窗口（时刻表在没播到的部分变了）。

        已经交给通道的音频改不了：不重合成的话，播放中在判定线前方新增 /
        删除的音效要么不响、要么多响一声。新窗口从当前位置开始，音乐接着
        往下走；正好在响的那一声会被截掉尾巴（音效只有 45ms，代价很小）。
        """
        window = self._make_window(position_ms)
        if window is None:
            return False
        try:
            self._channel.play(window[0])       # 用当前窗口替换播放队列
        except Exception:  # pragma: no cover - 依赖驱动
            return False
        self._anchor = window
        # 让 position_ms() 在新窗口上仍然返回 position_ms（音乐不跳时间）
        self._anchor_time = time.perf_counter() - (
            (float(position_ms) - window[1]) / 1000.0 / max(1e-6, self.rate)
        )
        self._queued = None
        self._handed_end_ms = window[2]
        return True

    def _effect_data(self, kind: str):
        data = self._effect_cache.get(kind)
        if data is None and self._effect_factory is not None:
            data = self._effect_factory(kind, self.freq, self.channels)
            self._effect_cache[kind] = data
            length_ms = float(data.shape[0]) / self.freq * 1000.0
            self._effect_margin_ms = max(self._effect_margin_ms, length_ms)
        return data

    def _mix_effects(self, output, start_ms: float, stop_ms: float) -> None:
        """把落在这一窗口里的音效混进输出缓冲。

        音效按自己的采样长度叠加（不参与歌曲的时间伸缩），所以慢放时
        “滴”声依然是 45ms；输出位置 = (音效时刻 − 窗口起点) / 倍速。
        窗口起点左侧 音效长度 × 倍速 内开始的音效也要回看：它们在本窗口
        里只剩尾巴，不回看就会被窗口边界截断。
        """
        if not self._effects or self._effect_factory is None or output.size == 0:
            return
        rate = max(1e-6, float(self.rate))
        index = bisect.bisect_left(
            self._effects, start_ms - self._effect_margin_ms * rate, key=lambda item: item[0]
        )
        total = int(output.shape[0])
        for position in range(index, len(self._effects)):
            song_ms, kind = self._effects[position]
            if song_ms >= stop_ms:
                break
            data = self._effect_data(kind)
            if data is None:
                continue
            offset = int(round((song_ms - start_ms) / rate * self.freq / 1000.0))
            skip = 0
            if offset < 0:                       # 上一个窗口就开始的音效：接尾巴
                skip = min(int(data.shape[0]), -offset)
                offset = 0
            if offset >= total:
                break
            count = min(int(data.shape[0]) - skip, total - offset)
            if count <= 0:
                continue
            gain = float(self._effect_gains.get(kind, 1.0))
            piece = output[offset:offset + count].astype(_NP.int32)
            mix = data[skip:skip + count].astype(_NP.int32)
            if abs(gain - 1.0) > 1e-9:
                mix = (mix * gain).astype(_NP.int32)
            output[offset:offset + count] = _NP.clip(
                piece + mix, -32768, 32767
            ).astype(_NP.int16)

    # ------------------------------------------------------------------ 播放
    def _make_window(self, start_ms: float):
        if self._samples is None:
            return None
        start = max(0, int(round(start_ms / 1000.0 * self.freq)))
        if start >= self._samples.shape[0]:
            return None
        # 源窗口大小随倍速缩放：输出窗口长度大致固定，单次合成开销可控
        window_ms = stretch_window_ms(self.rate)
        stop = min(
            int(self._samples.shape[0]),
            start + int(round(window_ms / 1000.0 * self.freq)),
        )
        if stop - start < max(2, min(STRETCH_FRAME, int(round(self.freq * 0.05)))):
            return None
        chunk = self._samples[start:stop]
        stretched = time_stretch(chunk, self.rate)
        volume = max(0.0, min(1.0, self._volume))
        if self._effect_factory is not None:
            # 音效混音模式：歌曲音量烘进缓冲，音效按各自音量叠加（两者互不影响）；
            # 通道 / Sound 音量固定 1.0，避免把音效一起缩放
            stretched = _NP.array(stretched, copy=True)
            if volume < 0.999:
                stretched = _NP.clip(
                    stretched.astype(_NP.float32) * volume, -32768, 32767
                ).astype(_NP.int16)
            self._mix_effects(
                stretched, start / self.freq * 1000.0, stop / self.freq * 1000.0
            )
            volume = 1.0
        sound = _PYGAME.sndarray.make_sound(_NP.ascontiguousarray(stretched))
        sound.set_volume(volume)
        return sound, start / self.freq * 1000.0, stop / self.freq * 1000.0

    def play(self, start_ms: float) -> None:
        if self._samples is None:
            return
        self.stop()
        first = self._make_window(start_ms)
        if first is None:
            return
        self._channel = _PYGAME.mixer.Channel(0)
        self._channel.play(first[0])
        self._anchor = first                   # 正在播的窗口
        self._anchor_time = time.perf_counter()
        self._queued = None                    # 已排队的下一个窗口（通道只有一个槽位）
        self._handed_end_ms = first[2]         # 已经交给通道的音频末端（严格单调）
        self._playing = True
        self.poll()

    def poll(self) -> None:
        """每帧调用：维护“当前 + 一个排队”的窗口，保证连续、不重不漏。

        关键：必须等排队的那一段真的开始播了，才把槽位让给下一段。
        只看时间近似判断会让下一段还没开始播就被 Channel.queue() 顶掉，
        那一段就再也听不到（表现就是音乐漏片段），所以以实际播放位置为准。
        """
        if not self._playing or self._channel is None or self._anchor is None:
            return
        now = time.perf_counter()
        try:
            playing = self._channel.get_sound()
        except Exception:  # pragma: no cover - 依赖驱动
            playing = None
        try:
            busy = bool(self._channel.get_busy())
        except Exception:  # pragma: no cover
            busy = True
        # 1) 排队的窗口真的开始播了：换锚点、腾出槽位
        if self._queued is not None:
            due = self._anchor_time + (
                (self._queued[1] - self._anchor[1]) / 1000.0 / max(1e-6, self.rate)
            )
            if playing is self._queued[0]:
                self._anchor = self._queued
                self._anchor_time = due
                self._queued = None
            elif not busy and now > due + 0.25:
                # 通道彻底空了、排队段始终没播出来：从它的位置继续，别停在这里
                self._anchor = self._queued
                self._anchor_time = now
                self._queued = None
        # 2) 断流检测：通道空了且没有排队段 -> 立刻补上
        if not busy and self._queued is None:
            nxt = self._make_window(self._handed_end_ms)
            if nxt is not None:
                self._channel.play(nxt[0])
                self._anchor = nxt
                self._anchor_time = now
                self._queued = None
                self._handed_end_ms = nxt[2]
            return
        # 3) 槽位空着就补一个排队窗口（只有上一段已经在播时才会走到这里）
        if self._queued is None:
            nxt = self._make_window(self._handed_end_ms)
            if nxt is not None:
                self._channel.queue(nxt[0])
                self._queued = nxt
                self._handed_end_ms = nxt[2]

    def stop(self) -> None:
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:
                pass
        self._channel = None
        self._queued = None
        self._anchor = None
        self._playing = False
        self._paused_ms = 0.0

    def pause(self) -> None:
        self._paused_ms = self.position_ms()
        if self._channel is not None:
            try:
                self._channel.stop()
            except Exception:
                pass
        self._channel = None
        self._queued = None
        self._anchor = None
        self._playing = False

    def position_ms(self) -> float:
        if not self._playing or self._anchor is None:
            return self._paused_ms
        elapsed = (time.perf_counter() - self._anchor_time) * 1000.0 * self.rate
        position = self._anchor[1] + elapsed
        # 不超过当前窗口末端，也不超过已交给通道的音频末端
        return min(position, self._anchor[2], self._length_ms)

    def set_volume(self, volume: float) -> None:
        self.set_mix(volume, self._effect_gains)

    @property
    def is_playing(self) -> bool:
        if not self._playing:
            return False
        if self._channel is None:
            return False
        try:
            return bool(self._channel.get_busy())
        except Exception:
            return self._playing

    @property
    def length_ms(self) -> float:
        return self._length_ms


class AudioPlayer:
    """对上层暴露的音频接口：以“歌曲原始毫秒位置”为单位。"""

    def __init__(self) -> None:
        self.rate = config.DEFAULT_RATE
        self.path: Optional[str] = None
        self.latency_ms = 0.0
        self._playing = False
        self._paused_at_ms = 0.0
        # 歌曲音量（重采样后端重建后要重新套用）
        self._volume = 1.0
        # 出问题时的说明（界面会显示）
        self.last_error = ""
        # 上次改倍速时是否因为正在播放而自动暂停
        self.paused_by_rate_change = False
        # 没有音频文件时用静音时钟推进（pygame.music 没有载入文件时不会走时间）
        self._silent_clock = SilentBackend()
        self._plain_backend = self._create_plain_backend()
        self._stretch_backend = None
        self.backend = self._plain_backend
        # 预计算的音效轨道（编辑器用）：[(歌曲毫秒, 种类)] + 各自音量 + 波形工厂
        self._effects: List[Tuple[float, str]] = []
        self._effect_gains: dict = {}
        self._effect_factory = None

    # ------------------------------------------------------------------ 后端
    @staticmethod
    def _create_plain_backend():
        global _MIXER_ERROR
        if _PYGAME is not None:
            try:
                backend = PygameBackend()
            except Exception as exc:  # pragma: no cover - 依赖音频设备
                _MIXER_ERROR = f"{type(exc).__name__}: {exc}".strip()
            else:
                _MIXER_ERROR = None
                return backend
        return SilentBackend()

    def _rebuild_backends(self) -> None:
        """按当前 mixer 格式重建后端。

        采样率切换后旧的 Sound 缓存全部失效，必须丢弃重来。
        """
        self._plain_backend = self._create_plain_backend()
        self._stretch_backend = None
        self.backend = self._plain_backend

    def _configure_mixer_for(self, path: str) -> bool:
        """按音频文件的原始采样率初始化 mixer，避免 SDL 重采样改变播放速度。

        SDL 的 48k↔44.1k 重采样实测会把歌曲时长改变约 0.05%～0.09%，
        对长歌来说就是逐渐累积的音画偏移。能按文件原生采样率播放时就
        不要让 mixer 重采样。
        """
        global _MIXER_ERROR
        if _PYGAME is None:
            return False
        native = audio_file_sample_rate(path)
        if not native:
            return False
        info = _PYGAME.mixer.get_init()
        current = int(info[0]) if info else None
        if current == native:
            return False
        try:
            actual = _init_mixer(native)
        except Exception as exc:  # pragma: no cover - 依赖音频设备
            # 原生采样率打不开时退回原采样率，宁可重采样也不要丢播放设备
            try:
                if current:
                    _init_mixer(current)
                else:
                    _ensure_mixer()
            except Exception:
                pass
            _MIXER_ERROR = f"{type(exc).__name__}: {exc}".strip()
            self.last_error = (
                f"无法按 {native} Hz 打开音频设备，已回退；"
                "音频可能被重采样并产生轻微时序漂移"
            )
            self._rebuild_backends()
            return False
        if actual[0] != native:
            self.last_error = (
                f"音频设备实际采样率 {actual[0]} Hz（文件为 {native} Hz），"
                "音频可能被重采样并产生轻微时序漂移"
            )
        else:
            self.last_error = ""
            _MIXER_ERROR = None
        self._rebuild_backends()
        return True

    def _select_backend(self, path: Optional[str] = None):
        # path 传入“即将载入的歌曲”：load() 会先把 self.path 清空，
        # 只按 self.path 判断会把刚载入的歌曲误判成“没有歌曲”而退回静音时钟
        # （表现就是打开谱面后第一次播放没有声音）。
        target = self.path if path is None else path
        if not target:
            return self._silent_clock
        # 只要不是 1.0x 就必须用重采样后端（< 1 慢放，> 1 加速）；
        # 启用音效混音时也必须用它（普通流式后端无法把音效混进输出）
        want_stretch = abs(self.rate - 1.0) > 1e-9 or self._effect_factory is not None
        if want_stretch and STRETCH_AVAILABLE:
            if self._stretch_backend is None:
                try:
                    self._stretch_backend = StretchedSoundBackend()
                except Exception:  # pragma: no cover - 无音频设备
                    self._stretch_backend = None
            if self._stretch_backend is not None:
                return self._stretch_backend
        return self._plain_backend

    @property
    def stretch_active(self) -> bool:
        return isinstance(self.backend, StretchedSoundBackend)

    # ------------------------------------------------------------------ 音效
    def enable_effects(self, factory) -> None:
        """启用“音效混音”路径：后端优先选择可混音的重采样后端。

        factory(kind, sample_rate, channels) 按种类合成波形。
        """
        self._effect_factory = factory
        backend = self._select_backend()
        if backend is self.backend:
            self._sync_effects()
            self._apply_volume()
            return
        position = self.position_ms()
        was_playing = self._playing
        self._stop_all_backends()
        self._reload_backend(backend)
        self._paused_at_ms = position
        self._playing = False
        if was_playing and self.path:
            self.play(position)

    def set_effects(self, events) -> None:
        """设置预先算好、已排序的音效轨道：events = [(歌曲毫秒, 种类)]。"""
        self._effects = list(events)
        self._sync_effects()

    def set_effect_gains(self, gains) -> None:
        """音效各自的音量（0..1）：与歌曲音量互相独立。"""
        self._effect_gains = {str(k): float(v) for k, v in gains.items()}
        if isinstance(self.backend, StretchedSoundBackend):
            self.backend.set_effect_gains(self._effect_gains)

    def set_mix(self, volume: float, gains) -> None:
        """一次设置歌曲音量 + 音效音量：混音模式下只重合成一次窗口。"""
        self._volume = float(volume)
        self._effect_gains = {str(k): float(v) for k, v in gains.items()}
        if isinstance(self.backend, StretchedSoundBackend):
            try:
                self.backend.set_mix(float(volume), self._effect_gains)
            except Exception:                        # pragma: no cover
                pass
            return
        self.set_volume(volume)

    def _sync_effects(self) -> None:
        if isinstance(self.backend, StretchedSoundBackend):
            self.backend.set_effects(self._effects, self._effect_factory)
            self.backend.set_effect_gains(self._effect_gains)

    def _reload_backend(self, backend) -> None:
        """换后端：载入当前歌曲、套用倍速，并把音效轨道同步过去。"""
        self.backend = backend
        if self.path:
            try:
                if not backend.load(self.path):      # pragma: no cover - 载入失败
                    self.backend = self._plain_backend
            except Exception:                        # pragma: no cover
                self.backend = self._plain_backend
        try:
            self.backend.set_rate(self.rate)
        except Exception:                            # pragma: no cover
            pass
        self._sync_effects()
        self._apply_volume()

    def _apply_volume(self) -> None:
        """把当前歌曲 / 音效音量套用到当前后端。"""
        try:
            self.backend.set_volume(float(self._volume))
        except Exception:                            # pragma: no cover
            pass
        if isinstance(self.backend, StretchedSoundBackend):
            try:
                self.backend.set_mix(float(self._volume), self._effect_gains)
            except Exception:                        # pragma: no cover
                pass

    @property
    def length_ms(self) -> float:
        """当前后端知道的歌曲长度（毫秒；未知时为 0）。"""
        try:
            return float(self.backend.length_ms)
        except Exception:                            # pragma: no cover
            return 0.0

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
            if not self.usable_audio:
                if audio_backend_state() == "ok":
                    return (
                        f"音频后端：{audio_backend_name()}"
                        f"（未载入歌曲）· {config.format_rate(self.rate)}"
                    )
                return f"音频后端：silent（{_silent_reason()}）· {config.format_rate(self.rate)}"
            text = f"音频后端：{self.backend.name}（未载入歌曲）· {config.format_rate(self.rate)}"
            return f"{text} · {self.last_error}" if self.last_error else text
        base = os.path.basename(self.path)
        if not self.usable_audio:
            return f"音频后端：silent（{_silent_reason()}）· {base} · {config.format_rate(self.rate)}"
        tone = "· 变调" if self.stretch_active and abs(self.rate - 1.0) > 1e-9 else ""
        text = f"音频后端：{self.backend.name} · {base} · {config.format_rate(self.rate)}{tone}"
        return f"{text} · {self.last_error}" if self.last_error else text

    # ------------------------------------------------------------------ 载入
    def load(self, path: Optional[str]) -> bool:
        self.stop()
        self.path = None
        self.last_error = ""
        if path and os.path.isfile(path):
            self._configure_mixer_for(path)
        self.backend = self._select_backend(path)
        if not path or not os.path.isfile(path):
            # 未载入歌曲：用静音时钟，播放时判定线照常前进（不会一点播放就立刻“结束”）
            self.backend = self._silent_clock
            self.backend.set_rate(self.rate)
            return False
        try:
            ok = self.backend.load(path)
        except Exception:
            ok = False
        if not ok and self.backend is not self._plain_backend:
            # 拉伸后端失败时退回普通后端
            self.backend = self._plain_backend
            try:
                ok = self.backend.load(path)
            except Exception:
                ok = False
        if ok:
            self.path = path
            self.backend.set_rate(self.rate)
            self._sync_effects()
            self._apply_volume()
        return ok

    def unload(self) -> None:
        """停止并释放当前音频文件（Windows 下会解除文件占用）。"""
        self.stop()
        for backend in (self._plain_backend, self._stretch_backend):
            if backend is None:
                continue
            try:
                backend.unload()
            except Exception:
                pass
        self.path = None

    # ------------------------------------------------------------------ 倍速
    def set_rate(self, rate: float) -> None:
        """设置播放倍速（下限 config.RATE_MIN）：慢放通过重采样实现，音调随之变低。"""
        rate = round(max(config.RATE_MIN, min(config.RATE_LIMIT_MAX, float(rate))), 2)
        if abs(rate - self.rate) < 1e-9:
            return
        was_playing = self._playing
        position = self.position_ms()
        self.rate = rate
        # 切换后端前先停掉当前后端，避免两路声音同时输出
        self._stop_all_backends()
        backend = self._select_backend()
        if backend is not self.backend:
            self._reload_backend(backend)
        else:
            try:
                self.backend.set_rate(self.rate)
            except Exception:  # pragma: no cover
                pass
            self._sync_effects()
        self._paused_at_ms = position
        self._playing = False
        # 播放中切倍速会切换后端（普通 ↔ 重采样），两段音频很难无缝接力：
        # 这里一律自动暂停在当前位置，按空格即可用新倍速继续（界面会提示）。
        self.paused_by_rate_change = bool(was_playing)

    # ------------------------------------------------------------------ 控制
    def play(self, song_ms: float) -> None:
        start = max(0.0, float(song_ms))
        try:
            self.backend.play(start)
        except Exception as exc:  # pragma: no cover - 后端异常
            # 拉伸合成失败时退回普通播放（听得见，只是不慢放），
            # 不静默切成静音时钟（那样音乐就“没了”）
            self._fallback_plain(f"慢放合成失败（{exc}），已回到 1.0x 播放")
            try:
                self.backend.play(start)
            except Exception:
                self.backend = SilentBackend()
                self.backend.set_rate(self.rate)
                self.backend.play(start)
        self._playing = True
        self._paused_at_ms = start

    def pause(self) -> float:
        """暂停并返回当前歌曲位置（毫秒）。"""
        position = self.position_ms()
        self._stop_all_backends()
        self._playing = False
        self._paused_at_ms = position
        return position

    def stop(self) -> None:
        self._stop_all_backends()
        self._playing = False
        self._paused_at_ms = 0.0

    def _stop_all_backends(self) -> None:
        """把所有后端都停掉。

        切换倍速（拉伸后端 ↔ 普通后端）时，被换下的后端也要停：只停当前后端的话
        它还会继续出声（听感是“按了暂停 / 停止以后声音还响一会儿”）。这里连同
        队列里的窗口一起清掉，并停掉混音器里所有通道，确保立刻静音。
        """
        for backend in (self.backend, self._plain_backend, self._stretch_backend):
            if backend is None:
                continue
            try:
                backend.stop()
            except Exception:
                pass
        if _PYGAME is not None:
            try:
                _PYGAME.mixer.stop()      # 连排队等待播放的窗口一起清空
            except Exception:  # pragma: no cover
                pass

    def poll(self) -> None:
        """每帧调用：拉伸后端用它提前准备后续窗口。"""
        try:
            self.backend.poll()
        except Exception as exc:  # pragma: no cover
            self._fallback_plain(f"慢放合成失败（{exc}），已回到 1.0x 播放")

    def _fallback_plain(self, reason: str) -> None:
        """拉伸后端出问题时退回普通后端（保持可听，并把原因告诉界面）。"""
        if self.backend is self._plain_backend:
            return
        was_playing = self._playing
        position = self.position_ms()
        self.rate = config.RATE_MAX
        self.backend = self._plain_backend
        if self.path:
            try:
                self._plain_backend.load(self.path)
                self._plain_backend.set_rate(1.0)
            except Exception:
                pass
        self.last_error = reason
        self._playing = False
        self._apply_volume()
        if was_playing and self.path:
            try:
                self.play(position)
            except Exception:
                pass

    def set_volume(self, volume: float) -> None:
        self._volume = float(volume)
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
            # 延迟是真实时间：慢放时真实 1ms 只推进 rate ms 的歌曲时间，
            # 所以要按倍速缩放，否则延迟补偿会被倍速放大/缩小。
            position -= self.latency_ms * self.rate
        return max(0.0, position)

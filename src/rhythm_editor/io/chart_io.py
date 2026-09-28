"""谱面 JSON 读写与音频复制。

音频策略：选择音频文件后，把歌曲复制到 **谱面文件所在文件夹** 的 ``audio/`` 子目录，
谱面里记录相对路径（``audio/song.mp3``），保证谱面可以整体搬迁。
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, Optional, Tuple

from .. import config
from ..model.chart import Chart


def ensure_chart_suffix(path: str) -> str:
    """保证谱面文件名以 ``.json`` 结尾（只支持 JSON 格式）。"""
    root, ext = os.path.splitext(path)
    if ext.lower() == config.CHART_EXTENSION:
        return path
    # 写成别的后缀（含旧的 .ivchart）时统一改成 .json
    return root + config.CHART_EXTENSION


def default_chart_dir(path: str) -> str:
    return os.path.dirname(os.path.abspath(path)) or os.getcwd()


def save_chart(chart: Chart, path: str) -> str:
    """保存谱面，返回实际写入的路径。"""
    path = ensure_chart_suffix(path)
    folder = default_chart_dir(path)
    os.makedirs(folder, exist_ok=True)
    data = chart.to_dict()
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def load_chart(path: str) -> Tuple[Chart, Dict[str, Any]]:
    """读取谱面，返回 (谱面, 原始字典)。"""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("谱面文件格式错误：根节点必须是对象")
    chart = Chart.from_dict(data)
    return chart, data


def choose_audio_relative_path(audio_path: str) -> str:
    """把绝对/外部音频路径转换为谱面内的相对路径。"""
    return os.path.basename(audio_path)


def copy_audio_into_chart_dir(audio_path: str, chart_path: str) -> str:
    """复制歌曲到**谱面文件所在文件夹**，返回谱面内记录的文件名。

    如果谱面尚未保存（``chart_path`` 为空），使用当前工作目录下的 ``charts/unsaved``。
    """
    if not audio_path:
        return ""
    source = os.path.abspath(audio_path)
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if chart_path:
        folder = default_chart_dir(chart_path)
    else:
        folder = os.path.abspath(os.path.join(os.getcwd(), "charts", "unsaved"))
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, os.path.basename(source))
    if os.path.abspath(source) != os.path.abspath(target):
        shutil.copy2(source, target)
    return choose_audio_relative_path(target)


def chart_audio_path(chart_path: Optional[str], chart: Chart) -> Optional[str]:
    """返回谱面对应音频的绝对路径（不存在时返回 None）。"""
    rel = (chart.metadata.audio_file or "").strip()
    if not rel:
        return None
    candidates = []
    if os.path.isabs(rel):
        candidates.append(rel)
    else:
        if chart_path:
            candidates.append(os.path.join(default_chart_dir(chart_path), rel))
        candidates.append(os.path.abspath(rel))
        base = os.path.basename(rel)
        if chart_path:
            # 兼容把音频放在 audio/ 子目录里的写法
            candidates.append(os.path.join(default_chart_dir(chart_path), "audio", base))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None

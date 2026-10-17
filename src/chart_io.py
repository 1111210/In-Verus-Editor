"""谱面 JSON 读写与音频复制。

音频策略：选择音频文件后，把歌曲复制到谱面文件所在文件夹的 audio/ 子目录，
谱面里记录相对路径（audio/song.mp3），保证谱面可以整体搬迁。
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Dict, List, Mapping, Optional, Tuple

import config
from model.chart import Chart


class ChartDataError(ValueError):
    """谱面根结构或可识别内容不足，加载应被拒绝。"""


_ROOT_KEYS = {
    "format", "version", "metadata", "timing", "speed_groups",
    "lanes", "mouse",
}
_METADATA_KEYS = {
    "title", "artist", "difficulty_name", "difficulty_level", "author", "audio_file",
}
_TIMING_KEYS = {"offset_ms", "bpm_segments"}
_BPM_KEYS = {"start_beat", "bpm", "beats_per_bar"}
_GROUP_KEYS = {"id", "name", "segments", "note_ids"}
_SPEED_KEYS = {"start_beat", "multiplier"}
_COMMON_NOTE_KEYS = {"id", "type", "group"}
_NOTE_KEYS_BY_TYPE = {
    "lane": {"lane_start", "lane_end", "beat_start", "beat_end"},
    "region": {"beat_start", "beat_end", "left", "right"},
    "forbidden": {"beat_start", "beat_end", "left", "right"},
    "slide": {"beat", "x_start", "x_end"},
}
_CURVE_KEYS = {"start", "end", "ease"}


def _unknown_keys(
    mapping: Mapping[str, Any], allowed: set[str], path: str, warnings: List[str]
) -> None:
    for key in mapping:
        if key not in allowed:
            warnings.append(f"{path}.{key}：未识别字段，保存时保留。")


def _inspect_chart_data(data: Dict[str, Any]) -> List[str]:
    """收集未识别字段和明显的类型异常，不阻止可能的物件继续加载。"""
    warnings: List[str] = []
    _unknown_keys(data, _ROOT_KEYS, "<root>", warnings)

    if "format" in data and not isinstance(data.get("format"), str):
        warnings.append("<root>.format：预期字符串，保存时使用规范值。")
    elif "format" in data and data.get("format") != config.CHART_FORMAT:
        warnings.append("<root>.format：不是当前谱面格式，按可识别 JSON 继续加载。")
    if "version" in data and not isinstance(data.get("version"), (int, float)):
        warnings.append("<root>.version：预期数字，保存时使用规范值。")

    metadata = data.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            warnings.append("<root>.metadata：预期对象，已使用默认元数据。")
        else:
            _unknown_keys(metadata, _METADATA_KEYS, "metadata", warnings)
            for key in _METADATA_KEYS:
                if key in metadata and not isinstance(metadata[key], str):
                    warnings.append(f"metadata.{key}：预期字符串，加载时会转换。")

    timing = data.get("timing")
    if timing is not None:
        if not isinstance(timing, dict):
            warnings.append("<root>.timing：预期对象，已使用默认时间轴。")
        else:
            _unknown_keys(timing, _TIMING_KEYS, "timing", warnings)
            if "offset_ms" in timing and not isinstance(timing.get("offset_ms"), (int, float)):
                warnings.append("timing.offset_ms：预期数字。")
            segments = timing.get("bpm_segments")
            if segments is not None and not isinstance(segments, list):
                warnings.append("timing.bpm_segments：预期数组。")
            elif isinstance(segments, list):
                for index, segment in enumerate(segments):
                    if not isinstance(segment, dict):
                        warnings.append(f"timing.bpm_segments[{index}]：预期对象，已跳过。")
                        continue
                    _unknown_keys(
                        segment, _BPM_KEYS, f"timing.bpm_segments[{index}]", warnings
                    )
                    for key in ("start_beat", "bpm", "beats_per_bar"):
                        if key in segment and not isinstance(segment.get(key), (int, float)):
                            warnings.append(
                                f"timing.bpm_segments[{index}].{key}：预期数字。"
                            )

    groups = data.get("speed_groups")
    if groups is not None and not isinstance(groups, list):
        warnings.append("<root>.speed_groups：预期数组，已使用默认速度组。")
    elif isinstance(groups, list):
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                warnings.append(f"speed_groups[{index}]：预期对象，已跳过。")
                continue
            _unknown_keys(group, _GROUP_KEYS, f"speed_groups[{index}]", warnings)
            if "id" in group and not isinstance(group.get("id"), int):
                warnings.append(f"speed_groups[{index}].id：预期整数。")
            if "name" in group and not isinstance(group.get("name"), str):
                warnings.append(f"speed_groups[{index}].name：预期字符串。")
            if "note_ids" in group and not isinstance(group.get("note_ids"), list):
                warnings.append(f"speed_groups[{index}].note_ids：预期数组。")
            segments = group.get("segments")
            if segments is not None and not isinstance(segments, list):
                warnings.append(f"speed_groups[{index}].segments：预期数组。")
            elif isinstance(segments, list):
                for seg_index, segment in enumerate(segments):
                    if not isinstance(segment, dict):
                        warnings.append(
                            f"speed_groups[{index}].segments[{seg_index}]：预期对象，已跳过。"
                        )
                        continue
                    _unknown_keys(
                        segment,
                        _SPEED_KEYS,
                        f"speed_groups[{index}].segments[{seg_index}]",
                        warnings,
                    )
                    for key in _SPEED_KEYS:
                        if key in segment and not isinstance(segment.get(key), (int, float)):
                            warnings.append(
                                f"speed_groups[{index}].segments[{seg_index}].{key}：预期数字。"
                            )

    for section in ("lanes", "mouse"):
        notes = data.get(section)
        if notes is not None and not isinstance(notes, list):
            warnings.append(f"<root>.{section}：预期数组，已跳过。")
            continue
        if not isinstance(notes, list):
            continue
        for index, note in enumerate(notes):
            path = f"{section}[{index}]"
            if not isinstance(note, dict):
                warnings.append(f"{path}：预期对象，已跳过。")
                continue
            note_type = note.get("type")
            allowed = _COMMON_NOTE_KEYS | _NOTE_KEYS_BY_TYPE.get(str(note_type), set())
            if note_type not in _NOTE_KEYS_BY_TYPE:
                warnings.append(f"{path}.type：未识别物件类型，已跳过。")
            _unknown_keys(note, allowed, path, warnings)
            if "id" in note and not isinstance(note.get("id"), str):
                warnings.append(f"{path}.id：预期字符串。")
            if "group" in note and not isinstance(note.get("group"), int):
                warnings.append(f"{path}.group：预期整数。")
            numeric_keys = {
                "lane": ("lane_start", "lane_end", "beat_start", "beat_end"),
                "region": ("beat_start", "beat_end"),
                "forbidden": ("beat_start", "beat_end"),
                "slide": ("beat", "x_start", "x_end"),
            }.get(str(note_type), ())
            for key in numeric_keys:
                if key in note and not isinstance(note.get(key), (int, float)):
                    warnings.append(f"{path}.{key}：预期数字。")
            for curve_key in ("left", "right"):
                curve = note.get(curve_key)
                if curve is None:
                    continue
                if not isinstance(curve, dict):
                    warnings.append(f"{path}.{curve_key}：预期对象。")
                else:
                    _unknown_keys(curve, _CURVE_KEYS, f"{path}.{curve_key}", warnings)
                    for key in ("start", "end"):
                        if key in curve and not isinstance(curve.get(key), (int, float)):
                            warnings.append(f"{path}.{curve_key}.{key}：预期数字。")
                    if "ease" in curve and not isinstance(curve.get("ease"), str):
                        warnings.append(f"{path}.{curve_key}.ease：预期字符串。")
    return warnings


def _has_recognized_root(data: Dict[str, Any]) -> bool:
    """判断文件是否至少有一个符合预期类型的顶层结构。"""
    return (
        isinstance(data.get("format"), str)
        or isinstance(data.get("version"), (int, float))
        or isinstance(data.get("metadata"), dict)
        or isinstance(data.get("timing"), dict)
        or isinstance(data.get("speed_groups"), list)
        or isinstance(data.get("lanes"), list)
        or isinstance(data.get("mouse"), list)
    )


def ensure_chart_suffix(path: str) -> str:
    """保证谱面文件名以 .json 结尾（只支持 JSON 格式）。"""
    root, ext = os.path.splitext(path)
    if ext.lower() == config.CHART_EXTENSION:
        return path
    # 其它后缀（含 .ivchart）统一写成 .json
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
    """容错读取谱面，返回 (谱面, 原始字典)。

    可识别内容会继续加载；脏字段和跳过的对象记录在 ``chart.load_warnings``。
    文件不存在、JSON 无效或完全无法识别时抛出异常。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"文件未找到：{path}")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ChartDataError(
            f"不是有效的 JSON：第 {exc.lineno} 行第 {exc.colno} 列，{exc.msg}"
        ) from exc
    if not isinstance(data, dict):
        raise ChartDataError("谱面文件格式错误：根节点必须是 JSON 对象。")
    if data and not _has_recognized_root(data):
        raise ChartDataError(
            "没有识别到任何谱面字段、物件、BPM、速度组或元数据；文件可能不是本编辑器谱面。"
        )
    chart = Chart.from_dict(data)
    warnings = _inspect_chart_data(data)
    for warning in warnings:
        if warning not in chart.load_warnings:
            chart.load_warnings.append(warning)
    if data and not _has_recognized_root(data) and not chart.all_notes():
        raise ChartDataError(
            "所有字段都不符合预期，且没有加载到任何物件、BPM、速度组或元数据。"
        )
    return chart, data


def choose_audio_relative_path(audio_path: str) -> str:
    """把绝对/外部音频路径转换为谱面内的相对路径。"""
    return os.path.basename(audio_path)


def copy_audio_into_chart_dir(audio_path: str, chart_path: str) -> str:
    """复制歌曲到谱面文件所在文件夹，返回谱面内记录的文件名。

    如果谱面尚未保存（chart_path 为空），使用当前工作目录下的 charts/unsaved。
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
            # 音频也可位于谱面目录下的 audio/ 子目录
            candidates.append(os.path.join(default_chart_dir(chart_path), "audio", base))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None

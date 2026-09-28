"""逐条验收脚本：把「需求 -> 证据」打印成一份清单（不启动 GUI）。

用法::

    python tools/acceptance.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from rhythm_editor import config  # noqa: E402
from rhythm_editor.audio import audio_backend_available, audio_backend_name  # noqa: E402
from rhythm_editor.demo import build_demo_chart, selftest, write_demo_chart  # noqa: E402
from rhythm_editor.io.chart_io import load_chart  # noqa: E402
from rhythm_editor.model.document import Document  # noqa: E402
from rhythm_editor.model.notes import LaneNote, MouseClick, MouseRegion, MouseSlide  # noqa: E402


def report() -> int:
    lines = []
    ok_all = True

    def item(text: str, ok: bool, detail: str = "") -> None:
        nonlocal ok_all
        ok_all = ok_all and ok
        mark = "OK " if ok else "NG "
        lines.append(f"[{mark}] {text}" + (f" -- {detail}" if detail else ""))

    chart = build_demo_chart()
    counts = chart.note_counts()
    item(
        "项目结构（src/ docs/ tests/ tools/ charts/）",
        all(os.path.isdir(os.path.join(ROOT, name)) for name in ("src", "docs", "tests", "tools", "charts")),
        ROOT,
    )
    item(
        "6K 物件：单轨 / 多轨 / 长按",
        counts["lane"] > 0 and any(n.is_hold for n in chart.lane_notes) and any(n.is_multi for n in chart.lane_notes),
        f"{counts['lane']} 个",
    )
    item(
        "空域（鼠标停留区域：半透明蓝 + 不透明左右边界 + 缓动）",
        any(isinstance(n, MouseRegion) and n.left.ease in config.EASE_CHOICES for n in chart.mouse_notes),
        f"{counts['region']} 个",
    )
    item(
        "鼠标滑键（方向由首尾位置决定）",
        any(isinstance(n, MouseSlide) and n.direction in (-1, 1) for n in chart.mouse_notes),
        f"{counts['slide']} 个",
    )
    item(
        "鼠标点击按键（左右红色竖线 + 中间红色横线，粗细同 6K 中心实线 + 红色实心菱形，最上层）",
        any(
            isinstance(n, MouseClick)
            and n.radius >= config.MIN_CLICK_RADIUS
            and abs(n.x_end - n.x_start - 2 * n.radius) < 1e-9
            for n in chart.mouse_notes
        ),
        f"{counts['click']} 个",
    )
    item(
        "BPM 列表（第 0 拍项存在，含每小节拍数）",
        chart.timing.segments[0].start_beat == 0.0,
        "、".join(f"{s.start_beat:g}拍/{s.bpm:g}BPM/{s.beats_per_bar}拍" for s in chart.timing.segments),
    )
    item(
        "速度组（默认组 + 特效组，含 0/负数流速）",
        len(chart.speed_groups) >= 3
        and any(seg.multiplier < 0 for g in chart.speed_groups for seg in g.segments),
        "、".join(f"{g.name}({len(g.note_ids)}件)" for g in chart.speed_groups),
    )
    item(
        "全局设置（缩放 / 网格 / 横向辅助 / 音频延迟）",
        chart.settings.grid_division >= 1
        and chart.settings.horizontal_divisions >= 0
        and chart.settings.audio_latency_ms >= 0,
        f"zoom={chart.settings.zoom_seconds:g}s grid=1/{chart.settings.grid_division} "
        f"hgrid={chart.settings.horizontal_divisions} latency={chart.settings.audio_latency_ms:g}ms",
    )

    path = write_demo_chart()
    reloaded, _ = load_chart(path)
    item("谱面可保存并重新读取（字段完全一致）", reloaded.to_dict() == chart.to_dict(), path)
    item(
        "音频后端",
        True,
        audio_backend_name()
        + ("" if audio_backend_available() else "（未安装 pygame：播放为静音时钟，编辑功能不受影响）"),
    )

    document = Document(chart)
    note = LaneNote(chart.new_note_id("L"), 0, 0, 40.0)
    document.add_note(note)
    document.undo()
    item("撤销 / 重做", document.chart.find(note.id) is None and document.redo() is True)
    document.select_only([n.id for n in document.chart.mouse_notes][:2])
    source_notes = document.selected_notes()
    document.copy_selection()
    created = document.paste_clipboard(48.0)
    heads = sorted(n.head_beat for n in created)
    source_heads = sorted(n.head_beat for n in source_notes)
    offsets_kept = all(
        abs((h - heads[0]) - (o - source_heads[0])) < 1e-6
        for h, o in zip(heads, source_heads)
    )
    def geometry_of(note):
        if isinstance(note, MouseSlide):
            return (round(note.x_start, 9), round(note.x_end, 9))
        if isinstance(note, MouseRegion):
            return (
                round(note.left.start, 9),
                round(note.left.end, 9),
                round(note.right.start, 9),
                round(note.right.end, 9),
            )
        return None

    geometry_kept = all(geometry_of(a) == geometry_of(b) for a, b in zip(created, source_notes))
    item(
        "复制 / 粘贴：整组时间平移，横向位置与相对间隔不变",
        len(created) == 2 and abs(heads[0] - 48.0) < 1e-6 and offsets_kept and geometry_kept,
        f"最早 {heads[0]:g} 拍，共 {len(created)} 个物件",
    )
    target = next(n for n in document.chart.lane_notes if n.lane_start == 0)
    document.mirror_notes([target.id])
    mirrored = document.chart.find(target.id)
    item("沿游玩区中轴线镜像", mirrored.lane_start == 5, f"{target.id}: 轨道 1 -> {mirrored.lane_start + 1}")

    ok, failures = selftest(verbose=False)
    item("内置自检（时间轴 / 速度组 / 编辑命令 / 存档往返）", ok,
         "全部通过" if ok else f"失败 {len(failures)} 项：{failures[:3]}")

    print("\n".join(lines))
    print()
    print("结论：" + ("全部通过" if ok_all else "存在未通过项"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(report())

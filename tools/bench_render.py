"""渲染性能基准：量化“物件多时卡顿”的各环节开销。

用法::

    python tools/bench_render.py                # 默认 500 / 2000 / 8000 物件
    python tools/bench_render.py 200 2000 20000

输出每一项的毫秒数，便于定位瓶颈：

* ``sorted_notes`` —— 每帧对全部物件排序
* ``draw``        —— 整帧渲染（含差分比较、画布读写）
* ``hit``         —— 单次点击命中测试
"""

from __future__ import annotations

import os
import sys
import time

from _common import add_paths, use_utf8_console

add_paths()
use_utf8_console()

import tkinter as tk  # noqa: E402

import config  # noqa: E402
from model.chart import Chart  # noqa: E402
from model.document import Document  # noqa: E402
from model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide  # noqa: E402
from ui.geometry import notes_at  # noqa: E402
from ui.renderer import RenderState, Renderer  # noqa: E402
from ui.scene import Scene  # noqa: E402
from ui.theme import DARK  # noqa: E402
from ui.view import ViewState  # noqa: E402

WIDTH, HEIGHT = 1280, 800


def build_chart(count: int) -> Chart:
    """造一张大约 ``count`` 个物件的谱面：70% 6K、15% 空域、15% 滑键。"""
    chart = Chart()
    charts_total = max(1, count // 4)
    for index in range(charts_total):
        beat = index * 0.25
        lane = index % config.LANE_COUNT
        if index % 5 == 0 and index > 0:
            note = LaneNote(f"L{index}", lane, min(config.LANE_COUNT - 1, lane + 1), beat, beat + 1.0)
        else:
            note = LaneNote(f"L{index}", lane, lane, beat, beat)
        chart.add(note)
    for index in range(max(1, count // 8)):
        beat = index * 2.0
        chart.add(
            MouseRegion(
                f"M{index}",
                beat,
                beat + 1.5,
                BoundaryCurve(0.0, 0.2),
                BoundaryCurve(1.0, 0.8),
            )
        )
    for index in range(max(1, count // 8)):
        beat = index * 2.0 + 0.5
        chart.add(MouseSlide(f"S{index}", beat, 0.25, 0.75))
    return chart


def bench(count: int, frames: int = 20) -> dict:
    root = tk.Tk()
    root.withdraw()
    canvas = tk.Canvas(root, width=WIDTH, height=HEIGHT, highlightthickness=0)
    chart = build_chart(count)
    view = ViewState(chart)
    view.resize(WIDTH, HEIGHT)
    view.set_beat(chart.beat_bounds()[1] / 2)   # 停在中间，一半物件已判定
    scene = Scene(canvas)
    renderer = Renderer(scene, DARK)
    state = RenderState()

    # 预热一帧（建对象）
    renderer.draw(chart, view, state)

    # 排序：缓存命中（每帧实际路径） vs 冷启动（每次重新排序）
    t0 = time.perf_counter()
    for _ in range(frames):
        chart.sorted_notes()
    sorted_ms = (time.perf_counter() - t0) * 1000 / frames
    t0 = time.perf_counter()
    for _ in range(frames):
        chart.invalidate_order()
        chart.sorted_notes()
    sorted_cold_ms = (time.perf_counter() - t0) * 1000 / frames

    t0 = time.perf_counter()
    for _ in range(frames):
        renderer.draw(chart, view, state)
    draw_ms = (time.perf_counter() - t0) * 1000 / frames

    scene.stats.update({k: 0 for k in scene.stats})
    renderer.draw(chart, view, state)
    per_frame = dict(scene.frame_stats)

    t0 = time.perf_counter()
    for i in range(5):
        notes_at(chart, view, WIDTH / 2, 200 + i * 5)
    hit_ms = (time.perf_counter() - t0) * 1000 / 5      # 无缓存（兜底路径）

    # 有渲染缓存时（编辑器实际路径）：直接复用上一帧的可见物件几何
    for i in range(5):
        notes_at(chart, view, WIDTH / 2, 200 + i * 5, cache=state.hit_cache)
    t0 = time.perf_counter()
    for i in range(20):
        notes_at(chart, view, WIDTH / 2, 200 + i * 2, cache=state.hit_cache)
    hit_cached_ms = (time.perf_counter() - t0) * 1000 / 20

    # 编辑一次的开销：差量撤销（只记录被改动的物件）
    document = Document(chart)
    t0 = time.perf_counter()
    for _ in range(3):
        note = LaneNote(f"BENCH{len(chart.all_notes())}", 0, 0, 20.0)
        document.add_note(note)
        document.undo()
    edit_ms = (time.perf_counter() - t0) * 1000 / 3

    t0 = time.perf_counter()
    chart.beat_bounds()
    bounds_ms = (time.perf_counter() - t0) * 1000

    stats = {
        "notes": len(chart.all_notes()),
        "items": scene.item_count,
        "sorted_ms": sorted_ms,
        "sorted_cold_ms": sorted_cold_ms,
        "draw_ms": draw_ms,
        "hit_ms": hit_ms,
        "hit_cached_ms": hit_cached_ms,
        "edit_ms": edit_ms,
        "bounds_ms": bounds_ms,
        "per_frame": per_frame,
    }
    canvas.destroy()
    try:
        root.destroy()
    except tk.TclError:  # pragma: no cover
        pass
    return stats


def main() -> int:
    counts = [int(a) for a in sys.argv[1:]] or [500, 2000, 8000]
    print(
        f"{'物件数':>8} {'画布图元':>9} {'排序(缓存)':>11} {'排序(冷)':>10} {'整帧渲染':>10} {'命中(兜底)':>11} {'命中(缓存)':>11}"
        f" {'beat_bounds':>12} {'编辑+撤销':>10}   (静态帧的建/移/改/删)"
    )
    for count in counts:
        stats = bench(count)
        print(
            f"{stats['notes']:>8} {stats['items']:>9} "
            f"{stats['sorted_ms']:>9.3f}ms {stats['sorted_cold_ms']:>8.2f}ms {stats['draw_ms']:>8.2f}ms "
            f"{stats['hit_ms']:>9.2f}ms {stats['hit_cached_ms']:>9.3f}ms"
            f" {stats['bounds_ms']:>10.2f}ms {stats['edit_ms']:>8.3f}ms"
            f"   {stats['per_frame']['created']}/{stats['per_frame']['moved']}"
            f"/{stats['per_frame']['configured']}/{stats['per_frame']['deleted']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

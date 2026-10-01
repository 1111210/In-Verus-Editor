"""剖析 Panda3D 游玩路径的每帧耗时，找出真正的大头。

用法::

    python tools/profile_panda.py [宽 高] [时间毫秒] [帧数]

分段：Python 扫描/筛选物件 → 组场景（quad 调用）→ Panda 渲染 → 回读显存 →
建 pygame 表面 → 上下翻转。另附"不渲染只组场景"的对照，量化 CPU 侧开销。
"""

from __future__ import annotations

import collections
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault(
    "VERUS_PLAYER_SETTINGS", os.path.join(tempfile.gettempdir(), "inverus_profile.json")
)

import pygame  # noqa: E402

from rhythm_editor.game import render3d, scene as scene_module  # noqa: E402
from rhythm_editor.game.app import PlayerApp  # noqa: E402
from rhythm_editor.io.chart_io import load_chart  # noqa: E402


def main() -> int:
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1920
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 991
    now = float(sys.argv[3]) if len(sys.argv) > 3 else 15500.0
    frames = int(sys.argv[4]) if len(sys.argv) > 4 else 60
    app = PlayerApp([])
    app.screen = pygame.display.set_mode((width, height))
    app.scene3d = render3d.Scene3D(app.w, app.h, app.texture_dir)
    app.chart, _ = load_chart(os.path.join(ROOT, "charts", "test", "chart.json"))
    app.chart.invalidate_order()
    s = app.settings
    s.rate, s.speed, s.autoplay, s.side_lane_highlight = 0.4, 2.6, True, True
    # 厚度用世界单位（合法区宽 = 1）：0.3 是旧单位下的取值
    s.note_thickness = s.multi_thickness = 0.3 * scene_module.UNIT_SCALE
    scene = app.scene3d
    stages: "collections.OrderedDict[str, float]" = collections.OrderedDict()

    def add(name: str, seconds: float) -> None:
        stages[name] = stages.get(name, 0.0) + seconds

    real_render = scene.render

    def timed_render():
        t0 = time.perf_counter()
        for group in scene._batches.values():          # 把这一帧攒的四边形整块传上去
            group.flush()
        t1 = time.perf_counter()
        scene.base.graphicsEngine.render_frame()
        t2 = time.perf_counter()
        raw = bytes(scene.frame_texture.getRamImageAs("RGB"))
        t3 = time.perf_counter()
        surface = pygame.image.frombuffer(raw, (scene.width, scene.height), "RGB")
        t4 = time.perf_counter()
        flipped = pygame.transform.flip(surface, False, True)
        t5 = time.perf_counter()
        add("批量上传(批渲染)", t1 - t0)
        add("Panda 渲染", t2 - t1)
        add("回读显存", t3 - t2)
        add("建 pygame 表面", t4 - t3)
        add("上下翻转", t5 - t4)
        return flipped

    # ---- 整帧（真实路径）
    scene.render = timed_render
    for _ in range(frames):
        app._render_scene3d(now)
    full = sum(stages.values())

    # ---- 只组场景（把渲染换掉，量 Python 侧）
    build_only: "collections.OrderedDict[str, float]" = collections.OrderedDict()

    def null_render():
        return pygame.Surface((width, height))

    scene.render = null_render
    t0 = time.perf_counter()
    for _ in range(frames):
        app._render_scene3d(now)
    python_side = (time.perf_counter() - t0) / frames * 1000.0
    scene.render = real_render

    # ---- 只扫描/筛选（不进场景）
    t0 = time.perf_counter()
    counts = []
    for _ in range(frames):
        visible = [n for n in app.chart.all_notes()
                   if app._note_ahead(n, now) and app._note_visible(n, now)]
        visible.sort(key=lambda n: (app._layer_of(n), n.head_beat))
        counts.append(len(visible))
    scan = (time.perf_counter() - t0) / frames * 1000.0

    print(f"== {width}x{height} · t={now:.0f}ms · {frames} 帧平均 ==")
    print(f"  整帧（渲染+回读+建表面+翻转）: {full / frames * 1000:.2f} ms")
    for name, seconds in stages.items():
        print(f"    {name:14s} {seconds / frames * 1000:6.2f} ms")
    print(f"  仅 Python 组场景（不做任何 GL）: {python_side:.2f} ms")
    print(f"  仅扫描+筛选物件: {scan:.2f} ms"
          f"（谱面 {len(app.chart.all_notes())} 个物件，可见 {sum(counts) // max(1, len(counts))} 个）")
    scene.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

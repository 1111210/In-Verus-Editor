"""逐物件检查渲染里的"1px 横线 / 竖线"（贴图接缝）。

用法::  python tools/check_seams.py [宽 高]

做法：给每种物件单独造一张谱面，分别用 Panda3D 与软件渲染各画一帧，然后在物件
自己的包围盒里找**孤立的 1 像素线**（本行与上下行的颜色都明显不同、而上下两行彼此
接近）—— 这就是分片贴图 / 贴图环绕漏出的接缝。返回码非 0 表示发现问题。
"""

from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault(
    "VERUS_PLAYER_SETTINGS", os.path.join(tempfile.gettempdir(), "inverus_seams.json")
)

import numpy as np  # noqa: E402
import pygame  # noqa: E402

from rhythm_editor.game import render3d  # noqa: E402
from rhythm_editor.game.app import PlayerApp  # noqa: E402
from rhythm_editor.model.chart import Chart  # noqa: E402
from rhythm_editor.model.notes import (  # noqa: E402
    BoundaryCurve,
    LaneNote,
    MouseClick,
    MouseRegion,
    MouseSlide,
)


def isolated_lines(data: np.ndarray, box, axis: str, min_jump: int = 10,
                   max_neighbour: int = 6):
    """在 ``box`` 内找孤立 1px 线：``axis="row"`` 找横线，``axis="col"`` 找竖线。"""
    x0, y0, x1, y1 = box
    found = []
    if axis == "row":
        for y in range(y0 + 1, y1 - 1):
            for x in range(x0, x1, 2):
                up, mid, dn = data[x, y - 1], data[x, y], data[x, y + 1]
                if (np.abs(mid - up).max() > min_jump and np.abs(mid - dn).max() > min_jump
                        and np.abs(up - dn).max() <= max_neighbour):
                    found.append((x, y, tuple(int(v) for v in mid)))
                    break
    else:
        for x in range(x0 + 1, x1 - 1):
            for y in range(y0, y1, 2):
                left, mid, right = data[x - 1, y], data[x, y], data[x + 1, y]
                if (np.abs(mid - left).max() > min_jump and np.abs(mid - right).max() > min_jump
                        and np.abs(left - right).max() <= max_neighbour):
                    found.append((x, y, tuple(int(v) for v in mid)))
                    break
    return found


CASES = (
    # (名字, 谱面内容, 相对判定时刻的偏移毫秒)：**每个物件扫几个位置**，
    # 接缝是整像素取整造成的，只有某些尺寸 / 位置组合才会露出来。
    ("单键", dict(lane=[LaneNote("L1", 2, 2, 0.0)]), (-15.0, -45.0, -80.0, -120.0, -170.0)),
    ("多轨单键", dict(lane=[LaneNote("L1", 1, 4, 0.0)]), (-15.0, -45.0, -80.0, -120.0, -170.0)),
    ("单轨长按", dict(lane=[LaneNote("H", 3, 3, 0.0, 12.0)]),
     (0.0, -40.0, -80.0, -120.0, -170.0)),
    ("多轨长按", dict(lane=[LaneNote("H", 0, 5, 0.0, 12.0)]),
     (0.0, -40.0, -80.0, -120.0, -170.0)),
    ("空域", dict(mouse=[MouseRegion("R", 0.0, 12.0, BoundaryCurve(0.2, 0.1),
                                     BoundaryCurve(0.8, -0.1))]),
     (-30.0, -80.0, -150.0)),
    ("滑键", dict(mouse=[MouseSlide("S", 0.0, 0.2, 0.8)]), (-20.0, -60.0, -130.0)),
    ("鼠标点击", dict(mouse=[MouseClick("C", 0.0, 0.35, 0.65)]), (-20.0, -60.0, -130.0)),
)


def build(width: int, height: int, notes: dict, now: float):
    app = PlayerApp([])
    app.screen = pygame.display.set_mode((width, height))
    app.chart = Chart()
    app.chart.lane_notes = list(notes.get("lane", []))
    app.chart.mouse_notes = list(notes.get("mouse", []))
    app.chart.invalidate_order()
    # 用**实际游玩时的那套设置**（倍速 0.4 / 流速 2.6 / 用户厚度）：接缝跟屏幕尺寸
    # 和分片数都有关，用真实配置扫位置才容易碰到。
    app.settings.rate, app.settings.speed, app.settings.autoplay = 0.4, 2.6, True
    app.settings.note_thickness = app.settings.multi_thickness = 0.3 * 0.7142857142857143
    app.settings.slide_thickness = app.settings.click_thickness = 0.1 * 0.7142857142857143
    app.settings.side_lane_highlight = False
    return app


def render(app, use_panda: bool, now: float):
    scene3d = render3d.Scene3D(app.w, app.h, app.texture_dir) if use_panda else None
    app.scene3d = scene3d
    try:
        if scene3d is not None:
            return np.transpose(pygame.surfarray.array3d(app._render_scene3d(now)), (1, 0, 2)).astype(np.int16)
        app.screen.fill((10, 10, 14))
        app._draw_planes(now)
        app._draw_notes(now)
        return np.transpose(pygame.surfarray.array3d(app.screen), (1, 0, 2)).astype(np.int16)
    finally:
        if scene3d is not None:
            scene3d.close()


def object_box(app, notes, now: float, width: int, height: int):
    """物件本体的屏幕包围盒（用它来限定检查范围，避开判定线 / 指针等装饰）。"""
    xs, ys = [], []
    for note in list(notes.get("lane", [])):
        low, high = min(note.lane_start, note.lane_end), max(note.lane_start, note.lane_end)
        x0 = app._proj(0.75 * (low / 3.0 - 1.0), 0.0, 0.0)[0]
        x1 = app._proj(0.75 * ((high + 1) / 3.0 - 1.0), 0.0, 0.0)[0]
        z0 = app._note_z(app._head_ms(note), now)
        z1 = app._note_z(app.chart.timing.beat_to_song_ms(
            note.tail_beat if note.is_hold else note.head_beat), now)
        for z in (max(0.0, z0), max(0.0, z1)):
            for x in (x0, x1):
                p = app._proj(x, 0.0, z)
                xs.append(p[0]); ys.append(p[1])
    for note in list(notes.get("mouse", [])):
        if isinstance(note, MouseSlide):
            x0 = app._proj(-0.5 + note.x_start, 0.0, 0.0)[0]
            x1 = app._proj(-0.5 + note.x_end, 0.0, 0.0)[0]
        elif isinstance(note, MouseClick):
            lo, hi = note.x_range()
            x0 = app._proj(-0.5 + lo, 0.0, 0.0)[0]
            x1 = app._proj(-0.5 + hi, 0.0, 0.0)[0]
        else:
            lo = min(note.left_at(0.0), note.right_at(0.0))
            hi = max(note.left_at(0.0), note.right_at(0.0))
            x0 = app._proj(-0.5 + lo, 0.0, 0.0)[0]
            x1 = app._proj(-0.5 + hi, 0.0, 0.0)[0]
        z0 = app._note_z(app._head_ms(note), now)
        z1 = (app._note_z(app.chart.timing.beat_to_song_ms(note.beat_end), now)
              if isinstance(note, MouseRegion) else z0)
        for z in (max(0.0, z0), max(0.0, z1)):
            for x in (x0, x1):
                p = app._proj(x, 0.0, z)
                xs.append(p[0]); ys.append(p[1])
    if not xs:
        return (0, 0, width, height)
    pad = 4
    return (max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad),
            min(width - 1, int(max(xs)) + pad), min(height - 1, int(max(ys)) + pad))


def main() -> int:
    if len(sys.argv) > 2:
        sizes = [(int(sys.argv[1]), int(sys.argv[2]))]
    else:
        # 接缝是**整像素取整**造成的，跟窗口尺寸有关：两种常用尺寸都查
        sizes = [(1280, 744), (1920, 991)]
    problems = 0
    for width, height in sizes:
        print(f"== 接缝检查（{width}x{height}）==")
        for name, notes, offsets in CASES:
            for tag, use_panda in (("panda", True), ("soft", False)):
                rows_found, cols_found = [], []
                for offset in offsets:
                    app = build(width, height, notes, offset)
                    now = app.chart.timing.beat_to_song_ms(0.0) + offset
                    data = render(app, use_panda, now)
                    box = object_box(app, notes, now, width, height)
                    rows_found += isolated_lines(data, box, "row")
                    cols_found += isolated_lines(data, box, "col")
                mark = "OK" if not rows_found else "!!"
                print(f"  [{mark}] {name:6s} {tag:5s} 横线 {len(rows_found):2d} "
                      f"竖线 {len(cols_found):2d}"
                      + (f"  例：{rows_found[:2]}" if rows_found else ""))
                problems += len(rows_found)
    print("结论：" + ("没有发现接缝" if not problems else f"发现 {problems} 处疑似接缝"))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())

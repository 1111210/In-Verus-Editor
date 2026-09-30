"""诊断 Panda3D 渲染层：空域形状/透明度、运动条纹、6K 按键消失。

用法::

    python tools/diag_panda_render.py [输出目录] [宽] [高]

做法是「同一帧画两次」：一次只有静态场景，一次加上待测物件，相减得到物件
自己的像素。于是可以定量地问三件事：

* 物件像素数随 z 变化时有没有掉到 0（= 某些位置消失）；
* 物件像素在竖直扫描线上被切成多少段（段数远大于 1 = 条纹/撕列）；
* 空域每行的左右边界是不是梯形（和左右边界曲线对比）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import math

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault(
    "VERUS_PLAYER_SETTINGS", os.path.join(tempfile.gettempdir(), "inverus_diag.json")
)

import numpy as np  # noqa: E402
import pygame  # noqa: E402

from rhythm_editor.game.app import PlayerApp  # noqa: E402
from rhythm_editor.game import render3d, scene as soft_scene  # noqa: E402
from rhythm_editor.model.notes import (  # noqa: E402
    BoundaryCurve,
    LaneNote,
    MouseRegion,
    MouseSlide,
)


def pixels(surface) -> np.ndarray:
    """pygame 画面 -> ``(高, 宽, 3)`` 的 numpy 数组。"""
    return np.transpose(pygame.surfarray.array3d(surface), (1, 0, 2)).astype(np.int16)


def mask_of(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """两张画面的差异掩码（有物件 vs 无物件）。"""
    return (np.abs(a - b).sum(axis=2) > 12)


def column_runs(mask: np.ndarray, x: int) -> int:
    """某一列里掩码被切成了多少段。"""
    col = mask[:, x]
    index = np.flatnonzero(col)
    if index.size == 0:
        return 0
    return int(1 + np.count_nonzero(np.diff(index) > 1))


def describe(mask: np.ndarray) -> str:
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size == 0:
        return "无像素"
    cols = np.flatnonzero(mask.any(axis=0))
    counts = mask.sum(axis=0)
    x = int(np.argmax(counts))
    return (f"像素 {int(mask.sum()):6d}  行 {rows.min()}~{rows.max()}  "
            f"列 {cols.min()}~{cols.max()}  中列段数 {column_runs(mask, x)}")


def crop_save(surface, mask: np.ndarray, path: str, zoom: int = 3) -> None:
    """把掩码的外接矩形放大存图（人眼检查条纹用）。"""
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return
    box = pygame.Rect(
        max(0, int(cols.min()) - 6), max(0, int(rows.min()) - 6),
        int(cols.max() - cols.min()) + 13, int(rows.max() - rows.min()) + 13,
    )
    piece = surface.subsurface(box.clip(surface.get_rect())).copy()
    pygame.image.save(pygame.transform.scale(piece, (piece.get_width() * zoom,
                                                     piece.get_height() * zoom)), path)


def mask_surface(mask: np.ndarray) -> pygame.Surface:
    """把布尔掩码存成黑白图（人眼检查差异分布）。"""
    data = np.where(mask[..., None], 255, 0).astype(np.uint8).repeat(3, axis=2)
    return pygame.image.fromstring(np.ascontiguousarray(
        np.transpose(data, (1, 0, 2))).tobytes(), (mask.shape[1], mask.shape[0]), "RGB")


def bright_runs(frame: np.ndarray, limit: int = 6) -> None:
    """打印最长的几条水平亮线（检查判定线 / 平面边线的位置与宽度）。"""
    lum = frame.sum(axis=2)
    found = []
    for row in range(lum.shape[0]):
        xs = np.flatnonzero(lum[row] > 330)
        if xs.size < 120:
            continue
        found.append((int(xs.size), row, int(xs.min()), int(xs.max()),
                      tuple(int(v) for v in frame[row, int(np.median(xs))])))
    found.sort(reverse=True)
    for size, row, x0, x1, color in found[:limit]:
        print(f"    亮线 行 {row:4d} 列 {x0:4d}~{x1:4d}（长 {size}）色 {color}")


def row_profile(frame: np.ndarray, row: int, x0: int, x1: int, label: str = "") -> None:
    """打印某一行在给定列范围内的颜色游程（相邻同色合并）——条纹会表现为许多短游程。"""
    line = frame[row, x0:x1]
    changes = np.flatnonzero((np.abs(np.diff(line.astype(np.int32), axis=0)).sum(axis=1) > 6))
    runs = len(changes) + 1
    print(f"    {label} 行 {row} 列 {x0}~{x1}：游程 {runs} 段，"
          f"均值 {tuple(int(v) for v in line.mean(axis=0))}，"
          f"极差 {tuple(int(v) for v in (line.max(axis=0) - line.min(axis=0)))}")


def texture_report(texture_dir: str, names) -> None:
    """贴图尺寸 / 平均 RGBA / 上下边缘 alpha（判断渐变方向与透明度是否太淡）。"""
    for name in names:
        path = os.path.join(texture_dir, f"{name}.png")
        if not os.path.isfile(path):
            print(f"    {name:22s} 缺失")
            continue
        surface = pygame.image.load(path).convert_alpha()
        data = np.transpose(pygame.surfarray.array3d(surface), (1, 0, 2)).astype(np.float64)
        alpha = np.transpose(pygame.surfarray.array_alpha(surface), (1, 0)).astype(np.float64)
        top = alpha[min(1, alpha.shape[0] - 1)].mean()
        mid = alpha[alpha.shape[0] // 2].mean()
        bottom = alpha[max(0, alpha.shape[0] - 2)].mean()
        print(f"    {name:22s} {surface.get_size()}  平均 "
              f"({data[..., 0].mean():5.1f},{data[..., 1].mean():5.1f},"
              f"{data[..., 2].mean():5.1f},a{alpha.mean():6.1f})  "
              f"上边 a{top:5.1f} / 中心 a{mid:5.1f} / 下边 a{bottom:5.1f}")


def new_node_check(scene3d, out_dir: str = ".") -> None:
    """查「新增一张卡片会不会改变画面其它区域」（条纹/闪烁的根源）。"""
    print("\n[5d] 新增卡片的稳定性检查")
    scene3d.begin()
    a = pixels(scene3d.render())
    line_row = int(scene3d.project(0.0, 0.4, 0.0)[1])
    for layer, z in (("bar", 3.0), ("marker", 3.0), ("line", 0.0), ("line", 3.0)):
        scene3d.begin()
        scene3d.quad("", 0.0, z, 1.4, 0.08, 0.03, (1, 0, 1, 1), layer=layer)
        data = pixels(scene3d.render())
        row = data[line_row]
        cyan = int(((row[:, 2] > 120) & (row[:, 0] < 200)).sum())
        print(f"    在 {layer} 层加一张卡片(z={z})：鼠标判定线行 {line_row} "
              f"上的青色像素 {cyan}（静态时 "
              f"{int(((a[line_row][:, 2] > 120) & (a[line_row][:, 0] < 200)).sum())}）")
    scene3d.begin()
    scene3d.quad("", 0.0, 3.0, 1.0, 0.6, 0.5, (1, 1, 1, 0.5), layer="bar")   # 新节点
    b = pixels(scene3d.render())
    scene3d.begin()
    scene3d.quad("", 0.0, 3.0, 1.0, 0.6, 0.5, (1, 1, 1, 0.5), layer="bar")   # 复用同一节点
    c = pixels(scene3d.render())
    scene3d.begin()
    d = pixels(scene3d.render())                                            # 去掉这张卡
    for name, first, second in (("静态 -> 新增卡片", a, b), ("新增 -> 复用同卡片", b, c),
                                ("静态 -> 卡片移走", a, d)):
        mask = mask_of(first, second)
        print(f"    {name}：差异 {int(mask.sum())} px"
              + (f"（行 {np.flatnonzero(mask.any(axis=1)).min()}~"
                 f"{np.flatnonzero(mask.any(axis=1)).max()}）" if mask.any() else ""))
    mask = mask_of(a, b)
    if mask.any():
        pygame.image.save(mask_surface(mask), os.path.join(out_dir, "diag_newcard_mask.png"))
        rows = np.flatnonzero(mask.any(axis=1))
        cols = np.flatnonzero(mask.any(axis=0))
        for r, c in ((int(rows[min(1, rows.size - 1)]), 700), (int(rows.max()) - 1, 700)):
            print(f"      点 ({c},{r}) 静态 {tuple(int(v) for v in a[r, c])}"
                  f" / 新增后 {tuple(int(v) for v in b[r, c])}")


def calibrate(scene3d, out_dir: str = ".") -> None:
    """用亮洋红标记卡片实测「世界 z -> 屏幕行」，和 ``Scene3D.project`` 对比。"""
    print("\n[5] 投影标定（洋红标记卡片实测行 vs 公式行）")
    scene3d.begin()
    empty = pixels(scene3d.render())            # 只有静态场景（紫色边线也会命中洋红筛选）
    repeat = pixels(scene3d.render())           # 同一状态再画一帧（查读回是否稳定）
    instability = mask_of(empty, repeat)
    print(f"    同一状态连续两帧的差异像素：{int(instability.sum())}"
          f"（读回/渲染是否稳定）")
    if instability.any():
        pygame.image.save(mask_surface(instability),
                          os.path.join(out_dir, "diag_calib_unstable.png"))
    errors = []
    for plane_y, label in ((0.0, "6K"), (0.4, "鼠标")):
        for z in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0):
            scene3d.begin()
            scene3d.quad("", 0.0, z, 1.4, 0.08, plane_y + 0.03, (1, 0, 1, 1), layer="line")
            marker = mask_of(pixels(scene3d.render()), empty)
            rows = np.flatnonzero(marker.any(axis=1))
            cols = np.flatnonzero(marker.any(axis=0))
            if rows.size < 2:
                print(f"    {label:3s} z={z:4.1f}  未找到")
                continue
            if z in (0.0, 2.0) and label == "6K":
                counts = marker.sum(axis=1)
                busiest = np.argsort(counts)[::-1][:4]
                data = pixels(scene3d.render())
                print(f"      （掩码 {int(marker.sum())} px，行 {rows.min()}~{rows.max()}，"
                      f"列 {cols.min()}~{cols.max()}；最密行 "
                      + ", ".join(f"{int(r)}:{int(counts[r])}" for r in busiest) + "）")
                for r, c in ((rows.min(), 640), (665, 640), (int(rows.max()), 640)):
                    print(f"        点 ({c},{r}) 空场景 {tuple(int(v) for v in empty[r, c])}"
                          f" / 标记帧 {tuple(int(v) for v in data[r, c])}")
                for r in (460, 500, 550, 600, 640, 655):
                    differing = np.flatnonzero(marker[r])
                    print(f"        行 {r}: 差异 {differing.size} px"
                          + (f"，列 {differing.min()}~{differing.max()}" if differing.size else "")
                          + f"；列 640 空 {tuple(int(v) for v in empty[r, 640])}"
                          f" / 标记 {tuple(int(v) for v in data[r, 640])}")
                pygame.image.save(mask_surface(marker),
                                  os.path.join(out_dir, f"diag_calib_mask_{z:.1f}.png"))
                pygame.image.save(scene3d.render(),
                                  os.path.join(out_dir, f"diag_calib_marker_{z:.1f}.png"))
            point = scene3d.project(0.0, plane_y + 0.03, z)   # 标记卡片抬高 0.03
            error = float(rows.mean()) - point[1]
            errors.append(abs(error))
            print(f"    {label:3s} z={z:4.1f}  实测 行 {rows.mean():6.1f} 列 "
                  f"{cols.min()}~{cols.max()}   project {point[1]:6.1f}"
                  f"（列 {point[0]:.0f}，差 {error:+.1f}px）")
    if errors:
        print(f"    project() 与渲染的最大行偏差 {max(errors):.2f}px")


def bin_order_check(scene3d) -> None:
    """验证图层父节点的顺序是否真的决定绘制顺序（画家算法的基础）。"""
    from panda3d.core import NodePath, TransparencyAttrib

    print("\n[5c] 绘制顺序检查（默认 bin / unsorted bin / 图层父节点）")
    point = scene3d.project(0.0, 0.5, 3.0)
    row, col = int(point[1]), int(point[0])

    def sample() -> str:
        color = pixels(scene3d.render())[row, col]
        if color[0] + color[1] < 60:
            return f"背景 {tuple(int(v) for v in color)}"
        return ("红" if color[0] > color[1] else "绿") + f" {tuple(int(v) for v in color)}"

    for name, first, second in (("低位先画", "lane", "line"), ("高位先画", "line", "lane")):
        scene3d.begin()
        scene3d.quad("", 0.0, 3.0, 60.0, 60.0, 0.5, (1, 0, 0, 1), layer=first)
        scene3d.quad("", 0.0, 5.0, 60.0, 60.0, 0.5, (0, 1, 0, 1), layer=second)
        top = max((first, second), key=scene3d.LAYER_SEQUENCE.index)
        expected = "绿" if top == second else "红"
        print(f"    {name}：红={first} 绿={second} 调用序 -> {sample()}（期望 {expected} 在上）")

    for bin_name in ("unsorted", "fixed"):
        for name, first_order, second_order in (("低排序号先画", 10, 30), ("高排序号先画", 30, 10)):
            scene3d.begin()
            red = scene3d.quad("", 0.0, 3.0, 60.0, 60.0, 0.5, (1, 0, 0, 1), layer="note0")
            green = scene3d.quad("", 0.0, 5.0, 60.0, 60.0, 0.5, (0, 1, 0, 1), layer="note6")
            red.setBin(bin_name, first_order)
            green.setBin(bin_name, second_order)
            print(f"    bin={bin_name} {name}（红 {first_order} / 绿 {second_order}，"
                  f"红线更近）-> {sample()}；红节点 bin={red.getBinName()}"
                  f"/{red.getBinDrawOrder()}，绿节点 bin={green.getBinName()}"
                  f"/{green.getBinDrawOrder()}；深度测试 根={scene3d.root.getDepthTest()}"
                  f" 红={red.getDepthTest()} 绿={green.getDepthTest()}")


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "images")
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 1280
    height = int(sys.argv[3]) if len(sys.argv) > 3 else 744
    os.makedirs(out_dir, exist_ok=True)
    if not render3d.available():
        print(f"Panda3D 不可用：{render3d.import_error()}")
        return 1

    app = PlayerApp([])
    app.screen = pygame.display.set_mode((width, height))   # w / h 由画面尺寸推出
    app.scene3d = render3d.Scene3D(width, height, app.texture_dir)
    app.chart = soft_scene_chart()
    scene3d = app.scene3d
    try:
        print(f"== Panda3D 渲染诊断（{width}x{height}）==")
        tap_z = lambda ms: app._note_z(0.0, ms)
        print(f"可视跨度：START_Z={scene3d.start_z}，判定线行 = "
              f"6K {scene3d.judge_line_y(0.0):.1f} / 鼠标 {scene3d.judge_line_y(0.4):.1f}"
              f"（软件 {soft_scene.PlayerScene().judgment_lines(width, height)['mouse']:.1f}）")
        print("\n[-1] 贴图统计")
        texture_report(app.texture_dir, (
            "lane_1_default", "lane_2_default", "lane_1_pressed",
            "region_area", "region_edge", "region_fill", "region_line",
            "tap_1", "hold_body_1", "hold_head_1",
        ))

        # ---------------------------------------------------------- 场景 A：单键扫描
        app.chart.lane_notes = [LaneNote("L1", 0, 0, 0.0)]
        app.chart.mouse_notes = []
        app.chart.invalidate_order()
        app.settings.rate = 1.0
        empty = app._render_scene3d(-1e9)                 # 只画静态场景
        base = pixels(empty)
        pygame.image.save(empty, os.path.join(out_dir, "diag_00_static.png"))
        print("\n[0] 静态场景亮线（判定线 / 平面边线）")
        bright_runs(base)
        for row in (200, 300, 400, 500, 600, 650):
            row_profile(base, row, 0, width, f"6K 平面横切")
        print("    关键点投影：")
        for label, (px, py, pz) in (
            ("6K左近", (-0.75, 0.0, 0.0)), ("6K右近", (0.75, 0.0, 0.0)),
            ("6K左远", (-0.75, 0.0, 12.0)), ("6K右远", (0.75, 0.0, 12.0)),
            ("鼠标左近", (-0.5, 0.4, 0.0)), ("鼠标右近", (0.5, 0.4, 0.0)),
            ("鼠标左远", (-0.5, 0.4, 12.0)),
        ):
            point = scene3d.project(px, py, pz)
            shown = tuple(round(v, 1) for v in point) if point else None
            print(f"      {label:8s} ({px:+.2f}, y={py:0.2f}, z={pz:0.2f}) -> {shown}")
        calibrate(scene3d, out_dir)
        new_node_check(scene3d, out_dir)
        bin_order_check(scene3d)

        print("\n[1] 单键扫描（z 由远到近，看像素数是否掉 0、是否被切条纹）")
        worst = None
        for step in range(0, 41):
            ms = -1600.0 + step * 40.0
            frame = app._render_scene3d(ms)
            mask = mask_of(pixels(frame), base)
            z = tap_z(ms)
            if mask.any():
                count = int(mask.sum())
                if worst is None or count < worst[1]:
                    worst = (z, count)
                if step % 5 == 0 or column_runs(mask, int(np.argmax(mask.sum(axis=0)))) > 3:
                    print(f"    z={z:5.2f}  {describe(mask)}")
                    pygame.image.save(
                        frame, os.path.join(out_dir, f"diag_01_tap_z{z:05.2f}.png")
                    )
                    crop_save(frame, mask, os.path.join(
                        out_dir, f"diag_01_crop_z{z:05.2f}.png"))
            else:
                print(f"    z={z:5.2f}  物件像素为 0（消失）")
        print(f"    最少像素：z={worst[0]:.2f} -> {worst[1]}" if worst else "    全灭")

        # ---------------------------------------------------------- 场景 B：长按 + 滑键
        app.chart.lane_notes = [LaneNote("L2", 2, 2, 0.0, 2.0)]
        app.chart.mouse_notes = [MouseSlide("M1", 0.0, 0.2, 0.8)]
        app.chart.invalidate_order()
        print("\n[2] 长按 + 滑键（同一帧）")
        frame = app._render_scene3d(-600.0)
        mask = mask_of(pixels(frame), base)
        print(f"    全部物件 {describe(mask)}")
        pygame.image.save(frame, os.path.join(out_dir, "diag_02_hold_slide.png"))

        # ---------------------------------------------------------- 场景 C：空域梯形
        app.chart.lane_notes = []
        app.chart.mouse_notes = [
            MouseRegion("M2", 0.0, 4.0, BoundaryCurve(0.10, 0.40), BoundaryCurve(0.90, 0.60))
        ]
        app.chart.invalidate_order()
        print("\n[3] 空域（梯形：左右边界都会变）")
        frame = app._render_scene3d(-900.0)
        mask = mask_of(pixels(frame), base)
        print(f"    空域 {describe(mask)}")
        if mask.any():
            rows = np.flatnonzero(mask.any(axis=1))
            for row in (rows.min(), (rows.min() + rows.max()) // 2, rows.max()):
                cols = np.flatnonzero(mask[row])
                print(f"    行 {row:4d}：列 {cols.min()}~{cols.max()}（宽 {cols.max() - cols.min()}）")
            area = pixels(frame)[mask].mean(axis=0)
            base_area = base[mask].mean(axis=0)
            print(f"    平均色 空域 {tuple(round(v) for v in area)} / "
                  f"底色 {tuple(round(v) for v in base_area)}")
        pygame.image.save(frame, os.path.join(out_dir, "diag_03_region.png"))
        app.chart.mouse_notes = []
        app.chart.invalidate_order()
        app.chart.mouse_notes = [
            MouseRegion("M2", 0.0, 4.0, BoundaryCurve(0.00, 0.00), BoundaryCurve(1.00, 1.00))
        ]
        app.chart.invalidate_order()
        frame = app._render_scene3d(-900.0)
        mask = mask_of(pixels(frame), base)
        print(f"    满宽空域 {describe(mask)}")
        pygame.image.save(frame, os.path.join(out_dir, "diag_04_region_full.png"))
        note_coverage(app, out_dir)
        bar_and_timing_check(app, out_dir)
        compare_chart(app, out_dir, base)
    finally:
        scene3d.close()
    return 0


def user_settings(app) -> None:
    """套用用户实际在用的设置（取自 %APPDATA%/InVerus/player.json）。"""
    app.settings.rate = 0.4
    app.settings.speed = 2.6
    app.settings.side_lane_highlight = True
    app.settings.autoplay = True
    app.settings.note_thickness = 0.3
    app.settings.multi_thickness = 0.3
    app.settings.slide_thickness = 0.1
    app.settings.click_thickness = 0.1


def bar_and_timing_check(app, out_dir: str, frames: int = 120) -> None:
    """小节线是否真的画出来 + Panda 路径的每帧耗时。"""
    import time as _time

    from rhythm_editor.io.chart_io import load_chart

    chart_path = os.path.join(ROOT, "charts", "test", "chart.json")
    if not os.path.isfile(chart_path):
        print("\n[6] 小节线与耗时：找不到实谱，跳过")
        return
    app.chart, _ = load_chart(chart_path)
    app.chart.invalidate_order()
    user_settings(app)
    timing = app.chart.timing
    found = None
    for step in range(0, 4000):
        now = step * 20.0
        beat = timing.song_ms_to_beat(max(0.0, now))
        span = max(0.5, app._visible_span_ms() / 1000.0 * timing.bpm_at(beat) / 60.0)
        zs = [app._note_z(timing.beat_to_song_ms(line), now)
              for line in timing.bar_lines(beat, beat + span)]
        if any(0.5 <= z <= 11.5 for z in zs):
            found = (now, [round(z, 2) for z in zs])
            break
    print(f"\n[6] 小节线检查：{found if found else '没找到可视窗口内的小节线'}")
    if found:
        now = found[0]
        base = pixels(app._render_scene3d(now - 1e6))
        frame = app._render_scene3d(now)
        mask = mask_of(pixels(frame), base)
        rows = np.flatnonzero(mask.any(axis=1))
        wide = sorted(((int(mask[r].sum()), int(r)) for r in rows), reverse=True)[:6]
        print(f"    该帧差异最宽的行（宽, 行）：{wide}")
    start = _time.perf_counter()
    for step in range(frames):
        app._render_scene3d(14000.0 + step * 16.7)
    cost = (_time.perf_counter() - start) / frames * 1000.0
    print(f"    Panda 路径每帧耗时（含回读）：{cost:.2f} ms（{frames} 帧平均）")


def note_coverage(app, out_dir: str, frames: int = 180, start_ms: float = 14000.0) -> None:
    """逐帧核对：每个「按逻辑应当可见」的 6K 物件在 Panda 画面里有没有像素。

    这是「按键在某些位置消失」的直接判据：物件逻辑上可见（``_note_ahead`` /
    ``_note_visible`` / z > -0.4），但它在屏幕上的多边形里几乎没有亮像素。
    """
    from rhythm_editor.io.chart_io import load_chart

    chart_path = os.path.join(ROOT, "charts", "test", "chart.json")
    if not os.path.isfile(chart_path):
        print("\n[4] 逐帧可见性：找不到 charts/test/chart.json，跳过")
        return
    app.chart, _ = load_chart(chart_path)
    app.chart.invalidate_order()
    user_settings(app)
    scene3d = app.scene3d
    assert scene3d is not None
    print(f"\n[4] 逐帧可见性（实谱 / 用户设置 rate=0.4 speed=2.6 厚度 0.3，"
          f"{frames} 帧，起始 {start_ms:.0f}ms）")
    print(f"    可视跨度 {app._visible_span_ms():.0f} 歌曲毫秒")
    mask_surface = pygame.Surface((app.w, app.h))
    worst_frames = []
    for step in range(frames):
        now = start_ms + step * 16.7
        frame = app._render_scene3d(now)
        data = pixels(frame)
        missing = []
        for note in app.chart.lane_notes:
            if not (app._note_ahead(note, now) and app._note_visible(note, now)):
                continue
            head = app._note_z(app._head_ms(note), now)
            if head < -0.4:
                continue
            low, high = min(note.lane_start, note.lane_end), max(note.lane_start, note.lane_end)
            x0 = soft_scene.K6_LEFT + low * soft_scene.LANE_WIDTH + soft_scene.LANE_WIDTH * 0.18
            x1 = soft_scene.K6_LEFT + (high + 1) * soft_scene.LANE_WIDTH - soft_scene.LANE_WIDTH * 0.18
            if note.is_hold:
                tail = app._note_z(app.chart.timing.beat_to_song_ms(note.tail_beat), now)
                z_lo, z_hi = max(0.0, head), max(0.2, max(0.0, tail))
            else:
                thick = (app.settings.multi_thickness if note.is_multi
                         else app.settings.note_thickness)
                z_lo, z_hi = max(0.0, head), max(0.0, head) + thick
            corners = [scene3d.project(x0, 0.0, z_lo), scene3d.project(x1, 0.0, z_lo),
                       scene3d.project(x1, 0.0, z_hi), scene3d.project(x0, 0.0, z_hi)]
            if any(point is None for point in corners):
                continue
            mask_surface.fill((0, 0, 0))
            pygame.draw.polygon(mask_surface, (255, 255, 255), corners)
            mask = np.transpose(pygame.surfarray.array3d(mask_surface), (1, 0, 2))[..., 0] > 128
            total = int(mask.sum())
            if total < 4:
                continue
            lit = int((mask & (data.sum(axis=2) > 300)).sum())
            if lit / total < 0.15:
                missing.append((note.id, round(head, 2), round(lit / total, 3), total))
        if missing:
            worst_frames.append((now, len(missing), missing[:4]))
            if len(worst_frames) <= 4:
                print(f"    t={now:8.0f}ms  应有 {len(missing)} 个物件没画出来："
                      + "；".join(f"{i}(z={z}, 覆盖 {c}, 面积 {a})" for i, z, c, a in missing[:4]))
                pygame.image.save(frame, os.path.join(out_dir, f"diag_06_miss_{now:.0f}.png"))
    print(f"    共 {len(worst_frames)}/{frames} 帧存在「应当可见但没有像素」的物件")


def compare_chart(app, out_dir: str, panda_base: np.ndarray) -> None:
    """实谱逐帧对比：软件渲染 vs Panda3D（找"消失的按键"与"条纹"）。"""
    from rhythm_editor.io.chart_io import load_chart

    chart_path = os.path.join(ROOT, "charts", "test", "chart.json")
    if not os.path.isfile(chart_path):
        print("\n[4] 实谱对比：找不到 charts/test/chart.json，跳过")
        return
    app.chart, _ = load_chart(chart_path)
    app.chart.invalidate_order()
    app.settings.rate = 0.4
    app.settings.speed = 2.6
    print("\n[4] 实谱对比（rate=0.4 / speed=2.6，与用户设置一致）")
    print(f"    可视跨度 {app._visible_span_ms():.0f} 歌曲毫秒，"
          f"物件数 {len(app.chart.all_notes())}")
    bright = lambda frame: (frame.sum(axis=2) > 330)
    worst = []
    for step in range(0, 120):
        now = -500.0 + step * 200.0
        app.screen.fill((10, 10, 14))
        app._draw_planes(now)
        app._draw_notes(now)
        soft = pixels(app.screen)
        panda = pixels(app._render_scene3d(now))
        soft_count = int(bright(soft).sum())
        panda_count = int(bright(panda).sum())
        runs = max((column_runs(bright(panda), x) for x in range(0, panda.shape[1], 17)),
                   default=0)
        worst.append((soft_count - panda_count, now, soft_count, panda_count, runs))
        if step % 20 == 0:
            print(f"    t={now:7.0f}ms  软件亮点 {soft_count:6d}  Panda 亮点 {panda_count:6d}  "
                  f"最大列段数 {runs}")
    worst.sort(reverse=True)
    print("    差异最大的几帧（软件亮点 - Panda 亮点）：")
    for delta, now, soft_count, panda_count, runs in worst[:6]:
        print(f"      t={now:7.0f}ms  差 {delta:6d}（软件 {soft_count} / Panda {panda_count}）"
              f"  Panda 最大列段数 {runs}")
        app.screen.fill((10, 10, 14))
        app._draw_planes(now)
        app._draw_notes(now)
        pygame.image.save(app.screen, os.path.join(out_dir, f"diag_05_soft_{now:.0f}.png"))
        pygame.image.save(app._render_scene3d(now),
                          os.path.join(out_dir, f"diag_05_panda_{now:.0f}.png"))


def soft_scene_chart():
    """一张空谱面（物件由各场景自行塞进去）。"""
    from rhythm_editor.model.chart import Chart

    return Chart()


if __name__ == "__main__":
    raise SystemExit(main())

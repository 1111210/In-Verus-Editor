"""示例谱面与自检：既作为 demo 数据，也作为无界面自检脚本。

* build_demo_chart()  —— 用 Python 代码构造一张包含全部物件类型的示例谱面。
* write_demo_chart()  —— 把示例谱面写到 charts/demo/demo.json。
* selftest()          —— 校验时间轴换算、速度组积分、编辑命令、存档往返。
"""

from __future__ import annotations

import json
import os
from typing import List, Tuple

import config
from chart_io import load_chart, save_chart
from editor_settings import EditorSettings
from model.chart import Chart, Metadata
from model.document import Document
from model.lanes import (
    LaneChangeEvent,
    LaneChangeMap,
    lane_display_index,
    lane_position_index,
    lane_span_length,
)
from model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from model.speed import SpeedSegment
from model.timing import BpmSegment, TimingMap


def project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))       # src/
    candidate = os.path.dirname(here)                       # 仓库根目录
    if os.path.isdir(os.path.join(candidate, "src")):
        return candidate
    return os.getcwd()


def build_demo_chart() -> Chart:
    chart = Chart(
        metadata=Metadata(
            title="Demo: 双平面测试曲",
            artist="Community",
            difficulty_name="演示难度",
            difficulty_level="8",
            author="Codex",
            audio_file="",
        ),
        timing=TimingMap(
            [
                BpmSegment(0.0, 140.0, 4),
                BpmSegment(32.0, 168.0, 4),
                BpmSegment(64.0, 120.0, 3),
            ],
            offset_ms=120.0,
        ),
        settings=EditorSettings(zoom_seconds=3.0, grid_division=4, horizontal_divisions=8,
                                audio_latency_ms=45.0),
    )

    def tap(lane: int, beat: float, beat_end: float | None = None) -> LaneNote:
        note = LaneNote(chart.new_note_id("L"), lane, lane, beat, beat if beat_end is None else beat_end)
        chart.add(note)
        return note

    # 1-4 小节：单轨按键
    for index, beat in enumerate([0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]):
        tap(index % 6, beat)
    for beat in [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5]:
        tap(5 - int(beat * 2) % 6, beat)
    # 多轨按键
    # 注意：要避开同拍/同时段其它轨道的按键与长按，保证示例谱面能通过「自动检查」
    for beat, lanes in ((4.0, (1, 2)), (6.0, (3, 4)), (10.0, (0, 2)), (15.0, (3, 5))):
        note = LaneNote(chart.new_note_id("L"), lanes[0], lanes[1], beat, beat)
        chart.add(note)
    # 长按
    for lane, start, end in ((0, 8.0, 10.0), (3, 9.0, 11.0), (5, 12.0, 14.5), (2, 16.0, 16.75)):
        note = LaneNote(chart.new_note_id("L"), lane, lane, start, end)
        chart.add(note)
    # 长按 + 多轨组合
    hold_multi = LaneNote(chart.new_note_id("L"), 1, 4, 20.0, 22.0)
    chart.add(hold_multi)

    # 临时轨道：三条轨道始终同时出现 / 消失，谱面开始时不存在
    chart.lane_changes = LaneChangeMap(
        [
            LaneChangeEvent(config.LANE_CHANGE_IN, 0.0, 2.0),
            LaneChangeEvent(config.LANE_CHANGE_OUT, 28.0, 30.0),
            LaneChangeEvent(config.LANE_CHANGE_IN, 32.0, 34.0),
            LaneChangeEvent(config.LANE_CHANGE_OUT, 72.0, 74.0),
        ]
    )
    # 临时轨道上的物件：单轨与“原始轨道 + 临时轨道”的多轨组合
    for lane, beat in ((1.5, 18.0), (2.5, 40.0), (3.5, 66.0)):
        note = LaneNote(chart.new_note_id("L"), lane, lane, beat, beat)
        chart.add(note)
    temp_multi = LaneNote(chart.new_note_id("L"), 1, 1.5, 18.5, 18.5)
    chart.add(temp_multi)

    # 24-28 拍：为“加速”速度组准备的物件（跨越变速点，便于观察特效）
    for lane, beat in ((3, 24.0), (2, 25.0), (4, 26.0), (1, 27.0)):
        tap(lane, beat)

    # 48 拍之后流速转负、60 拍再恢复：正负叠加可以把远处的物件推回判定线附近
    for lane, beat in ((0, 72.0), (1, 73.0), (2, 74.0)):
        tap(lane, beat)

    # 空域
    region_specs = [
        (16.0, 20.0, BoundaryCurve(0.25, 0.05, config.EASE_LINEAR), BoundaryCurve(0.75, 0.95, config.EASE_OUT)),
        (24.0, 27.0, BoundaryCurve(0.0, 0.4, config.EASE_IN), BoundaryCurve(1.0, 0.6, config.EASE_IN)),
        (28.0, 30.0, BoundaryCurve(0.6, 0.2, config.EASE_OUT), BoundaryCurve(0.9, 0.8, config.EASE_LINEAR)),
    ]
    for start, end, left, right in region_specs:
        note = MouseRegion(chart.new_note_id("M"), start, end, left, right)
        chart.add(note)

    # 鼠标滑键
    slide_specs = [
        (21.0, 0.2, 0.6),
        (22.0, 0.8, 0.3),
        (23.0, 0.5, 0.875),
        (26.5, 0.125, 0.625),
        (31.0, 0.75, 0.25),
    ]
    for beat, x_start, x_end in slide_specs:
        note = MouseSlide(chart.new_note_id("M"), beat, x_start, x_end)
        chart.add(note)

    roundtrip_slide = MouseSlide(chart.new_note_id("M"), 55.0, 0.25, 0.75)
    chart.add(roundtrip_slide)

    # 速度组：加速 / 反向
    fast = chart.add_group("加速特效", [SpeedSegment(0.0, 1.0), SpeedSegment(24.0, 2.0), SpeedSegment(28.0, 1.0)])
    reverse = chart.add_group("反向特效", [SpeedSegment(0.0, 1.0), SpeedSegment(16.0, -1.5), SpeedSegment(20.0, 1.0)])
    chart.assign_group(
        [n.id for n in chart.all_notes() if 24.0 <= n.head_beat < 28.0 and not isinstance(n, MouseRegion)],
        fast.id,
    )
    chart.assign_group(
        [n.id for n in chart.all_notes() if 16.0 <= n.head_beat < 20.0],
        reverse.id,
    )
    roundtrip = chart.add_group(
        "往返特效",
        [SpeedSegment(0.0, 1.0), SpeedSegment(48.0, -1.0), SpeedSegment(60.0, 1.0)],
    )
    chart.assign_group([n.id for n in chart.all_notes() if n.head_beat >= 48.0], roundtrip.id)
    return chart


def write_demo_chart(path: str | None = None) -> str:
    chart = build_demo_chart()
    if path is None:
        path = os.path.join(project_root(), "charts", "demo", "demo.json")
    written = save_chart(chart, path)
    return written


# ---------------------------------------------------------------- 自检
class _Check:
    def __init__(self) -> None:
        self.failures: List[str] = []
        self.passed = 0

    def ok(self, condition: bool, message: str) -> None:
        if condition:
            self.passed += 1
        else:
            self.failures.append(message)

    def close(self, a: float, b: float, message: str, tol: float = 1e-6) -> None:
        self.ok(abs(a - b) <= tol, f"{message}（{a} != {b}）")


def selftest(verbose: bool = True) -> Tuple[bool, List[str]]:
    check = _Check()
    chart = build_demo_chart()

    # ---- 时间轴 ----
    timing = chart.timing
    check.close(timing.beat_to_seconds(0.0), 0.0, "第 0 拍对应 0 秒")
    check.close(timing.beat_to_seconds(4.0), 4 * 60.0 / 140.0, "140BPM 下 4 拍时长")
    check.close(timing.seconds_to_beat(timing.beat_to_seconds(37.3)), 37.3, "拍/秒往复换算")
    check.close(timing.beat_to_song_ms(0.0), 120.0, "偏移使第 0 拍对应歌曲 120ms")
    check.close(timing.song_ms_to_beat(timing.beat_to_song_ms(20.0)), 20.0, "歌曲位置与拍号互逆")
    check.ok(all(timing.beat_to_seconds(a) < timing.beat_to_seconds(b)
                 for a, b in [(0, 1), (31, 33), (63, 65)]), "时间随拍号单调递增")
    check.close(timing.beat_to_seconds(32.0 + 1.0) - timing.beat_to_seconds(32.0),
                60.0 / 168.0, "变速点之后的秒/拍使用新 BPM")
    check.close(timing.snap_beat(1.37, 4), 1.25, "网格吸附（1/4 拍）")
    check.ok(len(timing.bar_lines(0.0, 8.0)) == 3, "4/4 拍下 0..8 拍有 3 条小节线")

    # ---- 速度组 ----
    default_group = chart.group_by_id(config.DEFAULT_GROUP_ID)
    check.close(default_group.distance(0.0, 4.0), 4.0, "默认流速下距离等于拍差")
    check.close(default_group.distance(4.0, 0.0), -4.0, "反向积分取负")
    fast = chart.group_by_id(1)
    # 24..28 拍流速 2 倍
    check.close(fast.distance(24.0, 26.0), 4.0, "2 倍流速下 2 拍 = 4 个网格距离")
    reverse = chart.group_by_id(2)
    # 判定线在 18 拍、物件判定时刻为 20 拍：反向位移为负（物件在判定线下方）
    check.close(reverse.judging_distance(18.0, 20.0), -3.0, "负流速产生反向位移")
    check.ok(reverse.multiplier_at(18.0) < 0, "17 拍处流速为负")
    check.ok(abs(reverse.distance(16.0, 16.0)) < 1e-9, "判定时刻距离为 0（一定在判定线上）")

    # ---- 物件几何 ----
    region = next(n for n in chart.mouse_notes if isinstance(n, MouseRegion))
    check.close(region.left_at(region.beat_start), region.left.start, "边界起点值")
    check.close(region.left_at(region.beat_end), region.left.end, "边界终点值")
    mid = (region.beat_start + region.beat_end) / 2
    check.ok(region.left_at(mid) <= region.right_at(mid), "左边界恒在右边界左侧")
    slide = next(n for n in chart.mouse_notes if isinstance(n, MouseSlide))
    check.ok(slide.direction in (-1, 1), "滑键方向由首尾位置决定")

    # ---- 临时轨道与 9K 映射 ----
    check.ok(lane_display_index(1.5, False) == 1, "6K 模式临时轨道折叠到左侧原始轨道")
    check.ok(lane_display_index(1.5, True) == 2, "9K 模式 1.5 位于第 3 条轨道")
    check.ok(lane_display_index(2.5, True) == 4, "9K 模式 2.5 位于第 5 条轨道")
    check.ok(lane_display_index(3.5, True) == 6, "9K 模式 3.5 位于第 7 条轨道")
    check.ok(lane_span_length(1, 2) == 3, "轨道 1..2 在 9K 中跨 3 条轨道")
    check.ok(lane_position_index(1.5) == 2, "临时轨道 1.5 的合法位置下标为 2")
    temp_mirror = LaneNote("L-mirror", 1, 1.5, 0.0, 0.0)
    temp_mirror.mirrored()
    check.ok(
        (temp_mirror.lane_start, temp_mirror.lane_end) == (3.5, 4.0),
        "含临时轨道的多轨物件沿中轴线镜像",
    )
    changes = chart.lane_changes
    check.ok(changes.state_at(1.0) == config.LANE_CHANGE_STATE_FADING, "淡入中为过渡状态")
    check.ok(changes.state_at(10.0) == config.LANE_CHANGE_STATE_PRESENT, "淡入后完全出现")
    check.ok(changes.state_at(29.0) == config.LANE_CHANGE_STATE_FADING, "淡出中为过渡状态")
    check.ok(changes.state_at(31.0) == config.LANE_CHANGE_STATE_ABSENT, "淡出后不存在")
    check.ok(changes.validation_error() is None, "示例谱面的轨道数改变事件顺序合法")
    segments = changes.segments(0.0, 40.0)
    check.ok(
        [state for _low, _high, state in segments]
        == [
            config.LANE_CHANGE_STATE_FADING,
            config.LANE_CHANGE_STATE_PRESENT,
            config.LANE_CHANGE_STATE_FADING,
            config.LANE_CHANGE_STATE_ABSENT,
            config.LANE_CHANGE_STATE_FADING,
            config.LANE_CHANGE_STATE_PRESENT,
        ],
        "三条临时轨道按统一事件切分状态",
    )

    # ---- 编辑命令 ----
    document = Document(chart)
    before = len(chart.all_notes())
    new_note = LaneNote(chart.new_note_id("L"), 2, 3, 40.0, 40.0)
    document.add_note(new_note, "测试放置")
    check.ok(len(document.chart.all_notes()) == before + 1, "新增物件")
    document.undo()
    check.ok(len(document.chart.all_notes()) == before, "撤销后物件数还原")
    document.redo()
    check.ok(len(document.chart.all_notes()) == before + 1, "重做后物件恢复")

    document.select_only([new_note.id])
    check.ok(document.copy_selection() == 1, "复制选择")
    created = document.paste_clipboard(60.0)
    check.ok(len(created) == 1, "粘贴 1 个物件")
    check.close(created[0].head_beat, 60.0, "粘贴到目标时间")
    check.ok((created[0].lane_start, created[0].lane_end) == (2, 3), "粘贴保持轨道不变")

    mirror_target = next(n for n in document.chart.lane_notes if n.lane_start == 0 and n.lane_end == 0)
    document.select_only([mirror_target.id])
    document.mirror_notes([mirror_target.id])
    mirrored = document.chart.find(mirror_target.id)
    check.ok(mirrored.lane_start == 5 and mirrored.lane_end == 5, "6K 镜像沿中轴线翻转轨道")
    region_target = next(n for n in document.chart.mouse_notes if isinstance(n, MouseRegion))
    old_left = region_target.left.start
    document.mirror_notes([region_target.id])
    check.close(document.chart.find(region_target.id).right.start, 1.0 - old_left, "区域镜像左右边界互换")
    document.undo()
    document.undo()
    check.ok(len(document.chart.all_notes()) == before + 2, "连续撤销回到粘贴后的状态")

    # ---- 剪切：复制 + 删除，整体一步撤销 ----
    document.select_only([created[0].id])
    check.ok(document.cut_selection() == 1, "剪切选择")
    check.ok(document.chart.find(created[0].id) is None, "剪切后物件从谱面移除")
    check.ok(len(document.clipboard) == 1, "剪切后剪贴板仍保留数据")
    document.undo()
    check.ok(document.chart.find(created[0].id) is not None, "撤销剪切恢复物件")
    check.ok(document.selection == {created[0].id}, "撤销剪切恢复选择")
    document.redo()
    check.ok(document.chart.find(created[0].id) is None, "重做剪切再次删除")
    document.undo()
    check.ok(len(document.chart.all_notes()) == before + 2, "撤销重做后回到剪切前状态")

    # ---- 速度组成员双向一致 ----
    for group in document.chart.speed_groups:
        for note_id in group.note_ids:
            note = document.chart.find(note_id)
            check.ok(note is not None and note.group == group.id, f"{note_id} 与速度组 {group.id} 双向一致")
    document.select_only([new_note.id])
    document.set_selection_group(1)
    check.ok(document.chart.find(new_note.id).group == 1, "设置物件速度组")
    document.chart.delete_group(1)
    check.ok(document.chart.find(new_note.id).group == config.DEFAULT_GROUP_ID, "删除速度组后物件回到默认组")
    check.ok(not document.chart.delete_group(config.DEFAULT_GROUP_ID), "默认组不可删除")

    # ---- 轨道数改变可撤销 ----
    original_changes = list(document.chart.lane_changes.events)
    replacement = LaneChangeMap(
        [LaneChangeEvent(config.LANE_CHANGE_IN, 30.0, 32.0)]
    )
    document.apply(
        "测试轨道数改变",
        lambda: setattr(document.chart, "lane_changes", replacement),
    )
    check.ok(len(document.chart.lane_changes) == 1, "修改轨道数改变")
    document.undo()
    check.ok(
        list(document.chart.lane_changes.events) == original_changes,
        "撤销轨道数改变",
    )
    document.redo()
    check.ok(len(document.chart.lane_changes) == 1, "重做轨道数改变")
    document.undo()
    check.ok(
        list(document.chart.lane_changes.events) == original_changes,
        "再次撤销轨道数改变",
    )

    # ---- 存档往返 ----
    folder = os.path.join(project_root(), "charts", "demo")
    path = os.path.join(folder, "_selftest.json")
    os.makedirs(folder, exist_ok=True)
    written = save_chart(document.chart, path)
    reloaded, _raw = load_chart(written)
    check.ok(reloaded.to_dict() == document.chart.to_dict(), "保存/读取后谱面完全一致")
    old_data = reloaded.to_dict()
    old_data.pop("lane_changes", None)
    old_chart = Chart.from_dict(old_data)
    check.ok(old_chart.lane_changes.is_empty, "旧谱面没有轨道数改变字段时按全程 6K 加载")
    check.ok(
        all(isinstance(n.lane_start, float) for n in old_chart.lane_notes),
        "旧谱面的整数轨道编号可正常加载",
    )
    with open(written, "r", encoding="utf-8") as handle:
        json.load(handle)
    os.remove(written)

    if verbose:
        print(f"自检通过 {check.passed} 项，失败 {len(check.failures)} 项")
        for failure in check.failures:
            print("  ✗", failure)
    return (not check.failures), check.failures


if __name__ == "__main__":  # pragma: no cover
    ok, _ = selftest()
    raise SystemExit(0 if ok else 1)

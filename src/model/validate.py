"""谱面自动检查（纯数据层，便于单元测试）。

检查规则
--------
6K 下落式（单轨 / 多轨 / 长按，含它们之间的任意组合）：

* 同一轨道在**任意时刻只能存在一个按键**；
* 两条物件的轨道范围相交、且**时间范围相交**即视为冲突：
  按键互相重叠、长按/多轨按键覆盖别的按键、长按之间相交、多轨按键相交、
  长按与多轨按键相交…… 全部由这一条规则覆盖；
* **端点相接（紧贴）不算冲突**：例如长按在第 10 拍结束、另一个按键正好在第 10 拍 —— 允许。
  但瞬时按键落在长按/多轨按键的**内部**要报错。

鼠标平面：**只检查滑键**。

* 滑键所在时刻如果没有任何空域 —— 通过；
* 如果该时刻存在空域，则滑键的横向范围必须与该空域的横向范围**相交或紧贴**，否则报错。

说明：规则的判定对象是“判定时刻”，速度组只影响视觉表现，不影响判定时间。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .chart import Chart
from .notes import LaneNote, MouseClick, MouseRegion, MouseSlide, Note

EPS = 1e-7
REGION_BUCKET_BEATS = 4.0   # 空域按 4 拍分桶，避免大谱面上滑键检查退化成 O(n·m)

ISSUE_LANE = "lane_overlap"
ISSUE_SLIDE = "slide_region"
ISSUE_CLICK = "click_region"
ISSUE_CLICK_SAME_BEAT = "click_same_beat"


@dataclass
class Issue:
    """一条检查结果。"""

    kind: str
    message: str
    note_ids: Tuple[str, ...]

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return self.message


# ------------------------------------------------------------------ 基础判定
def intervals_conflict(a0: float, a1: float, b0: float, b1: float, eps: float = EPS) -> bool:
    """两个时刻区间是否冲突（端点相接允许，瞬时点落在区间内部算冲突）。"""
    a_point = abs(a1 - a0) <= eps
    b_point = abs(b1 - b0) <= eps
    if a_point and b_point:
        return abs(a0 - b0) <= eps
    if a_point:
        return (b0 + eps) < a0 < (b1 - eps)
    if b_point:
        return (a0 + eps) < b0 < (a1 - eps)
    return min(a1, b1) - max(a0, b0) > eps


def ranges_touch_or_overlap(lo1: float, hi1: float, lo2: float, hi2: float,
                            eps: float = 1e-6) -> bool:
    """两个横向范围是否相交或紧贴。"""
    return not (hi1 < lo2 - eps or hi2 < lo1 - eps)


def lane_range_overlap(a: LaneNote, b: LaneNote) -> Tuple[int, int] | None:
    lo = max(a.lane_start, b.lane_start)
    hi = min(a.lane_end, b.lane_end)
    return (lo, hi) if lo <= hi else None


def _beat_text(note: Note) -> str:
    if isinstance(note, MouseSlide):
        return f"第 {note.beat:g} 拍"
    if abs(note.tail_beat - note.head_beat) <= EPS:
        return f"第 {note.head_beat:g} 拍"
    return f"第 {note.head_beat:g} → {note.tail_beat:g} 拍"


def _lane_text(note: LaneNote) -> str:
    if note.lane_start == note.lane_end:
        return f"轨道 {note.lane_start + 1}"
    return f"轨道 {note.lane_start + 1}-{note.lane_end + 1}"


# ------------------------------------------------------------------ 检查
def check_lane_notes(chart: Chart) -> List[Issue]:
    """6K 部分：同轨道任意时刻只能有一个按键。"""
    issues: List[Issue] = []
    notes = sorted(chart.lane_notes, key=lambda n: (n.beat_start, n.lane_start, n.id))
    for index, a in enumerate(notes):
        for other in notes[index + 1:]:
            # 后面的物件起点只会更晚；一旦超出 a 的结束时刻就不可能再相交
            if other.beat_start > a.beat_end + EPS:
                break
            lanes = lane_range_overlap(a, other)
            if lanes is None:
                continue
            if not intervals_conflict(a.beat_start, a.beat_end, other.beat_start, other.beat_end):
                continue
            lo, hi = lanes
            lane_text = f"轨道 {lo + 1}" if lo == hi else f"轨道 {lo + 1}-{hi + 1}"
            issues.append(
                Issue(
                    ISSUE_LANE,
                    f"{a.id}（{_lane_text(a)}，{_beat_text(a)}）与 "
                    f"{other.id}（{_lane_text(other)}，{_beat_text(other)}）"
                    f"在 {lane_text} 上重叠",
                    (a.id, other.id),
                )
            )
    return issues


def _build_region_buckets(regions: Sequence[MouseRegion]) -> Dict[int, List[MouseRegion]]:
    buckets: Dict[int, List[MouseRegion]] = {}
    for region in regions:
        start = int(region.beat_start // REGION_BUCKET_BEATS)
        end = int(region.beat_end // REGION_BUCKET_BEATS)
        for cell in range(start, end + 1):
            buckets.setdefault(cell, []).append(region)
    return buckets


def check_slides(chart: Chart) -> List[Issue]:
    """鼠标部分：滑键所在时刻要么没有空域，要么与空域相交 / 紧贴。"""
    issues: List[Issue] = []
    regions = [n for n in chart.mouse_notes if isinstance(n, MouseRegion)]
    slides = [n for n in chart.mouse_notes if isinstance(n, MouseSlide)]
    if not slides or not regions:
        return issues
    buckets = _build_region_buckets(regions)
    for slide in slides:
        cell = int(slide.beat // REGION_BUCKET_BEATS)
        active = [
            region
            for region in buckets.get(cell, ())
            if region.beat_start - EPS <= slide.beat <= region.beat_end + EPS
        ]
        if not active:
            continue
        x_lo, x_hi = slide.x_range()
        hit = False
        for region in active:
            left = region.left_at(slide.beat)
            right = region.right_at(slide.beat)
            if ranges_touch_or_overlap(x_lo, x_hi, min(left, right), max(left, right)):
                hit = True
                break
        if hit:
            continue
        detail = "；".join(
            f"{region.id}（{_beat_text(region)}，横向 "
            f"{min(region.left_at(slide.beat), region.right_at(slide.beat)):g} → "
            f"{max(region.left_at(slide.beat), region.right_at(slide.beat)):g}）"
            for region in active
        )
        issues.append(
            Issue(
                ISSUE_SLIDE,
                f"{slide.id}（{_beat_text(slide)}，横向 {x_lo:g} → {x_hi:g}）："
                f"该时刻存在空域 {detail}，但滑键范围与空域不相交也不紧贴",
                (slide.id, *(region.id for region in active)),
            )
        )
    return issues


def check_clicks(chart: Chart) -> List[Issue]:
    """鼠标点击按键：

    * 与滑键相同的规则 —— 所在时刻要么没有空域，要么与空域相交 / 紧贴；
    * 额外限制 —— **同一时刻不能出现两个鼠标点击按键**。
    """
    issues: List[Issue] = []
    clicks = [n for n in chart.mouse_notes if isinstance(n, MouseClick)]
    if not clicks:
        return issues
    regions = [n for n in chart.mouse_notes if isinstance(n, MouseRegion)]
    buckets = _build_region_buckets(regions) if regions else {}

    # 1) 同一时刻只能有一个点击按键
    ordered = sorted(clicks, key=lambda n: n.beat)
    for index, first in enumerate(ordered):
        for second in ordered[index + 1:]:
            if second.beat - first.beat > EPS:
                break
            if abs(second.beat - first.beat) <= EPS:
                issues.append(
                    Issue(
                        ISSUE_CLICK_SAME_BEAT,
                        f"{first.id} 与 {second.id} 都位于{_beat_text(first)}："
                        f"同一时刻不能出现两个鼠标点击按键",
                        (first.id, second.id),
                    )
                )

    # 2) 与滑键一致：该时刻有空域时必须相交 / 紧贴
    for click in clicks:
        cell = int(click.beat // REGION_BUCKET_BEATS)
        active = [
            region
            for region in buckets.get(cell, ())
            if region.beat_start - EPS <= click.beat <= region.beat_end + EPS
        ]
        if not active:
            continue
        x_lo, x_hi = click.x_range()
        hit = False
        for region in active:
            left = region.left_at(click.beat)
            right = region.right_at(click.beat)
            if ranges_touch_or_overlap(x_lo, x_hi, min(left, right), max(left, right)):
                hit = True
                break
        if hit:
            continue
        detail = "；".join(
            f"{region.id}（{_beat_text(region)}，横向 "
            f"{min(region.left_at(click.beat), region.right_at(click.beat)):g} → "
            f"{max(region.left_at(click.beat), region.right_at(click.beat)):g}）"
            for region in active
        )
        issues.append(
            Issue(
                ISSUE_CLICK,
                f"{click.id}（{_beat_text(click)}，横向 {x_lo:g} → {x_hi:g}）："
                f"该时刻存在空域 {detail}，但点击按键范围与空域不相交也不紧贴",
                (click.id, *(region.id for region in active)),
            )
        )
    return issues


def validate_chart(chart: Chart) -> List[Issue]:
    """执行全部检查，返回问题列表（空列表 = 通过）。"""
    return check_lane_notes(chart) + check_slides(chart) + check_clicks(chart)


# ------------------------------------------------------------------ 报告
def format_report(chart: Chart, issues: Sequence[Issue]) -> str:
    """把检查结果整理成可读文本（用于弹窗展示）。"""
    counts = chart.note_counts()
    lines = [
        "自动检查范围：",
        f"  · 6K 下落式：{counts['lane']} 个物件（单轨 / 多轨 / 长按）",
        f"  · 空域：{counts['region']} 个   滑键：{counts['slide']} 个   "
        f"点击按键：{counts['click']} 个",
        "",
        "检查规则：",
        "  · 任意轨道在任意时刻只能存在一个按键；长按 / 多轨按键与其它按键重叠都算冲突",
        "    （端点相接的“紧贴”不算冲突，但瞬时按键落在长按内部算冲突）",
        "  · 滑键所在时刻若存在空域，则滑键横向范围必须与该空域相交或紧贴",
        "  · 鼠标点击按键与滑键规则相同，且同一时刻不能出现两个点击按键",
        "",
    ]
    lane_issues = [i for i in issues if i.kind == ISSUE_LANE]
    slide_issues = [i for i in issues if i.kind == ISSUE_SLIDE]
    click_issues = [
        i for i in issues if i.kind in (ISSUE_CLICK, ISSUE_CLICK_SAME_BEAT)
    ]
    if not issues:
        lines.append("检查结果：未发现问题 ✓")
        return "\n".join(lines)
    lines.append(f"检查结果：共发现 {len(issues)} 处问题（相关物件已在画布中选中）")
    lines.append("")
    if lane_issues:
        lines.append(f"【6K 轨道冲突】共 {len(lane_issues)} 处")
        for index, issue in enumerate(lane_issues, 1):
            lines.append(f"  {index}. {issue.message}")
        lines.append("")
    if slide_issues:
        lines.append(f"【滑键与空域】共 {len(slide_issues)} 处")
        for index, issue in enumerate(slide_issues, 1):
            lines.append(f"  {index}. {issue.message}")
        lines.append("")
    if click_issues:
        lines.append(f"【鼠标点击按键】共 {len(click_issues)} 处")
        for index, issue in enumerate(click_issues, 1):
            lines.append(f"  {index}. {issue.message}")
        lines.append("")
    return "\n".join(lines)

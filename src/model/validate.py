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

鼠标平面：滑键与空域，外加禁区覆盖检查。

* 滑键所在时刻如果没有任何空域 —— 通过；
* 如果该时刻存在空域（**空域的开始 / 结束时刻不算“同时存在”**），
  则滑键的横向范围必须与该空域的横向范围**相交或紧贴**，否则报错。

禁区（与滑键 / 空域相对）：

* 滑键与空域的**任意时刻**都不能被禁区**完全覆盖**（横向范围整段被盖住）；
* 覆盖按**并集**计算：多个禁区在同一时刻的横向范围拼起来也算数；
* 禁区的**开始 / 结束时刻同样计入**（那一刻禁区已经生效）。

说明：规则的判定对象是“判定时刻”，速度组只影响视觉表现，不影响判定时间。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .chart import Chart
from .notes import ForbiddenRegion, LaneNote, MouseRegion, MouseSlide, Note

EPS = 1e-7
REGION_BUCKET_BEATS = 4.0   # 空域按 4 拍分桶，避免大谱面上滑键检查退化成 O(n·m)
# 禁区覆盖检查：空域在每个检查时刻上采样（每拍多少个点 / 最多多少个点）
FORBIDDEN_SAMPLES_PER_BEAT = 4.0
FORBIDDEN_MAX_SAMPLES = 400

ISSUE_LANE = "lane_overlap"
ISSUE_SLIDE = "slide_region"
ISSUE_FORBIDDEN = "forbidden_cover"


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


def intervals_cover(
    lo: float, hi: float, intervals: Sequence[Tuple[float, float]], eps: float = 1e-6
) -> bool:
    """``[lo, hi]`` 是否被若干区间（取并集）**完全覆盖**。"""
    if hi < lo:
        lo, hi = hi, lo
    merged: List[Tuple[float, float]] = []
    for raw_lo, raw_hi in sorted(intervals):
        start, end = min(raw_lo, raw_hi), max(raw_lo, raw_hi)
        if merged and start <= merged[-1][1] + eps:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return any(start <= lo + eps and hi <= end + eps for start, end in merged)


def region_active_at(region: MouseRegion, beat: float, eps: float = EPS) -> bool:
    """``beat`` 处空域是否“同时存在”。

    **空域的开始 / 结束时刻不算同时存在**：滑键正好落在空域的起止
    那一刻时不参与这条检查；只有落在空域内部，才要求横向范围相交 / 紧贴。
    """
    return region.beat_start + eps < beat < region.beat_end - eps


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
    """滑键：所在时刻要么没有空域，要么与空域相交 / 紧贴。

    空域的开始 / 结束时刻不算“同时存在”，滑键正好落在那一刻时不检查。
    """
    issues: List[Issue] = []
    regions = [
        n for n in chart.mouse_notes
        if isinstance(n, MouseRegion) and not isinstance(n, ForbiddenRegion)
    ]
    slides = [n for n in chart.mouse_notes if isinstance(n, MouseSlide)]
    if not slides or not regions:
        return issues
    buckets = _build_region_buckets(regions)
    for slide in slides:
        cell = int(slide.beat // REGION_BUCKET_BEATS)
        active = [
            region
            for region in buckets.get(cell, ())
            if region_active_at(region, slide.beat)
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


def validate_chart(chart: Chart) -> List[Issue]:
    """执行全部检查，返回问题列表（空列表 = 通过）。"""
    return check_lane_notes(chart) + check_slides(chart) + check_forbidden(chart)


def _check_beats(start: float, end: float) -> List[float]:
    """覆盖检查的采样拍：均匀取样，跨度大时限制上限。"""
    start, end = float(start), float(end)
    if end - start <= EPS:
        return [start]
    count = int(min(FORBIDDEN_MAX_SAMPLES, max(3, int((end - start) * FORBIDDEN_SAMPLES_PER_BEAT) + 1)))
    return [start + (end - start) * index / (count - 1) for index in range(count)]


def _forbidden_active(
    buckets: Dict[int, List[MouseRegion]], beat: float
) -> List[MouseRegion]:
    """该时刻“生效”的禁区：**包含开始 / 结束时刻**（端点那一刻也算盖住）。"""
    return [
        zone
        for zone in buckets.get(int(beat // REGION_BUCKET_BEATS), ())
        if zone.beat_start - EPS <= beat <= zone.beat_end + EPS
    ]


def check_forbidden(chart: Chart) -> List[Issue]:
    """禁区覆盖：滑键与空域在任意时刻都不能被（一个或多个）禁区完全覆盖。

    对空域在其时间跨度内采样（``_check_beats``），对滑键检查它的判定时刻；
    每一时刻把生效禁区的横向范围取**并集**，并集完整盖住目标范围才算问题。
    禁区的开始 / 结束时刻**计入**（那一刻禁区已经生效）。
    """
    issues: List[Issue] = []
    zones = [n for n in chart.mouse_notes if isinstance(n, ForbiddenRegion)]
    if not zones:
        return issues
    targets: List[MouseRegion | MouseSlide] = [
        n for n in chart.mouse_notes
        if isinstance(n, MouseSlide)
        or (isinstance(n, MouseRegion) and not isinstance(n, ForbiddenRegion))
    ]
    if not targets:
        return issues
    buckets = _build_region_buckets(zones)
    for target in targets:
        if isinstance(target, MouseSlide):
            beats = [target.beat]
        else:
            beats = _check_beats(target.beat_start, target.beat_end)
        for beat in beats:
            active = _forbidden_active(buckets, beat)
            if not active:
                continue
            if isinstance(target, MouseSlide):
                lo, hi = target.x_range()
            else:
                lo, hi = target.left_at(beat), target.right_at(beat)
            intervals = [
                (zone.left_at(beat), zone.right_at(beat)) for zone in active
            ]
            if not intervals_cover(lo, hi, intervals):
                continue
            x_lo, x_hi = min(lo, hi), max(lo, hi)
            zone_ids = "、".join(zone.id for zone in active)
            if isinstance(target, MouseSlide):
                message = (
                    f"{target.id}（{_beat_text(target)}，横向 {x_lo:g} → {x_hi:g}）："
                    f"被禁区 {zone_ids} 完全覆盖"
                )
            else:
                message = (
                    f"{target.id}（{_beat_text(target)}）：第 {beat:g} 拍横向 "
                    f"{x_lo:g} → {x_hi:g} 被禁区 {zone_ids} 完全覆盖"
                )
            issues.append(
                Issue(
                    ISSUE_FORBIDDEN,
                    message,
                    (target.id, *(zone.id for zone in active)),
                )
            )
            break
    return issues


# ------------------------------------------------------------------ 报告
def format_report(chart: Chart, issues: Sequence[Issue]) -> str:
    """把检查结果整理成可读文本（用于弹窗展示）。"""
    counts = chart.note_counts()
    lines = [
        "自动检查范围：",
        f"  · 6K 下落式：{counts['lane']} 个物件（单轨 / 多轨 / 长按）",
        f"  · 空域：{counts['region']} 个   禁区：{counts['forbidden']} 个   "
        f"滑键：{counts['slide']} 个",
        "",
        "检查规则：",
        "  · 任意轨道在任意时刻只能存在一个按键；长按 / 多轨按键与其它按键重叠都算冲突",
        "    （端点相接的“紧贴”不算冲突，但瞬时按键落在长按内部算冲突）",
        "  · 滑键所在时刻若落在空域内部（开始 / 结束时刻不算），",
        "    则滑键横向范围必须与该空域相交或紧贴",
        "  · 滑键与空域的任意时刻都不能被禁区完全覆盖；多个禁区的横向范围会合并计算",
        "    （禁区的开始 / 结束时刻同样计入）",
        "",
    ]
    lane_issues = [i for i in issues if i.kind == ISSUE_LANE]
    slide_issues = [i for i in issues if i.kind == ISSUE_SLIDE]
    forbidden_issues = [i for i in issues if i.kind == ISSUE_FORBIDDEN]
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
    if forbidden_issues:
        lines.append(f"【禁区覆盖】共 {len(forbidden_issues)} 处")
        for index, issue in enumerate(forbidden_issues, 1):
            lines.append(f"  {index}. {issue.message}")
        lines.append("")
    return "\n".join(lines)

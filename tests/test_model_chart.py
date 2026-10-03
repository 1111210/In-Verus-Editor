"""谱面 / 文档 / 存档：分组、撤销、排序缓存与 JSON 读写。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import filecmp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import config
from rhythm_editor.io.chart_io import (
    chart_audio_path,
    copy_audio_into_chart_dir,
    ensure_chart_suffix,
    load_chart,
    save_chart,
)
from rhythm_editor.model.chart import Chart, EditorSettings, Metadata
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from rhythm_editor.model.speed import SpeedSegment
from rhythm_editor.model.timing import BpmSegment, TimingMap


def make_chart() -> Chart:
    chart = Chart(
        metadata=Metadata(title="t", artist="a", difficulty_name="d", difficulty_level="9",
                          author="me", audio_file="audio/x.mp3"),
        timing=TimingMap([BpmSegment(0.0, 150.0, 4), BpmSegment(16.0, 100.0, 3)], offset_ms=90.0),
        settings=EditorSettings(zoom_seconds=2.5, grid_division=8, horizontal_divisions=4,
                                audio_latency_ms=30.0),
    )
    chart.add(LaneNote("L1", 0, 0, 0.0))
    chart.add(LaneNote("L2", 1, 3, 2.0, 4.0))
    chart.add(MouseRegion("M1", 0.0, 4.0, BoundaryCurve(0.0, 0.2), BoundaryCurve(1.0, 0.8)))
    chart.add(MouseSlide("M2", 6.0, 0.3, 0.7))
    return chart


class ChartTests(unittest.TestCase):
    def test_settings_clamp_and_ids_unique(self) -> None:
        settings = EditorSettings(grid_division=256, horizontal_divisions=128, show_past_notes=True)
        settings.normalize()
        self.assertEqual((settings.grid_division, settings.horizontal_divisions), (256, 128))
        settings = EditorSettings(grid_division=0, horizontal_divisions=-5)
        settings.normalize()
        self.assertEqual(settings.grid_division, config.GRID_DIVISION_MIN)
        self.assertEqual(settings.horizontal_divisions, 0)
        chart = make_chart()
        ids = [chart.new_note_id("L") for _ in range(5)]
        self.assertEqual(len(set(ids)), 5)
        self.assertNotIn("L1", ids)

    def test_group_bookkeeping(self) -> None:
        chart = make_chart()
        default = chart.group_by_id(config.DEFAULT_GROUP_ID)
        self.assertCountEqual(default.note_ids, ["L1", "L2", "M1", "M2"])
        fast = chart.add_group("快", [SpeedSegment(0.0, 1.0), SpeedSegment(4.0, 3.0)])
        chart.assign_group(["L1"], fast.id)
        self.assertEqual(chart.find("L1").group, fast.id)
        self.assertNotIn("L1", default.note_ids)
        self.assertTrue(chart.delete_group(fast.id))
        self.assertEqual(chart.find("L1").group, config.DEFAULT_GROUP_ID)
        self.assertFalse(chart.delete_group(config.DEFAULT_GROUP_ID))

    def test_remove_counts_and_bounds(self) -> None:
        chart = make_chart()
        self.assertEqual(len(chart.remove(["M1"])), 1)
        counts = chart.note_counts()
        self.assertEqual((counts["lane"], counts["region"], counts["slide"]), (2, 0, 1))
        low, high = chart.beat_bounds()
        self.assertLessEqual(low, 0.0)
        self.assertGreaterEqual(high, 16.0)

    def test_serialization_and_group_repair(self) -> None:
        chart = make_chart()
        data = chart.to_dict()
        restored = Chart.from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        self.assertNotIn("editor", data, "编辑器设置不写进谱面")
        broken = dict(data, speed_groups=[])
        repaired = Chart.from_dict(broken)
        self.assertTrue(any(g.is_default for g in repaired.speed_groups))
        for note in repaired.all_notes():
            self.assertEqual(note.group, config.DEFAULT_GROUP_ID)


class DocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = Document(make_chart())

    def test_add_undo_redo_restores_selection(self) -> None:
        self.document.select_only(["L1"])
        count = len(self.document.chart.all_notes())
        self.document.add_note(LaneNote("L10", 5, 5, 8.0))
        self.assertTrue(self.document.dirty)
        self.document.select_only(["L10"])
        self.assertTrue(self.document.undo())
        self.assertEqual(len(self.document.chart.all_notes()), count)
        self.assertEqual(self.document.selection, {"L1"})
        self.assertTrue(self.document.redo())
        self.assertIsNotNone(self.document.chart.find("L10"))

    def test_copy_paste_mirror_and_replace(self) -> None:
        self.document.select_only(["L2", "M2"])
        self.assertEqual(self.document.copy_selection(), 2)
        created = self.document.paste_clipboard(20.0)
        lane_new = next(n for n in created if isinstance(n, LaneNote))
        self.assertEqual((lane_new.lane_start, lane_new.lane_end), (1, 3))
        self.assertAlmostEqual(lane_new.beat_start, 20.0)
        self.assertAlmostEqual(lane_new.beat_end, 22.0)
        self.document.mirror_notes(["L1", "M2"])
        self.assertEqual(self.document.chart.find("L1").lane_start, 5)
        self.assertAlmostEqual(self.document.chart.find("M2").x_start, 0.7)
        group_id = self.document.chart.add_group("g").id
        self.document.select_only(["L1"])
        self.document.set_selection_group(group_id)
        data = self.document.chart.find("L1").to_dict()
        data["lane_start"], data["lane_end"] = 3, 4
        data.pop("group")
        replaced = self.document.replace_note("L1", data)
        self.assertEqual((replaced.lane_start, replaced.lane_end), (3, 4))
        self.assertEqual(replaced.group, group_id)

    def test_delete_undo_and_record_size(self) -> None:
        self.document.delete_notes(["L1", "M1"])
        self.assertIsNone(self.document.chart.find("L1"))
        self.document.undo()
        self.assertIsNotNone(self.document.chart.find("M1"))
        self.assertEqual(self.document.undo_records_size(), 0)
        for index in range(10):
            self.document.add_note(LaneNote(f"X{index}", 0, 0, 100.0 + index))
        self.assertEqual(self.document.undo_records_size(), 10, "记录量只与改动量有关")
        capped = Document(make_chart())
        capped.max_undo = 5
        for index in range(12):
            capped.add_note(LaneNote(f"C{index}", 0, 0, 200.0 + index))
        self.assertEqual(capped.undo_depth, 5)
        for _ in range(5):
            self.assertTrue(capped.undo())
        self.assertFalse(capped.undo())

    def test_undo_roundtrip_for_structural_edits(self) -> None:
        def assert_roundtrip(action) -> None:
            before = self.document.chart.to_dict()
            action()
            after = self.document.chart.to_dict()
            self.assertNotEqual(before, after)
            self.assertTrue(self.document.undo())
            self.assertEqual(self.document.chart.to_dict(), before)
            self.assertTrue(self.document.redo())
            self.assertEqual(self.document.chart.to_dict(), after)

        assert_roundtrip(lambda: self.document.mirror_notes(["L1", "L2", "M2"]))
        data = self.document.chart.find("M1").to_dict()
        data["left"] = {"start": -0.3, "end": 0.4, "ease": config.EASE_IN}
        assert_roundtrip(lambda: self.document.replace_note("M1", data))
        assert_roundtrip(
            lambda: self.document.apply(
                "改 BPM", lambda: setattr(self.document.chart, "timing", TimingMap([BpmSegment(0.0, 90.0, 3)]))
            )
        )

    def test_editor_settings_are_not_undoable(self) -> None:
        self.assertFalse(self.document.can_undo())
        self.document.chart.settings.grid_division = 7
        self.assertFalse(self.document.can_undo())
        self.document.apply("改元数据", lambda: setattr(self.document.chart.metadata, "title", "改了"))
        self.document.undo()
        self.assertEqual(self.document.chart.settings.grid_division, 7)


class SortedCacheTests(unittest.TestCase):
    def test_cache_reuse_and_invalidation(self) -> None:
        chart = Chart()
        for index, beat in enumerate((8.0, 2.0, 4.0)):
            chart.add(LaneNote(f"L{index + 1}", index % 6, index % 6, beat))
        first = chart.sorted_notes()
        self.assertIs(first, chart.sorted_notes(), "同一份缓存应被复用")
        self.assertEqual([n.head_beat for n in first], [2.0, 4.0, 8.0])
        Document(chart).add_note(MouseSlide("M1", 0.5, 0.2, 0.6))
        self.assertIsNot(first, chart.sorted_notes())
        self.assertEqual(chart.sorted_notes()[0].id, "M1")


class ChartIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.chart = make_chart()

    def test_suffix_and_roundtrip(self) -> None:
        self.assertTrue(ensure_chart_suffix("song").endswith(".json"))
        self.assertTrue(ensure_chart_suffix("song.ivchart").endswith("song.json"))
        path = os.path.join(self.tmp.name, "chart.json")
        written = save_chart(self.chart, path)
        chart, raw = load_chart(written)
        self.assertEqual(chart.to_dict(), self.chart.to_dict())
        self.assertEqual(raw["format"], "inverus.chart")

    def test_audio_copy_and_lookup(self) -> None:
        audio_src = os.path.join(self.tmp.name, "song.mp3")
        with open(audio_src, "wb") as handle:
            handle.write(b"ID3fake")
        chart_path = os.path.join(self.tmp.name, "level", "chart.json")
        os.makedirs(os.path.dirname(chart_path), exist_ok=True)
        relative = copy_audio_into_chart_dir(audio_src, chart_path)
        self.assertEqual(relative, "song.mp3")
        copied = os.path.join(self.tmp.name, "level", "song.mp3")
        self.assertTrue(os.path.isfile(copied))
        self.chart.metadata.audio_file = relative
        self.assertEqual(os.path.abspath(chart_audio_path(chart_path, self.chart)), os.path.abspath(copied))
        self.chart.metadata.audio_file = "audio/none.mp3"
        self.assertIsNone(chart_audio_path(chart_path, self.chart))


class FormatFreezeTests(unittest.TestCase):
    """谱面格式冻结：性能优化只改内存结构，写出的 JSON 必须与磁盘文件逐字节一致。"""

    def _assert_roundtrip_is_byte_identical(self, relative: str) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, relative)
        if not os.path.isfile(path):
            self.skipTest(f"仓库内没有 {relative}")
        chart, _raw = load_chart(path)
        with tempfile.TemporaryDirectory() as tmp:
            written = save_chart(chart, os.path.join(tmp, "out.json"))
            self.assertTrue(
                filecmp.cmp(path, written, shallow=False),
                f"{relative} 重新保存后必须与磁盘文件逐字节一致",
            )

    def test_demo_chart_roundtrip_is_byte_identical(self) -> None:
        self._assert_roundtrip_is_byte_identical(os.path.join("charts", "demo", "demo.json"))

    def test_large_local_chart_roundtrip_if_present(self) -> None:
        self._assert_roundtrip_is_byte_identical(os.path.join("charts", "test", "chart.json"))


if __name__ == "__main__":
    unittest.main()

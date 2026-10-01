from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import config
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
    def test_settings_have_no_grid_upper_limit(self) -> None:
        """时间网格 / 横向辅助网格都不再有上限（只保留最小值）。"""
        settings = EditorSettings(grid_division=256, horizontal_divisions=128)
        settings.normalize()
        self.assertEqual(settings.grid_division, 256)
        self.assertEqual(settings.horizontal_divisions, 128)
        settings = EditorSettings(grid_division=0, horizontal_divisions=-5)
        settings.normalize()
        self.assertEqual(settings.grid_division, config.GRID_DIVISION_MIN)
        self.assertEqual(settings.horizontal_divisions, 0)

    def test_ids_unique_and_registered(self) -> None:
        chart = make_chart()
        ids = [chart.new_note_id("L") for _ in range(5)]
        self.assertEqual(len(set(ids)), 5)
        self.assertNotIn("L1", ids)
        self.assertNotIn("L2", ids)

    def test_group_bookkeeping(self) -> None:
        chart = make_chart()
        default = chart.group_by_id(config.DEFAULT_GROUP_ID)
        self.assertCountEqual(default.note_ids, ["L1", "L2", "M1", "M2"])
        fast = chart.add_group("快", [SpeedSegment(0.0, 1.0), SpeedSegment(4.0, 3.0)])
        chart.assign_group(["L1"], fast.id)
        self.assertEqual(chart.find("L1").group, fast.id)
        self.assertNotIn("L1", default.note_ids)
        self.assertIn("L1", fast.note_ids)
        self.assertTrue(chart.delete_group(fast.id))
        self.assertEqual(chart.find("L1").group, config.DEFAULT_GROUP_ID)
        self.assertIn("L1", chart.group_by_id(config.DEFAULT_GROUP_ID).note_ids)
        self.assertFalse(chart.delete_group(config.DEFAULT_GROUP_ID))

    def test_remove_and_counts(self) -> None:
        chart = make_chart()
        removed = chart.remove(["M1"])
        self.assertEqual(len(removed), 1)
        counts = chart.note_counts()
        self.assertEqual(counts["lane"], 2)
        self.assertEqual(counts["region"], 0)
        self.assertEqual(counts["slide"], 1)
        self.assertNotIn("M1", chart.group_by_id(config.DEFAULT_GROUP_ID).note_ids)

    def test_serialization_roundtrip(self) -> None:
        chart = make_chart()
        data = chart.to_dict()
        restored = Chart.from_dict(data)
        self.assertEqual(restored.to_dict(), data)
        self.assertAlmostEqual(restored.timing.offset_ms, 90.0)
        self.assertEqual(len(restored.lane_notes), 2)
        self.assertEqual(len(restored.mouse_notes), 2)
        # 编辑器设置（视图 / 音效 / 编辑器设置）不写进谱面：全局配置文件负责
        self.assertNotIn("editor", data)
        self.assertEqual(restored.settings.grid_division, config.DEFAULT_GRID_DIVISION)

    def test_repair_groups_on_load(self) -> None:
        chart = make_chart()
        data = chart.to_dict()
        data["speed_groups"] = []          # 故意丢掉速度组信息
        restored = Chart.from_dict(data)
        self.assertTrue(any(g.is_default for g in restored.speed_groups))
        for note in restored.all_notes():
            self.assertEqual(note.group, config.DEFAULT_GROUP_ID)
        self.assertCountEqual(
            restored.group_by_id(config.DEFAULT_GROUP_ID).note_ids,
            [n.id for n in restored.all_notes()],
        )

    def test_beat_bounds(self) -> None:
        chart = make_chart()
        low, high = chart.beat_bounds()
        self.assertLessEqual(low, 0.0)
        self.assertGreaterEqual(high, 16.0)


class DocumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = Document(make_chart())

    def test_add_undo_redo(self) -> None:
        count = len(self.document.chart.all_notes())
        note = LaneNote("L10", 5, 5, 8.0)
        self.document.add_note(note, "放置")
        self.assertEqual(len(self.document.chart.all_notes()), count + 1)
        self.assertTrue(self.document.dirty)
        self.assertTrue(self.document.undo())
        self.assertEqual(len(self.document.chart.all_notes()), count)
        self.assertTrue(self.document.redo())
        self.assertEqual(len(self.document.chart.all_notes()), count + 1)
        self.assertIsNotNone(self.document.chart.find("L10"))

    def test_undo_restores_selection(self) -> None:
        self.document.select_only(["L1"])
        self.document.add_note(LaneNote("L11", 2, 2, 5.0))
        self.document.select_only(["L11"])
        self.document.undo()
        self.assertEqual(self.document.selection, {"L1"})

    def test_copy_paste_keeps_lanes(self) -> None:
        self.document.select_only(["L2", "M2"])
        self.assertEqual(self.document.copy_selection(), 2)
        created = self.document.paste_clipboard(20.0)
        self.assertEqual(len(created), 2)
        lane_new = next(n for n in created if isinstance(n, LaneNote))
        self.assertEqual((lane_new.lane_start, lane_new.lane_end), (1, 3))
        self.assertAlmostEqual(lane_new.beat_start, 20.0)
        self.assertAlmostEqual(lane_new.beat_end, 22.0)
        mouse_new = next(n for n in created if isinstance(n, MouseSlide))
        self.assertAlmostEqual(mouse_new.beat, 24.0)

    def test_mirror(self) -> None:
        self.document.mirror_notes(["L1", "M2"])
        lane = self.document.chart.find("L1")
        self.assertEqual((lane.lane_start, lane.lane_end), (5, 5))
        slide = self.document.chart.find("M2")
        self.assertAlmostEqual(slide.x_start, 0.7)
        self.assertAlmostEqual(slide.x_end, 0.3)

    def test_replace_note_keeps_id_and_group(self) -> None:
        self.document.set_selection_group(self.document.chart.add_group("g").id)
        self.document.select_only(["L1"])
        group_id = self.document.chart.find("L1").group
        data = self.document.chart.find("L1").to_dict()
        data["lane_start"] = 3
        data["lane_end"] = 4
        data.pop("group")
        replaced = self.document.replace_note("L1", data)
        self.assertEqual(replaced.id, "L1")
        self.assertEqual((replaced.lane_start, replaced.lane_end), (3, 4))
        self.assertEqual(replaced.group, group_id)
        self.assertIn("L1", self.document.chart.group_by_id(group_id).note_ids)

    def test_delete_and_undo(self) -> None:
        self.document.delete_notes(["L1", "M1"])
        self.assertIsNone(self.document.chart.find("L1"))
        self.document.undo()
        self.assertIsNotNone(self.document.chart.find("L1"))
        self.assertIsNotNone(self.document.chart.find("M1"))

    def test_title_marks_dirty(self) -> None:
        self.document.path = os.path.join("tmp", "song.json")
        self.document.dirty = False
        self.assertNotIn("*", self.document.title)
        self.document.dirty = True
        self.assertIn("*", self.document.title)


if __name__ == "__main__":
    unittest.main()

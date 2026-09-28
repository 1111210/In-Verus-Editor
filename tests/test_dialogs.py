"""对话框级测试：BPM 列表的第 0 拍项不可删除、速度组默认组不可删除、物件编辑结果。

需要可用的图形环境；没有显示时自动跳过。模态提示框（messagebox）会被替换成记录器，
避免测试阻塞。
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tkinter as tk

from rhythm_editor.model.chart import Chart
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from rhythm_editor.model.timing import BpmSegment, TimingMap
from rhythm_editor.ui import dialogs as dialogs_module
from rhythm_editor.ui.dialogs import BpmDialog, NoteEditDialog, SpeedGroupDialog
from rhythm_editor.ui.theme import DARK


class DialogTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"没有可用的图形环境：{exc}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except tk.TclError:  # pragma: no cover
            pass

    def make_dialog(self, cls, *args):
        dialog = cls(self.root, DARK, *args)
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        return dialog


class BpmDialogTests(DialogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 180.0, 3)], 50.0)

    def test_first_segment_cannot_be_deleted(self) -> None:
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.tree.selection_set("0")
        with mock.patch.object(dialogs_module.messagebox, "showwarning") as warning:
            dialog._delete()
            self.assertTrue(warning.called)
        self.assertEqual(len(dialog.segments), 2)
        self.assertEqual(dialog.segments[0].start_beat, 0.0)

    def test_first_segment_can_be_modified(self) -> None:
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.tree.selection_set("0")
        dialog.e_start.delete(0, "end")
        dialog.e_start.insert(0, "99")   # 起始拍会被强制回到 0
        dialog.e_bpm.delete(0, "end")
        dialog.e_bpm.insert(0, "150")
        dialog.e_bar.delete(0, "end")
        dialog.e_bar.insert(0, "6")
        dialog._update()
        self.assertEqual(dialog.segments[0].start_beat, 0.0)
        self.assertAlmostEqual(dialog.segments[0].bpm, 150.0)
        self.assertEqual(dialog.segments[0].beats_per_bar, 6)

    def test_add_and_delete_other_segments(self) -> None:
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.e_start.delete(0, "end")
        dialog.e_start.insert(0, "16")
        dialog.e_bpm.delete(0, "end")
        dialog.e_bpm.insert(0, "90")
        dialog.e_bar.delete(0, "end")
        dialog.e_bar.insert(0, "4")
        dialog._add()
        self.assertEqual(len(dialog.segments), 3)
        dialog.tree.selection_set("2")
        dialog._delete()
        self.assertEqual(len(dialog.segments), 2)

    def test_result_timing(self) -> None:
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.e_offset.delete(0, "end")
        dialog.e_offset.insert(0, "75")
        dialog._ok()
        timing = dialog.result
        self.assertAlmostEqual(timing.offset_ms, 75.0)
        self.assertEqual(len(timing.segments), 2)
        self.assertAlmostEqual(timing.seconds_per_beat(0.0), 0.5)


class SpeedGroupDialogTests(DialogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.document = Document(Chart())
        self.document.chart.add(LaneNote("L1", 0, 0, 0.0))

    def test_default_group_cannot_be_deleted(self) -> None:
        dialog = self.make_dialog(SpeedGroupDialog, self.document)
        dialog.tree.selection_set("0")
        with mock.patch.object(dialogs_module.messagebox, "showwarning") as warning:
            dialog._delete_group()
            self.assertTrue(warning.called)
        self.assertEqual(len(self.document.chart.speed_groups), 1)

    def test_create_group_and_assign_selection(self) -> None:
        dialog = self.make_dialog(SpeedGroupDialog, self.document)
        dialog._new_group()
        self.assertEqual(len(self.document.chart.speed_groups), 2)
        new_id = self.document.chart.speed_groups[1].id
        self.document.select_only(["L1"])
        dialog.tree.selection_set(str(new_id))
        dialog._assign_selection()
        self.assertEqual(self.document.chart.find("L1").group, new_id)
        self.assertIn("L1", self.document.chart.group_by_id(new_id).note_ids)
        # 删除该组后物件回到默认组
        with mock.patch.object(dialogs_module.messagebox, "askyesno", return_value=True):
            dialog._delete_group()
        self.assertEqual(self.document.chart.find("L1").group, 0)
        self.assertTrue(dialog.changed)

    def test_edit_speed_segments(self) -> None:
        dialog = self.make_dialog(SpeedGroupDialog, self.document)
        dialog.e_start.delete(0, "end")
        dialog.e_start.insert(0, "12")
        dialog.e_mult.delete(0, "end")
        dialog.e_mult.insert(0, "-2")
        dialog._upsert_segment()
        group = dialog._current_group()
        self.assertEqual(len(group.segments), 2)
        self.assertAlmostEqual(group.multiplier_at(13.0), -2.0)
        dialog.seg_tree.selection_set("1")
        dialog._delete_segment()
        self.assertEqual(len(group.segments), 1)


class NoteEditDialogTests(DialogTestCase):
    def test_lane_note_edit_swaps_lane_order(self) -> None:
        note = LaneNote("L5", 1, 2, 4.0, 6.0)
        dialog = self.make_dialog(NoteEditDialog, note)
        dialog.e_lane_start.set(5)
        dialog.e_lane_end.set(2)
        dialog.e_beat_start.delete(0, "end")
        dialog.e_beat_start.insert(0, "10")
        dialog.e_beat_end.delete(0, "end")
        dialog.e_beat_end.insert(0, "12")
        dialog._ok()
        data = dialog.result
        self.assertEqual((data["lane_start"], data["lane_end"]), (1, 4))
        self.assertAlmostEqual(data["beat_start"], 10.0)
        self.assertAlmostEqual(data["beat_end"], 12.0)

    def test_region_edit_keeps_boundaries(self) -> None:
        note = MouseRegion("M1", 0.0, 4.0, BoundaryCurve(0.0, 0.2), BoundaryCurve(1.0, 0.8))
        dialog = self.make_dialog(NoteEditDialog, note)
        dialog.e_left_start.delete(0, "end")
        dialog.e_left_start.insert(0, "-0.3")
        dialog.c_left_ease.set("缓入")
        dialog._ok()
        data = dialog.result
        self.assertAlmostEqual(data["left"]["start"], -0.3)
        self.assertEqual(data["left"]["ease"], "ease_in")
        self.assertAlmostEqual(data["right"]["end"], 0.8)

    def test_slide_edit_enforces_minimum_span(self) -> None:
        note = MouseSlide("M2", 8.0, 0.5, 0.25)
        dialog = self.make_dialog(NoteEditDialog, note)
        dialog.e_x_end.delete(0, "end")
        dialog.e_x_end.insert(0, "0.5")
        dialog._ok()
        data = dialog.result
        self.assertNotAlmostEqual(data["x_end"], data["x_start"])
        self.assertLess(data["x_start"], data["x_end"])


if __name__ == "__main__":
    unittest.main()

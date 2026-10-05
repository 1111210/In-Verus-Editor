"""对话框关键行为：第 0 拍 / 默认组不可删、物件编辑、只有一个确认按钮。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tkinter as tk
from tkinter import ttk

from model.chart import Chart, EditorSettings
from model.document import Document
from model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from model.timing import BpmSegment, TimingMap
from ui import dialogs as dialogs_module
from ui.dialogs import (
    BpmDialog,
    DemoViewDialog,
    HelpDialog,
    NoteEditDialog,
    SaveChangesDialog,
    SettingsDialog,
    SpeedGroupDialog,
    TextInputDialog,
    VolumeDialog,
)
from ui.theme import DARK


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

    def make_dialog(self, cls, *args, **kwargs):
        dialog = cls(self.root, DARK, *args, **kwargs)
        self.addCleanup(lambda: dialog.winfo_exists() and dialog.destroy())
        return dialog


class BpmDialogTests(DialogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.timing = TimingMap([BpmSegment(0.0, 120.0, 4), BpmSegment(8.0, 180.0, 3)], 50.0)

    def test_first_segment_is_protected_but_editable(self) -> None:
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.tree.selection_set("0")
        with mock.patch.object(dialogs_module.messagebox, "showwarning") as warning:
            dialog._delete()
            self.assertTrue(warning.called)
        self.assertEqual(len(dialog.segments), 2)
        dialog.e_bpm.delete(0, "end")
        dialog.e_bpm.insert(0, "150")
        dialog.e_bar.delete(0, "end")
        dialog.e_bar.insert(0, "6")
        dialog._update()
        self.assertEqual(dialog.segments[0].start_beat, 0.0)
        self.assertAlmostEqual(dialog.segments[0].bpm, 150.0)
        self.assertEqual(dialog.segments[0].beats_per_bar, 6)

    def test_add_delete_offset_and_change_beat(self) -> None:
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
        dialog.e_offset.delete(0, "end")
        dialog.e_offset.insert(0, "75")
        dialog._ok()
        self.assertAlmostEqual(dialog.result.offset_ms, 75.0)
        self.assertAlmostEqual(dialog.result.seconds_per_beat(0.0), 0.5)

    def test_zero_beats_per_bar_and_unaligned_change_are_accepted(self) -> None:
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.tree.selection_set("1")
        dialog.e_start.delete(0, "end")
        dialog.e_start.insert(0, "10")      # 不必落在上一段的小节线上
        dialog.e_bar.delete(0, "end")
        dialog.e_bar.insert(0, "0")         # 0 = 关闭段内小节线
        dialog._update()
        self.assertIn(10.0, [seg.start_beat for seg in dialog.segments])
        dialog._ok()
        self.assertEqual(int(dialog.result.segments[1].beats_per_bar), 0)


class SpeedGroupDialogTests(DialogTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.document = Document(Chart())
        self.document.chart.add(LaneNote("L1", 0, 0, 0.0))

    def test_default_group_protected_and_assignment_roundtrip(self) -> None:
        dialog = self.make_dialog(SpeedGroupDialog, self.document)
        dialog.tree.selection_set("0")
        with mock.patch.object(dialogs_module.messagebox, "showwarning") as warning:
            dialog._delete_group()
            self.assertTrue(warning.called)
        dialog._new_group()
        new_id = self.document.chart.speed_groups[1].id
        self.document.select_only(["L1"])
        dialog.tree.selection_set(str(new_id))
        dialog._assign_selection()
        self.assertEqual(self.document.chart.find("L1").group, new_id)
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
    def test_lane_region_and_slide_edits(self) -> None:
        lane = self.make_dialog(NoteEditDialog, LaneNote("L5", 1, 2, 4.0, 6.0))
        lane.e_lane_start.set(5)
        lane.e_lane_end.set(2)
        lane.e_beat_start.delete(0, "end")
        lane.e_beat_start.insert(0, "10")
        lane._ok()
        self.assertEqual((lane.result["lane_start"], lane.result["lane_end"]), (1, 4))
        self.assertAlmostEqual(lane.result["beat_start"], 10.0)

        region = self.make_dialog(
            NoteEditDialog, MouseRegion("M1", 0.0, 4.0, BoundaryCurve(0.0, 0.2), BoundaryCurve(1.0, 0.8))
        )
        region.e_left_start.delete(0, "end")
        region.e_left_start.insert(0, "-0.3")
        region.c_left_ease.set("缓入")
        region._ok()
        self.assertAlmostEqual(region.result["left"]["start"], -0.3)
        self.assertEqual(region.result["left"]["ease"], "ease_in")

        slide = self.make_dialog(NoteEditDialog, MouseSlide("M2", 8.0, 0.5, 0.25))
        slide.e_x_end.delete(0, "end")
        slide.e_x_end.insert(0, "0.5")
        slide._ok()
        self.assertNotAlmostEqual(slide.result["x_end"], slide.result["x_start"])
        self.assertLess(slide.result["x_start"], slide.result["x_end"])


class DialogButtonTests(DialogTestCase):
    @staticmethod
    def _button_texts(widget) -> list:
        texts: list = []
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button):
                texts.append(str(child.cget("text")))
            texts.extend(DialogButtonTests._button_texts(child))
        return texts

    def test_dialogs_have_only_the_confirm_button(self) -> None:
        chart = Chart()
        dialogs = (
            self.make_dialog(BpmDialog, TimingMap([BpmSegment(0.0, 120.0, 4)])),
            self.make_dialog(VolumeDialog, chart),
            self.make_dialog(SettingsDialog, chart),
            self.make_dialog(DemoViewDialog, EditorSettings()),
            self.make_dialog(HelpDialog, "操作指南", "内容"),
        )
        for dialog in dialogs:
            texts = self._button_texts(dialog)
            self.assertNotIn("取消", texts, type(dialog).__name__)
            self.assertEqual(sum(1 for text in texts if text == dialog.ok_text), 1, type(dialog).__name__)

    def test_demo_view_dialog_returns_settings(self) -> None:
        settings = EditorSettings(demo_note_thickness=0.5, demo_speed=2.0, demo_side_lane_highlight=True)
        dialog = self.make_dialog(DemoViewDialog, settings)
        dialog._vars["demo_note_thickness"].set(0.3)
        dialog._vars["demo_slide_thickness"].set(0.1)
        dialog._vars["demo_speed"].set(3.0)
        dialog.var_side.set(False)
        dialog._ok()
        self.assertAlmostEqual(dialog.result["demo_note_thickness"], 0.3)
        self.assertAlmostEqual(dialog.result["demo_speed"], 3.0)
        self.assertFalse(dialog.result["demo_side_lane_highlight"])

    def test_text_input_and_save_changes(self) -> None:
        dialog = self.make_dialog(TextInputDialog, "标题", "提示", "8", kind="int", minimum=1)
        dialog.entry.delete(0, "end")
        dialog.entry.insert(0, "abc")
        dialog._ok()
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.winfo_exists())
        dialog.entry.delete(0, "end")
        dialog.entry.insert(0, "12")
        dialog._ok()
        self.assertEqual(dialog.result, 12)
        save = self.make_dialog(SaveChangesDialog)
        texts = self._button_texts(save)
        self.assertNotIn("取消", texts)
        self.assertIn("保存", texts)
        save._discard()
        self.assertIs(save.result, False)
        escape = self.make_dialog(SaveChangesDialog)
        escape._cancel()
        self.assertIsNone(escape.result)


if __name__ == "__main__":
    unittest.main()

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
from tkinter import ttk

from rhythm_editor.model.chart import Chart
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from rhythm_editor.model.timing import BpmSegment, TimingMap
from rhythm_editor.ui import dialogs as dialogs_module
from rhythm_editor.ui.dialogs import (
    BpmDialog,
    DemoViewDialog,
    NoteEditDialog,
    SpeedGroupDialog,
)
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

    def make_dialog(self, cls, *args, **kwargs):
        dialog = cls(self.root, DARK, *args, **kwargs)
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

    def test_change_beat_is_kept_as_typed(self) -> None:
        """BPM 变更拍允许落在上一段的两条小节线之间：照填不误（变更处自己开新小节）。"""
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.tree.selection_set("1")
        dialog.e_start.delete(0, "end")
        dialog.e_start.insert(0, "10")      # 8 之后每 3 拍一条：10 不在线上，也允许
        dialog.e_bpm.delete(0, "end")
        dialog.e_bpm.insert(0, "150")
        dialog.e_bar.delete(0, "end")
        dialog.e_bar.insert(0, "4")
        dialog._update()
        self.assertIn(10.0, [seg.start_beat for seg in dialog.segments])
        self.assertAlmostEqual(dialog.segments[1].bpm, 150.0)
        self.assertEqual(dialog.segments[1].beats_per_bar, 4)

    def test_zero_beats_per_bar_is_accepted(self) -> None:
        """每小节 0 拍 = 关闭小节线，允许保存。"""
        dialog = self.make_dialog(BpmDialog, self.timing)
        dialog.tree.selection_set("1")
        dialog.e_start.delete(0, "end")
        dialog.e_start.insert(0, "8")
        dialog.e_bpm.delete(0, "end")
        dialog.e_bpm.insert(0, "180")
        dialog.e_bar.delete(0, "end")
        dialog.e_bar.insert(0, "0")
        dialog._update()
        self.assertEqual(dialog.segments[1].beats_per_bar, 0)
        dialog._ok()
        self.assertEqual(int(dialog.result.segments[1].beats_per_bar), 0)


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


class DialogButtonTests(DialogTestCase):
    """弹出窗口只有确认按钮（不再单列「取消」，取消走 Esc / 关闭窗口）。"""

    @staticmethod
    def _button_texts(widget) -> list:
        texts: list = []
        for child in widget.winfo_children():
            if isinstance(child, ttk.Button):
                texts.append(str(child.cget("text")))
            texts.extend(DialogButtonTests._button_texts(child))
        return texts

    def test_dialogs_have_only_the_confirm_button(self) -> None:
        from rhythm_editor.model.chart import Chart, EditorSettings
        from rhythm_editor.ui.dialogs import HelpDialog, SettingsDialog, VolumeDialog

        chart = Chart()
        timing = TimingMap([BpmSegment(0.0, 120.0, 4)])
        dialogs = (
            self.make_dialog(BpmDialog, timing),
            self.make_dialog(VolumeDialog, chart),
            self.make_dialog(SettingsDialog, chart),
            self.make_dialog(DemoViewDialog, EditorSettings()),
            self.make_dialog(HelpDialog, "操作指南", "内容"),
        )
        for dialog in dialogs:
            texts = self._button_texts(dialog)
            self.assertNotIn("取消", texts, f"{type(dialog).__name__} 不应有取消按钮")
            self.assertIn(dialog.ok_text, texts, f"{type(dialog).__name__} 缺少确认按钮")
            self.assertEqual(
                sum(1 for text in texts if text == dialog.ok_text),
                1,
                f"{type(dialog).__name__} 应当只有一个确认按钮",
            )

    def test_demo_view_dialog_returns_render_settings(self) -> None:
        from rhythm_editor.model.chart import EditorSettings

        settings = EditorSettings(
            demo_note_thickness=0.5,
            demo_speed=2.0,
            demo_side_lane_highlight=True,
        )
        dialog = self.make_dialog(DemoViewDialog, settings)
        dialog._vars["demo_note_thickness"].set(0.3)
        dialog._vars["demo_slide_thickness"].set(0.1)
        dialog._vars["demo_speed"].set(3.0)
        dialog.var_side.set(False)
        dialog._ok()
        self.assertAlmostEqual(dialog.result["demo_note_thickness"], 0.3)
        self.assertAlmostEqual(dialog.result["demo_slide_thickness"], 0.1)
        self.assertAlmostEqual(dialog.result["demo_speed"], 3.0)
        self.assertFalse(dialog.result["demo_side_lane_highlight"])

    def test_text_input_dialog_parses_int_and_stays_open_on_bad_input(self) -> None:
        from rhythm_editor.ui.dialogs import TextInputDialog

        dialog = self.make_dialog(
            TextInputDialog, "标题", "提示", "8", kind="int", minimum=1
        )
        texts = self._button_texts(dialog)
        self.assertIn("确定", texts)
        self.assertNotIn("取消", texts)
        dialog.entry.delete(0, "end")
        dialog.entry.insert(0, "abc")
        dialog._ok()
        self.assertIsNone(dialog.result, "非法输入不应关闭窗口")
        self.assertTrue(dialog.winfo_exists())
        dialog.entry.delete(0, "end")
        dialog.entry.insert(0, "12")
        dialog._ok()
        self.assertEqual(dialog.result, 12)

    def test_save_changes_dialog_has_save_and_discard_only(self) -> None:
        """未保存修改的询问框：保存 / 不保存，没有「取消」；Esc 仍可放弃操作。"""
        from rhythm_editor.ui.dialogs import SaveChangesDialog

        dialog = self.make_dialog(SaveChangesDialog)
        texts = self._button_texts(dialog)
        self.assertNotIn("取消", texts)
        self.assertIn("保存", texts)
        self.assertIn("不保存", texts)
        dialog._discard()
        self.assertIs(dialog.result, False)

        escape_dialog = self.make_dialog(SaveChangesDialog)
        escape_dialog._cancel()
        self.assertIsNone(escape_dialog.result, "Esc / 关窗 = 放弃这次操作")


if __name__ == "__main__":
    unittest.main()

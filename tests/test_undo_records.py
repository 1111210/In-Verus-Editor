"""差量记录式撤销：只记录被改动的数据，撤销/重做结果与整谱快照等价。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import config
from rhythm_editor.model.chart import Chart, EditorSettings, Metadata
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion, MouseSlide
from rhythm_editor.model.timing import BpmSegment, TimingMap


def make_document(count: int = 200) -> Document:
    chart = Chart()
    for index in range(count):
        beat = index * 0.5
        chart.add(LaneNote(f"L{index}", index % config.LANE_COUNT, index % config.LANE_COUNT, beat))
    chart.add(MouseRegion("M1", 4.0, 8.0, BoundaryCurve(0.1, 0.2), BoundaryCurve(0.9, 0.8)))
    chart.add(MouseSlide("M2", 10.0, 0.2, 0.7))
    chart.add(MouseSlide("M3", 12.0, 0.8, 0.3))
    return Document(chart)


class UndoRecordTests(unittest.TestCase):
    def assert_roundtrip(self, document: Document, action) -> None:
        before = document.chart.to_dict()
        action()
        after = document.chart.to_dict()
        self.assertNotEqual(before, after, "编辑应该真的改变了谱面")
        self.assertTrue(document.undo())
        self.assertEqual(document.chart.to_dict(), before, "撤销后应与编辑前完全一致")
        self.assertTrue(document.redo())
        self.assertEqual(document.chart.to_dict(), after, "重做后应与编辑后完全一致")

    def test_add_note_roundtrip(self) -> None:
        document = make_document()
        note = LaneNote("NEW", 2, 4, 30.0, 32.0)
        self.assert_roundtrip(document, lambda: document.add_note(note))

    def test_delete_roundtrip(self) -> None:
        document = make_document()
        self.assert_roundtrip(document, lambda: document.delete_notes(["L3", "M1"]))

    def test_paste_roundtrip(self) -> None:
        document = make_document()
        document.select_only(["L5", "M2"])
        document.copy_selection()

        def action() -> None:
            document.paste_clipboard(40.0)

        self.assert_roundtrip(document, action)

    def test_mirror_roundtrip(self) -> None:
        document = make_document()

        def action() -> None:
            document.mirror_notes(["L1", "L2", "M2"])

        self.assert_roundtrip(document, action)

    def test_replace_note_roundtrip(self) -> None:
        document = make_document()
        data = document.chart.find("M1").to_dict()
        data["left"] = {"start": -0.3, "end": 0.4, "ease": config.EASE_IN}
        self.assert_roundtrip(document, lambda: document.replace_note("M1", data))

    def test_group_change_roundtrip(self) -> None:
        document = make_document()
        document.select_only(["L4", "L5"])
        group = document.chart.add_group("测试组")   # id = 1
        self.assert_roundtrip(document, lambda: document.set_selection_group(group.id))

    def test_light_state_roundtrip(self) -> None:
        document = make_document()

        def change_metadata() -> None:
            document.apply("改元数据", lambda: setattr(document.chart.metadata, "title", "改了"))

        self.assert_roundtrip(document, change_metadata)

        def change_timing() -> None:
            document.apply(
                "改 BPM",
                lambda: setattr(
                    document.chart, "timing", TimingMap([BpmSegment(0.0, 90.0, 3)], 33.0)
                ),
            )

        self.assert_roundtrip(document, change_timing)

        def change_settings() -> None:
            def _apply() -> None:
                document.chart.settings.grid_division = 7
                document.chart.settings.audio_latency_ms = -45.0

            document.apply("改设置", _apply)

        self.assert_roundtrip(document, change_settings)

    def test_records_only_touch_changed_notes(self) -> None:
        """1000 物件里改一个物件：记录规模只与改动量有关（不是整谱）。"""
        document = make_document(1000)
        note = LaneNote("NEW", 1, 1, 5.0)
        document.add_note(note)
        self.assertEqual(document.undo_records_size(), 1, "单条记录只应保存 1 个物件")
        document.undo()
        self.assertEqual(document.chart.find("NEW"), None)
        # 连续 10 次单物件编辑：累计记录量仍是 10 左右，与谱面规模无关
        for index in range(10):
            document.add_note(LaneNote(f"X{index}", 0, 0, 100.0 + index))
        self.assertEqual(document.undo_records_size(), 10)

    def test_undo_stack_cap(self) -> None:
        document = make_document(10)
        document.max_undo = 5
        for index in range(12):
            document.add_note(LaneNote(f"C{index}", 0, 0, 200.0 + index))
        self.assertEqual(document.undo_depth, 5)
        for _ in range(5):
            self.assertTrue(document.undo())
        self.assertFalse(document.undo())

    def test_undo_restores_selection(self) -> None:
        document = make_document(10)
        document.select_only(["L1"])
        document.add_note(LaneNote("NEW", 0, 0, 3.0))
        document.select_only(["NEW"])
        document.undo()
        self.assertEqual(document.selection, {"L1"})
        document.redo()
        self.assertEqual(document.selection, {"NEW"})


if __name__ == "__main__":
    unittest.main()

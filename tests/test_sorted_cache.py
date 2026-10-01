"""物件排序缓存：只在增删 / 编辑后重建，不再每帧排序。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.model.chart import Chart
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import LaneNote, MouseSlide


class SortedCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chart = Chart()
        for index, beat in enumerate((8.0, 2.0, 4.0)):
            self.chart.add(LaneNote(f"L{index + 1}", index % 6, index % 6, beat))
        self.document = Document(self.chart)

    def test_returns_cached_list(self) -> None:
        first = self.chart.sorted_notes()
        second = self.chart.sorted_notes()
        self.assertIs(first, second, "同一份缓存应被复用，而不是每帧重新排序")
        self.assertEqual([n.head_beat for n in first], [2.0, 4.0, 8.0])

    def test_add_invalidates(self) -> None:
        before = self.chart.sorted_notes()
        self.chart.add(LaneNote("L9", 1, 1, 1.0))
        after = self.chart.sorted_notes()
        self.assertIsNot(before, after)
        self.assertEqual([n.head_beat for n in after], [1.0, 2.0, 4.0, 8.0])

    def test_remove_invalidates(self) -> None:
        before = self.chart.sorted_notes()
        self.chart.remove(["L1"])
        after = self.chart.sorted_notes()
        self.assertIsNot(before, after)
        self.assertEqual([n.id for n in after], ["L2", "L3"])

    def test_document_edit_invalidates(self) -> None:
        before = self.chart.sorted_notes()
        self.document.add_note(MouseSlide("M1", 0.5, 0.2, 0.6))
        after = self.chart.sorted_notes()
        self.assertIsNot(before, after)
        self.assertEqual(after[0].id, "M1")

    def test_inplace_mutation_with_explicit_invalidate(self) -> None:
        note = self.chart.find("L1")
        cached = self.chart.sorted_notes()
        note.beat_start = 0.25          # 就地改时间
        note.beat_end = 0.25
        self.chart.invalidate_order()   # 外部直接改动时需要显式失效（Document.apply 会自动做）
        self.assertIsNot(cached, self.chart.sorted_notes())
        self.assertEqual(self.chart.sorted_notes()[0].id, "L1")

    def test_lane_and_mouse_sorted_lists(self) -> None:
        self.chart.add(MouseSlide("M2", 3.0, 0.1, 0.4))
        lanes = self.chart.lane_notes_sorted()
        self.assertTrue(all(isinstance(n, LaneNote) for n in lanes))
        mouse = self.chart.mouse_notes_sorted()
        self.assertEqual([n.id for n in mouse], ["M2"])

if __name__ == "__main__":
    unittest.main()

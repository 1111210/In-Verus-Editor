from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor.demo import build_demo_chart, selftest, write_demo_chart
from rhythm_editor.io.chart_io import load_chart


class DemoTests(unittest.TestCase):
    def test_selftest_passes(self) -> None:
        ok, failures = selftest(verbose=False)
        self.assertTrue(ok, f"自检失败：{failures}")

    def test_demo_chart_has_all_note_kinds(self) -> None:
        chart = build_demo_chart()
        counts = chart.note_counts()
        self.assertGreater(counts["lane"], 10)
        self.assertGreaterEqual(counts["region"], 3)
        self.assertGreaterEqual(counts["slide"], 4)
        self.assertTrue(any(n.is_hold for n in chart.lane_notes))
        self.assertTrue(any(n.is_multi for n in chart.lane_notes))
        self.assertGreaterEqual(len(chart.speed_groups), 3)

    def test_write_demo_chart(self) -> None:
        path = write_demo_chart()
        self.assertTrue(os.path.isfile(path))

    def test_committed_demo_chart_loads(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "charts", "demo", "demo.ivchart")
        if not os.path.isfile(path):
            self.skipTest("仓库内没有附带示例谱面")
        chart, _raw = load_chart(path)
        counts = chart.note_counts()
        self.assertGreater(counts["lane"], 10)
        self.assertGreaterEqual(counts["region"], 3)
        self.assertGreaterEqual(counts["slide"], 3)
        self.assertTrue(any(n.is_hold for n in chart.lane_notes))
        self.assertTrue(any(n.is_multi for n in chart.lane_notes))
        self.assertGreaterEqual(len(chart.speed_groups), 3)
        # 物件与速度组双向一致
        for group in chart.speed_groups:
            for note_id in group.note_ids:
                note = chart.find(note_id)
                self.assertIsNotNone(note)
                self.assertEqual(note.group, group.id)
        assigned = {nid for g in chart.speed_groups for nid in g.note_ids}
        self.assertEqual(assigned, {n.id for n in chart.all_notes()})
        self.assertEqual(len(chart.timing.segments), 3)

    def test_demo_chart_passes_auto_check(self) -> None:
        """仓库自带的示例谱面必须通过「自动检查」。"""
        from rhythm_editor.model.validate import validate_chart

        chart = build_demo_chart()
        issues = validate_chart(chart)
        self.assertEqual(issues, [], [issue.message for issue in issues])


if __name__ == "__main__":
    unittest.main()

"""示例谱面：构造、自检、自动检查与仓库内文件的完整性。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from demo import build_demo_chart, selftest, write_demo_chart
from chart_io import load_chart
from model.validate import validate_chart


class DemoTests(unittest.TestCase):
    def test_selftest_passes(self) -> None:
        ok, failures = selftest(verbose=False)
        self.assertTrue(ok, f"自检失败：{failures}")

    def test_demo_chart_covers_all_note_kinds_and_passes_check(self) -> None:
        chart = build_demo_chart()
        counts = chart.note_counts()
        self.assertGreater(counts["lane"], 10)
        self.assertGreaterEqual(counts["region"], 3)
        self.assertGreaterEqual(counts["slide"], 4)
        self.assertTrue(any(n.is_hold for n in chart.lane_notes))
        self.assertTrue(any(n.is_multi for n in chart.lane_notes))
        self.assertGreaterEqual(len(chart.speed_groups), 3)
        self.assertEqual(validate_chart(chart), [])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(os.path.isfile(write_demo_chart(os.path.join(tmp, "demo.json"))))

    def test_committed_demo_chart_loads_consistently(self) -> None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "charts", "demo", "demo.json")
        if not os.path.isfile(path):
            self.skipTest("仓库内没有附带示例谱面")
        chart, _raw = load_chart(path)
        self.assertGreater(chart.note_counts()["lane"], 10)
        self.assertGreaterEqual(len(chart.speed_groups), 3)
        self.assertEqual(len(chart.timing.segments), 3)
        assigned = {nid for g in chart.speed_groups for nid in g.note_ids}
        self.assertEqual(assigned, {n.id for n in chart.all_notes()})
        for group in chart.speed_groups:
            for note_id in group.note_ids:
                self.assertEqual(chart.find(note_id).group, group.id)


if __name__ == "__main__":
    unittest.main()

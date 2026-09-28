"""命中测试复用渲染缓存的测试（大谱面点击/框选不再全量重算几何）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import tkinter as tk

from rhythm_editor import config
from rhythm_editor.model.chart import Chart
from rhythm_editor.model.document import Document
from rhythm_editor.model.notes import BoundaryCurve, LaneNote, MouseRegion
from rhythm_editor.ui import geometry
from rhythm_editor.ui.geometry import HitCache, hit_signature, notes_at, notes_in_box
from rhythm_editor.ui.renderer import RenderState, Renderer
from rhythm_editor.ui.scene import Scene
from rhythm_editor.ui.theme import DARK
from rhythm_editor.ui.view import ViewState

WIDTH, HEIGHT = 1280, 800


class HitCacheTests(unittest.TestCase):
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

    def setUp(self) -> None:
        self.canvas = tk.Canvas(self.root, width=WIDTH, height=HEIGHT, highlightthickness=0)
        self.chart = Chart()
        # 1000 个远处物件 + 少量屏内物件
        for index in range(1000):
            beat = 200.0 + index * 0.5
            self.chart.add(LaneNote(f"L{index}", index % config.LANE_COUNT, index % config.LANE_COUNT, beat))
        self.near = LaneNote("NEAR", 2, 2, 2.0)
        self.chart.add(self.near)
        self.chart.add(
            MouseRegion("M1", 1.0, 3.0, BoundaryCurve(0.1, 0.1), BoundaryCurve(0.9, 0.9))
        )
        self.document = Document(self.chart)
        self.view = ViewState(self.chart)
        self.view.resize(WIDTH, HEIGHT)
        self.view.set_beat(0.0)
        self.scene = Scene(self.canvas)
        self.renderer = Renderer(self.scene, DARK)
        self.state = RenderState()

    def tearDown(self) -> None:
        self.canvas.destroy()

    def draw(self) -> None:
        self.renderer.draw(self.chart, self.view, self.state)

    def test_cache_contains_only_visible_notes(self) -> None:
        self.draw()
        cache = self.state.hit_cache
        self.assertIsNotNone(cache)
        self.assertLess(len(cache.entries), 10, "缓存里只应该有几屏内的物件")
        self.assertIn(self.near, [entry[0] for entry in cache.entries])

    def test_notes_at_uses_cache_without_touching_all_notes(self) -> None:
        self.draw()
        calls = {"count": 0}
        original = geometry.note_hit

        def counting(note, chart, view, x, y):
            calls["count"] += 1
            return original(note, chart, view, x, y)

        geometry.note_hit = counting  # type: ignore[assignment]
        try:
            bounds = geometry.lane_note_bounds(self.near, self.chart, self.view)
            hits = notes_at(
                self.chart,
                self.view,
                (bounds[0] + bounds[2]) / 2,
                (bounds[1] + bounds[3]) / 2,
                cache=self.state.hit_cache,
            )
        finally:
            geometry.note_hit = original  # type: ignore[assignment]
        self.assertTrue(any(note.id == "NEAR" for note in hits))
        self.assertEqual(calls["count"], 0, "走缓存时不应再对整张谱面做几何计算")

    def test_stale_cache_falls_back_to_full_scan(self) -> None:
        self.draw()
        # 视图一变，缓存签名失效 -> 回到全量扫描（结果依然正确）
        self.view.set_beat(100.0)
        cache = self.state.hit_cache
        self.assertFalse(cache.valid_for(self.view))
        far = self.chart.lane_notes[0]
        bounds = geometry.lane_note_bounds(far, self.chart, self.view)
        hits = notes_at(
            self.chart,
            self.view,
            (bounds[0] + bounds[2]) / 2,
            (bounds[1] + bounds[3]) / 2,
            cache=cache,
        )
        self.assertTrue(any(note.id == far.id for note in hits))

    def test_notes_in_box_uses_cache(self) -> None:
        self.draw()
        layout = self.view.layout
        found = notes_in_box(
            self.chart,
            self.view,
            0,
            0,
            layout.width,
            layout.height,
            cache=self.state.hit_cache,
        )
        self.assertEqual(len(found), len(self.state.hit_cache.entries))
        self.assertIn(self.near, found)

    def test_signature_changes_with_view_and_chart(self) -> None:
        before = hit_signature(self.view)
        self.view.scroll_beats(1.0)
        self.assertNotEqual(before, hit_signature(self.view))
        after_view = hit_signature(self.view)
        self.chart.add(LaneNote("NEW", 0, 0, 3.0))
        self.assertNotEqual(after_view, hit_signature(self.view))

    def test_empty_cache_is_not_valid(self) -> None:
        empty = HitCache()
        self.assertFalse(empty.valid_for(self.view))


if __name__ == "__main__":
    unittest.main()

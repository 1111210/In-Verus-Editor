from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from rhythm_editor import config
from rhythm_editor.model.notes import (
    BoundaryCurve,
    LaneNote,
    MouseRegion,
    MouseSlide,
    ease_value,
    note_from_dict,
)


class LaneNoteTests(unittest.TestCase):
    def test_single_and_multi(self) -> None:
        single = LaneNote("L1", 2, 2, 4.0)
        self.assertFalse(single.is_multi)
        self.assertFalse(single.is_hold)
        multi = LaneNote("L2", 4, 1, 8.0, 8.0)
        self.assertTrue(multi.is_multi)
        self.assertEqual((multi.lane_start, multi.lane_end), (1, 4))

    def test_hold(self) -> None:
        hold = LaneNote("L3", 0, 5, 10.0, 12.0)
        self.assertTrue(hold.is_hold)
        self.assertAlmostEqual(hold.duration_beats, 2.0)
        hold.beat_end = 8.0
        hold.normalize()
        self.assertEqual((hold.beat_start, hold.beat_end), (8.0, 10.0))

    def test_shift_and_clone(self) -> None:
        note = LaneNote("L4", 1, 3, 4.0, 6.0)
        note.shift(2.0)
        self.assertEqual((note.beat_start, note.beat_end), (6.0, 8.0))
        clone = note.clone("L5", 1.0)
        self.assertEqual(clone.id, "L5")
        self.assertEqual((clone.beat_start, clone.beat_end), (7.0, 9.0))

    def test_mirror(self) -> None:
        note = LaneNote("L6", 1, 4, 0.0)
        note.mirrored()
        self.assertEqual((note.lane_start, note.lane_end), (1, 4))
        note = LaneNote("L7", 0, 2, 0.0)
        note.mirrored()
        self.assertEqual((note.lane_start, note.lane_end), (3, 5))


class BoundaryTests(unittest.TestCase):
    def test_easing(self) -> None:
        self.assertAlmostEqual(ease_value(config.EASE_LINEAR, 0.5), 0.5)
        self.assertAlmostEqual(ease_value(config.EASE_IN, 0.5), 0.25)
        self.assertAlmostEqual(ease_value(config.EASE_OUT, 0.5), 0.75)
        for ease in config.EASE_CHOICES:
            self.assertAlmostEqual(ease_value(ease, 0.0), 0.0)
            self.assertAlmostEqual(ease_value(ease, 1.0), 1.0)

    def test_curve(self) -> None:
        curve = BoundaryCurve(0.2, 0.8, config.EASE_OUT)
        self.assertAlmostEqual(curve.value_at(0.0), 0.2)
        self.assertAlmostEqual(curve.value_at(1.0), 0.8)
        mirrored = curve.mirrored()
        self.assertAlmostEqual(mirrored.start, 0.8)
        self.assertAlmostEqual(mirrored.end, 0.2)


class RegionTests(unittest.TestCase):
    def test_region_geometry(self) -> None:
        region = MouseRegion(
            "M1", 4.0, 8.0, BoundaryCurve(0.0, 0.2, config.EASE_IN), BoundaryCurve(1.0, 0.8, config.EASE_OUT)
        )
        self.assertAlmostEqual(region.left_at(4.0), 0.0)
        self.assertAlmostEqual(region.left_at(8.0), 0.2)
        self.assertAlmostEqual(region.right_at(4.0), 1.0)
        self.assertAlmostEqual(region.right_at(8.0), 0.8)
        self.assertTrue(region.contains(6.0, region.left_at(6.0)))
        self.assertFalse(region.contains(6.0, region.right_at(6.0) + 0.01))
        self.assertFalse(region.contains(9.0, 0.5))

    def test_region_mirror_swaps_borders(self) -> None:
        region = MouseRegion(
            "M2", 0.0, 4.0, BoundaryCurve(0.1, 0.3), BoundaryCurve(0.9, 0.7)
        )
        region.mirrored()
        self.assertAlmostEqual(region.left.start, 0.1)
        self.assertAlmostEqual(region.left.end, 0.3)
        self.assertAlmostEqual(region.right.start, 0.9)
        self.assertAlmostEqual(region.right.end, 0.7)
        self.assertLess(region.left.start, region.right.start)

    def test_x_range_allows_overflow(self) -> None:
        region = MouseRegion("M3", 0.0, 1.0, BoundaryCurve(-0.5, 0.2), BoundaryCurve(1.4, 1.0))
        low, high = region.x_range()
        self.assertAlmostEqual(low, -0.5)
        self.assertAlmostEqual(high, 1.4)


class SlideTests(unittest.TestCase):
    def test_direction(self) -> None:
        self.assertEqual(MouseSlide("M4", 4.0, 0.2, 0.8).direction, 1)
        self.assertEqual(MouseSlide("M5", 4.0, 0.8, 0.2).direction, -1)
        self.assertAlmostEqual(MouseSlide("M6", 4.0, 0.5, 0.9).span, 0.4)

    def test_mirror_flips_direction(self) -> None:
        slide = MouseSlide("M7", 4.0, 0.2, 0.8)
        slide.mirrored()
        self.assertAlmostEqual(slide.x_start, 0.8)
        self.assertAlmostEqual(slide.x_end, 0.2)
        self.assertEqual(slide.direction, -1)


class FactoryTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        notes = [
            LaneNote("L1", 1, 3, 2.0, 4.0),
            MouseRegion("M1", 0.0, 2.0, BoundaryCurve(0.0, 0.5), BoundaryCurve(1.0, 0.75)),
            MouseSlide("M2", 6.0, 0.25, 0.75),
        ]
        for note in notes:
            restored = note_from_dict(note.to_dict())
            self.assertEqual(restored.to_dict(), note.to_dict())


if __name__ == "__main__":
    unittest.main()

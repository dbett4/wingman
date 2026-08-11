#!/usr/bin/env python3
"""Tests for server/vision_candidates.py."""
from __future__ import annotations

import unittest

import vision_candidates as vc


class VisionCandidateTests(unittest.TestCase):
    def test_flagged_first(self):
        cells = [
            {"addr": "A1", "value": "Revenue", "fontColor": "#000000", "backgroundColor": "#FFFFFF"},
            {"addr": "B1", "value": "999", "calculatedValue": "999",
             "fontColor": "#000000", "backgroundColor": "#FFFFFF"},
        ]
        out = vc.build_vision_candidates(cells, {"C3"}, cap=10)
        self.assertEqual(out[0], "C3")
        self.assertIn("A1", out)

    def test_label_probe_includes_passing_contrast_text(self):
        cells = [
            {"addr": "A1", "value": "Section Header",
             "fontColor": "#000000", "backgroundColor": "#FFFFFF"},
            {"addr": "B1", "value": "1,234",
             "calculatedValue": "1,234", "fontColor": "#000000", "backgroundColor": "#FFFFFF"},
        ]
        out = vc.build_vision_candidates(cells, set(), cap=10)
        self.assertIn("A1", out)
        self.assertEqual(out[0], "A1")

    def test_cap_respected(self):
        flagged = {f"Z{i}" for i in range(1, 20)}
        out = vc.build_vision_candidates([], flagged, cap=3)
        self.assertEqual(len(out), 3)

    def test_dedupe_flagged(self):
        out = vc.build_vision_candidates([], {"A1", "A1", "B2"}, cap=10)
        self.assertEqual(out, ["A1", "B2"])

    def test_formula_excluded_from_label_probe(self):
        cells = [
            {"addr": "A1", "value": "Label", "formula": "=B1",
             "fontColor": "#000000", "backgroundColor": "#FFFFFF"},
        ]
        out = vc.build_vision_candidates(cells, set(), cap=10)
        self.assertIn("A1", out)  # tier 3 non-empty fill


if __name__ == "__main__":
    unittest.main()

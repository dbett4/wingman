#!/usr/bin/env python3
"""Unit tests for content API cell type enrichment (mock payloads only)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import type_enrich as te


class TypeFromValueTests(unittest.TestCase):
    def test_richtext(self):
        self.assertEqual(te.type_from_content_value({"richText": {"runs": []}}), "richText")

    def test_formula(self):
        self.assertEqual(te.type_from_content_value({"type": "formula", "formula": "=A1"}), "formula")

    def test_plain_string(self):
        self.assertEqual(te.type_from_content_value("hello"), "plainText")

    def test_missing(self):
        self.assertIsNone(te.type_from_content_value(None))

    def test_skips_existing_type(self):
        cells = [{"addr": "A1", "value": "x", "type": "plainText"}]
        rows = [{"cells": [{"value": {"richText": {"runs": []}}}]}]
        out, n = te.enrich_cells_with_types(
            cells, "tbl", "tok", None, raw_cells=rows,
        )
        self.assertEqual(n, 0)
        self.assertEqual(out[0]["type"], "plainText")

    def test_enrich_from_raw_rows(self):
        cells = [{"addr": "B2", "value": "Title"}]
        rows = [{"cells": [{"value": {"richText": {"runs": [{"text": "Title"}]}}}]}]
        out, n = te.enrich_cells_with_types(
            cells, "tbl", "tok", None, raw_cells=rows,
        )
        self.assertEqual(n, 1)
        self.assertEqual(out[0]["type"], "richText")


class ContrastLaneImpactTests(unittest.TestCase):
    def test_plaintext_type_enables_safe_auto_contrast(self):
        import detectors

        cell = {
            "addr": "C5",
            "type": "plainText",
            "value": "100",
            "fontColor": "#FFFFFF",
            "backgroundColor": "#7F7F7F",
        }
        f = detectors.detect_low_contrast(cell)
        self.assertIsNotNone(f)
        self.assertTrue(f.get("fixable"))
        self.assertEqual(f.get("fix_lane"), "safe-auto")

    def test_untyped_stays_surfaced(self):
        import detectors

        cell = {
            "addr": "C5",
            "type": None,
            "value": "100",
            "fontColor": "#FFFFFF",
            "backgroundColor": "#7F7F7F",
        }
        f = detectors.detect_low_contrast(cell)
        self.assertIsNotNone(f)
        self.assertFalse(f.get("fixable"))
        self.assertEqual(f.get("fix_lane"), "surfaced")


if __name__ == "__main__":
    unittest.main()

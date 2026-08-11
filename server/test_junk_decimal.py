#!/usr/bin/env python3
"""Unit tests for server/junk_decimal.py (no Workiva creds)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import junk_decimal as jd  # noqa: E402


def _cell(addr, *, prec_value=23, calc="1234.567890123", neighbors=None):
    vf = {
        "valueFormatType": "NUMBER",
        "precision": {"auto": False, "value": prec_value},
        "showThousandsSeparator": True,
        "useParensForNegatives": True,
    }
    cells = {addr: {"addr": addr, "calculatedValue": calc, "valueFormat": vf, "type": "plainText"}}
    if neighbors:
        cells.update(neighbors)
    return cells[addr], cells


class JunkDecimalTests(unittest.TestCase):
    def test_is_absurd_precision_true(self):
        self.assertTrue(jd.is_absurd_precision({"precision": {"auto": False, "value": 23}}))
        self.assertTrue(jd.is_absurd_precision({"precision": {"auto": False, "value": 5}}))

    def test_is_absurd_precision_false(self):
        self.assertFalse(jd.is_absurd_precision({"precision": {"auto": True}}))
        self.assertFalse(jd.is_absurd_precision({"precision": {"auto": False, "value": 0}}))
        self.assertFalse(jd.is_absurd_precision({"precision": {"auto": False, "value": 4}}))
        self.assertFalse(jd.is_absurd_precision(None))

    def test_detect_junk_decimal_flags_high_precision(self):
        cell, _ = _cell("B10")
        f = jd.detect_junk_decimal(cell)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "junk-decimal")
        self.assertEqual(f["fix_lane"], "safe-auto")

    def test_detect_skips_text_format(self):
        cell = {
            "addr": "B11",
            "calculatedValue": "1.23",
            "valueFormat": {"valueFormatType": "TEXT", "precision": {"auto": False, "value": 23}},
        }
        self.assertIsNone(jd.detect_junk_decimal(cell))

    def test_neighbor_consensus_two_of_three(self):
        good = {
            "valueFormatType": "NUMBER",
            "precision": {"auto": False, "value": 0},
            "useParensForNegatives": True,
        }
        neighbors = {
            "B9": {"addr": "B9", "valueFormat": dict(good)},
            "B11": {"addr": "B11", "valueFormat": dict(good)},
            "B12": {"addr": "B12", "valueFormat": {**good, "precision": {"auto": False, "value": 23}}},
        }
        cell, cells = _cell("B10", neighbors=neighbors)
        cells["B10"] = cell
        vf = jd.neighbor_consensus_format("B10", cells)
        self.assertIsNotNone(vf)
        self.assertEqual(vf["precision"]["value"], 0)

    def test_neighbor_consensus_refuses_split_column(self):
        a = {"valueFormatType": "NUMBER", "precision": {"auto": False, "value": 0}}
        b = {"valueFormatType": "ACCOUNTING", "precision": {"auto": False, "value": 0}}
        c = {"valueFormatType": "CURRENCY", "precision": {"auto": False, "value": 2}}
        neighbors = {
            "B9": {"addr": "B9", "valueFormat": dict(a)},
            "B11": {"addr": "B11", "valueFormat": dict(b)},
            "B12": {"addr": "B12", "valueFormat": dict(c)},
        }
        cell, cells = _cell("B10", neighbors=neighbors)
        cells["B10"] = cell
        self.assertIsNone(jd.neighbor_consensus_format("B10", cells))

    def test_adjust_downgrades_without_consensus(self):
        cell, cells = _cell("C5")
        cells["C5"] = cell
        raw = [jd.detect_junk_decimal(cell)]
        out = jd.adjust_junk_decimal_lanes(raw, list(cells.values()))
        self.assertEqual(out[0]["fix_lane"], "surfaced")
        self.assertFalse(out[0]["fixable"])

    def test_adjust_sets_target_with_consensus(self):
        good = {
            "valueFormatType": "NUMBER",
            "precision": {"auto": False, "value": 0},
            "useParensForNegatives": True,
        }
        neighbors = {
            "D4": {"addr": "D4", "valueFormat": dict(good)},
            "D6": {"addr": "D6", "valueFormat": dict(good)},
        }
        cell, cells = _cell("D5", neighbors=neighbors)
        cells["D5"] = cell
        raw = [jd.detect_junk_decimal(cell)]
        out = jd.adjust_junk_decimal_lanes(raw, list(cells.values()))
        self.assertEqual(out[0]["fix_lane"], "safe-auto")
        self.assertIn("afterFormat", out[0]["target"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

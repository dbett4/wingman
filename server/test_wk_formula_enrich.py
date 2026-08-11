#!/usr/bin/env python3
"""Unit tests for wk_client formula enrichment (mocked content API)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wk_client as wk  # noqa: E402


class FormulaEnrichTests(unittest.TestCase):
    def test_normalize_sheetdata_sets_formula_from_value(self):
        raw = {
            "data": {
                "range": {"startRow": 0, "startColumn": 0},
                "cells": [[{
                    "value": '=IF(H12="","",TEXT(H12/1000,"#,##0"))',
                    "calculatedValue": "12,340",
                    "effectiveFormats": {},
                }]],
            }
        }
        cells = wk.normalize_sheetdata(raw)
        self.assertEqual(len(cells), 1)
        self.assertTrue(cells[0]["formula"].startswith("=IF"))

    def test_enrich_merges_content_formulas(self):
        cells = [{"addr": "W21", "formula": None, "value": "12,340", "calculatedValue": "12,340"}]
        mock_body = {
            "data": [{
                "cells": [{
                    "value": {
                        "type": "formula",
                        "formula": '=IF(B21="","",TEXT(B21/1000,"#,##0"))',
                    },
                }],
            }],
        }
        with patch.object(wk, "_get", return_value=mock_body):
            out, enriched = wk.enrich_cells_with_formulas(cells, "tbl123", "tok", None)
        self.assertIn("/1000", out[0]["formula"])
        self.assertEqual(enriched, 1)

    def test_enrich_noop_without_table(self):
        cells = [{"addr": "A1", "formula": None, "value": "x"}]
        out, enriched = wk.enrich_cells_with_formulas(cells, None, "tok", None)
        self.assertIsNone(out[0]["formula"])
        self.assertEqual(enriched, 0)

    def test_build_formula_fetch_meta_disabled(self):
        meta = wk.build_formula_fetch_meta(
            enabled=False, cell_count=10, enriched_count=0, table_id_present=True,
        )
        self.assertTrue(meta["partial"])
        self.assertFalse(meta["enabled"])

    def test_build_formula_fetch_meta_capped(self):
        meta = wk.build_formula_fetch_meta(
            enabled=True, cell_count=600, enriched_count=500, table_id_present=True,
        )
        self.assertTrue(meta["partial"])
        self.assertIn("500", meta["reason"])


class FormulaFetchModeTests(unittest.TestCase):
    """U1: WINGMAN_FORMULA_FETCH is tri-state, default-on/scoped (resolved open decision #1)."""

    def _mode_for(self, value):
        env = {} if value is None else {"WINGMAN_FORMULA_FETCH": value}
        with patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop("WINGMAN_FORMULA_FETCH", None)
            return wk.formula_fetch_mode(), wk.formula_fetch_enabled(), wk.formula_fetch_cap()

    def test_default_is_scoped_and_enabled(self):
        mode, enabled, cap = self._mode_for(None)
        self.assertEqual(mode, "scoped")
        self.assertTrue(enabled)
        self.assertEqual(cap, wk.FORMULA_FETCH_SCOPED_CAP)

    def test_explicit_off_disables(self):
        for val in ("off", "0", "false", "no"):
            mode, enabled, cap = self._mode_for(val)
            self.assertEqual(mode, "off", val)
            self.assertFalse(enabled, val)
            # cap falls back to the full cap when not scoped (used by the separate type-fetch lane)
            self.assertEqual(cap, wk.FORMULA_FETCH_CAP, val)

    def test_explicit_on_is_full_cap(self):
        for val in ("on", "1", "true", "full"):
            mode, enabled, cap = self._mode_for(val)
            self.assertEqual(mode, "full", val)
            self.assertTrue(enabled, val)
            self.assertEqual(cap, wk.FORMULA_FETCH_CAP, val)

    def test_unrecognized_value_fails_open_to_scoped(self):
        mode, enabled, _ = self._mode_for("maybe")
        self.assertEqual(mode, "scoped")
        self.assertTrue(enabled)

    def test_meta_reports_scoped_mode_and_cap(self):
        meta = wk.build_formula_fetch_meta(
            enabled=True, cell_count=250, enriched_count=200, table_id_present=True,
            cap=200, mode="scoped",
        )
        self.assertEqual(meta["mode"], "scoped")
        self.assertEqual(meta["cap"], 200)
        self.assertTrue(meta["partial"])
        self.assertIn("200", meta["reason"])
        self.assertIn("visible-range", meta["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

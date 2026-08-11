#!/usr/bin/env python3
"""Unit tests for server/display_wrapper.py (Lane A display-mirror patterns)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import display_wrapper  # noqa: E402
import diagnose  # noqa: E402
import detectors  # noqa: E402

# Representative formulas from formula_display_wrapper_inventory.csv (2026-06-17).
ISNUMBER_SCALED = (
    '=IF(A1="","",IF(ISNUMBER(A1),IF(A1=0,"",TEXT(ROUND(A1/1000,0),"#,##0")&"\u200b"),A1))'
)
TEXT_DIV_W21 = (
    '=IF($A21="","",IF(B21="","-",IF(B21=0,"-",TEXT(B21/1000,"#,##0;(#,##0);-")&"\u200b")))'
)
ISNUMBER_DIRECT = (
    '=IF(B21="","",IF(ISNUMBER(B21),IF(B21<0,"("&TEXT(ABS(B21),"#,##0")&")",TEXT(B21,"#,##0")),B21))'
)
TEXT_DIV_SIMPLE = '=IF(H12="","",TEXT(H12/1000,"#,##0"))'
LITERAL_NUMERIC = '="17,066"'
PLAIN_SUM = "=SUM(A1:A10)"


class ClassifyTests(unittest.TestCase):
    def test_isnumber_scaled_mirror(self):
        hit = display_wrapper.classify_display_wrapper(ISNUMBER_SCALED)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["subkind"], "text-round-scale")

    def test_text_div_w21(self):
        hit = display_wrapper.classify_display_wrapper(TEXT_DIV_W21)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["subkind"], "text-div-scale")

    def test_isnumber_direct_mirror(self):
        hit = display_wrapper.classify_display_wrapper(ISNUMBER_DIRECT)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["subkind"], "isnumber-text-mirror")

    def test_text_div_simple(self):
        hit = display_wrapper.classify_display_wrapper(TEXT_DIV_SIMPLE)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["subkind"], "text-div-scale")

    def test_literal_numeric_string(self):
        hit = display_wrapper.classify_display_wrapper(LITERAL_NUMERIC)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["subkind"], "literal-numeric-string")

    def test_plain_sum_skipped(self):
        self.assertIsNone(display_wrapper.classify_display_wrapper(PLAIN_SUM))

    def test_empty_skipped(self):
        self.assertIsNone(display_wrapper.classify_display_wrapper(None))
        self.assertIsNone(display_wrapper.classify_display_wrapper("Revenue"))


class DetectorTests(unittest.TestCase):
    def _cell(self, addr, formula):
        return {"addr": addr, "formula": formula, "calculatedValue": "12340", "value": ""}

    def test_detector_surfaced_red(self):
        f = display_wrapper.detect_display_wrapper(self._cell("W21", TEXT_DIV_W21))
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "display-wrapper")
        self.assertEqual(f["fix_lane"], "surfaced")
        self.assertFalse(f["fixable"])
        self.assertEqual(f["severity"], "high")

    def test_formula_from_value_field(self):
        f = display_wrapper.detect_display_wrapper({
            "addr": "M1",
            "formula": None,
            "value": ISNUMBER_SCALED,
            "calculatedValue": "1,234",
        })
        self.assertIsNotNone(f)
        self.assertEqual(f["target"]["subkind"], "text-round-scale")

    def test_no_formula_no_finding(self):
        self.assertIsNone(display_wrapper.detect_display_wrapper({
            "addr": "A1", "formula": None, "value": "Revenue", "calculatedValue": "",
        }))

    def test_scan_cells_integration(self):
        cells = [
            self._cell("M1", ISNUMBER_SCALED),
            {"addr": "A1", "type": "plainText", "value": "Label", "calculatedValue": ""},
        ]
        findings = detectors.scan_cells(cells)
        kinds = [x["kind"] for x in findings]
        self.assertIn("display-wrapper", kinds)


# D21 — ACCOUNTING `$` format inert under a string-emitting wrapper.
ACC_DOLLAR = {"valueFormatType": "ACCOUNTING", "prefix": "$"}
ACC_NO_PREFIX = {"valueFormatType": "ACCOUNTING"}
CURRENCY_VF = {"valueFormatType": "CURRENCY"}
NUMBER_VF = {"valueFormatType": "NUMBER"}
# unscaled TEXT wrapper the scaled/ISNUMBER classifier does NOT catch on its own
TEXT_UNSCALED = '=IF(B21<0,"("&TEXT(ABS(B21),"#,##0")&")",TEXT(B21,"#,##0"))'
# IF()-dash wrapper with no TEXT(): returns "-" string for the non-numeric branch
IF_DASH = '=IF(ISNUMBER(B21),B21,"-")'


class AccountingDollarFormatTests(unittest.TestCase):
    def test_accounting_with_dollar_prefix(self):
        self.assertTrue(display_wrapper.accounting_dollar_format(ACC_DOLLAR))

    def test_accounting_without_prefix_is_not_dollar(self):
        self.assertFalse(display_wrapper.accounting_dollar_format(ACC_NO_PREFIX))

    def test_currency_is_dollar(self):
        self.assertTrue(display_wrapper.accounting_dollar_format(CURRENCY_VF))

    def test_number_format_is_not_dollar(self):
        self.assertFalse(display_wrapper.accounting_dollar_format(NUMBER_VF))

    def test_none_and_garbage(self):
        self.assertFalse(display_wrapper.accounting_dollar_format(None))
        self.assertFalse(display_wrapper.accounting_dollar_format("ACCOUNTING"))


class StringEmittingFormulaTests(unittest.TestCase):
    def test_literal_numeric_string(self):
        self.assertTrue(display_wrapper.string_emitting_formula(LITERAL_NUMERIC))

    def test_text_call(self):
        self.assertTrue(display_wrapper.string_emitting_formula(TEXT_UNSCALED))
        self.assertTrue(display_wrapper.string_emitting_formula(ISNUMBER_SCALED))

    def test_if_dash_literal(self):
        self.assertTrue(display_wrapper.string_emitting_formula(IF_DASH))

    def test_numeric_formula_is_not_string(self):
        self.assertFalse(display_wrapper.string_emitting_formula(PLAIN_SUM))
        self.assertFalse(display_wrapper.string_emitting_formula("=SUMIFS(A:A,B:B,C1)"))
        self.assertFalse(display_wrapper.string_emitting_formula("=B21"))

    def test_text_criteria_if_is_not_numeric_display(self):
        # quoted criterion has letters -> not a numeric-display literal -> not string-emitting
        self.assertFalse(display_wrapper.string_emitting_formula('=IF(A1="Revenue",B1,C1)'))

    def test_non_formula(self):
        self.assertFalse(display_wrapper.string_emitting_formula("Revenue"))
        self.assertFalse(display_wrapper.string_emitting_formula(None))


class FormatInertDetectorTests(unittest.TestCase):
    def _cell(self, addr, formula, value_format, calc="123,456,789"):
        return {"addr": addr, "formula": formula, "value": "",
                "calculatedValue": calc, "valueFormat": value_format}

    def test_classifier_hit_enriched_when_accounting_dollar(self):
        f = display_wrapper.detect_display_wrapper(
            self._cell("W21", TEXT_DIV_W21, ACC_DOLLAR))
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "display-wrapper")
        self.assertEqual(f["target"]["subkind"], "text-div-scale")  # routing preserved
        self.assertTrue(f["target"]["accountingDollarInert"])
        self.assertIn("ACCOUNTING_DOLLAR_INERT", f["target"]["patterns"])
        self.assertIn("inert", f["detail"].lower())
        self.assertFalse(f["fixable"])
        self.assertEqual(f["fix_lane"], "surfaced")

    def test_classifier_hit_not_enriched_without_dollar_format(self):
        f = display_wrapper.detect_display_wrapper(
            self._cell("W21", TEXT_DIV_W21, NUMBER_VF))
        self.assertIsNotNone(f)
        self.assertEqual(f["target"]["subkind"], "text-div-scale")
        self.assertNotIn("accountingDollarInert", f["target"])
        self.assertNotIn("ACCOUNTING_DOLLAR_INERT", f["target"]["patterns"])

    def test_unscaled_text_wrapper_emits_format_inert_finding(self):
        # classifier alone returns None for this formula...
        self.assertIsNone(display_wrapper.classify_display_wrapper(TEXT_UNSCALED))
        # ...but with ACCOUNTING $ format it surfaces as format-inert-wrapper
        f = display_wrapper.detect_display_wrapper(
            self._cell("H12", TEXT_UNSCALED, ACC_DOLLAR))
        self.assertIsNotNone(f)
        self.assertEqual(f["target"]["subkind"], "format-inert-wrapper")
        self.assertTrue(f["target"]["accountingDollarInert"])
        self.assertEqual(f["target"]["valueFormatType"], "ACCOUNTING")
        self.assertFalse(f["fixable"])

    def test_if_dash_wrapper_emits_format_inert_finding(self):
        f = display_wrapper.detect_display_wrapper(
            self._cell("H13", IF_DASH, CURRENCY_VF))
        self.assertIsNotNone(f)
        self.assertEqual(f["target"]["subkind"], "format-inert-wrapper")

    def test_no_finding_for_numeric_formula_even_with_dollar_format(self):
        self.assertIsNone(display_wrapper.detect_display_wrapper(
            self._cell("H14", "=SUMIFS(A:A,B:B,C1)", ACC_DOLLAR)))

    def test_no_finding_when_no_value_format(self):
        # unscaled TEXT wrapper with no format context -> classifier miss, no inert signal
        self.assertIsNone(display_wrapper.detect_display_wrapper(
            {"addr": "H15", "formula": TEXT_UNSCALED, "value": "", "calculatedValue": ""}))

    def test_scan_cells_surfaces_format_inert(self):
        cells = [self._cell("H12", TEXT_UNSCALED, ACC_DOLLAR)]
        findings = detectors.scan_cells(cells)
        subkinds = [(x.get("target") or {}).get("subkind") for x in findings
                    if x["kind"] == "display-wrapper"]
        self.assertIn("format-inert-wrapper", subkinds)


class FormatInertDiagnoseTests(unittest.TestCase):
    def test_format_inert_pathway(self):
        p = diagnose.lookup_pathway(
            "display-wrapper",
            "surfaced",
            display_wrapper.SUBKIND_LABELS["format-inert-wrapper"],
            target={"subkind": "format-inert-wrapper"},
        )
        self.assertEqual(p["pathway_id"], "formula.display-wrapper-format-inert")
        self.assertIn("inert", p["explain"])

    def test_enriched_hit_still_routes_by_subkind(self):
        # enriched detail string must still resolve to the original subkind pathway
        item = diagnose.diagnose_group({
            "kind": "display-wrapper",
            "severity": "high",
            "signature": display_wrapper.SUBKIND_LABELS["text-div-scale"]
            + " — ACCOUNTING $ format inert (string formula bypasses it)",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["W21"],
            "count": 1,
            "target": {"subkind": "text-div-scale", "accountingDollarInert": True,
                       "patterns": ["TEXT", "SCALE_IN_TEXT", "ACCOUNTING_DOLLAR_INERT"]},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "formula.display-wrapper-scaled-text")


class DiagnoseTests(unittest.TestCase):
    def test_w21_scaled_pathway_cites_trap(self):
        item = diagnose.diagnose_group({
            "kind": "display-wrapper",
            "severity": "high",
            "signature": display_wrapper.SUBKIND_LABELS["text-div-scale"],
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["W21"],
            "count": 1,
            "target": {"subkind": "text-div-scale", "patterns": ["TEXT", "SCALE_IN_TEXT"]},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "formula.display-wrapper-scaled-text")
        traps = [t["key"] for t in item["diagnosis"]["trap_refs"]]
        self.assertIn("w21-native-valueformat-scale-rewrite", traps)
        self.assertFalse(item["fixable"])
        self.assertNotIn("valueFormat", item["diagnosis"]["suggested_action"].lower()[:20])

    def test_isnumber_scaled_pathway(self):
        p = diagnose.lookup_pathway(
            "display-wrapper",
            "surfaced",
            display_wrapper.SUBKIND_LABELS["isnumber-text-mirror-scaled"],
            target={"subkind": "isnumber-text-mirror-scaled"},
        )
        self.assertEqual(p["pathway_id"], "formula.display-wrapper-isnumber-scaled")
        self.assertTrue(any("W21" in t["note"] or "valueFormat" in t["note"]
                            for t in p["trap_refs"]))

    def test_literal_string_pathway(self):
        p = diagnose.lookup_pathway(
            "display-wrapper",
            "surfaced",
            display_wrapper.SUBKIND_LABELS["literal-numeric-string"],
            target={"subkind": "literal-numeric-string"},
        )
        self.assertEqual(p["pathway_id"], "formula.literal-numeric-string")

    def test_display_wrapper_priority_boost(self):
        item = diagnose.diagnose_group({
            "kind": "display-wrapper",
            "severity": "high",
            "signature": display_wrapper.SUBKIND_LABELS["text-round-scale"],
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["M1"],
            "count": 1,
            "target": {"subkind": "text-round-scale"},
        })
        ref = diagnose.diagnose_group({
            "kind": "label-hygiene",
            "severity": "low",
            "signature": "trailing space",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["A1"],
            "count": 1,
        })
        self.assertGreater(item["priority"], ref["priority"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

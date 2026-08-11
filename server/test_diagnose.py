#!/usr/bin/env python3
"""Unit tests for server/diagnose.py (stdlib only — run: python3 server/test_diagnose.py)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diagnose  # noqa: E402


class DiagnoseTests(unittest.TestCase):
    def test_plain_contrast_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "low-contrast",
            "severity": "medium",
            "signature": "#FFFFFF on #7F7F7F",
            "fixable": True,
            "fix_lane": "safe-auto",
            "addrs": ["A1"],
            "count": 1,
            "target": {"hex": "#333333", "ratio": 4.6},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "format.contrast-plain-auto")
        self.assertTrue(item["fixable"])
        self.assertGreater(item["priority"], 2000)

    def test_richtext_contrast_surfaced(self):
        item = diagnose.diagnose_group({
            "kind": "low-contrast",
            "severity": "high",
            "signature": "#FFFFFF on #00A19B",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["B2"],
            "count": 1,
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "format.contrast-richtext-ui")
        self.assertFalse(item["fixable"])

    def test_blank_linked_red(self):
        item = diagnose.diagnose_group({
            "kind": "blank-linked-cell",
            "severity": "high",
            "signature": "cell carries a link but renders blank",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["D12"],
            "count": 1,
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "link.blank-dl-review")
        self.assertTrue(item["diagnosis"]["trap_refs"])
        self.assertFalse(item["fixable"])
        steps = item["diagnosis"].get("guided_steps")
        self.assertIsNotNone(steps)
        self.assertEqual(len(steps), 4)
        self.assertEqual(steps[0]["id"], "verify-source")
        self.assertEqual(steps[2]["id"], "publish-document")
        self.assertIn("ownLinks", steps[1]["detail"])
        self.assertIn("allLinks", steps[2]["detail"])
        self.assertIn("empty cached", steps[3]["detail"].lower())

    def test_blank_dl_guided_steps_helper(self):
        steps = diagnose.blank_dl_guided_steps()
        self.assertEqual(len(steps), 4)
        ids = [s["id"] for s in steps]
        self.assertEqual(ids, ["verify-source", "publish-spreadsheet", "publish-document", "re-walk-cache"])
        self.assertTrue(all(s.get("title") and s.get("detail") for s in steps))

    def test_broken_ref_subkinds(self):
        ref = diagnose.lookup_pathway("broken-ref", "surfaced", "formula result is #REF!")
        name = diagnose.lookup_pathway("broken-ref", "surfaced", "formula result is #NAME?")
        self.assertEqual(ref["pathway_id"], "formula.broken-ref")
        self.assertEqual(name["pathway_id"], "formula.unsupported-name")

    def test_negative_parens_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "negative-without-parens",
            "severity": "medium",
            "signature": "minus-prefix on negative (NUMBER, minus-prefix)",
            "fixable": True,
            "fix_lane": "safe-auto",
            "addrs": ["C5"],
            "count": 1,
            "target": {"valueFormat": {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True}},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "format.negative-accounting-auto")
        self.assertTrue(item["fixable"])
        mixed = diagnose.diagnose_group({
            "kind": "negative-without-parens",
            "severity": "medium",
            "signature": "minus-prefix on negative (mixed column)",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["C6"],
            "count": 1,
        })
        self.assertEqual(mixed["diagnosis"]["pathway_id"], "format.negative-accounting-mixed")

    def test_junk_decimal_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "junk-decimal",
            "severity": "medium",
            "signature": "junk precision (23 decimals, NUMBER, parens)",
            "fixable": True,
            "fix_lane": "safe-auto",
            "addrs": ["B10"],
            "count": 1,
            "target": {"valueFormat": {"precision": {"auto": False, "value": 0}}},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "format.junk-decimal-auto")
        self.assertTrue(item["fixable"])

    def test_prefix_mismatch_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "prefix-mismatch",
            "severity": "medium",
            "signature": "missing prefix (ACCOUNTING, parens)",
            "fixable": True,
            "fix_lane": "safe-auto",
            "addrs": ["J10"],
            "count": 1,
            "target": {"valueFormat": {"prefix": "$", "valueFormatType": "ACCOUNTING"}},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "format.prefix-mismatch-auto")
        manual = diagnose.lookup_pathway("prefix-mismatch", "surfaced", "missing prefix")
        self.assertEqual(manual["pathway_id"], "format.prefix-mismatch-manual")

    def test_zero_display_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "zero-display-mismatch",
            "severity": "low",
            "signature": "zero shows 0 not em-dash (ACCOUNTING, parens)",
            "fixable": True,
            "fix_lane": "safe-auto",
            "addrs": ["K10"],
            "count": 1,
            "target": {"valueFormat": {"displayZeroAs": "EM DASH", "valueFormatType": "ACCOUNTING"}},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "format.zero-display-auto")

    def test_unbounded_sumifs_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "unbounded-full-column-sumifs",
            "severity": "medium",
            "signature": "unbounded full-column range in SUMIFS-family call — #VALUE! on xlsx "
                         "reimport/roll-forward; bind ranges to rows ($1:$N)",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["K17", "K18", "K19"],
            "count": 3,
            "target": None,
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "formula.unbounded-full-column-sumifs")
        self.assertFalse(item["fixable"])
        self.assertEqual(item["fix_lane"], "surfaced")
        self.assertIn("#VALUE!", item["diagnosis"]["explain"])
        self.assertIn("$1:$N", item["diagnosis"]["judgment"] + item["diagnosis"]["suggested_action"])

    def test_dead_function_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "dead-unsupported-function",
            "severity": "high",
            "signature": "dead/unsupported function(s) in formula: FALSE(), INDIRECT() — "
                         "#NAME? on xlsx import/roll-forward",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["M1", "M2", "M3"],
            "count": 3,
            "target": {"functions": ["FALSE()", "INDIRECT()"]},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "formula.dead-unsupported-function")
        self.assertEqual(item["severity"], "high")
        self.assertFalse(item["fixable"])
        self.assertEqual(item["fix_lane"], "surfaced")
        self.assertIn("#NAME?", item["diagnosis"]["explain"])
        # high severity must out-prioritize a medium surfaced scan item
        med = diagnose.diagnose_group({
            "kind": "unbounded-full-column-sumifs", "severity": "medium",
            "signature": "x", "fixable": False, "fix_lane": "surfaced",
            "addrs": ["K1"], "count": 1, "target": None,
        })
        self.assertGreater(item["priority"], med["priority"])

    def test_degenerate_placeholder_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "degenerate-placeholder-formula",
            "severity": "medium",
            "signature": "degenerate placeholder formula (=0 or constant-only arithmetic) — "
                         "replace with governed pull/formula or documented input",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["P1", "P2"],
            "count": 2,
            "target": {"value": 0.0},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "formula.degenerate-placeholder")
        self.assertFalse(item["fixable"])
        self.assertEqual(item["fix_lane"], "surfaced")
        self.assertIn("governed", item["diagnosis"]["explain"])
        self.assertTrue(item["diagnosis"]["trap_refs"])

    def test_gated_target_passthrough(self):
        gt = {
            "valueFormat": {"valueFormatType": "ACCOUNTING", "showThousandsSeparator": True},
            "afterFormat": "ACCOUNTING, thousands",
            "column": "F",
        }
        item = diagnose.diagnose_group({
            "kind": "missing-thousands-separator",
            "severity": "medium",
            "signature": "no thousands separator",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["F10"],
            "count": 1,
            "gated_target": gt,
        })
        self.assertEqual(item["gated_target"], gt)
        self.assertIn("Apply column format", item["diagnosis"]["suggested_action"])

    def test_formula_evaluates_blank_pathway(self):
        item = diagnose.diagnose_group({
            "kind": "formula-evaluates-blank",
            "severity": "high",
            "signature": "formula evaluates blank (=SUM(A1:A9))",
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["E3"],
            "count": 1,
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "formula.evaluates-blank")
        self.assertFalse(item["fixable"])

    def test_clipped_text_pathway(self):
        p = diagnose.lookup_pathway("clipped-text", "surfaced", "text clipped by row height")
        self.assertEqual(p["pathway_id"], "format.clipped-text-review")
        self.assertIn("vision", p["explain"].lower())

    def test_queue_priority_order(self):
        q = diagnose.build_sheet_queue([
            {
                "kind": "label-hygiene",
                "severity": "low",
                "signature": "trailing space",
                "fixable": False,
                "fix_lane": "surfaced",
                "addrs": ["A1"],
                "count": 1,
            },
            {
                "kind": "broken-ref",
                "severity": "high",
                "signature": "formula result is #REF!",
                "fixable": False,
                "fix_lane": "surfaced",
                "addrs": ["E1"],
                "count": 1,
            },
            {
                "kind": "low-contrast",
                "severity": "medium",
                "signature": "#FFFFFF on #7F7F7F",
                "fixable": True,
                "fix_lane": "safe-auto",
                "addrs": ["B3"],
                "count": 1,
                "target": {"hex": "#000000"},
            },
        ], spreadsheet_id="ss", sheet_id="sh", sheet_name="TB")
        kinds = [i["kind"] for i in q["items"]]
        self.assertEqual(kinds[0], "broken-ref")
        self.assertEqual(q["summary"]["fixable"], 1)
        self.assertEqual(q["summary"]["review"], 2)

    def test_workbook_scan_error_item(self):
        q = diagnose.build_workbook_queue({
            "spreadsheetId": "ss",
            "sheetCount": 1,
            "scanned": 1,
            "findingTotal": 0,
            "sheets": [{
                "sheetId": "x",
                "name": "Locked",
                "groups": [],
                "findingCount": 0,
                "error": "HTTP Error 403: Forbidden",
            }],
        })
        self.assertEqual(len(q["items"]), 1)
        self.assertEqual(q["items"][0]["kind"], "scan-error")

    def test_check_tieout_pathway(self):
        item = diagnose.diagnose_check_fail({
            "check": "tieout",
            "detail": "Gov_Funds CY assets Δ=+100.00",
            "source": "tieout_table",
            "label": "Total assets",
            "calc": "1,000",
            "pub": "1,100",
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "export-proof.guided")
        steps = item["diagnosis"].get("guided_steps")
        self.assertIsNotNone(steps)
        self.assertEqual(len(steps), 5)
        self.assertEqual(steps[0]["id"], "publish-spreadsheet")
        self.assertEqual(item["diagnosis"]["guided_lane"], "export-proof")
        ep = item["diagnosis"].get("export_proof")
        self.assertIsNotNone(ep)
        self.assertEqual(ep["label"], "Total assets")
        self.assertIn("1,000", steps[3]["detail"])

    def test_check_formula_consistency_pathway(self):
        p = diagnose.lookup_check_pathway("formula_consistency")
        self.assertEqual(p["pathway_id"], "formula.formula-consistency")

    def test_display_wrapper_w21_pathway(self):
        import display_wrapper
        item = diagnose.diagnose_group({
            "kind": "display-wrapper",
            "severity": "high",
            "signature": display_wrapper.SUBKIND_LABELS["text-div-scale"],
            "fixable": False,
            "fix_lane": "surfaced",
            "addrs": ["W21"],
            "count": 1,
            "target": {"subkind": "text-div-scale"},
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "formula.display-wrapper-scaled-text")
        self.assertFalse(item["fixable"])
        self.assertTrue(item["diagnosis"]["trap_refs"])
        self.assertEqual(item["diagnosis"]["guided_lane"], "export-proof")
        self.assertEqual(len(item["diagnosis"]["guided_steps"]), 5)
        self.assertIn("TEXT", item["diagnosis"]["title"])

    def test_hardening_gate_criteria_bank_pathway(self):
        p = diagnose.lookup_check_pathway("hardening_gate_criteria_bank")
        self.assertEqual(p["pathway_id"], "hardening-gate.criteria-bank")

    def test_merge_queue_scorecard_timestamp(self):
        queue = diagnose.build_sheet_queue([], spreadsheet_id="ss", sheet_id="sh1", sheet_name="TB")
        meta = {
            "requested": True,
            "suite": "tieout",
            "applied": True,
            "fail_count": 1,
            "scorecard_generated_at": "2026-06-16T10:00:00",
            "fail_rows": [{
                "check": "tieout",
                "detail": "Gov_Funds CY assets Δ=+100.00",
                "source": "tieout_table",
            }],
        }
        merged = diagnose.merge_queue_with_checks(queue, meta)
        self.assertEqual(merged["checks"]["scorecard_generated_at"], "2026-06-16T10:00:00")
        tieout = next(i for i in merged["items"] if i["kind"] == "check-tieout")
        self.assertEqual(tieout["scorecard_generated_at"], "2026-06-16T10:00:00")
        self.assertEqual(tieout["diagnosis"]["scorecard_generated_at"], "2026-06-16T10:00:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)

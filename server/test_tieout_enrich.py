#!/usr/bin/env python3
"""Unit tests for server/tieout_enrich.py."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tieout_enrich  # noqa: E402
import diagnose  # noqa: E402


SAMPLE_SCORECARD = {
    "results": [
        {
            "statement": "Gov-Wide - Net Position",
            "line_item": "Restricted - Endowments, Expendable",
            "column": "Total",
            "wb_label": "Expendable",
            "wb_row": 106,
            "wb_col": 12,
            "wb_value_thousands": 54.0,
            "pdf_value_thousands": 27.0,
            "variance_thousands": 27.0,
            "status": "FAIL",
        },
        {
            "statement": "Gov Funds - Balance Sheet",
            "line_item": "Unearned Rev - Other",
            "column": "General",
            "wb_label": "Unearned revenue",
            "wb_row": 42,
            "wb_col": 8,
            "wb_value_thousands": 0.0,
            "pdf_value_thousands": 2051.0,
            "variance_thousands": -2051.0,
            "status": "ZERO",
        },
    ],
    "critic": [
        {
            "statement": "Gov-Wide - Net Position",
            "line_item": "Restricted - Endowments, Expendable",
            "column": "Total",
            "status": "FAIL",
            "cause": "unknown",
            "confidence": "low",
            "notes": "wb=54K, pdf=27K — no heuristic matched.",
        },
        {
            "statement": "Gov Funds - Balance Sheet",
            "line_item": "Unearned Rev - Other",
            "column": "General",
            "status": "ZERO",
            "cause": "tb_source_gap",
            "confidence": "medium",
            "notes": "wb≈0 but PDF=2051K.",
        },
    ],
}


class TieoutEnrichTests(unittest.TestCase):
    def test_col_index_to_letter(self):
        self.assertEqual(tieout_enrich.col_index_to_letter(8), "H")
        self.assertEqual(tieout_enrich.col_index_to_letter(12), "L")
        self.assertEqual(tieout_enrich.cell_addr(106, 12), "L106")

    def test_detect_double_aggregation(self):
        h = tieout_enrich.detect_heuristic(SAMPLE_SCORECARD["results"][0])
        self.assertEqual(h["cause"], tieout_enrich.CAUSE_DOUBLE_AGG)
        self.assertEqual(h["confidence"], "high")

    def test_detect_zero_acctmap_gap(self):
        h = tieout_enrich.detect_heuristic(SAMPLE_SCORECARD["results"][1])
        self.assertEqual(h["cause"], tieout_enrich.CAUSE_TB_SOURCE_GAP)

    def test_match_tieout_table_row(self):
        idx = tieout_enrich.build_scorecard_index(SAMPLE_SCORECARD)
        critic = tieout_enrich.build_critic_index(SAMPLE_SCORECARD)
        row = {
            "check": "tieout",
            "stmt": "Gov-Wide - Net Position",
            "column": "Total",
            "label": "Restricted - Endowments, Expendable",
            "source": "tieout_table",
        }
        enriched = tieout_enrich.enrich_fail_row(row, index=idx, critic_index=critic)
        self.assertEqual(enriched["wb_row"], 106)
        self.assertEqual(enriched["wb_col"], 12)
        self.assertEqual(enriched["jumpHint"]["addr"], "L106")
        self.assertEqual(enriched["jumpHint"]["sheetName"], "Gov-Wide - Net Position")

    def test_scorecard_fail_rows_includes_zero(self):
        rows = tieout_enrich.scorecard_fail_rows(SAMPLE_SCORECARD)
        self.assertEqual(len(rows), 2)
        statuses = {r["tieout_status"] for r in rows}
        self.assertEqual(statuses, {"FAIL", "ZERO"})

    def test_merge_dedupes(self):
        existing = [{
            "check": "tieout",
            "stmt": "Gov-Wide - Net Position",
            "column": "Total",
            "label": "Restricted - Endowments, Expendable",
            "source": "tieout_table",
        }]
        merged = tieout_enrich.merge_scorecard_fail_rows(existing, SAMPLE_SCORECARD)
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0].get("jumpHint"))

    def test_enrich_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "validation", "tieout_scorecard.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = dict(SAMPLE_SCORECARD, generated_at="2026-06-16T10:00:00")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            rows, meta = tieout_enrich.enrich_fail_rows(
                [], working_dir=td, project="acfr",
            )
            self.assertTrue(meta["applied"])
            self.assertEqual(meta["scorecard_generated_at"], "2026-06-16T10:00:00")
            self.assertEqual(len(rows), 2)

    def test_scorecard_generated_at_helper(self):
        self.assertEqual(
            tieout_enrich.scorecard_generated_at({"generated_at": "2026-06-17T05:35:22"}),
            "2026-06-17T05:35:22",
        )
        self.assertIsNone(tieout_enrich.scorecard_generated_at({}))

    def test_acfr_diagnosis_pathway(self):
        idx = tieout_enrich.build_scorecard_index(SAMPLE_SCORECARD)
        critic = tieout_enrich.build_critic_index(SAMPLE_SCORECARD)
        row = tieout_enrich.enrich_fail_row({
            "check": "tieout",
            "stmt": "Gov Funds - Balance Sheet",
            "column": "General",
            "label": "Unearned Rev - Other",
            "source": "tieout_scorecard",
        }, index=idx, critic_index=critic)
        item = diagnose.diagnose_check_fail(row, spreadsheet_id="ss1")
        self.assertEqual(item["diagnosis"]["pathway_id"], "tieout.acfr-acctmap-gap")
        self.assertEqual(item["addrs"], ["H42"])
        self.assertIn("AcctMap", item["diagnosis"]["suggested_action"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

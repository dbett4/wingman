#!/usr/bin/env python3
"""Unit tests for server/hardening_gate_bridge.py (mocked subprocess — no live hardening)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import diagnose  # noqa: E402
import hardening_gate_bridge  # noqa: E402

ACFR_PRESET_SS = "a1b2c3d4e5f60718293a4b5c6d7e8f90"

SAMPLE_EVAL = {
    "parity": [
        {"sheet": "Combining Statement", "cell": "I11", "expected": 73000, "actual": 72000, "pass": False},
        {"sheet": "Combining Statement", "cell": "I9", "expected": 9425000, "actual": 9425000, "pass": True},
    ],
    "patch_refs": {
        "Combining Statement!ZA200:ZB207": 3,
        "Combining Statement!QA6:QW36": 0,
    },
    "criteria_bank_nonempty_cells": 2,
    "parity_pass": False,
}


class HardeningGateBridgeTests(unittest.TestCase):
    def test_evaluation_to_fail_rows(self):
        rows = hardening_gate_bridge.evaluation_to_fail_rows(SAMPLE_EVAL)
        checks = {r["check"] for r in rows}
        self.assertIn("hardening_gate_parity", checks)
        self.assertIn("hardening_gate_patch_refs", checks)
        self.assertIn("hardening_gate_criteria_bank", checks)
        parity = [r for r in rows if r["check"] == "hardening_gate_parity"]
        self.assertEqual(len(parity), 1)
        self.assertIn("I11", parity[0]["detail"])

    def test_evaluation_missing_snapshot(self):
        rows = hardening_gate_bridge.evaluation_to_fail_rows({"error": "missing snapshot"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check"], "hardening_gate_missing_snapshot")

    def test_parse_dry_run_operations(self):
        out = "DRY RUN: 42 operations planned\n  Combining - IS RevExp A11: A11 sign\n"
        self.assertEqual(hardening_gate_bridge.parse_dry_run_operations(out), 42)

    def test_is_acfr_preset_spreadsheet(self):
        self.assertTrue(hardening_gate_bridge.is_acfr_preset_spreadsheet(ACFR_PRESET_SS))
        self.assertFalse(hardening_gate_bridge.is_acfr_preset_spreadsheet("abc123"))

    def test_run_hardening_gate_skipped_non_preset(self):
        meta = hardening_gate_bridge.run_hardening_gate_suite("not-preset-ss")
        self.assertFalse(meta["applied"])
        self.assertIn("ACFR-preset only", meta["skipped"])

    def test_run_hardening_gate_skipped_no_script(self):
        with patch.object(hardening_gate_bridge, "resolve_hardening_script", return_value=None):
            meta = hardening_gate_bridge.run_hardening_gate_suite(ACFR_PRESET_SS)
        self.assertFalse(meta["applied"])
        self.assertIn("not found", meta["skipped"])

    def test_run_hardening_gate_mocked_success(self):
        eval_json = json.dumps(SAMPLE_EVAL)

        def fake_runner(cmd, *, cwd, timeout, env=None):
            class Proc:
                pass

            p = Proc()
            if len(cmd) > 1 and cmd[1] == "-c":
                p.returncode = 0
                p.stdout = eval_json + "\n"
                p.stderr = ""
            else:
                p.returncode = 0
                p.stdout = "DRY RUN: 17 operations planned\n"
                p.stderr = ""
            return p

        with patch.object(hardening_gate_bridge, "resolve_hardening_script", return_value=__import__("pathlib").Path(__file__)):
            meta = hardening_gate_bridge.run_hardening_gate_suite(ACFR_PRESET_SS, runner=fake_runner)

        self.assertTrue(meta["applied"])
        self.assertEqual(meta["planned_operations"], 17)
        self.assertEqual(meta["fail_count"], 3)
        self.assertGreaterEqual(len(meta["fail_rows"]), 3)

    def test_diagnose_hardening_gate_parity_pathway(self):
        item = diagnose.diagnose_check_fail({
            "check": "hardening_gate_parity",
            "detail": "Combining - IS RevExp I11: expected 73000, actual 72000",
            "source": "hardening_gate_eval",
            "sheet": "Combining - IS RevExp",
        })
        self.assertEqual(item["kind"], "check-hardening-gate-parity")
        self.assertEqual(item["diagnosis"]["pathway_id"], "hardening-gate.parity-fail")
        self.assertEqual(item["source"], "hardening_gate_eval")

    def test_diagnose_hardening_gate_patch_refs_pathway(self):
        item = diagnose.diagnose_check_fail({
            "check": "hardening_gate_patch_refs",
            "detail": "Combining - IS RevExp!BA200:BB207: 3 formula ref(s) into retired patch zone",
            "source": "hardening_gate_eval",
        })
        self.assertEqual(item["diagnosis"]["pathway_id"], "hardening-gate.patch-refs")

    def test_merge_queue_with_hardening_gate(self):
        queue = diagnose.build_sheet_queue([], spreadsheet_id=ACFR_PRESET_SS, sheet_id="sh1", sheet_name="TB")
        meta = {
            "requested": True,
            "suite": "hardening_gate",
            "applied": True,
            "fail_count": 1,
            "planned_operations": 17,
            "fail_rows": [{
                "check": "hardening_gate_criteria_bank",
                "detail": "BA200:BB207 criteria bank has 2 non-empty cell(s)",
                "source": "hardening_gate_eval",
            }],
        }
        merged = diagnose.merge_queue_with_checks(queue, meta)
        self.assertEqual(len(merged["items"]), 1)
        self.assertEqual(merged["items"][0]["kind"], "check-hardening-gate-criteria-bank")
        self.assertEqual(merged["checks"]["planned_operations"], 17)


if __name__ == "__main__":
    unittest.main(verbosity=2)

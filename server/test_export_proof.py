#!/usr/bin/env python3
"""Unit tests for server/export_proof.py (stdlib only)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_proof  # noqa: E402


class ExportProofTests(unittest.TestCase):
    def test_guided_steps_count_and_ids(self):
        steps = export_proof.export_proof_guided_steps()
        self.assertEqual(len(steps), 5)
        ids = [s["id"] for s in steps]
        self.assertEqual(
            ids,
            [
                "publish-spreadsheet",
                "publish-document",
                "export-pdf",
                "grep-pdf",
                "mark-verdict",
            ],
        )
        self.assertTrue(all(s.get("title") and s.get("detail") for s in steps))

    def test_guided_steps_target_hints(self):
        steps = export_proof.export_proof_guided_steps(
            target_label="Unrestricted net position",
            target_value="331,744",
        )
        grep = steps[3]["detail"]
        self.assertIn("Unrestricted net position", grep)
        self.assertIn("331,744", grep)
        self.assertIn("own links", steps[0]["detail"].lower())
        self.assertIn("all links", steps[1]["detail"].lower())
        self.assertIn("binary-safe", steps[2]["detail"].lower())

    def test_targets_from_tieout_row(self):
        t = export_proof.export_proof_targets_from_row({
            "label": "Total assets",
            "calc": "1,234,567",
            "pub": "1,234,000",
        })
        self.assertEqual(t["label"], "Total assets")
        self.assertEqual(t["calc"], "1,234,567")
        self.assertEqual(t["value"], "1,234,567")

    def test_attach_export_proof_to_diagnosis(self):
        dx: dict = {"pathway_id": "tieout.line-fail", "title": "old"}
        row = {
            "check": "tieout",
            "label": "Net position",
            "calc": "331,744",
            "pub": "331,771",
            "source": "tieout_table",
        }
        export_proof.attach_export_proof_to_diagnosis(dx, row)
        self.assertEqual(dx["guided_lane"], export_proof.EXPORT_PROOF_LANE)
        self.assertEqual(dx["pathway_id"], "export-proof.guided")
        self.assertEqual(len(dx["guided_steps"]), 5)
        self.assertIn("331,744", dx["guided_steps"][3]["detail"])
        ep = dx["export_proof"]
        self.assertEqual(ep["label"], "Net position")
        self.assertEqual(ep["calc"], "331,744")
        self.assertIsNone(ep["verdict"])
        self.assertIn(export_proof.EXPORT_PROVEN, ep["valid_verdicts"])

    def test_normalize_verdict(self):
        self.assertEqual(export_proof.normalize_verdict("export_proven"), export_proof.EXPORT_PROVEN)
        self.assertEqual(export_proof.normalize_verdict("NOT_PROVEN_VISUAL"), export_proof.NOT_PROVEN_VISUAL)
        self.assertEqual(export_proof.normalize_verdict("proven"), export_proof.EXPORT_PROVEN)
        self.assertIsNone(export_proof.normalize_verdict("maybe"))

    def test_not_proven_visual_footer(self):
        self.assertIn("grep pass", export_proof.NOT_PROVEN_VISUAL_FOOTER)
        self.assertIn("raster", export_proof.NOT_PROVEN_VISUAL_FOOTER)


if __name__ == "__main__":
    unittest.main(verbosity=2)

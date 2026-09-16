#!/usr/bin/env python3
"""Unit tests for server/format_lane.py (no Workiva creds)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import format_lane as fl  # noqa: E402


def _accounting_vf(**overrides):
    vf = {
        "valueFormatType": "ACCOUNTING",
        "precision": {"auto": False, "value": 0},
        "showThousandsSeparator": True,
        "useParensForNegatives": True,
    }
    vf.update(overrides)
    return vf


class FormatLaneTests(unittest.TestCase):
    @staticmethod
    def _accounting_column_with_target(target):
        neighbor_vf = _accounting_vf(prefix="$")
        return [
            target,
            {"addr": "B1", "calculatedValue": "1,000", "valueFormat": dict(neighbor_vf)},
            {"addr": "B3", "calculatedValue": "3,000", "valueFormat": dict(neighbor_vf)},
        ]

    def test_automatic_year_is_owned_by_year_detector_despite_neighbor_consensus(self):
        cell = {
            "addr": "B2",
            "calculatedValue": "2025",
            "valueFormat": {"valueFormatType": "AUTOMATIC"},
        }
        cells = self._accounting_column_with_target(cell)

        self.assertEqual(fl.scan_format_lane(cells), [])
        self.assertIsNotNone(fl.detectors.detect_year_automatic_coercion(cell))

    def test_period_year_ignores_conflicting_neighbor_consensus(self):
        cell = {
            "addr": "B2",
            "calculatedValue": "2024",
            "valueFormat": {"valueFormatType": "PERIOD"},
        }
        cells = self._accounting_column_with_target(cell)

        self.assertEqual(fl.scan_format_lane(cells), [])
        self.assertIsNone(fl.detectors.detect_year_automatic_coercion(cell))

    def test_explicit_number_year_shaped_amount_remains_eligible(self):
        cell = {
            "addr": "B2",
            "calculatedValue": "2025",
            "valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False},
        }
        findings = fl.scan_format_lane(self._accounting_column_with_target(cell))

        self.assertEqual(
            {f["kind"] for f in findings},
            {"missing-thousands-separator", "number-on-accounting-column", "prefix-mismatch"},
        )
        self.assertTrue(all(f["fix_lane"] == "safe-auto" for f in findings))

    def test_out_of_range_automatic_amount_remains_eligible(self):
        cell = {
            "addr": "B2",
            "calculatedValue": "1800",
            "valueFormat": {"valueFormatType": "AUTOMATIC", "showThousandsSeparator": False},
        }
        findings = fl.scan_format_lane(self._accounting_column_with_target(cell))

        self.assertEqual(
            {f["kind"] for f in findings},
            {"missing-thousands-separator", "number-on-accounting-column", "prefix-mismatch"},
        )
        self.assertTrue(all(f["fix_lane"] == "safe-auto" for f in findings))

    def test_missing_thousands_flags_large_number(self):
        cell = {
            "addr": "E10",
            "type": "plainText",
            "calculatedValue": "1234567",
            "valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False,
                            "precision": {"auto": False, "value": 0}},
        }
        f = fl.detect_missing_thousands(cell)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "missing-thousands-separator")

    def test_missing_thousands_skips_small_values(self):
        cell = {
            "addr": "E11",
            "calculatedValue": "999",
            "valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False},
        }
        self.assertIsNone(fl.detect_missing_thousands(cell))

    def test_number_on_accounting_flags_number_type(self):
        cell = {
            "addr": "F5",
            "calculatedValue": "1000",
            "valueFormat": {"valueFormatType": "NUMBER", "useParensForNegatives": True},
        }
        f = fl.detect_number_on_accounting_column(cell)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "number-on-accounting-column")

    def test_precision_mismatch_flags_one_decimal(self):
        cell = {
            "addr": "G8",
            "calculatedValue": "12.3",
            "valueFormat": {
                "valueFormatType": "NUMBER",
                "precision": {"auto": False, "value": 1},
                "useParensForNegatives": True,
            },
        }
        f = fl.detect_precision_mismatch(cell)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "precision-mismatch")

    def test_precision_mismatch_skips_junk_band(self):
        cell = {
            "addr": "G9",
            "calculatedValue": "1.23456",
            "valueFormat": {"valueFormatType": "NUMBER", "precision": {"auto": False, "value": 5}},
        }
        self.assertIsNone(fl.detect_precision_mismatch(cell))

    def test_thousands_consensus_downgrades_without_neighbors(self):
        cell = {
            "addr": "H10",
            "calculatedValue": "5000",
            "valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False},
        }
        raw = [fl.detect_missing_thousands(cell)]
        out = fl.adjust_format_lane_findings(raw, [cell])
        self.assertEqual(out[0]["fix_lane"], "surfaced")
        self.assertFalse(out[0]["fixable"])

    def test_thousands_consensus_sets_target(self):
        good = _accounting_vf()
        neighbors = {
            "H9": {"addr": "H9", "valueFormat": dict(good)},
            "H11": {"addr": "H11", "valueFormat": dict(good)},
        }
        cell = {
            "addr": "H10",
            "calculatedValue": "5000",
            "valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False,
                            "precision": {"auto": False, "value": 0}},
        }
        cells = [cell, neighbors["H9"], neighbors["H11"]]
        raw = [fl.detect_missing_thousands(cell)]
        out = fl.adjust_format_lane_findings(raw, cells)
        self.assertEqual(out[0]["fix_lane"], "safe-auto")
        self.assertTrue(out[0]["target"]["valueFormat"]["showThousandsSeparator"])

    def test_prefix_mismatch_flags_missing_dollar(self):
        cell = {
            "addr": "J10",
            "calculatedValue": "1000",
            "valueFormat": {
                "valueFormatType": "ACCOUNTING",
                "useParensForNegatives": True,
                "precision": {"auto": False, "value": 0},
            },
        }
        f = fl.detect_prefix_mismatch(cell)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "prefix-mismatch")

    def test_prefix_mismatch_skips_percent(self):
        cell = {
            "addr": "J11",
            "calculatedValue": "0.12",
            "valueFormat": {"valueFormatType": "PERCENT", "precision": {"auto": False, "value": 0}},
        }
        self.assertIsNone(fl.detect_prefix_mismatch(cell))

    def test_prefix_consensus_sets_target(self):
        neighbor_vf = _accounting_vf(prefix="$")
        neighbors = {
            "J9": {"addr": "J9", "valueFormat": dict(neighbor_vf)},
            "J11": {"addr": "J11", "valueFormat": dict(neighbor_vf)},
        }
        cell = {
            "addr": "J10",
            "calculatedValue": "5000",
            "valueFormat": {
                "valueFormatType": "ACCOUNTING",
                "useParensForNegatives": True,
                "precision": {"auto": False, "value": 0},
            },
        }
        cells = [cell, neighbors["J9"], neighbors["J11"]]
        raw = [fl.detect_prefix_mismatch(cell)]
        out = fl.adjust_format_lane_findings(raw, cells)
        self.assertEqual(out[0]["fix_lane"], "safe-auto")
        self.assertEqual(out[0]["target"]["valueFormat"]["prefix"], "$")

    def test_zero_display_mismatch_flags_literal_zero(self):
        cell = {
            "addr": "K10",
            "calculatedValue": "0",
            "valueFormat": {
                "valueFormatType": "ACCOUNTING",
                "useParensForNegatives": True,
                "precision": {"auto": False, "value": 0},
            },
        }
        f = fl.detect_zero_display_mismatch(cell)
        self.assertIsNotNone(f)
        self.assertEqual(f["kind"], "zero-display-mismatch")

    def test_zero_display_skips_em_dash(self):
        cell = {
            "addr": "K11",
            "calculatedValue": "—",
            "valueFormat": {
                "valueFormatType": "ACCOUNTING",
                "displayZeroAs": "EM DASH",
                "useParensForNegatives": True,
            },
        }
        self.assertIsNone(fl.detect_zero_display_mismatch(cell))

    def test_zero_display_skips_when_format_already_em_dash(self):
        """Sheetdata calculatedValue can stay '0' while displayZeroAs EM DASH is already set."""
        cell = {
            "addr": "J11",
            "calculatedValue": "0",
            "valueFormat": {
                "valueFormatType": "ACCOUNTING",
                "displayZeroAs": "EM DASH",
                "useParensForNegatives": True,
                "precision": {"auto": False, "value": 0},
            },
        }
        self.assertIsNone(fl.detect_zero_display_mismatch(cell))

    def test_zero_display_consensus_sets_target(self):
        neighbor_vf = _accounting_vf(displayZeroAs="EM DASH")
        neighbors = {
            "K9": {"addr": "K9", "valueFormat": dict(neighbor_vf)},
            "K11": {"addr": "K11", "valueFormat": dict(neighbor_vf)},
        }
        cell = {
            "addr": "K10",
            "calculatedValue": "0",
            "valueFormat": {
                "valueFormatType": "ACCOUNTING",
                "useParensForNegatives": True,
                "precision": {"auto": False, "value": 0},
            },
        }
        cells = [cell, neighbors["K9"], neighbors["K11"]]
        raw = [fl.detect_zero_display_mismatch(cell)]
        out = fl.adjust_format_lane_findings(raw, cells)
        self.assertEqual(out[0]["fix_lane"], "safe-auto")
        self.assertEqual(out[0]["target"]["valueFormat"]["displayZeroAs"], "EM DASH")

    def test_zero_display_is_disabled_even_with_neighbor_agreement(self):
        zero = {"addr": "K10", "calculatedValue": "0", "valueFormat": _accounting_vf()}
        # Keep the low-level legacy behavior visible: the product entry point,
        # not a demo filter, must suppress this unsupported convention judgment.
        self.assertIsNotNone(fl.detect_zero_display_mismatch(zero))
        for display in ("EM DASH", "ZERO"):
            with self.subTest(display=display):
                vf = _accounting_vf(displayZeroAs=display)
                cells = [zero, {"addr": "K9", "calculatedValue": "17", "valueFormat": vf},
                         {"addr": "K11", "calculatedValue": "83", "valueFormat": vf}]
                self.assertNotIn("zero-display-mismatch", {f["kind"] for f in fl.detectors.scan_cells(cells)})
                groups = [{"kind": "zero-display-mismatch", "fix_lane": "surfaced", "addrs": ["K10"]}]
                fl.attach_gated_format_targets(groups, cells)
                self.assertNotIn("gated_target", groups[0])
                self.assertNotIn("gated_columns", groups[0])

    def test_column_majority_format_picks_dominant(self):
        vf = _accounting_vf(prefix="$")
        cells = [
            {"addr": "E5", "type": "plainText", "calculatedValue": "100",
             "valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False}},
            {"addr": "E6", "type": "plainText", "calculatedValue": "200", "valueFormat": dict(vf)},
            {"addr": "E7", "type": "plainText", "calculatedValue": "300", "valueFormat": dict(vf)},
            {"addr": "E8", "type": "plainText", "calculatedValue": "400", "valueFormat": dict(vf)},
        ]
        got = fl.column_majority_format("E", {c["addr"]: c for c in cells}, exclude_addrs={"E5"})
        self.assertIsNotNone(got)
        self.assertEqual(got.get("prefix"), "$")

    def test_attach_gated_format_targets_on_surfaced_group(self):
        vf = _accounting_vf(showThousandsSeparator=True)
        cells = [
            {"addr": "F10", "type": "plainText", "calculatedValue": "5000",
             "valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False}},
            {"addr": "F11", "type": "plainText", "calculatedValue": "100", "valueFormat": dict(vf)},
            {"addr": "F12", "type": "plainText", "calculatedValue": "200", "valueFormat": dict(vf)},
        ]
        groups = [{
            "kind": "missing-thousands-separator",
            "fix_lane": "surfaced",
            "addrs": ["F10"],
            "count": 1,
        }]
        fl.attach_gated_format_targets(groups, cells)
        self.assertIn("gated_target", groups[0])
        self.assertTrue(groups[0]["gated_target"]["valueFormat"]["showThousandsSeparator"])


class NoEvidenceFormatNoiseTests(unittest.TestCase):
    """drop_no_evidence_format_groups: silence the '(no neighbor format consensus)' noise."""

    def test_drops_surfaced_format_with_no_fix_and_no_gated(self):
        g = {"kind": "number-on-accounting-column", "fix_lane": "surfaced",
             "fixable": False, "addrs": ["F5"], "count": 1}
        self.assertTrue(fl.is_no_evidence_format_group(g))
        kept, dropped = fl.drop_no_evidence_format_groups([g])
        self.assertEqual(kept, [])
        self.assertEqual(dropped, [g])

    def test_keeps_fixable_format(self):
        g = {"kind": "missing-thousands-separator", "fix_lane": "safe-auto",
             "fixable": True, "addrs": ["E10"], "count": 1, "target": {"valueFormat": {}}}
        self.assertFalse(fl.is_no_evidence_format_group(g))
        kept, dropped = fl.drop_no_evidence_format_groups([g])
        self.assertEqual(kept, [g])
        self.assertEqual(dropped, [])

    def test_keeps_surfaced_format_with_gated_target(self):
        g = {"kind": "prefix-mismatch", "fix_lane": "surfaced", "fixable": False,
             "addrs": ["J10"], "count": 1, "gated_target": {"valueFormat": {"prefix": "$"}}}
        self.assertFalse(fl.is_no_evidence_format_group(g))
        self.assertEqual(fl.drop_no_evidence_format_groups([g])[0], [g])

    def test_keeps_surfaced_format_with_gated_columns(self):
        g = {"kind": "zero-display-mismatch", "fix_lane": "surfaced", "fixable": False,
             "addrs": ["K10", "L10"], "count": 2, "gated_columns": {"K": {}, "L": {}}}
        self.assertFalse(fl.is_no_evidence_format_group(g))
        self.assertEqual(fl.drop_no_evidence_format_groups([g])[0], [g])

    def test_keeps_negative_without_parens_surfaced(self):
        # ADR-0005: a mixed-column negative IS informative evidence, not no-evidence noise.
        g = {"kind": "negative-without-parens", "fix_lane": "surfaced", "fixable": False,
             "addrs": ["C3"], "count": 1, "detail": "minus-prefix (mixed column)"}
        self.assertFalse(fl.is_no_evidence_format_group(g))
        self.assertEqual(fl.drop_no_evidence_format_groups([g])[0], [g])

    def test_keeps_non_format_detectors(self):
        for kind in ("broken-ref", "blank-linked-cell", "low-contrast", "label-hygiene",
                     "display-wrapper", "formula-evaluates-blank"):
            g = {"kind": kind, "fix_lane": "surfaced", "fixable": False, "addrs": ["A1"], "count": 1}
            self.assertFalse(fl.is_no_evidence_format_group(g), kind)

    def test_partition_preserves_order_of_kept(self):
        noise = {"kind": "precision-mismatch", "fix_lane": "surfaced", "fixable": False,
                 "addrs": ["G8"], "count": 1}
        keep1 = {"kind": "broken-ref", "fix_lane": "surfaced", "fixable": False,
                 "addrs": ["E1"], "count": 1}
        keep2 = {"kind": "junk-decimal", "fix_lane": "safe-auto", "fixable": True,
                 "addrs": ["H2"], "count": 1, "target": {"valueFormat": {}}}
        kept, dropped = fl.drop_no_evidence_format_groups([keep1, noise, keep2])
        self.assertEqual(kept, [keep1, keep2])
        self.assertEqual(dropped, [noise])

    def test_end_to_end_pure_number_column_collapses_to_real_defects(self):
        """
        A column of plain NUMBER integers (no accounting neighbors, no column majority to
        copy from) used to emit a wall of surfaced 'number-on-accounting / prefix-mismatch
        (no neighbor format consensus)' rows. Through the real pipeline those now drop, while a
        genuine broken-ref survives.
        """
        import detectors

        cells = [
            {"addr": f"C{i}", "type": "plainText", "calculatedValue": str(i * 100),
             "valueFormat": {"valueFormatType": "NUMBER", "useParensForNegatives": False}}
            for i in range(1, 6)
        ]
        cells.append({"addr": "E1", "calculatedValue": "#REF!", "value": "=BAD()"})

        cells_by_addr = {c["addr"]: c for c in cells}
        groups = detectors.group_findings(detectors.scan_cells(cells), cells_by_addr)
        fl.attach_gated_format_targets(groups, cells)
        # before the drop: the format wave produced surfaced no-evidence rows
        self.assertTrue(any(fl.is_no_evidence_format_group(g) for g in groups))

        kept, dropped = fl.drop_no_evidence_format_groups(groups)
        kept_kinds = {g["kind"] for g in kept}
        self.assertIn("broken-ref", kept_kinds)
        self.assertNotIn("number-on-accounting-column", kept_kinds)
        self.assertNotIn("prefix-mismatch", kept_kinds)
        self.assertTrue(dropped)


if __name__ == "__main__":
    unittest.main(verbosity=2)

import json
import tempfile
import unittest
from pathlib import Path

import diagnose
import review_packet as rp


def _sheet_queue():
    """A realistic sheet queue built through diagnose (real item shapes)."""
    safe = {
        "kind": "junk-decimal", "severity": "medium", "signature": "5 trailing dp",
        "fixable": True, "fix_lane": "safe-auto", "addrs": ["B7", "B8"], "count": 2,
        "target": {"valueFormat": {}},
    }
    # empty-linked-range is surfaced with no guided checklist in diagnose -> UNVERIFIED.
    surfaced = {
        "kind": "empty-linked-range", "severity": "medium",
        "signature": "linked block F5:R9 is wholly empty", "fixable": False, "fix_lane": "surfaced",
        "addrs": ["F5"], "count": 1,
    }
    broken = {
        "kind": "broken-ref", "severity": "high", "signature": "formula result is #REF!",
        "fixable": False, "fix_lane": "surfaced", "addrs": ["E1"], "count": 1,
    }
    return diagnose.build_sheet_queue(
        [safe, surfaced, broken], spreadsheet_id="ss-secret-123", sheet_id="sh1", sheet_name="TB",
    )


class RedactionTests(unittest.TestCase):
    def test_redacts_financial_runs(self):
        self.assertEqual(rp._redact_numbers("Total assets Δ=+1,234,567.00"), "Total assets Δ=#")
        self.assertEqual(rp._redact_numbers("plug of $50,000"), "plug of #")
        self.assertEqual(rp._redact_numbers("(1,234)"), "#")

    def test_preserves_hex_colors(self):
        self.assertEqual(rp._redact_numbers("#FFFFFF on #7F7F7F"), "#FFFFFF on #7F7F7F")

    def test_none_and_empty_safe(self):
        self.assertIsNone(rp._redact_numbers(None))
        self.assertEqual(rp._redact_numbers(""), "")

    def test_workbook_hash_pseudonymous(self):
        h = rp._workbook_hash("ss-secret-123")
        self.assertNotIn("ss-secret-123", h)
        self.assertEqual(len(h), 16)
        self.assertEqual(h, rp._workbook_hash("ss-secret-123"))  # stable


class ClassificationTests(unittest.TestCase):
    def test_safe_auto_is_accept(self):
        item = {"fixable": True, "fix_lane": "safe-auto", "kind": "junk-decimal"}
        self.assertEqual(rp.classify_disposition(item), rp.ACCEPT)

    def test_guided_is_blocked(self):
        item = {"fixable": True, "fix_lane": "guided", "kind": "negative-without-parens"}
        self.assertEqual(rp.classify_disposition(item), rp.BLOCKED)

    def test_fixable_non_safe_auto_is_blocked(self):
        item = {"fixable": True, "fix_lane": "surfaced", "kind": "whatever"}
        self.assertEqual(rp.classify_disposition(item), rp.BLOCKED)

    def test_tieout_check_is_blocked(self):
        item = {"fixable": False, "fix_lane": "surfaced", "kind": "check-tieout",
                "check": "tieout", "diagnosis": {"guided_lane": "export-proof"}}
        self.assertEqual(rp.classify_disposition(item), rp.BLOCKED)

    def test_broken_ref_is_blocked(self):
        item = {"fixable": False, "fix_lane": "surfaced", "kind": "broken-ref"}
        self.assertEqual(rp.classify_disposition(item), rp.BLOCKED)

    def test_scan_error_is_blocked(self):
        item = {"fixable": False, "fix_lane": "surfaced", "kind": "scan-error"}
        self.assertEqual(rp.classify_disposition(item), rp.BLOCKED)

    def test_plain_surfaced_is_unverified(self):
        item = {"fixable": False, "fix_lane": "surfaced", "kind": "display-wrapper", "diagnosis": {}}
        self.assertEqual(rp.classify_disposition(item), rp.UNVERIFIED)


class RollbackAndReasonTests(unittest.TestCase):
    def test_accept_rollback_is_automatic_revert(self):
        rb = rp.rollback_path({"addrs": ["B7"]}, rp.ACCEPT)
        self.assertIn("reverts", rb)
        self.assertIn("B7", rb)

    def test_blocked_rollback_no_wingman_write(self):
        rb = rp.rollback_path({"kind": "negative-without-parens"}, rp.BLOCKED)
        self.assertIn("No Wingman write", rb)

    def test_scan_error_rollback_na(self):
        self.assertTrue(rp.rollback_path({"kind": "scan-error"}, rp.BLOCKED).startswith("n/a"))

    def test_unverified_rollback_na(self):
        self.assertTrue(rp.rollback_path({}, rp.UNVERIFIED).startswith("n/a"))

    def test_reason_tieout_mentions_source(self):
        r = rp.disposition_reason({"kind": "check-tieout", "check": "tieout"}, rp.BLOCKED)
        self.assertIn("source", r.lower())


class BuildPacketTests(unittest.TestCase):
    def setUp(self):
        self.packet = rp.build_review_packet(
            _sheet_queue(), label="ACFR preset", generated_at="2026-06-22T19:00:00+00:00",
        )

    def test_overall_blocked(self):
        # safe-auto -> ACCEPT, display-wrapper -> UNVERIFIED, broken-ref -> BLOCKED
        self.assertEqual(self.packet["overall"], rp.BLOCKED)

    def test_buckets(self):
        bd = self.packet["summary"]["byDisposition"]
        self.assertEqual(bd[rp.ACCEPT], 1)
        self.assertEqual(bd[rp.BLOCKED], 1)
        self.assertEqual(bd[rp.UNVERIFIED], 1)

    def test_never_client_ready(self):
        self.assertIs(self.packet["clientReady"], False)
        self.assertIn("never client-ready", self.packet["caveat"])

    def test_workbook_pseudonymized_no_raw_id(self):
        self.assertNotIn("ss-secret-123", json.dumps(self.packet))
        self.assertEqual(len(self.packet["workbookHash"]), 16)

    def test_capability_tier_carried(self):
        accept = self.packet["dispositions"][rp.ACCEPT][0]
        self.assertEqual(accept["capabilityTier"], "safe-auto")
        self.assertIn("rollback", accept)

    def test_each_finding_has_rollback(self):
        for bucket in self.packet["dispositions"].values():
            for f in bucket:
                self.assertTrue(f.get("rollback"))

    def test_empty_queue_without_coverage_is_unverified(self):
        packet = rp.build_review_packet({"scope": "sheet", "spreadsheetId": "x", "items": []})
        self.assertEqual(packet["overall"], "UNVERIFIED")
        self.assertFalse(packet["coverage"]["complete"])
        self.assertEqual(packet["summary"]["total"], 0)

    def test_addr_cap_truncates(self):
        many = {"kind": "junk-decimal", "severity": "low", "fixable": True, "fix_lane": "safe-auto",
                "addrs": [f"A{i}" for i in range(40)], "count": 40, "signature": "x",
                "diagnosis": {}}
        q = {"scope": "sheet", "spreadsheetId": "x", "items": [
            diagnose.diagnose_group(many, sheet_id="s", spreadsheet_id="x")]}
        packet = rp.build_review_packet(q, addr_cap=25)
        f = packet["dispositions"][rp.ACCEPT][0]
        self.assertEqual(len(f["addrs"]), 25)
        self.assertTrue(f["addrsTruncated"])
        self.assertEqual(f["addrCount"], 40)


class TieoutPacketTests(unittest.TestCase):
    def test_merged_tieout_is_blocked_and_redacted(self):
        base = diagnose.build_sheet_queue([], spreadsheet_id="ss9", sheet_id="sh9", sheet_name="GW")
        checks_meta = {
            "applied": True, "suite": "tieout", "fail_count": 1,
            "fail_rows": [{
                "check": "tieout", "source": "tieout_table",
                "detail": "Gov_Funds CY assets Total assets Δ=+1,250,000.00",
                "label": "Total assets", "calc": "1,000,000", "pub": "2,250,000",
            }],
        }
        merged = diagnose.merge_queue_with_checks(base, checks_meta)
        packet = rp.build_review_packet(merged, generated_at="2026-06-22T19:00:00+00:00")
        self.assertEqual(packet["overall"], rp.BLOCKED)
        blob = json.dumps(packet)
        self.assertNotIn("1,250,000", blob)
        self.assertNotIn("2,250,000", blob)
        blocked = packet["dispositions"][rp.BLOCKED][0]
        self.assertEqual(blocked["check"], "tieout")
        self.assertIn("source", blocked["dispositionReason"].lower())


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.packet = rp.build_review_packet(
            _sheet_queue(), label="ACFR preset", generated_at="2026-06-22T19:00:00+00:00")
        self.md = rp.render_packet_md(self.packet)

    def test_overall_in_md(self):
        self.assertIn("Overall:** BLOCKED", self.md)

    def test_blocked_section_before_accept(self):
        self.assertLess(self.md.index("BLOCKED —"), self.md.index("ACCEPT —"))

    def test_rollback_rendered(self):
        self.assertIn("Rollback:", self.md)

    def test_no_client_value_in_md(self):
        self.assertNotIn("1,234", self.md)


class WritePacketTests(unittest.TestCase):
    def test_writes_json_and_md_outside_repo(self):
        packet = rp.build_review_packet(
            _sheet_queue(), generated_at="2026-06-22T19:00:00+00:00")
        with tempfile.TemporaryDirectory() as td:
            paths = rp.write_packet(packet, out_dir=td)
            self.assertTrue(Path(paths["jsonPath"]).exists())
            self.assertTrue(Path(paths["mdPath"]).exists())
            reloaded = json.loads(Path(paths["jsonPath"]).read_text())
            self.assertEqual(reloaded["overall"], rp.BLOCKED)
            # filename carries the pseudonymous hash, never a raw id
            self.assertIn(packet["workbookHash"], Path(paths["jsonPath"]).name)

    def test_packet_dir_env_override(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            os.environ["WINGMAN_PACKET_DIR"] = td
            try:
                self.assertEqual(str(rp.packet_dir()), td)
            finally:
                os.environ.pop("WINGMAN_PACKET_DIR", None)


class CoverageTests(unittest.TestCase):
    def test_partial_coverage_prevents_clean_packet(self):
        packet = rp.build_review_packet({
            "scope": "sheet",
            "spreadsheetId": "ss",
            "items": [],
            "truncated": True,
            "link_fetch": {"applied": False, "skipped": "disabled"},
        })
        self.assertEqual(packet["overall"], rp.UNVERIFIED)
        self.assertFalse(packet["coverage"]["complete"])
        md = rp.render_packet_md(packet)
        self.assertIn("Coverage", md)
        self.assertIn("sheet scan truncated", md)


if __name__ == "__main__":
    unittest.main()

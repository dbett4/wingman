#!/usr/bin/env python3
"""Tests for the Wingman activity log + improvement digest (PII safety, aggregation, flags)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

import wingman_log as wl
import wingman_log_digest as wd


class ScanSummaryTests(unittest.TestCase):
    def _payload(self):
        return {
            "groups": [
                {"kind": "low-contrast", "fix_lane": "safe-auto", "severity": "high",
                 "count": 3, "addrs": ["A1", "A2", "A3"],
                 "cellValues": {"A1": "CONFIDENTIAL CLIENT NAME"}},
                {"kind": "junk-decimal", "fix_lane": "surfaced", "severity": "medium", "addrs": ["B7"]},
            ],
            "cellCount": 500, "truncated": True,
            "vision": {"applied": True, "netNew": 2},
            "formula_fetch": {"enabled": False},
        }

    def test_counts_cells_not_groups(self):
        s = wl.scan_summary(self._payload(), spreadsheet_id="ss", sheet_id="sh")
        self.assertEqual(s["findingCount"], 4)
        self.assertEqual(s["byKind"], {"low-contrast": 3, "junk-decimal": 1})
        self.assertEqual(s["byLane"], {"safe-auto": 3, "surfaced": 1})

    def test_metadata_carried(self):
        s = wl.scan_summary(self._payload(), spreadsheet_id="ss", sheet_id="sh")
        self.assertTrue(s["truncated"])
        self.assertTrue(s["visionApplied"])
        self.assertEqual(s["visionNetNew"], 2)
        self.assertEqual(s["cellCount"], 500)

    def test_sheet_is_hashed_not_raw(self):
        s = wl.scan_summary(self._payload(), spreadsheet_id="ss-secret", sheet_id="sh-9")
        self.assertEqual(len(s["sheet"]), 16)
        self.assertNotIn("ss-secret", json.dumps(s))

    def test_no_cell_content_leaks(self):
        blob = json.dumps(wl.scan_summary(self._payload(), spreadsheet_id="ss", sheet_id="sh"))
        self.assertNotIn("CONFIDENTIAL CLIENT NAME", blob)
        self.assertNotIn("cellValues", blob)


class FixSummaryTests(unittest.TestCase):
    def test_status_and_phase(self):
        f = wl.fix_summary("junk-decimal", True, {"status": "mismatch-reverted", "addr": "B7"})
        self.assertEqual(f["status"], "mismatch-reverted")
        self.assertEqual(f["phase"], "apply")
        self.assertEqual(f["kind"], "junk-decimal")

    def test_dry_run_phase(self):
        f = wl.fix_summary("low-contrast", False, {"status": "dry-run"})
        self.assertEqual(f["phase"], "dry-run")

    def test_no_before_state_leak(self):
        f = wl.fix_summary("low-contrast", True, {"status": "applied", "before": "#abc", "value": "secret"})
        self.assertNotIn("before", f)
        self.assertNotIn("value", f)


class LogEventTests(unittest.TestCase):
    def test_writes_jsonl_with_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["WINGMAN_LOG_DIR"] = td
            try:
                wl.log_event({"event": "scan", "findingCount": 1})
                files = list(Path(td).glob("wingman-events-*.jsonl"))
                self.assertEqual(len(files), 1)
                rec = json.loads(files[0].read_text().strip())
                self.assertIn("ts", rec)
                self.assertEqual(rec["schema"], wl.SCHEMA_VERSION)
            finally:
                os.environ.pop("WINGMAN_LOG_DIR", None)

    def test_log_helpers_never_raise(self):
        # bad inputs must be swallowed, never propagate to the caller
        wl.log_scan(None, None, None)
        wl.log_fix(None, None, None)


class DigestTests(unittest.TestCase):
    def _events(self):
        return [
            {"event": "scan", "byKind": {"low-contrast": 2, "junk-decimal": 5},
             "byLane": {"safe-auto": 7}, "visionNetNew": 1, "truncated": True},
            *[{"event": "fix", "kind": "junk-decimal", "phase": "apply", "status": "mismatch-reverted"}
              for _ in range(6)],
            *[{"event": "fix", "kind": "low-contrast", "phase": "apply", "status": "applied"}
              for _ in range(5)],
            *[{"event": "fix", "kind": "label-hygiene", "phase": "apply", "status": "refused"}
              for _ in range(5)],
        ]

    def test_volume_ranking(self):
        d = wd.build_digest(self._events())
        self.assertEqual(list(d["findingsByKind"].keys())[0], "junk-decimal")  # 5 > 2
        self.assertEqual(d["truncatedScans"], 1)
        self.assertEqual(d["visionNetNew"], 1)

    def test_high_revert_flag(self):
        d = wd.build_digest(self._events())
        kinds = {(f["kind"], f["signal"]) for f in d["flags"]}
        self.assertIn(("junk-decimal", "high-revert"), kinds)

    def test_high_refused_flag(self):
        d = wd.build_digest(self._events())
        kinds = {(f["kind"], f["signal"]) for f in d["flags"]}
        self.assertIn(("label-hygiene", "high-refused"), kinds)

    def test_healthy_kind_not_flagged(self):
        d = wd.build_digest(self._events())
        self.assertNotIn("low-contrast", {f["kind"] for f in d["flags"]})

    def test_below_min_samples_not_flagged(self):
        events = [{"event": "fix", "kind": "junk-decimal", "phase": "apply", "status": "mismatch-reverted"}
                  for _ in range(3)]  # only 3 < _MIN_SAMPLES
        self.assertEqual(wd.build_digest(events)["flags"], [])

    def test_load_events_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "wingman-events-2026-06-19.jsonl"
            p.write_text('{"event":"scan"}\nNOT JSON\n\n{"event":"fix","kind":"x"}\n')
            events = wd.load_events(Path(td))
            self.assertEqual(len(events), 2)

    def test_not_in_scan_page_counts_as_refused(self):
        events = [{"event": "fix", "kind": "low-contrast", "phase": "apply", "status": "not-in-scan-page"}
                  for _ in range(5)]
        flags = {(f["kind"], f["signal"]) for f in wd.build_digest(events)["flags"]}
        self.assertIn(("low-contrast", "high-refused"), flags)

    def test_no_fix_needed_excluded_from_denominator(self):
        # 5 refused + 100 no-fix-needed. ONLY excluding no-fix-needed lets refused_rate hit 100% and flag.
        events = ([{"event": "fix", "kind": "k", "phase": "apply", "status": "refused"} for _ in range(5)]
                  + [{"event": "fix", "kind": "k", "phase": "apply", "status": "no-fix-needed"} for _ in range(100)])
        flags = {(f["kind"], f["signal"]) for f in wd.build_digest(events)["flags"]}
        self.assertIn(("k", "high-refused"), flags)

    def test_bad_rate_exact_30pct_threshold_fires(self):
        events = ([{"event": "fix", "kind": "k", "phase": "apply", "status": "mismatch-reverted"} for _ in range(3)]
                  + [{"event": "fix", "kind": "k", "phase": "apply", "status": "applied"} for _ in range(7)])
        flags = {(f["kind"], f["signal"]) for f in wd.build_digest(events)["flags"]}
        self.assertIn(("k", "high-revert"), flags)  # 3/10 == 0.30, >= threshold


class PIIGuardTests(unittest.TestCase):
    """log_event must strip content-bearing keys even when a caller bypasses the summary helpers."""

    def test_strip_helper_recurses(self):
        out = wl._strip_forbidden({"a": 1, "value": "x", "n": {"detail": "y", "ok": 2}, "lst": [{"formula": "=A1", "k": 3}]})
        self.assertEqual(out, {"a": 1, "n": {"ok": 2}, "lst": [{"k": 3}]})

    def test_log_event_strips_smuggled_content(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["WINGMAN_LOG_DIR"] = td
            try:
                wl.log_event({
                    "event": "scan", "findingCount": 1, "addr": "B12",
                    "value": "SECRET VALUE", "detail": "Total  Assets ", "formula": "=A1",
                    "groups": [{"cellValues": {"A1": "CLIENT NAME"}, "addrs": ["A1"]}],
                    "byKind": {"low-contrast": 2},  # safe nested dict must survive
                })
                blob = list(Path(td).glob("*.jsonl"))[0].read_text()
                for leak in ("SECRET VALUE", "CLIENT NAME", "Total  Assets", "=A1"):
                    self.assertNotIn(leak, blob)
                rec = json.loads(blob.strip())
                self.assertEqual(rec["addr"], "B12")                 # safe coord preserved
                self.assertEqual(rec["byKind"], {"low-contrast": 2})  # safe nested survives
                self.assertNotIn("groups", rec)
                self.assertNotIn("value", rec)
            finally:
                os.environ.pop("WINGMAN_LOG_DIR", None)


class IntegrationTests(unittest.TestCase):
    """log_scan/log_fix -> load_events -> build_digest, the wiring behind GET /digest."""

    def test_log_then_digest_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["WINGMAN_LOG_DIR"] = td
            try:
                wl.log_scan("ss", "s1", {"groups": [{"kind": "junk-decimal", "fix_lane": "surfaced",
                                                      "severity": "medium", "count": 5, "addrs": ["B1"]}],
                                         "cellCount": 100})
                for _ in range(5):
                    wl.log_fix("junk-decimal", True, {"status": "mismatch-reverted", "addr": "B1"})
                d = wd.build_digest(wd.load_events(Path(td)))
                self.assertEqual(d["scanCount"], 1)
                self.assertEqual(d["fixCount"], 5)
                self.assertEqual(d["findingsByKind"], {"junk-decimal": 5})
                flags = {(f["kind"], f["signal"]) for f in d["flags"]}
                self.assertIn(("junk-decimal", "high-revert"), flags)
            finally:
                os.environ.pop("WINGMAN_LOG_DIR", None)


if __name__ == "__main__":
    unittest.main()

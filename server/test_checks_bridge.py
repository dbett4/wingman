#!/usr/bin/env python3
"""Unit tests for server/checks_bridge.py (mocked subprocess — no live run_checks)."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checks_bridge  # noqa: E402
import diagnose  # noqa: E402


SAMPLE_REPORT = """# Run-Checks Report
xlsx: /tmp/snap.xlsx
suite: tieout

## Tieout (Live Workiva)
# Tieout Scorecard

OK=10  WARN=0  FAIL=2  ACCEPTED=0  ZERO=0  SKIP=0

## FAILs (|delta| > $10000)

| Stmt | Col | Label | Calc | Pub | Delta |
|---|---|---|---:|---:|---:|
| Gov_Funds | CY | Total assets | 1,234.00 | 1,000.00 | +234.00 |
| Gov_Funds | PY | Total assets | 900.00 | 1,000.00 | -100.00 |

---
2 FAIL(s) | elapsed 3.2s
"""

SAMPLE_JSON = {
    "fail_count": 2,
    "fail_checks": ["TieoutSuite"],
    "pass_count": 1,
    "exit_code": 1,
    "report_path": "",  # filled in tests
}


# Minimal scorecard: one FAIL (double-aggregation), one ZERO (source gap), one OK.
SAMPLE_SCORECARD = {
    "generated_at": "2026-06-22T06:15:00Z",
    "results": [
        {
            "statement": "Gov-Wide - Net Position",
            "line_item": "Total net position",
            "column": "Total",
            "wb_value_thousands": 2000.0,
            "pdf_value_thousands": 1000.0,
            "variance_thousands": 1000.0,
            "wb_row": 50,
            "wb_col": 12,
            "status": "FAIL",
        },
        {
            "statement": "Gov Funds - Balance Sheet",
            "line_item": "Unearned Rev - Other",
            "column": "General",
            "wb_value_thousands": 0.0,
            "pdf_value_thousands": 250.0,
            "variance_thousands": -250.0,
            "wb_row": 42,
            "wb_col": 8,
            "status": "ZERO",
        },
        {
            "statement": "Gov-Wide - Net Position",
            "line_item": "Cash and investments",
            "column": "Total",
            "wb_value_thousands": 500.0,
            "pdf_value_thousands": 500.0,
            "variance_thousands": 0.0,
            "status": "OK",
        },
    ],
}


def _write_scorecard(td: str) -> str:
    path = os.path.join(td, "tieout_scorecard.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(SAMPLE_SCORECARD, f)
    return path


class ScorecardIngestTests(unittest.TestCase):
    """F3: surface an existing tieout scorecard JSON read-only (no subprocess)."""

    def test_ingest_scorecard_from_override_path(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_scorecard(td)
            with patch.dict(os.environ, {"WINGMAN_TIEOUT_SCORECARD": path}, clear=False):
                meta = checks_bridge.ingest_scorecard("ss1", project="acfr")
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["source"], "scorecard")
        self.assertIsNone(meta["exit_code"])  # no process spawned
        # Only FAIL + ZERO surface, not OK.
        self.assertEqual(meta["fail_count"], 2)
        statuses = {r.get("tieout_status") for r in meta["fail_rows"]}
        self.assertEqual(statuses, {"FAIL", "ZERO"})
        self.assertEqual(meta["scorecard_generated_at"], "2026-06-22T06:15:00Z")
        self.assertEqual(meta["tieout_enrich"]["source"], "scorecard_ingest")
        # Coordinates carried through from the scorecard.
        fail = next(r for r in meta["fail_rows"] if r["tieout_status"] == "FAIL")
        self.assertEqual(fail["wb_row"], 50)
        self.assertEqual(fail.get("jumpHint", {}).get("addr"), "L50")

    def test_ingest_scorecard_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "WINGMAN_TIEOUT_SCORECARD"}
        with patch.dict(os.environ, env, clear=True):
            meta = checks_bridge.ingest_scorecard("ss1", project="othercity")
        self.assertFalse(meta["applied"])
        self.assertIsNotNone(meta["skipped"])
        self.assertEqual(meta["fail_count"], 0)

    def test_ingest_scorecard_corrupt_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            meta = checks_bridge.ingest_scorecard(
                "ss1", project="acfr", scorecard_path=path)
        self.assertFalse(meta["applied"])
        self.assertIsNotNone(meta["error"])
        self.assertEqual(os.path.realpath(meta["report_path"]), os.path.realpath(path))

    def test_suite_scorecard_routes_to_ingest(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_scorecard(td)
            with patch.dict(os.environ, {"WINGMAN_TIEOUT_SCORECARD": path}, clear=False):
                # Even with a live script available, suite=scorecard never spawns it.
                with patch.object(checks_bridge, "resolve_run_checks_script") as rs:
                    meta = checks_bridge.run_checks_suite("ss1", suite="scorecard")
                    rs.assert_not_called()
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["source"], "scorecard")
        self.assertEqual(meta["fail_count"], 2)

    def test_fallback_when_no_script_acfr(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_scorecard(td)
            env = {**os.environ, "WINGMAN_TIEOUT_SCORECARD": path}
            with patch.dict(os.environ, env, clear=False):
                with patch.object(checks_bridge, "resolve_run_checks_script", return_value=None):
                    with patch.object(
                        checks_bridge, "resolve_working_dir", return_value=(td, "acfr")):
                        meta = checks_bridge.run_checks_suite("ss1", suite="tieout")
        # Live skipped, but the existing scorecard was surfaced instead.
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["source"], "scorecard")
        self.assertIn("live_skipped", meta)
        self.assertEqual(meta["fail_count"], 2)

    def test_no_fallback_when_not_eligible(self):
        # Non-acfr project + no override -> stays a skip even if a script is missing.
        env = {k: v for k, v in os.environ.items() if k != "WINGMAN_TIEOUT_SCORECARD"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(checks_bridge, "resolve_run_checks_script", return_value=None):
                with patch.object(
                    checks_bridge, "resolve_working_dir", return_value=("/x", "othercity")):
                    meta = checks_bridge.run_checks_suite("ss1", suite="tieout")
        self.assertFalse(meta["applied"])
        self.assertIsNotNone(meta["skipped"])

    def test_fallback_on_timeout_acfr(self):
        import subprocess as _sp
        with tempfile.TemporaryDirectory() as td:
            path = _write_scorecard(td)
            script = os.path.join(td, "run_checks.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write("# stub\n")

            def boom(cmd, *, cwd, timeout):
                raise _sp.TimeoutExpired(cmd, timeout)

            env = {**os.environ, "WINGMAN_TIEOUT_SCORECARD": path}
            with patch.dict(os.environ, env, clear=False):
                with patch.object(
                    checks_bridge, "resolve_run_checks_script",
                    return_value=pathlib.Path(script)):
                    with patch.object(
                        checks_bridge, "resolve_working_dir", return_value=(td, "acfr")):
                        meta = checks_bridge.run_checks_suite(
                            "ss1", suite="tieout", runner=boom)
        self.assertTrue(meta["applied"])
        self.assertEqual(meta["source"], "scorecard")
        self.assertIn("timed out", meta["live_skipped"])
        self.assertEqual(meta["fail_count"], 2)

    def test_merge_scorecard_meta_into_queue(self):
        with tempfile.TemporaryDirectory() as td:
            path = _write_scorecard(td)
            meta = checks_bridge.ingest_scorecard(
                "ss1", project="acfr", scorecard_path=path)
        queue = diagnose.build_sheet_queue(
            [], spreadsheet_id="ss1", sheet_id="sh1", sheet_name="Net Position")
        merged = diagnose.merge_queue_with_checks(queue, meta)
        self.assertEqual(len(merged["items"]), 2)
        self.assertTrue(all(i["kind"] == "check-tieout" for i in merged["items"]))
        self.assertEqual(merged["checks"]["source"], "scorecard")
        self.assertEqual(merged["summary"]["checks"], 2)


class ChecksBridgeTests(unittest.TestCase):
    def test_parse_report_fails_tieout_table(self):
        rows = checks_bridge.parse_report_fails(SAMPLE_REPORT)
        self.assertGreaterEqual(len(rows), 2)
        table_rows = [r for r in rows if r.get("source") == "tieout_table"]
        self.assertEqual(len(table_rows), 2)
        self.assertEqual(table_rows[0]["check"], "tieout")
        self.assertIn("Gov_Funds", table_rows[0]["detail"])

    def test_parse_report_acctmap_fail_line(self):
        text = "  [FAIL] acctmap_gaps: 3 unmapped GL code(s)\n"
        rows = checks_bridge.parse_report_fails(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check"], "acctmap_gaps")

    def test_resolve_run_checks_script_env(self):
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "run_checks.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write("# stub\n")
            with patch.dict(os.environ, {"WINGMAN_CHECKS_SCRIPTS": td}, clear=False):
                resolved = checks_bridge.resolve_run_checks_script()
            self.assertEqual(os.path.realpath(str(resolved)), os.path.realpath(script))

    def test_resolve_working_dir_from_projects_json(self):
        with tempfile.TemporaryDirectory() as td:
            ss = "abc123def456"
            pj = os.path.join(td, "projects.json")
            with open(pj, "w", encoding="utf-8") as f:
                json.dump({"ss_id": ss, "client": "test"}, f)
            with patch.dict(os.environ, {"WINGMAN_CHECKS_WORKING_DIR": td}, clear=False):
                got = checks_bridge.resolve_working_dir(ss)
            self.assertIsNotNone(got)
            self.assertEqual(os.path.realpath(got[0]), os.path.realpath(td))

    def test_default_acfr_preset_ss_map(self):
        ss = checks_bridge.client_preset.acfr_preset_ss_id()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINGMAN_CHECKS_WORKING_DIR", None)
            os.environ.pop("CHECKS_CLIENT_DIR", None)
            os.environ.pop("WINGMAN_CHECKS_SS_MAP", None)
        mapped = checks_bridge._ss_map().get(ss)
        self.assertIsNotNone(mapped)
        self.assertTrue(mapped.endswith("riverton"))

    def test_resolve_acfr_preset_infers_project(self):
        ss = checks_bridge.client_preset.acfr_preset_ss_id()
        wd = checks_bridge.client_preset.acfr_preset_working_dir()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINGMAN_CHECKS_WORKING_DIR", None)
            os.environ.pop("WINGMAN_CHECKS_PROJECT", None)
            os.environ.pop("WINGMAN_CHECKS_SS_MAP", None)
        with patch.object(pathlib.Path, "is_dir", return_value=True):
            got = checks_bridge.resolve_working_dir(ss)
        self.assertIsNotNone(got)
        self.assertEqual(got[1], "acfr")

    def test_env_ss_map_overrides_default(self):
        ss = checks_bridge.client_preset.acfr_preset_ss_id()
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"WINGMAN_CHECKS_SS_MAP": json.dumps({ss: td})}, clear=False):
                self.assertEqual(checks_bridge._ss_map().get(ss), td)

    def test_run_checks_suite_mocked_success(self):
        with tempfile.TemporaryDirectory() as td:
            report = os.path.join(td, "run_checks_report.md")
            with open(report, "w", encoding="utf-8") as f:
                f.write(SAMPLE_REPORT)
            payload = dict(SAMPLE_JSON)
            payload["report_path"] = report

            def fake_runner(cmd, *, cwd, timeout):
                class Proc:
                    returncode = 1
                    stdout = json.dumps(payload) + "\n"
                    stderr = ""
                return Proc()

            with patch.dict(os.environ, {"WINGMAN_CHECKS_WORKING_DIR": td}, clear=False):
                with patch.object(checks_bridge, "resolve_run_checks_script", return_value=__import__("pathlib").Path(__file__)):
                    meta = checks_bridge.run_checks_suite("abc123def456", suite="tieout", runner=fake_runner)

            self.assertTrue(meta["applied"])
            self.assertEqual(meta["fail_count"], 2)
            self.assertGreaterEqual(len(meta["fail_rows"]), 2)

    def test_run_checks_suite_skipped_no_script(self):
        # No script, no resolvable acfr project, no scorecard override -> honest skip.
        env = {k: v for k, v in os.environ.items() if k != "WINGMAN_TIEOUT_SCORECARD"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(checks_bridge, "resolve_run_checks_script", return_value=None):
                meta = checks_bridge.run_checks_suite("ss1", suite="tieout")
        self.assertFalse(meta["applied"])
        self.assertIn("skipped", meta)
        self.assertIsNotNone(meta["skipped"])

    def test_diagnose_check_fail_pathway(self):
        item = diagnose.diagnose_check_fail({
            "check": "acctmap_gaps",
            "detail": "acctmap_gaps: 2 unmapped GL",
        }, spreadsheet_id="ss1")
        self.assertEqual(item["kind"], "check-acctmap-gaps")
        self.assertEqual(item["diagnosis"]["pathway_id"], "data.acctmap-gaps")
        self.assertFalse(item["fixable"])

    def test_merge_queue_with_checks(self):
        queue = diagnose.build_sheet_queue([], spreadsheet_id="ss1", sheet_id="sh1", sheet_name="TB")
        meta = {
            "requested": True,
            "suite": "tieout",
            "applied": True,
            "fail_count": 1,
            "fail_rows": [{"check": "tieout", "detail": "Gov_Funds CY Total assets Δ=+234.00", "source": "tieout_table", "stmt": "Gov_Funds"}],
        }
        merged = diagnose.merge_queue_with_checks(queue, meta)
        self.assertEqual(len(merged["items"]), 1)
        self.assertEqual(merged["items"][0]["kind"], "check-tieout")
        self.assertEqual(merged["summary"]["checks"], 1)
        self.assertTrue(merged["checks"]["applied"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

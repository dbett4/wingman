#!/usr/bin/env python3
"""Unit tests for the document range-link lane (U2 / ADR-0008)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import link_lane  # noqa: E402
import wk_client as wk  # noqa: E402


def _src_link(lid, sr, sc, er, ec, revision=None):
    out = {"id": lid, "type": "source",
            "range": {"startRow": sr, "startColumn": sc, "stopRow": er, "stopColumn": ec}}
    if revision is not None:
        out["revision"] = revision
    return out


def _dest_link(lid, sr, sc, er, ec, revision=None):
    out = {"id": lid, "type": "destination",
            "range": {"startRow": sr, "startColumn": sc, "stopRow": er, "stopColumn": ec}}
    if revision is not None:
        out["revision"] = revision
    return out


class AnalyzeLinksTests(unittest.TestCase):
    def test_empty_block_fires_once_at_anchor(self):
        cells = [{"addr": "B2", "calculatedValue": ""}, {"addr": "C2", "value": ""}]
        links = [_src_link("L1", 1, 1, 1, 2)]  # B2:C2
        findings, meta = link_lane.analyze_links(cells, links, truncated=False)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "empty-linked-range")
        self.assertEqual(findings[0]["addr"], "B2")          # anchor = top-left
        self.assertEqual(findings[0]["rangeA1"], "B2:C2")
        self.assertEqual(findings[0]["fix_lane"], "surfaced")  # never auto-fix
        self.assertEqual(meta["emptyLinkedBlocks"], 1)
        self.assertEqual(meta["linkHealth"]["summary"]["no_dest"], 1)
        self.assertEqual(meta["linkHealthVocabulary"], ["healthy", "no_dest", "destination", "orphaned"])

    def test_populated_block_does_not_fire(self):
        cells = [{"addr": "B2", "calculatedValue": "100"}, {"addr": "C2", "value": ""}]
        findings, meta = link_lane.analyze_links(cells, [_src_link("L1", 1, 1, 1, 2)], truncated=False)
        self.assertEqual(findings, [])
        self.assertEqual(meta["emptyLinkedBlocks"], 0)

    def test_truncated_scan_never_asserts_empty(self):
        cells = [{"addr": "B2", "calculatedValue": ""}]
        findings, _ = link_lane.analyze_links(cells, [_src_link("L1", 1, 1, 1, 2)], truncated=True)
        self.assertEqual(findings, [])

    def test_marks_linkrole_and_id_without_setting_islinked(self):
        cells = [{"addr": "B2", "calculatedValue": "5"}]
        link_lane.analyze_links(cells, [_src_link("L9", 1, 1, 1, 1)], truncated=False)
        self.assertEqual(cells[0]["linkRole"], "source")
        self.assertEqual(cells[0]["linkId"], "L9")
        # per-cell isLinked stays unset so detectors.detect_blank_linked remains inert
        self.assertNotIn("isLinked", cells[0])

    def test_skips_link_without_range(self):
        findings, meta = link_lane.analyze_links([], [{"id": "L1", "type": "source", "range": None}],
                                                 truncated=False)
        self.assertEqual(findings, [])
        self.assertEqual(meta["linkCount"], 1)

    def test_meta_counts_source_and_destination(self):
        links = [_src_link("L1", 1, 1, 1, 1),
                 {"id": "L2", "type": "destination",
                  "range": {"startRow": 5, "startColumn": 0, "stopRow": 5, "stopColumn": 0}}]
        _, meta = link_lane.analyze_links([], links, truncated=False)
        self.assertEqual(meta["sourceLinks"], 1)
        self.assertEqual(meta["destinationLinks"], 1)

    def test_classify_link_health_source_with_dest_is_healthy(self):
        out = link_lane.classify_link_health(
            [_src_link("S1", 1, 1, 1, 2)],
            {"S1": [{"id": "D1", "table": "TDEST"}]},
        )
        self.assertEqual(out["summary"]["healthy"], 1)
        self.assertEqual(out["links"][0]["status"], "healthy")
        self.assertEqual(out["links"][0]["dest_count"], 1)
        self.assertEqual(out["links"][0]["destinations"][0]["table_id"], "TDEST")

    def test_classify_link_health_source_without_dest_is_no_dest(self):
        out = link_lane.classify_link_health([_src_link("S1", 1, 1, 1, 2)])
        self.assertEqual(out["summary"]["no_dest"], 1)
        self.assertEqual(out["links"][0]["status"], "no_dest")

    def test_classify_link_health_destination_and_orphan_anchor(self):
        out = link_lane.classify_link_health(
            [{"id": "D1", "type": "destination"}, {"id": "X1", "type": "mystery"}],
            anchors=[{"id": "A1", "type": "bookmark", "linked": False}, {"id": "A2", "type": "bookmark", "linked": True}],
        )
        self.assertEqual(out["summary"], {"total": 4, "healthy": 1, "orphaned": 2, "no_dest": 0, "destination": 1})
        self.assertEqual([row["status"] for row in out["links"]], ["destination", "orphaned", "orphaned", "healthy"])

    def test_classify_publish_state_revision_match_and_mismatch(self):
        out = link_lane.classify_publish_state([
            _src_link("L1", 1, 1, 1, 2, revision="rev-a"),
            _dest_link("L1", 1, 1, 1, 2, revision="rev-a"),
            _src_link("L2", 4, 0, 4, 2, revision="rev-new"),
            _dest_link("L2", 4, 0, 4, 2, revision="rev-old"),
            _src_link("L3", 9, 0, 9, 0, revision="rev-source-only"),
        ])
        self.assertEqual(out["summary"], {
            "total": 3,
            "published": 1,
            "unpublished": 1,
            "source_only": 1,
            "destination_only": 0,
            "unknown": 0,
        })
        by_id = {row["id"]: row for row in out["links"]}
        self.assertEqual(by_id["L1"]["status"], "published")
        self.assertEqual(by_id["L2"]["status"], "unpublished")
        self.assertEqual(by_id["L2"]["range"], "A5:C5")
        self.assertEqual(by_id["L3"]["status"], "source_only")

    def test_analyze_links_surfaces_unpublished_revision_mismatch(self):
        cells = [{"addr": "A5", "calculatedValue": "100"}, {"addr": "B5", "calculatedValue": "200"}]
        findings, meta = link_lane.analyze_links(cells, [
            _src_link("L2", 4, 0, 4, 1, revision="rev-new"),
            _dest_link("L2", 4, 0, 4, 1, revision="rev-old"),
        ], truncated=False)
        self.assertEqual(meta["unpublishedLinkedRanges"], 1)
        self.assertEqual(meta["publishState"]["summary"]["unpublished"], 1)
        self.assertEqual(meta["publishStateVocabulary"], ["published", "unpublished", "source_only", "destination_only", "unknown"])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "unpublished-linked-range")
        self.assertEqual(findings[0]["fix_lane"], "surfaced")
        self.assertFalse(findings[0]["fixable"])
        self.assertEqual(findings[0]["addr"], "A5")
        self.assertIn("publish links before export", findings[0]["detail"])

    def test_rc_to_a1(self):
        self.assertEqual(link_lane._rc_to_a1(4, 2), "C5")
        self.assertEqual(link_lane._rc_to_a1(0, 26), "AA1")
        self.assertEqual(link_lane._rc_to_a1(111, 17), "R112")  # matches the live F5:R112 stop cell


class DlSourceNumericFormulaTests(unittest.TestCase):
    """D20: DL source cells with numeric formulas render raw digits in documents."""

    def _source_cell(self, addr, formula, cv, vft="ACCOUNTING", link_id="L1"):
        return {
            "addr": addr,
            "formula": formula,
            "calculatedValue": cv,
            "valueFormatType": vft,
            "linkRole": "source",
            "linkId": link_id,
        }

    def test_no_finding_without_formula(self):
        cell = self._source_cell("B2", None, "1234567")
        self.assertEqual(link_lane.detect_dl_source_numeric_formula([cell]), [])

    def test_no_finding_for_destination_role(self):
        cell = {**self._source_cell("B2", "=SUMIFS(A:A,B:B,1)", "1234567"), "linkRole": "destination"}
        self.assertEqual(link_lane.detect_dl_source_numeric_formula([cell]), [])

    def test_no_finding_for_text_formula(self):
        cell = self._source_cell("B2", '=TEXT(SUMIFS(A:A),"$#,##0")', "1234567")
        self.assertEqual(link_lane.detect_dl_source_numeric_formula([cell]), [])

    def test_no_finding_for_nonnumeric_calculated_value(self):
        cell = self._source_cell("B2", "=A1&\" million\"", "1.2 million")
        self.assertEqual(link_lane.detect_dl_source_numeric_formula([cell]), [])

    def test_no_finding_for_automatic_format(self):
        cell = self._source_cell("B2", "=SUMIFS(A:A,B:B,1)", "1234567", vft="AUTOMATIC")
        self.assertEqual(link_lane.detect_dl_source_numeric_formula([cell]), [])

    def test_no_finding_for_blank_calculated_value(self):
        cell = self._source_cell("B2", "=IF(A1,B1,\"\")", "")
        self.assertEqual(link_lane.detect_dl_source_numeric_formula([cell]), [])

    def test_fires_for_accounting_format(self):
        cell = self._source_cell("C5", "=SUMIFS(A:A,B:B,\"GOV\")", "1234567.89", vft="ACCOUNTING")
        findings = link_lane.detect_dl_source_numeric_formula([cell])
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["kind"], "dl-source-numeric-formula")
        self.assertEqual(f["addr"], "C5")
        self.assertEqual(f["fix_lane"], "surfaced")
        self.assertFalse(f["fixable"])
        self.assertEqual(f["linkId"], "L1")
        self.assertEqual(f["valueFormatType"], "ACCOUNTING")
        self.assertIn("raw digits", f["detail"])
        self.assertIn("TEXT()", f["detail"])
        self.assertIn("1234567.89", f["detail"])

    def test_fires_for_number_format(self):
        cell = self._source_cell("D3", "=SUM(A1:A10)", "55000", vft="NUMBER")
        findings = link_lane.detect_dl_source_numeric_formula([cell])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["valueFormatType"], "NUMBER")

    def test_fires_for_currency_format(self):
        cell = self._source_cell("E4", "=B2+C2", "99.5", vft="CURRENCY")
        findings = link_lane.detect_dl_source_numeric_formula([cell])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["valueFormatType"], "CURRENCY")

    def test_groups_by_link_id(self):
        cells = [
            self._source_cell("B2", "=SUMIFS(A:A,C:C,1)", "100000", link_id="L9"),
            self._source_cell("B3", "=SUMIFS(A:A,C:C,2)", "200000", link_id="L9"),
        ]
        findings = link_lane.detect_dl_source_numeric_formula(cells)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["linkId"], "L9")
        self.assertEqual(findings[0]["affectedCellCount"], 2)
        self.assertIn("2 cells", findings[0]["detail"])

    def test_separate_link_ids_produce_separate_findings(self):
        cells = [
            self._source_cell("B2", "=SUMIFS(A:A)", "100", link_id="LA"),
            self._source_cell("C5", "=SUM(D1:D5)", "200", link_id="LB"),
        ]
        findings = link_lane.detect_dl_source_numeric_formula(cells)
        self.assertEqual(len(findings), 2)
        ids = {f["linkId"] for f in findings}
        self.assertEqual(ids, {"LA", "LB"})

    def test_analyze_links_surfaces_dl_numeric_via_meta_and_findings(self):
        links = [_src_link("L7", 1, 1, 2, 1)]
        cells = [
            {"addr": "B2", "formula": "=SUMIFS(A:A,C:C,1)", "calculatedValue": "500000",
             "valueFormatType": "ACCOUNTING"},
            {"addr": "B3", "formula": "=SUMIFS(A:A,C:C,2)", "calculatedValue": "750000",
             "valueFormatType": "ACCOUNTING"},
        ]
        findings, meta = link_lane.analyze_links(cells, links, truncated=False)
        dl_findings = [f for f in findings if f.get("kind") == "dl-source-numeric-formula"]
        self.assertEqual(len(dl_findings), 1)
        self.assertEqual(dl_findings[0]["linkId"], "L7")
        self.assertEqual(dl_findings[0]["affectedCellCount"], 2)
        self.assertEqual(meta["dlSourceNumericFormulas"], 1)

    def test_text_format_cells_not_flagged(self):
        cell = self._source_cell("B2", "=A1", "2025", vft="TEXT")
        self.assertEqual(link_lane.detect_dl_source_numeric_formula([cell]), [])


class FetchRangeLinksTests(unittest.TestCase):
    _LIVE_SHAPE = {
        "data": [{
            "id": "50639afe", "revision": "2c6438ab", "type": "source", "table": "WA299",
            "source": {"range": {"startColumn": 2, "startRow": 1, "stopColumn": 11, "stopRow": 24}},
        }],
    }

    def test_normalizes_live_source_shape(self):
        with patch.object(wk, "_get", return_value=self._LIVE_SHAPE):
            out = wk.fetch_range_links("tbl", "tok", None)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["type"], "source")
        self.assertEqual(out[0]["range"]["startRow"], 1)
        self.assertEqual(out[0]["range"]["stopColumn"], 11)

    def test_empty_table_id_short_circuits(self):
        self.assertEqual(wk.fetch_range_links("", "tok", None), [])

    def test_fail_open_on_error(self):
        with patch.object(wk, "_get", side_effect=RuntimeError("boom")):
            self.assertEqual(wk.fetch_range_links("tbl", "tok", None), [])

    def test_follows_nextlink_pagination(self):
        # Workiva returns @nextLink as a FULL URL -> fetch routes it through _get_url.
        page1 = {"data": [self._LIVE_SHAPE["data"][0]],
                 "@nextLink": "https://h.app.wdesk.com/s/content/tables/tbl/rangeLinks?page=2"}
        page2 = {"data": [{"id": "p2", "type": "source",
                           "source": {"range": {"startRow": 30, "startColumn": 0,
                                                "stopRow": 31, "stopColumn": 1}}}]}
        with patch.object(wk, "_get", return_value=page1), \
             patch.object(wk, "_get_url", return_value=page2):
            out = wk.fetch_range_links("tbl", "tok", None)
        self.assertEqual([o["id"] for o in out], ["50639afe", "p2"])


class LinkFetchEnabledTests(unittest.TestCase):
    def test_default_on(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WINGMAN_LINK_FETCH", None)
            self.assertTrue(wk.link_fetch_enabled())

    def test_explicit_off(self):
        for val in ("off", "0", "false", "no"):
            with patch.dict(os.environ, {"WINGMAN_LINK_FETCH": val}, clear=False):
                self.assertFalse(wk.link_fetch_enabled(), val)


if __name__ == "__main__":
    unittest.main(verbosity=2)

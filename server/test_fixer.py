#!/usr/bin/env python3
"""Unit tests for fixer negative-parens path (mocked sheetdata — no Workiva creds)."""
from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixer  # noqa: E402


def _fake_jwt(arid_plain="Account\x1f123456789"):
    arid_b64 = base64.urlsafe_b64encode(arid_plain.encode()).decode().rstrip("=")
    claims = {"arid": arid_b64}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.sig"


def _neg_cell(vf=None, display="-1234"):
    vf = vf or {"valueFormatType": "NUMBER", "useParensForNegatives": False}
    return {
        "calculatedValue": display,
        "value": display,
        "effectiveFormats": {"valueFormat": vf},
    }


class FixNegativeParensTests(unittest.TestCase):
    def test_dry_run_planned_accounting(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False}
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(_neg_cell(), True)):
                r = fixer.fix_negative_parens("ss", "sh", "tbl", "B5", "tok", None, confirm=False)
        self.assertEqual(r["status"], "dry-run")
        self.assertIn("minus-prefix", r["beforeFormat"])
        self.assertIn("ACCOUNTING", r["afterFormat"])
        self.assertIn("parens", r["afterFormat"])
        self.assertEqual(r["valueFormat"]["valueFormatType"], "ACCOUNTING")
        self.assertTrue(r["valueFormat"]["useParensForNegatives"])

    def test_refuse_merged(self):
        meta = {"type": "plainText", "merged": True, "linked": False, "formula": False}
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            r = fixer.fix_negative_parens("ss", "sh", "tbl", "B5", "tok", None, confirm=False)
        self.assertEqual(r["status"], "refused")

    def test_no_fix_when_already_parens(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False}
        cell = _neg_cell({"valueFormatType": "ACCOUNTING", "useParensForNegatives": True}, "(1,234)")
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_negative_parens("ss", "sh", "tbl", "B5", "tok", None, confirm=False)
        self.assertEqual(r["status"], "no-fix-needed")

    def test_apply_readback_success(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False}
        before = _neg_cell()
        after_vf = {
            "valueFormatType": "ACCOUNTING",
            "useParensForNegatives": True,
            "showThousandsSeparator": True,
        }
        after = {
            "calculatedValue": "(1,234)",
            "effectiveFormats": {"valueFormat": after_vf},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", side_effect=[(before, True), (after, True)]):
                with patch.object(fixer, "apply_valueformat", return_value=({}, 202)):
                    r = fixer.fix_negative_parens("ss", "sh", "tbl", "B5", "tok", None, confirm=True)
        self.assertEqual(r["status"], "applied")

    def test_vf_matches_requires_accounting_parens(self):
        target = {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True}
        self.assertTrue(fixer._vf_matches(target, {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True}))
        self.assertFalse(fixer._vf_matches(target, {"valueFormatType": "NUMBER", "useParensForNegatives": True}))
        self.assertFalse(fixer._vf_matches(target, {"valueFormatType": "ACCOUNTING", "useParensForNegatives": False}))

    def test_preflight_blocks_text_formula(self):
        self.assertIsNotNone(fixer.preflight_no_text_wrapper('=TEXT(B21/1000,"#,##0")'))
        self.assertIsNone(fixer.preflight_no_text_wrapper("=SUM(A1:A9)"))

    def test_negative_parens_refuses_text_formula(self):
        meta = {
            "type": "formula", "merged": False, "linked": False, "formula": True,
            "formulaText": '=TEXT(B21/1000,"#,##0")',
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            r = fixer.fix_negative_parens("ss", "sh", "tbl", "W21", "tok", None, confirm=False)
        self.assertEqual(r["status"], "refused")
        self.assertIn("TEXT()", r["reason"])

    def test_read_cell_sheetdata_uses_cellrange(self):
        cell = _neg_cell()
        page = {"data": {"range": {"startRow": 104, "startColumn": 2}, "cells": [[cell]]}}
        with patch.object(fixer.wk, "get_sheetdata_cell", return_value=page) as gs:
            got, found = fixer.read_cell_sheetdata("ss", "sh", 104, 2, "tok", None)
        gs.assert_called_once_with("ss", "sh", "C105", "tok", None)
        self.assertTrue(found)
        self.assertEqual(got, cell)

    def test_apply_uses_scan_target_vf(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False}
        before = _neg_cell()
        scan_vf = {
            "valueFormatType": "ACCOUNTING",
            "useParensForNegatives": True,
            "showThousandsSeparator": True,
            "precision": {"auto": False, "value": 0},
        }
        after = {
            "calculatedValue": "(1,234)",
            "effectiveFormats": {"valueFormat": scan_vf},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", side_effect=[(before, True), (after, True)]):
                with patch.object(fixer, "apply_valueformat", return_value=({}, 202)) as av:
                    r = fixer.fix_negative_parens(
                        "ss", "sh", "tbl", "B5", "tok", None, confirm=True, target_vf=scan_vf,
                    )
        self.assertEqual(r["status"], "applied")
        av.assert_called_once()
        self.assertEqual(av.call_args[0][4]["valueFormatType"], "ACCOUNTING")

    def test_junk_decimal_dry_run(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False, "formulaText": None}
        junk_vf = {"valueFormatType": "NUMBER", "precision": {"auto": False, "value": 23}}
        good_vf = {"valueFormatType": "NUMBER", "precision": {"auto": False, "value": 0}, "useParensForNegatives": True}
        target_cell = {
            "calculatedValue": "1234.56",
            "effectiveFormats": {"valueFormat": junk_vf},
        }
        page_cells = [
            {"addr": "B9", "valueFormat": good_vf},
            {"addr": "B10", "valueFormat": junk_vf, "calculatedValue": "1234.56"},
            {"addr": "B11", "valueFormat": good_vf},
        ]
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(target_cell, True)):
                with patch.object(fixer.wk, "get_sheetdata", return_value={"data": {"cells": []}}):
                    with patch.object(fixer.wk, "normalize_sheetdata", return_value=page_cells):
                        r = fixer.fix_junk_decimal("ss", "sh", "tbl", "B10", "tok", None, confirm=False)
        self.assertEqual(r["status"], "dry-run")
        self.assertIn("NUMBER", r["beforeFormat"])
        self.assertEqual(r["valueFormat"]["precision"]["value"], 0)

    def test_junk_decimal_refuses_text_formula(self):
        meta = {
            "type": "formula", "merged": False, "linked": False, "formula": True,
            "formulaText": '=TEXT(B21/1000,"#,##0")',
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            r = fixer.fix_junk_decimal("ss", "sh", "tbl", "W21", "tok", None, confirm=False)
        self.assertEqual(r["status"], "refused")
        self.assertIn("TEXT()", r["reason"])

    def test_missing_thousands_dry_run(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False, "formulaText": None}
        target_vf = {
            "valueFormatType": "ACCOUNTING",
            "showThousandsSeparator": True,
            "precision": {"auto": False, "value": 0},
            "useParensForNegatives": True,
        }
        cell = {
            "calculatedValue": "5000",
            "effectiveFormats": {"valueFormat": {"valueFormatType": "NUMBER", "showThousandsSeparator": False}},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_missing_thousands(
                    "ss", "sh", "tbl", "H10", "tok", None, confirm=False, target_vf=target_vf,
                )
        self.assertEqual(r["status"], "dry-run")
        self.assertTrue(r["valueFormat"]["showThousandsSeparator"])

    def test_number_on_accounting_no_fix_when_already_accounting(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False}
        cell = {
            "calculatedValue": "1000",
            "effectiveFormats": {"valueFormat": {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True}},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_number_on_accounting(
                    "ss", "sh", "tbl", "F5", "tok", None, confirm=False,
                    target_vf={"valueFormatType": "ACCOUNTING", "useParensForNegatives": True},
                )
        self.assertEqual(r["status"], "no-fix-needed")

    def test_prefix_mismatch_dry_run(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False, "formulaText": None}
        target_vf = {
            "valueFormatType": "ACCOUNTING",
            "prefix": "$",
            "useParensForNegatives": True,
            "showThousandsSeparator": True,
            "precision": {"auto": False, "value": 0},
        }
        cell = {
            "calculatedValue": "5000",
            "effectiveFormats": {"valueFormat": {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True}},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_prefix_mismatch(
                    "ss", "sh", "tbl", "J10", "tok", None, confirm=False, target_vf=target_vf,
                )
        self.assertEqual(r["status"], "dry-run")
        self.assertEqual(r["valueFormat"]["prefix"], "$")

    def test_zero_display_mismatch_dry_run(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False, "formulaText": None}
        target_vf = {
            "valueFormatType": "ACCOUNTING",
            "displayZeroAs": "EM DASH",
            "useParensForNegatives": True,
            "precision": {"auto": False, "value": 0},
        }
        cell = {
            "calculatedValue": "0",
            "effectiveFormats": {"valueFormat": {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True}},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_zero_display_mismatch(
                    "ss", "sh", "tbl", "K10", "tok", None, confirm=False, target_vf=target_vf,
                )
        self.assertEqual(r["status"], "dry-run")
        self.assertEqual(r["valueFormat"]["displayZeroAs"], "EM DASH")

    def test_zero_display_refuses_when_already_em_dash(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False, "formulaText": None}
        target_vf = {
            "valueFormatType": "ACCOUNTING",
            "displayZeroAs": "EM DASH",
            "useParensForNegatives": True,
        }
        cell = {
            "calculatedValue": "0",
            "effectiveFormats": {"valueFormat": {
                "valueFormatType": "ACCOUNTING",
                "displayZeroAs": "EM DASH",
                "useParensForNegatives": True,
            }},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_zero_display_mismatch(
                    "ss", "sh", "tbl", "J11", "tok", None, confirm=False, target_vf=target_vf,
                )
        self.assertEqual(r["status"], "no-fix-needed")
        self.assertIn("em-dash", r["reason"].lower())


class CurrencySymbolGuardTests(unittest.TestCase):
    def test_strip_unshown_currency_symbol_removes_hidden_symbol(self):
        vf = {"valueFormatType": "ACCOUNTING", "showCurrencySymbol": False, "currencySymbol": "$"}
        cleaned = fixer.strip_unshown_currency_symbol(vf)
        self.assertNotIn("currencySymbol", cleaned)
        self.assertIn("currencySymbol", vf)  # source object unchanged

    def test_strip_unshown_currency_symbol_keeps_explicit_symbol(self):
        vf = {"valueFormatType": "ACCOUNTING", "showCurrencySymbol": True, "currencySymbol": "$"}
        self.assertEqual(fixer.strip_unshown_currency_symbol(vf)["currencySymbol"], "$")

    def test_format_copy_sanitizes_planned_valueformat(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False, "formulaText": None}
        before = {"calculatedValue": "5000", "effectiveFormats": {"valueFormat": {"valueFormatType": "NUMBER"}}}
        target_vf = {
            "valueFormatType": "ACCOUNTING",
            "showCurrencySymbol": False,
            "currencySymbol": "$",
            "showThousandsSeparator": True,
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(before, True)):
                r = fixer.fix_format_copy("ss", "sh", "tbl", "B5", "tok", None, target_vf=target_vf)
        self.assertEqual(r["status"], "dry-run")
        self.assertNotIn("currencySymbol", r["valueFormat"])

    def test_negative_parens_sanitizes_scan_target_valueformat(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False, "formulaText": None}
        before = _neg_cell()
        target_vf = {
            "valueFormatType": "ACCOUNTING",
            "useParensForNegatives": True,
            "showCurrencySymbol": False,
            "currencySymbol": "$",
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(before, True)):
                r = fixer.fix_negative_parens("ss", "sh", "tbl", "B5", "tok", None, target_vf=target_vf)
        self.assertEqual(r["status"], "dry-run")
        self.assertNotIn("currencySymbol", r["valueFormat"])


class AsyncOperationPollingTests(unittest.TestCase):
    """H3: poll Workiva async 202 operation results before readback."""

    class _Resp:
        def __init__(self, status, body, headers=None):
            self.status = status
            self._body = body
            self.headers = headers or {}

        def read(self):
            return self._body

    def test_operation_url_accepts_relative_location(self):
        with patch.object(fixer.wk, "_base", return_value="https://api.example.test"):
            self.assertEqual(
                fixer._operation_url({"operationLocation": "/platform/v1/operations/op-1"}),
                "https://api.example.test/platform/v1/operations/op-1",
            )

    def test_request_json_polls_202_to_completion(self):
        write_resp = self._Resp(202, b'{"operationLocation":"https://api.example.test/op/1"}')
        poll_resp = self._Resp(200, b'{"status":"completed"}')
        with patch.dict(os.environ, {"WORKIVA_EXPECTED_ARID": "Account/123456789"}, clear=False):
            with patch.object(fixer.wk, "_base", return_value="https://api.example.test"):
                with patch.object(fixer.urllib.request, "urlopen", side_effect=[write_resp, poll_resp]) as u:
                    payload, status = fixer._post("/write", _fake_jwt(), None, {"x": 1})
        self.assertEqual(status, 202)
        self.assertEqual(payload["operationLocation"], "https://api.example.test/op/1")
        self.assertEqual(u.call_count, 2)

    def test_poll_operation_honors_retry_after_and_one_second_floor(self):
        body = {"operationLocation": "https://api.example.test/op/slow"}
        pending = self._Resp(200, b'{"status":"running"}', headers={"Retry-After": "2"})
        done = self._Resp(200, b'{"status":"completed"}')
        with patch.object(fixer.time, "time", side_effect=[0, 0, 1, 3]):
            with patch.object(fixer.time, "sleep") as sleep:
                with patch.object(fixer.urllib.request, "urlopen", side_effect=[pending, done]):
                    fixer._poll_operation(body, "tok", None, timeout_s=10)
        sleep.assert_called_once_with(2.0)

    def test_request_json_raises_on_failed_async_operation(self):
        write_resp = self._Resp(202, b'{"operationLocation":"https://api.example.test/op/2"}')
        poll_resp = self._Resp(200, b'{"status":"failed","error":"validation"}')
        with patch.object(fixer.wk, "_base", return_value="https://api.example.test"):
            with patch.object(fixer.urllib.request, "urlopen", side_effect=[write_resp, poll_resp]):
                with self.assertRaises(RuntimeError):
                    fixer._put("/write", "tok", None, {"x": 1})

    def test_request_json_does_not_poll_when_no_operation_location(self):
        write_resp = self._Resp(202, b'{"status":"accepted"}')
        with patch.dict(os.environ, {"WORKIVA_EXPECTED_ARID": "Account/123456789"}, clear=False):
            with patch.object(fixer.wk, "_base", return_value="https://api.example.test"):
                with patch.object(fixer.urllib.request, "urlopen", return_value=write_resp) as u:
                    payload, status = fixer._post("/write", _fake_jwt(), None, {"x": 1})
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(u.call_count, 1)


class ExpectedAridGateTests(unittest.TestCase):
    """H4: opt-in write-time account/workspace identity gate."""

    def test_token_arid_decodes_account_claim(self):
        self.assertEqual(fixer.token_arid(_fake_jwt()), "Account/123456789")

    def test_expected_arid_allows_matching_token(self):
        with patch.dict(os.environ, {"WORKIVA_EXPECTED_ARID": "Account/123456789"}, clear=False):
            self.assertEqual(fixer.assert_expected_arid(_fake_jwt()), "Account/123456789")

    def test_expected_arid_blocks_mismatch_before_network(self):
        with patch.dict(os.environ, {"WORKIVA_EXPECTED_ARID": "Account/expected"}, clear=False):
            with patch.object(fixer.urllib.request, "urlopen") as u:
                with self.assertRaises(RuntimeError):
                    fixer._post("/write", _fake_jwt("Account\x1factual"), None, {"x": 1})
        u.assert_not_called()

    def test_expected_arid_unset_blocks_before_network(self):
        with patch.dict(os.environ, {"WORKIVA_EXPECTED_ARID": ""}, clear=False):
            with patch.object(fixer.urllib.request, "urlopen") as u:
                with self.assertRaises(RuntimeError):
                    fixer._post("/write", _fake_jwt(), None, {"x": 1})
        u.assert_not_called()


class FixLabelTrimTests(unittest.TestCase):
    _meta = {"type": "plainText", "merged": False, "linked": False, "formula": False}

    def test_dry_run_plans_trim(self):
        # read_effective_fontcolor returns (fontColor, value, cell, found)
        with patch.object(fixer, "read_cell_meta", return_value=self._meta):
            with patch.object(fixer, "read_effective_fontcolor",
                              return_value=(None, "Revenue Summary ", {}, True)):
                r = fixer.fix_label_trim("ss", "sh", "tbl", "A1", "tok", None, confirm=False)
        self.assertEqual(r["status"], "dry-run")
        self.assertEqual(r["before"], "Revenue Summary ")
        self.assertEqual(r["after"], "Revenue Summary")

    def test_apply_readback_success(self):
        # Regression: the readback must compare the cell VALUE, not fontColor. With the old
        # bug (unpacking fontColor into `after`), None != "Revenue Summary" -> mismatch-reverted.
        before = (None, "Revenue Summary ", {}, True)   # pre-write read
        after = (None, "Revenue Summary", {}, True)      # post-write readback: trimmed value landed
        with patch.object(fixer, "read_cell_meta", return_value=self._meta):
            with patch.object(fixer, "read_effective_fontcolor", side_effect=[before, after]):
                with patch.object(fixer, "apply_cell_value", return_value=({}, 202)):
                    with patch.object(fixer.time, "sleep", return_value=None):
                        r = fixer.fix_label_trim("ss", "sh", "tbl", "A1", "tok", None, confirm=True)
        self.assertEqual(r["status"], "applied")
        self.assertEqual(r["readback"], "Revenue Summary")

    def test_apply_reverts_on_real_mismatch(self):
        # If the write truly did not land (readback value still untrimmed), revert and report.
        before = (None, "Revenue Summary ", {}, True)
        after = (None, "Revenue Summary ", {}, True)   # value unchanged -> genuine mismatch
        reverts = []
        with patch.object(fixer, "read_cell_meta", return_value=self._meta):
            with patch.object(fixer, "read_effective_fontcolor", side_effect=[before, after]):
                with patch.object(fixer, "apply_cell_value",
                                  side_effect=lambda *a, **k: reverts.append(a[3]) or ({}, 202)):
                    with patch.object(fixer.time, "sleep", return_value=None):
                        r = fixer.fix_label_trim("ss", "sh", "tbl", "A1", "tok", None, confirm=True)
        self.assertEqual(r["status"], "mismatch-reverted")
        self.assertEqual(reverts, ["Revenue Summary", "Revenue Summary "])  # wrote trim, then reverted

    def test_refuse_richtext(self):
        meta = {"type": "richText", "merged": False, "linked": False, "formula": False}
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            r = fixer.fix_label_trim("ss", "sh", "tbl", "A1", "tok", None, confirm=True)
        self.assertEqual(r["status"], "refused")


class FixYearCoercionTests(unittest.TestCase):
    """S1 / ADR-0009: a 4-digit year under AUTOMATIC → PERIOD valueFormat (reversible)."""
    _meta = {"type": "plainText", "merged": False, "linked": False, "formula": False,
             "formulaText": None}

    def _cell(self, vft="AUTOMATIC", cv="2025"):
        return {"calculatedValue": cv, "value": cv,
                "effectiveFormats": {"valueFormat": {"valueFormatType": vft}}}

    def test_dry_run_plans_period(self):
        with patch.object(fixer, "read_cell_meta", return_value=self._meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(self._cell(), True)):
                r = fixer.fix_year_coercion("ss", "sh", "tbl", "B6", "tok", None, confirm=False)
        self.assertEqual(r["status"], "dry-run")
        self.assertEqual(r["valueFormat"]["valueFormatType"], "PERIOD")

    def test_no_fix_when_already_period(self):
        with patch.object(fixer, "read_cell_meta", return_value=self._meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(self._cell("PERIOD"), True)):
                r = fixer.fix_year_coercion("ss", "sh", "tbl", "B5", "tok", None, confirm=False)
        self.assertEqual(r["status"], "no-fix-needed")

    def test_no_fix_when_not_a_year(self):
        with patch.object(fixer, "read_cell_meta", return_value=self._meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(self._cell(cv="12345"), True)):
                r = fixer.fix_year_coercion("ss", "sh", "tbl", "B7", "tok", None, confirm=False)
        self.assertEqual(r["status"], "no-fix-needed")

    def test_apply_readback_success(self):
        before = self._cell()
        after = {"calculatedValue": "2025",
                 "effectiveFormats": {"valueFormat": {"valueFormatType": "PERIOD"}}}
        with patch.object(fixer, "read_cell_meta", return_value=self._meta):
            with patch.object(fixer, "read_cell_sheetdata", side_effect=[(before, True), (after, True)]):
                with patch.object(fixer, "apply_valueformat", return_value=({}, 202)):
                    r = fixer.fix_year_coercion("ss", "sh", "tbl", "B6", "tok", None, confirm=True)
        self.assertEqual(r["status"], "applied")


class ScaleChangeGuardTests(unittest.TestCase):
    """H1: refuse any valueFormat write that would change enteredIn/shownIn (lossy rescale)."""

    def test_helper_blocks_entered_in_change(self):
        reason = fixer.scale_change_block(
            {"enteredIn": "THOUSANDS"}, {"valueFormatType": "ACCOUNTING", "enteredIn": "ONES"}
        )
        self.assertIsNotNone(reason)
        self.assertIn("enteredIn", reason)

    def test_helper_blocks_shown_in_change(self):
        reason = fixer.scale_change_block({"shownIn": "ONES"}, {"shownIn": "THOUSANDS"})
        self.assertIsNotNone(reason)
        self.assertIn("shownIn", reason)

    def test_helper_allows_when_scale_field_absent_in_planned(self):
        # Planned omits enteredIn -> applyFormats leaves it untouched -> safe.
        self.assertIsNone(
            fixer.scale_change_block({"enteredIn": "THOUSANDS"}, {"prefix": "$"})
        )

    def test_helper_allows_matching_scale(self):
        self.assertIsNone(
            fixer.scale_change_block({"enteredIn": "THOUSANDS"}, {"enteredIn": "THOUSANDS"})
        )

    def test_helper_normalizes_absent_to_ones(self):
        # before has no enteredIn (ONES default), planned sets ONES explicitly -> no change.
        self.assertIsNone(fixer.scale_change_block({}, {"enteredIn": "ONES"}))
        # before ONES default, planned THOUSANDS -> change.
        self.assertIsNotNone(fixer.scale_change_block({}, {"enteredIn": "THOUSANDS"}))

    def test_format_copy_refuses_scale_change(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False,
                "formulaText": None}
        # Neighbor consensus target carries a DIFFERENT enteredIn than the cell -> must refuse.
        target_vf = {"valueFormatType": "ACCOUNTING", "prefix": "$", "enteredIn": "ONES"}
        cell = {
            "calculatedValue": "5000",
            "effectiveFormats": {"valueFormat": {"valueFormatType": "ACCOUNTING",
                                                 "enteredIn": "THOUSANDS"}},
        }
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_prefix_mismatch(
                    "ss", "sh", "tbl", "J10", "tok", None, confirm=False, target_vf=target_vf,
                )
        self.assertEqual(r["status"], "refused")
        self.assertIn("enteredIn", r["reason"])

    def test_negative_parens_refuses_scale_change(self):
        meta = {"type": "plainText", "merged": False, "linked": False, "formula": False}
        before_vf = {"valueFormatType": "NUMBER", "useParensForNegatives": False,
                     "shownIn": "THOUSANDS"}
        scan_vf = {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True,
                   "shownIn": "ONES"}
        cell = _neg_cell(before_vf)
        with patch.object(fixer, "read_cell_meta", return_value=meta):
            with patch.object(fixer, "read_cell_sheetdata", return_value=(cell, True)):
                r = fixer.fix_negative_parens(
                    "ss", "sh", "tbl", "B5", "tok", None, confirm=False, target_vf=scan_vf,
                )
        self.assertEqual(r["status"], "refused")
        self.assertIn("shownIn", r["reason"])


if __name__ == "__main__":
    unittest.main()

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import app


class RoutePathTests(unittest.TestCase):
    def test_trailing_slash_stripped(self):
        self.assertEqual(app._route_path("/api/queue/"), "/api/queue")
        self.assertEqual(app._route_path("/health/"), "/health")

    def test_root_slash_preserved(self):
        self.assertEqual(app._route_path("/"), "/")

    def test_query_not_in_path(self):
        self.assertEqual(app._route_path("/api/queue?x=1"), "/api/queue")


class ApiErrorPayloadTests(unittest.TestCase):
    def test_workiva_404_is_actionable(self):
        payload = app._api_error_payload(urllib.error.HTTPError(
            "https://api.app.wdesk.com/x", 404, "Not Found", {}, None))
        self.assertIn("Workiva returned 404", payload["error"])
        self.assertIn("detail", payload)

    def test_generic_error_passthrough(self):
        payload = app._api_error_payload(RuntimeError("boom"))
        self.assertEqual(payload["error"], "RuntimeError('boom')")


class HandlerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_token = app.WINGMAN_TOKEN
        app.WINGMAN_TOKEN = "wingman-test-token"
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        app.WINGMAN_TOKEN = cls.original_token

    def _request(self, path, *, method="GET", token=None, body=None):
        headers = {}
        if token is not False:
            headers["X-Wingman-Token"] = app.WINGMAN_TOKEN
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health_without_token(self):
        req = urllib.request.Request(self.base + "/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(json.loads(resp.read().decode()), {"ok": True})

    def test_status_alias(self):
        for path in ("/status", "/status/", "/api/status", "/api/status/"):
            code, payload = self._request(path)
            self.assertEqual(code, 200, path)
            self.assertTrue(payload.get("ok"), path)

    def test_status_alias_allows_local_probe_without_token(self):
        code, payload = self._request("/api/status", token=False)
        self.assertEqual(code, 200)
        self.assertTrue(payload.get("ok"))

    def test_connection_requires_correct_token_even_on_loopback(self):
        from unittest.mock import patch

        with patch.object(app, "_token", side_effect=AssertionError("OAuth must not run")) as oauth:
            for headers in ({}, {"X-Wingman-Token": "incorrect-synthetic-token"}):
                request = urllib.request.Request(self.base + "/api/connection", headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(failure.exception.code, 403)
                self.assertEqual(failure.exception.headers["Cache-Control"], "no-store")
            oauth.assert_not_called()

    def test_connection_reports_presence_not_workiva_access_or_secrets(self):
        from unittest.mock import patch

        with patch.object(app, "_token", side_effect=AssertionError("OAuth must not run")) as oauth:
            for client_id, secret, expected in [("fictional-id", "fictional-secret", "present"),
                                                ("fictional-id", "", "missing"), ("", "fictional-secret", "missing")]:
                with patch.dict(app.os.environ, {"WORKIVA_CLIENT_ID": client_id, "WORKIVA_CLIENT_SECRET": secret}):
                    code, data = self._request("/api/connection")
                self.assertEqual(code, 200)
                self.assertEqual(data, {"service": "wingman", "protocol": 1, "readOnly": True,
                                       "authorization": "accepted", "workivaAccess": "not_tested",
                                       "serviceMode": "standard",
                                       "workivaCredentials": expected})
                self.assertNotIn("fictional", json.dumps(data))
            oauth.assert_not_called()
        request = urllib.request.Request(self.base + "/api/connection", headers={"X-Wingman-Token": app.WINGMAN_TOKEN})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_read_only_rejects_post_before_parsing_or_upstream_calls(self):
        from unittest.mock import patch

        with patch.dict(app.os.environ, {"WINGMAN_READ_ONLY": "1"}), \
             patch.object(app, "_token", side_effect=AssertionError("No OAuth")) as oauth, \
             patch.object(app, "_table_id", side_effect=AssertionError("No table lookup")) as table:
            for path in ("/apply", "/apply/", "/fix", "/scan", "/api/queue", "/api/review-packet", "/unknown"):
                request = urllib.request.Request(self.base + path, data=b"not-json",
                                                 headers={"X-Wingman-Token": app.WINGMAN_TOKEN})
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(request, timeout=2)
                self.assertEqual(failure.exception.code, 403, path)
                self.assertEqual(json.load(failure.exception)["code"], "read_only")
            code, denied = self._request("/apply", method="POST", token=False, body={})
            self.assertEqual(code, 403)
            self.assertNotIn("code", denied, "The token gate must still run first")
            oauth.assert_not_called()
            table.assert_not_called()

    def test_read_only_blocks_get_side_effects_and_external_checks(self):
        from unittest.mock import patch

        with patch.dict(app.os.environ, {"WINGMAN_READ_ONLY": "1"}), \
             patch.object(app, "_token", side_effect=AssertionError("No OAuth")) as oauth, \
             patch.object(app, "_run_checks_only", side_effect=AssertionError("No subprocess")) as checks, \
             patch.object(app, "_run_review_packet", side_effect=AssertionError("No export")) as packet:
            for path in ("/api/checks/?spreadsheetId=fictional", "/scan?checks=tieout",
                         "/scan-workbook?checks=hardening_gate", "/api/queue?checks=&checks=tieout",
                         "/api/review-packet?write=true", "/api/review-packet?write=false&write=true",
                         "/api/review-packet?checks=scorecard"):
                code, data = self._request(path)
                self.assertEqual((code, data.get("code")), (403, "read_only"), path)
            oauth.assert_not_called()
            checks.assert_not_called()
            packet.assert_not_called()

    def test_read_only_keeps_authenticated_inspection_and_metadata_reads(self):
        from unittest.mock import patch

        with patch.dict(app.os.environ, {"WINGMAN_READ_ONLY": "1"}), \
             patch.object(app, "_token", return_value="synthetic") as oauth, \
             patch.object(app.inspector, "inspect_cell", return_value={"observed": "fictional"}) as inspect:
            code, data = self._request("/api/connection")
            self.assertEqual((code, data["serviceMode"]), (200, "read-only"))
            oauth.assert_not_called()
            _, config = self._request("/config")
            self.assertIs(config["read_only"], True)
            self.assertEqual(config["safe_fix_kinds"], [])
            _, status = self._request("/api/status")
            self.assertIs(status["features"]["read_only"], True)
            self.assertIs(status["features"]["checks_adapter"], False)
            self.assertIs(status["features"]["vision"], False)
            code, data = self._request("/api/inspect?spreadsheetId=abc&sheetId=def&addr=C12&sources=true")
            self.assertEqual((code, data), (200, {"observed": "fictional"}))
            inspect.assert_called_once_with("abc", "def", "C12", "synthetic", app._ctx, include_sources=True)

    def test_read_only_flag_fails_closed_on_typo_without_changing_standard_mode(self):
        from unittest.mock import patch

        for value, enabled in [("1", True), ("true", True), ("treu", True), ("", False), ("0", False), ("false", False)]:
            with patch.dict(app.os.environ, {"WINGMAN_READ_ONLY": value}):
                self.assertEqual(app.wingman_config.read_only_enabled(), enabled)
                self.assertEqual(bool(app.wingman_config.service_config()["safe_fix_kinds"]), not enabled)

    def test_status_operator_config_shape_no_secret_values(self):
        code, payload = self._request("/api/status", token=False)
        self.assertEqual(code, 200)
        oc = payload.get("operator_config")
        self.assertIsNotNone(oc, "operator_config missing from /api/status")
        for key in ("wingman_token", "extension_origin", "workiva_client_id", "workiva_client_secret"):
            self.assertIn(key, oc, f"operator_config missing field: {key}")
        values_text = str(list(oc.values()))
        for forbidden in ("Bearer ",):
            self.assertNotIn(forbidden, values_text, f"operator_config must not expose credential values: {forbidden}")
        for field in ("workiva_client_id", "workiva_client_secret"):
            self.assertIn(oc.get(field), ("present", "missing"), f"{field} must be present/missing not a value")
        self.assertIn(oc["wingman_token"], ("configured", "missing"))
        self.assertIn(oc["extension_origin"], ("custom", "default-packaged"))
        self.assertIsInstance(oc.get("warnings"), list)

    def test_root_operator_config_present(self):
        code, payload = self._request("/", token=False)
        self.assertEqual(code, 200)
        self.assertIn("operator_config", payload, "operator_config missing from / route")

    def test_digest_allows_local_probe_without_token(self):
        code, payload = self._request("/digest", token=False)
        self.assertEqual(code, 200)
        self.assertIn("scanCount", payload)

    def test_root_allows_local_probe_without_token(self):
        code, payload = self._request("/", token=False)
        self.assertEqual(code, 200)
        self.assertEqual(payload.get("service"), "wingman")
        self.assertIn("guarded_endpoints", payload)

    def test_queue_still_requires_token(self):
        code, payload = self._request("/api/queue?spreadsheetId=x", token=False)
        self.assertEqual(code, 403)
        self.assertIn("not authorized", payload.get("error", ""))

    def test_inspect_requires_token_and_validates_before_oauth(self):
        from unittest.mock import patch

        with patch.object(app, "_token", side_effect=AssertionError("OAuth must not run")) as token:
            code, _ = self._request("/api/inspect?spreadsheetId=x&sheetId=y&addr=B7", token=False)
            self.assertEqual(code, 403)
            for query in ("spreadsheetId=x&sheetId=y&addr=B7:C8", "spreadsheetId=x&sheetId=y&addr=B0",
                          "spreadsheetId=../x&sheetId=y&addr=B7", "spreadsheetId=x&addr=B7",
                          "spreadsheetId=x&sheetId=y&addr=B7&sources=all",
                          "spreadsheetId=x&sheetId=y&addr=B7&sources=true&sources=false"):
                code, _ = self._request("/api/inspect?" + query)
                self.assertEqual(code, 400, query)
            token.assert_not_called()

    def test_source_inspection_requires_token_and_exact_revision_target(self):
        from unittest.mock import patch

        path = "/api/inspect-source?tableId=source&revision=rev-7&addr=C12"
        with patch.object(app, "_token", side_effect=AssertionError("OAuth must not run")) as token:
            self.assertEqual(self._request(path, token=False)[0], 403)
            for query in ("tableId=x&addr=C12", "tableId=x&revision=&addr=C12",
                          "tableId=x&revision=r&addr=C12:D13", "tableId=x&revision=r&addr=C0",
                          "tableId=x&revision=r&addr=C12&revision=s", "tableId=x&revision=r&addr=C12&spreadsheetId=y",
                          "tableId=x&revision=%0A&addr=C12"):
                self.assertEqual(self._request("/api/inspect-source?" + query)[0], 400, query)
            token.assert_not_called()
        with patch.object(app, "_token", return_value="synthetic"), \
             patch.object(app.wingman_config, "read_only_enabled", return_value=True), \
             patch.object(app.inspector, "inspect_source", return_value={"readOnly": True}) as inspect:
            self.assertEqual(self._request(path), (200, {"readOnly": True}))
            inspect.assert_called_once_with("source", "rev-7", "C12", "synthetic", app._ctx)

    def test_guarded_route_rejects_when_service_token_missing(self):
        original = app.WINGMAN_TOKEN
        app.WINGMAN_TOKEN = ""
        try:
            code, payload = self._request("/api/queue?spreadsheetId=x")
        finally:
            app.WINGMAN_TOKEN = original
        self.assertEqual(code, 403)
        self.assertIn("not authorized", payload.get("error", ""))

    def test_trailing_slash_queue_route(self):
        code, _payload = self._request("/api/queue/?spreadsheetId=x")
        self.assertNotEqual(code, 404)

    def test_unknown_route_404(self):
        code, payload = self._request("/nope")
        self.assertEqual(code, 404)
        self.assertEqual(payload.get("error"), "not found")

    def test_digest_route(self):
        import os
        import tempfile
        import wingman_log
        with tempfile.TemporaryDirectory() as td:
            os.environ["WINGMAN_LOG_DIR"] = td
            try:
                wingman_log.log_event({"event": "scan", "findingCount": 2, "byKind": {"low-contrast": 2}})
                code, payload = self._request("/digest")
                self.assertEqual(code, 200)
                self.assertEqual(payload.get("scanCount"), 1)
                self.assertIn("findingsByKind", payload)
                self.assertIn("flags", payload)
            finally:
                os.environ.pop("WINGMAN_LOG_DIR", None)

    def test_review_packet_requires_spreadsheet_id(self):
        code, payload = self._request("/api/review-packet")
        self.assertEqual(code, 400)
        self.assertIn("spreadsheetId", payload.get("error", ""))

    def test_review_packet_route_builds_packet(self):
        # Monkeypatch the read-only scan path so the route never touches Workiva.
        import diagnose
        fake_queue = diagnose.build_sheet_queue(
            [
                {"kind": "junk-decimal", "severity": "medium", "signature": "5 dp",
                 "fixable": True, "fix_lane": "safe-auto", "addrs": ["B7"], "count": 1},
                {"kind": "broken-ref", "severity": "high", "signature": "result is #REF!",
                 "fixable": False, "fix_lane": "surfaced", "addrs": ["E1"], "count": 1},
            ],
            spreadsheet_id="ss-secret-xyz", sheet_id="sh1", sheet_name="TB",
        )
        orig = app._run_sheet_queue
        app._run_sheet_queue = lambda ss, sh, **kw: fake_queue
        try:
            code, payload = self._request(
                "/api/review-packet?spreadsheetId=ss-secret-xyz&sheetId=sh1&label=ACFR")
        finally:
            app._run_sheet_queue = orig
        self.assertEqual(code, 200)
        self.assertIn("packet", payload)
        self.assertIn("markdown", payload)
        packet = payload["packet"]
        self.assertEqual(packet["overall"], "BLOCKED")
        self.assertEqual(packet["label"], "ACFR")
        self.assertIs(packet["clientReady"], False)
        self.assertNotIn("ss-secret-xyz", __import__("json").dumps(payload))


class OriginAndApplySafetyTests(unittest.TestCase):
    def test_extension_origin_requires_exact_allowlisted_id(self):
        old = app.ALLOWED_EXT_IDS
        try:
            app.ALLOWED_EXT_IDS = {"goodextensionid"}
            self.assertTrue(app._origin_ok("chrome-extension://goodextensionid"))
            self.assertFalse(app._origin_ok("chrome-extension://evil-extension"))
        finally:
            app.ALLOWED_EXT_IDS = old

    def test_apply_safety_allows_dummy_and_blocks_source(self):
        import os
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as td:
            safety = f"{td}/safety.json"
            dummy = f"{td}/dummy_ids.json"
            with open(safety, "w", encoding="utf-8") as fh:
                json.dump({"protected_source_ids": {"spreadsheet": "source-ss"}}, fh)
            with open(dummy, "w", encoding="utf-8") as fh:
                json.dump({"dummy_ids": [{"id": "dummy-ss", "kind": "Spreadsheet"}]}, fh)
            env = {"WINGMAN_SAFETY_JSON": safety, "WINGMAN_DUMMY_IDS_JSON": dummy, "WINGMAN_APPLY_ALLOWLIST": ""}
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(app._assert_apply_safety("dummy-ss"), "dummy-allowlisted")
                with self.assertRaises(RuntimeError):
                    app._assert_apply_safety("source-ss")
                with self.assertRaises(RuntimeError):
                    app._assert_apply_safety("other-ss")

    def test_apply_safety_missing_config_fails_closed(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"WINGMAN_SAFETY_JSON": "/tmp/wingman-no-such-safety.json", "WINGMAN_APPLY_ALLOWLIST": "dummy"}, clear=False):
            with self.assertRaises(RuntimeError):
                app._assert_apply_safety("dummy")


if __name__ == "__main__":
    unittest.main()

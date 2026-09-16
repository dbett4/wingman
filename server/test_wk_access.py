"""Private credentialed-read boundary: no real credentials or Workiva calls."""
import base64
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import app
import wk_client as wk


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    for key in ("WORKIVA_CREDENTIALS_FILE", "WORKIVA_READ_SCOPE_FILE", "WORKIVA_CLIENT_ID",
                "WORKIVA_CLIENT_SECRET", "WORKIVA_EXPECTED_ARID", "WINGMAN_READ_ONLY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app, "_tok", {"value": None, "ts": 0})


def test_credential_file_is_authoritative_and_status_does_not_authenticate(monkeypatch, tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"WORKIVA_CLIENT_ID": "file-id", "WORKIVA_CLIENT_SECRET": "file-secret"}))
    monkeypatch.setenv("WORKIVA_CREDENTIALS_FILE", str(path))
    monkeypatch.setenv("WORKIVA_CLIENT_ID", "wrong-workspace-id")
    monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "wrong-workspace-secret")
    monkeypatch.setattr(wk, "get_token", lambda *_: pytest.fail("Status must not authenticate"))
    assert wk._resolve_credentials() == ("file-id", "file-secret")
    assert wk.credentials_present() is True
    status = app._operator_config_status()
    assert status["workiva_client_id"] == status["workiva_client_secret"] == "present"
    assert "file-secret" not in json.dumps(status)
    path.unlink()
    assert wk.credentials_present() is False
    with pytest.raises(wk.AuthError, match="configured Workiva credential file"):
        wk._resolve_credentials()


@pytest.mark.parametrize("content", ["not-json", "[]", "null", '{}',
    '{"WORKIVA_CLIENT_ID":7,"WORKIVA_CLIENT_SECRET":"secret"}',
    '{"WORKIVA_CLIENT_ID":"id","WORKIVA_CLIENT_SECRET":" "}'])
def test_invalid_file_cannot_fall_back_to_env_or_dotenv(monkeypatch, tmp_path, content):
    path = tmp_path / "credentials.json"
    path.write_text(content)
    (tmp_path / ".env").write_text("WORKIVA_CLIENT_ID=dotenv-id\nWORKIVA_CLIENT_SECRET=dotenv-secret\n")
    monkeypatch.setenv("WORKIVA_CREDENTIALS_FILE", str(path))
    monkeypatch.setenv("WORKIVA_CLIENT_ID", "environment-id")
    monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "environment-secret")
    with pytest.raises(wk.AuthError) as exc:
        wk._resolve_credentials()
    assert "secret" not in str(exc.value)
    assert wk.credentials_present() is False


def test_unconfigured_file_preserves_env_and_local_dotenv(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("WORKIVA_CLIENT_ID=local-id\nWORKIVA_CLIENT_SECRET=local-secret\n")
    assert wk._resolve_credentials() == ("local-id", "local-secret")
    monkeypatch.setenv("WORKIVA_CLIENT_ID", "env-id")
    monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "env-secret")
    assert wk._resolve_credentials() == ("env-id", "env-secret")


@pytest.fixture
def read_scope(monkeypatch, tmp_path):
    path = tmp_path / "scope.json"
    path.write_text(json.dumps({"documents": ["old-doc"], "spreadsheets": ["old-book"],
                               "tables": ["old-table"], "destinationLinks": ["old-link"]}))
    monkeypatch.setenv("WORKIVA_READ_SCOPE_FILE", str(path))
    monkeypatch.setenv("WORKIVA_EXPECTED_ARID", "Account/123")
    return path


@pytest.mark.parametrize("path", ["/documents/old-doc/tables", "/documents/old-doc/sections/a?$revision=old",
    "/spreadsheets/old-book/sheets", "/platform/v1/spreadsheets/old-book/sheets/a/sheetdata",
    "/content/tables/old-table/cells?$region=A1", "/content/destinationLinks/old-link?$revision=old"])
def test_scope_accepts_only_exact_resource_ids(read_scope, path):
    wk._assert_read_scope(wk._base() + path)


@pytest.mark.parametrize("path", ["/documents/active-doc/tables", "/documents/old-doc-other/tables",
    "/documents", "/platform/v1/spreadsheets", "/spreadsheets/new-book/sheets",
    "/content/tables/unapproved-source/cells", "/content/sourceLinks/old-link",
    "/content/destinationLinks/unknown", "/content/tables/old-table/../other/cells",
    "/content/tables/old-table/%2e%2e/other/cells", "/content/tables/old-table/%252e%252e/other/cells",
    "/documents/old-doc%2f..%2factive-doc/tables", "/documents/old-doc/..\\active-doc",
    "https://other.invalid/documents/old-doc/tables", "http://api.app.wdesk.com/documents/old-doc/tables",
    "https://user@api.app.wdesk.com/documents/old-doc/tables", "/documents/old-doc/tables#ignored"])
def test_scope_denies_before_any_transport(monkeypatch, read_scope, path):
    monkeypatch.setattr(wk, "_open", lambda *_: pytest.fail("No transport outside scope"))
    with pytest.raises(wk.ReadScopeError):
        wk._get_url(path, "fictional-token", None)


@pytest.mark.parametrize("content", ["{}", "[]", "null", "not-json", '{"documents":"old-doc"}',
                                    '{"documents":["old-doc"],"unknown":[]}'])
def test_invalid_or_empty_scope_is_not_unrestricted(read_scope, content):
    read_scope.write_text(content)
    with pytest.raises(wk.ReadScopeError):
        wk._assert_read_scope(wk._base() + "/documents/old-doc/tables")
    read_scope.unlink()
    with pytest.raises(wk.ReadScopeError):
        wk._assert_read_scope(wk._base() + "/documents/old-doc/tables")


def token(account):
    arid = base64.urlsafe_b64encode(f"Account\x1f{account}".encode()).decode().rstrip("=")
    claims = base64.urlsafe_b64encode(json.dumps({"arid": arid}).encode()).decode().rstrip("=")
    return f"header.{claims}.signature"


def test_workspace_mismatch_never_enters_token_cache(monkeypatch, read_scope):
    monkeypatch.setenv("WORKIVA_EXPECTED_ARID", "Account/123")
    for candidate in (token("456"), "invalid", "header.e30.signature"):
        monkeypatch.setattr(wk, "get_token", lambda *_: candidate)
        with pytest.raises(wk.AuthError, match="configured workspace"):
            app._token()
        assert app._tok == {"value": None, "ts": 0}
    monkeypatch.setattr(wk, "get_token", lambda *_: token("123"))
    assert app._token() == token("123")
    monkeypatch.setattr(wk, "get_token", lambda *_: pytest.fail("Reuse the verified token"))
    assert app._token() == token("123")
    monkeypatch.setenv("WORKIVA_EXPECTED_ARID", "Account/456")
    with pytest.raises(wk.AuthError, match="configured workspace"):
        app._token()


def test_scoped_service_requires_expected_workspace_before_oauth(monkeypatch, read_scope):
    monkeypatch.delenv("WORKIVA_EXPECTED_ARID")
    monkeypatch.setattr(wk, "get_token", lambda *_: pytest.fail("No OAuth without workspace pin"))
    with pytest.raises(wk.AuthError, match="require WORKIVA_EXPECTED_ARID"):
        app._token()


@pytest.mark.parametrize("private_mode", ["file", "read-only-env"])
@pytest.mark.parametrize("missing", ["scope", "account", "both", "unreadable", "empty"])
def test_incomplete_commissioning_blocks_oauth_and_get(monkeypatch, read_scope, private_mode, missing):
    if private_mode == "file":
        monkeypatch.setenv("WORKIVA_CREDENTIALS_FILE", "/never-read-in-this-test")
    else:
        monkeypatch.setenv("WINGMAN_READ_ONLY", "1")
        monkeypatch.setenv("WORKIVA_CLIENT_ID", "fictional-id")
        monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "fictional-secret")
    if missing in ("scope", "both"):
        monkeypatch.setenv("WORKIVA_READ_SCOP_FILE", str(read_scope))  # planted operator typo
        monkeypatch.delenv("WORKIVA_READ_SCOPE_FILE")
    if missing in ("account", "both"):
        monkeypatch.setenv("WORKIVA_EXPECTED_ARD", "Account/123")
        monkeypatch.delenv("WORKIVA_EXPECTED_ARID")
    if missing == "unreadable":
        read_scope.unlink()
    if missing == "empty":
        read_scope.write_text("{}")
    monkeypatch.setattr(wk, "_open", lambda *_: pytest.fail("No network for incomplete commissioning"))
    for call in (wk.get_token, app._token,
                 lambda: wk._get("/documents/old-doc/tables", "fictional-token", None)):
        with pytest.raises((wk.ReadScopeError, wk.AuthError)):
            call()
    # Credential presence alone cannot satisfy the commissioning contract.
    assert wk.read_access_status() != {"workivaReadScope": "valid", "workivaAccountPin": "present"}


@pytest.mark.parametrize("missing", [None, "scope", "account"])
def test_commissioning_checker_uses_real_connection_contract(monkeypatch, read_scope, missing):
    spec = importlib.util.spec_from_file_location(
        "wingman_connection_checker", Path(__file__).resolve().parents[1] / "deploy" / "check_connection.py")
    check_connection = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check_connection)

    monkeypatch.setenv("WINGMAN_READ_ONLY", "1")
    monkeypatch.setenv("WORKIVA_CLIENT_ID", "fictional-id")
    monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "fictional-secret")
    monkeypatch.setattr(app, "WINGMAN_TOKEN", "fictional-pair-for-connection-checker")
    monkeypatch.setattr(app, "_token", lambda: pytest.fail("Commissioning check must not call OAuth"))
    if missing == "scope":
        monkeypatch.delenv("WORKIVA_READ_SCOPE_FILE")
    if missing == "account":
        monkeypatch.delenv("WORKIVA_EXPECTED_ARID")
    connection = check_connection.http.client.HTTPConnection
    with ThreadingHTTPServer(("127.0.0.1", 0), app.Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(check_connection.http.client, "HTTPConnection",
                            lambda host, port, timeout: connection(host, server.server_port, timeout=timeout))
        try:
            if missing:
                with pytest.raises(ValueError, match="Connection contract"):
                    check_connection.probe(app.WINGMAN_TOKEN, "present")
            else:
                result = check_connection.probe(app.WINGMAN_TOKEN, "present")
                assert result["read_scope"] == "valid" and result["account_pin"] == "present"
                assert result["repairs_and_exports"] == "rejected"
        finally:
            server.shutdown()
            thread.join()


def test_real_http_redirect_cannot_escape_scope_or_forward_oauth(monkeypatch, read_scope):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.command, self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/documents/active-doc/tables")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(wk, "_base", lambda: f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("WORKIVA_CLIENT_ID", "fictional-id")
        monkeypatch.setenv("WORKIVA_CLIENT_SECRET", "fictional-secret")
        try:
            with pytest.raises(wk.urllib.error.HTTPError) as failure:
                wk._get("/documents/old-doc/tables", "fictional-token", None)
            assert failure.value.code == 302
            with pytest.raises(wk.urllib.error.HTTPError):
                wk.get_token()
            assert received == [("GET", "/documents/old-doc/tables", "Bearer fictional-token"),
                                ("POST", wk.AUTH_PATH, None)]
        finally:
            server.shutdown()
            thread.join()

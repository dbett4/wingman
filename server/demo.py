#!/usr/bin/env python3
"""Credential-free demo: real app/extension, session-isolated simulated Workiva HTTP API.

Run as a SEPARATE process: python3 server/demo.py [--port 8771]. This entry point
replaces the Workiva region map and token provider in this process only. It never
loads .env, resolves OAuth credentials, or enables external check runners.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import mimetypes
import os
import re
import secrets
import signal
import sys
import tempfile
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.request

import app
import fixer
import wk_client as wk

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "server/fixtures/riverton.json").read_text())
WORKBOOK = FIXTURE["spreadsheetId"]
CURRENT_SESSION = ContextVar("demo_session")
COOKIE = "wingman_demo"
SESSION_TTL = 3600


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def demo_token():
    # Synthetic identity only. It satisfies the real fixer's account gate, but
    # cannot authenticate to Workiva. The upstream simulator also checks session existence.
    claims = {"arid": encode("Account\x1fdemo"), "demoSession": CURRENT_SESSION.get()}
    return "demo." + encode(json.dumps(claims)) + ".not-a-signature"


class Workbook:
    def __init__(self):
        self.lock = threading.RLock()
        self.touched = time.monotonic()
        self.reset()

    def reset(self):
        self.sheets = copy.deepcopy(FIXTURE["sheets"])
        self.events = []
        self.failure = False
        self.request_count = 0
        for sheet in self.sheets:
            sheet["cells"] = []
            for ri, row in enumerate(sheet.pop("rows")):
                cells = []
                for ci, value in enumerate(row):
                    addr = wk.a1(ri, ci)
                    formats = {
                        "textFormat": {"fontColor": "#20242C"},
                        "cellFormat": {"backgroundColor": "#FFFFFF"},
                    }
                    if isinstance(value, (int, float)):
                        formats["valueFormat"] = {
                            "valueFormatType": "ACCOUNTING", "useParensForNegatives": True,
                            "showThousandsSeparator": True, "precision": {"auto": False, "value": 0},
                        }
                    formats.update(sheet["formats"].get(addr, {}))
                    cells.append({"value": value, "calculatedValue": value, "effectiveFormats": formats})
                sheet["cells"].append(cells)

    def sheet(self, sid):
        return next(s for s in self.sheets if s["id"] == sid)

    def state(self):
        return {"name": FIXTURE["name"], "spreadsheetId": WORKBOOK,
                "notice": FIXTURE["notice"], "sheets": self.sheets,
                "events": self.events[-20:], "failureArmed": self.failure,
                "requestCount": self.request_count, "generatedAt": timestamp()}

    def request(self, method, raw_path, body):
        """Implement only the API subset exercised by this fixture; fail closed otherwise."""
        self.request_count += 1
        url = urlparse(raw_path)
        path, query = url.path, parse_qs(url.query)
        current_revision = "demo-current-" + str(len(self.events))
        if method == "GET" and path == f"/spreadsheets/{WORKBOOK}/sheets":
            if query.get("$revision", [current_revision]) != [current_revision]:
                raise ValueError("Revision is not part of this simulation")
            return {"data": [{"id": s["id"], "name": s["name"], "table": {"table": s["id"], "revision": current_revision}}
                             for s in self.sheets]}
        if method == "GET":
            for link in FIXTURE["publishedRangeLinks"]:
                if (path == f"/content/tables/{link['table']}/rangeLinks/{link['id']}"
                        and query.get("$revision") == [link["revision"]]):
                    return copy.deepcopy(link)
            for link in FIXTURE["cellDestinationLinks"]:
                if (path == f"/content/destinationLinks/{link['id']}"
                        and query.get("$revision") == [link["revision"]]):
                    return copy.deepcopy(link)
            for anchor in FIXTURE["sourceAnchors"]:
                if (path == f"/content/tables/{anchor['content']['table']}/anchors/{anchor['id']}"
                        and query.get("$revision") == [anchor["revision"]]):
                    return copy.deepcopy(anchor)
        content = re.fullmatch(r"/content/tables/(de0[12]|demo-notes-table)/(cells|rangeLinks|properties)", path)
        if method == "GET" and content:
            published = FIXTURE["publishedTables"].get(content[1])
            sheet = published or self.sheet(content[1])
            revision = published["revision"] if published else current_revision
            if query.get("$revision", [revision]) != [revision]:
                raise ValueError("Revision is not part of this simulation")
            if content[2] == "properties":
                return {"id": content[1], "name": sheet["name"], "revision": revision}
            if content[2] == "rangeLinks":
                return {"data": copy.deepcopy(sheet["rangeLinks"])}
            r0, r1, c0, c1 = [int(query[k][0]) for k in ("startRow", "stopRow", "startColumn", "stopColumn")]
            result = []
            for ri in range(r0, r1 + 1):
                row = []
                for ci in range(c0, c1 + 1):
                    if published:
                        pr, pc = ri - sheet["startRow"], ci - sheet["startColumn"]
                        if pr < 0 or pc < 0:
                            raise ValueError("Outside published range")
                        value = sheet["rows"][pr][pc]
                        formula = None
                    else:
                        value = sheet["cells"][ri][ci]["value"]
                        formula = sheet["formulas"].get(wk.a1(ri, ci))
                    link_ref = sheet.get("cellLinks", {}).get(wk.a1(ri, ci))
                    row.append({"rawValue": formula or str(value), "value": {
                        "type": "formula", "formula": {"calculatedValue": str(value), "effectiveValue": str(value)}}
                        if formula else {"type": "destinationLink", "destinationLink": {"destinationLink": link_ref, "paragraphs": []}}
                        if link_ref else {"type": "plainText", "plainText": {"effectiveValue": str(value)}}})
                result.append({"cells": row})
            return {"data": result, "revision": revision,
                    "range": dict(zip(("startRow", "stopRow", "startColumn", "stopColumn"), (r0, r1, c0, c1)))}
        match = re.fullmatch(rf"/platform/v1/spreadsheets/{WORKBOOK}/sheets/(de0[12])/(sheetdata|update|values/([A-Z]+[1-9][0-9]*))", path)
        if not match:
            raise ValueError("This endpoint or workbook is not part of the simulation")
        sheet = self.sheet(match[1])
        if method == "GET" and match[2] == "sheetdata":
            if "$cellrange" in query:
                addr = query["$cellrange"][0].split(":")[0]
                ri, ci = fixer.addr_to_rc(addr)
                cells = [[sheet["cells"][ri][ci]]]
            else:
                # Two pages exercise the real pagination/absolute-coordinate path.
                ri, ci = int(query.get("page", ["0"])[0]), 0
                cells = sheet["cells"][ri:ri + 5]
            data = {"data": {"range": {"startRow": ri, "startColumn": ci}, "cells": copy.deepcopy(cells)}}
            if "$cellrange" not in query and ri + 5 < len(sheet["cells"]):
                data["@nextLink"] = path + "?page=" + str(ri + 5)
            return data
        if method == "PUT" and match[3]:
            addr = match[3]
            ri, ci = fixer.addr_to_rc(addr)
            cell = sheet["cells"][ri][ci]
            before = cell["value"]
            value = body["values"][0][0]
            # A single deliberately wrong write lets the real readback/revert path run.
            failed = self.failure
            self.failure = False
            cell["value"] = cell["calculatedValue"] = "SIMULATED WRITE MISMATCH" if failed else value
            self.record(addr, "value", before, cell["value"], failed)
            return {}
        if method == "POST" and match[2] == "update":
            fmt = body["applyFormats"]["formats"][0]
            bounds = fmt["ranges"][0]
            ri, ci = bounds["startRow"], bounds["startColumn"]
            cell = sheet["cells"][ri][ci]
            key = "valueFormat" if "valueFormat" in fmt else "textFormat"
            before = copy.deepcopy(cell["effectiveFormats"].get(key, {}))
            failed = self.failure
            self.failure = False
            cell["effectiveFormats"][key] = copy.deepcopy(fmt[key])
            if failed:
                cell["effectiveFormats"][key] = ({"valueFormatType": "NUMBER"} if key == "valueFormat"
                                                  else {"fontColor": "#FFFFFF"})
            self.record(wk.a1(ri, ci), key, before, cell["effectiveFormats"][key], failed)
            return {}
        raise ValueError("Unsupported simulated operation")

    def record(self, addr, field, before, after, failed):
        self.events.append({"at": timestamp(), "addr": addr, "field": field,
                            "before": before, "after": copy.deepcopy(after), "injectedMismatch": failed})
        self.events = self.events[-20:]


class Sessions:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def get(self, sid=None, *, create=False):
        with self.lock:
            now = time.monotonic()
            self.items = {k: v for k, v in self.items.items() if now - v.touched < SESSION_TTL}
            if sid not in self.items:
                if not create:
                    raise ValueError("Demo session expired; reload the page")
                if len(self.items) >= 128:
                    raise ValueError("Demo is at capacity; try again later")
                sid = secrets.token_urlsafe(24)
                self.items[sid] = Workbook()
            book = self.items[sid]
            book.touched = now
            return sid, book


class UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_request(self):
        try:
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            sid = fixer._b64url_json(token.split(".")[1])["demoSession"]
            _, book = self.server.sessions.get(sid)
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size)) if size else None
            # Outer app handler owns this session's lock; this upstream request
            # runs on another thread, so must not acquire that same lock.
            result = book.request(self.command, self.path, body)
            code = 200
        except (ValueError, KeyError, IndexError, StopIteration):
            result, code = {"error": "Unsupported simulated Workiva request"}, 400
        raw = json.dumps(result).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PUT = handle_request


ASSETS = {"/": "demo/index.html", "/demo/demo.css": "demo/demo.css", "/demo/demo.js": "demo/demo.js",
          "/wingman-inspector.js": "extension/wingman-inspector.js",
          "/wingman-connection.js": "extension/wingman-connection.js",
          "/wingman-core.js": "extension/wingman-core.js", "/wingman-panel.js": "extension/wingman-panel.js",
          "/icons/icon128.png": "extension/icons/icon128.png"}
GET_ROUTES = {"/config", "/api/inspect", "/api/queue", "/api/review-packet"}
POST_ROUTES = {"/fix", "/apply", "/demo/reset", "/demo/failure"}


class DemoHandler(app.Handler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def _cors(self, origin):
        pass  # No cross-origin access, unlike the live extension service.

    def _authorized(self):
        # Only this demo's same-origin browser adapter sends this header.
        # Cross-origin JavaScript cannot send it without a preflight, which we deny.
        return self.headers.get("X-Wingman-Demo") == "1"

    def do_OPTIONS(self):
        self._send(403, {"error": "Cross-origin demo requests are disabled"})

    def dispatch(self):
        path = urlparse(self.path).path
        if path == "/health" and self.command == "GET":
            self._send(200, {"ok": True, "simulation": True})
            return
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            cookie = cookies.get(COOKIE)
            sid, book = self.server.sessions.get(cookie.value if cookie else None,
                                                 create=self.command == "GET" and path == "/")
        except (ValueError, CookieError) as exc:
            self.close_connection = True
            self._send(403, {"error": str(exc)})
            return
        if self.command == "GET" and path in ASSETS:
            raw = (ROOT / ASSETS[path]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(ASSETS[path])[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            if path == "/":
                # Partitioned HTTPS cookies also work inside Amp's cross-site Portal iframe.
                policy = ("SameSite=None; Secure; Partitioned" if self.headers.get("X-Forwarded-Proto") == "https"
                          else "SameSite=Strict")
                self.send_header("Set-Cookie", f"{COOKIE}={sid}; HttpOnly; {policy}; Path=/; Max-Age={SESSION_TTL}")
            self.end_headers()
            self.wfile.write(raw)
            return
        if not self._guard():
            self.close_connection = True
            return
        query = parse_qs(urlparse(self.path).query)
        if "checks" in query or "write" in query:
            self.close_connection = True
            self._send(400, {"error": "External checks and server-side packet persistence are disabled in this demo"})
            return
        token = CURRENT_SESSION.set(sid)
        try:
            with book.lock:
                if self.command == "GET" and path == "/demo/state":
                    self._send(200, book.state())
                elif self.command == "GET" and path == "/api/connection":
                    self._send(200, {"simulation": True, "service": "wingman-demo"})
                elif self.command == "GET" and path in ("/api/status", "/status"):
                    self._send(200, {"simulation": True, "service": "wingman-demo",
                                     "features": {"formula_fetch": True, "formula_fetch_mode": "full",
                                                  "vision": False, "checks_adapter": False},
                                     "operator_config": {"token_configured": True}})
                elif self.command == "GET" and path in GET_ROUTES:
                    super().do_GET()
                elif self.command == "POST" and path in POST_ROUTES:
                    try:
                        size = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        self.close_connection = True
                        self._send(400, {"error": "Invalid Content-Length"})
                        return
                    if not 0 <= size <= 16384:
                        self.close_connection = True
                        self._send(413, {"error": "Demo request too large"})
                    elif path in ("/demo/reset", "/demo/failure"):
                        self.rfile.read(size)
                        if path == "/demo/reset":
                            book.reset()
                        else:
                            book.failure = True
                        self._send(200, book.state())
                    else:
                        super().do_POST()
                else:
                    self.close_connection = True
                    self._send(404, {"error": "Not simulated: vision, external checks, publishing, and live integrations are unavailable"})
        finally:
            CURRENT_SESSION.reset(token)

    do_GET = do_POST = dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8771)
    args = parser.parse_args()
    sessions = Sessions()
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    upstream.sessions = sessions
    # Fail closed even if a future client route accidentally uses a live URL.
    # Audit hooks are process-wide and intentionally confined to this entry point.
    def only_simulated_connections(event, args):
        if event == "socket.connect" and args[1] != ("127.0.0.1", upstream.server_port):
            raise RuntimeError("Demo blocks all outbound connections except its simulated API")

    sys.addaudithook(only_simulated_connections)
    # Inherited corporate proxy variables must not reroute loopback API calls.
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    with tempfile.TemporaryDirectory(prefix="wingman-demo-") as tmp:
        # Replace ALL known regions; even an inherited WORKIVA_REGION cannot
        # select a live host. No general base-URL override is added to production.
        wk.REGIONS = {k: f"http://127.0.0.1:{upstream.server_port}" for k in wk.REGIONS}
        app._token = demo_token
        app.PORT = args.port
        app.WINGMAN_TOKEN = secrets.token_urlsafe(32)
        os.environ.update({"WORKIVA_EXPECTED_ARID": "Account/demo", "WINGMAN_APPLY_ALLOWLIST": WORKBOOK,
                           "WINGMAN_FORMULA_FETCH": "full", "WINGMAN_TYPE_FETCH": "on", "WINGMAN_LINK_FETCH": "on",
                           "WINGMAN_SAFETY_JSON": tmp + "/safety.json", "WINGMAN_DUMMY_IDS_JSON": tmp + "/dummy.json",
                           "WINGMAN_LOG_DIR": tmp + "/logs", "WINGMAN_RECEIPT_DIR": tmp + "/receipts"})
        Path(tmp + "/safety.json").write_text('{"protected_source_ids": {}}')
        Path(tmp + "/dummy.json").write_text(json.dumps({"dummy_ids": [{"id": WORKBOOK}]}))
        server = app.WingmanHTTPServer(("127.0.0.1", args.port), DemoHandler)
        server.sessions = sessions
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        print(f"Wingman demo ready on port {server.server_port} — fictional data, no Workiva connection", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            upstream.shutdown()
            upstream.server_close()
            thread.join()


if __name__ == "__main__":
    main()

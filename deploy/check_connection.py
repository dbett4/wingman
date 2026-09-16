#!/usr/bin/env python3
"""Verify the private connection-only installation without printing its token.

Only loopback status/auth/write-refusal probes; no workbook identifiers or reads.
"""
import argparse
import http.client
import json
from pathlib import Path
import re
import sys


def require(condition):
    if not condition:
        raise ValueError("Connection contract not satisfied")


def probe(token, expected_credentials="missing"):
    def request(path, *, auth=None, method="GET", body=None):
        connection = http.client.HTTPConnection("127.0.0.1", 8770, timeout=10)
        try:
            headers = {"X-Wingman-Token": auth} if auth is not None else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    if not token or len(token) < 32:
        raise ValueError("Invalid configuration")
    require(request("/health") == (200, {"ok": True}))
    for auth in (None, "deliberately-incorrect-probe-token"):
        require(request("/api/connection", auth=auth)[0] == 403)
    code, data = request("/api/connection", auth=token)
    require(code == 200)
    scope = data.pop("workivaReadScope", None)
    account = data.pop("workivaAccountPin", None)
    if expected_credentials == "present":
        require(scope == "valid" and account == "present")
    require(data == {
        "service": "wingman", "protocol": 1, "readOnly": True,
        "authorization": "accepted", "workivaAccess": "not_tested",
        "serviceMode": "read-only", "workivaCredentials": expected_credentials,
    })
    for path in ("/apply", "/apply/", "/fix", "/api/queue"):
        code, refusal = request(path, auth=token, method="POST", body=b"not-json")
        require(code == 403 and refusal.get("code") == "read_only")
    for path in ("/api/checks", "/api/review-packet?write=true"):
        code, refusal = request(path, auth=token)
        require(code == 403 and refusal.get("code") == "read_only")
    return {"connection": "accepted", "service_mode": "read-only",
            "unauthorized_requests": "rejected", "repairs_and_exports": "rejected",
            "read_scope": scope, "account_pin": account,
            "workiva_credentials": expected_credentials, "workiva_access": "not_tested"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--token-file", type=Path)
    source.add_argument("--extension-config", type=Path)
    parser.add_argument("--expect-credentials", choices=("missing", "present"), default="missing")
    args = parser.parse_args()
    try:
        if args.token_file:
            token = args.token_file.read_text().strip()
        else:
            match = re.search(r"Object\.freeze\((\{[^\n]*\})\);", args.extension_config.read_text())
            if not match:
                raise ValueError("Invalid configuration")
            token = json.loads(match.group(1))["token"]
        result = probe(token, args.expect_credentials)
    except Exception as error:
        # No response body, configuration content or secret-bearing traceback.
        print("Connection verification failed: " + type(error).__name__, file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

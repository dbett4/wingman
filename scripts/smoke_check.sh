#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${WINGMAN_BASE_URL:-http://127.0.0.1:${WINGMAN_PORT:-8770}}"
TMP_DIR="${TMPDIR:-/tmp}/wingman-smoke.$$"
SERVER_PID=""
mkdir -p "$TMP_DIR"
cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

if ! curl -fsS "$BASE/health" >/dev/null 2>&1; then
  if [[ -n "${WINGMAN_BASE_URL:-}" ]]; then
    echo "FAIL health: $BASE is not reachable" >&2
    exit 1
  fi
  smoke_port="${WINGMAN_SMOKE_PORT:-18770}"
  BASE="http://127.0.0.1:$smoke_port"
  echo "INFO starting temporary Wingman service on $BASE"
  (cd "$HERE/server" && exec env WINGMAN_PORT="$smoke_port" python3 app.py) >"$TMP_DIR/server.log" 2>&1 &
  SERVER_PID="$!"
  for _ in {1..40}; do
    curl -fsS "$BASE/health" >/dev/null 2>&1 && break
    sleep 0.1
  done
  if ! curl -fsS "$BASE/health" >/dev/null 2>&1; then
    echo "FAIL temporary Wingman service did not start" >&2
    sed 's/[A-Za-z0-9_=-]\{24,\}/[REDACTED]/g' "$TMP_DIR/server.log" >&2 || true
    exit 1
  fi
fi

request() {
  local name="$1"
  local path="$2"
  local expected="$3"
  local out="$TMP_DIR/${name}.json"
  local code
  code=$(curl -sS -o "$out" -w '%{http_code}' "$BASE$path")
  if [[ "$code" != "$expected" ]]; then
    echo "FAIL $name: expected HTTP $expected, got $code" >&2
    sed 's/[A-Za-z0-9_=-]\{24,\}/[REDACTED]/g' "$out" >&2 || true
    exit 1
  fi
  echo "OK $name HTTP $code"
}

request health /health 200
request root / 200
python3 - "$TMP_DIR/root.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1]))
assert p.get('service') == 'wingman', p
assert 'operator_config' in p, p
print('OK root service=wingman operator_config=redacted')
PY
request status /api/status 200
python3 - "$TMP_DIR/status.json" <<'PY'
import json, sys
p=json.load(open(sys.argv[1]))
assert p.get('service') == 'wingman', p
oc=p.get('operator_config') or {}
for key in ('wingman_token','extension_origin','workiva_client_id','workiva_client_secret'):
    assert key in oc, (key, oc)
text=json.dumps(oc)
for bad in ('wm-local-1665dd6a',):
    assert bad not in text, bad
print('OK status operator_config present/no secret values')
PY
request digest /digest 200
request guarded_queue '/api/queue?spreadsheetId=x' 403

echo "Wingman smoke PASS ($BASE)"

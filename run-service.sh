#!/usr/bin/env bash
# Start the Wingman local service (holds Workiva creds; serves /scan /fix /apply /version).
# Leave it running while you use or iterate on the extension.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# Load per-checkout config/secrets from .env if present (Workiva creds, port, flags).
# Shell exports win; .env only fills vars the shell did not set; empty values are ignored.
load_env() {
  [ -f "$1" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
    line="${line%$'\r'}"
    key="$(printf '%s' "${line%%=*}" | tr -d '[:space:]')"
    val="${line#*=}"
    [ -z "$key" ] || [ -z "$val" ] && continue
    [ -z "${!key:-}" ] && export "$key=$val"
  done < "$1"
}
load_env "$HERE/.env"

# Fail fast with a clear message if creds are missing — otherwise every Workiva call 401s.
if [ -z "${WORKIVA_CLIENT_ID:-}" ] || [ -z "${WORKIVA_CLIENT_SECRET:-}" ]; then
  echo "Wingman: WORKIVA_CLIENT_ID / WORKIVA_CLIENT_SECRET are not set." >&2
  echo "  Add them to $HERE/.env (run ./setup.sh to create it) or export them in your shell." >&2
  echo "  You need your OWN Workiva OAuth client — see README.md." >&2
  exit 1
fi

cd "$HERE/server" && exec python3 app.py

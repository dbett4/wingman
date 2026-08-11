#!/usr/bin/env bash
# Wingman one-time setup. Checks Python, optional vision deps, local config,
# and Workiva creds, then runs a smoke test. Safe to re-run. Does NOT require creds to
# run — it tells you exactly what is still missing. See README.md for the full flow.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

say()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }

say "1. Python"
if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found — install Python 3.9+ (e.g. 'brew install python')"
  exit 1
fi
ok "python3 $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"

say "2. Core service imports (stdlib only)"
if python3 -c "import sys; sys.path.insert(0,'server'); import app" >/dev/null 2>&1; then
  ok "server/app.py imports clean — no pip install needed for the core"
else
  warn "server/app.py failed to import; debug with: cd server && python3 -c 'import app'"
fi

say "3. Vision lane (optional — NumPy + Pillow)"
if python3 -c "import numpy, PIL" >/dev/null 2>&1; then
  ok "NumPy + Pillow present — vision available"
else
  warn "NumPy/Pillow not installed — vision reports unavailable (the core still works)"
  read -r -p "  Install vision deps now? [y/N] " a
  case "${a:-}" in
    y|Y) python3 -m pip install -r server/requirements.txt && ok "vision deps installed" ;;
    *)   warn "skipped — run later with: python3 -m pip install -r server/requirements.txt" ;;
  esac
fi

say "4. Local config (.env)"
if [ ! -f .env ]; then
  cat > .env <<'ENVEOF'
# Wingman service config. This file is gitignored — never commit real credentials.
# Shell exports (~/.zshrc) win over these; .env only fills vars the shell did not set.
# Do not wrap values in quotes. Use your own Workiva OAuth client-credentials pair.

WORKIVA_CLIENT_ID=
WORKIVA_CLIENT_SECRET=
WINGMAN_TOKEN=

# Optional:
# WORKIVA_REGION=us
# WORKIVA_CA_BUNDLE=/path/to/corp-ca-bundle.pem
# WINGMAN_PORT=8770            # if changed, update extension/manifest.json host_permissions
# WINGMAN_FORMULA_FETCH=1      # enable hardcoded-value / formula-hygiene detectors
# WORKIVA_EXPECTED_ARID=Account/...          # write gate: expected account resource ID
# WINGMAN_APPLY_ALLOWLIST=<workbook-id>      # restrict /apply writes to listed workbooks
ENVEOF
  ok "created .env — edit it and add your Workiva creds"
else
  ok ".env already exists"
fi

if python3 scripts/configure_local.py; then
  ok "generated/preserved a per-install token and synchronized extension/local-config.js"
else
  warn "local token setup failed — guarded endpoints will remain disabled"
  exit 1
fi

say "5. Workiva credentials"
load_env() {
  [ -f "$1" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
    line="${line%$'\r'}"; key="$(printf '%s' "${line%%=*}" | tr -d '[:space:]')"; val="${line#*=}"
    [ -z "$key" ] || [ -z "$val" ] && continue
    [ -z "${!key:-}" ] && export "$key=$val"
  done < "$1"
}
# Load .env without overriding shell exports (mirrors run-service.sh); ignore empty values.
load_env .env
if [ -n "${WORKIVA_CLIENT_ID:-}" ] && [ -n "${WORKIVA_CLIENT_SECRET:-}" ]; then
  ok "WORKIVA_CLIENT_ID / WORKIVA_CLIENT_SECRET are set"
else
  warn "Workiva creds NOT set — add them to .env (or export them in your shell)."
  warn "You need your OWN Workiva OAuth client (admin-gated); see README.md."
fi

say "6. Detector self-test (smoke)"
if python3 server/detectors.py >/dev/null 2>&1; then
  ok "detector self-test passed"
else
  warn "detector self-test failed — run: python3 server/detectors.py"
fi

say "Done."
echo "  Next: ./run-service.sh   (leave it running)"
echo "  Then install the extension and verify the cell pill — see README.md."

# Wingman

## The problem this answers

A financial statement can “tie out” and still be wrong in ways an export will not
show: broken live links, hardcoded values hiding inside formulas, formatting drift,
years rendered as `2,025`, low-contrast text, stray label whitespace.

Reviewers need those defects found in the live workbook. Most of them still need
human judgment. Only a narrow set should ever be auto-fixed — and only if the tool
can prove the fix and undo it.

**One-line pitch:** find workbook defects in the live editor; automate only the
fixes you can confirm, read back, and reverse.

Wingman is a Chrome extension plus a local Python service for that loop in
Workiva-shaped reporting workbooks. Fictional demo data only; not affiliated with
or endorsed by Workiva.

> **Provenance.** Sanitized public extract published August 2026; Git dates are
> publication dates, not the original private development timeline. City of
> Riverton data is fictional — no client data or credentials are included.

![Tests](https://github.com/dbett4/wingman/actions/workflows/test.yml/badge.svg)

## Current demo

The demo uses a fictional City of Riverton workbook. It scans the workbook, separates
mechanical fixes from items that need review, and shows the readback result after a
confirmed write. No screenshots or sample data in this repository come from a client.

## Why I built it

Financial reports often live in cloud editors where an exported file omits useful
context. Wingman turns repeated government financial-reporting review checks into
detectors. It does not try to repair every finding. A change is automated only when
the service can capture the original state, predict the result, read the cell back,
and restore the original value if the result differs.

## Proof signal

Current clean-run result: 469 Python tests pass, 6 integration-dependent tests
skip (475 collected), and 243 extension tests pass. CI runs the same Python and
extension commands on Python 3.11 and 3.12.

## Architecture

```mermaid
flowchart LR
    subgraph Chrome
        E[Chrome extension<br/>active-cell address · review panel · screenshots]
    end
    subgraph localhost
        S[Python service on 127.0.0.1:8770<br/>credentials · request checks]
    end
    W[Workiva REST API<br/>OAuth · pagination · async operations · writes]

    E -->|token-protected JSON| S
    S -->|OAuth2| W
```

The extension reads the active cell address from the page and sends requests to the
local service. Credentials stay in that local process. The service checks the target
workbook and account before it allows a write.

## Write controls

- Writes are dry runs until the user confirms them.
- Before writing, the service saves the prior state and checks that the target has not
  changed.
- After writing, it reads the cell again. A mismatch triggers a restore from the saved
  state.
- Scale and currency checks prevent format changes from altering stored values or
  adding a currency symbol where it does not belong.
- The service refuses writes outside an explicit workbook allowlist.
- Findings that require accounting judgment or UI-only work remain review items.
- Activity logs contain coordinates, counts, and hashes, not cell values or formulas.

The [architecture decision records](docs/adr/) document the individual detector and
write-path choices, including rejected approaches and measured false positives.

## Run it

```bash
git clone https://github.com/dbett4/wingman.git
cd wingman
./setup.sh                 # creates a per-install token + .env, then runs smoke checks
# add your Workiva OAuth client credentials to .env
./run-service.sh           # starts the service on 127.0.0.1:8770
```

Load `extension/` as an unpacked extension from `chrome://extensions`, open a Workiva
spreadsheet, and click the Wingman toolbar icon. Without credentials, you can still
start the detector self-test with `python3 server/detectors.py`. The full service
launcher exits early if credentials are missing.

`./setup.sh` writes the same generated local token to the gitignored `.env` and
`extension/local-config.js`. Guarded routes have no packaged fallback token and stay
disabled if setup has not completed. Reload the unpacked extension after setup.

`/api/checks` is an optional adapter for a separate `run_checks.py` installation.
That external checks CLI is not included here; `/api/status` reports whether it is
available, and the adapter returns an explicit unavailable result when it is absent.

## Test it

```bash
pip install pytest
python3 -m pytest server/          # 469 pass, 6 skip (475 collected)
node extension/content.test.js     # 243 tests
scripts/smoke_check.sh             # route and write-gate smoke checks
```

## Amp orbs

`.agents/setup` prepares Python 3.12 in `.venv` with pytest and the optional
NumPy/Pillow vision dependencies. It uses uv and Node from the orb base image;
the extension needs no npm install. Repository login shells activate `.venv`
automatically, so the test commands above work without manual activation.
Setup is idempotent and reuses installed dependencies on warm snapshots.

`.agents/resume` generates the local service/extension token after snapshot
activation and preserves it on subsequent wakes. Setup does not create credentials
or start services. Workiva OAuth credentials and the external checks CLI remain
optional, user-supplied integrations; live Workiva operations require credentials.
For manual dependency changes, use `uv pip install --python .venv/bin/python`.

## Scope

This repository was extracted from tooling used on live government financial reports.
It currently targets Workiva's API and is not affiliated with or endorsed by Workiva.
The detector, readback, and restore pattern can be applied to other systems, but that
portability is not implemented here.

Built by [Dave Bettner](https://davebettner.com).

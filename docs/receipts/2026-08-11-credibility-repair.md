# Wingman credibility and local-auth repair receipt

Date: 2026-08-11

## Changed behavior

- Removed the packaged `WINGMAN_TOKEN` fallback from both the Python service and extension worker.
- Added `scripts/configure_local.py`, which creates one cryptographically random per-install token, stores it in gitignored `.env` and `extension/local-config.js`, uses mode `0600` on POSIX, preserves it on rerun, and never prints it.
- Guarded endpoints now require a configured matching token. An allowed CORS origin is no longer an authentication fallback.
- The extension reports an actionable setup error when its generated local config is absent.
- `run-service.sh` fails closed when setup has not created a token.
- `/api/status` distinguishes the checks adapter from availability of the external `run_checks.py` CLI.
- README now states exact test outcomes and the optional external-checks boundary.
- Public-extract provenance is visible near the top.

## Verification

```text
$ python3 -m pytest -q server/
462 passed, 13 skipped in 3.52s

$ node extension/content.test.js
243/243 passed

$ scripts/smoke_check.sh
Wingman smoke PASS (http://127.0.0.1:18770)

$ env -i ... ./setup.sh; ./run-service.sh
SETUP_SHARED_TOKEN_PASS
RUN_SERVICE_MISSING_CREDS_FAIL_CLOSED
CLEANED_LOCAL_SETUP_ARTIFACTS

$ git diff --check
(exit 0)
```

The isolated setup run had no Workiva credentials. It verified that the service and extension configs held the same generated token without printing it, then verified that the service refused to start for missing Workiva credentials. Generated `.env`, extension config, and temporary output were deleted after the check.

## Boundaries

- Workiva reads/writes: 0
- Client data: 0
- Credential values printed or committed: 0
- Remote writes: 0
- Commit/push performed by this receipt: no

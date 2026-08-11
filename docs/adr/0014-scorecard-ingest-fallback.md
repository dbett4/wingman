# ADR 0014 — Read an existing tie-out scorecard when live checks cannot run

**Status:** Accepted — 2026-06-22
**Track:** project-checks (watcher/tieout bridge)

## Context

Wingman surfaces ACFR tieout / check failures only by spawning the live `run_checks.py`
subprocess (`checks_bridge.run_checks_suite`). When that subprocess cannot run — the script is
not installed (`resolve_run_checks_script() is None`), no client working dir resolves, or the
subprocess times out — Wingman returned a `skipped` meta and surfaced **nothing**, even though a
freshly generated tieout scorecard JSON already sits on disk.

Scheduled ACFR checks already produce this file by running `run_checks --json`; the
production tieout run persists `validation/tieout_scorecard.json`. The existing diagnostics
output was not actionable inside Wingman without re-running the whole live check, which needs the
the external checks toolkit present and (for the tieout suite) live Workiva access.

## Decision

Add a read-only **scorecard-ingestion** path that surfaces the existing scorecard JSON as queue
findings without spawning any subprocess:

- `checks_bridge.ingest_scorecard(spreadsheet_id, ...)` resolves the scorecard path
  (`WINGMAN_TIEOUT_SCORECARD` override → `tieout_enrich.resolve_scorecard_path`), loads it, and
  converts FAIL/ZERO rows to the same `fail_rows` structure `run_checks_suite` emits (via the existing
  `tieout_enrich.scorecard_fail_rows`, which already attaches coordinates + heuristics). It returns a
  metadata dict with `source="scorecard"` and `exit_code=None` (no process spawned), so the result
  drops straight into `diagnose.merge_queue_with_checks`.
- **Explicit mode:** `suite=scorecard` (via `/api/checks` or `?checks=scorecard`) always ingests
  the scorecard and never spawns the live script.
- **Automatic fallback:** when the live subprocess is skipped (no script, no working dir, or
  timeout), `run_checks_suite` falls back to the scorecard *only when eligible* —
  `_scorecard_eligible` = project resolves to `acfr` **or** `WINGMAN_TIEOUT_SCORECARD` is set. On a
  fallback the meta carries `live_skipped` naming why the live run was bypassed, so the caller can
  see that the data came from a saved file rather than a fresh live check.

`live_skipped` and `source` are added to the `checks` metadata allowlist in
`merge_queue_with_checks` and the `/api/checks` route so the provenance is visible to the UI and to
the F1 review packet.

## Why eligibility is gated

The tie-out scorecard is engagement-specific today, and the default-path resolver can return
the mirror copy regardless of which spreadsheet is being scanned. Gating the *automatic* fallback
to `acfr` (or an explicit path override) prevents a scorecard for one engagement from appearing in
an unrelated workbook's queue. The explicit `suite=scorecard` mode simply skips when no file can be
resolved.

## Consequences

- A saved scorecard becomes actionable in Wingman even where `run_checks` cannot run (cloud
  session, no live Workiva, external checks toolkit absent). Findings get the full tieout diagnosis
  pathway (coordinates, jump hints, double-aggregation / source-gap heuristics) because they reuse
  `scorecard_fail_rows` + `tieout_pathways`.
- Read-only: no subprocess in the scorecard path, no Workiva access, no writes. Surfaced provenance
  (`source`, `live_skipped`, `scorecard_generated_at`) keeps a saved result from being
  mistaken for a fresh live tieout — staleness is visible, not hidden.
- A saved scorecard can feed the review packet without a live run.

## Alternatives considered

- **Always fall back to any scorecard found on disk.** Rejected — would cross-contaminate
  unrelated engagements via the default mirror path. Eligibility gating fixes this.
- **A separate watcher-state reader** (a per-client regression-diff state file kept by the
  scheduled check runs). Deferred — that state file is a regression-diff snapshot, not a per-line
  scorecard; the tie-out scorecard includes cell coordinates and already had a loader. A
  watcher-state reader can be added later if per-client freshness or regression data is needed.

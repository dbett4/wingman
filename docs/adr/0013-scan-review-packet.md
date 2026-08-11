# ADR-0013 — Scan → review packet (F1)

**Status:** Accepted (2026-06-22). Backlog source: internal improvement backlog
("Scan → review packet"). Sequenced after the link lane (ADR-0008) and the display-link
detectors.

## Context
Wingman finds issues but leaves no durable artifact. Every scan produces a rich, prioritized
queue (`diagnose.build_sheet_queue` / `build_workbook_queue`, optionally merged with run_checks
rows), but that payload lives only in the side panel for the length of a session. There is no
hand-off object a verifier, an external task-tracker entry, or a future session can act on — and no place
that states, per finding, *whether Wingman can act, who must act, and how to roll it back.*

The operating framing already exists in the codebase for tieout (`export_proof.py`,
"tieout green ≠ client-ready") and for the telemetry log (`wingman_log.py`, a strict PII
boundary). The review packet brings those two ideas together: a structured, **redacted** packet that classifies
each finding and rolls up an overall verdict.

## Decision
A new pure-logic module `review_packet.py` turns a scan queue into a handoff packet:

1. **Three dispositions per finding.**
   - **ACCEPT** — Wingman can fix it itself, safely and reversibly (`fixable and fix_lane ==
     "safe-auto"`). Rollback = the fixer's automatic readback-and-revert.
   - **BLOCKED** — a known remediation that needs human authority / source / a Workiva write
     Wingman will not auto-do: guided fixes, fixable-but-not-safe-auto, run_checks / tieout
     exceptions, anything carrying a guided lane (export-proof, blank-dl), and confirmed
     error-class defects (`broken-ref`, `scan-error`).
   - **UNVERIFIED** — a surfaced read-only heuristic with no fix and no guided remediation; a
     verifier must confirm it against the live source / export before acting.

   The classification is total and rule-based (`classify_disposition`). The `_BLOCKED_KINDS`
   allowlist (`broken-ref`, `scan-error`) is the only per-kind special-case; everything else is
   driven by `fix_lane`, `fixable`, `check`, and the diagnosis guided-lane fields — so it tracks
   the existing detector taxonomy without a parallel rule table.

2. **Per-finding capability tier + rollback path.** Each finding carries its `capabilityTier`
   (`safe-auto | guided | surfaced`), a `dispositionReason`, and a concrete `rollback` string.
   This is the "capability tier per fix" + "per-fix rollback path" the plan asks for.

3. **Overall verdict.** `overall` rolls up most-gating-wins (BLOCKED > UNVERIFIED > ACCEPT >
   CLEAN). `clientReady` is **always False** with a fixed caveat — a scan-derived packet is never
   client-ready proof (mirrors the tieout/export-proof gate and the link lane's "rendered proof
   still required").

4. **Redaction is structural, with a documented boundary distinct from the telemetry log.**
   - The raw Workiva spreadsheet id is pseudonymized to a SHA-256 `workbookHash` (same idea as
     `wingman_log._sheet_hash`).
   - Client cell **values** never enter the packet: `cellValues` is dropped, and free-text
     `signature` strings have their numeric runs redacted to `#` (`_redact_numbers`, which
     preserves `#RRGGBB` hex color tokens so low-contrast findings still read cleanly).
   - Structural coordinates (A1 `addrs`, capped) and `sheetName` **are** retained — the packet's
     entire purpose is to be actionable for a verifier. This is a deliberate, narrower boundary
     than the telemetry log (which pseudonymizes everything because it is mined in bulk). The
     packet is meant to live in the client's own task-tracker context, so retaining sheet
     geometry while stripping values and the raw workbook id is the right line.

5. **Output + persistence.** `render_packet_md` produces a human-readable markdown rendering
   (BLOCKED section first — act on these). `write_packet` persists JSON + MD to a local dir
   (`WINGMAN_PACKET_DIR`, default `~/.wingman/packets`) **outside the repo** — never committed,
   same convention as the activity log.

6. **Route.** `GET`/`POST /api/review-packet {spreadsheetId, sheetId?, checks?, label?, write?}`
   runs the **same read-only scan path** as `/api/queue` (sheet or workbook, with optional checks
   merge), builds the packet, and returns `{packet, markdown}` (+ `written` paths when
   `write=1`). No new Workiva access and **no writes** — the only side effect is the optional
   local artifact.

## Consequences
- Wingman becomes a review surface, not just a scanner: a single call yields a triaged,
  redacted, durable ACCEPT/BLOCKED/UNVERIFIED packet ready to attach to a task or hand to a
  verifier.
- The packet is read-only and never asserts client-ready; ACCEPT still requires the safe-auto
  write + readback, and BLOCKED/UNVERIFIED still require human action and export/render proof.
- Wingman does **not** create the external task-tracker entry itself — that would be an external
  send (RED). The artifact is the JSON/MD; wiring it to a task is a downstream, explicitly-gated
  step.

## Alternatives considered
- **Reuse `wingman_log` redaction wholesale** — rejected: it drops `addrs` and all signatures,
  which would make a handoff packet unactionable. The packet needs a narrower, documented
  boundary (values + raw id out; geometry in).
- **Carry the full diagnosis (guided_steps, export_proof targets) into the packet** — rejected
  for now: those fields embed client grep targets and would need their own redaction pass. The
  concise `suggestedAction` + `addrs` + `rollback` route a verifier without re-exposing values;
  a redacted target hint can be added later if needed.
- **Auto-create the external task-tracker entry** — rejected: external send, RED gate. Out of scope.

## Acceptance
`python3 server/review_packet.py` self-test (28/28). `python3 -m pytest server/ -q`
(**394 passed**; new `test_review_packet.py` = 31 tests + 2 `/api/review-packet` route tests in
`test_app_routes.py`). `node extension/content.test.js` (222/222, unchanged — server-only lane).
No Workiva writes; route exercises the read-only scan path (route test monkeypatches the scan so
no live Workiva call is made).

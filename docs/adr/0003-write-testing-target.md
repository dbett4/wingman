# ADR-0003 — Integration write-tests run against a live production workbook, bounded

**Status:** Accepted. The workbook owner authorized this; it overrides the recommended disposable-sandbox option.

## Context
The safe-lane fix (ADR-0002) writes to Workiva, so integration tests need live write +
readback + revert. The candidate test workbook is **not a throwaway** — it is a live
production workbook that Wingman had previously been authorized to read. The
recommendation was to spin up a disposable sandbox; the workbook owner chose to authorize
scoped writes on the production workbook instead.

## Decision
Integration write-tests may run against the production workbook, under hard bounds:
- Test writes target **only** cells proven (by pre-read) to be **empty, unlinked, and
  plainText**, in an **unused region** of a sheet — never live financial data or linked cells.
- Every test write uses the full harness: pre-read captured before-state → apply → poll to
  completion → readback → revert to before-state. The cell returns to its exact prior state.
- Confirm-before-write stands; no structural ops; no DL-touching ops.

## Consequences
- Tests exercise the real API write path and the real revert harness, not a mock.
- Residual risk (best-effort revert is not true undo) is accepted by the workbook owner.
- If a test write ever fails to revert cleanly, that is a stop-the-line event: log it, leave
  the captured before-state in the receipt, and surface it before any further writes.

## Alternatives considered
- **Disposable sandbox spreadsheet** (recommended) — not chosen; would have removed all risk
  to the live workbook.
- **Mock the write path** — rejected: under-tests the exact risky code (silent no-op, readback
  mismatch, revert) the harness exists to prove.

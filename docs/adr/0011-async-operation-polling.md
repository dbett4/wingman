# ADR-0011 — Poll async Workiva write operations

Date: 2026-06-20
Status: Accepted

## Context

Some Workiva write endpoints return `202 Accepted` before the platform has finished validating and applying the change. A later operation can fail asynchronously, especially on validated or constrained cells. If Wingman writes and immediately reads back without polling the operation result, it can misclassify an unchanged cell as a successful no-op or produce confusing readback/revert behavior.

## Decision

Wingman's shared JSON request path now detects async operation handles in write responses and polls them before returning to the fixer harness:

- `operationLocation`
- `operationUrl`
- `href`

Relative operation URLs are resolved against the configured Workiva API base. Polling accepts completed/succeeded/success statuses and raises on failed/error/cancelled/canceled statuses. If a 202 response contains no operation URL, the request path preserves previous behavior.

## Scope

This affects forward writes and restores that use the shared `_post` / `_put` helpers. Dry-run behavior is unchanged. The change does not add any write capability; it prevents the existing code from reading a result before the asynchronous operation has finished.

## Verification

Covered in `server/test_fixer.py` by `AsyncOperationPollingTests`:

- relative operation URL resolution
- 202 response polling to completion
- failed async operation raises
- 202 without an operation URL does not poll

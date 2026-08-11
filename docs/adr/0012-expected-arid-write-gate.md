# ADR-0012 — Expected Workiva ARID write gate

Date: 2026-06-20
Status: Accepted

## Context

Wingman reads and writes Workiva through client-credentials auth. If the local environment points at the wrong Workiva account/workspace, a safe-auto write could target the wrong tenant even though the spreadsheet/sheet IDs are syntactically valid. A companion internal tool already uses an opt-in `WORKIVA_EXPECTED_ARID` guard for mutating Workiva requests; Wingman needed the same boundary.

## Decision

Wingman's write request path now enforces an opt-in account/workspace identity gate:

- If `WORKIVA_EXPECTED_ARID` is unset or empty, behavior is unchanged.
- If set, mutating requests decode the JWT `arid` claim and require an exact match, such as `Account/123456789` or `Workspace/<id>`.
- A mismatch raises before any network write request is sent.
- Reads are not gated.

The gate is implemented in `server/fixer.py` because that is Wingman's shared write path (`_post` / `_put`).

## Consequences

This does not add any write capability. It reduces wrong-tenant blast radius for existing safe-auto writes and mirrors that proven guard pattern.

## Verification

Covered in `server/test_fixer.py` by `ExpectedAridGateTests`:

- JWT `arid` decode to `Type/id`
- matching `WORKIVA_EXPECTED_ARID` allows the write path
- mismatch blocks before `urlopen`
- unset/empty env leaves behavior unchanged

# ADR-0017 — Degenerate Placeholder Formula Detector

**Status:** Accepted — 2026-06-22
**Track:** formula-portability / hardening-gate hygiene

## Context

Some cells look formula-driven but contain only placeholders such as `=1+1`, `=0`, or other constant arithmetic. Numeric tie-outs can miss them when another cell offsets the error, and the leading equals sign makes them easy to overlook during review.

The existing `hardcoded-constant-in-formula` detector catches large embedded plugs (`=12345`, `=SUMIFS(...)+250000`). It intentionally skips small constants because row-control cells often use `=1` or `=-1`.

## Decision

Add `degenerate-placeholder-formula` to `server/formula_hygiene.py` as a review-only detector:

- fires on `=0`, `=0.00`, and small constant-only arithmetic (`=1+1`, `=100-100`, `=(2+2)*0`);
- skips bare `=1` / `=-1` row-control shapes;
- skips pure exponent constants such as `=2^3` as possible support-sheet math;
- skips large literals already owned by `hardcoded-constant-in-formula`;
- skips year-like literals, refs, functions, strings, comparisons, commas, percentages, and invalid arithmetic;
- emits one stable per-sheet signature so repeated placeholders group into one Review row;
- adds a `formula.degenerate-placeholder` diagnose pathway that tells the user to rebuild from the governed row/column pattern or document the value as an input.

No auto-fix is provided. The correct replacement depends on the statement architecture and neighboring formula pattern.

## Alternatives rejected

1. **Flag every literal formula.** Rejected: `=1` / `=-1` are common row-control/sign cells and would create false positives.
2. **Fold into `hardcoded-constant-in-formula`.** Rejected: the risk is different. Large dollar plugs are compensating hardcodes; tiny placeholder arithmetic is formula-presence debt.
3. **Auto-rewrite to neighbor formula.** Rejected: row/column pattern inference is architecture-specific and can corrupt formula references.

## Verification

- `python3 server/formula_hygiene.py` → 86/86 passed.
- `python3 -m pytest server/test_formula_hygiene.py server/test_diagnose.py -q` → 123 passed.

## Scope

Read-only. The detector makes no Workiva requests, network calls, subprocess calls, or writes.

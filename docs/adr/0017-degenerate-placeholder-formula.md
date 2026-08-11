# ADR-0017 — Degenerate Placeholder Formula Detector

**Status:** Accepted — 2026-06-22
**Track:** formula-portability / hardening-gate hygiene

## Context

The improvement backlog called out the **degenerate placeholder formula**: cells that look formula-driven but are only stubs such as `=1+1`, `=0`, or constant-only arithmetic. These are missed by numeric tieout when a compensating cell offsets them, and they survive roll-forward because reviewers see an equals sign and assume the row is governed.

The existing `hardcoded-constant-in-formula` detector already catches large embedded plugs (`=12345`, `=SUMIFS(...)+250000`). It intentionally does not catch tiny placeholder formulas because row-control cells often use `=1` / `=-1`, and broad constant detection would erode trust.

## Decision

Add `degenerate-placeholder-formula` to `server/formula_hygiene.py` as a surfaced-only detector:

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

## Safety boundary

Read-only / surfaced. No Workiva access, no network, no subprocess, no writes, no auto-rewrite.

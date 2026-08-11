# ADR 0016 — Detect functions that Workiva cannot import

**Status:** Accepted — 2026-06-22
**Track:** formula-portability (sibling of ADR-0015 / D1)

## Context

A formula that calls a function Workiva's engine does not support computes (or spills) in Excel but
fails on xlsx **import** with `#NAME?` — the formula stops calculating entirely. Field history names
this the highest-weighted recurring portability defect: on one county FY25 engagement, **thousands of
`FALSE()` calls** were the single #1 import blocker. The proven detection already lives outside Wingman in
the external checks toolkit's `corpus_audit.py` as `RE_DEAD` (`dead_func`), the highest-weighted
corpus detector (weight 5.0, vs 2.0 for the D1 unbounded-range detector) precisely because a `#NAME?`
breaks the line completely rather than only on round-trip. Wingman did not check for this before an
import attempt.

Like ADR-0015, this is a deterministic formula-text check. Results are grouped by sheet
to avoid repeating the same warning for thousands of cells.

## Decision

Add `formula_hygiene.detect_dead_functions` as the fifth formula-hygiene detector. It
sets `fixable=False` and creates a review item because the correct rewrite depends on
the function and the surrounding formula.

- **Gate (mirrors `corpus_audit.RE_DEAD`):** `_DEAD_CALL` matches the dynamic-array / spill family
  (`FILTER, SORT, SORTBY, UNIQUE, LAMBDA, LET, VSTACK, HSTACK, SEQUENCE, TOROW, TOCOL, TAKE, DROP,
  EXPAND, CHOOSEROWS, CHOOSECOLS`), `INDIRECT`, and the volatile positional `ROW`/`COLUMN`/`OFFSET`,
  each as a **call** (`name + "("`). `_ANCHOR_HYPERLINK` matches the internal-anchor `HYPERLINK("#...`.
- **Extends the corpus gate** with `_BOOL_CALL` — the `TRUE()`/`FALSE()` **call form** (empty parens).
  Workiva accepts the bare `TRUE`/`FALSE` literals but rejects the parenthesised call on import; this
  is the #1 import blocker the coarse corpus detector does not separately count.
- **Severity `high`** — unlike D1's latent `medium` (computes live, fails only on round-trip), a dead
  function fails on import / breaks the line outright. This matches the corpus weight ranking
  (`dead_func` 5.0 > `unbounded_rng` 2.0) and lets the queue rank a dead function above an unbounded
  range. Severity feeds only queue ordering; it does not change the review-packet disposition.
- **Per-sheet, grouped by function set:** the `detail` names the sorted unique function set, so a
  sheet's thousands of `FALSE()` cells collapse to **one** Review row, while a distinct set (e.g. `INDIRECT()`)
  is its own row. This satisfies the anti-noise requirement while staying actionable (the remediation
  differs by function). The function set also rides in `target.functions`.
- A `formula.dead-unsupported-function` diagnose pathway explains the `#NAME?` import break and gives
  the per-function rewrite map (INDIRECT → explicit ref; spills → SUMIFS / INDEX+MATCH;
  ROW()/COLUMN()/OFFSET() → fixed ref or row control; TRUE()/FALSE() → bare literal; internal-anchor
  HYPERLINK → UI Insert-Link).

## False-positive guards (tested)

- **Call form required (`name + "("`):** the supported count functions `ROWS(`/`COLUMNS(` never fire,
  and a longer name that merely starts with a dead token (`OFFSETTOTAL(`, `FILTERED()`) never fires —
  the trailing `(` anchor and `\b` boundary prevent the prefix match.
- **Bare `TRUE`/`FALSE` literals** (`=IF(A1>0,TRUE,FALSE)`) — supported; the empty-paren requirement
  keeps them quiet.
- **`INDEX`/`MATCH`** (the port baseline, a GOOD signal) — not in the dead set, never fires.
- **Function name inside a sheet ref** (`'SORT detail'!A1`, `'ROW map'!B2`) — sheet refs stripped first.
- **Function name inside a quoted criterion** (`SUMIFS(...,"FILTER")`) — string literals stripped first.
- **Normal external HYPERLINK** (`HYPERLINK("https://…")`) — only the `"#` internal-anchor form fires.
- **`SORTBY` vs `SORT`** — `SORTBY` precedes `SORT` in the alternation, so the canonical longest token
  is captured.

### Precision over the coarse corpus gate (deliberate divergence)

`corpus_audit` runs `RE_DEAD` over the whole formula and counts a sheet hit; it also matches function
names inside quoted strings / sheet names because it does not strip them. Wingman strips `'Sheet'!`
refs and `"..."` literals before matching, so a quoted criterion or a sheet name that contains a
function word never fires. Counts may therefore be marginally lower than the corpus total; that is
correct for the named defect.

## Boundary

Read-only. The detector reads normalized cell formulas already in the scan path and emits a
Review finding. It performs **no Workiva write** and proposes **no auto-rewrite** — each rewrite is a
judgment that a person applies, or that a controlled script generates, with before-and-after readback.

## Alternatives considered

- **Auto-fix (e.g. strip `()` from `FALSE()`)** — rejected. Even the mechanical-looking TRUE()/FALSE()
  case rides in formulas whose surrounding logic a scanner cannot prove safe to rewrite; the whole
  detector remains review-only.
- **Fixed per-sheet signature (like D1)** — rejected in favour of grouping by function set: the
  remediation differs by function, and naming the set is more actionable while still collapsing the
  thousands-of-`FALSE()` case to one row.
- **Fold into the existing `broken-ref`/#NAME? pathway** — rejected. `broken-ref` fires only after a
  cell already shows `#NAME?`; D2 is proactive, flagging the dead function in formula TEXT before any
  import attempt (and while the cell still computes live), so it is complementary, not duplicate.
- **Add to `review_packet._BLOCKED_KINDS`** — rejected for now; kept UNVERIFIED to match its sibling
  D1, so a verifier confirms the formula text against the live source before acting.

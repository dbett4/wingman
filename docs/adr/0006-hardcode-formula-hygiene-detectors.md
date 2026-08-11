# ADR-0006 — Hardcode / formula-hygiene detector wave

**Status:** Accepted (2026-06-19). Extends ADR-0002 / ADR-0004 (narrow, honest, low-noise detect scope).

## Context
Asked to "improve the scans," we mined the ACFR project history across several production engagements
for the corrections made *most often*. One family dominated: **hardcoded values that should be
formula-driven**, his stated #1 rule ("no hiding hardcoded values in formulas; route criteria and
amounts through the architecture"). The recurring, cell-detectable shapes, with their frequency:

| pattern | evidence | detectable from |
|---|---|---|
| literal CODE criteria in SUMIFS (`"ACT-ADMIN"`, exclusion `"<>EXP-EQUIP"`) | thousands of include-literals + hundreds of `<>` exclusions on one engagement's statement faces | formula string |
| numeric PLUG welded into a formula (`+12000000`, `-250000`, sole-body `=12345`) | a documented compensating-hardcode-stack pattern | formula string |
| bare numeric cell where the column is formula-driven (a raw amount sitting among SUMIFS) | recurring fix recipe (observed on every engagement mined) | cell value + column consensus |
| `=ROUND(SUM(...),-3)` display rounding | a documented display-rounding anti-pattern | formula string |

A prior lane already shipped the third one as `hardcoded-face-value` (`hardcoded_value.py`), wired
through the panel's `FORMULA_GAP_KINDS` triage bucket. This wave adds the other three and hardens the
existing one.

Every candidate was put through an adversarial false-positive pass before any code. Two real FPs were
caught and gated out (see Decision). The bar is unchanged from ADR-0004: *a scan that cries wolf is
worse than a narrower one that is trusted.*

## Decision
**Ship three new detectors in `formula_hygiene.py`** (companion to `hardcoded_value.py` — that module
flags a cell that should be a formula but is a bare literal; this one flags a formula that *hides* a
hardcode). All are **surfaced**, `fixable=False` — the correct fix is a mapping/formula change the
scanner cannot synthesize, and some hardcodes are intentional (a documented PDF-tie cell with an
audit-trail marker), so the reviewer decides. All need the formula string
(`WINGMAN_FORMULA_FETCH`); each no-ops cleanly when it is absent.

1. **`hardcoded-text-criteria-in-formula`** — a code-like quoted literal in a SUMIFS-family criteria
   position. Exclusion lists (`<>`) are `high` (they silently capture every new code next year);
   include literals are `medium`. Grouped by code-set so a repeated house pattern is one Review line.
   Gates: must be a SUMIFS-family call; the literal must be code-like (`^(<>)?\*?[A-Z]...`), which
   rejects `""`, operators (`"<>"`,`">0"`), format masks (`"#,##0"`), and sentence strings
   (`"Mapping error"`).
2. **`hardcoded-constant-in-formula`** — a numeric literal ≥1000 in **additive position** (`+N`/`-N`
   or sole body). The additive-position gate is what makes it safe: it structurally excludes the FPs
   the adversarial pass found — date serials in comparisons (`=IF(A1>=44927,...)`), function args
   (`MROUND(...,10000)`, `PRICE(44927,...)`), and `ROUND(...,-3)` precision args — because those are
   not additive operands. Plus a year-range exclusion and a date-serial-range exclusion when a date
   function is present. Refs are stripped to placeholders first, so `$B$7` / `A12345` digits never leak.
3. **`round-wrapper-workaround`** — `ROUND/ROUNDUP/ROUNDDOWN(...,-N)` (negative precision = display
   rounding). `TEXT(ROUND(...))` is left to `display_wrapper` to avoid a double-fire.

**Harden the existing `hardcoded-face-value`** with a cap-boundary guard. `WINGMAN_FORMULA_FETCH_CAP`
(500) enriches only the first 500 cells row-major; a *real* formula cell beyond the cap has
`formula=None` and looked like a bare literal next to in-cap formula neighbors — a false positive on
large sheets. `wk_client.enrich_cells_with_formulas` now stamps `formula_fetched=True` on the cells it
fetched; the detector trusts only marked cells **once any marker is present**, so the guard is inert for
the existing unit tests (no markers) and closes the FP on real scans.

The three new kinds join `FORMULA_GAP_KINDS` in `wingman-core.js`, so they roll into the "N formula
gaps" triage bucket (ADR lane-002) rather than the format-only "review" count, and get honest
`diagnose_pathways` entries (route-through-architecture, never another hardcode).

## Deferred (recorded so they are not re-attempted blind)
- **number-stored-as-text** — ADR-0004 already measured and rejected it (5 hits, all year headers);
  TEXT/PERIOD on a year cell is the *intended* fix (documented as the year-header route).
  An adversarial pass confirmed the column-only neighbor model cannot carry the row-band gate it would
  need. Stays out.
- **scaled-decimal-code-key** (`10796`→`10.796`, sign `1`→`0.001`) — real and data-integrity-impacting
  (a documented fix recipe) but niche: it was observed only on dedicated override/support
  sheets, and the `×1000-clean-int` test is near-vacuous without an `override|support|code` sheet-name
  gate. Ship when that gate exists, scoped to those sheets.
- **wrong-sign value & cross-foot / sum-vs-subtract** (a documented sum-vs-subtract bug pattern) — need the
  account natural-balance map / statement structure (which rows are details vs totals) the per-cell
  scanner does not have. These belong in the **tieout path** (`checks_bridge.py`), not the cell scan.
  The only per-cell sign signal — a hardcoded `*-1` multiplier — needed so much gating it did not clear
  the bar; deferred.

## Consequences
- The detector roster grows by three trustworthy classes that directly target the most-repeated
  manual correction, plus a latent FP fixed in the shipped face-value detector.
- Net-new write surface: none. All surfaced; the safe-auto lane is untouched.
- Coverage is honest about its one dependency: the formula detectors are silent without
  `WINGMAN_FORMULA_FETCH`, and the scan chip already reports formula-coverage as partial/off.
- Tests: `python3 server/formula_hygiene.py` (30), `pytest server/test_formula_hygiene.py` +
  `test_hardcoded_value.py` (84), full `pytest server` (244), `node extension/content.test.js` (205).

## Alternatives considered
- **One combined `hardcode_lane.py`** that re-owned face-value — rejected: would orphan the
  already-wired `hardcoded-face-value` kind (panel + diagnose + tests). Kept one canonical owner per
  concern (`hardcoded_value.py` = literal-where-a-formula-belongs; `formula_hygiene.py` = formula-that-
  hides-a-hardcode), mirroring the `junk_decimal.py` / `format_lane.py` split.
- **Flag every include-literal at `high`** — rejected: exclusion lists are the dangerous ones
  (silent new-code capture); include literals are `medium`, and code-set grouping keeps volume sane.
- **A bare-`ROUND(...,0)` detector** — rejected: `,0` is ambiguous (often a legitimate scale); only
  negative-precision display rounding is unambiguous.

# ADR-0009 — Year-automatic-coercion detector + PERIOD fix

**Status:** Accepted (2026-06-19). New safe-auto detector. Backlog source: internal improvement backlog (Tier 2
S1). Extends the safe-auto valueFormat lane (ADR-0005) and rides the H1 scale guard (ADR-0007).

## Context
A 4-digit year stored as a number under Workiva's **AUTOMATIC** value format is silently coerced by
the render layer to a thousands-separated string — `2025` → `2.025` / `2,025` — in **document
destination links** (e.g. a paragraph reads "August 2.025"). This is a client-visible presentation
defect; the canonical fix is `valueFormatType=PERIOD`, which suppresses the separator
(a documented year-header fix recipe; 52 cells across 220
production ACFR sheets).

**Live probe (2026-06-19) settled the detection signal.** Sheetdata does **not** carry
`effectiveValue` (0/20000 cells), so the divergence (`calculatedValue` vs displayed string) is not
directly observable at scan time. But the coercion is fully predictable from the value format: on
the production "Dates" sheet, year cells appear in both states — some already `PERIOD` (correct) and
some `AUTOMATIC` (the defect). The format type is the clean discriminator.

## Decision
1. **Detector `detect_year_automatic_coercion`** (severity medium) fires only when:
   - `calculatedValue` (or `value`) is a **bare 4-digit integer in [1900, 2100]**, AND
   - `valueFormatType` is **AUTOMATIC or unset**.
   A deliberate amount of 2025 carries a NUMBER/ACCOUNTING/etc. format and is excluded; already-PERIOD
   year cells are excluded. richText is surfaced (ADR-0002), plainText is safe-auto.
2. **Fix `fix_year_coercion`** sets `valueFormatType=PERIOD` via the shared `fix_format_copy` harness
   (TEXT preflight, refuse richText/merged/linked, H1 scale guard, readback/revert). PERIOD carries
   no `enteredIn`/`shownIn` change, so it never trips the scale guard and never rescales the value —
   fully reversible. `year-automatic-coercion` is added to `SAFE_FIX_KINDS` (now 10).
3. **Pathways** `format.year-period-auto` (safe-auto) + `format.year-period-richtext` (surfaced),
   with the consumer-propagation note: a formula year cell needs PERIOD on the cell itself, and every
   coerced consumer is flagged independently (so the scan covers propagation without special logic).

## Consequences
- Catches a high-frequency, client-visible ACFR defect that tieout never sees (it checks statement
  numbers, not year display in narrative links).
- Low-FP by construction: the AUTOMATIC-type gate + year-range gate. Even on a residual false positive
  (a genuine AUTOMATIC quantity of 2025), the PERIOD result is benign and reversible.
- Consumer propagation is handled by the scan, not by walking references: each coerced cell (source
  or formula consumer) is its own flagged finding.

## Alternatives considered
- **Detect via `calculatedValue != effectiveValue`** — rejected: sheetdata has no `effectiveValue`
  (verified live); the format-type signal is available and cleaner.
- **Surfaced-only (no auto-fix)** — rejected: the fix is a reversible, no-rescale valueFormat write
  that fits the existing safe-auto lane; richText still surfaces.
- **Widen the year range / drop the AUTOMATIC gate** — rejected: would flag deliberate amounts and
  break the cry-wolf bar (ADR-0002/0004).

## Acceptance
`python3 detectors.py` (64/64, +7 year cases), `pytest -q` (311 passed; new `FixYearCoercionTests`
+ config count→10), `node extension/content.test.js` (205/205). **Live proof:** dry-run scan of the
production "Dates" sheet flagged 25 AUTOMATIC year cells, planned `AUTOMATIC → PERIOD`, and did not
flag the 2 already-PERIOD year cells (0 false positives). No writes made.

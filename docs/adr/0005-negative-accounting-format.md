# ADR-0005 — Negative-without-parens uses ACCOUNTING valueFormat

**Status:** Accepted (2026-06-17). Extends ADR-0004 (deferred `negative-without-parens` detector).

## Context
ADR-0004 deferred `negative-without-parens` until a dirty workbook could prove the detector.
Government ACFR presentation expects parentheses for negative amounts (thousands separators,
em-dash zero, optional `$` prefix on totals). Workiva can render negatives with a minus prefix
when `useParensForNegatives` is false or when cells use plain NUMBER/AUTOMATIC formats.

Alternatives for auto-fix:
- **TEXT** — converts display to string; breaks numeric type, DL cache semantics, and formula pipelines.
- **NUMBER** with `useParensForNegatives` — can work but is not the ACFR house style; lacks ACCOUNTING
  zero-as-em-dash behavior and is inconsistent with the house formatting recipes.
- **ACCOUNTING** with `useParensForNegatives: true` — proven in prior note-schedule repairs;
  preserves numeric `calculatedValue`, thousands scaling, and `$` prefix when present.

## Decision
1. **Detector `negative-without-parens`** flags negative numeric cells whose effective format shows
   minus-prefix (`useParensForNegatives: false` + minus display). Column-homogeneous gate: safe-auto
   only when the entire column's negatives use minus-prefix; mixed columns are surfaced.
2. **Fixer applies ACCOUNTING valueFormat** via `applyFormats` on platform update:
   `valueFormatType: ACCOUNTING`, `useParensForNegatives: true`, `showThousandsSeparator: true`,
   preserving cell `prefix` and `precision` when already set. Dry-run → apply → readback → revert.
3. **Guards:** refuse richText, linked, merged; skip TEXT format cells in detection.

## Consequences
- Auto-fix aligns with ACFR presentation standards without TEXT()-wrapper bypass traps.
- Mixed-format columns stay human-reviewed — batch format paste risk is real.
- Extension dry-run shows format descriptions (`NUMBER, minus-prefix → ACCOUNTING, parens`), not color chips.

## Alternatives considered
- **NUMBER + useParensForNegatives only** — rejected: misses ACCOUNTING zero/em-dash conventions.
- **TEXT or string formula rewrite** — rejected: breaks numeric type and DL propagation.
- **Auto-fix without column gate** — rejected: partial column fixes leave worse inconsistency.

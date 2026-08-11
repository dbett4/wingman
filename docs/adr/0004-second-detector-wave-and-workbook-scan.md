# ADR-0004 — Second detector wave (label-hygiene) + workbook-wide scan

**Status:** Accepted (2026-06-15). Extends ADR-0002's limited detect/fix scope.

## Context
v1 scanned only the open sheet with three detectors (low-contrast, blank-linked, broken-ref).
Before adding presentation-consistency detectors, the live `sheetdata` response was checked on the production
workbook (ADR-0003) and candidate detectors were measured against all 16 sheets.
`effectiveFormats` turned out to be rich — full `valueFormat` (`valueFormatType`, `precision`,
`showThousandsSeparator`, `useParensForNegatives`, prefix/suffix, currency), `textFormat`
(bold, family, size, italic…), and `cellFormat` (alignment, borders, indent). But richness is
not signal. The measured prevalence decided what ships:

| candidate | live count (16 sheets) | verdict |
|---|---|---|
| **label-hygiene** (stray whitespace in text labels) | 10 real (`'Other  '`, `'Revenue Summary '`, double-spaces) | **ship** — real, invisible-on-screen, low-noise |
| number-as-text | 5 — all year headers (`'2015'`…) | **reject** — intentional text, pure false positives |
| precision-outlier (auto vs fixed decimals in a column) | 86 | **reject** — mostly invisible/benign on whole numbers; noise |
| naive format-consistency (minority format in a column) | high | **reject** — CURRENCY-`$`-on-section-totals and PERCENT variance rows are *correct* ACFR convention, not defects |
| negative-without-parens (self-relative to column) | 0 | **defer** — objective and low-noise, but a clean book can't demonstrate it; ship when a dirty book proves it fires |

Many patterns were easy to detect, but most produced too many false positives to be useful.

## Decision
1. **Add one detector — `label-hygiene`.** Flags a leading/trailing space or an internal double
   space in a cell that holds a real text label. Deliberately conservative: requires a letter
   (so spacer `" "` cells, pure numbers, and year headers never fire) and skips stored
   formula/crosswalk strings (`("` / `->`, e.g. CWUDFsStorage). Severity `low`, **surfaced** —
   never auto-written in this change. A trim write can be considered separately.
2. **Add workbook-wide scan.** `GET /scan-workbook?spreadsheetId=…` lists every sheet, runs the
   same per-sheet path, and rolls findings up per sheet + a workbook total. Reuses the proven
   detectors, so it inherits their honesty (page-bound `truncated` flag; a sheet that errors is
   reported, never silently dropped). The panel gets a **Workbook** button and a triage summary.

One cross-sheet limitation remains: **jump-to-cell** drives the
name box, which only exists for the open sheet, so off-sheet address chips show but do not
navigate (they say "open that sheet to jump"). The **fix path is pure API**, so Check-fixes /
Apply work on any sheet, open or not.

## Consequences
- Wingman now reviews a whole workbook in one pass, which matches how reviewers work.
- The detector roster grows by exactly one trustworthy class, not a noisy wave.
- The rejected candidates are recorded with their counts so they are not re-attempted blind.
- `negative-without-parens` is the documented next detector once a workbook exercises it.

## Alternatives considered
- **Ship the full valueFormat consistency wave** — rejected because the tests produced too many
  false positives (86 precision outliers, plus correct formatting conventions).
- **Make label-hygiene a safe-auto trim** — deferred: a value (not format) write needs its own
  readback/revert proof cycle on a data-bearing cell, outside the ADR-0003 empty-cell harness.
- **Parallelize workbook scan** — deferred: 16 sequential sheetdata calls are a few seconds;
  one token, predictable load. Revisit if very large workbooks feel slow.

## Update — same day: complete paging + report export
Two follow-ups landed once the scan was in use:
- **Complete paging.** The scan now follows every `@nextLink` page instead of reading only the
  first 20k-cell page. This was not cosmetic: the TB sheet had been reported "thousands of cells, clean
  (partial)" while **dozens of real label defects sat beyond page 1** (double/leading spaces on account
  labels). Verified live: page 2's `range.startRow` is **absolute** (not 0 — unlike the
  Values API, whose chunk offsets are range-relative and once clobbered dozens of rows on a live engagement), so
  each page normalizes correctly with no running counter. Capped at `MAX_SCAN_PAGES` (25 ≈ 500k
  cells); `truncated` now means "hit the cap with pages remaining," i.e. genuinely incomplete.
- **Review report.** A pure `buildReport(data)` (in the node-tested layer) turns a workbook scan
  into a Markdown defect list that the panel copies to the clipboard. This adds no Workiva writes.

## Update — 2026-06-18: drop no-evidence format-consistency noise
A later "Wave B" (`format_lane.py`) reintroduced the very detectors this ADR rejected —
`number-on-accounting-column`, `prefix-mismatch`, `missing-thousands-separator`,
`precision-mismatch`, `zero-display-mismatch` (≈ the rejected *naive format-consistency* and
*precision-outlier* rows above) — plus `junk-decimal`. They fire on essentially every numeric
cell, then look for 2-of-3 column neighbor consensus. The regression was the **failure path**:
a cell with no consensus was **kept and surfaced** with "(no neighbor format consensus)" instead
of dropped. That is exactly the cry-wolf noise this ADR designed out — a finding with no evidence
of a column house-style and no fix action, flooding the Review lane (measured: 90 of 96 flagged
cells on a representative statement column were this noise).

**Decision:** a surfaced format-consistency finding is dropped unless it carries either a per-cell
safe-auto fix **or** a column-majority gated target. Implemented as
`format_lane.drop_no_evidence_format_groups`, applied after `attach_gated_format_targets` in both
scan paths (`app.py` sheet queue, `wk_client.scan_workbook` rollup; workbook counts recomputed from
surviving groups). Every other detector and every evidence-backed format fix is untouched. The
gated-column-apply feature is preserved because gated findings are explicitly kept.
`negative-without-parens` is excluded from the drop set: its surfaced "mixed column" case is
informative evidence (ADR-0005), not no-evidence noise.

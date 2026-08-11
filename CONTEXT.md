# Wingman — Context & Ubiquitous Language

Glossary for the Wingman Workiva copilot. Terms only — no implementation detail.
Decisions live in `docs/adr/`.

## Terms

- **Wingman** — the Chrome extension + local service that sits beside a live
  Workiva tab, scans the open spreadsheet for defects, and offers safe one-click fixes.
- **The engine** — the pre-existing execution + knowledge layer (a Workiva API
  client, external check runners, and a written defect/fix knowledge base). Reused, not rebuilt.
- **The brain** — the local service that holds Workiva credentials, runs the detectors,
  and brokers writes. The extension is a thin client to it.
- **Active cell / address** — the cell the user has selected, expressed in A1 notation
  (e.g. `C42`). Read deterministically from the Workiva page DOM, never from pixels.
- **Identify** — resolving what the user is pointing at into addressable ids
  (spreadsheet id, sheet id, cell address). Never pixel-maps the canvas grid.
- **Drift** — the DOM signal Wingman depends on stopped matching its expected shape
  (e.g. a Workiva UI change). Surfaced, never silently ignored.
- **Defect** — a problem in the workbook Wingman can detect: a **blank linked cell**, a
  **low-contrast defect** (text vs background below WCAG AA 4.5:1), a
  **broken/empty reference** (a formula whose result is an error or empty), or a
  **label-hygiene defect**.
- **Label-hygiene defect** — stray whitespace in a text label (leading, trailing, or internal
  double space): invisible on screen, but real in exports. Plain-text labels are a **safe-auto**
  fix (trim the value, with readback/revert via the fixer); richText labels are surfaced for a
  manual fix. Spacer cells, numbers, year headers, and stored formula strings are excluded by
  design to keep the scan low-noise (`docs/adr/0004`).
- **Hardcode / formula-gap defect** — a value that should be formula-driven but is not. Two shapes:
  a **hardcoded face value** (a bare numeric cell where the column is formula-driven —
  `hardcoded_value.py`), and a **formula that hides a hardcode** (literal SUMIFS criteria, a numeric
  plug welded into a calc, or a `ROUND(...,-N)` display workaround — `formula_hygiene.py`). All
  surfaced — the fix is to route through the architecture (TB / mapping / Formula Reference), which is
  judgment, never an auto-write. Counted in the **formula gaps** triage bucket. Needs formula text
  (`WINGMAN_FORMULA_FETCH`), which is **on by default, visible-range-scoped** (~200-cell cap on the
  active sheet); set `WINGMAN_FORMULA_FETCH=off` to silence it, `=full` for the full 500-cell cap.
  See `docs/adr/0006` and `docs/adr/0007`.
- **Workbook scan** — scanning every sheet in the open workbook in one pass (vs the single open
  sheet), rolled up per sheet with a workbook total. Reuses the per-sheet detectors and per-sheet
  error reporting (`docs/adr/0004`).
- **Complete scan / paging** — a sheet scan follows every `@nextLink` page, so a large sheet is
  read in full (not just the first 20k-cell page). `truncated` now means "hit the page cap with
  pages still remaining" — genuinely incomplete — not merely "spans multiple pages."
- **Triage summary** — the one-line count above a scan: issues · high · fixable · review.
- **Review report** — the markdown defect list **Copy report** puts on the clipboard: workbook
  summary then a section per sheet with grouped findings, for handoff to a reviewer.
- **Blank linked cell** — a cell whose linked source should supply a value but renders empty. The
  per-cell `detect_blank_linked` stays inert on live scans (sheetdata has no `isLinked`); it is
  **superseded by the link lane** below, which works at the correct (block-level) granularity. See
  `docs/adr/0008`.
- **Link lane / range-link awareness** — `GET /content/tables/{tid}/rangeLinks` returns **block-level**
  links (one rectangular range = one link; verified live), not per-cell flags. `link_lane.py` marks
  which scanned cells fall inside a link range (`linkRole`/`linkId` — the link map) and surfaces an
  **empty-linked-range** finding for any link whose block is **entirely blank** (the destination
  document table would render empty), but only on a complete (non-truncated) scan so a blank spacer
  cell inside a populated block is never flagged. Surfaced-only (re-link/populate is judgment).
  Gated by `WINGMAN_LINK_FETCH` (default on; one read-only GET per scan). `docs/adr/0008`.
- **Year-coercion defect** — a 4-digit year (1900–2100) stored as a number under AUTOMATIC value
  format, which Workiva renders with a thousands separator (`2025` → `2.025` / `2,025`) in document
  destination links. Safe-auto fix: set `valueFormatType=PERIOD` (reversible, no value rescale).
  Fires only on AUTOMATIC/unset format (deliberate NUMBER/ACCOUNTING amounts and already-PERIOD year
  cells are excluded); richText is surfaced. `docs/adr/0009`.
- **Scale guard** — Wingman refuses any valueFormat write that would change `enteredIn`/`shownIn`,
  because Workiva lossily + irreversibly rescales stored values on a scale change. A neighbor-format
  copy on a different scale is surfaced, never auto-applied. `docs/adr/0007`.
- **Currency-symbol guard** — Wingman strips `currencySymbol` from planned forward valueFormat writes
  unless `showCurrencySymbol` is explicitly true, preventing hidden copied symbols from turning into
  displayed `$` body-row defects. `docs/adr/0010`.
- **Safe-lane fix** — a fix Wingman may auto-apply after explicit confirm because it is
  reversible by re-writing the captured before-state and carries no data-integrity risk.
- **Surfaced-not-fixed** — a defect Wingman reports but will not write, because the fix
  is UI-only, judgment, or RED. The panel says "needs Workiva UI" — it never fakes a fix.
- **Dry-run** — the previewed diff of a proposed write, shown before anything is applied.
- **Readback** — re-reading a cell after a write and comparing to what was written;
  on mismatch Wingman re-writes the before-state. This is the only "undo" available.
- **RED write** — a write that is irreversible or structurally risky (blank-DL re-link,
  merge/unmerge, structural/DL ops). Hard-stop confirmed, never auto-applied.
- **Batch apply** — applying safe-auto fixes to many cells in one confirmed action (sheet-wide
  "Fix all safe" or column-scoped "Apply column B (N)"). Each cell still runs dry-run → confirm
  → apply → readback; bulk UX does not skip per-cell preflight.
- **Gated column format** — a user-confirmed valueFormat paste for surfaced (yellow) format
  defects when neighbor consensus failed at scan but the reviewer has homogenized the column.
  Wingman computes a column-majority format preview; the user confirms before any write. Not
  auto-publish and not in the safe-auto lane until rescan promotes cells.
- **Activity log** — every scan and fix outcome appended as one JSON line under
  `WINGMAN_LOG_DIR` (default `~/.wingman/logs`, OUTSIDE the repo). PII-safe by contract:
  counts by kind/lane/severity, fix STATUS, A1 coordinates, timings, and a pseudonymous
  sheet hash only — never cell values, formula text, or raw spreadsheet IDs
  (`server/wingman_log.py`). Failsafe: logging never breaks a scan or a fix.
- **Improvement digest** — the analysis mined from the activity log (`server/wingman_log_digest.py`,
  also `GET /digest`): detector volume by kind, fix health per kind (applied vs
  reverted/refused), and **flags** — kinds whose revert or refused rate crosses a threshold,
  i.e. the detectors/fixers most worth fixing next. This is the loop by which logs improve the tool.

# ADR-0008 — Document range-link awareness (link lane)

**Status:** Accepted (2026-06-19). Resolves the `detect_blank_linked` open decision (#2) from
CONTEXT.md. Backlog source: internal improvement backlog.

## Context
`detectors.detect_blank_linked` has been dead on every live scan: `wk_client.normalize_sheetdata`
hardcodes `isLinked=False` because spreadsheet `sheetdata` carries no link metadata — links are
doc-side. The improvement backlog proposed wiring `GET /content/tables/{tid}/rangeLinks` to revive it.

**Live probe (production ACFR preset, 2026-06-19) changed the design.** The endpoint returns
**block-level** links, not per-cell flags: 123/224 sheets carry `type:"source"` entries shaped
`{id, type, revision, table, source:{range:{startRow,stopRow,startColumn,stopColumn}}}` — the range
**is** present, but one link covers a whole rectangular block (e.g. a statement's `F5:R112`).

That granularity makes the per-cell detector the wrong tool: a 24-row linked statement block
includes blank spacer rows, so flagging every blank cell would create many false positives. A blank cell inside a populated block is not a defect; only a
wholly-empty block is.

## Decision
A new **link lane** (`link_lane.py`) operates at the link level, fed by
`wk_client.fetch_range_links` (paginates `@nextLink`, fail-open `[]` on error):

1. **Mark, don't flag per-cell.** `analyze_links` stamps `linkRole`/`linkId` on every scanned cell
   inside a link range (the link map, for the extension and future DL-source detectors) but does
   **not** set `isLinked` — so `detect_blank_linked` stays inert by design, no per-cell noise.
2. **`empty-linked-range` finding (surfaced).** Emit one finding per link whose block has **zero
   populated cells**, and only when the sheet scan was **not truncated** (an off-page block is never
   mis-reported as empty). A wholly-blank source block ⇒ the destination document table renders
   empty — a real, unambiguous, low-frequency defect. Never auto-fixed (re-link/populate is
   judgment; no reliable API undo).
3. **Gating.** `WINGMAN_LINK_FETCH` (default **on**) — one extra read-only GET per sheet scan when a
   content table id is available; `=off` disables it. `scan_sheet` returns a 7th tuple element
   `link_meta`; `/api/queue` attaches it as `link_fetch` (counts + per-link summaries).
4. **Pathway.** `diagnose_pathways` maps `("empty-linked-range","surfaced",None)` →
   `link.empty-linked-range` with the block-level caveat documented as a trap.
5. **Publish-state tranche.** `classify_publish_state` groups range-link rows by link id and
   compares source/destination `revision` values when both sides are present. Matching revisions are
   `published`; mismatched revisions emit one surfaced `unpublished-linked-range` finding with the
   source/destination revision evidence and the next action “publish links before export.” Source-only,
   destination-only, and unknown rows are reported as incomplete link data. They are not written
   automatically or treated as ready without destination readback.

## Consequences
- `detect_blank_linked` (per-cell) is formally superseded for live scans by the link-level check;
  it stays in place (harmless, still unit-tested) for synthetic/destination cells.
- Wingman gains a **link-source map** (`linkRole`/`linkId` on cells, `link_fetch` meta) — the
  foundation for link-health and the DL-source-numeric-formula detector.
- The link check now includes a publish-state classifier: when the same range-link id has source and
  destination rows with mismatched revisions, Wingman surfaces `unpublished-linked-range` as a
  review item. It never publishes links automatically.
- Verified low-noise on live data: a populated `F5:R112` block (698/698 cells populated) produced
  **0** findings while correctly mapping all 698 cells.

## Alternatives considered
- **Per-cell revival (set `isLinked` from the feed)** — rejected because block-level ranges would
  flag every blank spacer row.
- **Default `WINGMAN_LINK_FETCH` off** — rejected: verified, read-only, one GET/scan, high value;
  the off-switch covers the cost-sensitive case.
- **Flag any blank cell inside a link block** — rejected for the same noise reason; only a wholly-
  empty block is unambiguous.

## Acceptance
`pytest -q` (307 passed; new `test_link_lane.py`), `python3 link_lane.py` self-test (6/6),
`python3 detectors.py` (57/57), `node extension/content.test.js` (205/205). Live end-to-end:
`scan_sheet` on "Gov-Wide - Net Position" → `link_fetch` meta populated, 0 false `empty-linked-range`.

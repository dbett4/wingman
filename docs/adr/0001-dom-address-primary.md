# ADR-0001 — Read the active cell from the DOM, not from vision

**Status:** Accepted (2026-06-15). Supersedes CEO-plan decision D16; recorded as D17.

## Context
Wingman needs to know which Workiva cell the user is pointing at. The Workiva grid is
canvas-rendered, so the cell values cannot be read from the DOM. The original plan
assumed a vision model (point-grounding + name-box OCR) would supply the address, and
funded a fine-tune flywheel for it.

A live test on real Workiva (2026-06-15, this machine) measured three things:
- Live name-box OCR: **12%** corrected accuracy (96-97% on clean synthetic renders did
  not transfer to the live gray-pill render).
- Point-grounding on the canvas: ~45-60% even fine-tuned, plus a recurring drift-upkeep cost.
- The active-cell address is present in the page DOM as `div.dt-formula-cell-indicator`
  (app chrome, not the canvas), updated on every grid click, readable in **~2-3ms at ~100%**.

This was later confirmed live from a packed extension content script: a real Workiva page
stamped `data-copilot-loaded` once the extension was loaded.

## Decision
The active cell address is read from `div.dt-formula-cell-indicator` in the page DOM via a
content-script MutationObserver. Point-grounding is shelved. OCR plus grammar correction is
retained only as a last-resort fallback. Wingman reads the address from the DOM, checks that
the selector still behaves as expected, and validates the cell through `read_cells` before a
write. Cell *values* remain API-only.

## Consequences
- The vision subsystem shrinks dramatically: no fine-tune, no flywheel, no SAM, no heavy VLM.
- A new dependency on an undocumented Workiva class (`dt-formula-cell-indicator`) — mitigated
  by a drift guard that alerts if it returns null or changes shape on a Workiva deploy.
- Vision's remaining role is **defect detection** (things the API is blind to), not addressing.
- Cross-origin iframes (multi-pane / linked views) can hide the indicator; v1 is
  spreadsheet-only, top-frame only, and falls back to a typed-A1 prompt.

## Alternatives considered
- **Vision point-grounding as primary** — rejected: ~45-60% ceiling on the canvas, recurring
  drift-maintenance contract, and the click already yields the address for free.
- **Name-box OCR as primary** — rejected: 12% live accuracy; kept only as fallback.

# ADR-0002 — v1 auto-writes only plain-cell contrast; everything else is surfaced

**Status:** Accepted (2026-06-15).

## Context
Wingman's three v1 detectors find: blank linked cells, low-contrast cells, and
broken/empty references. An adversarial review confirmed against the live API that
the detectable set and the *safely auto-fixable* set barely overlap:
- **Blank linked cell** → fix is re-link/re-publish = the plan's #1 data-integrity risk
  (RED, no reliable undo).
- **Low-contrast** → on a **richText** cell the color write 404s / silently no-ops
  (UI-only); only a **plainText** cell takes a safe font-color write.
- **Broken/empty ref** → usually judgment (stale TB, mapping), not a mechanical fix.

`value.type` is a top-level field (`richText` | `plainText`), so the richText refusal is a
clean one-field gate. The `sheetdata` endpoint exposes `fontColor` + `backgroundColor` as
hex per cell, so plain-cell contrast is both detectable and fixable through the API.

## Decision
v1 auto-writes exactly one fix: **low-contrast on a plainText cell**, corrected by
auto-computing the *minimal* font-color adjustment that clears WCAG AA (4.5:1) against the
cell's background, shown as a before/after dry-run (swatch + ratio) the user confirms.
Every such write is gated by: `type==plainText` pre-check, full pre-read of the prior
color, apply, readback, revert-on-mismatch.

All other findings are **surfaced-not-fixed**: blank linked cells, richText contrast, and
broken references are reported with "needs Workiva UI" / "needs review" — never auto-written.

## Consequences
- Auto-fix covers a small subset of findings. Most styled ACFR text is richText, so the
  common result is a review item rather than a one-click fix.
- The v1 write path never changes financial values or links.
- Blank linked cells remain a possible later feature after they have a structural pre-screen.

## Alternatives considered
- **Fix to a fixed ink token** — rejected: flattens a deliberately colored cell to generic ink.
- **Surface contrast too (no v1 write)** — rejected: contradicts the Scan+Fix scope.
- **Blank-DL auto-fix now** — deferred because re-linking has no reliable undo.

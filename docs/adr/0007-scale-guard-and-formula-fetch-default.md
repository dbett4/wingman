# ADR-0007 — Scale-change write guard + formula-fetch default-on (scoped)

**Status:** Accepted (2026-06-19). Hardens the write path (ADR-0002/0003/0005) and resolves the
`WINGMAN_FORMULA_FETCH` open decision from ADR-0006. Backlog source: internal improvement backlog.

## Context
Two findings from a 2026-06-19 Workiva knowledge-harvest study, both verified against
the live code:

1. **No scale-change guard on valueFormat writes.** Changing a cell's `enteredIn`/`shownIn`
   triggers a **lossy, irreversible rescale** of stored numeric values — Workiva preserves the
   *displayed* value, not the raw value (a documented valueFormat-flip incident that silently
   corrupted formulas across dozens of statement sheets). Every neighbor-consensus format-copy fix
   (`format_lane.neighbor_*_consensus`) returns the neighbor's **whole** `valueFormat` via
   `dict(vf)`, so a neighbor on a different scale would carry its `enteredIn` into the write and
   corrupt the target cell. No guard existed in `fixer.py`/`format_lane.py`/`wk_client.py`.

2. **`WINGMAN_FORMULA_FETCH` defaulted off**, making five detectors dead code on a stock install
   (`formula-evaluates-blank`, `hardcoded-face-value`, `hardcoded-text-criteria-in-formula`,
   `hardcoded-constant-in-formula`, `round-wrapper-workaround`). That wave targets the
   highest-frequency ACFR correction class, and every new formula detector on the improvement backlog
   depends on it. The cost concern was the extra content-cells GET on large sheets.

## Decision
1. **Scale-change write guard.** `fixer.scale_change_block(before_vf, planned_vf)` returns a
   refusal reason when the planned write would change `enteredIn` or `shownIn` from the cell's
   current scale; `fix_format_copy` and `fix_negative_parens` return
   `status: refused` (shown for review in Workiva) before any write. Only a scale field **present** in
   the planned format is compared — an omitted field is left untouched by `applyFormats`, so it is
   safe — and absent values are normalized to the platform default (`ONES`). This is a hard
   never-rescale gate, in the same family as `preflight_no_text_wrapper` (W21 trap).
2. **Formula-fetch default-on, visible-range-scoped.** `WINGMAN_FORMULA_FETCH` becomes
   tri-state via `wk_client.formula_fetch_mode()`:
   - unset / unrecognized → **`scoped`** (on): formulas fetched for the active sheet's used range up
     to `FORMULA_FETCH_SCOPED_CAP` (default 200) — one extra content-cells GET.
   - `off`/`0`/`false`/`no` → **`off`**: silent (cost escape hatch).
   - `on`/`1`/`true`/`full` → **`full`**: full `FORMULA_FETCH_CAP` (default 500).
   `formula_fetch_cap()` returns the effective cap; the scan chip (`build_formula_fetch_meta`) and
   `GET /api/status` report `mode` + `cap`.

## Consequences
- Format-copy and negative-parens fixes can no longer silently rescale a cell; a cross-scale
  neighbor consensus becomes a review item instead. The common case (neighbors on the same scale,
  or targets that carry no scale field) is unaffected.
- The formula-hygiene wave is live out of the box at ~1 extra GET per scan; cost-sensitive users
  opt out with `WINGMAN_FORMULA_FETCH=off`, and `=full` restores full-sheet coverage.
- No change to `SAFE_FIX_KINDS`; both changes are guard/coverage, not new auto-fix lanes.

## Alternatives considered
- **Strip `enteredIn`/`shownIn` from copied formats instead of refusing** — rejected: a copy that
  needed a scale change is unsafe to apply automatically; leave it for human review.
- **Keep formula fetch off; document louder** — rejected: documentation does not make dead
  detectors fire; the scoped default makes them useful at bounded cost.
- **Flip straight to full-sheet default-on** — rejected: unbounded latency/cost on large sheets;
  the visible-range scope covers the reviewer's context cheaply.

## Acceptance
`python3 -m pytest -q` (294 passed), `python3 detectors.py` (57/57),
`python3 formula_hygiene.py` (39/39), `node extension/content.test.js` (205/205). New tests:
`ScaleChangeGuardTests` (test_fixer.py), `FormulaFetchModeTests` (test_wk_formula_enrich.py).

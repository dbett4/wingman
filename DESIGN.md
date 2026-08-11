# Wingman Design Contract

**Register:** product UI / browser extension panel  
**Posture:** high-trust, low-distraction Workiva operator surface

## Design goals

1. **Readable while Workiva remains primary.** The panel supports workbook review; it never becomes the page.
2. **Proof before confidence.** Every action state should expose what happened, what was guarded, and what can be copied.
3. **Compact but touch-safe.** Dense copy is acceptable; action targets still need at least 40px hit area.
4. **Freshness is visible.** Reports and copied packets must show generated time, scope, service state, and stale warnings when applicable.

## Surface rules

| Element | Rule |
| --- | --- |
| Panel shell | Clamp width for Workiva side-by-side use; avoid body horizontal overflow; keep internal scrolling inside the panel. |
| Sticky regions | Header/status and primary action strip may stick; reset/collapse must remain reachable at small heights. |
| Buttons | Minimum 40px hit area; `:active` scale may be subtle (`0.96` to `0.975`), never exaggerated. |
| Numbers/timestamps | Use tabular numerals in receipts, counts, timestamps, and ranges. |
| Headings | Use short operational labels; body text may use `text-wrap: pretty` where supported. |
| Status color | Color reinforces text, never replaces it. Guarded/error states need explicit labels. |
| Motion | Only clarify state changes; no decorative entrance choreography over Workiva. |

## Action states

Use this lifecycle unless a route has a more specific state machine:

`idle → scanning/running → accepted → copied/applied → stale/error/guarded`

Required copy fields for receipts:

- action name
- generated timestamp
- Workiva target identity when available
- route/gate used
- result (`accepted`, `guarded`, `copied`, `failed`, `stale`)
- redacted evidence path/range/run id when available

## Proof checklist

Run this checklist for visible panel changes:

- [ ] 390px-wide viewport: no body overflow, controls wrap or scroll internally.
- [ ] Desktop Workiva viewport: panel does not cover critical workbook controls by default.
- [ ] All primary controls have >=40px hit area.
- [ ] Sticky header/footer remain reachable after scroll.
- [ ] Copy/report includes freshness metadata.
- [ ] Guarded routes still deny unauthenticated/origin-mismatched calls.
- [ ] Screenshot or DOM metric receipt saved with the change.

## Anti-patterns

- Generic chat-panel chrome.
- Color-only status chips.
- Hidden mutations behind friendly copy.
- Long unstructured reports without path/range evidence.
- `transition: all`; animate exact properties only.
- Displaying credential values or raw tokens in setup/status output.

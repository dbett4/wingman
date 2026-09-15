# Wingman in two minutes

Run `python3 server/demo.py` with Python 3.11 or newer, then open local port 8771.
No pip install, `.env`, OAuth credentials, or unpacked extension is required.
In an Amp orb, use `amp orb services ensure` and its Wingman demo portal.

## Show the review, not just the findings count

1. **Inspect.** The panel opens without a scan. Select B7 and choose **Inspect
   selected cell**: stored formula `=SUM(B3:B6)+12500`, seeded result `3746500`,
   and native ACCOUNTING format appear separately. Try C8 (zero) and B1 (blank).
   Expand **References & links** for B7's `B3:B6` reference, `12500` numeric literal,
   and containing source range `B3:C7`. B8 keeps `#REF!` unresolved. On Review notes,
   inspect B2: its cell link points to source cell `D6`; a separate range link
   covers the table and points to `C5:D8`. Expand either link for its IDs and revision.
   Choose **Read source values** to fetch B7's four referenced amounts or B2's
   exact cell source and published source range. Source `D6` deliberately contains
   fiscal year `2024` while selected B2 shows `2025`; neither is automatically
   declared correct. Inspect Review notes B3: it still displays `Accrual`, but its
   cell link is disconnected. The covering range link does not prove this cell's
   connection, and Wingman does not infer a source cell from range offsets.
   Reads are limited to 100 cells across 10 ranges, never silently sampled.
   The inspector makes no edits, performs no reconciliation, and applies no
   client policy. Changing the cell, sheet, or workbook clears its evidence.
2. **Scan.** Open Scan and click **Scan** for Statement of activities. The seven findings
   include three safe fixes, three formula/reference review items, and a zero-display
   convention review item. The existing source-formula detector flags B7 because
   it is a formula in a range-link source; the fixture establishes no policy that
   makes this wrong. Inspect does not inherit that judgment. These review items
   are heuristics, not proven accounting errors.
3. **Review B2.** The grid shows `2,025`; the formula bar shows stored value `2025`.
   The year detector proposes PERIOD formatting. C2 is already PERIOD and is not
   flagged. Amounts below it must not cause competing accounting-format fixes.
4. **Preview A4.** Open Label hygiene and choose **Check fixes**. The preview removes
   trailing/double whitespace. The simulator's write trace stays empty.
5. **Apply.** Choose **Apply**. The actual fixer performs its pre-read, write, and
   readback. A4 changes to `Intergovernmental revenue`; the trace records one write.
6. **Demonstrate failure.** Reset the workbook, choose **Make next write mismatch**,
   open Scan and scan again, preview A4, and Apply. The simulator stores the wrong text once. The real
   fixer detects it and restores the original. The trace shows both writes. The
   integration test independently compares the entire restored workbook with its
   before-state; the live fixer's restore path itself does not yet do that check.
7. **Keep judgment visible.** B7 contains `=SUM(B3:B6)+12500`, while B8 is a broken
   reference. Neither is offered as a safe automatic fix. Review notes is a clean
   second sheet. Download a fresh review packet or scan all sheets in Workbook.

Applying a fix does not automatically rescan the panel's cached findings. Use Scan
to update them. Packet download always runs a fresh workbook scan.

## What is real and what is simulated

| Layer | Execution |
| --- | --- |
| Review UI | `wingman-core.js`, `wingman-inspector.js`, and `wingman-panel.js`; demo-only sizing and disabled live-only controls |
| Browser transport | `demo/demo.js` substitutes same-origin fetch for Chrome background messaging and selection for debugger-driven navigation |
| Service | `app.Handler` inspect, scan, preview, apply, and packet routes; demo wrapper restricts accessible routes |
| Inspect path | Metadata lookup, two pairs of single-cell sheetdata/content HTTP reads, formula tokenization, table range links, revision-specific source-range lookup, and cell destination-link/source-anchor lookup; opt-in bounded source-cell reads; no detectors or writes |
| Read/scan path | Real HTTP client, two-page sheetdata reads, formula/type enrichment, detectors, grouping, diagnosis |
| Fix path | Real fixer and account/workbook gates, using a synthetic identity and a fictional-only allowlist |
| Upstream | In-memory HTTP simulator supporting only the fixture's endpoints; seeded formula results, immediate writes |
| Fault injection | One incorrect value/format write, followed by a successful restore request |

The fixture simulates source/destination metadata and a fixed published source
table. No source values or destination updates propagate. Not simulated: actual Workiva DOM changes, OAuth, live range links, rate limits,
eventual consistency, async operation jobs, collaborative edits, process-crash
recovery, rich-text editing, vision, external checks, publishing, or PDF proof.
The demo is **not evidence of live integration compatibility or measured accuracy**.

The content simulator uses Workiva's documented `rawValue` and nested
`value.formula.calculatedValue` / `effectiveValue` shape. Formula text and result
are distinct; raw numeric-looking strings are not asserted to be numeric types.
Source formula ranges (including uniquely resolved same-workbook sheet names) use
the selected cell's content revision. Incoming range links use their reported
published revision. Cell-level destination links use the revision in the cell's
link reference, then follow the source anchor at its recorded revision. A
connected status does not establish correctness or freshness. Disconnected links
can retain their last published value; unavailable metadata is not disconnection.
Missing, mismatched, incomplete, or denied source reads never fall back to latest.
If the origin's content revision or cell changes during the read, all evidence
is discarded. This is still **not an atomic snapshot of cells and links**.
Named, external, dynamic, and unbounded references remain explicit; inline
rich-text links, non-table source content, and the full dependency chain are not
traversed. Source addresses are evidence, not cross-sheet navigation controls.

The zero-display finding currently receives a column-majority review target even
though the fixture does not establish an em-dash convention. This is an evaluation
candidate, not something to auto-apply; the demo deliberately leaves the real
detector output visible rather than filtering it to make a cleaner presentation.

## Isolation and lifecycle

- The demo is a separate entry point, never a flag that relaxes the live launcher.
- All Workiva region URLs point to its loopback simulator. A process-wide audit
  hook rejects outbound socket connections anywhere else, and proxy variables do
  not reroute its HTTP requests. It does not resolve OAuth credentials.
- Every browser session gets an opaque HttpOnly cookie and a separate workbook.
  Plain HTTP uses SameSite=Strict; HTTPS behind the portal uses Secure, SameSite=None,
  Partitioned cookies to support embedded previews. Cross-origin preflights are denied.
- Sessions are bounded to 128 and removed after an hour idle; browser cookies last
  an hour from page load. Tabs sharing the same cookie share the same demo workbook.
- Requests within one session are serialized for deterministic demonstration.
  **That does not add concurrency protection to the live service.**
- Reset only touches that session's fictional state. Restart clears all sessions.
- Ordinary logs/receipts go to a temporary directory, removed on orderly shutdown.
  Only the explicit browser download creates a user-facing review file.

## Repeatable checks

```bash
python3 -m pytest server/test_demo.py -q
python3 -m pytest server/test_inspector.py -q
python3 -m pytest scripts/test_demo_browser.py -q
```

The browser command requires `agent-browser` 0.37.1 and Chromium (preinstalled in
Amp orbs). These commands manage disposable demo processes. HTTP tests check exact
cell states and untouched neighbors for all three safe fixes, pagination, session
isolation, readback/recovery, packet redaction, and disabled routes. The browser
tests exercise the real panel, check the narrow layout, and inject delayed service
replies to test context invalidation and keyboard focus. Those injected replies
test the controller only, not live Workiva consistency. CI runs all three.

For a résumé walkthrough, say “reproducible simulated integration,” not “production
accuracy” or “guaranteed undo.” Publish time-saved or accuracy figures only after
the evaluation and pilot milestones in [the roadmap](roadmap.md).

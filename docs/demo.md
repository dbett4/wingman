# Wingman in two minutes

Run `python3 server/demo.py` with Python 3.11 or newer, then open local port 8771.
No pip install, `.env`, OAuth credentials, or unpacked extension is required.
In an Amp orb, use `amp orb services ensure` and its Wingman demo portal.

## Show the review, not just the findings count

1. **Scan.** The panel opens and scans Statement of activities. The six findings
   include three safe fixes, two formula/reference review items, and a zero-display
   convention review item. The latter is a heuristic, not a proven accounting error.
2. **Inspect B2.** The grid shows `2,025`; the formula bar shows stored value `2025`.
   The year detector proposes PERIOD formatting. C2 is already PERIOD and is not
   flagged. Amounts below it must not cause competing accounting-format fixes.
3. **Preview A4.** Open Label hygiene and choose **Check fixes**. The preview removes
   trailing/double whitespace. The simulator's write trace stays empty.
4. **Apply.** Choose **Apply**. The actual fixer performs its pre-read, write, and
   readback. A4 changes to `Intergovernmental revenue`; the trace records one write.
5. **Demonstrate failure.** Reset the workbook, choose **Make next write mismatch**,
   preview A4 again, and Apply. The simulator stores the wrong text once. The real
   fixer detects it and restores the original. The trace shows both writes. The
   integration test independently compares the entire restored workbook with its
   before-state; the live fixer's restore path itself does not yet do that check.
6. **Keep judgment visible.** B7 contains `=SUM(B3:B6)+12500`, while B8 is a broken
   reference. Neither is offered as a safe automatic fix. Review notes is a clean
   second sheet. Download a fresh review packet or scan all sheets in Workbook.

Applying a fix does not automatically rescan the panel's cached findings. Use Scan
to update them. Packet download always runs a fresh workbook scan.

## What is real and what is simulated

| Layer | Execution |
| --- | --- |
| Review UI | Existing `wingman-core.js` and `wingman-panel.js`; demo-only sizing and disabled live-only controls |
| Browser transport | `demo/demo.js` substitutes same-origin fetch for Chrome background messaging and selection for debugger-driven navigation |
| Service | Existing `app.Handler` scan, preview, apply, and packet routes; demo wrapper restricts accessible routes |
| Read/scan path | Real HTTP client, two-page sheetdata reads, formula/type enrichment, detectors, grouping, diagnosis |
| Fix path | Real fixer and account/workbook gates, using a synthetic identity and a fictional-only allowlist |
| Upstream | In-memory HTTP simulator supporting only the fixture's endpoints; seeded formula results, immediate writes |
| Fault injection | One incorrect value/format write, followed by a successful restore request |

Not simulated: actual Workiva DOM changes, OAuth, live range links, rate limits,
eventual consistency, async operation jobs, collaborative edits, process-crash
recovery, rich-text editing, vision, external checks, publishing, or PDF proof.
The demo is **not evidence of live integration compatibility or measured accuracy**.

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
python3 -m pytest scripts/test_demo_browser.py -q
```

The second command requires `agent-browser` 0.37.1 and Chromium (preinstalled in
Amp orbs). Both commands manage disposable demo processes. HTTP tests check exact
cell states and untouched neighbors for all three safe fixes, pagination, session
isolation, readback/recovery, packet redaction, and disabled routes. The browser
test exercises the real panel and checks the narrow layout. CI runs both.

For a résumé walkthrough, say “reproducible simulated integration,” not “production
accuracy” or “guaranteed undo.” Publish time-saved or accuracy figures only after
the evaluation and pilot milestones in [the roadmap](roadmap.md).

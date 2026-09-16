# Wingman product completion goals

Set September 15, 2026, for the revamp. This is the completion contract for a
**polished private v1 used during real Workiva reporting review**, not a claim
that the current prototype is ready. Store publication is a separate decision.

## Product promise

**Understand a reported value, follow its evidence, decide what needs attention,
and make only confirmed, verifiable repairs without leaving the review workflow.**

The primary user is a financial-reporting reviewer moving among supporting
spreadsheets, statement cells, and linked report tables. The product must help
answer five questions: Where am I? What am I looking at? Where does it come from?
What needs my judgment? What changed since the last review?

Use the existing panel and service where they meet these goals. Rebuild deficient
flows rather than rewriting working modules solely to change the stack. Default
inspection is read-only; automation never substitutes for accounting judgment.

## Baseline: useful prototype, seven completion goals still open

Implemented locally: credential-free fictional demo; selected-cell inspection;
separate stored content, calculated result and native format; bounded formula and
link-source reads; single-step cell-link tracing; stale-response rejection; and
existing scan/preview/apply workflows. Connection diagnosis now separates service
authorization, backend credential presence and untested Workiva access, with
explicit recovery states and redacted diagnostics. See [demo.md](demo.md) for
the fictional demo's boundaries.

The commissioning baseline passed 600 backend tests; thirteen NumPy-dependent
vision tests were skipped. The subsequent local setup-page candidate passed 304
extension checks, nine worker/setup-controller tests, three demo browser workflows
and two installed-extension connection cases. The installed test uses
the real MV3 worker and HTTP handler, but fictional credentials and a substituted
page, not a Workiva session. Dark/light and narrow connection views were inspected.
These results do not prove live compatibility, detector accuracy, write safety under
concurrent edits, or usability by a new reviewer. No goal below is closed by this baseline.

## G1. A reviewer can install, connect and recover without developer help

Why: a polished panel is not a usable product if the service connection is opaque.

**Open.** The local diagnostic/recovery slice is implemented and tested, including
missing configuration, rejected service tokens, missing backend credentials, network
loss, timeout and late replies. The owner approved private commissioning on September
15: a read-only service now runs on the authoritative VPS and the Mac runs only an
encrypted loopback SSH forward. Paired authorization, bad-token rejection, repair/export
refusal and separate backend/tunnel restarts passed over that real route. The service
initially had no Workiva credentials or external egress. On September 16 the owner
approved a private update and reads of an older implementation sandbox, excluding
audit clients. The deployed service now admits only the selected August 8 RCTC
document, its companion spreadsheet, their table IDs and one verified destination
link. Root-only credential files, account-pin checks, missing-scope refusal,
redirect refusal and the commissioning diagnostic passed independent security
review. Workiva writes remain disabled. The live API-host allowlist uses verified
IPv4 addresses; rotating IPv6 answers caused the first read attempt to time out.

The matching package is installed in the owner's Mac Chrome 153 profile. Its
standalone Connection & setup page is available on
first installation, through Extension options and as the toolbar's no-panel fallback.
The real setup button and a developer-assisted options-context request exercised
the installed MV3 broker, SSH forward, service and old RCTC cell read; a malformed
Apply request was refused. The rendered setup page was inspected. The Mac Workiva
session is signed out; its in-page journey remains untested. With the owner's
separate temporary-install approval, the authenticated VPS browser then exercised
the actual content script on the old RCTC report: table choice, D12 inspection,
following J18 and returning all passed. The temporary extension and pairing copy
were removed afterward; no browser/service restart or session transfer was needed.
Clean-machine onboarding remains untested.
[Private installation](../extension/PACKAGING.md) and
[deployment controls and rollback](../deploy/README.md).

Cancellation/late replies, missing callbacks, redacted failures, keyboard focus and
narrow setup layouts have fictional regression coverage. Chrome does not support optional
`debugger` permission, so navigation/capture permission reduction remains unresolved
rather than silently disabling those features.

- Provide one guided setup path, readable connection status, and a safe diagnostic
  action. Distinguish service offline, credentials missing, access denied,
  unsupported page, and extension reload; give the actual recovery action.
- Resolve the browser-to-service topology first. The current extension assumes
  localhost; the owner's production backend must run on the authoritative VPS.
  The browser remains the control surface, with no competing Mac production service.
  Choose and validate the authenticated connection before changing deployment or
  credential configuration. Do not assume a hosted endpoint alone solves it.
- Show the workspace, file and selected location. File identity does not establish
  client, fiscal period or working-copy authority; require explicit review context
  where a check depends on those facts.
- Minimize and explain extension permissions. No credentials in logs, exported
  reports or the repository, and no manual JavaScript editing to configure access.

Done when a new reviewer with provisioned access reaches a first real inspection
within **10 minutes**, using only the shipped guide; restart, browser reload,
expired access and network loss have tested recovery paths. This is a proposed
acceptance target, not a measured result. A clean-machine setup and an authorized
Workiva sandbox test are required. Blocks live acceptance of G2–G5 and G7.

## G2. A reviewer can follow a value to its source and back

Why: a table-level link or a green status does not explain an individual amount.

**Open.** The private build follows table-cell evidence on request, with a
revision-bound return trail, retained origin value, cycle detection and a 10-step
limit. Fictional two-step chains pass through both the demo and the installed
MV3 worker/handler; return performs no new reads or writes. Wrong revisions,
missing evidence, cancellation, timeout and stale replies have regression coverage.
Historical views deliberately omit native format, range-link listings and
named-sheet resolution when the source workbook identity is unknown. This is
revision-bound evidence navigation; opening a native sheet is a separate action.
General cross-workbook discovery, automatic native cell selection and new-reviewer
acceptance remain open.

The local candidate adds **Open source sheet** for a location whose sheet/table
membership matches the recorded revision. It opens today's sheet in a separate tab,
not the historical cell; values and the return trail remain in the original tab.
The actual installed MV3 test exercised a cross-sheet formula reference, keyboard
opening, no opener access, and return without new API reads or writes against
fictional pages. Missing/mismatched locations have no link. Light/dark and 390px
renders were inspected; source values precede navigation and keyboard focus is visible.
The owner subsequently approved a temporary VPS Chrome installation. The real
Workiva report offered two Statement of Activities tables; the chosen original
table's D12 returned 0, then J18 returned the retained SUMIFS formula and calculated
0 at its recorded revision. Return restored D12 without additional observed API
requests or changing the report URL. The extension was unloaded, its temporary
package/pairing copy removed and its own tabs closed. The browser and backend
services were not restarted. This proves the bounded live in-page journey, not
accounting accuracy or new-reviewer acceptance. Mac sign-in is not a VPS prerequisite.

That trial exposed a missing path: a report-origin source request had no workbook
candidate, so it correctly offered no native sheet link. The follow-up adds an
optional **Companion workbook URL** beneath **Locate source sheets** in the
table chooser. Only a current sheet URL on the report's site and in its workspace
is accepted; only the workbook ID is sent, on an explicit source-follow request.
The backend still must prove source-table membership at the recorded revision.
The candidate survives rereads but clears on a new choice or context. Installed
fictional tests cover wrong site/workspace/history without a read, unverified
membership without a link, verified membership with keyboard opening and return,
and 390px/light/dark layouts.

The owner-approved follow-up VPS trial passed on the same old RCTC pair. Entering
the companion URL let the backend verify J18's source location at its saved
revision. Keyboard activation opened the actual companion's **Stmt Activities FY25**
sheet in a new tab with no opener access. The grid and selected sheet were visually
inspected. Switching back retained the exact source formula and trail; returning to
D12 restored 0 without additional observed Wingman API requests or a changed report
URL. It opens today's sheet, not a selected historical J18 cell. The temporary
extension, package, pairing copy and trial tabs were removed; privileged path and
serialized Chrome readback confirmed cleanup. The harness's initial nonprivileged
post-removal path check failed on the protected parent; this was recovered without
replaying the trial. Both services retained their PIDs and zero restarts. No product
code repair was needed. This is a bounded live journey, not G2 or product acceptance;
the updated build is not permanently installed in either browser.

The September 16 private activation used the owner-selected older RCTC sandbox pair,
not an audit client. The installed Mac broker read the Statement of Activities table
at its recorded revision: report D12 linked to companion spreadsheet J18, whose
stored SUMIFS formula and calculated value were returned. File metadata remained
unchanged. This is a real broker/API source-chain proof, not a native-selection,
cross-sheet navigation, accounting accuracy or complete in-page acceptance claim.
All seven goals remain open; the backend suite passes 687 tests with 13 optional
vision skips, including the exact CI pytest invocation.

Earlier September 16 sandbox validation used a rollforward example with seven sheets
and three report tables. Real backend reads followed a two-step formula chain and
resolved a named-sheet reference. Native page-context readback exposed the live
`/-1/` URL segment; a successful current-revision anchor read exposed nested
`attachmentPoint.tableRange.range`. Both compatibility defects are fixed locally,
and fictional regression fixtures now exercise those native shapes. Historical
routes remain rejected rather than reading latest data.

The sampled connected links returned source-table identities, but historical anchor
reads returned HTTP 500 through both the table-specific and generic endpoints; the
same-revision anchor list also failed. Current-revision anchors worked and were
used only to diagnose the schema, never as historical substitutes. Document table
metadata mapped to report sections, and source tables matched the explicitly opened
workbook's sheet metadata. This establishes a bounded mapping route, not a general
cross-workbook resolver or an installed document-to-cell journey. Native document
selection and navigation remain unimplemented. The installed-extension regression
used fictional transport; that earlier validation did not update the installation.

Source-follow requests now carry the opened workbook as a candidate, not authority.
The backend checks the source table's sheet and that sheet's table identity at the
recorded revision before resolving named-sheet formulas. A missing, denied or
mismatched lookup retains table evidence without granting workbook context. Live
sandbox reads followed a named-sheet formula through two steps to its stored year;
the same first step without verified workbook context remained unresolved.
Installed MV3 tests with fictional transport verified the location display and
return without new reads or writes. Desktop and 390px location views were inspected.

The browser lease subsequently became available. In the sampled document, tables
rendered as SVG rather than accessible cell grids. Resource identities matched API
table metadata, but the tested viewing-mode signals did not expose a cell address.
The local candidate therefore offers **explicit table and A1 choice**, not native
selection detection: read the section's table list, choose a table/cell, then inspect.
Each cell read rechecks section membership at the chosen revision. Incomplete or
mismatched metadata is refused before reading the cell. Source trails and return
reuse the same inspector without moving the Workiva page.

Read-only sandbox validation read a linked report cell and rejected a table from
another section. The catalog revision differed from the earlier baseline, so that
attempt stopped before a cell read; a new explicit catalog read pinned the subsequent
inspection. Its before/after revisions matched. The cause of the earlier revision
change is unknown; historical source-anchor lookup still failed without fallback.
Fictional installed tests cover table choice, keyboard submit, two-step source return,
empty/unavailable sections, real 30-second timeouts, cancellation and late replies
after a section change. Light/dark, 390px, error and scrolled evidence states were
inspected. Automatic native cell selection, general cross-workbook discovery and the
new-reviewer journey remain open. The later bounded RCTC in-page trial is recorded
above; it does not establish automatic native cell selection.

- Support the complete review journey across spreadsheet cells and linked document
  tables: inspect a value, see the supported source/destination relationship,
  navigate to that location, and return to the origin with a breadcrumb trail.
- Keep destination and source values visible together with their identities and
  recorded revisions. Distinguish formulas, literal values, cell links, range
  links, disconnected links and unavailable evidence.
- Follow additional supported source steps only on request. Handle cycles,
  read limits, missing access and unsupported references explicitly; never guess
  cell mappings from range offsets or substitute latest revisions silently.
- Report destination/publication evidence only when the API and readback establish
  it. A matching number or a connected link is not proof of publication or accuracy.

Done when known two-step source chains, cross-sheet navigation and a linked
document-table journey pass in both fictional tests and an authorized sandbox.
Changing scope must clear or visibly invalidate evidence. Inline prose links and
an automatically expanded full dependency graph are outside v1. If a required
document-table route is unavailable, record the exact gap; do not mark G2 complete
with spreadsheet-only evidence. Builds on the inspector; live proof depends on G1.

## G3. Findings explain what matters without creating review noise

Why: reviewers need an actionable queue, not a count of vaguely suspicious cells.

**Open.** The unsupported zero-display detector and repair kind are disabled in the
private build, including column-format apply and direct API requests. Neighbor
agreement is not a reporting convention; the demo's legitimate zero no longer creates
review work. This is a product-level removal, not fixture filtering. The other format
heuristics, independent labels and held-out accuracy evaluation remain unfinished;
no precision/recall or general repair-safety claim follows from this change.

The local coverage repair preserves zero-finding sheets, scan failures, page limits
and formula/type/link read metadata through the workbook queue, panel, copied report
and review packet. Missing coverage remains unknown; zero findings no longer means
a clean workbook. Failed or short content reads and failed/capped link reads carry
explicit limits. Malformed sheet data and incomplete sheet inventories are refused,
not interpreted as empty results. The sheet-list endpoint remains bounded to its
first 500 entries; a continuation now produces an explicit inventory error rather
than an incomplete denominator. Sheet rows are keyboard-expandable, and copied
reports retain scope, timestamp and limits. Verification: 708 backend tests passed
with 13 optional vision skips, 397 extension checks and nine worker/setup tests
passed. The installed MV3/handler browser test passed with fictional mixed-coverage
scan states, actual keyboard interaction and clipboard readback; corrected light,
dark, 390px and legacy-unknown views were inspected. This is not live Workiva scan
acceptance, detector accuracy or G3 completion. No permanent installation or service
change accompanies this candidate.

- Give each finding its location, observed evidence, reason, coverage limits and
  next action. Separate definite defects, judgment calls and incomplete checks.
- Start with broken references, observed link problems, label whitespace, year
  display and plain-cell contrast. Formula/source-policy checks remain judgment
  calls unless a governing policy is explicitly supplied.
- Challenge every detector with legitimate lookalikes: approved frozen values,
  intentional blanks and zeros, fiscal years, source formulas and native formats.
  Resolve the demo's unsupported zero-display convention target before enabling
  that proposed repair in v1. Missing evidence must never become a clean result.
- Create independently labeled fictional fixtures and a held-out set not used
  for tuning. Report per-detector precision/recall, sample counts, false alarms,
  exclusions, elapsed time and API requests through one repeatable command.

Done when findings and next actions survive blind review, and enabled detectors
meet thresholds fixed before evaluating the held-out set. Initial product targets:
**at least 95% precision and 90% recall for each definite-defect detector within
its declared coverage**. Fix the sample, supported defect families and exclusions
before tuning; include legitimate negative cases. No positive cases or no emitted
findings is not a passing score. Judgment flags are evaluated separately. These
are acceptance targets, not measured accuracy or population-wide guarantees.
Labeling can begin now; final evaluation depends on G2 evidence.

## G4. A reviewer can stop, resume and hand off without losing decisions

Why: real reporting review spans sessions, revisions and more than one person.

- Persist review runs and distinguish new, recurring and resolved findings.
  Record accepted exceptions with a reason, exact scope and relevant-evidence
  fingerprint; reopen them when the evidence or governing policy changes.
- Keep review state separate across workspaces, file copies and fiscal periods.
  An unrelated edit must not discard every decision, and a changed defect must
  not inherit an old dismissal.
- Produce a readable handoff containing scope, freshness, coverage, unresolved
  items, decisions and observed repair outcomes. A partial scan cannot resolve
  findings it did not revisit.
- Keep sensitive review data on the approved private runtime. Export is explicit
  and previewable; include retention, deletion and recovery behavior.

Done when a seeded review survives restart, detects a material source change,
preserves unrelated decisions, and lets another reviewer identify the remaining
work without explanation from the first. Depends on G1 scope and G3 finding identity.

## G5. Every enabled repair has an honest, safe outcome

Why: a preview and best-effort restore are not sufficient protection for a workbook.

- Bind confirmation to an expiring server-side plan and the exact previewed state.
  Recheck target, permission, content type, scope and policy at application time.
- Deduplicate repeated Apply requests and recover uncertain outcomes after timeout
  or restart without blindly replaying writes.
- Verify the requested result and any restoration. Distinguish applied, restored,
  failed and uncertain; a successful HTTP response alone proves none of them.
- Test concurrent edits, changed previews, late async completion, timeout after
  commit and process restart. Restoration must not overwrite a newer collaborator
  edit. Establish Workiva's conditional-write support; a local lock is insufficient.
- Limit v1 repairs to proven label whitespace, year-display formatting and
  plain-text contrast operations. Never change financial amounts or formulas,
  relink, publish, or infer accounting adjustments. Re-run affected checks after
  repair instead of leaving stale findings on screen.

Done when every enabled repair passes the predeclared failure cases, preserves
untargeted data, and produces observed outcomes in an authorized sandbox. Unsupported
operations stay review-only with a reason. Any unresolved concurrent-write risk
blocks enabling that repair; a read-only preview is not completion of this goal.
If no viable write boundary exists, the owner must explicitly accept a read-only
release scope. Depends on G1 and G3; safety implementation can proceed on disposable data.

## G6. The interface feels like one coherent, finished product

Why: technically available features are not useful if reviewers cannot find or
understand them. Follow [the design contract](../DESIGN.md), not a new visual stack.

- Make the primary journey obvious: **Inspect → Review → Act → Resume**. Keep
  connection/settings separate from review work. Put IDs, raw formats, specialist
  checks and diagnostics behind progressive disclosure rather than the default view.
- Use consistent names and actions. Remove dead ends, redundant controls, misleading
  badges, stale product descriptions and unexplained specialist vocabulary.
- Make loading, empty, partial, stale, offline, denied, failed and completed states
  deliberate. Every long read needs visible progress or elapsed time and a way to
  stop waiting without allowing a late reply to replace current evidence.
- Preserve Workiva's usable workspace. Verify actual extension dimensions, keyboard
  focus, screen-reader labels, contrast, light/dark modes, zoom and narrow viewports.
  Primary actions meet the existing 40px target; no horizontal overflow or hidden
  critical result. Chrome desktop is the v1 platform, not mobile Chrome extension support.

Done when a first-time reviewer completes inspection, source navigation, triage,
repair and resume using the UI's labels rather than coaching. Inspect rendered
states and exercise keyboard/screen-reader flows. Target local interaction feedback
within **100 ms** and record end-to-end p50/p95 on a declared workbook/network sample;
set supported-workload read budgets before pilot acceptance rather than claiming
Workiva latency from a simulator. Starts with G1; final acceptance depends on G2–G5.

## G7. The installed product is supportable and earns daily use

Why: passing tests and polished screenshots do not establish a finished product.

- Provide a versioned private installation package, reproducible setup, update and
  rollback instructions, and a useful redacted support report. Verify install,
  upgrade, restart and uninstall on a clean supported browser/runtime combination.
- Keep docs, manifest, permissions and visible capabilities consistent. Test the
  actual packaged extension with the supported backend, not only the demo adapter.
- Have a separate reviewer check the exact release candidate's access controls,
  data handling, write boundaries and high-impact behavior. No unresolved critical
  issue may be relabeled as a documentation limitation to close this goal.
- With consent, run a pilot with the owner and at least one reviewer who did not
  build Wingman. Use comparable unfamiliar workbooks with and without Wingman,
  counterbalance order, and record completion time, missed defects, false alarms
  acted on, setup failures and manual workarounds. Use fictional data for the blind
  comparison; real-work evaluation needs its own approved scope.

Done when G1–G6 pass on the release candidate, reviewers complete all supported
journeys without developer rescue, no unresolved critical data/access failure
remains, and the owner accepts it for regular use. Report the pilot's actual sample
and limitations; claim time savings only if measured without worse review outcomes.
A small pilot is not proof of production-wide accuracy. Private delivery, public
hosting, recruitment, publication and store submission each retain their approval gates.

## Execution order and stop conditions

1. **Connect and orient:** G1 plus G6's setup/status flow. First resolve the VPS
   connection decision and prove an installed extension can perform a read-only
   inspection in a named sandbox. Local connection UX and contract tests can proceed
   while access/deployment approval is pending.
2. **Finish the review journey:** G2, then G3; apply G6 throughout. In parallel with
   source implementation, prepare independent labels and legitimate counterexamples.
3. **Make work durable and actions safe:** G4 and G5 after their prerequisites.
4. **Remove friction and qualify the package:** complete G6, then G7's independent
   review and pilot. Repair observed failures before accepting the candidate.

Track each goal as open, blocked by a named dependency, or accepted with its native
evidence. Tests, captures and local commits support acceptance; they do not close
the whole product. Do not expand the goal count to manufacture progress or silently
drop a blocked journey. The finish line is **all seven goals accepted for the same
private release candidate**, followed by explicitly authorized delivery.

Out of v1: generic AI chat, autonomous accounting judgments, engagement-wide
reconciliation/rollforward automation, automatic link publication, client-ready
certification, arbitrary Workiva UI automation, public multi-tenant SaaS and a
Chrome Web Store launch. Their absence must not leave misleading controls behind.

These goals authorize no live client work, secret access, security/infrastructure
changes, installation into the owner's browser, deployment, spend or external sends.
Use fictional fixtures and disposable local state until the relevant action is approved.

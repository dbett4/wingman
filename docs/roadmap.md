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

Local verification passed 600 backend tests, 304 extension checks, four worker
contract tests, three demo browser workflows and two installed-extension connection
cases. Thirteen NumPy-dependent vision tests were skipped. The installed test uses
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
has no Workiva credentials or non-loopback egress; no workbook was accessed. Independent
review covered the read-only deployment boundary, not overall release readiness.
The paired extension is privately staged on the Mac, not installed in the owner's
Chrome profile. Browser installation, a provisioned Workiva sandbox, and first real
inspection remain open. [Deployment controls and rollback](../deploy/README.md).

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

# Wingman improvement roadmap

Goal: make financial-workbook review demonstrably useful and technically credible.
Complete milestones separately so claims never get ahead of evidence.

## 1. Credential-free demonstration — implemented

- [x] Fictional workbook with real scan, diagnosis, preview, apply, and report code.
- [x] Isolated sessions, reset, seeded failure, and visible write evidence.
- [x] HTTP integration tests and a Chromium workflow in CI.
- [x] Demo startup and simulation boundary documented.
- [x] Regression fix for year-header findings that conflicted with amount formatting.

These tests validate workflow behavior against a simulation, not Workiva compatibility
or population-level detector accuracy. See [demo.md](demo.md).

## 2. Write safety — next

- [ ] Bind confirmation to an expiring server-side plan and the exact previewed state.
- [ ] Reject stale plans and deduplicate repeated Apply requests, including uncertain outcomes.
- [ ] Verify restoration; distinguish restored, restore failed, and state uncertain.
- [ ] Test concurrent edits, late async completion, timeout after commit, and process restart.
- [ ] Check authoritative Workiva support for conditional writes. A local lock cannot
      exclude another Workiva user; explicitly document any remaining race.

Acceptance: no result claims a verified state without observed evidence; an expired
or changed plan cannot silently apply a different action. Extend the simulator only
to model the failure sequences under test, not to recreate Workiva wholesale.

## 3. Detector evaluation — pending

- [ ] Publish independently labeled fictional workbook fixtures with defects and
      convincing legitimate cases, plus a held-out set not used for tuning.
- [ ] Report per-detector precision/recall, sample counts, false alarms per workbook,
      coverage exclusions, scan times, and API requests under defined conditions.
- [ ] Keep finding correctness separate from write success, refusal, and restoration.
- [ ] Evaluate gated format targets that do not actually correct the reported issue;
      the demo's zero-display convention finding is one concrete candidate.

Acceptance: one repeatable command produces metrics from labels, not expectations
computed by the detector itself. Do not treat the demonstration fixture as a benchmark.

## 4. Review history — pending

- [ ] Persist local review runs and compare new, recurring, and resolved findings.
- [ ] Record accepted exceptions with reasons, scope, and relevant-content fingerprints.
- [ ] Invalidate exceptions when the evidence changes; never suppress a changed defect.
- [ ] Include unresolved work, coverage, and freshness in reviewer handoffs.

Acceptance: a reviewer can return to a changed workbook and see what needs attention
without repeatedly dismissing unchanged, documented exceptions. Sensitive evidence
stays local unless the reviewer explicitly exports it.

## 5. Reviewer pilot — needs participants, not synthetic claims

Use consenting reviewers and fictional workbooks. Give comparable but different
workbooks with and without Wingman, counterbalance order, and time the same task.
Record defects found/missed, false alarms acted on, completion time, and short
qualitative feedback. Do not reuse planted examples participants have already seen.

Publish participant/sample counts and limitations. A small pilot supports a scoped
case study, not a broad production claim. Fill in résumé impact figures only from
observed results; never infer review-time savings from the test suite.

Hosting a permanently public demo, recruiting reviewers, and publishing outcomes
remain separate actions. The current Amp portal is an authenticated development
preview, not a public résumé URL.

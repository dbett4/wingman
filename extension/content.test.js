// Offline unit test for the pure cell-ID reader logic (node).
const {
  classifyAddress, DriftGuard, buildReport, tallyGroups, triageText,
  filterGroupsByLane, collectSafeFixAddrs, queueToWorkbookRollup,
  groupValues, addrValueLabel, segmentLabel, shouldAutoExpandGroups,
  VISION_CROP_MAX, normalizeAddr, collectVisionAddrs, collectVisionAddrsFromScan, buildVisionQueuePayload,
  formatVisionStatus, formatChecksStatus, scanCtxLine,
  linkHealthSummary, publishStateSummary, connectedReadiness,
  guidedStepsStorageKey, exportVerdictStorageKey, isExportProofItem, guidedLaneId, exportProofLabel,
  hasGuidedSteps, initialGuidedProgress, mergeGuidedProgress,
  toggleGuidedStep, guidedProgressSummary,   guidedLaneLabel,
  isDragPassthrough,
  checksSuiteDisplayName, formatElapsedSeconds, shouldShowChecksSummaryCard,
  formatChecksSummaryLines, parseTimestampMs, formatScorecardTimestamp,
  scorecardAgeState, resolveScorecardGeneratedAt, bulkApplyConfirmCopy,
  applyServiceConfig, presetSpreadsheetId, tabLabel, isAcfrPreset,
  formatTieoutBadges, isTextTrapRefusal, exportProofChecklistFooter,
  exportProofChecklistIntro, exportProofFilterBanner, exportProofVerdictHint,
  shouldShowThermoAlert, thermoAlertCopy,
  isExtensionContextValid, isExtensionContextError,
  RELOAD_TAB_PILL_TEXT, isReloadTabPrompt, shouldShowReloadTabButton, reloadTabButtonLabel,
  parseColumnLetter, groupAddrsByColumn, filterAddrsByColumn,
  isGatedFormatKind, resolveGatedTargets, hasGatedFormatApply,
  columnApplyConfirmCopy, columnSafeApplyLabel,
  blankDlGuidedTitle, guidedCompletionBadge,
  isFormulaGapKind, connectedReportingReadiness, reportMetaLines,
  operatorConfigWarningLines,
 } = require("./wingman-core.js");

let pass = 0, fail = 0;
function eq(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`${ok ? "OK  " : "FAIL"} ${label}` + (ok ? "" : `  got=${JSON.stringify(got)} want=${JSON.stringify(want)}`));
  ok ? pass++ : fail++;
}

// --- read-only inspector identity and lossless evidence ---
(function () {
  const wi = require("./wingman-inspector.js");
  const href = "https://app.wdesk.com/a/workspace-a/spreadsheet/book-b/sheet/sheet-c";
  const target = { workspaceId: "workspace-a", spreadsheetId: "book-b", sheetId: "sheet-c", addr: "C12" };
  eq("inspector exact context", wi.selection(href, " C12 "), { target });
  eq("inspector live Workiva revision route", wi.selection(href.replace("/sheet/", "/-1/sheet/"), "C12"), { target });
  for (const revision of ["0", "42", "historical-revision", "-2"]) {
    eq("inspector does not read latest for historical route " + revision,
      !!wi.selection(href.replace("/sheet/", "/" + revision + "/sheet/"), "C12").target, false);
  }
  for (const changed of [href.replace("workspace-a", "workspace-b"), href.replace("book-b", "book-c"), href.replace("sheet-c", "sheet-d")]) {
    eq("inspector identity changes at " + changed, wi.key(wi.selection(changed, "C12")) !== wi.key({ target }), true);
  }
  for (const invalid of ["https://evil.invalid" + new URL(href).pathname, href.replace("wdesk.com", "wdesk.com.evil.invalid"),
    href.replace("https:", "http:"), "https://app.wdesk.com/doc/doc-id#" + new URL(href).pathname]) {
    eq("inspector refuses URL " + invalid, !!wi.selection(invalid, "C12").target, false);
  }
  for (const invalid of [null, "C0", "C12:D13", "C12 C13", "c12", "C01", "AAAA1"]) {
    eq("inspector refuses address " + invalid, !!wi.selection(href, invalid).target, false);
  }
  eq("hash only allowed in explicit demo", wi.selection("http://localhost/#/spreadsheet/book-b/sheet/sheet-c", "C12", true).target,
    { workspaceId: null, spreadsheetId: "book-b", sheetId: "sheet-c", addr: "C12" });
  eq("matching response", wi.matches(target, { spreadsheetId: "book-b", sheetId: "sheet-c", addr: "C12" }), true);
  for (const reply of [null, { ...target, sheetId: "sheet-d" }, { ...target, spreadsheetId: "book-c" }, { ...target, addr: "D12" }]) {
    eq("reject foreign response " + JSON.stringify(reply), wi.matches(target, reply), false);
  }
  for (const [value, text] of [[0, "0"], [false, "false"], [null, "(blank)"], ["", "(blank)"], ["<script>", "<script>"]]) {
    eq("preserve observed " + JSON.stringify(value), wi.valueText({ status: "observed", value }), text);
  }
  eq("unavailable is not blank", wi.valueText({ status: "unavailable" }), "Not available");
  eq("formula object uses expression", wi.valueText({ status: "observed", value: { type: "formula" }, formula: "=6*7" }), "=6*7");
  eq("native precision and scale", wi.formatText({ status: "observed", value: {
    valueFormatType: "ACCOUNTING", precision: { auto: false, value: 0 }, enteredIn: "ONES", shownIn: "THOUSANDS",
  } }), "ACCOUNTING · 0 decimals · entered in ONES · shown in THOUSANDS");
  const origin = { target, tableId: "table-a", contentRevision: "rev-9" };
  const source = { tableId: "table-b", revision: "rev-3", addr: "D6" };
  const trail = [{ target: source }];
  eq("new source step permitted", wi.traceBlock(origin, [], source), "");
  eq("source response matches exact identity", wi.matchesSource(source, { ...source }), true);
  for (const key of ["tableId", "revision", "addr"]) {
    eq("source reply rejects changed " + key, wi.matchesSource(source, { ...source, [key]: "foreign" }), false);
    eq("cycle distinguishes changed " + key, wi.traceBlock(origin, trail, { ...source, [key]: "different" }), "");
  }
  eq("same historical source is a cycle", wi.traceBlock(origin, trail, source).startsWith("Already"), true);
  eq("root cell is part of cycle detection", wi.traceBlock(origin, [], { tableId: "table-a", revision: "rev-9", addr: "C12" }).startsWith("Already"), true);
  eq("tenth step allowed", wi.traceBlock(origin, Array(9).fill({ target: {} }), source), "");
  eq("eleventh step blocked", wi.traceBlock(origin, Array(10).fill({ target: {} }), source).startsWith("10-step"), true);

  const docUrl = "https://app.wdesk.com/a/workspace-a/doc/doc-b/r/-1/v/1/sec/sec-c";
  const doc = { workspaceId: "workspace-a", documentId: "doc-b", sectionId: "sec-c" };
  eq("document URL gives section scope, never a native cell", wi.selection(docUrl, "B2"), { document: doc });
  for (const invalid of [docUrl.replace("/-1/", "/9/"), docUrl.replace("/v/1/", "/v/2/"), docUrl.replace("wdesk.com", "wdesk.com.evil.invalid")]) {
    eq("unsupported document route refused " + invalid, !!wi.selection(invalid).document, false);
  }
  for (const field of ["workspaceId", "documentId", "sectionId"]) {
    eq("document scope changes with " + field, wi.key({ document: { ...doc, [field]: "another" } }) !== wi.key({ document: doc }), true);
  }
  const chosen = { ...doc, tableId: "report-table", revision: "report-5", addr: "C12" };
  eq("document evidence matches exact choice", wi.matches(chosen, { ...chosen }), true);
  for (const field of ["documentId", "sectionId", "tableId", "revision", "addr"]) {
    eq("document evidence rejects changed " + field, wi.matches(chosen, { ...chosen, [field]: "another" }), false);
  }
  eq("spreadsheet evidence cannot match document choice", wi.matches(chosen, target), false);
})();

// --- connection evidence is not Workiva access ---
(function () {
  const wc = require("./wingman-connection.js");
  const data = { service: "wingman", protocol: 1, readOnly: true, authorization: "accepted",
    workivaCredentials: "present", workivaAccess: "not_tested" };
  eq("connection initially unchecked", wc.describe({}).code, "unchecked");
  eq("connection check loading", wc.describe({ loading: true }).code, "checking");
  eq("connection stopped", wc.describe({ cancelled: true }).code, "cancelled");
  eq("connection accepted", wc.describe({ data }).code, "connected");
  eq("connection credential presence is not access", wc.facts({ data }), [
    ["Service authorization", "Accepted"], ["Workiva credentials", "Configured, not validated"],
    ["Workbook access", "Not tested"], ["Workbook changes", "None — connection check only"],
  ]);
  eq("read-only backend mode is separate from diagnostic readOnly", wc.facts({ data: { ...data, serviceMode: "read-only" } })[0],
    ["Service mode", "Read-only — repairs disabled"]);
  eq("incompatible response cannot claim read-only service", wc.facts({ data: { ...data, service: "other", serviceMode: "read-only" } })
    .some(row => row[0] === "Service mode"), false);
  eq("connection missing backend credentials", wc.describe({ data: { ...data, workivaCredentials: "missing" } }).code, "credentials");
  for (const [field, value] of [["service", "other"], ["protocol", "1"], ["readOnly", false],
    ["authorization", "unknown"], ["workivaAccess", "verified"], ["workivaCredentials", null], ["simulation", true]]) {
    eq("connection rejects incompatible " + field, wc.describe({ data: { ...data, [field]: value } }).code, "incompatible");
  }
  for (const invalid of [null, undefined, {}, [], "bad response"]) {
    eq("connection rejects empty or malformed response " + JSON.stringify(invalid), wc.describe({ data: invalid }).code, "incompatible");
  }
  for (const [error, expected] of [[{ configError: true }, "setup"], [{ offline: true }, "offline"],
    [{ timeout: true }, "timeout"], [{ status: 403 }, "denied"], [{ status: 401 }, "denied"],
    [{ status: 500 }, "failed"], [{ reloaded: true }, "reloaded"]]) {
    eq("connection error " + expected, wc.describe({ error }).code, expected);
  }
  const demo = { simulation: true, service: "wingman-demo" };
  eq("connection demo explicit", wc.describe({ data: demo }, true).code, "demo");
  eq("connection demo not live proof", wc.describe({ data: demo }, false).code, "incompatible");
  const state = { data: { ...data, token: "sensitive-marker", workbookId: "sensitive-marker" }, checkedAt: "2026-09-15T12:34:56Z" };
  eq("connection diagnostic has freshness", wc.diagnostics(state).includes(state.checkedAt), true);
  eq("connection diagnostic ignores unknown sensitive fields", wc.diagnostics(state).includes("sensitive-marker"), false);
  eq("connection diagnostic ignores upstream error bodies", wc.diagnostics({ error: { message: "sensitive-marker" } }).includes("sensitive-marker"), false);
})();

// --- classifyAddress ---
eq("single A1",      classifyAddress("A1"),     { status: "ok", addr: "A1", isRange: false });
eq("single C42",     classifyAddress("C42"),    { status: "ok", addr: "C42", isRange: false });
eq("triple-col AB12",classifyAddress("AB12"),   { status: "ok", addr: "AB12", isRange: false });
eq("range B2:D10",   classifyAddress("B2:D10"), { status: "ok", addr: "B2:D10", isRange: true });
eq("trim ' A1 '",    classifyAddress("  A1 "),  { status: "ok", addr: "A1", isRange: false });
eq("empty",          classifyAddress(""),       { status: "empty" });
eq("null",           classifyAddress(null),     { status: "drift-null" });
eq("undefined",      classifyAddress(undefined),{ status: "drift-null" });
eq("row 0 invalid",  classifyAddress("A0").status,    "drift-shape");
eq("leading digit",  classifyAddress("0A").status,    "drift-shape");
eq("4-letter col",   classifyAddress("ABCD1").status, "drift-shape");
eq("no digits (AL)", classifyAddress("AL").status,    "drift-shape");
eq("garbage",        classifyAddress("US").status,    "drift-shape");

// --- DriftGuard ---
(function () {
  const g = new DriftGuard(3);
  eq("first ok -> dom/no-drift", g.record("ok"), { tier: "dom", drift: false });
  eq("1st shape after ok", g.record("drift-shape"), { tier: "dom", drift: false });
  eq("2nd shape", g.record("drift-shape"), { tier: "dom", drift: false });
  eq("3rd shape -> drift/ocr", g.record("drift-null"), { tier: "ocr", drift: true });
})();
(function () {
  const g = new DriftGuard(3);
  g.record("ok");
  for (let i = 0; i < 5; i++) g.record("drift-shape");
  eq("6th consecutive -> typed tier", g.record("drift-null"), { tier: "typed", drift: true });
})();
(function () {
  const g = new DriftGuard(3);
  // drift before ever-ok is NOT drift (page still loading)
  eq("drift before everOk", g.record("drift-null"), { tier: "dom", drift: false });
  eq("still not drift", g.record("drift-null"), { tier: "dom", drift: false });
})();
(function () {
  const g = new DriftGuard(3);
  g.record("ok");
  g.record("drift-shape"); g.record("drift-shape");
  g.record("ok"); // recovery resets
  eq("recovery resets fails", g.record("drift-shape"), { tier: "dom", drift: false });
})();
(function () {
  const g = new DriftGuard(3);
  g.record("ok");
  eq("empty is not drift", g.record("empty"), { tier: "dom", drift: false });
})();

// --- buildReport ---
(function () {
  const data = {
    findingTotal: 3, scanned: 3, truncatedSheets: false,
    sheets: [
      { name: "Net Assets", findingCount: 2, truncated: false, error: null, groups: [
        { kind: "low-contrast", severity: "high", signature: "#FFFFFF on #00A19B", fix_lane: "safe-auto", count: 2, addrs: ["B7", "B8"] } ] },
      { name: "TB", findingCount: 1, truncated: false, error: null, groups: [
        { kind: "label-hygiene", severity: "low", signature: "trailing space", fix_lane: "surfaced", count: 1, addrs: ["A5"] } ] },
      { name: "Clean", findingCount: 0, truncated: false, error: null, groups: [] },
    ],
  };
  const r = buildReport(data);
  eq("report title", /^# Wingman review/.test(r), true);
  eq("report summary", r.includes("3 findings across 3 sheets · 1 clean"), true);
  eq("section header w/ count", r.includes("## Net Assets — 2 findings"), true);
  eq("fixable tag on safe-auto", r.includes("[fixable]"), true);
  eq("no fixable tag on surfaced", /label hygiene.*\[fixable\]/.test(r), false);
  eq("addrs listed", r.includes("B7, B8"), true);
  eq("clean sheet omitted", r.includes("## Clean"), false);
  eq("ordered by findings desc", r.indexOf("## Net Assets") < r.indexOf("## TB"), true);
  eq("readiness section present", r.includes("## Connected Reporting Readiness"), true);
  eq("safe-auto next action", r.includes("review/apply 2 safe-auto cell fixes with readback"), true);
  const readyLines = connectedReportingReadiness({ findingTotal: 3, scanned: 2, truncatedSheets: false,
    sheets: [
      { name: "Links", findingCount: 2, error: null, groups: [
        { kind: "empty-linked-range", severity: "high", signature: "blank DL", fix_lane: "surfaced", count: 1, addrs: ["B2"], linkHealthStatus: "no_dest" },
        { kind: "publish-proof-gap", severity: "high", signature: "publish needed", fix_lane: "surfaced", count: 1, addrs: ["C3"], linkHealthStatus: "orphaned" },
      ] },
      { name: "Formula", findingCount: 2, error: null, groups: [
        { kind: "hardcoded-face-value", severity: "high", signature: "manual value", fix_lane: "surfaced", count: 2, addrs: ["D4", "D5"] },
      ] },
    ] });
  eq("readiness counts source blockers", readyLines.some(function (l) { return l.indexOf("2 blockers need source data") >= 0; }), true);
  eq("readiness counts formula gaps", readyLines.some(function (l) { return l.indexOf("2 formula gaps require") >= 0; }), true);
  eq("readiness surfaces link vocabulary", readyLines.some(function (l) { return l.indexOf("healthy 0, no_dest 1, destination 0, orphaned 1") >= 0; }), true);
  eq("readiness says tieout alone not proof", readyLines.some(function (l) { return l.indexOf("tieout alone is not delivery proof") >= 0; }), true);
  // empty / all-clean workbook
  const clean = buildReport({ findingTotal: 0, scanned: 2, truncatedSheets: false,
    sheets: [{ name: "A", findingCount: 0, error: null, groups: [] }, { name: "B", findingCount: 0, error: null, groups: [] }] });
  eq("all-clean says so", clean.includes("No issues found."), true);
  eq("all-clean summary", clean.includes("0 findings across 2 sheets · 2 clean"), true);
  // a sheet that errored is reported, not dropped
  const withErr = buildReport({ findingTotal: 0, scanned: 1, truncatedSheets: false,
    sheets: [{ name: "Oops", findingCount: 0, error: "HTTP 500", groups: [] }] });
  eq("errored sheet surfaced", withErr.includes("## Oops — scan error") && withErr.includes("- ERROR: HTTP 500"), true);
  const stamped = buildReport({ findingTotal: 0, scanned: 1, truncatedSheets: false,
    generated_at: "2026-06-26T12:00:00Z", extension_build: "abc123", service_status: "ok", scan_scope: "workbook", stale: true,
    sheets: [{ name: "A", findingCount: 0, error: null, groups: [] }] });
  eq("report metadata generated", stamped.includes("- Generated: 2026-06-26T12:00:00Z"), true);
  eq("report metadata extension build", stamped.includes("- Extension build: abc123"), true);
  eq("report metadata stale warning", stamped.includes("Stale warning"), true);
  eq("reportMetaLines null safe", reportMetaLines({}).length, 0);
})();

// --- triage + panel filters ---
(function () {
  var groups = [
    { kind: "low-contrast", severity: "medium", fix_lane: "safe-auto", fixable: true, count: 3, addrs: ["B1", "B2", "B3"] },
    { kind: "broken-ref", severity: "high", fix_lane: "surfaced", fixable: false, count: 1, addrs: ["C4"] },
    { kind: "label-hygiene", severity: "low", fix_lane: "safe-auto", fixable: true, count: 2, addrs: ["A1", "A2"] },
    { kind: "negative-without-parens", severity: "medium", fix_lane: "safe-auto", fixable: true, count: 1, addrs: ["C1"] },
  ];
  eq("tally issues", tallyGroups(groups), { issues: 7, high: 1, fixable: 6, review: 0, formulaGaps: 1, exportProof: 0 });
  eq("triage text", triageText(groups), "7 issues · 1 high · 6 fixable · 1 formula gap");
  eq("filter all", filterGroupsByLane(groups, "all").length, 4);
  eq("filter fixable", filterGroupsByLane(groups, "fixable").map(function (g) { return g.kind; }),
    ["low-contrast", "label-hygiene", "negative-without-parens"]);
  eq("filter review count", filterGroupsByLane(groups, "review").length, 1);
  eq("collect safe addrs", collectSafeFixAddrs(groups).map(function (x) { return x.addr; }),
    ["B1", "B2", "B3", "A1", "A2", "C1"]);
  eq("segment fixable label", segmentLabel("fixable", tallyGroups(groups)), "Fixable 6");
  eq("segment review label", segmentLabel("review", tallyGroups(groups)), "Review");
  eq("auto-expand empty list", shouldAutoExpandGroups([]), false);
  eq("auto-expand single group", shouldAutoExpandGroups([groups[0]]), true);
  eq("auto-expand 3 groups", shouldAutoExpandGroups(groups.slice(0, 3)), true);
  eq("auto-expand 4 groups stays collapsed", shouldAutoExpandGroups(groups), false);
  eq("auto-expand null safe", shouldAutoExpandGroups(null), false);
  eq("groupValues prefers values", groupValues({ values: { A1: "x" }, cellValues: { A1: "y" } }), { A1: "x" });
  eq("groupValues cellValues fallback", groupValues({ cellValues: { A1: "y" } }), { A1: "y" });
  eq("addrValueLabel empty skipped", addrValueLabel("B4", { B4: "" }), { addr: "B4", value: null });
  eq("addrValueLabel shows text", addrValueLabel("B4", { B4: "2025" }), { addr: "B4", value: "2025" });
  eq("addrValueLabel truncates", addrValueLabel("B4", { B4: "x".repeat(40) }).value.length, 30);

  // --- isFormulaGapKind ---
  eq("isFormulaGapKind hardcoded-face-value", isFormulaGapKind("hardcoded-face-value"), true);
  eq("isFormulaGapKind formula-evaluates-blank", isFormulaGapKind("formula-evaluates-blank"), true);
  eq("isFormulaGapKind unbounded SUMIFS", isFormulaGapKind("unbounded-full-column-sumifs"), true);
  eq("isFormulaGapKind dead unsupported function", isFormulaGapKind("dead-unsupported-function"), true);
  eq("isFormulaGapKind degenerate placeholder", isFormulaGapKind("degenerate-placeholder-formula"), true);
  eq("isFormulaGapKind broken-ref", isFormulaGapKind("broken-ref"), true);
  eq("isFormulaGapKind display-wrapper", isFormulaGapKind("display-wrapper"), true);
  eq("isFormulaGapKind low-contrast is not gap", isFormulaGapKind("low-contrast"), false);
  eq("isFormulaGapKind label-hygiene is not gap", isFormulaGapKind("label-hygiene"), false);
  eq("isFormulaGapKind null safe", isFormulaGapKind(null), false);

  // --- formula gaps in tally + triage ---
  (function () {
    var mixed = [
      { kind: "hardcoded-face-value", severity: "high", fix_lane: "surfaced", fixable: false, count: 2, addrs: ["C5", "C7"] },
      { kind: "display-wrapper", severity: "high", fix_lane: "surfaced", fixable: false, count: 1, addrs: ["D3"] },
      { kind: "negative-without-parens", severity: "medium", fix_lane: "surfaced", fixable: false, count: 1, addrs: ["E9"] },
      { kind: "low-contrast", severity: "medium", fix_lane: "safe-auto", fixable: true, count: 4, addrs: ["B1","B2","B3","B4"] },
    ];
    eq("formula gap tally", tallyGroups(mixed), { issues: 8, high: 3, fixable: 4, review: 1, formulaGaps: 3, exportProof: 0 });
    eq("triage plural gaps", triageText(mixed), "8 issues · 3 high · 4 fixable · 3 formula gaps · 1 review");
    var gapOnly = [{ kind: "broken-ref", severity: "high", fix_lane: "surfaced", fixable: false, count: 1, addrs: ["A1"] }];
    eq("triage single formula gap", triageText(gapOnly), "1 issue · 1 high · 1 formula gap");
    var reviewOnly = [{ kind: "check-tieout", severity: "medium", fix_lane: "surfaced", fixable: false, count: 2, addrs: ["B1","B2"] }];
    eq("triage review-only unchanged", triageText(reviewOnly), "2 issues · 2 review");
  })();
})();

// --- queue rollup + diagnosis ---
(function () {
  var q = {
    issueCount: 3, scanned: 5, truncatedSheets: true,
    items: [
      { sheetId: "s1", sheetName: "TB", kind: "low-contrast", severity: "high", fix_lane: "safe-auto", count: 2, addrs: ["B1", "B2"] },
      { sheetId: "s2", sheetName: "Locked", kind: "scan-error", severity: "high", fix_lane: "surfaced", count: 1, addrs: [], signature: "HTTP 403" },
      { sheetId: "s1", sheetName: "TB", kind: "label-hygiene", severity: "low", fix_lane: "safe-auto", count: 1, addrs: ["A1"] },
    ],
  };
  var r = queueToWorkbookRollup(q);
  eq("rollup finding total", r.findingTotal, 3);
  eq("rollup sheet count", r.sheets.length, 2);
  eq("rollup groups on TB", r.sheets.find(function (s) { return s.name === "TB"; }).findingCount, 3);
  eq("rollup scan error", r.sheets.find(function (s) { return s.name === "Locked"; }).error, "HTTP 403");
})();

// --- vision crop planning ---
(function () {
  var items = [
    { kind: "low-contrast", fix_lane: "safe-auto", addrs: ["B1", "B2"] },
    { kind: "low-contrast", fix_lane: "surfaced", addrs: ["C3", "B2"] },
    { kind: "broken-ref", fix_lane: "surfaced", addrs: ["D4"] },
  ];
  eq("normalizeAddr strips range", normalizeAddr("b2:d10"), "B2");
  eq("normalizeAddr rejects bad", normalizeAddr("A0"), null);
  eq("collectVisionAddrs priority", collectVisionAddrs(items, 10), ["C3", "B2", "B1", "D4"]);
  eq("collectVisionAddrs cap", collectVisionAddrs(items, 2).length, 2);
  eq("collectVisionAddrs dedupe", collectVisionAddrs([{ addrs: ["A1", "A1"] }], 10), ["A1"]);
  eq("collectVisionAddrsFromScan uses server list", collectVisionAddrsFromScan(
    { items: [], visionCandidates: ["D4", "B2", "B2", "C3"] }, 2),
    ["D4", "B2"]);
  eq("collectVisionAddrsFromScan fallback", collectVisionAddrsFromScan({ items: items }, 10),
    ["C3", "B2", "B1", "D4"]);
  eq("buildVisionQueuePayload", buildVisionQueuePayload(
    { spreadsheetId: "ss", sheetId: "sh" }, { B1: "abc" }, true),
    { spreadsheetId: "ss", sheetId: "sh", vision: true, cellImages: { B1: "abc" } });
  eq("formatVisionStatus applied", formatVisionStatus({ applied: true, visionFindingCount: 2, netNew: 2 }),
    "vision on · +2 new (2 pixel hits)");
  eq("formatVisionStatus zero net new", formatVisionStatus({ applied: true, visionFindingCount: 3, netNew: 0 }),
    "vision on · 0 new (3 checked)");
  eq("formatVisionStatus legacy", formatVisionStatus({ applied: true, visionFindingCount: 2 }),
    "vision on · 2 pixel findings");
  eq("formatVisionStatus skipped", formatVisionStatus({ skipped: "no cellImages supplied" }),
    "vision skipped · no cellImages supplied");
  eq("scanCtxLine with vision", scanCtxLine({
    cellCount: 100, truncated: false, items: [],
    vision: { applied: true, visionFindingCount: 1, netNew: 1 },
  }), "100 cells · no issues · vision on · +1 new (1 pixel hits)");
  eq("formatChecksStatus green", formatChecksStatus({ requested: true, applied: true, suite: "tieout", fail_count: 0, elapsed_s: 2.1 }),
    "tieout · green · 2.1s");
  eq("formatChecksStatus skipped", formatChecksStatus({ requested: true, skipped: "no client dir" }),
    "checks skipped · no client dir");
  eq("scanCtxLine with checks", scanCtxLine({
    cellCount: 50, truncated: false, items: [{ kind: "check-tieout", severity: "high", fix_lane: "surfaced", count: 1 }],
    checks: { requested: true, applied: true, suite: "tieout", fail_count: 1, elapsed_s: 3 },
  }), "50 cells · 1 issue · 1 high · 1 review · tieout · 1 FAIL · 3s");
  eq("VISION_CROP_MAX default", VISION_CROP_MAX, 80);
})();

// --- connected reporting readiness ---
(function () {
  var data = {
    items: [
      { kind: "broken-ref", severity: "high", fix_lane: "surfaced", fixable: false, count: 1, addrs: ["C4"] },
      { kind: "low-contrast", severity: "medium", fix_lane: "safe-auto", fixable: true, count: 2, addrs: ["B1", "B2"] },
    ],
    formula_fetch: { enabled: true, partial: false },
    link_fetch: {
      enabled: true, sourceLinks: 2, destinationLinks: 1, emptyLinkedBlocks: 0,
      linkHealth: { summary: { total: 3, healthy: 1, no_dest: 1, destination: 1, orphaned: 0 } },
      publishState: { summary: { total: 3, published: 1, unpublished: 1, source_only: 1, destination_only: 0, unknown: 0 } },
    },
    checks: { requested: true, applied: true, fail_count: 0 },
  };
  eq("linkHealthSummary vocab counts", linkHealthSummary(data.link_fetch),
    { total: 3, healthy: 1, no_dest: 1, destination: 1, orphaned: 0 });
  eq("publishStateSummary vocab counts", publishStateSummary(data.link_fetch),
    { total: 3, published: 1, unpublished: 1, source_only: 1, destination_only: 0, unknown: 0 });
  var ready = connectedReadiness(data);
  eq("connected readiness title gaps", ready.title, "Connected reporting: proof gaps");
  eq("connected readiness includes no_dest", ready.lines.some(function (l) { return l.indexOf("no_dest") >= 0; }), true);
  eq("connected readiness includes publish stale", ready.lines.some(function (l) { return l.indexOf("1 unpublished/stale") >= 0; }), true);
  eq("connected readiness publish gaps", ready.gaps.indexOf("unpublished linked ranges") >= 0, true);
  eq("connected readiness next safe fix", ready.lines.some(function (l) { return l.indexOf("2 safe fixes") >= 0; }), true);
  eq("connected readiness missing evidence", connectedReadiness({ items: [] }).gaps.slice(0, 2),
    ["source refresh evidence missing", "formula evidence missing"]);

  // D20: DL source numeric formula surfacing
  var dataWithDlNumeric = {
    items: [],
    formula_fetch: { enabled: false },
    link_fetch: { enabled: true, sourceLinks: 3, destinationLinks: 1, emptyLinkedBlocks: 0, dlSourceNumericFormulas: 2 },
    checks: null,
  };
  var readyDl = connectedReadiness(dataWithDlNumeric);
  eq("D20 dl numeric gap added", readyDl.gaps.some(function (g) { return g.indexOf("DL source cells") >= 0; }), true);
  eq("D20 dl numeric line present", readyDl.lines.some(function (l) { return l.indexOf("2 source links") >= 0 && l.indexOf("raw digits") >= 0; }), true);
  var dataCleanDl = {
    items: [],
    formula_fetch: { enabled: false },
    link_fetch: { enabled: true, sourceLinks: 2, destinationLinks: 1, emptyLinkedBlocks: 0, dlSourceNumericFormulas: 0 },
    checks: null,
  };
  var readyCleanDl = connectedReadiness(dataCleanDl);
  eq("D20 no gap when zero", readyCleanDl.gaps.some(function (g) { return g.indexOf("DL source cells") >= 0; }), false);
})();

// --- vision crop rect golden fixtures ---
(function () {
  const fs = require("fs");
  const path = require("path");
  const { pickCellCropRect, CANVAS_INSET } = require("./vision-capture.js");

  function rect(left, top, width, height) {
    return {
      left: left, top: top, width: width, height: height,
      right: left + width, bottom: top + height,
    };
  }

  function buildDoc(fixture) {
    var canvasEls = (fixture.canvases || []).map(function (r) {
      return Object.assign({ getBoundingClientRect: function () { return rect(r.left, r.top, r.width, r.height); } }, r);
    });
    var overlayEls = (fixture.overlays || []).map(function (o) {
      return {
        className: o.className || "cell-select",
        style: {},
        getBoundingClientRect: function () { return rect(o.left, o.top, o.width, o.height); },
      };
    });
    var all = canvasEls.concat(overlayEls);
    return {
      querySelectorAll: function (sel) {
        if (sel === "canvas") return canvasEls;
        if (sel === "div,span") return overlayEls;
        return [];
      },
      defaultView: { getComputedStyle: function () { return { display: "block", visibility: "visible", opacity: "1" }; } },
    };
  }

  ["overlay-hit", "no-overlay", "no-canvas"].forEach(function (name) {
    var fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures/vision-crop", name + ".json"), "utf8"));
    var got = pickCellCropRect(buildDoc(fixture));
    eq("pickCellCropRect " + name + " ok", got.ok, fixture.expect.ok);
    if (fixture.expect.method) eq("pickCellCropRect " + name + " method", got.method, fixture.expect.method);
    if (fixture.expect.confidence) eq("pickCellCropRect " + name + " confidence", got.confidence, fixture.expect.confidence);
    if (fixture.expect.reason) eq("pickCellCropRect " + name + " reason", got.reason, fixture.expect.reason);
  });

  var inset = pickCellCropRect(buildDoc({ canvases: [{ left: 10, top: 20, width: 400, height: 300 }], overlays: [] }));
  eq("canvas inset coords", { x: inset.x, y: inset.y, w: inset.width, h: inset.height }, {
    x: 10 + CANVAS_INSET.dx, y: 20 + CANVAS_INSET.dy, w: CANVAS_INSET.w, h: CANVAS_INSET.h,
  });
})();

// --- panel drag passthrough (Vision toggle + toolbar buttons) ---
(function () {
  function mockTarget(tag, cls) {
    return {
      closest: function (sel) {
        if (sel.indexOf("button") >= 0 && tag === "button") return mockTarget(tag, cls);
        if (sel.indexOf(".wm-toolbar") >= 0 && cls === "wm-toolbar") return mockTarget(tag, cls);
        if (sel.indexOf("label") >= 0 && tag === "label") return mockTarget(tag, cls);
        return null;
      },
    };
  }
  eq("isDragPassthrough button", isDragPassthrough(mockTarget("button", "wm-btn")), true);
  eq("isDragPassthrough toolbar", isDragPassthrough(mockTarget("div", "wm-toolbar")), true);
  eq("isDragPassthrough brand", isDragPassthrough({ closest: function () { return null; } }), false);
})();

// --- guided lane helpers (blank-DL + export-proof) ---
(function () {
  var steps = [
    { id: "verify-source", title: "Verify source", detail: "Check source cell." },
    { id: "publish-spreadsheet", title: "Publish SS", detail: "ownLinks" },
    { id: "publish-document", title: "Publish doc", detail: "allLinks" },
    { id: "re-walk-cache", title: "Re-walk", detail: "empty-cached walk" },
  ];
  var group = {
    kind: "blank-linked-cell",
    id: "abc123",
    diagnosis: { guided_steps: steps, guided_lane: "blank-dl", pathway_id: "link.blank-dl-review" },
  };
  var exportGroup = {
    kind: "check-tieout",
    id: "tie456",
    diagnosis: {
      guided_lane: "export-proof",
      pathway_id: "export-proof.guided",
      guided_steps: [
        { id: "publish-spreadsheet", title: "SS", detail: "ownLinks" },
        { id: "publish-document", title: "Doc", detail: "allLinks" },
        { id: "export-pdf", title: "PDF", detail: "binary" },
        { id: "grep-pdf", title: "grep", detail: "pdftotext" },
        { id: "mark-verdict", title: "verdict", detail: "mark" },
      ],
    },
  };
  eq("guidedStepsStorageKey blank-dl", guidedStepsStorageKey("abc123", "blank-dl"), "wmGuided:blank-dl:abc123");
  eq("guidedStepsStorageKey export", guidedStepsStorageKey("tie456", "export-proof"), "wmGuided:export-proof:tie456");
  eq("exportVerdictStorageKey", exportVerdictStorageKey("tie456"), "wmExportVerdict:tie456");
  eq("hasGuidedSteps true", hasGuidedSteps(group), true);
  eq("hasGuidedSteps false", hasGuidedSteps({ kind: "broken-ref" }), false);
  eq("isExportProofItem true", isExportProofItem(exportGroup), true);
  eq("isExportProofItem false", isExportProofItem(group), false);
  eq("guidedLaneId", guidedLaneId(group), "blank-dl");
  var prog = initialGuidedProgress(steps);
  eq("initial progress all false", Object.values(prog).every(function (v) { return v === false; }), true);
  eq("merge stored progress", mergeGuidedProgress({ "publish-spreadsheet": true }, steps)["publish-spreadsheet"], true);
  eq("toggle step on", toggleGuidedStep(prog, "verify-source")["verify-source"], true);
  eq("toggle step off", toggleGuidedStep({ "verify-source": true }, "verify-source")["verify-source"], false);
  eq("summary partial", guidedProgressSummary({ "verify-source": true }, steps), { done: 1, total: 4, complete: false });
  eq("summary complete", guidedProgressSummary({
    "verify-source": true, "publish-spreadsheet": true,
    "publish-document": true, "re-walk-cache": true,
  }, steps), { done: 4, total: 4, complete: true });
  eq("guidedLaneLabel partial", guidedLaneLabel({ done: 2, total: 4, complete: false }, "blank-dl"), "Guided steps · 2/4");
  eq("guidedLaneLabel export", guidedLaneLabel({ done: 1, total: 5, complete: false }, "export-proof"), "Export proof · 1/5");
  eq("guidedLaneLabel default", guidedLaneLabel(null, "blank-dl"), "Guided steps");
  eq("filter export lane", filterGroupsByLane([group, exportGroup], "export").length, 1);
  eq("exportProofLabel proven", exportProofLabel("EXPORT_PROVEN"), "EXPORT_PROVEN");
})();

// --- tieout scorecard coord helpers ---
(function () {
  var {
    colIndexToLetter, tieoutCellAddr, tieoutCoordLabel, tieoutJumpContext,
  } = require("./wingman-core.js");
  eq("colIndexToLetter H", colIndexToLetter(8), "H");
  eq("colIndexToLetter L", colIndexToLetter(12), "L");
  var item = {
    kind: "check-tieout",
    wb_row: 106, wb_col: 12,
    jumpHint: { sheetName: "Gov-Wide - Net Position", addr: "L106" },
    sheetName: "Gov-Wide - Net Position",
    tieout_status: "FAIL",
  };
  eq("tieoutCellAddr", tieoutCellAddr(item), "L106");
  eq("tieoutCoordLabel", tieoutCoordLabel(item), "Gov-Wide - Net Position · L106 · FAIL");
  eq("tieoutJumpContext on sheet", tieoutJumpContext(item, { isCurrent: true, name: "Gov-Wide - Net Position" }).canJump, true);
  eq("tieoutJumpContext off sheet", tieoutJumpContext(item, { isCurrent: true, name: "TB" }).canJump, false);
})();

// --- Audit Rail helpers ---
(function () {
  var ACFR_PRESET_SS_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90";
  applyServiceConfig({
    presets: { acfr: { spreadsheetId: ACFR_PRESET_SS_ID, label: "ACFR preset" } },
    safe_fix_kinds: ["low-contrast", "label-hygiene", "negative-without-parens", "junk-decimal",
      "missing-thousands-separator", "number-on-accounting-column", "precision-mismatch",
      "prefix-mismatch", "year-automatic-coercion"],
  });
  eq("tabLabel scan", tabLabel("scan"), "Scan");
  eq("tabLabel checks", tabLabel("checks"), "Checks");
  eq("acfr preset id", presetSpreadsheetId("acfr"), ACFR_PRESET_SS_ID);
  eq("isAcfrPreset match", isAcfrPreset(ACFR_PRESET_SS_ID), true);
  eq("isAcfrPreset other", isAcfrPreset("abc123"), false);
  eq("formatTieoutBadges full", formatTieoutBadges({
    kind: "check-tieout",
    delta: "+234",
    diagnosis: { tieout_cause: "double-aggregation", tieout_confidence: "high" },
  }), [
    { key: "cause", label: "double-aggregation", variant: "cause" },
    { key: "confidence", label: "high", variant: "confidence" },
    { key: "variance", label: "Δ +234", variant: "variance" },
  ]);
  eq("formatTieoutBadges from signature", formatTieoutBadges({
    signature: "Gov_Funds CY Total assets Δ=+100.00",
    diagnosis: { tieout_cause: "reclass" },
  })[1].label, "Δ +100.00");
  eq("isTextTrapRefusal yes", isTextTrapRefusal({
    status: "refused",
    reason: "formula contains TEXT() — valueFormat write can rewrite scale (W21 trap); fix in Workiva UI",
  }), true);
  eq("isTextTrapRefusal no", isTextTrapRefusal({ status: "refused", reason: "rich text" }), false);
  eq("exportProofChecklistFooter", exportProofChecklistFooter().includes("raster proof"), true);
  eq("exportProofChecklistIntro manual", exportProofChecklistIntro().includes("does not publish"), true);
  eq("exportProofFilterBanner no button", exportProofFilterBanner().includes("no one-click export"), true);
  eq("exportProofVerdictHint", exportProofVerdictHint().includes("five steps"), true);
  eq("shouldShowThermoAlert acfr tieout", shouldShowThermoAlert(ACFR_PRESET_SS_ID, {
    applied: true, suite: "tieout",
  }), true);
  eq("shouldShowThermoAlert non-acfr", shouldShowThermoAlert("abc", { applied: true, suite: "tieout" }), false);
  eq("thermoAlertCopy", thermoAlertCopy().includes("export-ready"), true);
})();

// --- P1: checks summary + scorecard age + bulk confirm ---
(function () {
  var NOW = Date.parse("2026-06-17T12:00:00Z");
  eq("checksSuiteDisplayName tieout", checksSuiteDisplayName("tieout"), "Tieout");
  eq("checksSuiteDisplayName hardening_gate", checksSuiteDisplayName("hardening_gate"), "Hardening Gate");
  eq("formatElapsedSeconds", formatElapsedSeconds(2.14), "2.1s");
  eq("shouldShowChecksSummaryCard yes", shouldShowChecksSummaryCard({ requested: true, suite: "tieout" }), true);
  eq("shouldShowChecksSummaryCard no", shouldShowChecksSummaryCard(null), false);
  eq("formatChecksSummaryLines applied", formatChecksSummaryLines({
    requested: true, suite: "tieout", applied: true, fail_count: 2, elapsed_s: 3.2,
  }).map(function (l) { return l.key; }), ["suite", "status", "elapsed", "fail"]);
  eq("formatChecksSummaryLines skipped", formatChecksSummaryLines({
    requested: true, suite: "hardening_gate", skipped: "ACFR-preset only",
  }).find(function (l) { return l.key === "skipped"; }).value, "ACFR-preset only");
  eq("formatChecksSummaryLines hardening_gate dry-run", formatChecksSummaryLines({
    requested: true, suite: "hardening_gate", applied: true, fail_count: 1,
    planned_operations: 17, elapsed_s: 8,
  }).find(function (l) { return l.key === "dryrun"; }).value, "17");
  eq("scorecardAgeState fresh", scorecardAgeState("2026-06-17T08:00:00Z", NOW).stale, false);
  eq("scorecardAgeState stale", scorecardAgeState("2026-06-15T08:00:00Z", NOW).stale, true);
  eq("scorecardAgeState unknown", scorecardAgeState(null, NOW).label, "scorecard age unknown");
  eq("formatScorecardTimestamp", formatScorecardTimestamp("2026-06-17T05:35:22Z").includes("2026-06-17"), true);
  eq("resolveScorecardGeneratedAt item", resolveScorecardGeneratedAt(null, {
    scorecard_generated_at: "2026-06-16T10:00:00",
  }), "2026-06-16T10:00:00");
  eq("resolveScorecardGeneratedAt checks", resolveScorecardGeneratedAt({
    tieout_enrich: { scorecard_generated_at: "2026-06-10T10:00:00" },
  }), "2026-06-10T10:00:00");
  eq("bulkApplyConfirmCopy apply title", bulkApplyConfirmCopy(3).title, "Apply 3 safe fixes?");
  eq("bulkApplyConfirmCopy apply destructive", bulkApplyConfirmCopy(2).destructive, true);
  eq("bulkApplyConfirmCopy apply body", bulkApplyConfirmCopy(1).body.includes("Workiva"), true);
})();

// --- P2: command palette + formula chip + panel width ---
(function () {
  var {
    PANEL_WIDTH_NARROW, PANEL_WIDTH_WIDE,
    PANEL_MIN_WIDTH, PANEL_MAX_WIDTH, PANEL_MIN_HEIGHT,
    clampPanelWidth, clampPanelHeight, defaultPanelWidth, panelSizeIsCustom,
    buildCommandList, normalizeCommandQuery, filterCommands, groupCommands,
    commandSearchHaystack,
    shouldShowFormulaPartialChip, formulaPartialChipLabel, formulaPartialChipTooltip,
  } = require("./wingman-core.js");
  eq("panel width narrow", PANEL_WIDTH_NARROW, 372);
  eq("panel width wide", PANEL_WIDTH_WIDE, 480);
  eq("clampPanelWidth min", clampPanelWidth(100, 1200), PANEL_MIN_WIDTH);
  eq("clampPanelWidth max", clampPanelWidth(900, 800), Math.min(PANEL_MAX_WIDTH, Math.floor(800 * 0.92)));
  eq("clampPanelHeight min", clampPanelHeight(100, 900), PANEL_MIN_HEIGHT);
  eq("defaultPanelWidth wide", defaultPanelWidth(true), PANEL_WIDTH_WIDE);
  eq("panelSizeIsCustom height", panelSizeIsCustom({ width: 372, height: 500 }, false), true);
  eq("panelSizeIsCustom preset narrow", panelSizeIsCustom({ width: 372 }, false), false);
  eq("panelSizeIsCustom wide width", panelSizeIsCustom({ width: 480 }, false), true);
  eq("buildCommandList count", buildCommandList({ hasWorkbookScan: true }).length, 8);
  eq("copy_report disabled without workbook", buildCommandList({ hasWorkbookScan: false })
    .find(function (c) { return c.id === "copy_report"; }).disabled, true);
  eq("normalizeCommandQuery trim", normalizeCommandQuery("  Scan "), "scan");
  eq("filterCommands tieout action", filterCommands(buildCommandList({ hasWorkbookScan: true }), "re-run tieout")
    .map(function (c) { return c.id; }), ["tieout"]);
  eq("filterCommands empty query", filterCommands(buildCommandList({ hasWorkbookScan: true }), "").length, 8);
  eq("filterCommands skips disabled", filterCommands(buildCommandList({ hasWorkbookScan: false }), "copy")
    .some(function (c) { return c.id === "copy_report"; }), false);
  eq("groupCommands order", groupCommands(buildCommandList({ hasWorkbookScan: true })).map(function (g) { return g.name; }),
    ["Navigation", "Actions", "Tabs"]);
  eq("commandSearchHaystack", commandSearchHaystack({ label: "Jump", group: "Nav", keywords: ["goto"] }).includes("jump"), true);
  eq("shouldShowFormulaPartialChip scan partial", shouldShowFormulaPartialChip({ formula_fetch: { partial: true } }, "scan"), true);
  eq("shouldShowFormulaPartialChip scan full", shouldShowFormulaPartialChip({ formula_fetch: { partial: false, enabled: true } }, "scan"), false);
  eq("shouldShowFormulaPartialChip checks tab", shouldShowFormulaPartialChip({ formula_fetch: { partial: true } }, "checks"), false);
  eq("shouldShowFormulaPartialChip missing meta", shouldShowFormulaPartialChip({}, "scan"), true);
  eq("formulaPartialChipLabel", formulaPartialChipLabel(), "Formula scan partial — literals only");
  eq("formulaPartialChipTooltip reason", formulaPartialChipTooltip({ reason: "flag off" }).includes("flag off"), true);
  eq("formulaPartialChipTooltip default", formulaPartialChipTooltip(null).includes("WINGMAN_FORMULA_FETCH"), true);
})();

// --- extension context guard ---
(function () {
  eq("isExtensionContextValid no chrome", isExtensionContextValid(), false);
  eq("isExtensionContextError invalidated", isExtensionContextError(new Error("Extension context invalidated.")), true);
  eq("isExtensionContextError other", isExtensionContextError(new Error("Could not establish connection")), false);
  eq("isExtensionContextError string", isExtensionContextError("context invalidated"), true);
})();

// --- reload tab button helpers ---
(function () {
  eq("RELOAD_TAB_PILL_TEXT", RELOAD_TAB_PILL_TEXT, "reload tab");
  eq("isReloadTabPrompt pill", isReloadTabPrompt("reload tab"), true);
  eq("isReloadTabPrompt refresh workiva", isReloadTabPrompt("Refresh this Workiva tab, then reopen Wingman."), true);
  eq("isReloadTabPrompt extension reloaded", isReloadTabPrompt("Extension reloaded — refresh tab"), true);
  eq("isReloadTabPrompt unrelated", isReloadTabPrompt("Re-run Wingman scan after publish/cache refresh"), false);
  eq("shouldShowReloadTabButton invalidated", shouldShowReloadTabButton({ invalidated: true }), true);
  eq("shouldShowReloadTabButton prompt text", shouldShowReloadTabButton({ text: "refresh tab" }), true);
  eq("shouldShowReloadTabButton unrelated", shouldShowReloadTabButton({ text: "can't read cell" }), false);
  eq("reloadTabButtonLabel pill", reloadTabButtonLabel("pill"), "Reload tab");
  eq("reloadTabButtonLabel panel", reloadTabButtonLabel("panel"), "Refresh sheet");
  eq("reloadTabButtonLabel toast", reloadTabButtonLabel("toast"), "Reload tab");
})();

// --- Wave B: column batch + gated format + blank-DL checklist ---
(function () {
  eq("parseColumnLetter", parseColumnLetter("B12"), "B");
  eq("parseColumnLetter range", parseColumnLetter("b2:d10"), "B");
  eq("groupAddrsByColumn", groupAddrsByColumn(["B1", "B2", "C3"]), { B: ["B1", "B2"], C: ["C3"] });
  eq("filterAddrsByColumn", filterAddrsByColumn(["B1", "B2", "C3"], "B"), ["B1", "B2"]);
  eq("isGatedFormatKind yes", isGatedFormatKind("junk-decimal"), true);
  eq("isGatedFormatKind no", isGatedFormatKind("low-contrast"), false);
  var gatedGroup = {
    kind: "missing-thousands-separator",
    fix_lane: "surfaced",
    addrs: ["E5", "E6"],
    gated_target: {
      column: "E",
      valueFormat: { showThousandsSeparator: true },
      afterFormat: "ACCOUNTING, thousands",
    },
  };
  eq("resolveGatedTargets flat", resolveGatedTargets(gatedGroup).length, 1);
  eq("hasGatedFormatApply true", hasGatedFormatApply(gatedGroup), true);
  eq("legacy zero-display target cannot enable column apply",
    hasGatedFormatApply({ ...gatedGroup, kind: "zero-display-mismatch" }), false);
  eq("columnApplyConfirmCopy apply destructive", columnApplyConfirmCopy(3, "E", "apply", "ACCOUNTING").destructive, true);
  eq("columnApplyConfirmCopy fix body", columnApplyConfirmCopy(2, "E", "fix", "ACCOUNTING").body.includes("column E"), true);
  eq("columnSafeApplyLabel", columnSafeApplyLabel(4, "B"), "Apply column B (4)");
  eq("blankDlGuidedTitle complete", blankDlGuidedTitle({ complete: true, done: 4, total: 4 }), "Blank-DL checklist complete");
  eq("guidedCompletionBadge blank-dl", guidedCompletionBadge({ complete: true, done: 4, total: 4 }, "blank-dl"),
    "Checklist complete · re-scan after publish");
  eq("bulkApplyConfirmCopy column scope note", columnApplyConfirmCopy(1, "K", "apply").title.includes("K"), true);
})();

// --- rollup freshness + action receipt ---
(function () {
  var { isRollupStale, ROLLUP_STALE_MS, actionReceiptCopy } = require("./wingman-core.js");
  var BASE = Date.parse("2026-06-26T12:00:00Z");
  eq("isRollupStale false when fresh", isRollupStale({ generated_at: "2026-06-26T11:50:00Z" }, BASE, ROLLUP_STALE_MS), false);
  eq("isRollupStale true when old", isRollupStale({ generated_at: "2026-06-26T09:00:00Z" }, BASE, ROLLUP_STALE_MS), true);
  eq("isRollupStale false no ts", isRollupStale({}, BASE, ROLLUP_STALE_MS), false);
  eq("isRollupStale false null rollup", isRollupStale(null, BASE, ROLLUP_STALE_MS), false);
  eq("isRollupStale custom threshold", isRollupStale({ generated_at: "2026-06-26T11:50:00Z" }, BASE, 5 * 60 * 1000), true);
  eq("actionReceiptCopy basic", actionReceiptCopy({ applied: 3, readback: "ok" }, { sheet: "Net Assets", timestamp: "2026-06-26T12:00:00Z" }),
    'Applied 3 fixes · 2026-06-26T12:00:00Z · sheet: "Net Assets" · readback: ok');
  eq("actionReceiptCopy singular", actionReceiptCopy({ applied: 1 }).startsWith("Applied 1 fix"), true);
  eq("actionReceiptCopy dry run", actionReceiptCopy({ applied: 2, dry_run: true }).includes("dry-run only"), true);
  eq("actionReceiptCopy kind", actionReceiptCopy({ applied: 1, kind: "low-contrast" }).includes("kind: low-contrast"), true);
  eq("actionReceiptCopy empty result", actionReceiptCopy(null), "");
  eq("actionReceiptCopy readback check", actionReceiptCopy({ applied: 2, readback: "mismatch" }).includes("readback: check"), true);
})();

// --- operator config warnings ---
(function () {
  eq("operator warning null safe", operatorConfigWarningLines(null), []);
  eq("operator warning passes redacted service warnings", operatorConfigWarningLines({
    operator_config: { warnings: [" WINGMAN_TOKEN default "], workiva_client_id: "present", workiva_client_secret: "present" },
  }), ["WINGMAN_TOKEN default"]);
  eq("operator warning missing Workiva creds", operatorConfigWarningLines({
    operator_config: { warnings: [], workiva_client_id: "missing", workiva_client_secret: "present" },
  }).some(function (line) { return line.indexOf("Workiva credentials are missing") >= 0; }), true);
})();

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);

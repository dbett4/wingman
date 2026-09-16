// Workiva Copilot — cell-ID reader (content script, isolated world).
// PRIMARY identify path: read the active-cell address from DOM app-chrome
// (div.dt-formula-cell-indicator). Pure logic (classifyAddress, DriftGuard) is
// unit-tested in node (content.test.js); the DOM/observer wiring is browser-only.
(function (root) {
  "use strict";

  var SINGLE = /^[A-Z]{1,3}[1-9][0-9]{0,6}$/;
  var RANGE  = /^[A-Z]{1,3}[1-9][0-9]{0,6}:[A-Z]{1,3}[1-9][0-9]{0,6}$/;

  // Pure: classify whatever the selector returned.
  function classifyAddress(text) {
    if (text == null) return { status: "drift-null" };
    var t = String(text).trim();
    if (t === "") return { status: "empty" };          // no cell selected
    if (SINGLE.test(t)) return { status: "ok", addr: t, isRange: false };
    if (RANGE.test(t))  return { status: "ok", addr: t, isRange: true };
    return { status: "drift-shape", raw: t.slice(0, 24) };
  }

  // Pure: drift guard + fallback escalation. Once the selector has worked,
  // K consecutive null/shape reads => declare drift and step the fallback tier
  // (dom -> ocr -> typed). "empty" (no selection) is not drift.
  function DriftGuard(threshold) {
    this.threshold = threshold || 3;
    this.everOk = false;
    this.fails = 0;
  }
  DriftGuard.prototype.record = function (status) {
    if (status === "ok") { this.everOk = true; this.fails = 0; return { tier: "dom", drift: false }; }
    if (status === "empty") { return { tier: "dom", drift: false }; }
    this.fails++;
    var drift = this.everOk && this.fails >= this.threshold;
    var tier = !drift ? "dom" : (this.fails < this.threshold * 2 ? "ocr" : "typed");
    return { tier: tier, drift: drift };
  };

  // Pure: turn a /scan-workbook response into a shareable markdown review report. Lives in the
  // tested layer (no DOM) so the handoff artifact's shape is covered by content.test.js.
  function _sevRank(s) { return s === "high" ? 2 : s === "medium" ? 1 : 0; }
  function allWorkbookGroups(data) {
    var out = [];
    ((data && data.sheets) || []).forEach(function (s) {
      (s.groups || []).forEach(function (g) {
        out.push({ sheet: s, group: g });
      });
    });
    return out;
  }
  function connectedReportingReadiness(data) {
    var pairs = allWorkbookGroups(data);
    var status = {
      safeFixes: 0,
      sourceBlocked: 0,
      formulaGaps: 0,
      linkProof: 0,
      exportProof: 0,
      linkHealth: { healthy: 0, no_dest: 0, destination: 0, orphaned: 0 },
    };
    pairs.forEach(function (p) {
      var g = p.group || {}, n = g.count || 0, kind = g.kind || "";
      if (g.fix_lane === "safe-auto" || g.fixable) status.safeFixes += n;
      if (isFormulaGapKind(kind)) status.formulaGaps += n;
      if (/source|mapping|acctmap|tieout|hardcoded|formula-evaluates-blank/.test(kind)) status.sourceBlocked += n;
      if (/link|linked|publish|destination/.test(kind)) status.linkProof += n;
      if (isExportProofItem(g) || /export|render|publish/.test(kind)) status.exportProof += n;
      var lhs = g.linkHealthStatus || g.link_health_status || g.status;
      if (lhs && Object.prototype.hasOwnProperty.call(status.linkHealth, lhs)) {
        status.linkHealth[lhs] += n || 1;
      }
    });
    var lines = ["", "## Connected Reporting Readiness", ""];
    lines.push("- Source refresh / mappings: " + (status.sourceBlocked ?
      (status.sourceBlocked + " blocker" + (status.sourceBlocked === 1 ? "" : "s") + " need source data, mapping, or tieout judgment before document edits.") :
      "no source/mapping blockers surfaced in this scan."));
    lines.push("- Formula architecture: " + (status.formulaGaps ?
      (status.formulaGaps + " formula gap" + (status.formulaGaps === 1 ? "" : "s") + " require formula/source architecture review.") :
      "no formula architecture gaps surfaced in this scan."));
    lines.push("- Document links / link health: " + (status.linkProof ?
      (status.linkProof + " link or publish proof item" + (status.linkProof === 1 ? "" : "s") + " need destination/link-state proof.") :
      "no link-health or linked-range gaps surfaced in this scan."));
    var lh = status.linkHealth;
    if (lh.healthy || lh.no_dest || lh.destination || lh.orphaned) {
      lines.push("  - Link vocabulary: healthy " + lh.healthy + ", no_dest " + lh.no_dest +
        ", destination " + lh.destination + ", orphaned " + lh.orphaned + ".");
    }
    lines.push("- Publish / render / export proof: " + (status.exportProof ?
      (status.exportProof + " proof gate" + (status.exportProof === 1 ? "" : "s") + " remain open; tieout alone is not delivery proof.") :
      "no publish/render/export proof gaps surfaced by this scan."));
    lines.push("- Next safe action: " + (status.safeFixes ?
      ("review/apply " + status.safeFixes + " safe-auto cell fix" + (status.safeFixes === 1 ? "" : "es") + " with readback; keep source/link/publish items guided only.") :
      "resolve source/link/publish proof gaps outside safe-auto before claiming readiness."));
    return lines;
  }
  function _n(v) { return Number(v || 0); }
  function linkHealthSummary(linkFetch) {
    var lh = linkFetch && linkFetch.linkHealth;
    var s = lh && lh.summary ? lh.summary : null;
    if (!s) return null;
    return {
      total: _n(s.total), healthy: _n(s.healthy), no_dest: _n(s.no_dest),
      destination: _n(s.destination), orphaned: _n(s.orphaned),
    };
  }
  function publishStateSummary(linkFetch) {
    var ps = linkFetch && linkFetch.publishState;
    var s = ps && ps.summary ? ps.summary : null;
    if (!s) return null;
    return {
      total: _n(s.total), published: _n(s.published), unpublished: _n(s.unpublished),
      source_only: _n(s.source_only), destination_only: _n(s.destination_only), unknown: _n(s.unknown),
    };
  }
  function connectedReadiness(data) {
    data = data || {};
    var t = tallyGroups(data.items || []);
    var lf = data.link_fetch || null;
    var lh = linkHealthSummary(lf);
    var ps = publishStateSummary(lf);
    var formula = data.formula_fetch || null;
    var checks = data.checks || null;
    var gaps = [];
    var lines = [];
    if (lf && lf.enabled) {
      lines.push("Source/link refresh: rangeLinks read (" + _n(lf.sourceLinks) + " source, " + _n(lf.destinationLinks) + " destination).");
    } else {
      gaps.push("source refresh evidence missing");
      lines.push("Source/link refresh: no rangeLinks evidence on this scan.");
    }
    if (formula && formula.enabled && !formula.partial) {
      lines.push("Formula architecture: formulas fetched for scanned cells; formula-gap count " + t.formulaGaps + ".");
    } else if (formula && formula.enabled && formula.partial) {
      gaps.push("formula scan partial");
      lines.push("Formula architecture: partial formula fetch — treat unmapped formula gaps as unproven.");
    } else {
      gaps.push("formula evidence missing");
      lines.push("Formula architecture: formula fetch not proven in this scan.");
    }
    if (lh) {
      lines.push("Document links: " + lh.healthy + " healthy, " + lh.no_dest + " no_dest, " + lh.destination + " destination, " + lh.orphaned + " orphaned.");
      if (lh.no_dest) gaps.push("source links lack destination evidence");
      if (lh.orphaned) gaps.push("orphaned link markers");
    } else {
      lines.push("Document links: link-health vocabulary not available yet.");
    }
    if (ps) {
      lines.push("Publish state: " + ps.published + " published, " + ps.unpublished + " unpublished/stale, " + ps.source_only + " source-only, " + ps.destination_only + " destination-only, " + ps.unknown + " unknown.");
      if (ps.unpublished) gaps.push("unpublished linked ranges");
      if (ps.source_only || ps.destination_only || ps.unknown) gaps.push("incomplete publish-state proof");
    } else {
      lines.push("Publish state: revision proof not available yet.");
    }
    if (_n(lf && lf.emptyLinkedBlocks)) gaps.push("empty linked document ranges");
    if (_n(lf && lf.dlSourceNumericFormulas)) {
      gaps.push("DL source cells with numeric formulas — document receives raw digits");
      lines.push("DL numeric source: " + _n(lf.dlSourceNumericFormulas) + " source link" +
        (_n(lf.dlSourceNumericFormulas) === 1 ? "" : "s") +
        " have ACCOUNTING/NUMBER/CURRENCY numeric formulas — document links will render raw digits; wrap with TEXT() before linking.");
    }
    if (checks && checks.requested && checks.applied) {
      lines.push("Publish/export proof: " + (checks.fail_count ? checks.fail_count + " check FAIL rows remain" : "checks green") + " — still not a rendered PDF proof.");
      if (checks.fail_count) gaps.push("check FAIL rows");
    } else {
      gaps.push("publish/export proof not run");
      lines.push("Publish/export proof: not run here; publish state and rendered/export proof remain open.");
    }
    if (t.fixable) lines.push("Next action: " + t.fixable + " safe fix" + (t.fixable === 1 ? "" : "es") + " can be dry-run before apply.");
    else if (gaps.length) lines.push("Next action: resolve " + gaps[0] + ".");
    else lines.push("Next action: run publish/render proof before calling the report ready.");
    var state = gaps.length ? "needs-proof" : "ready-for-proof";
    return {
      state: state,
      title: state === "ready-for-proof" ? "Connected reporting: ready for proof" : "Connected reporting: proof gaps",
      gaps: gaps,
      lines: lines,
    };
  }
  function reportMetaLines(data) {
    var meta = [];
    var generated = data.generated_at || data.generatedAt || data.completed_at || data.completedAt;
    if (generated) meta.push("- Generated: " + generated);
    var build = data.extension_build || data.extensionBuild || (data.version && data.version.extension_build);
    if (build) meta.push("- Extension build: " + build);
    var status = data.service_status || data.serviceStatus || data.service;
    if (status) meta.push("- Service status: " + status);
    var scope = data.scope || data.scan_scope || data.scanScope;
    if (scope) meta.push("- Scan scope: " + scope);
    if (data.stale || data.isStale) meta.push("- Stale warning: this report may not match the current workbook state; re-run scan before applying fixes or calling readiness.");
    return meta;
  }

  var ROLLUP_STALE_MS = 30 * 60 * 1000;
  function isRollupStale(rollup, nowMs, thresholdMs) {
    var ts = rollup && rollup.generated_at;
    if (!ts) return false;
    var gen = Date.parse(ts);
    if (!gen || isNaN(gen)) return false;
    var now = nowMs != null ? nowMs : Date.now();
    var threshold = thresholdMs != null ? thresholdMs : ROLLUP_STALE_MS;
    return (now - gen) > threshold;
  }

  function actionReceiptCopy(result, opts) {
    opts = opts || {};
    var parts = [];
    var applied = result && (result.applied != null ? result.applied : (result.ok ? 1 : 0));
    if (applied != null) parts.push("Applied " + applied + " fix" + (applied === 1 ? "" : "es"));
    var ts = (opts.timestamp) || (result && result.timestamp) || null;
    if (ts) parts.push(String(ts));
    var sheet = opts.sheet || (result && (result.sheetName || result.sheet_name));
    if (sheet) parts.push('sheet: "' + sheet + '"');
    var kind = opts.kind || (result && result.kind);
    if (kind) parts.push("kind: " + kind);
    var readback = result && result.readback;
    if (readback) parts.push("readback: " + (readback === "ok" || readback === true ? "ok" : "check"));
    if (result && result.dry_run) parts.push("dry-run only");
    return parts.join(" · ");
  }

  function buildReport(data) {
    if (!data || !data.sheets) return "";
    var lines = ["# Wingman review", ""];
    var meta = reportMetaLines(data);
    if (meta.length) lines = lines.concat(meta, [""]);
    var withIssues = data.sheets.filter(function (s) { return s.findingCount || s.error; }).length;
    lines.push((data.findingTotal || 0) + " finding" + (data.findingTotal === 1 ? "" : "s") +
      " across " + data.scanned + " sheet" + (data.scanned === 1 ? "" : "s") +
      " · " + (data.scanned - withIssues) + " clean" +
      (data.truncatedSheets ? " · capped at " + data.scanned + " sheets" : ""));
    Array.prototype.push.apply(lines, connectedReportingReadiness(data));
    data.sheets.slice().filter(function (s) { return s.findingCount || s.error; })
      .sort(function (a, b) { return (b.findingCount || 0) - (a.findingCount || 0); })
      .forEach(function (s) {
        lines.push("", "## " + (s.name || "(unnamed sheet)") + " — " +
          (s.error ? "scan error" : (s.findingCount + " finding" + (s.findingCount === 1 ? "" : "s"))) +
          (s.truncated ? " (partial)" : ""));
        if (s.error) { lines.push("- ERROR: " + s.error); return; }
        (s.groups || []).slice().sort(function (a, b) { return _sevRank(b.severity) - _sevRank(a.severity); })
          .forEach(function (g) {
            var shown = g.addrs.slice(0, 40).join(", ") + (g.addrs.length > 40 ? " +" + (g.addrs.length - 40) + " more" : "");
            var linkHealth = g.linkHealthStatus || g.link_health_status || g.status;
            lines.push("- " + g.kind.replace(/-/g, " ") + " (" + g.severity + ") ×" + g.count + ": " +
              g.signature + (linkHealth ? " [link-health: " + linkHealth + "]" : "") +
              (g.fix_lane === "safe-auto" ? " [fixable]" : "") + " — " + shown);
          });
      });
    if (!withIssues) lines.push("", "No issues found.");
    return lines.join("\n");
  }

  // ---- triage + panel filters (pure, unit-tested) ----

  // Structural formula/source issues — require AcctMap/SUMIFS/link judgment, not format fixes.
  // Shown as a separate "formula gaps" count in the triage line so users distinguish them from
  // format-only review items (mixed column negatives, precision drift, etc.).
  var FORMULA_GAP_KINDS = {
    "hardcoded-face-value": true,
    "hardcoded-text-criteria-in-formula": true,
    "hardcoded-constant-in-formula": true,
    "round-wrapper-workaround": true,
    "unbounded-full-column-sumifs": true,
    "dead-unsupported-function": true,
    "degenerate-placeholder-formula": true,
    "formula-evaluates-blank": true,
    "broken-ref": true,
    "display-wrapper": true,
  };
  function isFormulaGapKind(kind) {
    return !!(kind && FORMULA_GAP_KINDS[kind]);
  }

  function tallyGroups(groups) {
    var t = { issues: 0, high: 0, fixable: 0, review: 0, formulaGaps: 0, exportProof: 0 };
    (groups || []).forEach(function (g) {
      var n = g.count || 0; t.issues += n;
      if (g.severity === "high") t.high += n;
      if (g.fix_lane === "safe-auto") t.fixable += n;
      else if (isFormulaGapKind(g.kind)) t.formulaGaps += n;
      else t.review += n;
      if (isExportProofItem(g)) t.exportProof += n;
    });
    return t;
  }
  function triageText(groups) {
    var t = tallyGroups(groups);
    if (!t.issues) return "no issues";
    return t.issues + " issue" + (t.issues === 1 ? "" : "s") +
      (t.high ? " · " + t.high + " high" : "") +
      (t.fixable ? " · " + t.fixable + " fixable" : "") +
      (t.formulaGaps ? " · " + t.formulaGaps + " formula gap" + (t.formulaGaps === 1 ? "" : "s") : "") +
      (t.review ? " · " + t.review + " review" : "");
  }
  function filterGroupsByLane(groups, lane) {
    if (!lane || lane === "all") return (groups || []).slice();
    if (lane === "fixable") return (groups || []).filter(function (g) { return g.fix_lane === "safe-auto"; });
    if (lane === "export") return (groups || []).filter(isExportProofItem);
    return (groups || []).filter(function (g) { return g.fix_lane !== "safe-auto"; });
  }
  function collectSafeFixAddrs(groups) {
    var out = [];
    (groups || []).forEach(function (g) {
      if (!g || g.fix_lane !== "safe-auto" || !g.fixable) return;
      (g.addrs || []).forEach(function (a) { out.push({ addr: a, group: g }); });
    });
    return out;
  }
  // Turn a workbook-scoped /api/queue payload into the sheet rollup shape used by
  // buildReport + renderSheetList (items carry sheetId/sheetName on each row).
  function queueToWorkbookRollup(q) {
    if (!q || !q.items) return { findingTotal: 0, scanned: 0, truncatedSheets: false, sheets: [] };
    var sheetMap = {};
    q.items.forEach(function (item) {
      var sid = item.sheetId || "";
      if (!sheetMap[sid]) {
        sheetMap[sid] = {
          sheetId: sid, name: item.sheetName || "(unnamed sheet)",
          groups: [], findingCount: 0, truncated: false, error: null,
        };
      }
      var s = sheetMap[sid];
      if (item.kind === "scan-error") s.error = item.signature;
      else { s.groups.push(item); s.findingCount += item.count || 0; }
    });
    var sheets = Object.keys(sheetMap).map(function (k) { return sheetMap[k]; });
    return {
      findingTotal: q.issueCount || 0,
      scanned: q.scanned || sheets.length,
      truncatedSheets: !!q.truncatedSheets,
      sheets: sheets,
    };
  }
  function groupValues(g) {
    return (g && (g.values || g.cellValues)) || null;
  }
  function addrValueLabel(addr, values) {
    if (!values || values[addr] == null) return { addr: addr, value: null };
    var v = String(values[addr]);
    if (v === "") return { addr: addr, value: null };
    if (v.length > 32) v = v.slice(0, 29) + "\u2026";
    return { addr: addr, value: v };
  }
  function segmentLabel(lane, t) {
    if (lane === "all") return "All";
    if (lane === "fixable") return "Fixable" + (t.fixable ? " " + t.fixable : "");
    if (lane === "export") return "Export proof" + (t.exportProof ? " " + t.exportProof : "");
    return "Review" + (t.review ? " " + t.review : "");
  }
  // Auto-expand grouped findings on render when the list is short. With scan noise dropped most
  // sheets return a handful of findings — forcing a click to reveal each one's cells/actions is
  // pure friction. A short list opens fully; a longer list stays collapsed so it stays scannable.
  var AUTO_EXPAND_MAX = 3;
  function shouldAutoExpandGroups(groups) {
    var n = (groups || []).length;
    return n > 0 && n <= AUTO_EXPAND_MAX;
  }

  // ---- vision crop planning (pure, unit-tested) ----
  var VISION_CROP_MAX = 80;

  function normalizeAddr(a) {
    if (a == null) return null;
    a = String(a).trim().split(":")[0].toUpperCase().replace(/\$/g, "");
    return SINGLE.test(a) ? a : null;
  }

  function collectVisionAddrs(items, cap, candidates) {
    cap = cap == null ? VISION_CROP_MAX : Math.max(0, cap);
    if (candidates && candidates.length) {
      var fromServer = [], seen = {};
      candidates.forEach(function (raw) {
        var a = normalizeAddr(raw);
        if (!a || seen[a]) return;
        seen[a] = true;
        fromServer.push(a);
      });
      return fromServer.slice(0, cap);
    }
    var priority = [], rest = [], seenLegacy = {};
    function push(addr, pri) {
      if (!addr || seenLegacy[addr]) return;
      seenLegacy[addr] = true;
      (pri ? priority : rest).push(addr);
    }
    (items || []).forEach(function (item) {
      if (!(item && item.kind === "low-contrast" && item.fix_lane !== "safe-auto")) return;
      (item.addrs || []).forEach(function (raw) { push(normalizeAddr(raw), true); });
    });
    (items || []).forEach(function (item) {
      (item && item.addrs || []).forEach(function (raw) { push(normalizeAddr(raw), false); });
    });
    return priority.concat(rest).slice(0, cap);
  }

  function collectVisionAddrsFromScan(apiData, cap) {
    return collectVisionAddrs(
      apiData && apiData.items,
      cap,
      apiData && apiData.visionCandidates,
    );
  }

  function buildVisionQueuePayload(ids, cellImages, visionEnabled) {
    return {
      spreadsheetId: ids.spreadsheetId,
      sheetId: ids.sheetId,
      vision: !!visionEnabled,
      cellImages: cellImages || {},
    };
  }

  function formatVisionStatus(vision) {
    if (!vision) return "";
    if (vision.skipped) return "vision skipped · " + vision.skipped;
    if (vision.applied) {
      if (vision.netNew != null) {
        var checked = vision.visionFindingCount != null ? vision.visionFindingCount : 0;
        if (vision.netNew === 0) return "vision on · 0 new (" + checked + " checked)";
        return "vision on · +" + vision.netNew + " new (" + checked + " pixel hits)";
      }
      var n = vision.visionFindingCount != null ? vision.visionFindingCount : 0;
      return "vision on · " + n + " pixel finding" + (n === 1 ? "" : "s");
    }
    if (vision.requested) return "vision requested · not applied";
    return "";
  }

  function formatChecksStatus(checks) {
    if (!checks || !checks.requested) return "";
    if (checks.skipped) return "checks skipped · " + checks.skipped;
    if (checks.applied) {
      var n = checks.fail_count != null ? checks.fail_count : 0;
      var suite = checks.suite || "checks";
      return suite + " · " + (n ? n + " FAIL" : "green") +
        (checks.elapsed_s != null ? " · " + checks.elapsed_s + "s" : "");
    }
    return "";
  }
  function scanCtxLine(data) {
    var parts = [
      (data.cellCount != null ? data.cellCount : "?") + " cells",
      triageText(data.items),
    ];
    if (data.truncated) parts.push("partial (large sheet)");
    var vs = formatVisionStatus(data.vision);
    if (vs) parts.push(vs);
    var cs = formatChecksStatus(data.checks);
    if (cs) parts.push(cs);
    return parts.join(" · ");
  }

  // ---- guided lanes: blank-DL + export-proof (pure, unit-tested) ----
  function isExportProofItem(group) {
    var dx = group && group.diagnosis;
    return !!(dx && (dx.guided_lane === "export-proof" || dx.pathway_id === "export-proof.guided"));
  }
  function guidedLaneId(group) {
    var dx = group && group.diagnosis;
    return (dx && dx.guided_lane) || (hasGuidedSteps(group) ? "guided" : null);
  }
  function guidedStepsStorageKey(itemId, lane) {
    return "wmGuided:" + (lane || "guided") + ":" + (itemId || "unknown");
  }
  function exportVerdictStorageKey(itemId) {
    return "wmExportVerdict:" + (itemId || "unknown");
  }
  function exportProofLabel(verdict) {
    if (verdict === "EXPORT_PROVEN") return "EXPORT_PROVEN";
    if (verdict === "NOT_PROVEN_VISUAL") return "NOT_PROVEN_VISUAL";
    return "";
  }
  function hasGuidedSteps(group) {
    var steps = group && group.diagnosis && group.diagnosis.guided_steps;
    return !!(steps && steps.length);
  }
  function initialGuidedProgress(steps) {
    var p = {};
    (steps || []).forEach(function (s) { if (s && s.id) p[s.id] = false; });
    return p;
  }
  function mergeGuidedProgress(stored, steps) {
    var base = initialGuidedProgress(steps);
    if (!stored || typeof stored !== "object") return base;
    (steps || []).forEach(function (s) {
      if (s && s.id && stored[s.id] === true) base[s.id] = true;
    });
    return base;
  }
  function toggleGuidedStep(progress, stepId) {
    var next = Object.assign({}, progress || {});
    if (stepId) next[stepId] = !next[stepId];
    return next;
  }
  function guidedProgressSummary(progress, steps) {
    var list = steps || [];
    var done = list.filter(function (s) { return s && s.id && progress && progress[s.id]; }).length;
    return { done: done, total: list.length, complete: list.length > 0 && done === list.length };
  }
  function guidedLaneLabel(summary, lane) {
    var prefix = lane === "export-proof" ? "Export proof" : "Guided steps";
    if (!summary || !summary.total) return prefix;
    return prefix + " · " + summary.done + "/" + summary.total;
  }

  // ---- tieout scorecard coord display (pure, unit-tested) ----
  function colIndexToLetter(col) {
    if (col == null || col < 1) return "";
    var c = col, s = "";
    while (c > 0) {
      var rem = (c - 1) % 26;
      s = String.fromCharCode(65 + rem) + s;
      c = Math.floor((c - 1) / 26);
    }
    return s;
  }
  function tieoutCellAddr(item) {
    if (!item) return null;
    var jh = item.jumpHint;
    if (jh && jh.addr) return jh.addr;
    if (item.addr) return item.addr;
    if (item.wb_row != null && item.wb_col != null) {
      var L = colIndexToLetter(item.wb_col);
      return L ? L + item.wb_row : null;
    }
    return null;
  }
  function tieoutCoordLabel(item) {
    var addr = tieoutCellAddr(item);
    if (!addr) return "";
    var sheet = (item.jumpHint && item.jumpHint.sheetName) || item.sheetName || "";
    var status = item.tieout_status ? " · " + item.tieout_status : "";
    return sheet ? sheet + " · " + addr + status : addr + status;
  }
  function tieoutJumpContext(item, jc) {
    var addr = tieoutCellAddr(item);
    if (!addr) return { addr: null, canJump: false, sheetName: null };
    var sheet = (item.jumpHint && item.jumpHint.sheetName) || item.sheetName || null;
    var onSheet = !jc || jc.isCurrent !== false;
    if (sheet && jc && jc.name && jc.name !== sheet) {
      onSheet = false;
    }
    return { addr: addr, canJump: onSheet, sheetName: sheet };
  }

  // ---- extension context guard (pure + browser) ----
  function isExtensionContextValid() {
    try {
      return !!(typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id);
    } catch (e) {
      return false;
    }
  }
  function isExtensionContextError(err) {
    var msg = (err && err.message) || String(err || "");
    return /extension context invalidated|context invalidated/i.test(msg);
  }
  var RELOAD_TAB_PILL_TEXT = "reload tab";
  function isReloadTabPrompt(text) {
    return /reload\s+tab|refresh\s+(this\s+)?(workiva\s+)?tab|extension\s+reloaded|context\s+invalidated/i.test(String(text || ""));
  }
  function shouldShowReloadTabButton(opts) {
    opts = opts || {};
    if (opts.invalidated) return true;
    return isReloadTabPrompt(opts.text);
  }
  function reloadTabButtonLabel(context) {
    return context === "panel" ? "Refresh sheet" : "Reload tab";
  }

  // ---- Audit Rail tabs + server-driven presets (pure, unit-tested) ----
  var _serviceConfig = null;
  var TAB_IDS = ["inspect", "scan", "checks", "workbook"];
  var TAB_LABELS = { inspect: "Inspect", scan: "Scan", checks: "Checks", workbook: "Workbook" };
  function applyServiceConfig(cfg) { _serviceConfig = cfg || null; }
  function presetSpreadsheetId(presetKey) {
    var p = _serviceConfig && _serviceConfig.presets && _serviceConfig.presets[presetKey];
    return p && p.spreadsheetId ? p.spreadsheetId : null;
  }
  function presetLabel(presetKey) {
    var p = _serviceConfig && _serviceConfig.presets && _serviceConfig.presets[presetKey];
    return p && p.label ? p.label : presetKey;
  }
  function tabLabel(tabId) { return TAB_LABELS[tabId] || tabId; }
  function normalizeSsId(ss) { return ss == null ? "" : String(ss).replace(/-/g, "").toLowerCase(); }
  function isAcfrPreset(spreadsheetId) {
    var sid = presetSpreadsheetId("acfr");
    if (!sid) return false;
    return normalizeSsId(spreadsheetId) === normalizeSsId(sid);
  }
  function formatTieoutBadges(g) {
    var badges = [];
    var dx = g && g.diagnosis;
    if (dx && dx.tieout_cause) badges.push({ key: "cause", label: String(dx.tieout_cause), variant: "cause" });
    if (dx && dx.tieout_confidence) badges.push({ key: "confidence", label: String(dx.tieout_confidence), variant: "confidence" });
    var variance = g && g.delta != null ? g.delta : (dx && dx.variance);
    if (variance == null && g && g.signature) {
      var m = String(g.signature).match(/Δ=([+\-]?[\d,.]+|n\/a)/);
      if (m) variance = m[1];
    }
    if (variance != null && variance !== "") badges.push({ key: "variance", label: "Δ " + variance, variant: "variance" });
    return badges;
  }
  function isTextTrapRefusal(r) {
    if (!r || r.status !== "refused") return false;
    return /TEXT\s*\(\s*\)/i.test(String(r.reason || ""));
  }
  function textTrapAlertCopy() {
    return "TEXT() trap — rebuild numeric layer, never valueFormat";
  }
  function exportProofChecklistFooter() {
    return "grep pass ≠ page flow / clipping / raster proof still required.";
  }
  function exportProofChecklistIntro() {
    return (
      "Manual proof checklist — complete each step in Workiva (publish links, export PDF, grep), " +
      "then check it off here. Wingman does not publish or export for you."
    );
  }
  function exportProofFilterBanner() {
    return (
      "Export proof items need client-visible verification: publish in Workiva → binary PDF export → " +
      "pdftotext grep. Open each row’s checklist below — there is no one-click export button."
    );
  }
  function exportProofVerdictHint() {
    return "After all five steps are checked, mark Proven or Not proven below.";
  }
  function shouldShowThermoAlert(spreadsheetId, checks) {
    if (!isAcfrPreset(spreadsheetId) || !checks || !checks.applied) return false;
    var suite = checks.suite || "";
    return suite === "tieout" || suite === "hardening_gate";
  }
  function thermoAlertCopy() {
    return "Workbook tieout ≠ export-ready. Export proof required before closure.";
  }

  // ---- Checks summary + scorecard age + bulk-apply confirm (pure, unit-tested) ----
  var SCORECARD_STALE_MS = 24 * 60 * 60 * 1000;

  function checksSuiteDisplayName(suite) {
    if (suite === "hardening_gate") return "Hardening Gate";
    if (suite === "tieout") return "Tieout";
    return suite || "Checks";
  }
  function formatElapsedSeconds(s) {
    if (s == null || isNaN(Number(s))) return "";
    var n = Number(s);
    return (Math.round(n * 10) / 10) + "s";
  }
  function shouldShowChecksSummaryCard(checks) {
    return !!(checks && checks.requested);
  }
  function formatChecksSummaryLines(checks) {
    if (!shouldShowChecksSummaryCard(checks)) return [];
    var lines = [];
    lines.push({ key: "suite", label: "Suite", value: checksSuiteDisplayName(checks.suite) });
    if (checks.skipped) {
      lines.push({ key: "status", label: "Status", value: "skipped" });
      lines.push({ key: "skipped", label: "Reason", value: String(checks.skipped) });
      if (checks.elapsed_s != null) {
        lines.push({ key: "elapsed", label: "Elapsed", value: formatElapsedSeconds(checks.elapsed_s) });
      }
      return lines;
    }
    lines.push({ key: "status", label: "Status", value: checks.applied ? "applied" : "not applied" });
    if (checks.elapsed_s != null) {
      lines.push({ key: "elapsed", label: "Elapsed", value: formatElapsedSeconds(checks.elapsed_s) });
    }
    if (checks.fail_count != null) {
      var fc = checks.fail_count;
      lines.push({ key: "fail", label: "FAIL rows", value: fc ? String(fc) : "0 (green)" });
    }
    if (checks.planned_operations != null) {
      lines.push({ key: "dryrun", label: "Dry-run ops", value: String(checks.planned_operations) });
    }
    if (checks.error) {
      lines.push({ key: "error", label: "Error", value: String(checks.error) });
    }
    return lines;
  }
  function parseTimestampMs(iso) {
    if (iso == null || iso === "") return null;
    var t = Date.parse(String(iso));
    return isNaN(t) ? null : t;
  }
  function formatScorecardTimestamp(generatedAt) {
    var ts = parseTimestampMs(generatedAt);
    if (ts == null) return "unknown";
    try {
      return new Date(ts).toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC");
    } catch (e) {
      return String(generatedAt);
    }
  }
  function scorecardAgeState(generatedAt, nowMs) {
    nowMs = nowMs != null ? nowMs : Date.now();
    var ts = parseTimestampMs(generatedAt);
    if (ts == null) {
      return { stale: true, label: "scorecard age unknown", tooltip: "Scorecard timestamp missing" };
    }
    var ageMs = Math.max(0, nowMs - ts);
    var stale = ageMs > SCORECARD_STALE_MS;
    var full = formatScorecardTimestamp(generatedAt);
    return {
      stale: stale,
      label: stale ? "scorecard stale" : "scorecard fresh",
      tooltip: full + (stale ? " (>24h old)" : ""),
    };
  }
  function resolveScorecardGeneratedAt(checks, item) {
    if (item) {
      if (item.scorecard_generated_at) return item.scorecard_generated_at;
      var dx = item.diagnosis;
      if (dx && dx.scorecard_generated_at) return dx.scorecard_generated_at;
    }
    if (checks && checks.scorecard_generated_at) return checks.scorecard_generated_at;
    var te = checks && checks.tieout_enrich;
    if (te && te.scorecard_generated_at) return te.scorecard_generated_at;
    return null;
  }
  // Confirm copy for the bulk write step (Apply all). The dry-run step writes nothing and no
  // longer prompts (it runs directly, like per-group "Check fixes"), so the old "fix" phase is gone.
  function bulkApplyConfirmCopy(count) {
    var n = Math.max(0, count || 0);
    var fixes = n + " safe fix" + (n === 1 ? "" : "es");
    return {
      title: "Apply " + fixes + "?",
      body: "This will write to " + n + " Workiva cell" + (n === 1 ? "" : "s") +
        ". Each apply is readback-verified when possible, but confirm scope before proceeding.",
      count: n,
      phase: "apply",
      destructive: true,
    };
  }

  // ---- Wave B: column batch + gated format apply (pure, unit-tested) ----
  var GATED_FORMAT_KINDS = {
    "junk-decimal": true,
    "missing-thousands-separator": true,
    "number-on-accounting-column": true,
    "precision-mismatch": true,
    "prefix-mismatch": true,
    "negative-without-parens": true,
  };

  function parseColumnLetter(addr) {
    var a = normalizeAddr(addr);
    if (!a) return null;
    var m = /^([A-Z]+)/.exec(a);
    return m ? m[1] : null;
  }
  function groupAddrsByColumn(addrs) {
    var out = {};
    (addrs || []).forEach(function (a) {
      var col = parseColumnLetter(a);
      if (!col) return;
      if (!out[col]) out[col] = [];
      out[col].push(a);
    });
    return out;
  }
  function filterAddrsByColumn(addrs, col) {
    if (!col) return (addrs || []).slice();
    return (addrs || []).filter(function (a) { return parseColumnLetter(a) === col; });
  }
  function isGatedFormatKind(kind) {
    return !!(kind && GATED_FORMAT_KINDS[kind]);
  }
  function resolveGatedTargets(group) {
    if (!group) return [];
    if (group.gated_columns && typeof group.gated_columns === "object") {
      return Object.keys(group.gated_columns).map(function (col) {
        var entry = group.gated_columns[col];
        return {
          column: col,
          valueFormat: entry && entry.valueFormat,
          afterFormat: entry && entry.afterFormat,
          addrCount: entry && entry.addrCount,
        };
      }).filter(function (x) { return x.valueFormat; });
    }
    if (group.gated_target && group.gated_target.valueFormat) {
      return [{
        column: group.gated_target.column || null,
        valueFormat: group.gated_target.valueFormat,
        afterFormat: group.gated_target.afterFormat,
        addrCount: null,
      }];
    }
    return [];
  }
  function hasGatedFormatApply(group) {
    return isGatedFormatKind(group && group.kind) &&
      group.fix_lane === "surfaced" &&
      resolveGatedTargets(group).length > 0;
  }
  function columnApplyConfirmCopy(count, col, phase, afterFormat) {
    var n = Math.max(0, count || 0);
    var scope = col ? ("column " + col) : "column";
    var fmt = afterFormat ? (" → " + afterFormat) : "";
    if (phase === "apply") {
      return {
        title: "Apply column format" + (col ? " (" + col + ")" : "") + "?",
        body: "Write gated valueFormat to " + n + " cell" + (n === 1 ? "" : "s") +
          " in " + scope + fmt + ". Spot-check financial rows before confirming.",
        count: n,
        phase: "apply",
        destructive: true,
      };
    }
    return {
      title: "Check column format" + (col ? " (" + col + ")" : "") + "?",
      body: "Dry-run valueFormat paste on " + n + " cell" + (n === 1 ? "" : "s") +
        " in " + scope + fmt + ". You will confirm again before any writes.",
      count: n,
      phase: "fix",
      destructive: false,
    };
  }
  function columnSafeApplyLabel(count, col) {
    var n = Math.max(0, count || 0);
    return "Apply column " + (col || "?") + " (" + n + ")";
  }
  function blankDlGuidedTitle(summary) {
    if (summary && summary.complete) return "Blank-DL checklist complete";
    return "Blank linked cell review";
  }
  function guidedCompletionBadge(summary, lane) {
    if (!summary || !summary.complete) return "";
    if (lane === "blank-dl") return "Checklist complete · re-scan after publish";
    if (lane === "export-proof") return "Checklist complete · set export verdict";
    return "Checklist complete";
  }

  // ---- command palette (pure, unit-tested) ----
  var PANEL_WIDTH_NARROW = 372;
  var PANEL_WIDTH_WIDE = 480;
  var PANEL_MIN_WIDTH = 300;
  var PANEL_MAX_WIDTH = 720;
  var PANEL_MIN_HEIGHT = 260;
  var PANEL_DEFAULT_MAX_HEIGHT_VH = 74;

  function clampPanelWidth(w, viewportW) {
    var maxW = Math.min(PANEL_MAX_WIDTH, Math.floor((viewportW == null ? 9999 : viewportW) * 0.92));
    return Math.max(PANEL_MIN_WIDTH, Math.min(maxW, Math.round(w)));
  }
  function clampPanelHeight(h, viewportH) {
    var maxH = Math.floor((viewportH == null ? 9999 : viewportH) * 0.92);
    return Math.max(PANEL_MIN_HEIGHT, Math.min(maxH, Math.round(h)));
  }
  function defaultPanelWidth(wideMode) {
    return wideMode ? PANEL_WIDTH_WIDE : PANEL_WIDTH_NARROW;
  }
  function panelSizeIsCustom(size, wideMode) {
    if (!size || size.width == null) return false;
    if (size.height != null && size.height > 0) return true;
    return size.width !== defaultPanelWidth(!!wideMode);
  }

  function buildCommandList(ctx) {
    ctx = ctx || {};
    var hasWb = !!ctx.hasWorkbookScan;
    return [
      { id: "goto", label: "Jump to cell", hint: "A1 address", group: "Navigation",
        keywords: ["goto", "jump", "cell", "address", "navigate", "wm_goto"] },
      { id: "rescan", label: "Re-run Scan", group: "Actions",
        keywords: ["scan", "rescan", "refresh", "sheet", "current", "tab"] },
      { id: "tieout", label: "Re-run Tieout checks", group: "Actions",
        keywords: ["tieout", "checks", "run_checks", "fail"] },
      { id: "hardening_gate", label: "Re-run Hardening Gate checks", group: "Actions",
        keywords: ["hardening", "gate", "checks", "acfr"] },
      { id: "copy_report", label: "Copy report", group: "Actions", disabled: !hasWb,
        keywords: ["copy", "report", "markdown", "clipboard", "workbook"] },
      { id: "tab_scan", label: "Switch to Scan tab", group: "Tabs",
        keywords: ["tab", "scan", "sheet"] },
      { id: "tab_checks", label: "Switch to Checks tab", group: "Tabs",
        keywords: ["tab", "checks", "tieout"] },
      { id: "tab_workbook", label: "Switch to Workbook tab", group: "Tabs",
        keywords: ["tab", "workbook", "sheets", "all"] },
    ];
  }
  function normalizeCommandQuery(q) {
    return String(q == null ? "" : q).trim().toLowerCase();
  }
  function commandSearchHaystack(cmd) {
    return [cmd.label, cmd.group, cmd.hint || ""].concat(cmd.keywords || []).join(" ").toLowerCase();
  }
  function filterCommands(commands, query) {
    var q = normalizeCommandQuery(query);
    return (commands || []).filter(function (c) {
      if (c.disabled) return false;
      if (!q) return true;
      return commandSearchHaystack(c).indexOf(q) >= 0;
    });
  }
  function groupCommands(commands) {
    var groups = {}, order = [];
    (commands || []).forEach(function (c) {
      var g = c.group || "Other";
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(c);
    });
    return order.map(function (g) { return { name: g, items: groups[g] }; });
  }

  // ---- formula-fetch coverage chip (pure, unit-tested) ----
  function shouldShowFormulaPartialChip(data, tabId) {
    if (tabId && tabId !== "scan") return false;
    var ff = data && data.formula_fetch;
    if (!ff) return true;
    return ff.partial !== false || ff.enabled === false;
  }
  function formulaPartialChipLabel() {
    return "Formula scan partial — literals only";
  }
  function formulaPartialChipTooltip(ff) {
    if (ff && ff.reason) {
      return ff.reason + " Enable WINGMAN_FORMULA_FETCH=1 on the local service for full formula-text detection.";
    }
    return "Wingman reads display values from sheetdata. Full formula-text detection requires WINGMAN_FORMULA_FETCH=1 on the service.";
  }

  function operatorConfigWarningLines(status) {
    var oc = status && status.operator_config;
    if (!oc) return [];
    var out = [];
    (oc.warnings || []).forEach(function (w) {
      var s = String(w == null ? "" : w).trim();
      if (s) out.push(s.slice(0, 220));
    });
    if (oc.workiva_client_id === "missing" || oc.workiva_client_secret === "missing") {
      out.push("Workiva credentials are missing in the local service environment; scans will fail until WORKIVA_CLIENT_ID and WORKIVA_CLIENT_SECRET are present.");
    }
    return out;
  }

  // Skip drag/minimize when the press starts on an interactive control.
  function isDragPassthrough(el) {
    return !!(el && el.closest && el.closest(
      "button,input,label,select,textarea,a,[contenteditable=true],.wm-btn,.wm-seg,.wm-toolbar,.wm-tabs,.wm-tab,.wm-tab-actions,.wm-cmd,.wm-cmd-input,.wm-cmd-item",
    ));
  }

  var api = {
    classifyAddress: classifyAddress, DriftGuard: DriftGuard, buildReport: buildReport,
    connectedReportingReadiness: connectedReportingReadiness,
    FORMULA_GAP_KINDS: FORMULA_GAP_KINDS, isFormulaGapKind: isFormulaGapKind,
    tallyGroups: tallyGroups, triageText: triageText, filterGroupsByLane: filterGroupsByLane,
    collectSafeFixAddrs: collectSafeFixAddrs, queueToWorkbookRollup: queueToWorkbookRollup,
    groupValues: groupValues, addrValueLabel: addrValueLabel,
    segmentLabel: segmentLabel, shouldAutoExpandGroups: shouldAutoExpandGroups,
    AUTO_EXPAND_MAX: AUTO_EXPAND_MAX, SINGLE: SINGLE, RANGE: RANGE,
    VISION_CROP_MAX: VISION_CROP_MAX, normalizeAddr: normalizeAddr,
    collectVisionAddrs: collectVisionAddrs, collectVisionAddrsFromScan: collectVisionAddrsFromScan,
    buildVisionQueuePayload: buildVisionQueuePayload,
    formatVisionStatus: formatVisionStatus, formatChecksStatus: formatChecksStatus, scanCtxLine: scanCtxLine,
    linkHealthSummary: linkHealthSummary, publishStateSummary: publishStateSummary,
    connectedReadiness: connectedReadiness,
    guidedStepsStorageKey: guidedStepsStorageKey, exportVerdictStorageKey: exportVerdictStorageKey,
    isExportProofItem: isExportProofItem, guidedLaneId: guidedLaneId, exportProofLabel: exportProofLabel,
    hasGuidedSteps: hasGuidedSteps,
    initialGuidedProgress: initialGuidedProgress, mergeGuidedProgress: mergeGuidedProgress,
    toggleGuidedStep: toggleGuidedStep, guidedProgressSummary: guidedProgressSummary,
    guidedLaneLabel: guidedLaneLabel,
    colIndexToLetter: colIndexToLetter, tieoutCellAddr: tieoutCellAddr,
    tieoutCoordLabel: tieoutCoordLabel, tieoutJumpContext: tieoutJumpContext,
    applyServiceConfig: applyServiceConfig, presetSpreadsheetId: presetSpreadsheetId,
    presetLabel: presetLabel, TAB_IDS: TAB_IDS, TAB_LABELS: TAB_LABELS,
    tabLabel: tabLabel, isAcfrPreset: isAcfrPreset,
    formatTieoutBadges: formatTieoutBadges, isTextTrapRefusal: isTextTrapRefusal,
    textTrapAlertCopy: textTrapAlertCopy, exportProofChecklistFooter: exportProofChecklistFooter,
    exportProofChecklistIntro: exportProofChecklistIntro,
    exportProofFilterBanner: exportProofFilterBanner,
    exportProofVerdictHint: exportProofVerdictHint,
    shouldShowThermoAlert: shouldShowThermoAlert, thermoAlertCopy: thermoAlertCopy,
    isDragPassthrough: isDragPassthrough,
    SCORECARD_STALE_MS: SCORECARD_STALE_MS,
    checksSuiteDisplayName: checksSuiteDisplayName, formatElapsedSeconds: formatElapsedSeconds,
    shouldShowChecksSummaryCard: shouldShowChecksSummaryCard,
    formatChecksSummaryLines: formatChecksSummaryLines,
    parseTimestampMs: parseTimestampMs, formatScorecardTimestamp: formatScorecardTimestamp,
    scorecardAgeState: scorecardAgeState, resolveScorecardGeneratedAt: resolveScorecardGeneratedAt,
    bulkApplyConfirmCopy: bulkApplyConfirmCopy,
    GATED_FORMAT_KINDS: GATED_FORMAT_KINDS,
    parseColumnLetter: parseColumnLetter,
    groupAddrsByColumn: groupAddrsByColumn,
    filterAddrsByColumn: filterAddrsByColumn,
    isGatedFormatKind: isGatedFormatKind,
    resolveGatedTargets: resolveGatedTargets,
    hasGatedFormatApply: hasGatedFormatApply,
    columnApplyConfirmCopy: columnApplyConfirmCopy,
    columnSafeApplyLabel: columnSafeApplyLabel,
    blankDlGuidedTitle: blankDlGuidedTitle,
    guidedCompletionBadge: guidedCompletionBadge,
    PANEL_WIDTH_NARROW: PANEL_WIDTH_NARROW, PANEL_WIDTH_WIDE: PANEL_WIDTH_WIDE,
    PANEL_MIN_WIDTH: PANEL_MIN_WIDTH, PANEL_MAX_WIDTH: PANEL_MAX_WIDTH,
    PANEL_MIN_HEIGHT: PANEL_MIN_HEIGHT, PANEL_DEFAULT_MAX_HEIGHT_VH: PANEL_DEFAULT_MAX_HEIGHT_VH,
    clampPanelWidth: clampPanelWidth, clampPanelHeight: clampPanelHeight,
    defaultPanelWidth: defaultPanelWidth, panelSizeIsCustom: panelSizeIsCustom,
    buildCommandList: buildCommandList, normalizeCommandQuery: normalizeCommandQuery,
    commandSearchHaystack: commandSearchHaystack, filterCommands: filterCommands,
    groupCommands: groupCommands,
    shouldShowFormulaPartialChip: shouldShowFormulaPartialChip,
    formulaPartialChipLabel: formulaPartialChipLabel,
    formulaPartialChipTooltip: formulaPartialChipTooltip,
    operatorConfigWarningLines: operatorConfigWarningLines,
    isExtensionContextValid: isExtensionContextValid,
    isExtensionContextError: isExtensionContextError,
    RELOAD_TAB_PILL_TEXT: RELOAD_TAB_PILL_TEXT,
    isReloadTabPrompt: isReloadTabPrompt,
    shouldShowReloadTabButton: shouldShowReloadTabButton,
    reloadTabButtonLabel: reloadTabButtonLabel,
    reportMetaLines: reportMetaLines,
    ROLLUP_STALE_MS: ROLLUP_STALE_MS,
    isRollupStale: isRollupStale,
    actionReceiptCopy: actionReceiptCopy,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
    return;
  }
  root.WingmanCore = api;
  Object.keys(api).forEach(function (k) { root[k] = api[k]; });
})(typeof globalThis !== "undefined" ? globalThis : this);

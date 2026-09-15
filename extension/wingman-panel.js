(function () {
  "use strict";

  // ---- browser wiring (content script) ----
  // The whole UI lives in-page in one shadow-DOM host, bottom-right: a small pill when
  // collapsed, the full tool when expanded. Clicking the pill (or the toolbar icon) opens
  // it. Workiva CSS can't touch it and ours can't leak. Top frame only.
  var SEL = "div.dt-formula-cell-indicator";
  var frame = window.top === window ? "top" : "iframe";
  document.documentElement.setAttribute("data-copilot-loaded", frame);
  // The standalone demo may itself be embedded in a preview portal. Chrome-injected
  // content scripts have no currentScript; the extension remains top-frame-only.
  var demoEmbed = document.currentScript && document.currentScript.hasAttribute("data-wingman-demo");
  if (frame !== "top" && !demoEmbed) return;

  function parseIds() {
    var m = location.href.match(/\/spreadsheet\/([0-9a-fA-F]+)[\s\S]*?\/sheet\/([0-9a-fA-F]+)/);
    return m ? { spreadsheetId: m[1], sheetId: m[2] } : null;
  }
  var hasChrome = isExtensionContextValid();
  var LOGO = hasChrome && chrome.runtime.getURL ? chrome.runtime.getURL("icons/icon128.png") : "";
  var extensionInvalidated = false;
  var domObserver = null;

  function markExtensionInvalidated() {
    if (extensionInvalidated) return;
    extensionInvalidated = true;
    hasChrome = false;
    clearInterval(inspectTimer);
    inspectRequest++;
    inspectState = { error: "Extension reloaded. Refresh this Workiva tab and reopen Wingman." };
    if (open && activeTab === "inspect" && panelUi) paintInspection();
    if (domObserver) { domObserver.disconnect(); domObserver = null; }
    if (toastT) { clearTimeout(toastT); toastT = null; }
    pillState = "drift";
    pillValue = RELOAD_TAB_PILL_TEXT;
    if (!open) renderPill();
  }
  function guardExtensionContext() {
    if (extensionInvalidated) return false;
    if (isExtensionContextValid()) return true;
    markExtensionInvalidated();
    return false;
  }
  function storageGet(keys, cb) {
    if (!guardExtensionContext() || !chrome.storage) return cb({});
    try {
      chrome.storage.local.get(keys, function (r) {
        if (chrome.runtime.lastError) {
          if (isExtensionContextError(chrome.runtime.lastError)) markExtensionInvalidated();
          return cb({});
        }
        cb(r || {});
      });
    } catch (e) {
      if (isExtensionContextError(e)) markExtensionInvalidated();
      cb({});
    }
  }
  function storageSet(obj) {
    if (!guardExtensionContext() || !chrome.storage) return;
    try {
      chrome.storage.local.set(obj, function () {
        if (chrome.runtime.lastError && isExtensionContextError(chrome.runtime.lastError)) {
          markExtensionInvalidated();
        }
      });
    } catch (e) {
      if (isExtensionContextError(e)) markExtensionInvalidated();
    }
  }

  // Service calls go through the background worker so they carry the extension origin.
  function svc(path, opts) {
    if (!guardExtensionContext()) {
      return Promise.reject(new Error("Extension reloaded — refresh this Workiva tab"));
    }
    return new Promise(function (resolve, reject) {
      try {
        chrome.runtime.sendMessage({ type: "WM_API", path: path, opts: opts }, function (resp) {
          if (chrome.runtime.lastError) {
            if (isExtensionContextError(chrome.runtime.lastError)) markExtensionInvalidated();
            return reject(new Error(chrome.runtime.lastError.message || "extension messaging failed"));
          }
          if (!resp) return reject(new Error("extension messaging failed"));
          if (resp.offline) return reject(Object.assign(new Error("service not reachable"), { offline: true }));
          if (!resp.ok) return reject(new Error((resp.data && resp.data.error) || resp.error || ("HTTP " + resp.status)));
          resolve(resp.data);
        });
      } catch (e) {
        if (isExtensionContextError(e)) markExtensionInvalidated();
        reject(e);
      }
    });
  }
  function post(path, bodyObj) {
    return svc(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(bodyObj) });
  }
  var serviceConfigLoaded = false;
  function ensureServiceConfig() {
    if (serviceConfigLoaded) return Promise.resolve();
    return svc("/config").then(function (cfg) {
      applyServiceConfig(cfg);
      serviceConfigLoaded = true;
      updateAcfrChip();
    }).catch(function () {});
  }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function loadGuidedProgress(itemId, lane, steps, cb) {
    var key = guidedStepsStorageKey(itemId, lane);
    if (!guardExtensionContext() || !chrome.storage) return cb(mergeGuidedProgress(null, steps));
    storageGet([key], function (r) {
      cb(mergeGuidedProgress(r && r[key], steps));
    });
  }
  function saveGuidedProgress(itemId, lane, progress) {
    if (!guardExtensionContext() || !chrome.storage) return;
    var o = {}; o[guidedStepsStorageKey(itemId, lane)] = progress;
    storageSet(o);
  }
  function loadExportVerdict(itemId, cb) {
    if (!guardExtensionContext() || !chrome.storage) return cb(null);
    storageGet([exportVerdictStorageKey(itemId)], function (r) {
      cb(r && r[exportVerdictStorageKey(itemId)]);
    });
  }
  function saveExportVerdict(itemId, verdict) {
    if (!guardExtensionContext() || !chrome.storage) return;
    var o = {}; o[exportVerdictStorageKey(itemId)] = verdict;
    storageSet(o);
  }

  function appendGuidedChecklist(det, g, jc) {
    var steps = g.diagnosis && g.diagnosis.guided_steps;
    if (!steps || !steps.length) return;
    var lane = guidedLaneId(g) || "guided";
    var itemId = g.id || (g.kind + ":" + (g.signature || ""));
    var ep = g.diagnosis && g.diagnosis.export_proof;
    var startCollapsed = lane === "blank-dl";
    var wrap = el("div", "wm-guided" + (startCollapsed ? "" : " open"));
    var hd = el("button", "wm-guided-hd");
    hd.type = "button";
    var chev = el("span", "wm-guided-chev", "\u25B6");
    var lbl = el("span", "wm-guided-lbl", guidedLaneLabel(guidedProgressSummary(null, steps), lane));
    var badge = el("span", "wm-guided-badge");
    badge.style.display = "none";
    hd.appendChild(chev);
    hd.appendChild(lbl);
    hd.appendChild(badge);
    wrap.appendChild(hd);
    if (lane === "export-proof") {
      wrap.appendChild(el("div", "wm-guided-intro", exportProofChecklistIntro()));
    }
    if (ep && (ep.label || ep.value || ep.calc || ep.pub)) {
      var targets = el("div", "wm-export-targets");
      if (ep.label) targets.appendChild(el("div", null, "Target label: " + ep.label));
      if (ep.value || ep.calc) targets.appendChild(el("div", null, "Expected value: " + (ep.value || ep.calc)));
      if (ep.pub) targets.appendChild(el("div", null, "Published (doc): " + ep.pub));
      wrap.appendChild(targets);
    }
    var list = el("ul", "wm-guided-list");
    wrap.appendChild(list);
    var startBtn = null;
    if (startCollapsed) {
      startBtn = el("button", "wm-btn primary wm-start-checklist", "Start checklist");
      startBtn.type = "button";
      startBtn.setAttribute("aria-label", "Start blank linked cell review checklist");
      startBtn.onclick = function (ev) {
        ev.stopPropagation();
        wrap.classList.add("open");
        startBtn.style.display = "none";
      };
      wrap.appendChild(startBtn);
    }
    var verdictHost = el("div", "wm-export-verdict");
    wrap.appendChild(verdictHost);
    if (lane === "export-proof") {
      wrap.appendChild(el("div", "wm-guided-footer", exportProofChecklistFooter()));
    }
    det.appendChild(wrap);

    var progress = initialGuidedProgress(steps);
    var rescanBtn = null;
    var verdict = null;

    function syncLabel() {
      var sum = guidedProgressSummary(progress, steps);
      lbl.textContent = lane === "blank-dl" ? blankDlGuidedTitle(sum) : guidedLaneLabel(sum, lane);
      var badgeText = guidedCompletionBadge(sum, lane);
      if (badgeText) {
        badge.textContent = badgeText;
        badge.style.display = "";
      } else {
        badge.textContent = "";
        badge.style.display = "none";
      }
    }
    function paintVerdict() {
      verdictHost.replaceChildren();
      if (lane !== "export-proof") return;
      var sum = guidedProgressSummary(progress, steps);
      if (!sum.complete) {
        verdictHost.appendChild(el("div", "wm-export-verdict-hint", exportProofVerdictHint()));
        return;
      }
      var row = el("div", "wm-export-verdict-row");
      row.appendChild(el("span", "wm-export-verdict-lbl", "Verdict:"));
      ["EXPORT_PROVEN", "NOT_PROVEN_VISUAL"].forEach(function (v) {
        var b = el("button", "wm-btn wm-verdict" + (verdict === v ? " active" : ""), v === "EXPORT_PROVEN" ? "Proven" : "Not proven");
        b.title = v;
        b.onclick = function (ev) {
          ev.stopPropagation();
          verdict = v;
          saveExportVerdict(itemId, v);
          paintVerdict();
        };
        row.appendChild(b);
      });
      if (verdict) {
        var badge = el("span", "wm-export-badge " + (verdict === "EXPORT_PROVEN" ? "proven" : "unproven"), exportProofLabel(verdict));
        row.appendChild(badge);
      }
      verdictHost.appendChild(row);
    }
    function syncStepStyles() {
      list.querySelectorAll(".wm-guided-step").forEach(function (row) {
        var sid = row.getAttribute("data-step-id");
        row.classList.toggle("done", !!(sid && progress[sid]));
        var cb = row.querySelector("input");
        if (cb) cb.checked = !!(sid && progress[sid]);
      });
      syncLabel();
      paintVerdict();
      if (rescanBtn) {
        rescanBtn.style.display = guidedProgressSummary(progress, steps).complete ? "" : "none";
        rescanBtn.textContent = lane === "export-proof" ? "Re-run tieout checks" : "Re-scan this sheet";
      }
    }
    function paintSteps() {
      list.replaceChildren();
      steps.forEach(function (step) {
        var li = el("li", "wm-guided-step" + (progress[step.id] ? " done" : ""));
        li.setAttribute("data-step-id", step.id);
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = !!progress[step.id];
        cb.title = "Mark step done (manual — Wingman does not run this step)";
        cb.setAttribute("aria-label", (step.title || step.id) + " — mark complete");
        cb.onclick = function (ev) { ev.stopPropagation(); };
        li.appendChild(cb);
        var body = el("div");
        body.appendChild(el("div", "wm-guided-step-t", step.title || step.id));
        if (step.detail) body.appendChild(el("div", "wm-guided-step-d", step.detail));
        li.appendChild(body);
        li.onclick = function (ev) {
          ev.stopPropagation();
          progress = toggleGuidedStep(progress, step.id);
          saveGuidedProgress(itemId, lane, progress);
          syncStepStyles();
        };
        list.appendChild(li);
      });
      if (!rescanBtn) {
        rescanBtn = el("button", "wm-btn wm-rescan", lane === "export-proof" ? "Re-run tieout checks" : "Re-scan this sheet");
        rescanBtn.title = lane === "export-proof"
          ? "Re-run tieout after publish/export proof"
          : "Re-run Wingman scan after publish/cache refresh";
        rescanBtn.style.display = guidedProgressSummary(progress, steps).complete ? "" : "none";
        rescanBtn.onclick = function (ev) {
          ev.stopPropagation();
          rescanBtn.disabled = true;
          rescanBtn.textContent = "Running\u2026";
          if (lane === "export-proof") runTieoutChecks();
          else scan();
        };
        wrap.appendChild(rescanBtn);
      }
      syncLabel();
    }

    hd.onclick = function (ev) {
      ev.stopPropagation();
      wrap.classList.toggle("open");
    };
    loadGuidedProgress(itemId, lane, steps, function (stored) {
      progress = stored;
      paintSteps();
      syncStepStyles();
      if (startCollapsed && guidedProgressSummary(progress, steps).done > 0) {
        wrap.classList.add("open");
        if (startBtn) startBtn.style.display = "none";
      }
    });
    loadExportVerdict(itemId, function (v) {
      verdict = v;
      paintVerdict();
    });
  }

  function appendTextTrapAlert(parent) {
    var alert = el("div", "wm-alert wm-alert-text-trap", textTrapAlertCopy());
    parent.appendChild(alert);
    return alert;
  }

  function appendTieoutBadges(det, g) {
    var badges = formatTieoutBadges(g);
    if (!badges.length) return;
    var row = el("div", "wm-badge-row");
    badges.forEach(function (b) {
      row.appendChild(el("span", "wm-badge " + b.variant, b.label));
    });
    det.appendChild(row);
  }

  function appendScorecardAgeBadge(parent, generatedAt, opts) {
    opts = opts || {};
    var state = scorecardAgeState(generatedAt, opts.nowMs);
    if (!opts.forceShow && !state.stale) return null;
    var badge = el("span", "wm-scorecard-badge" + (state.stale ? " stale" : ""), state.label);
    badge.title = state.tooltip;
    parent.appendChild(badge);
    return badge;
  }

  function renderChecksSummaryCard(parent, checks) {
    if (!shouldShowChecksSummaryCard(checks)) return null;
    var card = el("div", "wm-checks-summary");
    var hd = el("div", "wm-checks-summary-hd");
    hd.appendChild(el("span", null, checksSuiteDisplayName(checks.suite) + " summary"));
    appendScorecardAgeBadge(hd, resolveScorecardGeneratedAt(checks), { forceShow: true });
    card.appendChild(hd);
    formatChecksSummaryLines(checks).forEach(function (line) {
      if (line.key === "suite") return;
      var row = el("div", "wm-checks-summary-row");
      row.appendChild(el("span", "wm-checks-summary-k", line.label));
      row.appendChild(el("span", "wm-checks-summary-v", line.value));
      card.appendChild(row);
    });
    parent.appendChild(card);
    return card;
  }

  function renderConnectedReadinessCard(parent, data) {
    if (!data) return null;
    var r = connectedReadiness(data);
    var card = el("div", "wm-connected-readiness " + r.state);
    var hd = el("div", "wm-connected-hd");
    hd.appendChild(el("span", null, r.title));
    if (r.gaps && r.gaps.length) hd.appendChild(el("span", "wm-connected-badge", r.gaps.length + " gap" + (r.gaps.length === 1 ? "" : "s")));
    card.appendChild(hd);
    (r.lines || []).forEach(function (line) {
      card.appendChild(el("div", "wm-connected-line", line));
    });
    parent.appendChild(card);
    return card;
  }

  function showConfirmDialog(panelEl, copy, onConfirm) {
    if (!panelEl || !copy) { if (onConfirm) onConfirm(); return; }
    var overlay = el("div", "wm-confirm-overlay");
    var box = el("div", "wm-confirm");
    box.appendChild(el("div", "wm-confirm-title", copy.title || "Confirm"));
    box.appendChild(el("div", "wm-confirm-body", copy.body || ""));
    var actions = el("div", "wm-confirm-actions");
    var cancel = el("button", "wm-btn ghost", "Cancel");
    cancel.type = "button";
    var confirm = el("button", "wm-btn" + (copy.destructive ? " destructive primary" : " primary"), "Confirm");
    confirm.type = "button";
    function close() { overlay.remove(); }
    cancel.onclick = function (ev) { ev.stopPropagation(); close(); };
    confirm.onclick = function (ev) {
      ev.stopPropagation();
      close();
      if (onConfirm) onConfirm();
    };
    overlay.onclick = function (ev) { if (ev.target === overlay) close(); };
    actions.appendChild(cancel);
    actions.appendChild(confirm);
    box.appendChild(actions);
    overlay.appendChild(box);
    panelEl.appendChild(overlay);
    confirm.focus();
  }

  function appendDiagnosis(det, g, jc) {
    var dx = g && g.diagnosis;
    if (!dx) return;
    var block = el("div", "wm-dx");
    if (dx.title) block.appendChild(el("div", "wm-dx-title", dx.title));
    if (dx.explain) block.appendChild(el("div", "wm-dx-explain", dx.explain));
    if (dx.suggested_action) block.appendChild(el("div", "wm-dx-action", dx.suggested_action));
    if (block.childNodes.length) det.appendChild(block);
    if (hasGuidedSteps(g)) appendGuidedChecklist(det, g, jc);
    appendGatedFormatApply(det, g, jc);
  }

  function appendGatedFormatApply(det, g, jc) {
    if (!hasGatedFormatApply(g)) return;
    var sec = el("div", "wm-gated-format");
    sec.appendChild(el("div", "wm-gated-hd", "Gated column format"));
    resolveGatedTargets(g).forEach(function (gt) {
      var col = gt.column;
      var colAddrs = filterAddrsByColumn(g.addrs, col);
      if (!colAddrs.length) return;
      var target = { valueFormat: gt.valueFormat };
      var row = el("div", "wm-gated-row");
      var btn = el("button", "wm-btn primary", "Apply column format" + (col ? " (" + col + ")" : ""));
      btn.type = "button";
      btn.setAttribute("aria-label", columnSafeApplyLabel(colAddrs.length, col));
      if (gt.afterFormat) {
        btn.title = "Target format: " + gt.afterFormat;
        row.appendChild(el("span", "wm-mono wm-gated-preview", "\u2192 " + gt.afterFormat));
      }
      btn.onclick = function (ev) {
        ev.stopPropagation();
        showConfirmDialog(
          panelUi && panelUi.panel,
          columnApplyConfirmCopy(colAddrs.length, col, "fix", gt.afterFormat),
          function () { runGatedColumnApply(det, g, jc, colAddrs, target, btn, col, gt.afterFormat); },
        );
      };
      row.appendChild(btn);
      sec.appendChild(row);
    });
    det.appendChild(sec);
  }

  function runGatedColumnApply(det, g, jc, addrs, target, btn, col, afterFormat) {
    btn.disabled = true;
    btn.textContent = "Checking\u2026";
    mapLimit(addrs, 5, function (addr) {
      return post("/fix", fixPostBody(jc, addr, g, { target: target }))
        .then(function (r) { return { addr: addr, r: r }; })
        .catch(function (e) { return { addr: addr, r: { status: "error", error: e.message } }; });
    }).then(function (results) {
      var sec = el("div", "wm-gated-results");
      var fixable = results.filter(function (x) { return x.r.status === "dry-run"; });
      if (fixable.length) {
        var applyBtn = el("button", "wm-btn primary", columnSafeApplyLabel(fixable.length, col));
        applyBtn.type = "button";
        applyBtn.onclick = function () {
          showConfirmDialog(
            panelUi && panelUi.panel,
            columnApplyConfirmCopy(fixable.length, col, "apply", afterFormat),
            function () {
              applyBtn.disabled = true;
              applyBtn.textContent = "Applying\u2026";
              mapLimit(fixable, 1, function (x) {
                return post("/apply", fixPostBody(jc, x.addr, g, { target: target }))
                  .then(function (res) { return { addr: x.addr, res: res }; })
                  .catch(function (e) { return { addr: x.addr, res: { status: "error", error: e.message } }; });
              }).then(function (applied) {
                var ok = applied.filter(function (a) { return a.res.status === "applied"; }).length;
                applyBtn.textContent = ok ? ("Applied " + ok) : "Apply finished";
                flash(ok ? ("Applied " + ok + " gated format" + (ok === 1 ? "" : "s")) : "Gated apply finished — check rows");
              });
            },
          );
        };
        sec.appendChild(applyBtn);
      }
      results.forEach(function (x) {
        var fr = el("div", "wm-fix");
        fr.appendChild(addrChip(x.addr, jc, groupValues(g)));
        if (x.r.status === "dry-run") {
          fr.appendChild(el("span", "wm-mono", x.r.beforeFormat || x.r.before || ""));
          fr.appendChild(el("span", "wm-arrow", "\u2192"));
          fr.appendChild(el("span", "wm-mono", x.r.afterFormat || x.r.after || afterFormat || ""));
        } else if (x.r.status === "refused" && isTextTrapRefusal(x.r)) {
          appendTextTrapAlert(fr);
        } else {
          fr.appendChild(el("span", "wm-note", x.r.reason || x.r.error || x.r.status || "skipped"));
        }
        sec.appendChild(fr);
      });
      btn.replaceWith(sec);
    });
  }

  function appendColumnApplyButtons(sec, fixable, g, jc) {
    var byCol = groupAddrsByColumn(fixable.map(function (x) { return x.addr; }));
    var colKeys = Object.keys(byCol).sort();
    if (colKeys.length === 1) {
      var col = colKeys[0];
      var all = el("button", "wm-btn primary", columnSafeApplyLabel(fixable.length, col));
      all.type = "button";
      all.style.marginBottom = "4px";
      all.onclick = function () {
        var pending = fixable.filter(function (x) { return x._apply; });
        if (!pending.length) { all.disabled = true; return; }
        showConfirmDialog(panelUi && panelUi.panel, bulkApplyConfirmCopy(pending.length), function () {
          all.disabled = true;
          mapLimit(pending, 1, function (x) { return x._apply(); });
        });
      };
      sec.appendChild(all);
      return;
    }
    var allBtn = el("button", "wm-btn primary", "Apply all " + fixable.length + " safe fix" + (fixable.length > 1 ? "es" : ""));
    allBtn.type = "button";
    allBtn.style.marginBottom = "4px";
    allBtn.onclick = function () {
      var pending = fixable.filter(function (x) { return x._apply; });
      if (!pending.length) { allBtn.disabled = true; return; }
      showConfirmDialog(panelUi && panelUi.panel, bulkApplyConfirmCopy(pending.length), function () {
        allBtn.disabled = true;
        mapLimit(pending, 1, function (x) { return x._apply(); });
      });
    };
    sec.appendChild(allBtn);
    colKeys.forEach(function (col) {
      var colItems = fixable.filter(function (x) { return parseColumnLetter(x.addr) === col; });
      if (!colItems.length) return;
      var colBtn = el("button", "wm-btn ghost wm-col-apply", columnSafeApplyLabel(colItems.length, col));
      colBtn.type = "button";
      colBtn.style.marginRight = "4px";
      colBtn.style.marginBottom = "4px";
      colBtn.onclick = function () {
        showConfirmDialog(panelUi && panelUi.panel, columnApplyConfirmCopy(colItems.length, col, "apply"), function () {
          colBtn.disabled = true;
          mapLimit(colItems.filter(function (x) { return x._apply; }), 1, function (x) { return x._apply(); });
        });
      };
      sec.appendChild(colBtn);
    });
  }

  // Jump-to-cell: ask the background worker to drive Workiva's "Go To Cell" dialog with
  // trusted input (it owns chrome.debugger; a content script can't make trusted events).
  function gotoCell(addr, srcEl) {
    if (!guardExtensionContext()) return;
    if (srcEl) { srcEl.classList.add("busy"); srcEl.classList.remove("err"); }
    try {
      chrome.runtime.sendMessage({ type: "WM_GOTO", addr: addr }, function (resp) {
        if (srcEl) srcEl.classList.remove("busy");
        if (chrome.runtime.lastError) {
          if (isExtensionContextError(chrome.runtime.lastError)) markExtensionInvalidated();
          if (srcEl) srcEl.classList.add("err");
          flash("Extension reloaded — refresh tab", true);
          return;
        }
        if (!resp || !resp.ok) {
          if (srcEl) srcEl.classList.add("err");
          flash(shortErr(resp && resp.error), true);
        } else {
          flash("Jumped to " + (resp.landed || addr));
        }
      });
    } catch (e) {
      if (srcEl) srcEl.classList.remove("busy");
      if (isExtensionContextError(e)) markExtensionInvalidated();
      if (srcEl) srcEl.classList.add("err");
      flash("Extension reloaded — refresh tab", true);
    }
  }
  function shortErr(e) {
    e = String(e || "");
    if (/name box|spreadsheet/i.test(e)) return "Open a spreadsheet first";
    if (/dialog|did not land|accept/i.test(e)) return "Jump failed — try again";
    if (/cancelled|debugg/i.test(e)) return "Jump blocked (debugger)";
    return e ? e.slice(0, 44) : "Couldn't jump";
  }
  var toastT = null;
  function reloadBrowserTab() {
    window.location.reload();
  }
  function makeReloadTabButton(label) {
    var btn = el("button", "wm-btn primary wm-reload-btn", label);
    btn.type = "button";
    btn.setAttribute("aria-label", label);
    btn.onclick = function (ev) {
      ev.stopPropagation();
      ev.preventDefault();
      reloadBrowserTab();
    };
    return btn;
  }
  function flash(msg, isErr) {
    var panel = sh && sh.querySelector(".wm-panel");
    if (!panel) return;
    var t = panel.querySelector(".wm-toast");
    if (!t) { t = el("div", "wm-toast"); panel.appendChild(t); }
    t.className = "wm-toast" + (isErr ? " err" : "");
    if (shouldShowReloadTabButton({ invalidated: extensionInvalidated, text: msg })) {
      t.classList.add("actionable");
      t.replaceChildren(makeReloadTabButton(reloadTabButtonLabel("toast")));
    } else {
      t.classList.remove("actionable");
      t.textContent = msg;
    }
    void t.offsetWidth; t.classList.add("show");
    if (toastT) clearTimeout(toastT);
    toastT = setTimeout(function () { t.classList.remove("show"); }, 1700);
  }
  // jc (jump context) = {spreadsheetId, sheetId, isCurrent, name}. Jump drives the grid via the
  // name box, which only exists for the OPEN sheet — so off-sheet chips show but don't navigate.
  function addrChip(addr, jc, values) {
    var info = addrValueLabel(addr, values);
    var line = el("span", "wm-addr-line");
    var s = el("span", "wm-addr wm-mono", info.addr);
    var jumpTip = info.value != null ? "Jump to " + info.addr + " · " + info.value : "Jump to " + info.addr;
    if (jc && jc.isCurrent === false) {
      line.classList.add("offsheet");
      s.classList.add("offsheet");
      line.title = "On “" + (jc.name || "another sheet") + "” — open that sheet to jump";
      line.onclick = function (ev) { ev.stopPropagation(); flash("Open “" + (jc.name || "that sheet") + "” to jump to " + info.addr); };
    } else {
      line.title = jumpTip;
      line.onclick = function (ev) { ev.stopPropagation(); gotoCell(info.addr, s); };
    }
    line.appendChild(s);
    if (info.value != null) line.appendChild(el("span", "wm-addr-val", info.value));
    return line;
  }

  // Theme tokens. Dark is the default; light overrides via prefers-color-scheme
  // (when no explicit choice) or an explicit [data-theme="light"] on the host.
  var THEME_DARK =
    "--bg:#2a2a2e;--fg:#E6E8F0;--muted:#9aa0b4;--sep:#3a3a48;" +
    "--border:rgba(255,255,255,.10);--border-strong:rgba(255,255,255,.2);--border-soft:rgba(255,255,255,.08);" +
    "--surface2:#35353c;--surface2-hover:#3f3f48;--row-hover:#32323a;" +
    "--accent:#2E62E8;--accent-hover:#3a6cf0;" +
    "--accent-text:#9fb4f5;--accent-text-hover:#c4d2fb;--accent-surface:#23232f;--accent-surface-hover:#2b2b3b;" +
    "--accent-border:rgba(159,180,245,.28);--accent-border-hover:rgba(159,180,245,.6);" +
    "--proven:#2d8a4e;--proven-border:rgba(45,138,78,.45);" +
    "--warn:#E0A106;--err:#ff8f8f;--err-border:rgba(255,143,143,.5);" +
    "--logo-outline:rgba(255,255,255,.1);--chip-border:rgba(255,255,255,.25);" +
    "--pill-shadow:0 1px 2px rgba(0,0,0,.25);--panel-shadow:0 1px 2px rgba(0,0,0,.3),0 12px 34px rgba(0,0,0,.46);" +
    "--toast-bg:#35353c;--toast-shadow:0 6px 18px rgba(0,0,0,.45)";
  var THEME_LIGHT =
    "--bg:#ffffff;--fg:#2a2a2e;--muted:#5b6072;--sep:#c4c8d4;" +
    "--border:rgba(0,0,0,.12);--border-strong:rgba(0,0,0,.24);--border-soft:rgba(0,0,0,.08);" +
    "--surface2:#f1f2f6;--surface2-hover:#e7e9f0;--row-hover:#f4f6fb;" +
    "--accent:#2E62E8;--accent-hover:#3a6cf0;" +
    "--accent-text:#2952c8;--accent-text-hover:#1d3fa6;--accent-surface:#eef2fe;--accent-surface-hover:#e1e9fd;" +
    "--accent-border:rgba(46,98,232,.3);--accent-border-hover:rgba(46,98,232,.6);" +
    "--proven:#2d8a4e;--proven-border:rgba(45,138,78,.4);" +
    "--warn:#b9810a;--err:#d23b3b;--err-border:rgba(210,59,59,.5);" +
    "--logo-outline:rgba(0,0,0,.08);--chip-border:rgba(0,0,0,.2);" +
    "--pill-shadow:0 1px 2px rgba(0,0,0,.12);--panel-shadow:0 1px 2px rgba(0,0,0,.1),0 12px 34px rgba(15,23,42,.18);" +
    "--toast-bg:#2a2a2e;--toast-shadow:0 6px 18px rgba(15,23,42,.25)";
  var CSS =
    ":host{" + THEME_DARK + "}" +
    ":host([data-theme=\"light\"]){" + THEME_LIGHT + "}" +
    ".wm-pill{display:inline-flex;align-items:center;gap:7px;cursor:pointer;font:500 12px/1 'DM Sans',-apple-system,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased;color:var(--fg);background:var(--bg);border:1px solid var(--border);border-radius:7px;padding:7px 10px;user-select:none;box-shadow:var(--pill-shadow);transition:border-color .12s ease,transform .08s ease}" +
    ".wm-pill:hover{border-color:var(--border-strong)}.wm-pill:active{transform:scale(.96)}" +
    ".wm-pill .wm-reload-btn{font-size:11px;padding:3px 8px;line-height:1.2}" +
    ".wm-logo{width:17px;height:17px;flex:none;display:block}" +
    ".wm-label{color:var(--muted)}.wm-sep{color:var(--sep)}.wm-value{font-variant-numeric:tabular-nums}.wm-drift .wm-value{color:var(--warn)}" +
    ".wm-panel{position:relative;width:372px;max-height:74vh;display:flex;flex-direction:column;overflow:hidden;font:13px/1.45 'DM Sans',-apple-system,'Segoe UI',Roboto,sans-serif;-webkit-font-smoothing:antialiased;color:var(--fg);background:var(--bg);border:1px solid var(--border);border-radius:10px;box-shadow:var(--panel-shadow)}" +
    ".wm-panel.wm-wide{width:480px}" +
    ".wm-panel.wm-user-sized{transition:none}" +
    ".wm-resize-grip{position:absolute;right:2px;bottom:2px;width:14px;height:14px;cursor:nwse-resize;z-index:4;opacity:.35;border-radius:0 0 8px 0;background:linear-gradient(135deg,transparent 0 45%,var(--muted) 45% 52%,transparent 52% 68%,var(--muted) 68% 75%,transparent 75% 100%)}.wm-resize-grip:hover{opacity:.75}" +
    ".wm-mono{font-family:'DM Mono',ui-monospace,Menlo,monospace;font-variant-numeric:tabular-nums}" +
    ".wm-head{display:flex;align-items:center;gap:8px;padding:10px 12px 8px;user-select:none}" +
    ".wm-brand{font-weight:500}.wm-sp{flex:1}" +
    ".wm-preset{font-size:10px;font-weight:500;color:var(--accent-text);background:var(--accent-surface);border:1px solid var(--accent-border);border-radius:999px;padding:2px 8px;white-space:nowrap}" +
    ".wm-tabs{display:flex;gap:2px;padding:0 12px;border-bottom:1px solid var(--border-soft)}" +
    ".wm-tab{flex:1;font:inherit;font-size:12px;font-weight:500;line-height:1.2;color:var(--muted);background:transparent;border:0;border-bottom:2px solid transparent;border-radius:0;padding:8px 6px 10px;cursor:pointer;transition:color .12s ease,border-color .12s ease}" +
    ".wm-tab:hover{color:var(--fg)}.wm-tab.active{color:var(--accent-text);border-bottom-color:var(--accent)}" +
    ".wm-tab-actions{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:8px 12px 10px;border-bottom:1px solid var(--border-soft)}" +
    ".wm-toolbar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:0 12px 10px;border-bottom:1px solid var(--border-soft)}" +
    ".wm-btn{font:inherit;font-size:12px;line-height:1.2;color:var(--fg);background:var(--surface2);border:1px solid var(--border-soft);border-radius:7px;padding:5px 10px;cursor:pointer;white-space:nowrap;transition:background-color .12s ease,border-color .12s ease,transform .08s ease}.wm-btn:hover{background:var(--surface2-hover)}.wm-btn:active{transform:scale(.96)}.wm-btn:focus-visible{outline:2px solid var(--accent-border);outline-offset:1px}" +
    ".wm-btn.primary{background:var(--accent);border-color:transparent;color:#fff;font-weight:500}.wm-btn.primary:hover{background:var(--accent-hover)}.wm-btn.ghost{background:transparent}" +
    ".wm-btn.toggle.active{background:var(--accent-surface);border-color:var(--accent-border);color:var(--accent-text);font-weight:500}" +
    ".wm-x{position:relative;display:flex;align-items:center;justify-content:center;width:26px;height:26px;padding:0;color:var(--muted);background:transparent;border:0;border-radius:6px;cursor:pointer;font-size:17px;line-height:1;transition:background-color .12s ease,color .12s ease,transform .08s ease}.wm-x:hover{background:var(--surface2);color:var(--fg)}.wm-x:active{transform:scale(.96)}.wm-x::after{content:'';position:absolute;inset:-7px}" +
    ".wm-icon{font-size:14px}" +
    ".wm-ctx{padding:8px 12px;color:var(--muted);font-size:12px;border-bottom:1px solid var(--border-soft);display:flex;flex-wrap:wrap;align-items:center;gap:6px;line-height:1.45}" +
    ".wm-ctx-text{flex:1;min-width:0}" +
    ".wm-formula-chip{font-size:10px;font-weight:500;color:var(--muted);background:var(--surface2);border:1px solid var(--border-soft);border-radius:999px;padding:2px 8px;cursor:help;white-space:nowrap}" +
    ".wm-alert{margin:0 12px 8px;padding:8px 10px;font-size:12px;line-height:1.45;border-radius:7px;border:1px solid var(--warn);color:var(--fg);background:rgba(224,161,6,.08)}" +
    ".wm-alert-thermo{border-color:var(--accent-border);background:var(--accent-surface);color:var(--accent-text)}" +
    ".wm-alert-text-trap{border-color:var(--err-border);background:rgba(255,143,143,.08);color:var(--err)}" +
    ".wm-operator-warnings{display:none;margin:0 12px 8px;padding:7px 9px;border:1px solid rgba(224,161,6,.38);border-radius:7px;background:rgba(224,161,6,.07);font-size:11px;line-height:1.4;color:var(--muted)}" +
    ".wm-operator-warnings.show{display:block}.wm-operator-warn{display:flex;gap:6px;padding:2px 0}.wm-operator-warn::before{content:'!';color:var(--warn);font-weight:600}" +
    ".wm-badge-row{display:flex;flex-wrap:wrap;gap:5px;margin:4px 0 8px}" +
    ".wm-badge{font-size:10px;font-weight:500;border-radius:999px;padding:2px 8px;border:1px solid var(--border);color:var(--muted)}" +
    ".wm-badge.cause{color:var(--accent-text);border-color:var(--accent-border);background:var(--accent-surface)}" +
    ".wm-badge.confidence{color:var(--fg);border-color:var(--border-strong)}" +
    ".wm-badge.variance{color:var(--warn);border-color:rgba(224,161,6,.45)}" +
    ".wm-body{overflow:auto}" +
    ".wm-state{padding:30px 16px;text-align:center;color:var(--muted)}.wm-state b{display:block;color:var(--fg);font-size:14px;font-weight:500;margin-bottom:5px;text-wrap:balance}" +
    ".wm-state-reload{margin-top:10px}.wm-state-hint{display:block;margin-top:8px;font-size:12px;color:var(--muted)}" +
    ".wm-grp{border-bottom:1px solid var(--border-soft)}" +
    ".wm-row{display:flex;flex-wrap:wrap;align-items:center;gap:9px;padding:10px 12px;cursor:pointer;transition:background-color .12s ease}.wm-row:hover{background:var(--row-hover)}" +
    ".wm-sev{font-size:11px;min-width:46px;flex:none;color:var(--muted)}.wm-sev.high{color:var(--warn)}" +
    ".wm-kind{flex:1;min-width:0;overflow-wrap:anywhere;font-weight:500;text-transform:capitalize}.wm-sig{flex-basis:100%;order:1;overflow-wrap:anywhere;color:var(--muted);font-size:12px}" +
    ".wm-count{margin-left:auto;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;border:1px solid var(--border);border-radius:999px;padding:1px 8px}" +
    ".wm-lane{font-size:11px;border-radius:999px;padding:1px 8px;color:var(--muted);border:1px solid var(--border)}.wm-lane.fix{color:var(--accent-text);border-color:var(--accent-border)}" +
    ".wm-det{display:none;padding:0 12px 12px 12px}.wm-grp.open .wm-det{display:block}" +
    ".wm-cells{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin:4px 0 10px}" +
    ".wm-addr{display:inline-flex;align-items:center;cursor:pointer;color:var(--accent-text);background:var(--accent-surface);border:1px solid var(--accent-border);border-radius:5px;padding:2px 7px;transition:background-color .12s ease,border-color .12s ease,color .12s ease,transform .08s ease}.wm-addr:hover{background:var(--accent-surface-hover);border-color:var(--accent-border-hover);color:var(--accent-text-hover)}.wm-addr:active{transform:scale(.96)}" +
    ".wm-jump{display:inline-flex;align-items:center;justify-content:center;font:inherit;line-height:1;cursor:pointer;color:var(--accent-text);background:var(--accent-surface);border:1px solid var(--accent-border);border-radius:6px;padding:3px 8px;margin-left:6px;flex:none;transition:background-color .12s ease,border-color .12s ease,color .12s ease,transform .08s ease}.wm-jump:hover{background:var(--accent-surface-hover);border-color:var(--accent-border-hover);color:var(--accent-text-hover)}.wm-jump:active{transform:scale(.96)}" +
    ".wm-addr.busy,.wm-jump.busy{opacity:.5;cursor:progress}.wm-addr.err,.wm-jump.err{color:var(--err);border-color:var(--err-border)}" +
    ".wm-toast{position:absolute;left:50%;bottom:10px;transform:translateX(-50%) translateY(4px);background:var(--toast-bg);color:#fff;border:1px solid var(--border);border-radius:7px;padding:6px 11px;font-size:12px;box-shadow:var(--toast-shadow);pointer-events:none;opacity:0;transition:opacity .15s ease,transform .15s ease}.wm-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}.wm-toast.err{color:var(--err)}.wm-toast.actionable{pointer-events:auto}" +
    ".wm-fix{display:flex;flex-wrap:wrap;align-items:center;gap:9px;padding:7px 0;border-top:1px solid var(--border-soft)}.wm-fix>.wm-mono{min-width:0;max-width:100%;overflow-wrap:anywhere}" +
    ".wm-chip{width:13px;height:13px;border-radius:3px;border:1px solid var(--chip-border);display:inline-block;vertical-align:-2px}" +
    ".wm-arrow{color:var(--sep)}.wm-ratio{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}.wm-note{color:var(--muted);font-size:12px;font-style:italic}.wm-done{color:var(--accent);font-weight:500}.wm-err{color:var(--err)}" +
    // workbook sheet-list accordion (own classes so the inner .wm-grp.open selector never bleeds into it)
    ".wm-sheet{border-bottom:1px solid var(--border-soft)}" +
    ".wm-sheet-row{display:flex;align-items:center;gap:9px;padding:10px 12px;cursor:pointer;transition:background-color .12s ease}.wm-sheet-row:hover{background:var(--row-hover)}" +
    ".wm-sheet-det{display:none;padding:2px 0 8px}.wm-sheet.open .wm-sheet-det{display:block}" +
    ".wm-sname{font-weight:500}" +
    ".wm-sdot{width:8px;height:8px;border-radius:999px;flex:none;background:var(--muted)}.wm-sdot.clean{opacity:.35}.wm-sdot.some{background:var(--accent)}.wm-sdot.high{background:var(--warn)}.wm-sdot.err{background:var(--err)}" +
    ".wm-here{font-size:10px;color:var(--accent-text);border:1px solid var(--accent-border);border-radius:999px;padding:0 6px}" +
    ".wm-meta{margin-left:auto;color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}" +
    ".wm-addr.offsheet{cursor:default;color:var(--muted);background:transparent;border-color:var(--border)}.wm-addr.offsheet:hover{background:transparent;border-color:var(--border);color:var(--muted)}" +
    ".wm-addr-line{display:inline-flex;align-items:center;gap:5px;cursor:pointer;color:var(--accent-text);background:var(--accent-surface);border:1px solid var(--accent-border);border-radius:5px;padding:2px 7px;transition:background-color .12s ease,border-color .12s ease,color .12s ease,transform .08s ease}.wm-addr-line:hover{background:var(--accent-surface-hover);border-color:var(--accent-border-hover);color:var(--accent-text-hover)}.wm-addr-line:active{transform:scale(.96)}" +
    ".wm-addr-line .wm-addr{background:transparent;border:0;padding:0;color:inherit}.wm-addr-line .wm-addr:hover{background:transparent;border-color:transparent;color:inherit}" +
    ".wm-addr-line.offsheet,.wm-addr-line .wm-addr.offsheet{cursor:default;color:var(--muted);background:transparent;border-color:var(--border)}.wm-addr-line.offsheet:hover{background:transparent;border-color:var(--border);color:var(--muted)}" +
    ".wm-addr-val{color:var(--muted);font-size:11px;max-width:132px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-style:normal}" +
    ".wm-triage{position:sticky;top:0;z-index:1;padding:8px 12px;border-bottom:1px solid var(--border-soft);background:var(--bg)}" +
    ".wm-fixall{width:100%;margin-bottom:8px}.wm-fixall.done{opacity:.55;cursor:default}" +
    ".wm-segs{display:flex;flex-wrap:wrap;gap:6px}" +
    ".wm-seg{font:inherit;font-size:12px;color:var(--muted);background:transparent;border:1px solid var(--border-soft);border-radius:999px;padding:4px 10px;cursor:pointer;transition:background-color .12s ease,border-color .12s ease,color .12s ease,transform .08s ease}.wm-seg:hover{background:var(--surface2-hover)}.wm-seg:active{transform:scale(.96)}.wm-seg.active{color:var(--fg);background:var(--surface2);border-color:var(--border-strong);font-weight:500}" +
    ".wm-wbbar{display:flex;justify-content:flex-end;align-items:center;gap:8px;padding:7px 12px;border-bottom:1px solid var(--border-soft)}" +
    ".wm-dx{margin:6px 0 10px;padding:8px 10px;border:1px solid var(--border-soft);border-radius:7px;background:var(--surface2)}" +
    ".wm-dx-title{font-weight:500;margin-bottom:4px}.wm-dx-explain{color:var(--muted);font-size:12px;margin-bottom:4px}" +
    ".wm-dx-action{color:var(--accent-text);font-size:12px;margin-bottom:6px}" +
    ".wm-guided{margin-top:6px;border-top:1px solid var(--border-soft);padding-top:6px}" +
    ".wm-guided-hd{display:flex;align-items:center;gap:6px;width:100%;font:inherit;font-size:12px;font-weight:500;color:var(--fg);background:transparent;border:0;padding:4px 0;cursor:pointer;text-align:left}" +
    ".wm-guided-hd:hover{color:var(--accent-text)}" +
    ".wm-guided-chev{font-size:10px;color:var(--muted);transition:transform .12s ease}" +
    ".wm-guided.open .wm-guided-chev{transform:rotate(90deg)}" +
    ".wm-guided-list{display:none;margin:4px 0 0;padding:0;list-style:none}" +
    ".wm-guided.open .wm-guided-list{display:block}" +
    ".wm-guided-intro{font-size:11px;line-height:1.45;color:var(--muted);margin:6px 0 4px;padding:6px 8px;border-radius:6px;background:var(--surface2);border:1px solid var(--border-soft)}" +
    ".wm-export-banner{font-size:12px;line-height:1.45;color:var(--muted);margin-bottom:8px;padding:8px 10px;border-radius:7px;border:1px dashed var(--warn);background:rgba(224,161,6,.06)}" +
    ".wm-export-verdict-hint{font-size:11px;color:var(--muted);margin-top:6px;line-height:1.4}" +
    ".wm-guided-step{display:flex;gap:8px;padding:6px 0;border-bottom:1px solid var(--border-soft);font-size:12px;color:var(--muted);cursor:pointer;user-select:none}" +
    ".wm-guided-step:last-child{border-bottom:0}" +
    ".wm-guided-step input{margin:2px 0 0;flex:none;accent-color:var(--accent);cursor:pointer}" +
    ".wm-guided-step.done{color:var(--fg);opacity:.85}" +
    ".wm-guided-step-t{font-weight:500;color:var(--fg);margin-bottom:2px}" +
    ".wm-guided-step-d{font-size:11px;line-height:1.4}" +
    ".wm-rescan{margin-top:8px;width:100%}" +
    ".wm-export-targets{margin:4px 0 8px;padding:6px 8px;font-size:11px;color:var(--muted);border:1px dashed var(--border-soft);border-radius:6px;line-height:1.45}" +
    ".wm-export-verdict{margin-top:8px}.wm-export-verdict-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px}" +
    ".wm-export-verdict-lbl{font-size:12px;color:var(--muted)}" +
    ".wm-verdict{font-size:11px;padding:4px 8px}.wm-verdict.active{background:var(--accent-surface);border-color:var(--accent-border);color:var(--accent-text);font-weight:500}" +
    ".wm-export-badge{font-size:10px;border-radius:999px;padding:2px 8px;border:1px solid var(--border)}" +
    ".wm-export-badge.proven{color:var(--proven);border-color:var(--proven-border)}" +
    ".wm-export-badge.unproven{color:var(--warn);border-color:rgba(224,161,6,.45)}" +
    ".wm-guided-footer{margin-top:8px;padding-top:8px;border-top:1px dashed var(--border-soft);font-size:11px;color:var(--muted);line-height:1.45}" +
    ".wm-guided-badge{margin-left:auto;font-size:10px;font-weight:500;color:var(--accent-text);white-space:nowrap}" +
    ".wm-start-checklist{margin:6px 0 4px;width:100%}" +
    ".wm-gated-format{margin-top:8px;padding-top:8px;border-top:1px solid var(--border-soft)}" +
    ".wm-gated-hd{font-size:11px;font-weight:600;color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}" +
    ".wm-gated-row{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:4px}" +
    ".wm-gated-preview{font-size:11px;color:var(--muted)}" +
    ".wm-col-apply{font-size:11px}" +
    ".wm-lane.export{color:var(--warn);border-color:rgba(224,161,6,.45)}" +
    ".wm-checks-summary{margin:0 12px 8px;padding:8px 10px;border:1px solid var(--border-soft);border-radius:7px;background:var(--surface2);font-size:12px;line-height:1.45}" +
    ".wm-checks-summary-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;font-weight:500;color:var(--fg)}" +
    ".wm-checks-summary-row{display:flex;gap:8px;padding:2px 0;color:var(--muted)}" +
    ".wm-checks-summary-k{min-width:72px;color:var(--muted)}.wm-checks-summary-v{color:var(--fg);flex:1}" +
    ".wm-connected-readiness{margin:0 12px 8px;padding:8px 10px;border:1px solid var(--border-soft);border-radius:7px;background:var(--surface2);font-size:12px;line-height:1.45}" +
    ".wm-connected-readiness.needs-proof{border-color:rgba(224,161,6,.35);background:rgba(224,161,6,.06)}" +
    ".wm-connected-hd{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;font-weight:500;color:var(--fg)}" +
    ".wm-connected-badge{font-size:10px;font-weight:500;border-radius:999px;padding:2px 8px;border:1px solid rgba(224,161,6,.45);color:var(--warn);white-space:nowrap}" +
    ".wm-connected-line{color:var(--muted);padding:2px 0}" +
    ".wm-scorecard-badge{font-size:10px;font-weight:500;border-radius:999px;padding:2px 8px;border:1px solid var(--border);color:var(--muted);cursor:help;white-space:nowrap}" +
    ".wm-scorecard-badge.stale{color:var(--warn);border-color:rgba(224,161,6,.45)}" +
    ".wm-confirm-overlay{position:absolute;inset:0;z-index:20;display:flex;align-items:center;justify-content:center;padding:12px;background:rgba(0,0,0,.45);border-radius:10px}" +
    ".wm-confirm{width:100%;max-width:320px;padding:12px;border:1px solid var(--border-strong);border-radius:8px;background:var(--bg);box-shadow:var(--panel-shadow)}" +
    ".wm-confirm-title{font-weight:500;margin-bottom:6px;color:var(--fg)}" +
    ".wm-confirm-body{font-size:12px;line-height:1.45;color:var(--muted);margin-bottom:12px}" +
    ".wm-confirm-actions{display:flex;justify-content:flex-end;gap:8px}" +
    ".wm-confirm-actions .wm-btn.destructive{background:var(--err);border-color:transparent;color:#fff;font-weight:500}.wm-confirm-actions .wm-btn.destructive:hover{filter:brightness(1.08)}" +
    ".wm-cmd-overlay{position:absolute;inset:0;z-index:30;display:flex;align-items:flex-start;justify-content:center;padding:10px 8px 0;background:rgba(0,0,0,.42);border-radius:10px}" +
    ".wm-cmd{width:100%;max-height:min(58vh,420px);display:flex;flex-direction:column;overflow:hidden;border:1px solid var(--border-strong);border-radius:8px;background:var(--bg);box-shadow:var(--panel-shadow)}" +
    ".wm-cmd-hd{padding:8px 10px 6px;font-size:11px;font-weight:500;color:var(--muted);border-bottom:1px solid var(--border-soft)}" +
    ".wm-cmd-input-wrap{padding:8px 10px;border-bottom:1px solid var(--border-soft)}" +
    ".wm-cmd-input{width:100%;font:inherit;font-size:13px;color:var(--fg);background:transparent;border:0;outline:none;padding:0}" +
    ".wm-cmd-input::placeholder{color:var(--muted)}" +
    ".wm-cmd-list{overflow:auto;padding:6px 0 8px;flex:1}" +
    ".wm-cmd-group{padding:4px 10px 2px;font-size:10px;font-weight:500;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}" +
    ".wm-cmd-item{display:flex;align-items:center;gap:8px;width:100%;font:inherit;font-size:12px;text-align:left;color:var(--fg);background:transparent;border:0;border-radius:6px;padding:7px 10px;margin:0 6px;cursor:pointer;transition:background-color .1s ease}" +
    ".wm-cmd-item:hover,.wm-cmd-item.active{background:var(--surface2)}" +
    ".wm-cmd-item-hint{margin-left:auto;font-size:11px;color:var(--muted)}" +
    ".wm-cmd-empty{padding:16px 12px;text-align:center;color:var(--muted);font-size:12px}" +
    ".wm-cmd-foot{padding:6px 10px 8px;border-top:1px solid var(--border-soft);font-size:10px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}" +
    ".wm-wide-toggle.active{color:var(--accent-text);background:var(--accent-surface)}" +
    ".wm-reset-size.active{color:var(--accent-text);background:var(--accent-surface)}.wm-reset-size:disabled{opacity:.35;cursor:default}";

  var host = null, sh = null, view = null, open = false, guard = new DriftGuard(), last = null, pillState = "idle", pillValue = "ready";
  var visionOn = false;
  var pos = null;  // {left,top} once dragged; null => default bottom-right anchor
  var theme = "dark";  // 'light' | 'dark'
  var panelUi = null;
  var activeTab = "inspect";
  var inspectContext = null, inspectState = {}, inspectRequest = 0, inspectTimer = null;
  var tabCache = { scan: null, checks: null, workbook: null };
  var wideMode = false;
  var customPanelSize = null;  // { width, height } when user resized; null => preset narrow/wide
  var paletteOpen = false;
  var paletteUi = null;
  if (guardExtensionContext() && chrome.storage) storageGet(["wmPos", "wmTheme", "wmVision", "wmTab", "wmWide", "wmPanelSize"], function (r) {
    if (r && r.wmPos) { pos = r.wmPos; applyPos(); }
    if (r && r.wmTheme === "light") { theme = "light"; applyTheme(); }
    if (r && r.wmVision) visionOn = true;
    if (r && r.wmTab && TAB_IDS.indexOf(r.wmTab) >= 0) activeTab = r.wmTab;
    if (r && r.wmWide) wideMode = true;
    if (r && r.wmPanelSize && r.wmPanelSize.width) {
      customPanelSize = {
        width: clampPanelWidth(r.wmPanelSize.width, window.innerWidth),
        height: r.wmPanelSize.height
          ? clampPanelHeight(r.wmPanelSize.height, window.innerHeight) : null,
      };
    }
    applyPanelLayout();
  });
  function applyTheme() {
    if (!host) return;
    if (theme === "light") host.setAttribute("data-theme", "light");
    else host.removeAttribute("data-theme");
  }
  function cycleTheme() {
    theme = theme === "light" ? "dark" : "light";
    applyTheme();
    if (guardExtensionContext() && chrome.storage) storageSet({ wmTheme: theme });
    return theme;
  }
  function themeGlyph() { return theme === "light" ? "☀" : "☾"; }

  function applyPanelLayout() {
    if (!panelUi || !panelUi.panel) return;
    var panel = panelUi.panel;
    var custom = panelSizeIsCustom(customPanelSize, wideMode);
    panel.classList.toggle("wm-wide", !!wideMode && !custom);
    panel.classList.toggle("wm-user-sized", !!custom);
    if (custom && customPanelSize) {
      panel.style.width = customPanelSize.width + "px";
      if (customPanelSize.height) {
        panel.style.height = customPanelSize.height + "px";
        panel.style.maxHeight = customPanelSize.height + "px";
      } else {
        panel.style.height = "";
        panel.style.maxHeight = PANEL_DEFAULT_MAX_HEIGHT_VH + "vh";
      }
    } else {
      panel.style.width = "";
      panel.style.height = "";
      panel.style.maxHeight = "";
    }
    if (panelUi.wideBtn) {
      panelUi.wideBtn.classList.toggle("active", !!wideMode && !custom);
      panelUi.wideBtn.title = wideMode
        ? "Narrow panel (" + PANEL_WIDTH_NARROW + "px)"
        : "Widen panel (" + PANEL_WIDTH_WIDE + "px)";
    }
    if (panelUi.resetSizeBtn) {
      panelUi.resetSizeBtn.classList.toggle("active", !!custom || !!wideMode);
      panelUi.resetSizeBtn.disabled = !custom && !wideMode;
    }
    applyPos();
  }
  function persistPanelSize() {
    if (!guardExtensionContext() || !chrome.storage) return;
    if (customPanelSize) storageSet({ wmPanelSize: customPanelSize, wmWide: wideMode });
    else storageSet({ wmPanelSize: null, wmWide: wideMode });
  }
  function resetPanelSize() {
    customPanelSize = null;
    wideMode = false;
    applyPanelLayout();
    persistPanelSize();
    flash("Panel size reset");
  }
  function toggleWideMode() {
    customPanelSize = null;
    wideMode = !wideMode;
    applyPanelLayout();
    persistPanelSize();
  }
  function attachPanelResize(panel, grip) {
    grip.addEventListener("mousedown", function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      var sx = e.clientX, sy = e.clientY;
      var rect = panel.getBoundingClientRect();
      var startW = rect.width, startH = rect.height;
      function move(ev) {
        customPanelSize = {
          width: clampPanelWidth(startW + (ev.clientX - sx), window.innerWidth),
          height: clampPanelHeight(startH + (ev.clientY - sy), window.innerHeight),
        };
        applyPanelLayout();
      }
      function up() {
        document.removeEventListener("mousemove", move, true);
        document.removeEventListener("mouseup", up, true);
        if (customPanelSize) persistPanelSize();
      }
      document.addEventListener("mousemove", move, true);
      document.addEventListener("mouseup", up, true);
    });
  }

  function updateCtxLine(ui, text, data) {
    if (!ui || !ui.ctx) return;
    ui.ctx.replaceChildren();
    var line = el("span", "wm-ctx-text", text);
    ui.ctx.appendChild(line);
    if (shouldShowFormulaPartialChip(data, activeTab)) {
      var chip = el("span", "wm-formula-chip", formulaPartialChipLabel());
      chip.title = formulaPartialChipTooltip(data && data.formula_fetch);
      ui.ctx.appendChild(chip);
    }
  }

  function commandPaletteContext() {
    var wb = tabCache.workbook;
    return {
      activeTab: activeTab,
      hasWorkbookScan: !!(wb && wb.rollup && wb.rollup.sheets && wb.rollup.sheets.length),
    };
  }

  function runCommandById(id) {
    closeCommandPalette();
    if (id === "goto") {
      var addr = window.prompt("Jump to cell (A1 address):", "");
      if (!addr) return;
      var norm = normalizeAddr(addr);
      if (!norm) { flash("Invalid cell address", true); return; }
      gotoCell(norm);
      return;
    }
    if (id === "rescan") {
      if (activeTab === "checks") runTieoutChecks();
      else if (activeTab === "workbook") scanWorkbook();
      else scan();
      return;
    }
    if (id === "tieout") { runTieoutChecks(); return; }
    if (id === "hardening_gate") { runHardeningGateChecks(); return; }
    if (id === "copy_report") {
      var wb = tabCache.workbook;
      if (!wb || !wb.rollup) { flash("Run workbook scan first", true); return; }
      var tmp = el("button", "wm-btn ghost", "Copy report");
      var copyData = wb.rollup;
      if (isRollupStale(copyData)) copyData = Object.assign({}, copyData, { stale: true });
      copyText(buildReport(copyData), tmp);
      return;
    }
    if (id === "tab_scan") { setActiveTab("scan"); return; }
    if (id === "tab_checks") { setActiveTab("checks"); return; }
    if (id === "tab_workbook") { setActiveTab("workbook"); return; }
  }

  function closeCommandPalette() {
    paletteOpen = false;
    if (paletteUi && paletteUi.overlay) paletteUi.overlay.remove();
    paletteUi = null;
  }

  function paintCommandPalette(query) {
    if (!paletteUi) return;
    var cmds = filterCommands(buildCommandList(commandPaletteContext()), query);
    var grouped = groupCommands(cmds);
    var list = paletteUi.list;
    list.replaceChildren();
    paletteUi.activeIdx = Math.min(paletteUi.activeIdx, Math.max(0, cmds.length - 1));
    if (!cmds.length) {
      list.appendChild(el("div", "wm-cmd-empty", "No matching commands"));
      return;
    }
    var idx = 0;
    grouped.forEach(function (grp) {
      list.appendChild(el("div", "wm-cmd-group", grp.name));
      grp.items.forEach(function (cmd) {
        var btn = el("button", "wm-cmd-item wm-cmd" + (idx === paletteUi.activeIdx ? " active" : ""), cmd.label);
        btn.type = "button";
        btn.setAttribute("data-cmd-id", cmd.id);
        if (cmd.hint) btn.appendChild(el("span", "wm-cmd-item-hint", cmd.hint));
        btn.onclick = function (ev) { ev.stopPropagation(); runCommandById(cmd.id); };
        list.appendChild(btn);
        idx++;
      });
    });
    paletteUi.flat = cmds;
  }

  function openCommandPalette() {
    if (!open || !panelUi || !panelUi.panel) return;
    closeCommandPalette();
    paletteOpen = true;
    var overlay = el("div", "wm-cmd-overlay");
    overlay.onclick = function (ev) { if (ev.target === overlay) closeCommandPalette(); };
    var box = el("div", "wm-cmd");
    box.onclick = function (ev) { ev.stopPropagation(); };
    box.appendChild(el("div", "wm-cmd-hd", "Command palette"));
    var inputWrap = el("div", "wm-cmd-input-wrap");
    var input = el("input", "wm-cmd-input");
    input.type = "text";
    input.placeholder = "Type a command…";
    inputWrap.appendChild(input);
    box.appendChild(inputWrap);
    var list = el("div", "wm-cmd-list");
    box.appendChild(list);
    var foot = el("div", "wm-cmd-foot");
    foot.appendChild(el("span", null, "↑↓ navigate · Enter run · Esc close"));
    foot.appendChild(el("span", null, "⌘K"));
    box.appendChild(foot);
    overlay.appendChild(box);
    panelUi.panel.appendChild(overlay);
    paletteUi = { overlay: overlay, list: list, input: input, activeIdx: 0, flat: [] };
    paintCommandPalette("");
    input.focus();
    input.oninput = function () { paletteUi.activeIdx = 0; paintCommandPalette(input.value); };
    input.onkeydown = function (ev) {
      var flat = paletteUi.flat || [];
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        if (flat.length) paletteUi.activeIdx = (paletteUi.activeIdx + 1) % flat.length;
        paintCommandPalette(input.value);
      } else if (ev.key === "ArrowUp") {
        ev.preventDefault();
        if (flat.length) paletteUi.activeIdx = (paletteUi.activeIdx - 1 + flat.length) % flat.length;
        paintCommandPalette(input.value);
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        if (flat[paletteUi.activeIdx]) runCommandById(flat[paletteUi.activeIdx].id);
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        closeCommandPalette();
      }
    };
  }

  function toggleCommandPalette() {
    if (paletteOpen) closeCommandPalette();
    else openCommandPalette();
  }

  function ensureHost() {
    if (host) return;
    host = document.createElement("div");
    host.id = "__wk_wingman__";
    host.style.cssText = "position:fixed;bottom:14px;right:14px;z-index:2147483647";
    sh = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
    var st = document.createElement("style"); st.textContent = CSS; sh.appendChild(st);
    view = document.createElement("div"); sh.appendChild(view);
    (document.body || document.documentElement).appendChild(host);
    applyTheme();
    applyPos();
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  // Once dragged we anchor by top/left; until then keep the default bottom-right CSS anchor.
  function applyPos() {
    if (!host || !pos) return;
    var w = host.offsetWidth || 0, h = host.offsetHeight || 0;
    var left = clamp(pos.left, 6, Math.max(6, window.innerWidth - w - 6));
    var top = clamp(pos.top, 6, Math.max(6, window.innerHeight - h - 6));
    host.style.left = left + "px"; host.style.top = top + "px";
    host.style.right = "auto"; host.style.bottom = "auto";
  }
  // Drag the whole widget by `handle`. A press that doesn't move past the threshold is a
  // click and fires onClick (open when minimized / minimize from the header); a press that
  // moves repositions the widget. Presses that start on a button are left to that button.
  function makeDraggable(handle, onClick) {
    handle.addEventListener("mousedown", function (e) {
      if (e.button !== 0 || isDragPassthrough(e.target)) return;
      var sx = e.clientX, sy = e.clientY;
      var r = host.getBoundingClientRect(), ox = r.left, oy = r.top, moved = false;
      function move(ev) {
        var dx = ev.clientX - sx, dy = ev.clientY - sy;
        if (!moved && Math.abs(dx) + Math.abs(dy) > 4) moved = true;
        if (!moved) return;
        pos = { left: ox + dx, top: oy + dy };
        applyPos();
      }
      function up() {
        document.removeEventListener("mousemove", move, true);
        document.removeEventListener("mouseup", up, true);
        if (!moved) { if (onClick) onClick(); return; }
        if (guardExtensionContext() && chrome.storage) storageSet({ wmPos: pos });
      }
      document.addEventListener("mousemove", move, true);
      document.addEventListener("mouseup", up, true);
      e.preventDefault();  // no text selection / native image drag while dragging
    });
  }

  function renderPill() {
    ensureHost();
    var p = el("div", "wm-pill " + (pillState === "drift" ? "wm-drift" : ""));
    p.innerHTML = '<img class="wm-logo" alt="" src="' + LOGO + '"><span class="wm-label">Wingman</span>' +
      '<span class="wm-sep">·</span><span class="wm-value"></span>';
    var valueEl = p.querySelector(".wm-value");
    if (shouldShowReloadTabButton({ invalidated: extensionInvalidated, text: pillValue })) {
      valueEl.replaceChildren(makeReloadTabButton(reloadTabButtonLabel("pill")));
    } else {
      valueEl.textContent = pillValue;
    }
    makeDraggable(p, openPanel);   // drag to move, click to open
    view.replaceChildren(p);
    applyPos();
  }

  function openPanel() {
    open = true;
    var ui = panelShell();
    clearInterval(inspectTimer);
    inspectTimer = setInterval(tick, 500); // SPA URL-only changes do not fire DOM or popstate events.
    if (activeTab !== "inspect") ensureServiceConfig().finally(function () { fetchOperatorStatus(ui); });
  }
  function closePanel() {
    open = false;
    clearInterval(inspectTimer);
    inspectRequest++;
    inspectState = {};
    closeCommandPalette(); panelUi = null; renderPill();
  }

  function currentInspection() {
    var indicator = document.querySelector(SEL);
    return WingmanInspector.selection(location.href, indicator && indicator.textContent, !!demoEmbed);
  }
  function syncInspection() {
    var context = currentInspection();
    if (inspectContext && WingmanInspector.key(context) === WingmanInspector.key(inspectContext)) return;
    var before = inspectContext && inspectContext.target, after = context.target;
    var sheetChanged = !before || !after || before.workspaceId !== after.workspaceId ||
      before.spreadsheetId !== after.spreadsheetId || before.sheetId !== after.sheetId;
    inspectRequest++;
    inspectContext = context;
    inspectState = {};
    if (sheetChanged) {
      tabCache = { scan: null, checks: null, workbook: null };
      if (open && activeTab !== "inspect" && panelUi) restoreTabView();
    }
    if (open && activeTab === "inspect" && panelUi) paintInspection();
  }
  function paintInspection() {
    WingmanInspector.render(panelUi.body, inspectContext || currentInspection(), inspectState, inspectSelectedCell);
  }
  function inspectSelectedCell() {
    if (!guardExtensionContext()) return;
    syncInspection();
    var target = inspectContext.target;
    if (!target) return;
    var request = ++inspectRequest;
    inspectState = { loading: true };
    paintInspection();
    var query = ["spreadsheetId", "sheetId", "addr"].map(function (k) {
      return k + "=" + encodeURIComponent(target[k]);
    }).join("&");
    function stillCurrent() {
      syncInspection();
      return !extensionInvalidated && open && activeTab === "inspect" && request === inspectRequest;
    }
    svc("/api/inspect?" + query, { cache: "no-store" }).then(function (data) {
      if (!stillCurrent()) return;
      if (!WingmanInspector.matches(target, data.target) || data.readOnly !== true) {
        throw new Error("The response did not match the selected cell. No evidence displayed.");
      }
      inspectState = { data: data };
      paintInspection();
    }).catch(function (error) {
      if (!stillCurrent()) return;
      inspectState = { error: error.offline ? "Wingman service is unavailable. No cell was inspected." : error.message };
      paintInspection();
    });
  }

  function setActiveTab(tabId) {
    if (TAB_IDS.indexOf(tabId) < 0) tabId = "inspect";
    if (activeTab !== tabId && inspectState.loading) {
      inspectRequest++;
      inspectState = {};
    }
    activeTab = tabId;
    syncTabUi();
    if (guardExtensionContext() && chrome.storage) storageSet({ wmTab: activeTab });
    restoreTabView();
    if (activeTab !== "inspect") ensureServiceConfig();
  }

  function syncTabUi() {
    if (!panelUi || !panelUi.tabsBar) return;
    panelUi.panel.classList.toggle("wi-mode", activeTab === "inspect");
    panelUi.body.setAttribute("aria-labelledby", "wm-tab-" + activeTab);
    panelUi.tabsBar.querySelectorAll(".wm-tab").forEach(function (btn) {
      var tid = btn.getAttribute("data-tab");
      btn.classList.toggle("active", tid === activeTab);
      btn.setAttribute("aria-selected", tid === activeTab ? "true" : "false");
      btn.tabIndex = tid === activeTab ? 0 : -1;
    });
    renderTabActions();
  }

  function renderTabActions() {
    if (!panelUi || !panelUi.tabActions) return;
    var ta = panelUi.tabActions;
    ta.replaceChildren();
    if (activeTab === "scan") {
      var visBtn = el("button", "wm-btn toggle", "Vision");
      visBtn.type = "button";
      visBtn.title = "Include pixel-level cell crops on Scan (slower)";
      syncVisionToggle(visBtn);
      visBtn.onclick = function () {
        visionOn = !visionOn;
        syncVisionToggle(visBtn);
        if (guardExtensionContext() && chrome.storage) storageSet({ wmVision: visionOn });
      };
      ta.appendChild(visBtn);
      var rescan = el("button", "wm-btn primary", "Scan");
      rescan.type = "button";
      rescan.title = "Scan the open sheet";
      rescan.onclick = scan;
      ta.appendChild(rescan);
    } else if (activeTab === "checks") {
      var tieout = el("button", "wm-btn primary", "Tieout");
      tieout.type = "button";
      tieout.title = "Run live tieout checks (run_checks) and merge FAILs";
      tieout.onclick = runTieoutChecks;
      ta.appendChild(tieout);
      var hardeningGate = el("button", "wm-btn", "Hardening Gate");
      hardeningGate.type = "button";
      hardeningGate.title = "ACFR hardening-gate dry-run + snapshot evaluate";
      hardeningGate.onclick = runHardeningGateChecks;
      ta.appendChild(hardeningGate);
    } else if (activeTab === "workbook") {
      var wb = el("button", "wm-btn primary", "All sheets");
      wb.type = "button";
      wb.title = "Scan every sheet in this Workiva workbook for issues";
      wb.onclick = scanWorkbook;
      ta.appendChild(wb);
    }
  }

  function emptyTabMessage(tabId) {
    if (tabId === "checks") return { big: "No checks run yet", sub: "Run Tieout or Hardening Gate to merge FAIL rows." };
    if (tabId === "workbook") return { big: "No workbook scan yet", sub: "Click All sheets to roll up findings across every sheet." };
    return { big: "Scan the open sheet", sub: "Click Scan to run Wingman detectors on the current sheet." };
  }

  function restoreTabView() {
    if (!panelUi) return;
    syncInspection();
    if (activeTab === "inspect") {
      paintInspection();
      return;
    }
    var cached = tabCache[activeTab];
    if (!cached) {
      updateCtxLine(panelUi, "—", null);
      if (panelUi.thermo) panelUi.thermo.replaceChildren();
      var msg = emptyTabMessage(activeTab);
      stateMsg(panelUi.body, msg.big, msg.sub);
      return;
    }
    if (cached.mode === "workbook") {
      renderWorkbookView(panelUi, cached.ids, cached.rollup);
    } else {
      finishScan(panelUi, cached.ids, cached.data);
    }
  }

  function updateAcfrChip() {
    if (!panelUi || !panelUi.presetChip) return;
    var ids = parseIds();
    if (ids && isAcfrPreset(ids.spreadsheetId)) {
      panelUi.presetChip.textContent = presetLabel("acfr");
      panelUi.presetChip.style.display = "";
    } else {
      panelUi.presetChip.style.display = "none";
    }
  }

  function updateThermoAlert(ui, ids, data) {
    if (!ui.thermo) return;
    ui.thermo.replaceChildren();
    if (!shouldShowThermoAlert(ids && ids.spreadsheetId, data && data.checks)) return;
    ui.thermo.appendChild(el("div", "wm-alert wm-alert-thermo", thermoAlertCopy()));
  }

  function renderOperatorWarnings(ui, status) {
    if (!ui || !ui.operator) return;
    var lines = operatorConfigWarningLines(status);
    ui.operator.replaceChildren();
    ui.operator.classList.toggle("show", !!lines.length);
    lines.slice(0, 3).forEach(function (line) {
      ui.operator.appendChild(el("div", "wm-operator-warn", line));
    });
  }

  function fetchOperatorStatus(ui) {
    return svc("/api/status")
      .then(function (status) { renderOperatorWarnings(ui || panelUi, status); return status; })
      .catch(function () { renderOperatorWarnings(ui || panelUi, null); });
  }

  function syncVisionToggle(btn) {
    btn.classList.toggle("active", visionOn);
    btn.setAttribute("aria-pressed", visionOn ? "true" : "false");
  }

  function panelShell() {
    ensureHost();
    if (panelUi && panelUi.panel) {
      updateAcfrChip();
      return panelUi;
    }
    var panel = el("div", "wm-panel");
    var inspectorStyle = el("style", null, WingmanInspector.styles);
    panel.appendChild(inspectorStyle);
    var head = el("div", "wm-head");
    var logo = el("img", "wm-logo"); logo.src = LOGO; logo.style.width = "22px"; logo.style.height = "22px";
    head.appendChild(logo);
    head.appendChild(el("span", "wm-brand", "Wingman"));
    var presetChip = el("span", "wm-preset", "ACFR");
    presetChip.style.display = "none";
    head.appendChild(presetChip);
    head.appendChild(el("span", "wm-sp"));
    var cmdBtn = el("button", "wm-x wm-icon", "⌘");
    cmdBtn.title = "Command palette (⌘K / Ctrl+K)";
    cmdBtn.onclick = function (ev) { ev.stopPropagation(); toggleCommandPalette(); };
    head.appendChild(cmdBtn);
    var wideBtn = el("button", "wm-x wm-icon wm-wide-toggle", "⧉");
    wideBtn.title = wideMode ? "Narrow panel (" + PANEL_WIDTH_NARROW + "px)" : "Widen panel (" + PANEL_WIDTH_WIDE + "px)";
    wideBtn.onclick = function (ev) { ev.stopPropagation(); toggleWideMode(); };
    head.appendChild(wideBtn);
    var resetSizeBtn = el("button", "wm-x wm-icon wm-reset-size", "↺");
    resetSizeBtn.title = "Reset panel size (" + PANEL_WIDTH_NARROW + "px)";
    resetSizeBtn.disabled = true;
    resetSizeBtn.onclick = function (ev) { ev.stopPropagation(); resetPanelSize(); };
    head.appendChild(resetSizeBtn);
    var theming = el("button", "wm-x wm-icon", themeGlyph());
    theming.title = "Theme: " + theme + " (click to change)";
    theming.onclick = function () { var t = cycleTheme(); theming.textContent = themeGlyph(); theming.title = "Theme: " + t + " (click to change)"; };
    head.appendChild(theming);
    var x = el("button", "wm-x", "×"); x.title = "minimize"; x.onclick = closePanel; head.appendChild(x);
    makeDraggable(head, closePanel);
    panel.appendChild(head);

    var tabsBar = el("div", "wm-tabs");
    tabsBar.setAttribute("role", "tablist");
    TAB_IDS.forEach(function (tid) {
      var tab = el("button", "wm-tab" + (tid === activeTab ? " active" : ""), tabLabel(tid));
      tab.type = "button";
      tab.setAttribute("role", "tab");
      tab.setAttribute("data-tab", tid);
      tab.id = "wm-tab-" + tid;
      tab.setAttribute("aria-controls", "wm-body");
      tab.setAttribute("aria-selected", tid === activeTab ? "true" : "false");
      tab.onclick = function () { setActiveTab(tid); };
      tab.onkeydown = function (event) {
        var enabled = Array.from(tabsBar.querySelectorAll(".wm-tab:not(:disabled)"));
        var index = enabled.indexOf(tab);
        if (event.key === "ArrowRight") index = (index + 1) % enabled.length;
        else if (event.key === "ArrowLeft") index = (index + enabled.length - 1) % enabled.length;
        else if (event.key === "Home") index = 0;
        else if (event.key === "End") index = enabled.length - 1;
        else return;
        event.preventDefault();
        setActiveTab(enabled[index].getAttribute("data-tab"));
        enabled[index].focus();
      };
      tabsBar.appendChild(tab);
    });
    panel.appendChild(tabsBar);

    var tabActions = el("div", "wm-tab-actions");
    panel.appendChild(tabActions);

    var ctx = el("div", "wm-ctx", "—"); ctx.id = "wm-ctx";
    panel.appendChild(ctx);
    var operator = el("div", "wm-operator-warnings");
    panel.appendChild(operator);
    var thermo = el("div", "wm-thermo");
    panel.appendChild(thermo);
    var body = el("div", "wm-body"); body.id = "wm-body";
    body.setAttribute("role", "tabpanel");
    panel.appendChild(body);
    var resizeGrip = el("div", "wm-resize-grip");
    resizeGrip.title = "Drag to resize panel";
    resizeGrip.setAttribute("aria-label", "Resize panel");
    panel.appendChild(resizeGrip);
    attachPanelResize(panel, resizeGrip);
    view.replaceChildren(panel);
    panelUi = {
      panel: panel, ctx: ctx, operator: operator, body: body, tabActions: tabActions, tabsBar: tabsBar,
      thermo: thermo, presetChip: presetChip, wideBtn: wideBtn, resetSizeBtn: resetSizeBtn,
      cmdBtn: cmdBtn,
    };
    applyPanelLayout();
    syncTabUi();
    updateAcfrChip();
    restoreTabView();
    applyPos();
    return panelUi;
  }

  function stateMsg(body, big, sub) {
    var s = el("div", "wm-state");
    s.appendChild(el("b", null, big));
    if (sub) {
      if (shouldShowReloadTabButton({ invalidated: extensionInvalidated, text: sub })) {
        var wrap = el("div", "wm-state-reload");
        wrap.appendChild(makeReloadTabButton(reloadTabButtonLabel("panel")));
        var hint = String(sub).replace(/refresh\s+(this\s+)?(workiva\s+)?tab,?\s*/i, "").trim();
        if (hint) wrap.appendChild(el("span", "wm-state-hint", hint));
        s.appendChild(wrap);
      } else {
        s.append(sub);
      }
    }
    body.replaceChildren(s);
  }

  function sevRank(s) { return s === "high" ? 2 : s === "medium" ? 1 : 0; }

  function reviewIsCurrent(ui, ids, tab) {
    var current = parseIds();
    return ui === panelUi && activeTab === tab && current &&
      current.spreadsheetId === ids.spreadsheetId && current.sheetId === ids.sheetId;
  }

  function handleScanErr(ui, e, ids, tab) {
    if (!reviewIsCurrent(ui, ids, tab)) return;
    var msg = String(e.message || "");
    if (e.offline) stateMsg(ui.body, "Service not running", "Start it: ./run-service.sh");
    else if (/extension reloaded|context invalidated/i.test(msg)) {
      stateMsg(ui.body, "Extension reloaded", "Refresh this Workiva tab, then reopen Wingman.");
    } else if (/^not found$/i.test(msg)) {
      stateMsg(ui.body, "Wingman route missing", "Restart ./run-service.sh, then reload the extension.");
    } else if (/HTTPError 404|Workiva returned 404/i.test(msg)) {
      stateMsg(ui.body, "Workiva can't reach this workbook",
        "Confirm the open spreadsheet is accessible to the Wingman service credentials.");
    } else if (/HTTP 404/i.test(msg)) {
      stateMsg(ui.body, "Wingman service returned 404", msg + " — restart ./run-service.sh if routes look stale.");
    } else stateMsg(ui.body, "Scan failed", msg);
  }

  function finishScan(ui, ids, data) {
    var cacheKey = data.checks && data.checks.requested ? "checks" : "scan";
    if (!reviewIsCurrent(ui, ids, cacheKey)) return;
    updateCtxLine(ui, scanCtxLine(data), data);
    updateThermoAlert(ui, ids, data);
    tabCache[cacheKey] = { mode: "sheet", data: data, ids: ids };
    if (!data.items.length) {
      ui.body.replaceChildren();
      if (shouldShowChecksSummaryCard(data.checks)) renderChecksSummaryCard(ui.body, data.checks);
      renderConnectedReadinessCard(ui.body, data);
      var sub = "This sheet is clean.";
      if (data.vision && data.vision.applied && data.vision.netNew === 0) {
        sub = "Vision ran — no new defects beyond the API pass.";
      } else if (data.vision && data.vision.applied) sub = "Vision layer ran — no defects found.";
      else if (data.vision && data.vision.skipped) sub = "API scan clean · " + data.vision.skipped;
      else if (data.checks && data.checks.skipped) sub = "No scan issues · " + data.checks.skipped;
      else if (data.checks && data.checks.applied && !data.checks.fail_count) {
        sub = (data.checks.suite || "checks") + " green — no FAIL rows.";
      }
      var empty = el("div", "wm-state");
      empty.appendChild(el("b", null, "No issues found"));
      empty.append(sub);
      ui.body.appendChild(empty);
      return;
    }
    renderIssuePanel(ui, data.items, {
      spreadsheetId: ids.spreadsheetId, sheetId: ids.sheetId, isCurrent: true, name: data.sheetName,
    }, { showLanes: true, checks: data.checks, connectedData: data });
  }

  function captureCellCrops(addrs) {
    return new Promise(function (resolve) {
      if (!guardExtensionContext()) {
        return resolve({ ok: false, error: "extension context invalidated", cellImages: {}, errors: {} });
      }
      try {
        chrome.runtime.sendMessage(
          { type: "WM_CAPTURE_CELLS", addrs: addrs, limit: VISION_CROP_MAX },
          function (resp) {
            if (chrome.runtime.lastError) {
              if (isExtensionContextError(chrome.runtime.lastError)) markExtensionInvalidated();
              return resolve({ ok: false, error: "capture messaging failed", cellImages: {}, errors: {} });
            }
            resolve(resp || { ok: false, error: "capture messaging failed", cellImages: {}, errors: {} });
          },
        );
      } catch (e) {
        if (isExtensionContextError(e)) markExtensionInvalidated();
        resolve({ ok: false, error: String(e.message || e), cellImages: {}, errors: {} });
      }
    });
  }

  function checksSuiteLabel(suite) {
    if (suite === "hardening_gate") return "hardening-gate dry-run";
    if (suite === "tieout") return "tieout checks";
    return suite + " checks";
  }

  function runApiQueueScan(ui, ids, checksSuite) {
    stateMsg(ui.body, checksSuite ? ("Running " + checksSuiteLabel(checksSuite) + "…") : "Scanning…");
    var q = "/api/queue?spreadsheetId=" + encodeURIComponent(ids.spreadsheetId) +
      "&sheetId=" + encodeURIComponent(ids.sheetId);
    if (checksSuite) q += "&checks=" + encodeURIComponent(checksSuite);
    svc(q)
      .then(function (data) { finishScan(ui, ids, data); })
      .catch(function (e) { handleScanErr(ui, e, ids, checksSuite ? "checks" : "scan"); });
  }

  function runTieoutChecks() {
    var ui = panelShell();
    setActiveTab("checks");
    var ids = parseIds();
    if (!ids) { stateMsg(ui.body, "Open a Workiva spreadsheet", "then run Tieout from the Checks tab."); return; }
    runApiQueueScan(ui, ids, "tieout");
  }

  function runHardeningGateChecks() {
    var ui = panelShell();
    setActiveTab("checks");
    var ids = parseIds();
    if (!ids) { stateMsg(ui.body, "Open a Workiva spreadsheet", "then run the Hardening Gate from the Checks tab."); return; }
    runApiQueueScan(ui, ids, "hardening_gate");
  }

  function scan() {
    var ui = panelShell();
    setActiveTab("scan");
    var ids = parseIds();
    if (!ids) { stateMsg(ui.body, "Open a Workiva spreadsheet", "then click Scan."); return; }
    if (!visionOn) { runApiQueueScan(ui, ids); return; }

    stateMsg(ui.body, "Scanning…", "API pass first, then pixel crops.");
    svc("/api/queue?spreadsheetId=" + encodeURIComponent(ids.spreadsheetId) +
      "&sheetId=" + encodeURIComponent(ids.sheetId))
      .then(function (apiData) {
        if (!reviewIsCurrent(ui, ids, "scan")) return;
        var addrs = collectVisionAddrsFromScan(apiData, VISION_CROP_MAX);
        if (!addrs.length) {
          apiData.vision = apiData.vision || {
            requested: true, applied: false, skipped: "no flagged addresses",
          };
          finishScan(ui, ids, apiData);
          return;
        }
        stateMsg(ui.body, "Capturing cells…", addrs.length + " crops (jump + screenshot).");
        captureCellCrops(addrs).then(function (cap) {
          if (!reviewIsCurrent(ui, ids, "scan")) return;
          if (!cap.ok || !cap.captured) {
            apiData.vision = {
              requested: true,
              applied: false,
              skipped: cap.error || ("capture failed (" + addrs.length + " cells)"),
              captureErrors: cap.errors,
            };
            finishScan(ui, ids, apiData);
            return;
          }
          post("/api/queue", buildVisionQueuePayload(ids, cap.cellImages, true))
            .then(function (visionData) {
              if (cap.captureMethods) {
                visionData.vision = visionData.vision || {};
                visionData.vision.captureMethods = cap.captureMethods;
              }
              finishScan(ui, ids, visionData);
            })
            .catch(function () {
              apiData.vision = { requested: true, applied: false, skipped: "vision queue POST failed" };
              finishScan(ui, ids, apiData);
            });
        });
      })
      .catch(function (e) { handleScanErr(ui, e, ids, "scan"); });
  }

  function hasHigh(s) { return (s.groups || []).some(function (g) { return g.severity === "high"; }); }

  function fixPostBody(jc, addr, group, opts) {
    opts = opts || {};
    var body = { spreadsheetId: jc.spreadsheetId, sheetId: jc.sheetId, addr: addr, kind: group.kind };
    var tgt = opts.target != null ? opts.target : group.target;
    if (tgt) body.target = tgt;
    return body;
  }

  // Sheet scan: sticky triage bar (bulk fix + lane filters) then the issue list.
  function renderIssuePanel(ui, groups, jc, opts) {
    opts = opts || {};
    var showLanes = opts.showLanes !== false;
    var checksMeta = opts.checks || null;
    var connectedData = opts.connectedData || null;
    ui.body.replaceChildren();
    if (shouldShowChecksSummaryCard(checksMeta)) renderChecksSummaryCard(ui.body, checksMeta);
    if (connectedData) renderConnectedReadinessCard(ui.body, connectedData);
    var lane = "all", fixAllChecked = null;
    var triage = el("div", "wm-triage");
    var fixAllBtn = el("button", "wm-btn primary wm-fixall");
    var segs = el("div", "wm-segs");
    var listHost = el("div", "wm-list");
    if (showLanes) {
      triage.appendChild(fixAllBtn);
      triage.appendChild(segs);
    }
    ui.body.appendChild(triage);
    ui.body.appendChild(listHost);
    var t = tallyGroups(groups);
    var onSheet = !jc || jc.isCurrent !== false;

    function syncFixAllBtn() {
      if (!showLanes || !onSheet || !t.fixable) { fixAllBtn.style.display = "none"; return; }
      fixAllBtn.style.display = "";
      if (!fixAllChecked) {
        fixAllBtn.textContent = "Fix all " + t.fixable + " safe";
        fixAllBtn.disabled = false;
        fixAllBtn.classList.remove("done");
      } else {
        var n = fixAllChecked.filter(function (x) { return x.r.status === "dry-run" && !x._done; }).length;
        if (n) {
          fixAllBtn.textContent = "Apply all " + n + " safe";
          fixAllBtn.disabled = false;
          fixAllBtn.classList.remove("done");
        } else {
          fixAllBtn.textContent = fixAllChecked.some(function (x) { return x._done; }) ? "All safe fixes applied" : "No safe fixes";
          fixAllBtn.disabled = true;
          fixAllBtn.classList.add("done");
        }
      }
    }
    function syncSegs() {
      if (!showLanes) { segs.replaceChildren(); return; }
      segs.replaceChildren();
      var lanes = ["all", "fixable", "review"];
      if (t.exportProof) lanes.splice(2, 0, "export");
      lanes.forEach(function (lk) {
        var b = el("button", "wm-seg" + (lane === lk ? " active" : ""), segmentLabel(lk, t));
        b.onclick = function () { lane = lk; syncSegs(); paintList(); };
        segs.appendChild(b);
      });
    }
    function paintList() {
      var filtered = filterGroupsByLane(groups, lane);
      listHost.replaceChildren();
      if (lane === "export" && filtered.length) {
        listHost.appendChild(el("div", "wm-export-banner", exportProofFilterBanner()));
      }
      if (!filtered.length) {
        var empty = el("div", "wm-state");
        empty.style.padding = "16px";
        empty.textContent = lane === "fixable" ? "No fixable issues."
          : (lane === "export" ? "No export-proof items."
            : (lane === "review" ? "No review items." : "No issues."));
        listHost.appendChild(empty);
        return;
      }
      renderGroups(listHost, filtered, jc, { exportMode: lane === "export", checks: checksMeta });
    }
    function runFixAllDryRun() {
      fixAllBtn.disabled = true;
      fixAllBtn.textContent = "Checking\u2026";
      mapLimit(collectSafeFixAddrs(groups), 5, function (item) {
        return post("/fix", fixPostBody(jc, item.addr, item.group))
          .then(function (r) { return { addr: item.addr, group: item.group, r: r }; })
          .catch(function (e) { return { addr: item.addr, group: item.group, r: { status: "error", error: e.message } }; });
      }).then(function (results) {
        fixAllChecked = results;
        syncFixAllBtn();
        var ok = results.filter(function (x) { return x.r.status === "dry-run"; }).length;
        flash(ok ? ok + " safe fix" + (ok === 1 ? "" : "es") + " ready — click to apply" : "No safe fixes found");
      });
    }
    function runFixAllApply() {
      var pending = fixAllChecked.filter(function (x) { return x.r.status === "dry-run" && !x._done; });
      if (!pending.length) return;
      fixAllBtn.disabled = true;
      fixAllBtn.textContent = "Applying\u2026";
      mapLimit(pending, 1, function (x) {
        return post("/apply", fixPostBody(jc, x.addr, x.group))
          .then(function (res) { x._done = true; return { addr: x.addr, res: res }; })
          .catch(function (e) { return { addr: x.addr, res: { status: "error", error: e.message } }; });
      }).then(function (applied) {
        var ok = applied.filter(function (a) { return a.res.status === "applied"; }).length;
        syncFixAllBtn();
        flash(ok ? "Applied " + ok + " safe fix" + (ok === 1 ? "" : "es") : "Apply finished — check rows");
      });
    }
    fixAllBtn.onclick = function () {
      if (!fixAllChecked) {
        // The dry-run writes nothing — run it directly, matching the per-group "Check fixes"
        // button. The single proof gate is the confirm on the Apply step below, before any write.
        runFixAllDryRun();
        return;
      }
      var pending = fixAllChecked.filter(function (x) { return x.r.status === "dry-run" && !x._done; });
      if (!pending.length) return;
      showConfirmDialog(panelUi && panelUi.panel, bulkApplyConfirmCopy(pending.length), runFixAllApply);
    };
    syncFixAllBtn();
    syncSegs();
    paintList();
  }

  // Copy text to the clipboard from the button's click gesture; fall back to a hidden textarea
  // + execCommand when the async Clipboard API is unavailable or blocked.
  function copyText(text, btn) {
    var label = btn.textContent;
    function settle(ok) { btn.textContent = ok ? "Copied" : "Copy failed"; setTimeout(function () { btn.textContent = label; }, 1500); }
    function fallback() {
      try {
        var ta = document.createElement("textarea");
        ta.value = text; ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
        (document.body || document.documentElement).appendChild(ta);
        ta.focus(); ta.select();
        var ok = document.execCommand("copy"); ta.remove(); settle(ok);
      } catch (e) { settle(false); }
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { settle(true); }, fallback);
      } else { fallback(); }
    } catch (e) { fallback(); }
  }

  function renderWorkbookView(ui, ids, rollup) {
    if (!reviewIsCurrent(ui, ids, "workbook")) return;
    var withIssues = rollup.sheets.filter(function (s) { return s.findingCount || s.error; }).length;
    var clean = Math.max(0, (rollup.scanned || 0) - withIssues);
    updateCtxLine(ui,
      rollup.findingTotal + " finding" + (rollup.findingTotal === 1 ? "" : "s") +
      " · " + (rollup.scanned || rollup.sheets.length) + " sheet" + ((rollup.scanned || rollup.sheets.length) === 1 ? "" : "s") +
      (rollup.scanned ? " · " + clean + " clean" : "") +
      (rollup.truncatedSheets ? " · capped at " + rollup.scanned : ""),
      null);
    if (ui.thermo) ui.thermo.replaceChildren();
    if (!rollup.sheets.length) { stateMsg(ui.body, "No issues found", "This workbook is clean."); return; }
    ui.body.replaceChildren();
    var bar = el("div", "wm-wbbar");
    var rep = el("button", "wm-btn ghost", "Copy report");
    rep.title = "Copy a markdown defect list for this workbook";
    rep.onclick = function (ev) { ev.stopPropagation(); var d = isRollupStale(rollup) ? Object.assign({}, rollup, { stale: true }) : rollup; copyText(buildReport(d), rep); };
    bar.appendChild(rep);
    ui.body.appendChild(bar);
    var listHost = el("div"); ui.body.appendChild(listHost);
    renderSheetList(listHost, rollup, ids);
  }

  // ---- workbook scan: every sheet, rolled up ----
  function scanWorkbook() {
    var ui = panelShell();
    setActiveTab("workbook");
    var ids = parseIds();
    if (!ids) { stateMsg(ui.body, "Open a Workiva spreadsheet", "then click All sheets."); return; }
    stateMsg(ui.body, "Scanning workbook…", "every sheet — this can take a few seconds.");
    svc("/api/queue?spreadsheetId=" + encodeURIComponent(ids.spreadsheetId))
      .then(function (data) {
        if (!reviewIsCurrent(ui, ids, "workbook")) return;
        var rollup = queueToWorkbookRollup(data);
        rollup.generated_at = new Date().toISOString();
        tabCache.workbook = { mode: "workbook", rollup: rollup, ids: ids };
        renderWorkbookView(ui, ids, rollup);
      })
      .catch(function (e) {
        if (!reviewIsCurrent(ui, ids, "workbook")) return;
        if (e.offline) stateMsg(ui.body, "Service not running", "Start it: ./run-service.sh");
        else { stateMsg(ui.body, "Workbook scan failed", e.message); }
      });
  }

  function renderSheetList(body, data, ids) {
    body.replaceChildren();
    // issues first (most findings on top), errors next, clean sheets last
    data.sheets.slice().sort(function (a, b) {
      return (b.findingCount - a.findingCount) || ((b.error ? 1 : 0) - (a.error ? 1 : 0));
    }).forEach(function (s) {
      var isCurrent = s.sheetId === ids.sheetId;
      var wrap = el("div", "wm-sheet");
      var row = el("div", "wm-sheet-row");
      var dotCls = s.error ? "err" : (!s.findingCount ? "clean" : (hasHigh(s) ? "high" : "some"));
      row.appendChild(el("span", "wm-sdot " + dotCls));
      row.appendChild(el("span", "wm-sname", s.name || "(unnamed sheet)"));
      if (isCurrent) row.appendChild(el("span", "wm-here", "open"));
      if (s.truncated) { var p = el("span", "wm-lane", "partial"); p.title = "large sheet — first page scanned"; row.appendChild(p); }
      row.appendChild(el("span", "wm-meta", s.error ? "error" : (s.findingCount ? s.findingCount + " issue" + (s.findingCount === 1 ? "" : "s") : "clean")));
      wrap.appendChild(row);
      var det = el("div", "wm-sheet-det");
      if (s.error) {
        var e = el("div", "wm-err"); e.style.padding = "2px 12px 6px"; e.textContent = "Couldn't scan: " + s.error; det.appendChild(e);
      } else if (!s.findingCount) {
        var note = el("div", "wm-note"); note.style.padding = "2px 12px 6px"; note.textContent = "No issues found."; det.appendChild(note);
      } else {
        var inner = el("div");
        renderGroups(inner, s.groups, { spreadsheetId: ids.spreadsheetId, sheetId: s.sheetId, isCurrent: isCurrent, name: s.name });
        det.appendChild(inner);
      }
      wrap.appendChild(det);
      row.onclick = function () { wrap.classList.toggle("open"); };
      body.appendChild(wrap);
    });
  }

  function renderGroups(body, groups, jc, opts) {
    opts = opts || {};
    body.replaceChildren();
    var checksMeta = opts.checks || null;
    var onSheet = !jc || jc.isCurrent !== false;
    // Short result lists open on render so the cells/actions are one glance, not one click each.
    var autoOpen = shouldAutoExpandGroups(groups);
    groups.slice().sort(function (a, b) { return sevRank(b.severity) - sevRank(a.severity); }).forEach(function (g) {
      var wrap = el("div", "wm-grp" + ((opts.exportMode || isExportProofItem(g) || autoOpen) ? " open" : ""));
      var row = el("div", "wm-row");
      row.appendChild(el("span", "wm-sev " + (g.severity || ""), g.severity || ""));
      row.appendChild(el("span", "wm-kind", g.kind.replace(/-/g, " ")));
      row.appendChild(el("span", "wm-sig wm-mono", g.signature));
      row.appendChild(el("span", "wm-count", String(g.count)));
      row.appendChild(el("span", "wm-lane " + (g.fix_lane === "safe-auto" ? "fix" : (isExportProofItem(g) ? "export" : "")), isExportProofItem(g) ? "export proof" : (g.fix_lane === "safe-auto" ? "fixable" : "review")));
      if (g.kind === "check-tieout") {
        appendScorecardAgeBadge(row, resolveScorecardGeneratedAt(checksMeta, g));
      }
      var tj = g.kind === "check-tieout" ? tieoutJumpContext(g, jc) : null;
      var jumpAddr = tj && tj.addr ? tj.addr : (g.addrs && g.addrs.length ? g.addrs[0] : null);
      if (jumpAddr && (tj ? tj.canJump : onSheet)) {
        var jump = el("button", "wm-jump", "↗");
        jump.title = "Jump to " + jumpAddr + (g.addrs && g.addrs.length > 1 ? " (first of " + g.addrs.length + ")" : "");
        jump.onclick = function (ev) { ev.stopPropagation(); gotoCell(jumpAddr, jump); };
        row.appendChild(jump);
      } else if (jumpAddr && tj && !tj.canJump) {
        var offHint = el("span", "wm-lane", "off sheet");
        offHint.title = "Open “" + (tj.sheetName || "target sheet") + "” to jump to " + jumpAddr;
        row.appendChild(offHint);
      } else if (onSheet && g.addrs && g.addrs.length) {
        var jump2 = el("button", "wm-jump", "↗");
        jump2.title = "Jump to " + g.addrs[0] + (g.addrs.length > 1 ? " (first of " + g.addrs.length + ")" : "");
        jump2.onclick = function (ev) { ev.stopPropagation(); gotoCell(g.addrs[0], jump2); };
        row.appendChild(jump2);
      }
      wrap.appendChild(row);
      var det = el("div", "wm-det");
      if (g.kind === "check-tieout") {
        var coord = tieoutCoordLabel(g);
        if (coord) {
          var coordLine = el("div", "wm-dx-action wm-mono", coord);
          coordLine.title = "Tieout scorecard cell";
          det.appendChild(coordLine);
        }
        appendTieoutBadges(det, g);
      }
      var cells = el("div", "wm-cells");
      var vals = groupValues(g);
      g.addrs.forEach(function (a) { cells.appendChild(addrChip(a, jc, vals)); });
      det.appendChild(cells);
      appendDiagnosis(det, g, jc);
      if (g.fix_lane === "safe-auto") {
        var btn = el("button", "wm-btn ghost", "Check fixes");
        btn.onclick = function (ev) { ev.stopPropagation(); reviewGroup(det, btn, g, jc); };
        det.appendChild(btn);
      }
      wrap.appendChild(det);
      row.onclick = function () { wrap.classList.toggle("open"); };
      body.appendChild(wrap);
    });
  }

  function mapLimit(items, limit, fn) {
    var i = 0, out = [];
    function worker() { return Promise.resolve().then(function step() { if (i >= items.length) return; var k = i++; return Promise.resolve(fn(items[k])).then(function (v) { out[k] = v; return step(); }); }); }
    var n = Math.min(limit, items.length || 1), ws = [];
    for (var w = 0; w < n; w++) ws.push(worker());
    return Promise.all(ws).then(function () { return out; });
  }

  function reviewGroup(det, btn, g, jc) {
    btn.disabled = true; btn.textContent = "Checking…";
    mapLimit(g.addrs, 5, function (addr) {
      return post("/fix", fixPostBody(jc, addr, g))
        .then(function (r) { return { addr: addr, r: r }; })
        .catch(function (e) { return { addr: addr, r: { status: "error", error: e.message } }; });
    }).then(function (results) {
      btn.remove();
      var sec = el("div");
      var fixable = results.filter(function (x) { return x.r.status === "dry-run"; });
      if (fixable.length) {
        appendColumnApplyButtons(sec, fixable, g, jc);
      }
      results.forEach(function (x) {
        var fr = el("div", "wm-fix");
        fr.appendChild(addrChip(x.addr, jc, groupValues(g)));
        if (x.r.status === "dry-run") {
          if (g.kind === "low-contrast") {
            var c1 = el("span", "wm-chip"); c1.style.background = x.r.before || "#000";
            var c2 = el("span", "wm-chip"); c2.style.background = x.r.after || "#000";
            fr.appendChild(c1); fr.appendChild(el("span", "wm-arrow", "→")); fr.appendChild(c2);
            fr.appendChild(el("span", "wm-ratio", (x.r.ratioBefore != null ? x.r.ratioBefore : "?") + "→" + (x.r.ratioAfter != null ? x.r.ratioAfter : "?") + ":1"));
          } else if (g.kind === "negative-without-parens" || g.kind === "junk-decimal" ||
                     g.kind === "missing-thousands-separator" || g.kind === "number-on-accounting-column" ||
                     g.kind === "precision-mismatch" || g.kind === "prefix-mismatch" ||
                     g.kind === "zero-display-mismatch") {
            fr.appendChild(el("span", "wm-mono", x.r.beforeFormat || x.r.before || ""));
            fr.appendChild(el("span", "wm-arrow", "→"));
            fr.appendChild(el("span", "wm-mono", x.r.afterFormat || x.r.after || ""));
          } else {
            fr.appendChild(el("span", "wm-mono", JSON.stringify(x.r.before || "")));
            fr.appendChild(el("span", "wm-arrow", "→"));
            fr.appendChild(el("span", "wm-mono", JSON.stringify(x.r.after || "")));
          }
          var ap = el("button", "wm-btn ghost", "Apply"); ap.style.marginLeft = "auto";
          var doApply = function () {
            x._apply = null; ap.disabled = true; ap.textContent = "…";
            post("/apply", fixPostBody(jc, x.addr, g)).then(function (res) {
              if (res.status === "applied") ap.replaceWith(el("span", "wm-done", "fixed"));
              else if (res.status === "style-locked") { var sl = el("span", "wm-note", "conditional format — fix rule in Workiva"); sl.title = res.reason || ""; ap.replaceWith(sl); }
              else if (res.status === "mismatch-reverted") ap.replaceWith(el("span", "wm-err", "reverted (no change)"));
              else ap.replaceWith(el("span", "wm-err", res.warning || res.reason || res.error || res.status || "failed"));
            }).catch(function (e) { ap.replaceWith(el("span", "wm-err", e.message)); });
          };
          ap.onclick = doApply; x._apply = doApply; fr.appendChild(ap);
        } else if (x.r.status === "refused") {
          if (isTextTrapRefusal(x.r)) {
            appendTextTrapAlert(fr);
          } else if (/rich\s*text/i.test(String(x.r.reason || ""))) {
            fr.appendChild(el("span", "wm-note", "rich text — fix in Workiva UI"));
          } else {
            fr.appendChild(el("span", "wm-note", x.r.reason || "refused"));
          }
        } else if (x.r.status === "no-fix-needed") {
          fr.appendChild(el("span", "wm-note", x.r.reason || "no fix needed"));
        } else if (x.r.status === "not-in-scan-page") {
          fr.appendChild(el("span", "wm-err", x.r.reason || "cell not readable — re-scan sheet"));
        } else {
          fr.appendChild(el("span", "wm-err", x.r.error || x.r.reason || "error"));
        }
        sec.appendChild(fr);
      });
      det.appendChild(sec);
    });
  }

  // keep the pill value fresh (only when collapsed; the panel owns the view when open)
  function tick() {
    if (!guardExtensionContext()) return;
    if (open) syncInspection();
    if (!parseIds()) { guard.everOk = false; guard.fails = 0; pillState = "idle"; pillValue = "ready"; if (!open) renderPill(); return; }
    var elx = document.querySelector(SEL);
    var r = classifyAddress(elx ? elx.textContent : null);
    if (r.status === "drift-null" && !guard.everOk) return;
    var g = guard.record(r.status);
    if (r.status === "ok") {
      var v = r.addr + (r.isRange ? " (range)" : "");
      if (v === last) return;
      last = v; pillState = "ok"; pillValue = v; if (!open) renderPill();
    } else if (r.status === "empty") {
      pillState = "idle"; pillValue = "ready"; if (!open) renderPill();
    } else if (g.drift) {
      pillState = "drift"; pillValue = "can't read cell"; if (!open) renderPill();
    }
  }

  if (guardExtensionContext() && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener(function (msg) {
      if (!guardExtensionContext()) return;
      if (msg && msg.type === "WM_TOGGLE") { open ? closePanel() : openPanel(); }
    });
  }
  document.addEventListener("keydown", function (e) {
    if (!open) return;
    var mod = e.metaKey || e.ctrlKey;
    if (mod && String(e.key).toLowerCase() === "k") {
      e.preventDefault();
      toggleCommandPalette();
      return;
    }
    if (paletteOpen && e.key === "Escape") {
      e.preventDefault();
      closeCommandPalette();
    }
  }, true);
  renderPill();
  if (guardExtensionContext()) {
    domObserver = new MutationObserver(function () { tick(); });
    domObserver.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    tick();
    setTimeout(tick, 1500);
  } else {
    markExtensionInvalidated();
  }
})();

/* Read-only selected-cell context and evidence. No scan, fix, storage or AI calls. */
(function (root) {
  "use strict";

  function selection(href, address, demo) {
    var url = new URL(href);
    var path = demo ? url.hash.slice(1) : url.pathname;
    if (!demo && (url.protocol !== "https:" || !/(^|\.)(wdesk|workiva)\.com$/.test(url.hostname))) {
      return { message: "Open a Workiva spreadsheet to inspect a cell." };
    }
    var m = path.match(/^(?:\/a\/([A-Za-z0-9_-]+))?\/spreadsheet\/([A-Za-z0-9_-]+)\/sheet\/([A-Za-z0-9_-]+)\/?$/);
    if (!m) return { message: /\/doc\//.test(path)
      ? "Document and comment tracing is not connected yet. Open the source spreadsheet to inspect a cell."
      : "Open a spreadsheet sheet to inspect a cell." };
    var addr = String(address || "").trim();
    if (!/^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(addr)) return {
      message: addr.includes(":") ? "Select one cell, not a range. Nothing has been read."
        : "Select a cell in Workiva. Its address will appear here.",
    };
    return { target: { workspaceId: m[1] || null, spreadsheetId: m[2], sheetId: m[3], addr: addr } };
  }

  function key(context) { return JSON.stringify(context.target || context.message); }
  function matches(target, reply) {
    return !!reply && ["spreadsheetId", "sheetId", "addr"].every(function (k) { return target[k] === reply[k]; });
  }
  function valueText(observation) {
    if (!observation || observation.status !== "observed") return "Not available";
    var value = observation.formula || observation.value;
    if (value === null || value === "") return "(blank)";
    return typeof value === "string" ? value : JSON.stringify(value);
  }
  function formatText(observation) {
    if (!observation || observation.status !== "observed") return "Not available";
    var vf = observation.value;
    var parts = [vf.valueFormatType || "Type not provided"];
    if (vf.precision) parts.push(vf.precision.auto ? "automatic precision" : vf.precision.value + " decimals");
    if (vf.enteredIn) parts.push("entered in " + vf.enteredIn);
    if (vf.shownIn) parts.push("shown in " + vf.shownIn);
    return parts.join(" · ");
  }

  function render(body, context, state, inspect) {
    var hadFocus = body.contains(body.getRootNode().activeElement);
    function el(tag, cls, text) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    }
    var page = el("section", "wi-inspector");
    page.setAttribute("aria-label", "Selected cell inspector");
    page.tabIndex = -1;
    var head = el("div", "wi-heading");
    head.appendChild(el("span", "wi-eyebrow", "SELECTED CELL"));
    head.appendChild(el("span", "wi-readonly", "Read-only"));
    page.appendChild(head);
    if (!context.target) {
      page.appendChild(el("h2", "wi-address", "Start with one cell"));
      page.appendChild(el("p", "wi-description", context.message));
      body.replaceChildren(page);
      if (hadFocus) page.focus();
      return;
    }
    var target = context.target, data = state.data;
    page.appendChild(el("h2", "wi-address", target.addr));
    page.appendChild(el("p", "wi-sheet", data && data.sheetName ? data.sheetName : "Sheet " + target.sheetId));
    var button = el("button", "wm-btn primary wi-inspect", state.loading ? "Reading cell…" : "Inspect selected cell");
    button.type = "button";
    button.disabled = !!state.loading;
    button.onclick = inspect;
    page.appendChild(button);
    var feedback = el("p", "wi-description");
    feedback.setAttribute("role", "status");
    feedback.textContent = state.error || (state.loading ? "Reading " + target.addr + " and its link metadata. No workbook scan or changes."
      : data && data.status === "unavailable" ? "No cell evidence returned. Inspect again to retry."
      : data && data.status === "changed" ? "Cell changed while reading. Inspect again."
      : data ? "Read at " + new Date(data.observedAt).toLocaleTimeString() + ". Inspect again after edits."
        : "See the content, result and format separately. No changes will be made.");
    page.appendChild(feedback);
    function row(parent, label, text, code) {
      var item = el("div", "wi-row");
      item.appendChild(el("dt", null, label));
      var value = el("dd");
      value.appendChild(el(code ? "code" : "span", null, text));
      item.appendChild(value);
      parent.appendChild(item);
    }
    if (data) {
      if (data.content || data.calculated) {
        var evidence = el("dl", "wi-evidence");
        row(evidence, data.content && data.content.kind === "formula" ? "Formula" : "Stored content", valueText(data.content), true);
        row(evidence, "Calculated result", valueText(data.calculated), true);
        row(evidence, "Native format", formatText(data.nativeFormat));
        page.appendChild(evidence);
        var explanations = {
          formula: "",
          number: "This cell stores a number. Without its source policy, Wingman cannot say whether it should be a formula or an approved frozen value.",
          text: "This cell stores text. No formatting change is proposed.",
          blank: "The stored content is blank. This alone does not establish a broken link.",
          boolean: "This cell stores a boolean value, not a financial amount.",
        };
        var explanation = explanations[data.content && data.content.kind];
        if (explanation === undefined) explanation = "Stored content could not be classified. No hardcode or formula judgment is made.";
        if (explanation) page.appendChild(el("p", "wi-description", explanation));
      }
      if (data.content || data.calculated) {
        var sources = el("details", "wi-sources wi-details");
        sources.setAttribute("aria-label", "References and links");
        var source = data.source || {}, formula = source.formula || {}, links = source.rangeLinks || {};
        var items = links.items || [];
        var sourceSummary = el("summary", null, "References & links");
        var formulaSummary = formula.status === "text_only"
          ? formula.references.length + " formula address" + (formula.references.length === 1 ? "" : "es")
          : formula.status === "not_formula" ? "No formula" : "Formula unavailable";
        var linkSummary = links.status === "observed"
          ? (items.some(function (link) { return link.direction === "destination"; }) ? "Linked table"
            : items.length ? "Range-link source" : "No covering range link")
          : links.status === "partial" ? "Link list incomplete"
          : links.status === "unavailable" ? "Links unavailable" : "Links not inspected";
        if (formula.unresolved && formula.unresolved.length) formulaSummary += " · unresolved notation";
        if (items.some(function (link) { return link.direction === "destination" && link.resolution !== "observed"; })) {
          linkSummary += " · source unresolved";
        }
        sourceSummary.appendChild(el("span", "wi-source-summary", formulaSummary + " · " + linkSummary));
        sources.appendChild(sourceSummary);
        var refs = el("dl", "wi-evidence");
        if (formula.status === "text_only") {
          row(refs, "Addresses in formula", formula.references.join("\n") || "No supported static references extracted", true);
          if (formula.literalNumbers.length) row(refs, "Literal numbers", formula.literalNumbers.join(" · "), true);
          if (formula.unresolved.length) row(refs, "Not resolved", formula.unresolved.join("\n"), true);
          sources.appendChild(refs);
          sources.appendChild(el("p", "wi-description", "Formula text only. Referenced cells were not read; literal numbers are not automatically errors."));
        } else {
          sources.appendChild(el("p", "wi-description", formula.status === "not_formula"
            ? "No stored formula. Workiva links are checked separately."
            : "Formula references could not be inspected."));
        }
        var linkStatus = links.status === "observed"
          ? (items.length ? "Range-link metadata read for this cell." : "No range link covers this cell in the returned table metadata.")
          : links.status === "partial" ? "Range-link list incomplete. Only the evidence below was returned."
          : links.status === "unavailable" ? "Range links unavailable. This does not mean the cell is unlinked."
          : "Range links not inspected.";
        sources.appendChild(el("p", links.status === "partial" || links.status === "unavailable" ? "wi-warning" : "wi-description", linkStatus));
        items.forEach(function (link) {
          var linkDetail = el("details", "wi-details wi-link");
          var incoming = link.direction === "destination";
          linkDetail.appendChild(el("summary", null, incoming
            ? "Linked from " + (link.sourceRange || "an unresolved source range")
            : "Source range " + link.range + " includes this cell"));
          var fields = el("dl", "wi-evidence");
          row(fields, "Range-link ID", link.id, true);
          if (incoming) {
            row(fields, "Source table ID", link.source.table, true);
            row(fields, "Source range-link ID", link.source.rangeLink, true);
            row(fields, "Reported source revision", link.source.revision, true);
            row(fields, "Source range lookup", link.resolution === "observed"
              ? "Read at the reported revision; source values not read"
              : "Not available at the reported revision; no latest-revision substitute");
          } else {
            row(fields, "Last published revision", link.revision || "Not provided", true);
          }
          linkDetail.appendChild(fields);
          sources.appendChild(linkDetail);
        });
        sources.appendChild(el("p", "wi-description", "Range links only, not cell-level links or a full source chain. Link metadata is not proof that reports are up to date."));
        page.appendChild(sources);
      }
      (data.warnings || []).forEach(function (warning) {
        page.appendChild(el("p", "wi-warning", warning));
      });
      var detail = el("details", "wi-details");
      detail.appendChild(el("summary", null, "Context & raw format"));
      var metadata = el("dl", "wi-evidence");
      row(metadata, "Page workspace", target.workspaceId || "Not supplied by this page");
      row(metadata, "Workbook ID", target.spreadsheetId, true);
      row(metadata, "Sheet ID", target.sheetId, true);
      row(metadata, "Metadata revision", data.revision || "Not provided; not a pinned snapshot", true);
      row(metadata, "Format fields", valueText(data.nativeFormat), true);
      detail.appendChild(metadata);
      detail.appendChild(el("p", "wi-description", "Page identity is not proof of client, fiscal period, or working-copy authority. Rendered document text is not inspected."));
    }
    var scope = el("dl", "wi-scope");
    row(scope, "Full source chain", "Not traced");
    row(scope, "Source policy", "Not connected");
    row(scope, "Downstream reports", "Not inspected");
    page.appendChild(scope);
    if (data) page.appendChild(detail);
    body.replaceChildren(page);
    if (hadFocus) (button.disabled ? page : button).focus();
  }

  var styles = ".wi-inspector{padding:16px;overflow-wrap:anywhere}.wi-inspector *{box-sizing:border-box}" +
    ".wi-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.wi-eyebrow{font-size:10px;letter-spacing:.09em;color:var(--muted);font-weight:600}.wi-readonly{font-size:11px;color:var(--muted)}" +
    ".wi-address{font-size:32px;line-height:1.15;font-weight:500;letter-spacing:-.04em;margin:10px 0 4px}.wi-sheet{margin:0 0 14px;color:var(--muted)}" +
    ".wi-inspect{min-height:40px;width:100%}.wi-inspect:disabled{opacity:.65;cursor:wait}.wi-description{color:var(--muted);font-size:12px;line-height:1.55;margin:10px 0 12px}" +
    ".wi-evidence,.wi-scope{margin:0}.wi-row{padding:5px 0;border-top:1px solid var(--border-soft)}.wi-row dt{font-size:11px;color:var(--muted);margin-bottom:4px}.wi-row dd{margin:0;font-size:13px}.wi-row code{font:12px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}" +
    ".wi-warning{font-size:12px;line-height:1.55;color:var(--muted);border-left:2px solid var(--warn);padding-left:10px;margin:12px 0}.wi-details{margin:8px 0 0}.wi-details summary{min-height:40px;cursor:pointer;display:list-item;padding:10px 0;color:var(--accent-text)}" +
    ".wi-sources{margin:8px 0;border-top:1px solid var(--border-soft);border-bottom:1px solid var(--border-soft)}.wi-sources>summary{font-size:13px;line-height:1.5}.wi-source-summary{display:block;font-size:11px;color:var(--muted);margin-top:4px}.wi-link{border-top:1px solid var(--border-soft)}.wi-link summary{font-size:12px;line-height:1.5}" +
    ".wi-details summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}.wi-scope .wi-row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:6px 0}.wi-scope dt{margin:0}.wi-scope dd{font-size:12px;color:var(--muted)}" +
    ".wm-panel.wi-mode .wm-tab-actions,.wm-panel.wi-mode .wm-ctx,.wm-panel.wi-mode .wm-operator-warnings,.wm-panel.wi-mode .wm-thermo,.wm-panel.wi-mode .wm-preset{display:none!important}" +
    ".wm-panel.wi-mode{max-width:calc(100vw - 28px)}.wm-panel.wi-mode .wm-tab{min-height:40px}.wm-panel.wi-mode .wm-head .wm-x{min-height:32px;min-width:32px}.wm-panel.wi-mode .wm-reset-size,.wm-panel.wi-mode .wm-wide-toggle{display:none}";
  var api = { selection: selection, key: key, matches: matches, valueText: valueText, formatText: formatText, render: render, styles: styles };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WingmanInspector = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

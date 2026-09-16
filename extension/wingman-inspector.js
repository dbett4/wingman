/* Read-only selected-cell context and evidence. No scan, fix, storage or AI calls. */
(function (root) {
  "use strict";

  function selection(href, address, demo) {
    var url = new URL(href);
    var path = demo ? url.hash.slice(1) : url.pathname;
    if (!demo && (url.protocol !== "https:" || !/(^|\.)(wdesk|workiva)\.com$/.test(url.hostname))) {
      return { message: "Open a Workiva spreadsheet to inspect a cell." };
    }
    var doc = path.match(/^\/a\/([A-Za-z0-9_-]+)\/doc\/([A-Za-z0-9_-]+)\/r\/-1\/v\/1\/sec\/([A-Za-z0-9_-]+)\/?$/);
    if (doc) return { document: { workspaceId: doc[1], documentId: doc[2], sectionId: doc[3] } };
    // -1 is Workiva's live-sheet route. Other revisions must not read latest data.
    var m = path.match(/^(?:\/a\/([A-Za-z0-9_-]+))?\/spreadsheet\/([A-Za-z0-9_-]+)(?:\/-1)?\/sheet\/([A-Za-z0-9_-]+)\/?$/);
    if (!m) return { message: /\/doc\//.test(path)
      ? "Open a section in the current document view to choose a table cell. Historical pages and inline text are not supported."
      : "Open a spreadsheet sheet to inspect a cell." };
    var addr = String(address || "").trim();
    if (!/^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(addr)) return {
      message: addr.includes(":") ? "Select one cell, not a range. Nothing has been read."
        : "Select a cell in Workiva. Its address will appear here.",
    };
    return { target: { workspaceId: m[1] || null, spreadsheetId: m[2], sheetId: m[3], addr: addr } };
  }

  function key(context) { return JSON.stringify(context.target || context.document || context.message); }
  function matches(target, reply) {
    var fields = target.documentId ? ["documentId", "sectionId", "tableId", "revision", "addr"] : ["spreadsheetId", "sheetId", "addr"];
    return !!reply && fields.every(function (k) { return target[k] === reply[k]; });
  }
  function matchesSource(target, reply) {
    return !!reply && ["tableId", "revision", "addr"].every(function (k) { return target[k] === reply[k]; });
  }
  function traceBlock(origin, trail, target) {
    var seen = [{ tableId: origin.tableId, revision: origin.contentRevision, addr: origin.target.addr }]
      .concat(trail.map(function (data) { return data.target; }));
    if (seen.some(function (prior) { return matchesSource(target, prior); })) return "Already in this trail — use the return path above.";
    if (trail.length >= 10) return "10-step limit reached. Return to an earlier step to explore another source.";
    return "";
  }
  function valueText(observation) {
    if (!observation || observation.status !== "observed") return "Not available";
    var value = observation.formula || observation.value;
    if (value === null || value === "") return "(blank)";
    return typeof value === "string" ? value : JSON.stringify(value);
  }
  function formatText(observation) {
    if (observation && observation.status === "not_inspected") return "Not inspected at this revision";
    if (!observation || observation.status !== "observed") return "Not available";
    var vf = observation.value;
    var parts = [vf.valueFormatType || "Type not provided"];
    if (vf.precision) parts.push(vf.precision.auto ? "automatic precision" : vf.precision.value + " decimals");
    if (vf.enteredIn) parts.push("entered in " + vf.enteredIn);
    if (vf.shownIn) parts.push("shown in " + vf.shownIn);
    return parts.join(" · ");
  }

  function render(body, context, state, inspect, follow, back, documentActions) {
    var hadFocus = body.contains(body.getRootNode().activeElement);
    var trail = state.trail || [], tracing = trail.length > 0;
    function el(tag, cls, text) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      if (text != null) e.textContent = text;
      return e;
    }
    var page = el("section", "wi-inspector");
    page.setAttribute("aria-label", context.document ? "Document table inspector" : "Selected cell inspector");
    page.tabIndex = -1;
    var head = el("div", "wi-heading");
    head.appendChild(el("span", "wi-eyebrow", tracing ? "SOURCE EVIDENCE" : context.document ? "DOCUMENT TABLE" : "SELECTED CELL"));
    head.appendChild(el("span", "wi-readonly", "Read-only"));
    page.appendChild(head);
    if (context.document && !context.target) {
      page.appendChild(el("h2", "wi-doc-heading", "Inspect a report table"));
      page.appendChild(el("p", "wi-description", "Choose a table in this section and enter a cell address. Wingman does not detect or move your selection in the document."));
      var catalog = state.catalog;
      var load = el("button", "wm-btn wi-doc-load" + (catalog ? "" : " primary"), state.loading ? "Reading tables…" : catalog ? "Reload tables" : "Read section tables");
      load.type = "button"; load.disabled = !!state.loading; load.onclick = documentActions.load;
      page.appendChild(load);
      var status = el("p", "wi-description", state.error || (state.loading ? "Reading table identities only. No document changes." : ""));
      status.setAttribute("role", "status"); page.appendChild(status);
      if (state.loading) {
        var stop = el("button", "wm-btn wi-doc-change", "Stop waiting");
        stop.onclick = function () { documentActions.choose(null); }; page.appendChild(stop);
      }
      if (catalog) {
        page.appendChild(el("p", "wi-sheet", catalog.sectionName || "Current section"));
        page.appendChild(el("p", "wi-description", "Read at " + new Date(catalog.observedAt).toLocaleTimeString() + ". Reload tables after edits."));
        if (!catalog.tables.length) page.appendChild(el("p", "wi-description", "No body tables in this section. Header, footer and inline text inspection are not supported."));
        else {
          var form = el("form", "wi-doc-form");
          var tableLabel = el("label", null, "Table"), select = el("select", "wi-doc-table");
          select.required = true;
          var placeholder = el("option", null, "Choose a table"); placeholder.value = ""; select.appendChild(placeholder);
          catalog.tables.forEach(function (table, index) {
            var option = el("option", null, (index + 1) + ". " + table.name); option.value = table.tableId; select.appendChild(option);
          });
          tableLabel.appendChild(select); form.appendChild(tableLabel);
          var cellLabel = el("label", null, "Cell address"), input = el("input", "wi-doc-address");
          input.type = "text"; input.required = true; input.placeholder = "e.g. B3";
          input.pattern = "[A-Za-z]{1,3}[1-9][0-9]{0,6}"; input.maxLength = 10;
          input.autocomplete = "off"; input.spellcheck = false;
          cellLabel.appendChild(input); form.appendChild(cellLabel);
          var submit = el("button", "wm-btn primary", "Inspect cell & direct sources"); submit.type = "submit"; form.appendChild(submit);
          form.onsubmit = function (event) {
            event.preventDefault();
            documentActions.choose({tableId: select.value, revision: catalog.revision, addr: input.value.toUpperCase()});
          };
          page.appendChild(form);
        }
        var identity = el("details", "wi-details");
        identity.appendChild(el("summary", null, "Context & revision"));
        var fields = el("dl", "wi-evidence");
        row(fields, "Document ID", context.document.documentId, true);
        row(fields, "Section ID", context.document.sectionId, true);
        row(fields, "Recorded revision", catalog.revision, true);
        identity.appendChild(fields);
        page.appendChild(identity);
      }
      body.replaceChildren(page);
      if (hadFocus) page.focus();
      return;
    }
    if (!context.target) {
      page.appendChild(el("h2", "wi-address", "Start with one cell"));
      page.appendChild(el("p", "wi-description", context.message));
      body.replaceChildren(page);
      if (hadFocus) page.focus();
      return;
    }
    var data = tracing ? trail[trail.length - 1] : state.data;
    var target = tracing ? data.target : context.target;
    if (tracing) {
      var navigation = el("nav", "wi-trail");
      navigation.setAttribute("aria-label", "Source evidence return path");
      [state.data].concat(trail).forEach(function (entry, index) {
        if (index) navigation.appendChild(el("span", "wi-trail-arrow", "→"));
        var label = index === 0 ? (context.document ? "Chosen " : "Selected ") + entry.target.addr : entry.target.addr;
        var step = el(index === trail.length ? "span" : "button", "wi-trail-step", label);
        step.title = (entry.sheetName || entry.tableName || entry.tableId || "Source") + " · " + entry.contentRevision;
        step.setAttribute("aria-label", label + " · " + step.title);
        if (index === trail.length) step.setAttribute("aria-current", "step");
        else { step.type = "button"; step.onclick = function () { back(index); }; }
        navigation.appendChild(step);
      });
      page.appendChild(navigation);
      page.appendChild(el("p", "wi-origin", "Started at " + (state.data.sheetName || state.data.tableName || "Selected sheet") + " · " +
        context.target.addr + ": " + valueText(state.data.content) +
        (state.data.content.kind === "formula" ? " · result " + valueText(state.data.calculated) : "") +
        " · revision " + state.data.contentRevision));
    }
    page.appendChild(el("h2", "wi-address", target.addr));
    page.appendChild(el("p", "wi-sheet", tracing ? data.tableName || "Source table" : context.document
      ? data && data.tableName || "Chosen document table" : data && data.sheetName ? data.sheetName : "Sheet " + target.sheetId));
    if (context.document) {
      page.appendChild(el("p", "wi-description", "Explicit table-cell choice, not the document's native selection. Recorded revision: " + context.target.revision + "."));
      var change = el("button", "wm-btn wi-doc-change", state.loading ? "Stop waiting / change cell" : "Choose another table cell");
      change.type = "button"; change.onclick = function () { documentActions.choose(null); }; page.appendChild(change);
    }
    if (tracing) page.appendChild(el("p", "wi-description", "Recorded revision: " + target.revision + ". Your Workiva selection has not moved."));
    var button = el("button", "wm-btn primary wi-inspect", tracing ? context.document ? "Return to chosen cell" : "Return to selected cell"
      : state.loading ? "Reading cell…" : context.document ? "Reread chosen revision" : "Inspect selected cell");
    button.type = "button";
    button.disabled = !!state.loading;
    button.onclick = tracing ? function () { back(0); } : inspect;
    page.appendChild(button);
    var feedback = el("p", "wi-description");
    feedback.setAttribute("role", "status");
    feedback.textContent = state.error || (state.loading ? (state.sources
      ? "Refreshing " + target.addr + " and reading direct sources. Up to 100 cells / 10 ranges; no changes."
      : "Reading " + target.addr + " and its link metadata. No workbook scan or changes.")
      : data && data.status === "unavailable" ? "No cell evidence returned. Inspect again to retry."
      : data && data.status === "changed" ? "Cell or content revision changed while reading. Inspect again."
      : data ? "Read at " + new Date(data.observedAt).toLocaleTimeString() + (tracing
        ? ". Saved evidence, not a live update." : context.document ? ". Reload tables to read newer revisions." : ". Inspect again after edits.")
        : "See the content, result and format separately. No changes will be made.");
    page.appendChild(feedback);
    if (state.traceLoading || state.traceError) {
      var traceFeedback = el("p", "wi-warning wi-trace-status", state.traceError ||
        "Reading " + state.traceLoading.addr + " at its recorded revision and up to 100 direct source cells / 10 ranges…");
      traceFeedback.setAttribute("role", "status");
      page.appendChild(traceFeedback);
      if (state.traceLoading) {
        var cancel = el("button", "wm-btn wi-trace-cancel", "Stop waiting");
        cancel.type = "button";
        cancel.onclick = function () { back(trail.length); };
        page.appendChild(cancel);
      }
    }
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
          raw_value: "Workiva returns this raw content as text. That does not establish its numeric type or whether it should be a formula.",
          linked_value: "Workiva identifies a cell-level linked value. Follow available source cells below, one step at a time.",
        };
        var explanation = explanations[data.content && data.content.kind];
        if (explanation === undefined) explanation = "Stored content could not be classified. No hardcode or formula judgment is made.";
        if (explanation) page.appendChild(el("p", "wi-description", explanation));
      }
      if (data.content || data.calculated) {
        var sources = el("details", "wi-sources wi-details");
        sources.setAttribute("aria-label", "References and links");
        sources.open = tracing || !!state.sources;
        var source = data.source || {}, formula = source.formula || {}, links = source.rangeLinks || {};
        var sourceValues = source.values;
        var cellLink = source.cellLink;
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
        if (sourceValues && sourceValues.status !== "observed") linkSummary += " · source reads incomplete";
        if (cellLink && cellLink.status !== "not_present") {
          linkSummary += " · " + (cellLink.status !== "observed" ? "Cell link unavailable"
            : cellLink.linkState === "disconnected" ? "Cell link disconnected"
            : cellLink.resolution === "observed" ? "Cell link → " + cellLink.sourceCell : "Cell-link source unresolved");
        }
        sourceSummary.appendChild(el("span", "wi-source-summary", formulaSummary + " · " + linkSummary));
        sources.appendChild(sourceSummary);
        var refs = el("dl", "wi-evidence");
        if (formula.status === "text_only") {
          row(refs, "Addresses in formula", formula.references.join("\n") || "No supported static references extracted", true);
          if (formula.literalNumbers.length) row(refs, "Literal numbers", formula.literalNumbers.join(" · "), true);
          if (formula.unresolved.length) row(refs, "Not resolved", formula.unresolved.join("\n"), true);
          sources.appendChild(refs);
          sources.appendChild(el("p", "wi-description", sourceValues
            ? "Addresses extracted from formula text. Literal numbers are not automatically errors."
            : "Formula text only. Referenced cells were not read; literal numbers are not automatically errors."));
        } else {
          sources.appendChild(el("p", "wi-description", formula.status === "not_formula"
            ? "No stored formula. Workiva links are checked separately."
            : "Formula references could not be inspected."));
        }
        if (cellLink && cellLink.status !== "not_present") {
          var cellDetail = el("details", "wi-details wi-cell-link");
          cellDetail.open = cellLink.resolution !== "observed";
          cellDetail.appendChild(el("summary", null, "Cell-level link · " + (cellLink.linkState || "Unavailable")));
          if (cellLink.reason) cellDetail.appendChild(el("p", "wi-warning", cellLink.reason));
          var cellFields = el("dl", "wi-evidence");
          if (cellLink.sourceCell) row(cellFields, "Source cell", cellLink.sourceCell, true);
          if (cellLink.id) row(cellFields, "Destination link ID", cellLink.id, true);
          if (cellLink.revision) row(cellFields, "Destination link revision", cellLink.revision, true);
          if (cellLink.source) {
            row(cellFields, "Source content type", cellLink.source.type);
            if (cellLink.source.table) row(cellFields, "Source table ID", cellLink.source.table, true);
            row(cellFields, "Source anchor ID", cellLink.source.anchor, true);
            row(cellFields, "Source anchor revision", cellLink.source.revision, true);
          }
          cellDetail.appendChild(cellFields);
          sources.appendChild(cellDetail);
        }
        var canReadSources = (formula.references || []).length || (cellLink && cellLink.resolution === "observed") ||
          items.some(function (link) { return link.direction === "destination"; });
        if (canReadSources && !tracing && !context.document) {
          var sourceButton = el("button", "wm-btn wi-read-sources", sourceValues ? "Refresh source values" : "Read source values");
          sourceButton.type = "button";
          sourceButton.onclick = function () { inspect(true); };
          sources.appendChild(sourceButton);
          sources.appendChild(el("p", "wi-description", "Up to 100 cells / 10 ranges. Formula references use the selected content revision; incoming links use their recorded source revisions."));
        }
        if (sourceValues) {
          if (sourceValues.status !== "observed") sources.appendChild(el("p", "wi-warning", "Direct source reads are incomplete. Unresolved references or unavailable links remain above and below."));
          sourceValues.groups.forEach(function (group) {
            var block = el("details", "wi-details wi-source-values");
            block.open = sourceValues.groups.length === 1 || group.basis === "cell_link_revision";
            block.appendChild(el("summary", null, (group.basis === "cell_link_revision" ? "Cell-link source · " : "") +
              (group.name || "Source table") + " · " + (group.reference || "Range unresolved")));
            if (group.basis === "cell_link_revision") {
              var selected = el("p", "wi-description wi-selected-content", (tracing ? "This step " : "Selected ") + target.addr + " stored content: ");
              selected.appendChild(el("code", null, valueText(data.content)));
              block.appendChild(selected);
            }
            var revisionLabel = group.basis === "published_revision" ? "Published revision: "
              : group.basis === "cell_link_revision" ? "Source anchor revision: " : "Content revision: ";
            block.appendChild(el("p", "wi-description", revisionLabel + (group.revision || "Not available")));
            if (group.status !== "observed") {
              block.appendChild(el("p", "wi-warning", group.reason));
            } else {
              var table = el("table", "wi-values");
              table.setAttribute("aria-label", "Source cells in " + group.range);
              var tr = el("tr");
              ["Cell", "Raw content"].forEach(function (label) { var th = el("th", null, label); th.scope = "col"; tr.appendChild(th); });
              var thead = el("thead"); thead.appendChild(tr); table.appendChild(thead);
              var tbody = el("tbody");
              group.cells.forEach(function (cell) {
                var cellRow = el("tr"), address = el("th", null, cell.addr), value = el("td");
                address.scope = "row";
                cellRow.appendChild(address);
                value.appendChild(el("code", null, valueText(cell.content)));
                if (cell.content.kind === "formula") value.appendChild(el("div", "wi-source-result", "Formula result: " + valueText(cell.calculated)));
                if (follow) {
                  var next = { tableId: group.tableId, revision: group.revision, addr: cell.addr };
                  var blocked = traceBlock(state.data, trail, next);
                  var followButton = el("button", "wm-btn wi-follow", "Inspect " + cell.addr + " source");
                  followButton.type = "button";
                  followButton.disabled = !!state.traceLoading || !!blocked;
                  followButton.onclick = function () { follow(next); };
                  value.appendChild(followButton);
                  if (blocked) value.appendChild(el("p", "wi-description wi-trace-block", blocked));
                }
                cellRow.appendChild(value);
                tbody.appendChild(cellRow);
              });
              table.appendChild(tbody); block.appendChild(table);
            }
            var identity = el("dl", "wi-evidence");
            row(identity, "Source table ID", group.tableId || "Not available", true);
            block.appendChild(identity);
            sources.appendChild(block);
          });
          sources.appendChild(el("p", "wi-description", "Inspect a source to read that cell and its direct evidence at the recorded revision. Up to 10 steps per trail; return without rereading. Raw values are not presentation-formatted or reconciled."));
        } else if (data.sourceValuesRequested) {
          sources.appendChild(el("p", "wi-warning", "Source values were not retained because the selected content could not be rechecked. Inspect again."));
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
              ? "Range metadata read at the reported revision"
              : "Not available at the reported revision; no latest-revision substitute");
          } else {
            row(fields, "Last published revision", link.revision || "Not provided", true);
          }
          linkDetail.appendChild(fields);
          sources.appendChild(linkDetail);
        });
        sources.appendChild(el("p", "wi-description", "Only requested steps are traced. Inline text links and the full source chain are not inspected. Connected does not mean a report is correct or up to date."));
        page.appendChild(sources);
      }
      (data.warnings || []).forEach(function (warning) {
        page.appendChild(el("p", "wi-warning", warning));
      });
      var detail = el("details", "wi-details");
      detail.appendChild(el("summary", null, "Context & raw format"));
      var metadata = el("dl", "wi-evidence");
      if (tracing) {
        row(metadata, "Source table ID", target.tableId, true);
        if (data.location && data.location.status === "observed") {
          row(metadata, "Workbook ID", data.location.spreadsheetId, true);
          row(metadata, "Sheet ID", data.location.sheetId, true);
          row(metadata, "Source workbook location", "Matched at the recorded revision. Workiva selection has not moved.");
        } else row(metadata, "Source workbook location", "Not established; no workbook navigation offered");
      } else if (context.document) {
        row(metadata, "Page workspace", target.workspaceId);
        row(metadata, "Document ID", target.documentId, true);
        row(metadata, "Section ID", target.sectionId, true);
        row(metadata, "Table ID", target.tableId, true);
      } else {
        row(metadata, "Page workspace", target.workspaceId || "Not supplied by this page");
        row(metadata, "Workbook ID", target.spreadsheetId, true);
        row(metadata, "Sheet ID", target.sheetId, true);
        row(metadata, "Metadata revision", data.revision || "Not provided; not a pinned snapshot", true);
      }
      row(metadata, "Content revision", data.contentRevision || "Not available", true);
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
    if (hadFocus) (state.traceLoading ? cancel : tracing || button.disabled ? page : sourceButton && state.sources ? sourceButton : button).focus();
  }

  var styles = ".wi-inspector{padding:16px;overflow-wrap:anywhere}.wi-inspector *{box-sizing:border-box}" +
    ".wi-trail{display:flex;align-items:center;flex-wrap:wrap;gap:4px;margin-top:10px}.wi-trail-step{min-height:40px;display:inline-flex;align-items:center;padding:6px 8px;border:1px solid var(--border-soft);border-radius:4px;background:transparent;color:var(--accent-text);font:inherit}.wi-trail-step[aria-current]{color:var(--muted)}button.wi-trail-step{cursor:pointer}.wi-trail-arrow{color:var(--muted)}.wi-origin{font-size:12px;line-height:1.5;border-left:2px solid var(--border-soft);padding-left:10px;color:var(--muted)}.wi-follow{display:block;min-height:40px;margin-top:8px;width:100%;white-space:normal}.wi-follow:disabled{opacity:.65;cursor:default}.wi-trace-cancel{min-height:40px;width:100%}.wi-trail-step:focus-visible{outline:2px solid var(--accent);outline-offset:2px}" +
    ".wi-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.wi-eyebrow{font-size:10px;letter-spacing:.09em;color:var(--muted);font-weight:600}.wi-readonly{font-size:11px;color:var(--muted)}" +
    ".wi-address{font-size:32px;line-height:1.15;font-weight:500;letter-spacing:-.04em;margin:10px 0 4px}.wi-sheet{margin:0 0 14px;color:var(--muted)}" +
    ".wi-inspect{min-height:40px;width:100%}.wi-inspect:disabled{opacity:.65;cursor:wait}.wi-description{color:var(--muted);font-size:12px;line-height:1.55;margin:10px 0 12px}" +
    ".wi-doc-heading{font-size:22px;line-height:1.25;font-weight:500}.wi-doc-form{display:grid;gap:14px}.wi-doc-form label{display:grid;gap:6px;font-size:12px;color:var(--muted)}.wi-doc-form input,.wi-doc-form select{width:100%;min-width:0;min-height:40px;border:1px solid var(--border-soft);border-radius:4px;background:var(--bg);color:var(--fg);font:inherit;padding:8px}.wi-doc-form button,.wi-doc-load,.wi-doc-change{width:100%;min-height:40px}.wi-doc-change{margin-bottom:8px}" +
    ".wi-evidence,.wi-scope{margin:0}.wi-row{padding:5px 0;border-top:1px solid var(--border-soft)}.wi-row dt{font-size:11px;color:var(--muted);margin-bottom:4px}.wi-row dd{margin:0;font-size:13px}.wi-row code{font:12px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}" +
    ".wi-warning{font-size:12px;line-height:1.55;color:var(--muted);border-left:2px solid var(--warn);padding-left:10px;margin:12px 0}.wi-details{margin:8px 0 0}.wi-details summary{min-height:40px;cursor:pointer;display:list-item;padding:10px 0;color:var(--accent-text)}" +
    ".wi-sources{margin:8px 0;border-top:1px solid var(--border-soft);border-bottom:1px solid var(--border-soft)}.wi-sources>summary{font-size:13px;line-height:1.5}.wi-source-summary{display:block;font-size:11px;color:var(--muted);margin-top:4px}.wi-link{border-top:1px solid var(--border-soft)}.wi-link summary{font-size:12px;line-height:1.5}" +
    ".wi-read-sources{min-height:40px;width:100%}.wi-values{border-collapse:collapse;width:100%;table-layout:fixed;margin-bottom:12px}.wi-values th,.wi-values td{border-top:1px solid var(--border-soft);padding:7px 4px;text-align:left;vertical-align:top;font-size:12px}.wi-values th:first-child{width:54px}.wi-values th{font-weight:500;color:var(--muted)}.wi-values code{white-space:pre-wrap;font:12px/1.5 ui-monospace,monospace}.wi-source-result{color:var(--muted);margin-top:4px}.wi-source-values{border-top:1px solid var(--border-soft)}" +
    ".wi-details summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}.wi-scope .wi-row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:6px 0}.wi-scope dt{margin:0}.wi-scope dd{font-size:12px;color:var(--muted)}" +
    ".wm-panel.wi-mode .wm-tab-actions,.wm-panel.wi-mode .wm-ctx,.wm-panel.wi-mode .wm-operator-warnings,.wm-panel.wi-mode .wm-thermo,.wm-panel.wi-mode .wm-preset{display:none!important}" +
    ".wm-panel.wi-mode{max-width:calc(100vw - 28px)}.wm-panel.wi-mode .wm-tab{min-height:40px}.wm-panel.wi-mode .wm-head .wm-x{min-height:32px;min-width:32px}.wm-panel.wi-mode .wm-reset-size,.wm-panel.wi-mode .wm-wide-toggle{display:none}";
  var api = { selection: selection, key: key, matches: matches, matchesSource: matchesSource, traceBlock: traceBlock,
    valueText: valueText, formatText: formatText, render: render, styles: styles };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WingmanInspector = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

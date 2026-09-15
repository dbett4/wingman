/* Demo-only Chrome messaging adapter. The shipped extension never loads this file. */
(function () {
  "use strict";
  var listeners = [], state = null, sheetId = "de01", selected = "B2";
  var storage = { wmTheme: "light" };
  var busy = 0;
  var base = "/spreadsheet/de00/sheet/";
  // Hash keeps direct reloads on the static root while using the real panel's URL parser.
  history.replaceState(null, "", "#" + base + sheetId);

  function node(tag, text, cls) {
    var n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  function message(text) { document.getElementById("demo-status").textContent = text; }
  function api(path, opts) {
    opts = opts || {};
    return fetch(path, Object.assign({}, opts, { headers: Object.assign({}, opts.headers, { "X-Wingman-Demo": "1" }) }))
      .then(async function (r) {
        var data = await r.json();
        if (!r.ok) throw new Error(data.error || "Demo request failed");
        return data;
      });
  }
  function setBusy(delta) {
    busy += delta;
    ["reset", "failure", "report"].forEach(function (id) { document.getElementById(id).disabled = busy > 0; });
    document.querySelectorAll("#sheets button").forEach(function (button) { button.disabled = busy > 0; });
  }
  function sheet() { return state.sheets.find(function (s) { return s.id === sheetId; }); }
  function select(addr, focus) {
    selected = addr;
    document.getElementById("address").textContent = addr;
    var m = /^([A-Z]+)(\d+)$/.exec(addr);
    if (!m) return false;
    var col = 0;
    for (var i = 0; i < m[1].length; i++) col = col * 26 + m[1].charCodeAt(i) - 64;
    var cell = (sheet().cells[Number(m[2]) - 1] || [])[col - 1];
    if (!cell) return false;
    document.getElementById("formula").textContent = sheet().formulas[addr] || String(cell.value);
    document.querySelectorAll("#grid td").forEach(function (td) { td.classList.toggle("selected", td.dataset.addr === addr); });
    var button = document.querySelector('#grid [data-addr="' + addr + '"] button');
    if (focus && button) { button.focus(); button.scrollIntoView({ block: "nearest", inline: "nearest" }); }
    return true;
  }
  function display(cell) {
    var value = cell.calculatedValue;
    var vf = cell.effectiveFormats.valueFormat || {};
    if (typeof value !== "number") return String(value);
    if (vf.valueFormatType === "PERIOD") return String(value);
    return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }
  function render() {
    var grid = document.getElementById("grid");
    grid.replaceChildren(node("caption", sheet().name + " · fictional data"));
    var head = node("thead"), row = node("tr");
    ["", "A", "B", "C"].slice(0, sheet().cells[0].length + 1).forEach(function (v) { row.appendChild(node("th", v)); });
    head.appendChild(row); grid.appendChild(head);
    var body = node("tbody");
    sheet().cells.forEach(function (cells, ri) {
      var tr = node("tr");
      var number = node("th", ri + 1); number.scope = "row"; tr.appendChild(number);
      cells.forEach(function (cell, ci) {
        var addr = String.fromCharCode(65 + ci) + (ri + 1);
        var td = node("td", null, typeof cell.value === "number" ? "numeric" : "");
        td.dataset.addr = addr;
        td.style.color = cell.effectiveFormats.textFormat.fontColor;
        var btn = node("button", display(cell));
        btn.type = "button";
        btn.setAttribute("aria-label", addr + ": " + display(cell));
        btn.onclick = function () { select(addr, false); };
        td.appendChild(btn); tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    grid.appendChild(body);
    var tabs = document.getElementById("sheets"); tabs.replaceChildren();
    state.sheets.forEach(function (s) {
      var btn = node("button", s.name); btn.type = "button";
      btn.disabled = busy > 0;
      if (s.id === sheetId) btn.setAttribute("aria-current", "page");
      btn.onclick = function () {
        sheetId = s.id; selected = "B2";
        history.replaceState(null, "", "#" + base + sheetId);
        render();
      };
      tabs.appendChild(btn);
    });
    select(selected, false);
    document.getElementById("request-count").textContent = state.requestCount + " simulated API requests";
    var failure = document.getElementById("failure");
    failure.setAttribute("aria-pressed", String(state.failureArmed));
    failure.textContent = state.failureArmed ? "Next write will mismatch" : "Make next write mismatch";
    var trace = document.getElementById("trace"); trace.replaceChildren();
    state.events.slice(-4).forEach(function (event) {
      var item = node("div", null, "trace-row");
      item.appendChild(node("strong", event.addr + " · " + event.field + (event.injectedMismatch ? " · injected mismatch" : " · write received") + " · " + new Date(event.at).toLocaleTimeString()));
      item.appendChild(node("code", JSON.stringify(event.before) + " → " + JSON.stringify(event.after)));
      trace.appendChild(item);
    });
  }
  function refresh() { return api("/demo/state").then(function (data) { state = data; render(); }); }
  function outcome(path, data) {
    if (path !== "/fix" && path !== "/apply") return;
    var text = (path === "/fix" ? "Preview" : "Apply") + " · " + (data.addr || "") + " · " + data.status + " · " + new Date().toLocaleTimeString();
    if (data.status === "dry-run") text += " · no write: " + JSON.stringify(data.before) + " → " + JSON.stringify(data.after);
    if (data.status === "mismatch-reverted") text += " · mismatch detected; restore requested. Inspect the simulator's writes below.";
    if (data.warning || data.reason) text += " · " + (data.warning || data.reason);
    document.getElementById("outcome").textContent = text;
  }

  window.chrome = window.chrome || {};
  window.chrome.runtime = {
    id: "wingman-isolated-demo",
    getURL: function (path) { return "/" + path; },
    onMessage: { addListener: function (fn) { listeners.push(fn); } },
    sendMessage: function (msg, cb) {
      if (msg.type === "WM_GOTO") { cb({ ok: select(msg.addr, true) }); return; }
      if (msg.type !== "WM_API") { cb({ ok: false, error: "Vision and browser automation are not simulated." }); return; }
      setBusy(1);
      api(msg.path, msg.opts).then(function (data) {
        outcome(msg.path, data);
        cb({ ok: true, data: data });
        return refresh().catch(function (err) { message(err.message); });
      }, function (err) {
        cb({ ok: false, data: { error: err.message } });
        message(err.message);
      }).finally(function () { setBusy(-1); });
    },
  };
  window.chrome.storage = { local: {
    get: function (_keys, cb) { cb(storage); },
    set: function (obj, cb) { Object.assign(storage, obj); if (cb) cb(); },
  } };
  function panelRoot() { return document.getElementById("__wk_wingman__").shadowRoot; }
  function togglePanel() { listeners.forEach(function (fn) { fn({ type: "WM_TOGGLE" }, {}, function () {}); }); }
  function action(id, path, after) {
    document.getElementById(id).onclick = function () {
      setBusy(1);
      api(path, { method: "POST", body: "{}" }).then(function (data) {
        state = data; render(); after();
      }).catch(function (err) { message(err.message); }).finally(function () { setBusy(-1); });
    };
  }
  document.addEventListener("DOMContentLoaded", function () {
    var host = document.getElementById("__wk_wingman__");
    document.getElementById("panel-slot").appendChild(host);
    // Host the unchanged extension panel in the demo layout; never ship these overrides.
    var style = node("style", ".wm-panel{width:100%!important;max-width:100%!important;height:700px!important;max-height:85vh!important}.wm-pill{width:100%}button,.wm-addr{min-height:40px}.wm-addr{display:inline-flex;align-items:center}.wm-row{cursor:pointer}button:disabled{cursor:not-allowed}");
    panelRoot().appendChild(style);
    // Keep unavailable controls visible and explicitly disabled in this demo only.
    new MutationObserver(function () {
      panelRoot().querySelectorAll('.wm-tab[data-tab="checks"], .wm-btn.toggle').forEach(function (button) {
        if (!button.disabled) {
          button.disabled = true;
          button.title = "Not simulated. Requires the live Workiva integration.";
        }
      });
    }).observe(panelRoot(), { childList: true, subtree: true });
    document.getElementById("open-panel").onclick = togglePanel;
    action("failure", "/demo/failure", function () { message("Armed. Preview a safe fix, then Apply. Only the next write will mismatch; its restore is allowed to succeed."); });
    action("reset", "/demo/reset", function () { location.reload(); });
    document.getElementById("report").onclick = function () {
      setBusy(1);
      api("/api/review-packet?spreadsheetId=de00").then(function (data) {
        var blob = new Blob(["SIMULATION — fictional Riverton workbook; not audit evidence.\n\n" + data.markdown], { type: "text/markdown" });
        var url = URL.createObjectURL(blob), link = node("a");
        link.href = url; link.download = "wingman-riverton-demo-review.md";
        document.body.appendChild(link); link.click(); link.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        message("Downloaded a fresh, scan-derived review packet. Unresolved findings still need human review.");
        return refresh();
      }).catch(function (err) { message(err.message); }).finally(function () { setBusy(-1); });
    };
    refresh().then(function () {
      message("Your fictional workbook is ready. Reset affects only this browser session.");
      togglePanel();
    }).catch(function (err) { message(err.message); });
  });
})();

/* Read-only connection evidence. Never handles credentials or reads a workbook. */
(function (root) {
  "use strict";

  function describe(state, demo) {
    var code = "unchecked", error = state.error, data = state.data;
    if (state.loading) code = "checking";
    else if (state.cancelled) code = "cancelled";
    else if (error) code = error.reloaded ? "reloaded" : error.configError ? "setup"
      : error.timeout ? "timeout" : error.offline ? "offline"
      : error.status === 401 || error.status === 403 ? "denied" : "failed";
    else if ("data" in state) {
      if (!data) code = "incompatible";
      else if (demo && data.simulation === true && data.service === "wingman-demo") code = "demo";
      else if (data.service !== "wingman" || data.protocol !== 1 || data.readOnly !== true ||
          data.authorization !== "accepted" || data.workivaAccess !== "not_tested" || data.simulation ||
          ["present", "missing"].indexOf(data.workivaCredentials) < 0) code = "incompatible";
      else code = data.workivaCredentials === "present" ? "connected" : "credentials";
    }
    var copy = {
      unchecked: ["Not checked", "Check the extension-to-service connection. This does not read a workbook or test Workiva access."],
      checking: ["Checking connection…", "Waiting for the service. No workbook is being read. You can stop waiting at any time."],
      cancelled: ["Check stopped", "Stopped waiting. Any late response will be ignored. No workbook action was sent."],
      setup: ["Extension setup needed", "This extension has no service token. Install the paired extension configuration from your approved setup, then reload the extension and this Workiva tab. Do not paste Workiva credentials here."],
      offline: ["Service unreachable", "The configured connection could not be reached. Check the approved connection on this device and that the backend is running. Retry when the route is available."],
      timeout: ["Connection timed out", "The service did not respond within 8 seconds. Check the connection route and retry. No workbook action was sent."],
      denied: ["Service access rejected", "The service rejected this extension's request. Verify that the installed extension configuration is paired with the intended backend. This is not a Workiva workbook permission result."],
      failed: ["Connection check failed", "The service check did not complete. Retry; if it persists, copy the redacted diagnostics for your operator."],
      incompatible: ["Service update needed", "The response does not match this extension's connection protocol. Verify the backend and extension are a compatible pair. No access claim can be made."],
      reloaded: ["Extension reloaded", "Reload this Workiva tab, then reopen Wingman. A response from the old extension cannot be used."],
      credentials: ["Workiva setup incomplete", "The extension reached Wingman and its service token was accepted. Workiva credentials are missing on the backend; complete the approved backend setup, then check again."],
      connected: ["Service connected", "The service accepted this extension's token. Workiva credentials are configured, but their validity and access to this workbook have not been tested."],
      demo: ["Demo connection", "The fictional demo responded. Extension authorization and live Workiva access are not tested in this simulation."],
    }[code];
    return { code: code, title: copy[0], message: copy[1] };
  }

  function facts(state, demo) {
    var code = describe(state, demo).code;
    var reached = code === "connected" || code === "credentials";
    var rows = [
      ["Service authorization", reached ? "Accepted" : code === "demo" ? "Simulated, not tested" : "Not established"],
      ["Workiva credentials", code === "connected" ? "Configured, not validated" : code === "credentials" ? "Missing on backend" : "Not checked"],
      ["Workbook access", "Not tested"],
      ["Workbook changes", "None — connection check only"],
    ];
    if (reached && state.data.serviceMode === "read-only") rows.unshift(["Service mode", "Read-only — repairs disabled"]);
    return rows;
  }

  function diagnostics(state, demo) {
    return ["Wingman connection check", "Result: " + describe(state, demo).title,
      "Checked at: " + (state.checkedAt || "Not checked"),
      "Route: " + (demo ? "Fictional same-origin demo" : "http://127.0.0.1:8770 (backend location not established)")]
      .concat(facts(state, demo).map(function (row) { return row.join(": "); }))
      .concat(["No credentials, workbook identifiers, cell content or upstream error bodies included."]).join("\n");
  }

  function render(body, state, demo, check, cancel, copy) {
    var hadFocus = body.contains(body.getRootNode().activeElement);
    function el(tag, cls, text) {
      var element = document.createElement(tag);
      if (cls) element.className = cls;
      if (text != null) element.textContent = text;
      return element;
    }
    var result = describe(state, demo);
    var page = el("section", "wc-page"); page.tabIndex = -1;
    var heading = el("h2", null, "Connection"); heading.id = "wc-heading";
    page.appendChild(heading);
    page.appendChild(el("p", "wc-readonly", "Read-only diagnostic · no workbook access"));
    var status = el("div", "wc-status"); status.setAttribute("role", "status");
    status.appendChild(el("h3", null, result.title));
    status.appendChild(el("p", null, result.message));
    page.appendChild(status);
    var button = el("button", "wm-btn primary wc-check", state.loading ? "Stop waiting" : "Check connection");
    button.type = "button";
    button.disabled = result.code === "reloaded";
    button.onclick = state.loading ? cancel : check;
    page.appendChild(button);
    if (state.checkedAt) page.appendChild(el("p", "wc-time", "Checked at " + new Date(state.checkedAt).toLocaleString() + ". Check again after setup changes."));
    var evidence = el("dl", "wc-facts");
    facts(state, demo).forEach(function (row) {
      var item = el("div"); item.appendChild(el("dt", null, row[0])); item.appendChild(el("dd", null, row[1])); evidence.appendChild(item);
    });
    page.appendChild(evidence);
    var detail = el("details", "wc-route");
    detail.appendChild(el("summary", null, "Connection route & setup"));
    detail.appendChild(el("p", null, demo ? "This demo uses a fictional service on the same origin. It needs no Workiva credentials."
      : "This build connects to http://127.0.0.1:8770. That address alone does not identify where the backend runs."));
    if (!demo) {
      detail.appendChild(el("p", null, "For the VPS setup, an approved encrypted forward can connect this device to the backend without running a second service here. Remote setup has not been validated by this check."));
      var steps = el("ol");
      ["Use the approved backend and connection route for this installation.",
        "Load the paired extension package. Workiva credentials remain on the backend.",
        "Reload the extension and Workiva tab after changing setup, then check again."]
        .forEach(function (text) { steps.appendChild(el("li", null, text)); });
      detail.appendChild(steps);
    }
    page.appendChild(detail);
    var copyButton = el("button", "wm-btn wc-copy", "Copy diagnostics"); copyButton.type = "button";
    copyButton.disabled = !!state.loading;
    copyButton.onclick = function () { copy(diagnostics(state, demo), copyButton); };
    page.appendChild(copyButton);
    page.appendChild(el("p", "wc-note", "Diagnostics exclude credentials, workbook identifiers and cell content."));
    body.replaceChildren(page);
    if (hadFocus) (button.disabled ? page : button).focus();
  }

  var styles = ".wc-toggle{display:block;width:100%;min-height:40px;border:0;border-top:1px solid var(--border-soft);border-bottom:1px solid var(--border-soft);border-radius:0;text-align:left;white-space:normal}" +
    ".wc-page{padding:16px;overflow-wrap:anywhere}.wc-page h2{font-size:22px;font-weight:500;margin:0 0 6px}.wc-page h3{font-size:15px;font-weight:500;margin:0 0 8px}.wc-page p,.wc-page li{font-size:12px;line-height:1.55;color:var(--muted)}.wc-readonly,.wc-time,.wc-note{margin:6px 0 14px}.wc-status{margin:20px 0 14px}.wc-status p{margin:0}" +
    ".wc-time{font-variant-numeric:tabular-nums}.wc-check{width:100%;min-height:40px}.wc-facts{margin:18px 0}.wc-facts>div{padding:9px 0;border-top:1px solid var(--border-soft)}.wc-facts dt{font-size:11px;color:var(--muted);margin-bottom:4px}.wc-facts dd{margin:0;font-size:13px}.wc-route summary{min-height:40px;box-sizing:border-box;padding:10px 0;color:var(--accent-text);cursor:pointer}.wc-route ol{padding-left:20px}.wc-route li{margin:8px 0}.wc-copy{min-height:40px;margin-top:12px}.wc-route summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}" +
    ".wm-panel.wc-mode{max-width:calc(100vw - 28px)}.wc-mode .wm-tabs,.wc-mode .wm-tab-actions,.wc-mode .wm-ctx,.wc-mode .wm-operator-warnings,.wc-mode .wm-thermo,.wc-mode .wm-preset,.wc-mode .wm-wide-toggle,.wc-mode .wm-reset-size{display:none!important}";
  var api = { describe: describe, facts: facts, diagnostics: diagnostics, render: render, styles: styles };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WingmanConnection = api;
})(typeof globalThis !== "undefined" ? globalThis : this);

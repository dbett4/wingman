/* Standalone extension page. Configuration stays in the worker, never in this DOM. */
(function () {
  "use strict";
  var body = document.getElementById("connection");
  var state = {}, generation = 0, timer;
  var style = document.createElement("style");
  style.textContent = WingmanConnection.styles;
  document.head.appendChild(style);
  document.getElementById("version").textContent = chrome.runtime.getManifest().version;

  function paint() {
    WingmanConnection.render(body, state, false, check, cancel, function (text) {
      navigator.clipboard.writeText(text).then(function () {
        document.getElementById("copy-status").textContent = "Redacted diagnostics copied.";
      }, function () {
        document.getElementById("copy-status").textContent = "Clipboard unavailable. Keep this page open to review the connection details.";
      });
    });
  }
  function cancel() {
    generation++;
    clearTimeout(timer);
    state = { cancelled: true };
    paint();
  }
  function check() {
    var request = ++generation;
    clearTimeout(timer);
    state = { loading: true };
    document.getElementById("copy-status").textContent = "";
    paint();
    function finish(result) {
      if (request !== generation) return;
      generation++;
      clearTimeout(timer);
      state = Object.assign(result, { checkedAt: new Date().toISOString() });
      paint();
    }
    // Bounds both the service request and a missing worker callback.
    timer = setTimeout(function () { finish({ error: { timeout: true } }); }, 8500);
    try {
      chrome.runtime.sendMessage({ type: "WM_API", path: "/api/connection", opts: { cache: "no-store" } }, function (reply) {
        if (chrome.runtime.lastError) {
          finish({ error: { reloaded: /Extension context invalidated/i.test(chrome.runtime.lastError.message) } });
          return;
        }
        if (!reply) { finish({ error: {} }); return; }
        finish(reply.ok ? { data: reply.data } : { error: {
          status: reply.status, configError: !!reply.configError,
          offline: !!reply.offline, timeout: !!reply.timeout,
        } });
      });
    } catch (error) { finish({ error: { reloaded: /Extension context invalidated/i.test(error.message) } }); }
  }
  window.addEventListener("pagehide", function () { generation++; clearTimeout(timer); });
  paint();
})();

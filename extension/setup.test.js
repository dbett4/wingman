// Exercise the setup controller's async lifetime with synthetic callbacks and clocks.
const { readFileSync } = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const connection = require("./wingman-connection.js");
const source = readFileSync(require.resolve("./setup.js"), "utf8");

function setup() {
  const callbacks = [], clocks = [], cleared = new Set(), events = {}, nodes = {};
  let screen, paints = 0;
  const runtime = {
    getManifest: () => ({ version: "0.1.0" }),
    sendMessage: (msg, cb) => {
      assert.equal(msg.type, "WM_API");
      assert.equal(msg.path, "/api/connection");
      callbacks.push(cb);
    },
  };
  vm.runInNewContext(source, {
    chrome: { runtime },
    document: {
      getElementById: id => nodes[id] ||= {},
      createElement: () => ({}), head: { appendChild() {} },
    },
    window: { addEventListener: (event, fn) => { events[event] = fn; } },
    setTimeout: (fn, ms) => { assert.equal(ms, 8500); clocks.push(fn); return clocks.length - 1; },
    clearTimeout: id => cleared.add(id),
    WingmanConnection: { styles: "", render: (_body, state, _demo, check, cancel) => {
      screen = { state, check, cancel }; paints++;
    } },
  });
  return { callbacks, clocks, cleared, events, runtime,
    get screen() { return screen; }, get paints() { return paints; },
    get code() { return connection.describe(screen.state, false).code; },
  };
}
const connected = { ok: true, data: { service: "wingman", protocol: 1, readOnly: true,
  authorization: "accepted", workivaAccess: "not_tested", workivaCredentials: "present" } };

test("a cancelled reply cannot replace a newer check or clear its deadline", () => {
  const page = setup();
  assert.equal(page.code, "unchecked");
  assert.equal(page.callbacks.length, 0);
  page.screen.check();
  page.screen.cancel();
  assert.equal(page.code, "cancelled");
  page.screen.check();
  page.callbacks[0](connected);
  assert.equal(page.code, "checking");
  assert.equal(page.cleared.has(1), false);
  page.callbacks[1]({ ok: false, status: 403, error: "private-marker" });
  assert.equal(page.code, "denied");
  assert.ok(!JSON.stringify(page.screen.state).includes("private-marker"));
  page.callbacks[0](connected);
  assert.equal(page.code, "denied");
});

test("missing callbacks time out, late success is ignored, and retry recovers", () => {
  const page = setup();
  page.screen.check();
  page.clocks[0]();
  assert.equal(page.code, "timeout");
  page.callbacks[0](connected);
  assert.equal(page.code, "timeout");
  page.screen.check();
  page.callbacks[1](connected);
  assert.equal(page.code, "connected");
  assert.match(page.screen.state.checkedAt, /^\d{4}-\d\d-\d\dT/);
});

test("leaving the page invalidates replies; lost extension context is explicit", () => {
  const page = setup();
  page.screen.check();
  page.runtime.lastError = { message: "private-marker" };
  page.callbacks[0]();
  assert.equal(page.code, "failed"); // An unavailable worker is not proof of a reload.
  assert.ok(!JSON.stringify(page.screen.state).includes("private-marker"));
  page.screen.check();
  page.runtime.lastError = { message: "Extension context invalidated. private-marker" };
  page.callbacks[1]();
  assert.equal(page.code, "reloaded");
  assert.ok(!JSON.stringify(page.screen.state).includes("private-marker"));
  page.runtime.lastError = undefined;
  page.screen.check();
  const before = page.paints;
  page.events.pagehide();
  page.callbacks[2](connected);
  assert.equal(page.paints, before);
  assert.equal(page.cleared.has(2), true);
});

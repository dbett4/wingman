// Execute the actual worker with synthetic Chrome/fetch boundaries. Never load local-config.js.
const { readFileSync } = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const source = readFileSync(require.resolve("./background.js"), "utf8");

function broker(token, fetcher) {
  let listener;
  const signal = {}, deadlines = [];
  const context = {
    WINGMAN_LOCAL_CONFIG: { token },
    importScripts: () => {},
    console,
    AbortSignal: { timeout: ms => { deadlines.push(ms); return signal; } },
    fetch: async (url, opts) => url.endsWith("/version") ? { ok: false } : fetcher(url, opts),
    chrome: {
      runtime: { onInstalled: { addListener() {} }, onMessage: { addListener: fn => { listener = fn; } } },
      action: { onClicked: { addListener() {} } },
      alarms: { create() {}, onAlarm: { addListener() {} } },
    },
  };
  vm.runInNewContext(source, context, { filename: "background.js" });
  return {
    signal, deadlines,
    request: (opts = {}) => new Promise(resolve => {
      assert.equal(listener({ type: "WM_API", path: "/api/connection", opts }, {}, resolve), true);
    }),
  };
}

test("connection uses the paired token, no-store and an eight-second abort signal", async () => {
  const data = { service: "wingman", authorization: "accepted" };
  let requested;
  const worker = broker(" fictional-pair ", async (url, opts) => {
    requested = { url, opts };
    return { ok: true, status: 200, json: async () => data };
  });
  const opts = { cache: "force-cache", headers: { "X-Wingman-Token": "untrusted", Accept: "application/json" } };
  const reply = await worker.request(opts);
  assert.equal(requested.url, "http://127.0.0.1:8770/api/connection");
  assert.equal(requested.opts.cache, "no-store");
  assert.equal(requested.opts.signal, worker.signal);
  assert.equal(requested.opts.headers["X-Wingman-Token"], "fictional-pair");
  assert.equal(requested.opts.headers.Accept, "application/json");
  assert.equal(opts.headers["X-Wingman-Token"], "untrusted");
  assert.deepEqual(worker.deadlines, [8000]);
  assert.equal(reply.ok, true);
  assert.equal(reply.status, 200);
  assert.equal(reply.data, data);
});

test("missing configuration performs no fetch", async () => {
  const worker = broker(" ", () => assert.fail("Must not fetch without configuration"));
  const reply = await worker.request();
  assert.equal(reply.ok, false);
  assert.equal(reply.configError, true);
  assert.deepEqual(worker.deadlines, []);
});

test("HTTP rejection stays distinct from offline and timeout", async () => {
  const worker = broker("fictional-pair", async () => ({ ok: false, status: 403, json: async () => ({ error: "rejected" }) }));
  const reply = await worker.request();
  assert.equal(reply.ok, false);
  assert.equal(reply.status, 403);
  assert.equal(reply.offline, undefined);
  assert.equal(reply.timeout, undefined);
});

test("timeout omits raw error details and a network failure remains offline", async () => {
  for (const name of ["TimeoutError", "TypeError"]) {
    const worker = broker("fictional-pair", async () => { throw Object.assign(new Error("sensitive-marker"), { name }); });
    const reply = await worker.request();
    assert.equal(reply.ok, false);
    assert.equal(!!reply.timeout, name === "TimeoutError");
    assert.equal(!!reply.offline, name === "TypeError");
    if (reply.timeout) assert.equal(reply.error, undefined);
  }
});

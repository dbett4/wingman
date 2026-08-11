// MV3 service worker.
importScripts("vision-capture.js");
//  (1) Toolbar click toggles the in-page Wingman panel (messages the content script).
//  (2) API broker: the in-page panel asks the worker to call the local service, so the
//      request carries the extension origin (a content-script fetch would carry the
//      Workiva page origin instead).
//  (3) Dev auto-reload: poll /version, reload the extension when the on-disk code changes.
const SERVICE = "http://127.0.0.1:8770";
const WM_TOKEN = "wm-local-1665dd6a";  // shared with the service; identifies the extension (not a real secret)

chrome.runtime.onInstalled.addListener(() => console.log("[wingman] background installed"));

chrome.action.onClicked.addListener((tab) => {
  if (tab && tab.id) chrome.tabs.sendMessage(tab.id, { type: "WM_TOGGLE" }).catch(() => {});
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg) return;
  if (msg.type === "WM_API") {
    (async () => {
      try {
        const opts = Object.assign({}, msg.opts);
        opts.headers = Object.assign({}, opts.headers, { "X-Wingman-Token": WM_TOKEN });
        const r = await fetch(SERVICE + msg.path, opts);
        const data = await r.json().catch(() => ({}));
        sendResponse({ ok: r.ok, status: r.status, data });
      } catch (e) {
        sendResponse({ ok: false, offline: true, error: String(e) });  // connection refused -> service down
      }
    })();
    return true;  // async response
  }
  if (msg.type === "WM_GOTO") {
    const tabId = sender.tab && sender.tab.id;
    (async () => {
      try {
        if (!tabId) throw new Error("no tab");
        const r = await gotoCell(tabId, msg.addr);
        sendResponse(r);
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message || e) });
      }
    })();
    return true;
  }
  if (msg.type === "WM_CAPTURE_CELLS") {
    const tabId = sender.tab && sender.tab.id;
    (async () => {
      try {
        if (!tabId) throw new Error("no tab");
        const r = await captureCellImages(tabId, msg.addrs || [], msg.limit);
        sendResponse(r);
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.message || e), cellImages: {}, errors: {} });
      }
    })();
    return true;
  }
});

// ----- Jump-to-cell via trusted input (chrome.debugger / CDP Input domain) -----
// Workiva's grid is a canvas/Dart editor that only commits a cell address from TRUSTED
// keyboard/mouse events. Content-script synthetic events can't do this, so we briefly
// attach the debugger, drive its built-in "Go To Cell" dialog with real input events, and
// detach. The dialog (vs. inline name-box edit) is used because typing can never land in a
// data cell — a misfire is a no-op, not a corrupted workbook.
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function dbg(target, method, params) {
  return new Promise((resolve, reject) => {
    chrome.debugger.sendCommand(target, method, params || {}, (res) => {
      const e = chrome.runtime.lastError;
      if (e) reject(new Error(e.message)); else resolve(res);
    });
  });
}
// Reference-count our debugger sessions per tab. A vision capture holds the session across its
// whole loop; a concurrent WM_GOTO attaches+detaches the same tab. Without refcounting, the
// goto's detach() tears down the session the capture still needs (silent partial crops). Only the
// last holder actually detaches.
const dbgRefs = new Map();  // tabId -> number of outstanding attach() holders
function attach(target) {
  return new Promise((resolve, reject) => {
    const held = dbgRefs.get(target.tabId) || 0;
    if (held > 0) { dbgRefs.set(target.tabId, held + 1); resolve(); return; }  // we already hold it
    chrome.debugger.attach(target, "1.3", () => {
      const e = chrome.runtime.lastError;
      // "Another debugger is already attached" is fine to proceed on.
      if (e && !/already attached/i.test(e.message)) { reject(new Error(e.message)); return; }
      dbgRefs.set(target.tabId, 1);
      resolve();
    });
  });
}
function detach(target) {
  return new Promise((resolve) => {
    const held = dbgRefs.get(target.tabId) || 0;
    if (held > 1) { dbgRefs.set(target.tabId, held - 1); resolve(); return; }  // another holder remains
    dbgRefs.delete(target.tabId);
    chrome.debugger.detach(target, () => { void chrome.runtime.lastError; resolve(); });
  });
}
async function evalJS(target, expression) {
  const r = await dbg(target, "Runtime.evaluate", { expression, returnByValue: true });
  if (r && r.exceptionDetails) throw new Error("eval failed");
  return r && r.result ? r.result.value : undefined;
}
async function mouseClick(target, x, y, clickCount) {
  await dbg(target, "Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await dbg(target, "Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", buttons: 1, clickCount: clickCount || 1 });
  await dbg(target, "Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", buttons: 0, clickCount: clickCount || 1 });
}
async function typeText(target, text) {
  for (const ch of text) {
    const vk = ch.toUpperCase().charCodeAt(0);
    await dbg(target, "Input.dispatchKeyEvent", { type: "keyDown", text: ch, unmodifiedText: ch, key: ch, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
    await dbg(target, "Input.dispatchKeyEvent", { type: "keyUp", key: ch, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
  }
}
async function pressKey(target, key, vk) {
  await dbg(target, "Input.dispatchKeyEvent", { type: "rawKeyDown", windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, key, code: key });
  if (key === "Enter") await dbg(target, "Input.dispatchKeyEvent", { type: "char", text: "\r", key, code: key });
  await dbg(target, "Input.dispatchKeyEvent", { type: "keyUp", windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk, key, code: key });
}
const pressEnter = (t) => pressKey(t, "Enter", 13);
const pressEscape = (t) => pressKey(t, "Escape", 27);

// Locate the name box AND prove the computed click point actually lands on it. The
// elementFromPoint guard is the safety gate: it guarantees the trusted click hits the name
// box, never a data cell — so the subsequent typing can only edit the name box (which
// navigates or no-ops), and can never type-and-commit into a cell.
const JS_NAMEBOX_HIT =
  "(()=>{var i=document.querySelector('div.dt-formula-cell-indicator');if(!i)return{found:false};" +
  "var r=i.getBoundingClientRect();var x=Math.round(r.left+r.width/2),y=Math.round(r.top+r.height/2);" +
  "var h=document.elementFromPoint(x,y);" +
  "var ok=!!h&&(h===i||i.contains(h)||(h.closest&&h.closest('.dt-formula-cell-indicator,.dt-formula-container')));" +
  "return{found:true,x:x,y:y,cell:(i.textContent||'').trim(),hittable:!!ok};})()";
const JS_CELL =
  "(()=>{var i=document.querySelector('div.dt-formula-cell-indicator');return i?(i.textContent||'').trim():null;})()";

const CAPTURE_LIMIT_DEFAULT = 80;

async function captureCellCrop(target) {
  const rect = await evalJS(target, VisionCapture.PAGE_EVAL);
  if (!rect || !rect.ok) return { ok: false, reason: (rect && rect.reason) || "no crop rect" };
  const dpr = await evalJS(target, "window.devicePixelRatio||1") || 1;
  const shot = await dbg(target, "Page.captureScreenshot", {
    format: "png",
    clip: {
      x: rect.x,
      y: rect.y,
      width: Math.max(1, rect.width),
      height: Math.max(1, rect.height),
      scale: dpr,
    },
  });
  if (!shot || !shot.data) return { ok: false, reason: "screenshot empty" };
  return {
    ok: true,
    data: shot.data,
    method: rect.method,
    confidence: rect.confidence,
  };
}

async function captureCellImages(tabId, addrs, limit) {
  const cap = Math.min(Math.max(1, limit || CAPTURE_LIMIT_DEFAULT), CAPTURE_LIMIT_DEFAULT);
  const list = [];
  const seen = new Set();
  for (const raw of addrs || []) {
    const addr = String(raw || "").trim().split(":")[0].toUpperCase().replace(/\$/g, "");
    if (!/^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(addr) || seen.has(addr)) continue;
    seen.add(addr);
    list.push(addr);
    if (list.length >= cap) break;
  }
  const cellImages = {};
  const errors = {};
  const captureMethods = { overlay: 0, "canvas-inset": 0 };
  if (!list.length) return { ok: true, cellImages, errors, captured: 0, attempted: 0, captureMethods };

  const target = { tabId };
  await attach(target);
  try {
    for (const addr of list) {
      try {
        await gotoCellOnTarget(target, addr);
        await sleep(200);
        const cur = await evalJS(target, JS_CELL);
        if ((cur || "").toUpperCase() !== addr) {
          errors[addr] = "navigation mismatch";
          continue;
        }
        const crop = await captureCellCrop(target);
        if (crop.ok) {
          cellImages[addr] = crop.data;
          if (crop.method && captureMethods[crop.method] != null) captureMethods[crop.method]++;
        } else errors[addr] = crop.reason || "capture failed";
      } catch (e) {
        errors[addr] = String(e && e.message || e);
      }
    }
  } finally {
    await detach(target);
  }
  return {
    ok: true,
    cellImages,
    errors,
    captured: Object.keys(cellImages).length,
    attempted: list.length,
    captureMethods,
  };
}

// Jump via the inline name box: click it, type the A1 address, Enter. This is the only
// path that commits with TRUSTED input (the Go To Cell dialog opens only via untrusted
// dispatch, which yields a non-functional 'zombie' dialog). Safe because of the
// elementFromPoint gate above — we never type unless the click is proven on the name box.
async function gotoCellOnTarget(target, rawAddr) {
  const addr = String(rawAddr || "").trim().split(":")[0].toUpperCase().replace(/\$/g, "");
  if (!/^[A-Z]{1,3}[1-9][0-9]{0,6}$/.test(addr)) throw new Error("invalid address: " + rawAddr);
  const nb = await evalJS(target, JS_NAMEBOX_HIT);
  if (!nb || !nb.found) throw new Error("name box not found — open a spreadsheet first");
  if ((nb.cell || "").toUpperCase() === addr) return { ok: true, landed: addr };
  if (!nb.hittable) throw new Error("name box not clickable (covered)");

  await mouseClick(target, nb.x, nb.y, 1);
  await sleep(140);
  await typeText(target, addr);
  await sleep(140);

  let landed = null;
  for (let i = 0; i < 12; i++) {
    if (i === 0 || i === 5) await pressEnter(target);
    await sleep(140);
    const cur = await evalJS(target, JS_CELL);
    if ((cur || "").toUpperCase() === addr) { landed = addr; break; }
  }
  if (landed !== addr) {
    await pressEscape(target);
    throw new Error("navigation did not land");
  }
  return { ok: true, landed };
}

async function gotoCell(tabId, rawAddr) {
  const target = { tabId };
  await attach(target);
  try {
    return await gotoCellOnTarget(target, rawAddr);
  } finally {
    await detach(target);
  }
}

async function devReloadCheck() {
  let build;
  try {
    const r = await fetch(SERVICE + "/version", { headers: { "X-Wingman-Token": WM_TOKEN } });
    if (!r.ok) return;
    build = (await r.json()).build;
  } catch (e) { return; }
  if (!build) return;
  const { wmBuild } = await chrome.storage.session.get("wmBuild");
  if (wmBuild === undefined) { await chrome.storage.session.set({ wmBuild: build }); return; }
  if (build !== wmBuild) { await chrome.storage.session.set({ wmBuild: build }); chrome.runtime.reload(); }
}
chrome.alarms.create("wingman-dev-reload", { periodInMinutes: 0.1 });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === "wingman-dev-reload") devReloadCheck(); });
devReloadCheck();

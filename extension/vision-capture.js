// Pure crop-rect selection for vision cell screenshots (no chrome APIs).
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof root !== "undefined") root.VisionCapture = api;
})(typeof self !== "undefined" ? self : this, function () {
  var CANVAS_INSET = { dx: 48, dy: 24, w: 130, h: 28 };

  function area(r) { return r.width * r.height; }

  function pickCellCropRect(doc) {
    doc = doc || (typeof document !== "undefined" ? document : null);
    if (!doc || !doc.querySelectorAll) return { ok: false, reason: "no document" };

    var canvases = Array.from(doc.querySelectorAll("canvas")).map(function (c) {
      return c.getBoundingClientRect ? c.getBoundingClientRect() : c;
    }).filter(function (r) { return r.width > 200 && r.height > 80; })
      .sort(function (a, b) { return area(b) - area(a); });

    if (!canvases.length) return { ok: false, reason: "no grid canvas" };

    var cr = canvases[0];
    var hits = [];
    doc.querySelectorAll("div,span").forEach(function (el) {
      var st = el.style || {};
      if (typeof doc.defaultView !== "undefined" && doc.defaultView.getComputedStyle) {
        st = doc.defaultView.getComputedStyle(el);
      }
      if (st.display === "none" || st.visibility === "hidden" || st.opacity === "0") return;
      var r = el.getBoundingClientRect ? el.getBoundingClientRect() : el;
      if (r.width < 20 || r.width > 480 || r.height < 14 || r.height > 90) return;
      if (r.right < cr.left || r.left > cr.right || r.bottom < cr.top || r.top > cr.bottom) return;
      var cls = (el.className || "").toString();
      if (/select|focus|cursor|active|highlight|border|cell/i.test(cls)) {
        hits.push({ x: r.left, y: r.top, w: r.width, h: r.height });
      }
    });

    if (hits.length) {
      hits.sort(function (a, b) { return (a.w * a.h) - (b.w * b.h); });
      var h = hits[0];
      return {
        ok: true,
        x: Math.round(h.x),
        y: Math.round(h.y),
        width: Math.round(Math.min(h.w, 400)),
        height: Math.round(Math.min(h.h, 80)),
        method: "overlay",
      };
    }

    return {
      ok: true,
      x: Math.round(cr.left + CANVAS_INSET.dx),
      y: Math.round(cr.top + CANVAS_INSET.dy),
      width: CANVAS_INSET.w,
      height: CANVAS_INSET.h,
      method: "canvas-inset",
      confidence: "low",
    };
  }

  var PAGE_EVAL = "(function(){"
    + pickCellCropRect.toString()
    + ";return pickCellCropRect(document);})()";

  return { pickCellCropRect: pickCellCropRect, PAGE_EVAL: PAGE_EVAL, CANVAS_INSET: CANVAS_INSET };
});

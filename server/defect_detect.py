#!/usr/bin/env python3
"""
Vision defect-detection (pixel layer) for Wingman.

Graduates the validated spike detectors into a server-local library. NumPy/Pillow
only — no model, on-device. Per ADR-0001: addresses come from the DOM/API; vision
reports defects only (richText run-level contrast the API cannot see, clipped text).

Detectors wired into the scan path (opt-in via ?vision=1 or POST cellImages):
  - contrast : WCAG 2.1 readability on rendered cell crops (< 4.5:1)
  - clipped  : ink reaches the cell right edge (overflow signal; surfaced)

Blank-cell pixel detection lives here for self-test parity but is NOT merged into
the Wingman scan — blank-linked is API-grounded in detectors.py.
"""
from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field

_VISION_DEPS_OK = True
_VISION_DEPS_ERROR = None
try:
    import numpy as np
    from PIL import Image
except ImportError as _e:
    _VISION_DEPS_OK = False
    _VISION_DEPS_ERROR = str(_e)
    np = None  # type: ignore
    Image = None  # type: ignore

AA = 4.5
_DATA_URL = re.compile(r"^data:image/[\w+.-]+;base64,", re.I)


# ---- WCAG (shared math with detectors.py) ----

def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb):
    r, g, b = (_lin(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def wcag_ratio(c1, c2):
    l1, l2 = relative_luminance(c1), relative_luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def vision_available():
    """True when NumPy + Pillow are importable."""
    return _VISION_DEPS_OK


def vision_unavailable_reason():
    return _VISION_DEPS_ERROR


# ---- shared fg/bg separation (border-ring bg, color-distance fg) ----

def _bg_fg(img):
    arr = np.asarray(img.convert("RGB")).astype(np.float32)
    ring = np.concatenate([
        arr[0:2].reshape(-1, 3), arr[-2:].reshape(-1, 3),
        arr[:, 0:2].reshape(-1, 3), arr[:, -2:].reshape(-1, 3),
    ])
    bg = np.median(ring, axis=0)
    dist = np.linalg.norm(arr - bg, axis=2)
    return arr, bg, dist


@dataclass
class PixelFinding:
    kind: str          # "contrast" | "clipped"
    severity: str      # "high" | "medium"
    detail: str


@dataclass
class CellAnalysis:
    blank: bool
    contrast: float | None = None
    findings: list = field(default_factory=list)


def analyze_cell(img, contrast_threshold=AA, clip_edge_tol=2):
    """Return CellAnalysis with pixel findings for one rendered cell crop."""
    if not _VISION_DEPS_OK:
        raise RuntimeError(f"vision deps missing: {_VISION_DEPS_ERROR}")
    arr, bg, dist = _bg_fg(img)
    if float(np.percentile(dist, 99.5)) < 12.0:
        return CellAnalysis(blank=True, findings=[])
    fg_mask = dist > 18.0
    if float(fg_mask.mean()) < 0.001:
        return CellAnalysis(blank=True, findings=[])

    out = CellAnalysis(blank=False, findings=[])
    d = dist[fg_mask]
    core = arr[fg_mask][d >= np.percentile(d, 60)]
    fg = np.median(core, axis=0)
    ratio = wcag_ratio(fg, bg)
    out.contrast = round(float(ratio), 2)
    if ratio < contrast_threshold:
        out.findings.append(PixelFinding(
            "contrast", "high", f"vision WCAG {ratio:.2f}:1 < {contrast_threshold}:1"))

    cols_with_ink = np.where(fg_mask.any(axis=0))[0]
    if cols_with_ink.size:
        right_edge = fg_mask.shape[1] - 1
        if right_edge - cols_with_ink.max() <= clip_edge_tol:
            out.findings.append(PixelFinding(
                "clipped", "medium", "ink reaches cell right edge (overflow)"))
    return out


def decode_cell_image(data):
    """
    Accept raw base64, data-URL, or bytes; return a PIL RGB image.
    Raises ValueError on bad input.
    """
    if not _VISION_DEPS_OK:
        raise RuntimeError(f"vision deps missing: {_VISION_DEPS_ERROR}")
    if isinstance(data, (bytes, bytearray)):
        raw = bytes(data)
    else:
        s = str(data).strip()
        s = _DATA_URL.sub("", s)
        raw = base64.b64decode(s, validate=True)
    img = Image.open(io.BytesIO(raw))
    return img.convert("RGB")


def _to_wingman_finding(addr, pf: PixelFinding, cell_meta=None):
    """Map a pixel finding to the detectors.py finding dict shape."""
    cell_meta = cell_meta or {}
    if pf.kind == "contrast":
        # Vision catches richText run-level contrast the sheetdata API cannot see. It has no
        # deterministic fontColor target (the API colors pass), so it is never safe-auto —
        # always surfaced/fixable:False (ADR-0002). The old safe-auto-on-plainText branch
        # contradicted that and this very comment, since target is always None here.
        return {
            "kind": "low-contrast",
            "addr": addr,
            "severity": "high" if "high" in pf.severity else "medium",
            "detail": pf.detail,
            "fixable": False,
            "fix_lane": "surfaced",
            "target": None,
            "source": "vision",
        }
    if pf.kind == "clipped":
        return {
            "kind": "clipped-text",
            "addr": addr,
            "severity": "medium",
            "detail": pf.detail,
            "fixable": False,
            "fix_lane": "surfaced",
            "target": None,
            "source": "vision",
        }
    return None


def scan_cell_images(cell_images, cells_by_addr=None, *, contrast_threshold=AA):
    """
    Run pixel detectors on {addr: image} crops. Addresses are supplied by the caller
    (DOM/API — ADR-0001); vision never infers them from pixels.

    Returns (findings, errors) where errors is {addr: reason} for decode/analyze failures.
    """
    cells_by_addr = cells_by_addr or {}
    findings, errors = [], {}
    for addr, img_data in (cell_images or {}).items():
        addr = str(addr).strip().upper()
        if not addr:
            continue
        try:
            img = decode_cell_image(img_data) if not hasattr(img_data, "convert") else img_data
            analysis = analyze_cell(img, contrast_threshold=contrast_threshold)
            if analysis.blank:
                continue
            # Distinguish an API-CONFIRMED-empty cell (skip) from one simply absent from
            # cells_by_addr (unknown -> let pixel analysis run; catching what the API was blind to
            # is the point of vision). A non-blank crop already passed the analysis.blank check above.
            meta = cells_by_addr.get(addr)
            if meta is not None and meta.get("value", "") == "" and not meta.get("formula"):
                continue  # API-confirmed empty cell — skip contrast/clip on blank content
            meta = meta or {}
            for pf in analysis.findings:
                wf = _to_wingman_finding(addr, pf, meta)
                if wf:
                    findings.append(wf)
        except Exception as e:
            errors[addr] = repr(e)
    return findings, errors


def merge_vision_findings(api_findings, vision_findings, cells_by_addr=None):
    """
    Append vision findings, deduping contrast when the API already has a fixable
    plainText low-contrast hit for the same address.
    """
    cells_by_addr = cells_by_addr or {}
    out = list(api_findings)
    api_contrast = {}
    for f in api_findings:
        if f.get("kind") == "low-contrast" and f.get("source") != "vision":
            api_contrast[f["addr"]] = f

    for vf in vision_findings:
        addr = vf["addr"]
        if vf["kind"] == "low-contrast":
            existing = api_contrast.get(addr)
            if existing and existing.get("fixable"):
                continue  # API plainText path owns this cell
            if existing and not existing.get("fixable"):
                # API surfaced richText/unknown — keep one vision row (richer pixel detail)
                out = [f for f in out if not (f["addr"] == addr and f.get("kind") == "low-contrast"
                                               and f.get("source") != "vision")]
            cell = cells_by_addr.get(addr, {})
            if cell.get("type") == "plainText" and not existing:
                # PlainText with passing API colors but failing pixels → richText-style runs
                vf = dict(vf)
                vf["detail"] = vf["detail"] + " (run-level; API colors pass)"
        out.append(vf)
    return out


def vision_merge_stats(api_findings, vision_findings, merged):
    """
    Delta stats for the vision merge layer.

    netNew counts vision findings whose (kind, addr) pair is absent from API findings
    (excluding findings already tagged source=vision). duplicates = len(vision_findings) - netNew.
    """
    api_keys = set()
    for f in api_findings or []:
        if f.get("source") == "vision":
            continue
        kind = f.get("kind")
        addr = f.get("addr")
        if kind and addr:
            api_keys.add((kind, addr))

    net_new_by_kind = {}
    net_new = 0
    for vf in vision_findings or []:
        key = (vf.get("kind"), vf.get("addr"))
        if key in api_keys:
            continue
        net_new += 1
        kind = vf.get("kind") or "unknown"
        net_new_by_kind[kind] = net_new_by_kind.get(kind, 0) + 1

    duplicates = len(vision_findings or []) - net_new
    return {
        "visionFindingCount": len(vision_findings or []),
        "mergedFindingCount": len(merged or []),
        "netNew": net_new,
        "duplicates": duplicates,
        "netNewByKind": net_new_by_kind,
    }


# ---- self-test (synthetic renders; skips when deps missing) ----

def _selftest():
    if not _VISION_DEPS_OK:
        print(f"SKIP: vision deps missing ({_VISION_DEPS_ERROR})")
        return True
    from PIL import ImageDraw, ImageFont

    font_path = "/System/Library/Fonts/Helvetica.ttc"
    try:
        ImageFont.truetype(font_path, 13)
    except OSError:
        print("SKIP: Helvetica not available for render self-test")
        return True

    def cell(text, fg, bg, w=120, h=26, size=13, align="center"):
        img = Image.new("RGB", (w, h), bg)
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(font_path, size)
        box = d.textbbox((0, 0), text, font=f)
        tw, th = box[2] - box[0], box[3] - box[1]
        x = {"center": (w - tw) // 2, "left": 3, "rightflush": w - tw - 1}[align]
        d.text((x, (h - th) // 2 - box[1]), text, fill=fg, font=f)
        return img

    BLACK = (0, 0, 0)
    WHITE = (255, 255, 255)
    CHAR = (26, 26, 46)
    MID = (136, 136, 136)
    TEAL = (0, 161, 155)
    LY = (255, 248, 192)
    cases = [
        ("black/white", cell("1,234,567", BLACK, WHITE), False, False, False),
        ("white/charcoal", cell("1,234,567", WHITE, CHAR), False, False, False),
        ("midgray/white", cell("Revenue", MID, WHITE), False, True, False),
        ("white/lightyellow", cell("Note", WHITE, LY), False, True, False),
        ("white/teal", cell("Header", WHITE, TEAL), False, True, False),
        ("blank white", cell("", BLACK, WHITE), True, False, False),
        ("clipped overflow", cell("ThisLabelIsFarTooLongToFit", BLACK, WHITE, w=90, align="left"), False, False, True),
    ]
    ok = 0
    for label, img, eb, ec, ecl in cases:
        a = analyze_cell(img)
        types = {f.kind for f in a.findings}
        good = (a.blank == eb) and (a.blank or ("contrast" in types) == ec) and (a.blank or ("clipped" in types) == ecl)
        ok += good
        print(f"  {'PASS' if good else 'FAIL'}  {label}")
    print(f"\n{ok}/{len(cases)} passed")

    # integration: scan_cell_images + merge
    imgs = {"A1": cell("Revenue", MID, WHITE), "B1": cell("LongOverflowTextHere", BLACK, WHITE, w=60, align="left")}
    meta = {"A1": {"type": "richText", "value": "Revenue"}, "B1": {"type": "plainText", "value": "LongOverflowTextHere"}}
    vf, err = scan_cell_images(imgs, meta)
    check = len(err) == 0 and len(vf) == 2
    kinds = {f["kind"] for f in vf}
    check = check and "low-contrast" in kinds and "clipped-text" in kinds
    merged = merge_vision_findings([], vf, meta)
    check = check and len(merged) == 2
    print(f"  {'PASS' if check else 'FAIL'}  scan_cell_images + merge")
    return ok == len(cases) and check


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)

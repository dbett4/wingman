#!/usr/bin/env python3
"""
Wingman API-grounded defect detectors (pure logic).

Operates on NORMALIZED cell dicts assembled by wk_client from the Workiva API
(sheetdata + cells endpoints) — NOT on pixels. Pure + deterministic so it is unit
tested without network (run: python3 server/detectors.py). The I/O that fetches
cells and applies fixes lives in wk_client.py / fixer.py.

Normalized cell shape (all keys optional except addr):
    {
      "addr": "C42",                 # A1 address
      "type": "plainText"|"richText", # top-level Workiva cell.type
      "value": "...",                # display text ("" when blank)
      "formula": "=A1+B1" | None,    # present when the cell holds a formula
      "calculatedValue": "#REF!" | "" | "123",  # sheetdata calculatedValue
      "fontColor": "#RRGGBB" | None, # sheetdata effectiveFormats.textFormat.fontColor
      "backgroundColor": "#RRGGBB" | None,
      "isLinked": bool,              # destinationLink present on the cell
      "linkCachedEmpty": bool,       # DL present but cached text empty
    }

Each detector returns a Finding dict or None:
    {"kind","addr","severity","detail","fixable","fix_lane","target"}
  fix_lane: "safe-auto" | "surfaced" (per ADR-0002)
"""

from __future__ import annotations

import re

import display_wrapper

AA = 4.5  # WCAG 2.1 AA contrast ratio for normal text
ERROR_PREFIXES = ("#REF!", "#NAME?", "#VALUE!", "#DIV/0!", "#N/A", "#NULL!", "#NUM!", "#SHEET!", "#ERROR")
# A run of 2+ spaces sitting BETWEEN two non-space chars (not the leading indent some sheets
# fake with spaces) — the only internal-whitespace shape worth flagging in a label.
_INNER_DOUBLE_SPACE = re.compile(r"\S {2,}\S")
_ADDR_COL = re.compile(r"^([A-Z]+)")


# ---- WCAG color math (ported from defect_detect.py, the validated module) ----

def _hex_to_rgb(h):
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"bad hex color: {h!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02X}" for c in rgb)


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb):
    r, g, b = (_lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def wcag_ratio(fg, bg):
    """Contrast ratio between two RGB tuples. >=4.5 passes AA for normal text."""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def compute_aa_fontcolor(fg_hex, bg_hex, target=AA):
    """
    Smallest hue-preserving adjustment of the font color that clears `target`
    against the background. Blends the original fg toward black AND toward white;
    returns whichever reaches target with the smaller change.

    Returns {"hex","ratio","direction"} or None if neither extreme reaches target
    (impossible for any real bg, but guarded).
    """
    fg = _hex_to_rgb(fg_hex)
    bg = _hex_to_rgb(bg_hex)

    def blend(a, b, t):
        return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))

    best = None
    for direction, anchor in (("darken", (0, 0, 0)), ("lighten", (255, 255, 255))):
        # binary search the minimal blend t in [0,1] toward anchor that clears target
        lo, hi = 0.0, 1.0
        if wcag_ratio(blend(fg, anchor, hi), bg) < target:
            continue  # even the extreme can't reach target in this direction
        for _ in range(24):
            mid = (lo + hi) / 2
            if wcag_ratio(blend(fg, anchor, mid), bg) >= target:
                hi = mid
            else:
                lo = mid
        cand = blend(fg, anchor, hi)
        # quantize to a real 8-bit color, then step toward the anchor until the
        # ROUNDED color actually clears target (binary search on floats can round
        # back below AA for cells sitting just under the line, e.g. 4.49:1).
        rgb = _hex_to_rgb(_rgb_to_hex(cand))
        guard = 0
        while wcag_ratio(rgb, bg) < target and guard < 300:
            rgb = tuple(rgb[i] + (1 if anchor[i] > rgb[i] else (-1 if anchor[i] < rgb[i] else 0))
                        for i in range(3))
            guard += 1
        ratio = wcag_ratio(rgb, bg)
        if ratio < target:
            continue
        change = sum(abs(rgb[i] - fg[i]) for i in range(3))
        if best is None or change < best[0]:
            best = (change, {"hex": _rgb_to_hex(rgb), "ratio": round(ratio, 2), "direction": direction})
    return best[1] if best else None


# ---- detectors ----

def detect_low_contrast(cell):
    # Workiva render defaults: a no-bg cell shows white, no-font-color text shows black (DET-001).
    # Apply BOTH so a dark fill with default black text (e.g. a teal/charcoal header missing its
    # white font) is contrast-checked, not skipped — the symmetric twin of the bg default.
    fg = cell.get("fontColor") or "#000000"
    bg = cell.get("backgroundColor") or "#FFFFFF"
    # only meaningful when the cell actually shows text
    if cell.get("value", "") == "" and not cell.get("formula"):
        return None
    try:
        ratio = wcag_ratio(_hex_to_rgb(fg), _hex_to_rgb(bg))
    except ValueError:
        return None
    if ratio >= AA:
        return None
    is_plain = cell.get("type") == "plainText"
    target = compute_aa_fontcolor(fg, bg) if is_plain else None
    return {
        "kind": "low-contrast",
        "addr": cell["addr"],
        "severity": "high" if ratio < 3.0 else "medium",
        "detail": f"{fg} on {bg} = {ratio:.2f}:1 (below AA 4.5:1)",
        "fixable": bool(is_plain and target),
        "fix_lane": "safe-auto" if (is_plain and target) else "surfaced",
        "target": target,  # {"hex","ratio","direction"} for safe-auto, else None
    }


def detect_blank_linked(cell):
    if not cell.get("isLinked"):
        return None
    blank = cell.get("linkCachedEmpty") or cell.get("value", "") == ""
    if not blank:
        return None
    return {
        "kind": "blank-linked-cell",
        "addr": cell["addr"],
        "severity": "high",
        "detail": "cell carries a link but renders blank",
        "fixable": False,
        "fix_lane": "surfaced",   # RED — re-link is structural, ADR-0002
        "target": None,
    }


def detect_broken_ref(cell):
    cv = (cell.get("calculatedValue") or "").strip()
    if cv.upper().startswith(ERROR_PREFIXES):
        return {
            "kind": "broken-ref",
            "addr": cell["addr"],
            "severity": "high",
            "detail": f"formula result is {cv.upper()}",  # normalize casing so grouping collapses (DET-003)
            "fixable": False,
            "fix_lane": "surfaced",   # judgment fix
            "target": None,
        }
    # NOTE (DET-002): "formula evaluates to blank" is handled by detect_formula_evaluates_blank
    # when formula enrich is available; broken-ref keeps error-token cells only.
    return None


def detect_formula_evaluates_blank(cell):
    """
    Formula present but calculatedValue is empty — not an error token (broken-ref handles those).
    Requires formula on the normalized cell (wk_client.enrich_cells_with_formulas).
    """
    formula = cell.get("formula")
    if not formula or not str(formula).strip().startswith("="):
        return None
    cv = (cell.get("calculatedValue") or "").strip()
    if cv.upper().startswith(ERROR_PREFIXES):
        return None
    if cv != "":
        return None
    return {
        "kind": "formula-evaluates-blank",
        "addr": cell["addr"],
        "severity": "high",
        "detail": f"formula evaluates blank ({formula})",
        "fixable": False,
        "fix_lane": "surfaced",
        "target": None,
    }


def compute_trim_label(v):
    """
    Trim leading/trailing whitespace and collapse internal runs of 2+ spaces to one.
    Returns the cleaned string, or None when the value is not a trimmable text label.
    Same conservative gates as detect_label_hygiene (letters required; no crosswalk strings).
    """
    if not isinstance(v, str) or v == "":
        return None
    if not any(ch.isalpha() for ch in v):
        return None
    if '("' in v or "->" in v:
        return None
    t = re.sub(r" {2,}", " ", v.strip())
    return t if t != v else None


def _addr_col(addr):
    m = _ADDR_COL.match((addr or "").strip().upper())
    return m.group(1) if m else None


def _parse_numeric(val):
    """Parse a sheetdata value/calculatedValue into float, or None when not numeric."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s or s == "—":
        return None
    if s.startswith("(") and ")" in s:
        inner = s[1:s.rindex(")")].replace(",", "").replace("$", "").strip()
        try:
            return -float(inner)
        except ValueError:
            return None
    clean = s.replace(",", "").replace("$", "").strip()
    if not clean or clean == "-":
        return None
    try:
        return float(clean)
    except ValueError:
        return None


def _displays_minus_prefix(display, numeric):
    """True when a negative number renders with a leading minus (not parentheses)."""
    s = (display or "").strip()
    if s.startswith("("):
        return False
    if s.startswith("-") and len(s) > 1:
        return True
    if numeric is not None and numeric < 0:
        return "(" not in s
    return False


def _format_desc(vf):
    if not vf:
        return "default, minus-prefix"
    parts = [str(vf.get("valueFormatType") or "AUTOMATIC")]
    parts.append("parens" if vf.get("useParensForNegatives") else "minus-prefix")
    if vf.get("prefix"):
        parts.append(f"prefix={vf['prefix']!r}")
    if vf.get("displayZeroAs"):
        parts.append(f"zero={vf['displayZeroAs']}")
    return ", ".join(parts)


def build_accounting_parens_format(before_vf=None):
    """
    ACCOUNTING valueFormat with parentheses for negatives (ACFR / government presentation).
    Preserves prefix, precision, and thousands separator from the cell when present.
    """
    vf = {
        "valueFormatType": "ACCOUNTING",
        "precision": {"auto": False, "value": 0},
        "showThousandsSeparator": True,
        "useParensForNegatives": True,
        "showCurrencySymbol": False,
    }
    if before_vf:
        if before_vf.get("prefix"):
            vf["prefix"] = before_vf["prefix"]
        prec = before_vf.get("precision")
        if isinstance(prec, dict):
            vf["precision"] = dict(prec)
        if before_vf.get("showThousandsSeparator") is not None:
            vf["showThousandsSeparator"] = before_vf["showThousandsSeparator"]
    return vf


def detect_negative_without_parens(cell):
    """
    Negative numeric value formatted with a minus prefix instead of ACCOUNTING parentheses.
    Column-homogeneous gate (adjust_negative_parens_lanes) downgrades mixed columns to surfaced.
    Skips richText, linked, merged, TEXT format, and non-negative values.
    """
    if cell.get("type") == "richText":
        return None
    if cell.get("isLinked"):
        return None
    if cell.get("isMerged"):
        return None
    vf = cell.get("valueFormat") or {}
    # ACCOUNTING parens conversion only makes sense for number/currency formats. Skip TEXT
    # (W21 trap) and PERCENT/SCIENTIFIC — converting a negative % or exponential to accounting
    # parens would destroy its format if calculatedValue carries the raw value (DET-004).
    if (vf.get("valueFormatType") or "").upper() in ("TEXT", "PERCENT", "SCIENTIFIC"):
        return None
    display = cell.get("calculatedValue") or cell.get("value") or ""
    num = _parse_numeric(display)
    if num is None or num >= 0:
        return None
    if vf.get("useParensForNegatives") is True:
        return None
    if not _displays_minus_prefix(display, num):
        return None
    target_vf = build_accounting_parens_format(vf)
    return {
        "kind": "negative-without-parens",
        "addr": cell["addr"],
        "severity": "medium",
        "detail": f"minus-prefix on negative ({_format_desc(vf)})",
        "fixable": True,  # may be downgraded by column gate
        "fix_lane": "safe-auto",
        "target": {"valueFormat": target_vf, "beforeFormat": _format_desc(vf),
                   "afterFormat": _format_desc(target_vf)},
    }


def adjust_negative_parens_lanes(findings, cells):
    """
    Column-homogeneous gate: safe-auto only when every negative in the column uses minus-prefix.
    Mixed columns (some parens, some minus) are surfaced for manual review.
    """
    cells = cells or []
    col_state: dict[str, dict[str, bool]] = {}
    for c in cells:
        col = _addr_col(c.get("addr"))
        if not col:
            continue
        num = _parse_numeric(c.get("calculatedValue") or c.get("value"))
        if num is None or num >= 0:
            continue
        vf = c.get("valueFormat") or {}
        st = col_state.setdefault(col, {"has_parens": False, "has_minus": False})
        if vf.get("useParensForNegatives") is True or not _displays_minus_prefix(
                c.get("calculatedValue") or c.get("value"), num):
            st["has_parens"] = True
        if vf.get("useParensForNegatives") is False or _displays_minus_prefix(
                c.get("calculatedValue") or c.get("value"), num):
            st["has_minus"] = True
    mixed = {col for col, st in col_state.items() if st["has_parens"] and st["has_minus"]}
    out = []
    for f in findings:
        if f.get("kind") != "negative-without-parens":
            out.append(f)
            continue
        col = _addr_col(f.get("addr"))
        if col in mixed:
            g = dict(f)
            g["fix_lane"] = "surfaced"
            g["fixable"] = False
            g["detail"] = g["detail"] + " (mixed column)"
            g["target"] = None
            out.append(g)
        else:
            out.append(f)
    return out


def detect_label_hygiene(cell):
    """
    Stray whitespace in a TEXT LABEL — a trailing/leading space or an internal double space
    in a cell that holds words. These are real cleanup defects (a trailing space on a heading
    breaks alignment and shows up in exports) but invisible on screen, so a reviewer misses
    them. Deliberately conservative to stay low-noise (the scan must be trustworthy, ADR-0002):
      - requires a letter, so spacer cells (a lone " "), pure numbers, and year headers
        ("2015") never fire;
      - skips stored formula / crosswalk strings — '("' is a function call and '->' is a
        crosswalk arrow (e.g. a CWUDFsStorage cell), which are data, not display labels.
    plainText labels with a trim target are safe-auto (value write + readback/revert in fixer).
    """
    v = cell.get("value", "")
    if not isinstance(v, str) or v == "":
        return None
    trimmed = compute_trim_label(v)
    if not trimmed:
        return None
    issues = []
    if v != v.lstrip():
        issues.append("leading space")
    if v != v.rstrip():
        issues.append("trailing space")
    if _INNER_DOUBLE_SPACE.search(v):
        issues.append("double space")
    is_plain = cell.get("type") == "plainText"
    return {
        "kind": "label-hygiene",
        "addr": cell["addr"],
        "severity": "low",
        "detail": " + ".join(issues),   # stable signature so identical issues group into one row
        "fixable": bool(is_plain),
        "fix_lane": "safe-auto" if is_plain else "surfaced",
        "target": {"value": trimmed},
    }


_YEAR_MIN, _YEAR_MAX = 1900, 2100
_BARE_YEAR = re.compile(r"\d{4}")


def detect_year_automatic_coercion(cell):
    """A 4-digit year stored as a number under AUTOMATIC value format is silently coerced by
    Workiva's render layer to a thousands-separated string (2025 -> '2.025' / '2,025') in document
    destination links — a client-visible defect ("August 2.025"). The canonical fix is
    valueFormatType=PERIOD, which suppresses the separator and is fully reversible with no value
    rescale (verified live: correctly-built year cells on the same sheet are already PERIOD).

    Low-noise gate: fires ONLY when the value is a bare 4-digit year in [1900, 2100] AND the value
    format is AUTOMATIC (or unset). A deliberate amount of 2025 carries a NUMBER/ACCOUNTING/etc.
    format and is left alone; PERIOD/NUMBER/etc. year cells are already correct. richText is surfaced
    (ADR-0002). See ADR-0009.
    """
    display = cell.get("calculatedValue")
    if display is None or str(display).strip() == "":
        display = cell.get("value")
    s = str(display or "").strip()
    if not _BARE_YEAR.fullmatch(s):
        return None
    yr = int(s)
    if yr < _YEAR_MIN or yr > _YEAR_MAX:
        return None
    vf = cell.get("valueFormat") or {}
    vft = (vf.get("valueFormatType") or "AUTOMATIC").upper()
    if vft not in ("AUTOMATIC", ""):
        return None  # deliberate format (NUMBER/ACCOUNTING/PERIOD/…) — not a coercion defect
    is_plain = cell.get("type") != "richText"
    return {
        "kind": "year-automatic-coercion",
        "addr": cell["addr"],
        "severity": "medium",
        "detail": f"year {yr} under AUTOMATIC format renders a thousands separator in doc links — use PERIOD",
        "fixable": bool(is_plain),
        "fix_lane": "safe-auto" if is_plain else "surfaced",
        "target": {"valueFormat": {"valueFormatType": "PERIOD"}},
    }


DETECTORS = (
    detect_low_contrast, detect_blank_linked, detect_broken_ref,
    detect_formula_evaluates_blank,
    detect_label_hygiene, detect_negative_without_parens,
    detect_year_automatic_coercion,
    display_wrapper.detect_display_wrapper,
)


def scan_cells(cells):
    """Run every detector over an iterable of normalized cells; return findings list."""
    findings = []
    for cell in cells:
        for det in DETECTORS:
            f = det(cell)
            if f:
                findings.append(f)
    findings = adjust_negative_parens_lanes(findings, cells)
    try:
        import junk_decimal as jd
        findings.extend(jd.scan_junk_decimal(cells))
    except ImportError:
        pass
    try:
        import format_lane as fl
        findings.extend(fl.scan_format_lane(cells))
    except ImportError:
        pass
    try:
        import hardcoded_value as hv
        findings.extend(hv.scan_hardcoded_values(cells))
    except ImportError:
        pass
    try:
        import formula_hygiene as fh
        findings.extend(fh.scan_formula_hygiene(cells))
    except ImportError:
        pass
    return findings


def apply_vision_layer(api_findings, cell_images, cells_by_addr):
    """
    Opt-in pixel layer (ADR-0001: addresses from caller, vision = defect detection only).
    Returns (merged_findings, vision_meta).

    Graceful fallback: missing deps, empty cell_images, or decode errors → API findings
    unchanged with vision_meta explaining why vision was skipped or partial.
    """
    meta = {"requested": bool(cell_images), "applied": False}
    if not cell_images:
        meta["skipped"] = "no cellImages supplied"
        return api_findings, meta
    try:
        import defect_detect as dd
    except ImportError:
        meta["skipped"] = "defect_detect module unavailable"
        return api_findings, meta
    if not dd.vision_available():
        meta["skipped"] = f"vision deps missing: {dd.vision_unavailable_reason()}"
        return api_findings, meta
    cells_by_addr = cells_by_addr or {}
    vision_findings, errors = dd.scan_cell_images(cell_images, cells_by_addr)
    merged = dd.merge_vision_findings(api_findings, vision_findings, cells_by_addr)
    meta["applied"] = True
    stats = dd.vision_merge_stats(api_findings, vision_findings, merged)
    meta.update(stats)
    if errors:
        meta["errors"] = errors
    return merged, meta


DISPLAY_VALUE_MAX = 40


def cell_display_value(source, *, max_len=DISPLAY_VALUE_MAX):
    """Chip label for a cell or finding: prefer calculatedValue, else value string."""
    if not source:
        return None
    cv = source.get("calculatedValue")
    if cv is not None and str(cv).strip() != "":
        text = str(cv).strip()
    else:
        v = source.get("value")
        if v is None:
            v = source.get("displayValue")
        if not isinstance(v, str) or v == "":
            return None
        text = v
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def group_findings(findings, cells_by_addr=None):
    """
    Collapse identical findings into one row per (kind, signature, lane), keeping the
    full cell list (ADR/UX decision 2026-06-15: a repeated house-style is one actionable
    line, not N lines of noise). Distinct signatures stay separate rows. Signature for
    low-contrast is the 'fg on bg' pair; for the rest it is the detail string.

    Each group includes cellValues: {A1: "Revenue"} for Lane 1 addr chips (from the
    finding or cells_by_addr lookup).
    """
    cells_by_addr = cells_by_addr or {}
    groups, order = {}, []
    for f in findings:
        sig = f["detail"].split(" = ")[0] if f["kind"] == "low-contrast" else f["detail"]
        key = (f["kind"], sig, f["fix_lane"])
        if key not in groups:
            groups[key] = {
                "kind": f["kind"], "severity": f["severity"], "signature": sig,
                "fixable": f["fixable"], "fix_lane": f["fix_lane"], "target": f.get("target"),
                "addrs": [], "cellValues": {},
            }
            order.append(key)
        addr = f["addr"]
        groups[key]["addrs"].append(addr)
        if addr not in groups[key]["cellValues"]:
            disp = cell_display_value(f) or cell_display_value(cells_by_addr.get(addr))
            if disp:
                groups[key]["cellValues"][addr] = disp
    out = []
    for key in order:
        g = groups[key]
        g["count"] = len(g["addrs"])
        out.append(g)
    return out


# ---- self-test (deterministic, no network) ----

def _selftest():
    cases = []

    def check(name, cond):
        cases.append((name, bool(cond)))

    # contrast: white on mid-gray fails AA, plain -> safe-auto with a computed target
    f = detect_low_contrast({"addr": "A1", "type": "plainText", "value": "x",
                             "fontColor": "#FFFFFF", "backgroundColor": "#7F7F7F"})
    check("white/gray flagged", f and f["kind"] == "low-contrast")
    check("white/gray ratio < AA", f and "below AA" in f["detail"])
    check("plain cell is safe-auto", f and f["fix_lane"] == "safe-auto" and f["fixable"])
    check("target clears AA", f and f["target"] and f["target"]["ratio"] >= AA)

    # same case but richText -> surfaced, no target
    f = detect_low_contrast({"addr": "A2", "type": "richText", "value": "x",
                             "fontColor": "#FFFFFF", "backgroundColor": "#7F7F7F"})
    check("richText contrast surfaced", f and f["fix_lane"] == "surfaced" and not f["fixable"])

    # passing contrast -> no finding (black on white)
    check("black/white passes", detect_low_contrast({"addr": "A3", "type": "plainText", "value": "x",
                                                      "fontColor": "#000000", "backgroundColor": "#FFFFFF"}) is None)

    # white on a brand teal (#00A19B) is a real defect (~2.4-3.1:1) per the contrast spec
    f = detect_low_contrast({"addr": "A4", "type": "plainText", "value": "x",
                             "fontColor": "#FFFFFF", "backgroundColor": "#00A19B"})
    check("white/teal flagged", f and f["kind"] == "low-contrast")

    # blank but no colors -> not a contrast finding
    check("blank no-color skipped", detect_low_contrast({"addr": "A5", "type": "plainText", "value": "",
                                                          "fontColor": None, "backgroundColor": None}) is None)

    # DET-001: white text on a NONE (default-white) background is invisible -> must be flagged
    f = detect_low_contrast({"addr": "A6", "type": "plainText", "value": "x", "fontColor": "#FFFFFF", "backgroundColor": None})
    check("white on default-white flagged", f and f["kind"] == "low-contrast")
    check("black on default-white passes", detect_low_contrast({"addr": "A7", "type": "plainText", "value": "x", "fontColor": "#000000", "backgroundColor": None}) is None)

    # DET-001 (symmetric): default-black text (no fontColor) on a DARK fill is invisible -> flagged,
    # and lightening the font toward white clears AA so it is safe-auto.
    f = detect_low_contrast({"addr": "A8", "type": "plainText", "value": "x", "fontColor": None, "backgroundColor": "#1F3A3A"})
    check("default-black on dark-fill flagged", f and f["kind"] == "low-contrast")
    check("default-black on dark-fill safe-auto", f and f["fix_lane"] == "safe-auto" and f["fixable"])
    # default-black on a light fill must NOT false-positive
    check("default-black on light-fill passes", detect_low_contrast({"addr": "A9", "type": "plainText", "value": "x", "fontColor": None, "backgroundColor": "#FFFFFF"}) is None)

    # blank linked cell
    f = detect_blank_linked({"addr": "D12", "isLinked": True, "value": "", "type": "plainText"})
    check("blank-linked flagged", f and f["kind"] == "blank-linked-cell")
    check("blank-linked is RED/surfaced", f and f["fix_lane"] == "surfaced" and not f["fixable"])
    # linked but populated -> ok
    check("populated link ok", detect_blank_linked({"addr": "D13", "isLinked": True, "value": "100"}) is None)
    # not linked -> ok
    check("unlinked blank ok", detect_blank_linked({"addr": "D14", "isLinked": False, "value": ""}) is None)

    # broken ref
    f = detect_broken_ref({"addr": "E1", "calculatedValue": "#REF!"})
    check("#REF! flagged", f and f["kind"] == "broken-ref")
    f = detect_broken_ref({"addr": "E2", "calculatedValue": "#NAME?"})
    check("#NAME? flagged", f and f["kind"] == "broken-ref")
    # DET-002: empty-calc flagged when formula enrich present; still not broken-ref
    check("empty-calc not broken-ref", detect_broken_ref({"addr": "E3", "formula": "=SUM(A1:A9)", "calculatedValue": ""}) is None)
    f = detect_formula_evaluates_blank({"addr": "E3", "formula": "=SUM(A1:A9)", "calculatedValue": ""})
    check("empty-calc flagged (DET-002)", f and f["kind"] == "formula-evaluates-blank" and not f["fixable"])
    # healthy formula
    check("good formula ok", detect_broken_ref({"addr": "E4", "formula": "=1+1", "calculatedValue": "2"}) is None)
    # DET-003: mixed-case error token normalizes in the detail so grouping collapses
    f = detect_broken_ref({"addr": "E5", "calculatedValue": "#ref!"})
    check("lowercase #ref! flagged + normalized", f and f["kind"] == "broken-ref" and "#REF!" in f["detail"])

    # label-hygiene: trailing/leading/internal-double space on real text labels
    f = detect_label_hygiene({"addr": "A1", "value": "Revenue Summary "})
    check("trailing space flagged", f and f["kind"] == "label-hygiene" and f["detail"] == "trailing space")
    f = detect_label_hygiene({"addr": "A2", "value": " Cash"})
    check("leading space flagged", f and f["detail"] == "leading space")
    f = detect_label_hygiene({"addr": "A3", "value": "Total  Revenues"})
    check("internal double space flagged", f and f["detail"] == "double space")
    check("clean label ok", detect_label_hygiene({"addr": "A4", "value": "Total Revenues"}) is None)
    check("spacer (lone space) skipped", detect_label_hygiene({"addr": "A5", "value": " "}) is None)
    check("year header text skipped", detect_label_hygiene({"addr": "A6", "value": "2015 "}) is None)  # no letter
    check("crosswalk formula string skipped",
          detect_label_hygiene({"addr": "A7", "value": ' ROUND(-cw_map("BR","MB->41000.001"),0)'}) is None)
    check("blank label skipped", detect_label_hygiene({"addr": "A8", "value": ""}) is None)
    f = detect_label_hygiene({"addr": "A9", "value": " Net position (deficit) "})  # parens but not '("'
    check("label with parens still checked", f and "leading space" in f["detail"] and "trailing space" in f["detail"])

    # plainText label trim -> safe-auto with target value
    f = detect_label_hygiene({"addr": "B1", "type": "plainText", "value": "Revenue Summary "})
    check("plain label safe-auto", f and f["fix_lane"] == "safe-auto" and f["fixable"])
    check("trim target strips trailing", f and f["target"]["value"] == "Revenue Summary")
    f = detect_label_hygiene({"addr": "B2", "type": "richText", "value": "Other  "})
    check("richText label surfaced", f and f["fix_lane"] == "surfaced" and not f["fixable"])

    # compute_trim_label
    check("trim collapses double space", compute_trim_label("Total  Revenues") == "Total Revenues")
    check("trim strips both ends", compute_trim_label(" Net ") == "Net")
    check("trim skips crosswalk", compute_trim_label(' ROUND("MB->41000")') is None)

    # negative-without-parens
    f = detect_negative_without_parens({
        "addr": "B5", "type": "plainText", "calculatedValue": "-1234",
        "valueFormat": {"valueFormatType": "NUMBER", "useParensForNegatives": False},
    })
    check("minus-prefix negative flagged", f and f["kind"] == "negative-without-parens")
    check("minus-prefix safe-auto", f and f["fix_lane"] == "safe-auto" and f["fixable"])
    check("target ACCOUNTING parens", f and f["target"]["valueFormat"]["valueFormatType"] == "ACCOUNTING"
          and f["target"]["valueFormat"]["useParensForNegatives"])
    check("parens negative ok", detect_negative_without_parens({
        "addr": "B6", "calculatedValue": "(1,234)",
        "valueFormat": {"valueFormatType": "ACCOUNTING", "useParensForNegatives": True},
    }) is None)
    check("positive skipped", detect_negative_without_parens({
        "addr": "B7", "calculatedValue": "1234",
        "valueFormat": {"valueFormatType": "NUMBER", "useParensForNegatives": False},
    }) is None)
    check("richText skipped", detect_negative_without_parens({
        "addr": "B8", "type": "richText", "calculatedValue": "-99",
        "valueFormat": {"useParensForNegatives": False},
    }) is None)
    check("TEXT format skipped", detect_negative_without_parens({
        "addr": "B9", "calculatedValue": "-99",
        "valueFormat": {"valueFormatType": "TEXT"},
    }) is None)
    # DET-004: PERCENT/SCIENTIFIC negatives must NOT be converted to ACCOUNTING parens
    check("PERCENT format skipped", detect_negative_without_parens({
        "addr": "B10", "calculatedValue": "-0.052",
        "valueFormat": {"valueFormatType": "PERCENT", "useParensForNegatives": False},
    }) is None)
    check("SCIENTIFIC format skipped", detect_negative_without_parens({
        "addr": "B11", "calculatedValue": "-1.2",
        "valueFormat": {"valueFormatType": "SCIENTIFIC", "useParensForNegatives": False},
    }) is None)
    # column gate: mixed column -> surfaced
    cells_mixed = [
        {"addr": "C1", "calculatedValue": "-100", "valueFormat": {"useParensForNegatives": False}},
        {"addr": "C2", "calculatedValue": "(200)", "valueFormat": {"useParensForNegatives": True}},
        {"addr": "C3", "calculatedValue": "-300", "valueFormat": {"useParensForNegatives": False}},
    ]
    mixed_findings = adjust_negative_parens_lanes([
        detect_negative_without_parens(cells_mixed[0]),
        detect_negative_without_parens(cells_mixed[2]),
    ], cells_mixed)
    check("mixed column surfaced", all(x["fix_lane"] == "surfaced" for x in mixed_findings))
    homog = adjust_negative_parens_lanes([
        detect_negative_without_parens({"addr": "D1", "calculatedValue": "-100",
                                      "valueFormat": {"useParensForNegatives": False}}),
    ], [{"addr": "D1", "calculatedValue": "-100", "valueFormat": {"useParensForNegatives": False}}])
    check("homogeneous column safe-auto", homog[0]["fix_lane"] == "safe-auto")

    # year-automatic-coercion (ADR-0009)
    yc = detect_year_automatic_coercion({"addr": "B6", "calculatedValue": "2025", "value": "=B5",
                                         "valueFormat": {"valueFormatType": "AUTOMATIC"}})
    check("AUTOMATIC year flagged", yc and yc["kind"] == "year-automatic-coercion")
    check("year fix is PERIOD safe-auto", yc and yc["fix_lane"] == "safe-auto"
          and yc["target"]["valueFormat"]["valueFormatType"] == "PERIOD")
    check("PERIOD year not flagged", detect_year_automatic_coercion(
        {"addr": "B5", "calculatedValue": "2025", "valueFormat": {"valueFormatType": "PERIOD"}}) is None)
    check("NUMBER 2025 amount not flagged", detect_year_automatic_coercion(
        {"addr": "C1", "calculatedValue": "2025", "valueFormat": {"valueFormatType": "NUMBER"}}) is None)
    check("out-of-range year not flagged", detect_year_automatic_coercion(
        {"addr": "C2", "calculatedValue": "1800", "valueFormat": {"valueFormatType": "AUTOMATIC"}}) is None)
    check("non-4-digit not flagged", detect_year_automatic_coercion(
        {"addr": "C3", "calculatedValue": "20255", "valueFormat": {"valueFormatType": "AUTOMATIC"}}) is None)
    check("richText year surfaced", (lambda f: f and f["fix_lane"] == "surfaced" and not f["fixable"])(
        detect_year_automatic_coercion({"addr": "C4", "type": "richText", "calculatedValue": "2025",
                                        "valueFormat": {"valueFormatType": "AUTOMATIC"}})))

    # compute_aa_fontcolor sanity: result actually clears AA
    t = compute_aa_fontcolor("#FFFFFF", "#7F7F7F")
    check("aa-compute clears target", t and wcag_ratio(_hex_to_rgb(t["hex"]), _hex_to_rgb("#7F7F7F")) >= AA)
    t = compute_aa_fontcolor("#777777", "#FFFFFF")
    check("aa-compute on light bg", t and wcag_ratio(_hex_to_rgb(t["hex"]), _hex_to_rgb("#FFFFFF")) >= AA)

    # scan_cells aggregates
    findings = scan_cells([
        {"addr": "A1", "type": "plainText", "value": "x", "fontColor": "#FFFFFF", "backgroundColor": "#7F7F7F"},
        {"addr": "D12", "isLinked": True, "value": ""},
        {"addr": "E1", "calculatedValue": "#REF!"},
    ])
    check("scan finds all 3", len(findings) == 3)

    # grouping collapses identical contrast findings, keeps distinct ones separate
    group_cells = [
        {"addr": "B3", "type": "plainText", "value": "Header A", "fontColor": "#FFFFFF", "backgroundColor": "#7F7F7F"},
        {"addr": "E3", "type": "plainText", "value": "Header B", "fontColor": "#FFFFFF", "backgroundColor": "#7F7F7F"},
        {"addr": "Z9", "isLinked": True, "value": ""},
    ]
    cells_by_addr = {c["addr"]: c for c in group_cells}
    grouped = group_findings(scan_cells(group_cells), cells_by_addr)
    check("grouping yields 2 rows", len(grouped) == 2)
    gc = next((g for g in grouped if g["kind"] == "low-contrast"), None)
    check("contrast group has 2 cells", gc and gc["count"] == 2 and len(gc["addrs"]) == 2)
    check("contrast group cellValues", gc and gc["cellValues"] == {"B3": "Header A", "E3": "Header B"})
    gl = next((g for g in grouped if g["kind"] == "blank-linked-cell"), None)
    check("blank-linked cellValues empty", gl and gl["cellValues"] == {})

    # broken-ref prefers calculatedValue for chip text
    ref_cells = [{"addr": "E1", "calculatedValue": "#REF!", "value": "=BAD()"}]
    ref_grouped = group_findings(scan_cells(ref_cells), {c["addr"]: c for c in ref_cells})
    check("broken-ref cellValues", ref_grouped[0]["cellValues"] == {"E1": "#REF!"})

    passed = sum(1 for _, ok in cases if ok)
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(cases)} passed")
    return passed == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)

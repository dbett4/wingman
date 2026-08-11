#!/usr/bin/env python3
"""
Display-wrapper formula detector (Wingman Lane A — surfaced/RED only, no auto-fix).

Flags display-mirror formula patterns observed in production ACFR workbook scans:
  - TEXT(ROUND(.../1000)) and TEXT(.../1000) scaled mirrors
  - ISNUMBER → TEXT display mirrors (with or without scale)
  - Literal numeric string formulas (="12345" style)

Pure logic — unit-tested with mocked cells. Never suggests valueFormat auto-fix on TEXT
bands (a live canary showed valueFormat writes silently no-op on string-emitting cells).
"""
from __future__ import annotations

import re
from typing import Any

# Collapse whitespace for robust matching against live formula strings.
_WS = re.compile(r"\s+")

# ="12345", ="17,066", ="(16,709)" — numeric-looking literal strings.
_LITERAL_NUMERIC = re.compile(
    r'^=\s*"[\d$,().\-\s]+"\s*$',
    re.IGNORECASE,
)

# D21 — a quoted literal that looks like a formatted number / accounting em-dash
# ("-", "(1,234)", "1,234", "$5"). Used to spot IF()/concat wrappers that return a
# display string (so an ACCOUNTING `$` value format never attaches). em-dash = —.
_QUOTED_NUMERIC_DISPLAY = re.compile(r'"[\s\d$,().\-—]+"')

# Core display-wrapper signals (applied to whitespace-stripped upper formula body).
_HAS_TEXT = re.compile(r"TEXT\s*\(", re.IGNORECASE)
_HAS_ISNUMBER = re.compile(r"ISNUMBER\s*\(", re.IGNORECASE)
_SCALE_DIV_1000 = re.compile(r"/\s*1000\b")
_TEXT_ROUND_SCALE = re.compile(
    r"TEXT\s*\(\s*ROUND\s*\([^)]*/\s*1000",
    re.IGNORECASE,
)
_TEXT_DIV_SCALE = re.compile(
    r"TEXT\s*\([^)]*/\s*1000",
    re.IGNORECASE,
)

# Subkind labels — stable grouping signatures for scan UI.
SUBKIND_LABELS: dict[str, str] = {
    "text-round-scale": "TEXT(ROUND(.../1000)) display mirror",
    "text-div-scale": "TEXT(.../1000) scaled display mirror",
    "isnumber-text-mirror-scaled": "ISNUMBER→TEXT scaled display mirror",
    "isnumber-text-mirror": "ISNUMBER→TEXT display mirror",
    "literal-numeric-string": "literal numeric string formula",
    "format-inert-wrapper": "TEXT()/IF() wrapper makes ACCOUNTING $ format inert",
}

# D21 — value-format types whose `$`/commas/parens/em-dash render ONLY on a numeric
# calculatedValue. A string-emitting formula (TEXT()/IF()/literal) bypasses them
# silently (dozens of confirmed cells across two production ACFR engagements).
_ACCOUNTING = "ACCOUNTING"
_CURRENCY = "CURRENCY"


def normalize_formula(formula: str | None) -> str:
    """Uppercase, strip leading =, collapse whitespace — for pattern matching only."""
    if not formula or not isinstance(formula, str):
        return ""
    body = formula.strip()
    if body.startswith("="):
        body = body[1:]
    return _WS.sub("", body.upper())


def classify_display_wrapper(formula: str | None) -> dict[str, Any] | None:
    """
    Classify a formula as a display-wrapper defect, or return None when clean/unknown.

    Returns {"subkind": str, "patterns": list[str], "severity": str} or None.
    """
    if not formula or not isinstance(formula, str):
        return None
    raw = formula.strip()
    if not raw.startswith("="):
        return None

    if _LITERAL_NUMERIC.match(raw):
        return {
            "subkind": "literal-numeric-string",
            "patterns": ["NUMBER_LITERAL_STRING_FORMULA"],
            "severity": "high",
        }

    norm = normalize_formula(raw)
    if not norm or "TEXT(" not in norm and "ISNUMBER(" not in norm:
        return None

    patterns: list[str] = []
    if "TEXT(" in norm:
        patterns.append("TEXT")
    if "ISNUMBER(" in norm:
        patterns.append("ISNUMBER_TEXT")
    if _SCALE_DIV_1000.search(norm):
        patterns.append("SCALE_IN_TEXT")
    if _HAS_ISNUMBER.search(norm) and _HAS_TEXT.search(norm):
        patterns.append("SCREENSHOT_PATTERN")

    has_scale = bool(_SCALE_DIV_1000.search(norm))
    has_text_round = bool(_TEXT_ROUND_SCALE.search(norm))
    has_text_div = bool(_TEXT_DIV_SCALE.search(norm))
    has_isnumber = bool(_HAS_ISNUMBER.search(norm))
    has_text = bool(_HAS_TEXT.search(norm))

    if has_text_round:
        return {
            "subkind": "text-round-scale",
            "patterns": patterns or ["TEXT", "SCALE_IN_TEXT"],
            "severity": "high",
        }
    if has_text_div and not has_isnumber:
        return {
            "subkind": "text-div-scale",
            "patterns": patterns or ["TEXT", "SCALE_IN_TEXT"],
            "severity": "high",
        }
    if has_isnumber and has_text and has_scale:
        return {
            "subkind": "isnumber-text-mirror-scaled",
            "patterns": patterns or ["TEXT", "ISNUMBER_TEXT", "SCALE_IN_TEXT"],
            "severity": "high",
        }
    if has_isnumber and has_text:
        return {
            "subkind": "isnumber-text-mirror",
            "patterns": patterns or ["TEXT", "ISNUMBER_TEXT", "SCREENSHOT_PATTERN"],
            "severity": "high",
        }
    # NOTE: a bare has_text_div case cannot reach here — it implies TEXT( + /1000, so it is
    # always returned above by the no-ISNUMBER branch (text-div-scale) or the ISNUMBER branches.
    return None


def accounting_dollar_format(value_format: dict[str, Any] | None) -> bool:
    """D21 — True when the cell carries an ACCOUNTING `$` (or CURRENCY) value format.

    Workiva attaches the `$` / commas / parens / em-dash of these formats ONLY when the
    cell's calculatedValue is numeric. A string-emitting formula makes the format inert
    (a documented format-inert defect class). CURRENCY carries an inherent currency symbol; ACCOUNTING
    relies on an explicit `$` in its `prefix` field (format_lane treats ACCOUNTING prefix
    as the dollar carrier, unlike CURRENCY which is skipped there).
    """
    if not isinstance(value_format, dict):
        return False
    vft = (value_format.get("valueFormatType") or "").upper()
    if vft == _CURRENCY:
        return True
    if vft != _ACCOUNTING:
        return False
    prefix = value_format.get("prefix")
    return isinstance(prefix, str) and "$" in prefix


def string_emitting_formula(formula: str | None) -> bool:
    """D21 — True when the formula's RESULT is a display string, so an ACCOUNTING/$ value
    format is bypassed. Conservative: a literal numeric string (="1,234"), any
    TEXT() call, or an IF()/concat that returns a quoted formatted-number / em-dash literal.
    Numeric formulas (SUM/SUMIFS/bare refs/arithmetic) return False.
    """
    if not formula or not isinstance(formula, str):
        return False
    raw = formula.strip()
    if not raw.startswith("="):
        return False
    if _LITERAL_NUMERIC.match(raw):
        return True
    norm = normalize_formula(raw)
    if not norm:
        return False
    if _HAS_TEXT.search(norm):
        return True
    # IF()/concatenation that returns a quoted formatted-number or accounting-dash literal
    # (e.g. =IF(ISNUMBER(A1),A1,"-") or ="("&...&")"). A quoted text criterion like
    # "Revenue" is NOT a numeric-display literal, so plain text-criteria IFs do not match.
    if "IF(" in norm and _QUOTED_NUMERIC_DISPLAY.search(raw):
        return True
    return False


def _format_inert_target(formula: str, value_format: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "subkind": "format-inert-wrapper",
        "patterns": ["STRING_FORMULA", "ACCOUNTING_DOLLAR_INERT"],
        "formulaExcerpt": _excerpt(formula),
        "accountingDollarInert": True,
        "valueFormatType": (value_format or {}).get("valueFormatType"),
    }


def detect_display_wrapper(cell: dict[str, Any]) -> dict[str, Any] | None:
    """
    Detector entry point — requires formula on the normalized cell dict.
    Always surfaced/RED; never fixable (W21 canary guard).

    D21: when the cell ALSO carries an ACCOUNTING `$` (or CURRENCY) value format and the
    formula emits a string, the format is provably inert. For a classifier hit the
    finding is enriched with `accountingDollarInert`; for a string wrapper the classifier
    misses (unscaled TEXT(), IF()-dash), a dedicated `format-inert-wrapper` finding is emitted.
    """
    formula = cell.get("formula")
    if not formula:
        # sheetdata sometimes stores the formula string in value
        val = cell.get("value") or ""
        if isinstance(val, str) and val.strip().startswith("="):
            formula = val.strip()
    if not formula:
        return None

    value_format = cell.get("valueFormat")
    fmt_inert = accounting_dollar_format(value_format) and string_emitting_formula(formula)

    hit = classify_display_wrapper(formula)
    if not hit:
        if fmt_inert:
            return {
                "kind": "display-wrapper",
                "addr": cell["addr"],
                "severity": "high",
                "detail": SUBKIND_LABELS["format-inert-wrapper"],
                "fixable": False,
                "fix_lane": "surfaced",
                "target": _format_inert_target(formula, value_format),
            }
        return None

    subkind = hit["subkind"]
    detail = SUBKIND_LABELS.get(subkind, subkind)
    target: dict[str, Any] = {
        "subkind": subkind,
        "patterns": hit.get("patterns") or [],
        "formulaExcerpt": _excerpt(formula),
    }
    if fmt_inert:
        # D21 enrichment — the ACCOUNTING `$` value format on this mirror is inert.
        target["accountingDollarInert"] = True
        target["valueFormatType"] = (value_format or {}).get("valueFormatType")
        if "ACCOUNTING_DOLLAR_INERT" not in target["patterns"]:
            target["patterns"] = [*target["patterns"], "ACCOUNTING_DOLLAR_INERT"]
        detail = f"{detail} — ACCOUNTING $ format inert (string formula bypasses it)"
    return {
        "kind": "display-wrapper",
        "addr": cell["addr"],
        "severity": hit.get("severity", "high"),
        "detail": detail,
        "fixable": False,
        "fix_lane": "surfaced",
        "target": target,
    }


def _excerpt(formula: str, max_len: int = 48) -> str:
    s = formula.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def wrapper_subkind(detail: str, target: dict[str, Any] | None = None) -> str:
    """Resolve subkind for diagnose pathway lookup."""
    if target and target.get("subkind"):
        return str(target["subkind"])
    for sk, label in SUBKIND_LABELS.items():
        if detail == label:
            return sk
    return "other"

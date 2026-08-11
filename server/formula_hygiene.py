#!/usr/bin/env python3
"""
Wingman formula-hygiene detectors (Lane G — surfaced only, no auto-fix).

Companion to hardcoded_value.py. That module flags a cell that SHOULD be a formula but is a
bare literal (a hardcoded face value). This module flags the opposite shape: a real formula
that HIDES a hardcode — a literal criterion, a numeric plug, or a display workaround. Both
serve Dave's #1 rule: "no hiding hardcoded values in formulas; route criteria and amounts
through the architecture (TB / mapping / Formula Reference), not literals."

Every finding here is JUDGMENT (the correct fix is a mapping/formula change the scanner cannot
synthesize), so all are fix_lane="surfaced", fixable=False — review items, never auto-written.
All three need the formula string (WINGMAN_FORMULA_FETCH); each no-ops cleanly when absent.

Detectors:
  - hardcoded-text-criteria-in-formula : a literal CODE criterion baked into a SUMIFS-family
        call (SUMIFS(...,"ACT-ADMIN"); exclusion list "<>EXP-EQUIP") instead of a row / mapping /
        Formula Reference cell. Thousands of include-literals + hundreds of "<>" exclusion-literals sat on
        production ACFR statement faces — the highest-frequency
        defect. Quoted strings are read ONLY from SUMIFS-family CRITERIA argument positions (parsed
        per call), so IFERROR fallbacks ("N/A") and IF branch returns ("YES"/"NO") never fire.
        Grouped by code-set, so a repeated house pattern is one Review line, not N. An exclusion
        list is "high" (it silently captures every NEW code next year); an include literal is "medium".
  - hardcoded-constant-in-formula      : a large numeric PLUG in additive position
        (=SUMIFS(...)+12000000, =Total-250000) or a sole-body bare literal (=12345) — the
        compensating-stack constant (a documented compensating-hardcode-stack pattern). The
        additive-position gate structurally excludes the false positives an adversarial pass
        surfaced: date serials (=IF(A1>=44927,...)), function args (MROUND(...,10000)), and
        comparison operands.
  - round-wrapper-workaround           : =ROUND(SUM(...),-3) negative-precision display rounding.
        Workiva renders thousands via valueFormat (shownIn), not a ROUND wrapper that loses stored
        precision (display rounding belongs in valueFormat, not the formula). Each ROUND call is parsed to its matching
        close-paren and only its LAST argument is checked, so a ROUND(x,0) next to an unrelated
        SUMIFS(...,-N) never misfires. TEXT(.../1000) scale shapes are left to display_wrapper.
  - unbounded-full-column-sumifs        : a full-COLUMN range ($AC:$AC, A:A, A:AD) passed to a
        SUMIFS-family call. It computes fine LIVE but throws #VALUE! when the workbook is exported to
        xlsx and reimported / rolled forward (a documented bounding recipe covers the
        value-preserving repair) — the single most frequent Workiva
        portability defect. The fix is to BIND every full-column range to explicit rows ($1:$N),
        value-preserving when N >= the data extent; that gated write-plan is generated/applied by
        the external checks toolkit's bounded-SUMIFS write-planner, so Wingman only SURFACES the latent debt. Each SUMIFS-family call's
        argument list is parsed and tested for a full-column range AFTER quoted strings + sheet names
        are stripped, so a quoted criterion that merely looks like a range ("A:Z"), a sheet name
        containing a colon, a full-ROW range ($1:$1 header lookup — not the #VALUE risk), a bounded
        range ($AC$2:$AC$100), and a full-column ref OUTSIDE any IFS call (INDEX(A:A,…), a VLOOKUP
        table) all stay quiet. Grouped per sheet: the detail is a fixed signature so N flagged cells
        collapse to one Review line (per-sheet count, not per-cell noise).
  - dead-unsupported-function            : a formula calls a function Workiva's engine REJECTS on
        xlsx import → #NAME? (the formula stops computing entirely). Mirrors the external
        corpus-audit dead-function detector (the highest-weighted corpus detector): the dynamic-array /
        spill family (FILTER, SORT/SORTBY, UNIQUE, LAMBDA, LET, VSTACK/HSTACK, SEQUENCE, TOROW/TOCOL,
        TAKE, DROP, EXPAND, CHOOSEROWS/CHOOSECOLS), INDIRECT, the volatile positional ROW()/COLUMN()/
        OFFSET(), and an internal-anchor HYPERLINK("#...). Extends that gate with the TRUE()/FALSE()
        CALL form — Workiva accepts the TRUE/FALSE literals but rejects the parenthesised call
        (one production engagement had thousands of FALSE() calls — its #1 import blocker). Each token must be a CALL (name + "("),
        so the supported count functions ROWS(/COLUMNS( and any longer name (OFFSETTOTAL(, FILTERED()
        ) never misfire, and the bare TRUE/FALSE literals stay quiet. Function names inside sheet refs
        ('SORT detail'!A1) or quoted criteria ("FILTER") are stripped before matching. The detail
        names the function SET, so a sheet's identical-pattern cells collapse to one per-sheet Review
        row. It is surfaced only: the remediation is a per-function formula rewrite to a supported
        equivalent (a judgment Wingman cannot synthesize), never an auto-write.
  - degenerate-placeholder-formula       : a formula cell contains only a placeholder constant
        shape (=0, =1+1, =100-100, =(2+2)*0) instead of a governed pull/formula. These pass numeric
        tieout only when another cell compensates or when the line is untested; they also survive
        roll-forward because they look like formulas. To avoid row-control false positives, bare
        =1 / =-1 are intentionally skipped; large sole literals stay owned by
        hardcoded-constant-in-formula. Grouped per sheet with a fixed signature; surfaced only.

Deferred (recorded so they are not re-attempted blind — see docs/adr/0006):
  - number-stored-as-text : ADR-0004 measured rejection (5 hits, all year headers); TEXT/PERIOD on a
        year cell is intentional (the TEXT/PERIOD route is the documented fix for year headers).
  - scaled-decimal-code-key : real but niche (override sheets); needs an
        override/support/code sheet-name gate before it clears the cry-wolf bar.
  - wrong-sign-value & cross-foot / sum-vs-subtract : need the natural-balance map / statement
        structure the per-cell scanner lacks -> the tieout path (checks_bridge) owns these.

Pure + deterministic; run `python3 server/formula_hygiene.py` to self-test (no network).
"""
from __future__ import annotations

import ast
import re

# --- shared formula extraction / stripping -----------------------------------

# Double-quoted string literal (Excel escapes an inner quote by doubling it).
_DQ = re.compile(r'"(?:[^"]|"")*"')
# A quoted sheet name followed by '!' — 'Trial Balance'! (single quotes are sheet refs,
# never string literals, in the Workiva/Excel grammar).
_SHEETREF = re.compile(r"'[^']*'!")
# A1 / $A$1 / A1:B10 cell ref, or A:AD column range, or $1:$1 row range.
_CELLREF = re.compile(
    r"\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?"  # A1 or A1:B10
    r"|\$?[A-Za-z]{1,4}:\$?[A-Za-z]{1,4}"                  # A:AD column range
    r"|\$\d+:\$?\d+"                                       # $1:$1 row range
)


def _formula_of(cell: dict) -> str | None:
    """Return the cell's formula string (sheetdata stores it on .formula or in .value)."""
    f = cell.get("formula")
    if isinstance(f, str) and f.strip().startswith("="):
        return f.strip()
    v = cell.get("value")
    if isinstance(v, str) and v.strip().startswith("="):
        return v.strip()
    return None


def _arg_list(s: str, open_idx: int) -> tuple[list[str], int]:
    """
    Parse a function argument list. `open_idx` is the index of the '('. Returns
    (args, close_idx): top-level comma-separated argument substrings (quotes and nested
    parens respected) and the index of the matching ')'.
    """
    args: list[str] = []
    depth, in_str = 0, None
    start = i = open_idx + 1
    n = len(s)
    while i < n:
        ch = s[i]
        if in_str is not None:
            if ch == in_str:
                if ch == '"' and i + 1 < n and s[i + 1] == '"':  # doubled-quote escape
                    i += 2
                    continue
                in_str = None
            i += 1
            continue
        if ch in ('"', "'"):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                args.append(s[start:i])
                return args, i
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(s[start:i])
            start = i + 1
        i += 1
    args.append(s[start:i])
    return args, n  # unbalanced — best effort


def _skeleton(raw: str) -> str:
    """Replace refs / sheet refs / string literals with placeholders, preserving position."""
    body = raw[1:] if raw.startswith("=") else raw
    body = _SHEETREF.sub(" REF ", body)
    body = _DQ.sub(" STR ", body)
    body = _CELLREF.sub(" REF ", body)
    return body


# --- detector 1: hardcoded text criteria in a SUMIFS-family call --------------

_IFS_FUNC = re.compile(r"\b(?:SUMIFS?|COUNTIFS?|AVERAGEIFS?|MAXIFS|MINIFS)\b", re.I)
# Code-like literal: optional <> / leading wildcard, an UPPERCASE-led code with
# digits/underscores and hyphen/slash segments, optional trailing wildcard. Matches
# e.g. ACT-ADMIN, EXP-EQUIP, REV-FEES, TRF-IN, EXP-*, <>EXP-EQUIP.
# Rejects "" (empty), "<>"/"*"/">0" (no letter), "#,##0" (format mask). Because criteria are
# now read only from SUMIFS criteria positions, IFERROR/IF result strings (N/A, YES, NO) are
# never even considered.
_CODE_LIKE = re.compile(r"^(?:<>)?\*?[A-Z][A-Z0-9_]*(?:[-/][A-Z0-9_*]+)*\*?$")


def _criteria_indices(fname: str, nargs: int):
    """Argument indices that hold a criterion for each IFS-family function."""
    if fname in ("SUMIFS", "AVERAGEIFS", "MAXIFS", "MINIFS"):
        return range(2, nargs, 2)   # range/criteria pairs after the value range
    if fname == "COUNTIFS":
        return range(1, nargs, 2)   # range/criteria pairs from the start
    if fname in ("SUMIF", "AVERAGEIF", "COUNTIF"):
        return (1,) if nargs >= 2 else ()  # criterion is the 2nd argument
    return ()


def _ifs_criteria_args(raw: str) -> list[str]:
    """Collect the raw criterion argument strings across every IFS-family call in `raw`."""
    out: list[str] = []
    for m in _IFS_FUNC.finditer(raw):
        j = m.end()
        while j < len(raw) and raw[j] == " ":
            j += 1
        if j >= len(raw) or raw[j] != "(":
            continue
        args, _ = _arg_list(raw, j)
        for ci in _criteria_indices(m.group(0).upper(), len(args)):
            out.append(args[ci].strip())
    return out


def detect_text_criteria(cell: dict) -> dict | None:
    raw = _formula_of(cell)
    if not raw or not _IFS_FUNC.search(raw):
        return None
    codes = []
    for arg in _ifs_criteria_args(raw):
        if len(arg) >= 2 and arg[0] == '"' and arg[-1] == '"':  # a quoted literal criterion
            inner = arg[1:-1].replace('""', '"')
            if _CODE_LIKE.match(inner):
                codes.append(inner)
    if not codes:
        return None
    uniq = sorted(set(codes))
    excl = [c for c in uniq if c.startswith("<>")]
    incl = [c for c in uniq if not c.startswith("<>")]
    label = "include + exclusion" if (excl and incl) else ("exclusion" if excl else "include")
    return {
        "kind": "hardcoded-text-criteria-in-formula",
        "addr": cell["addr"],
        # stable signature: the code-set, so identical house patterns group into one row
        "detail": f"hardcoded {label} criteria: {', '.join(uniq)}",
        "severity": "high" if excl else "medium",
        "fixable": False,
        "fix_lane": "surfaced",
        "target": {"codes": uniq, "exclusion": bool(excl)},
    }


# --- detector 2: hardcoded numeric constant in a formula ----------------------

MAGNITUDE_MIN = 1000  # an additive / sole-body constant must be at least this to flag
_ADD_NUM = re.compile(r"([+\-])\s*(\d+(?:\.\d+)?)")
_SOLE_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
_DATE_FUNCS = re.compile(
    r"\b(?:DATE|DATEVALUE|TODAY|NOW|EOMONTH|WORKDAY|EDATE|YEAR|MONTH|DAY|WEEKDAY|YEARFRAC|PRICE|YIELD)\b",
    re.I,
)


def _is_year(av: float) -> bool:
    return 1900 <= av <= 2099 and av == int(av)


def _additive_constants(raw: str) -> list[float]:
    """Numeric literals in BINARY +/- position within the ref-stripped skeleton."""
    sk = _skeleton(raw)
    out = []
    for m in _ADD_NUM.finditer(sk):
        i = m.start() - 1
        while i >= 0 and sk[i] == " ":
            i -= 1
        prev = sk[i] if i >= 0 else ""
        # a binary +/- operator follows an operand: a placeholder (alnum), ')', '%', '_'
        if not (prev.isalnum() or prev in ")%_"):
            continue
        val = float(m.group(2))
        out.append(val if m.group(1) == "+" else -val)
    return out


def _fmt_num(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def detect_constant_in_formula(cell: dict) -> dict | None:
    raw = _formula_of(cell)
    if not raw:
        return None
    consts: list[float] = []
    body = raw[1:].strip()
    # sole-body bare literal (=12345). A quoted "=\"123\"" is display_wrapper territory and
    # never matches here (quotes are not numeric).
    if _SOLE_NUM.match(body):
        av = abs(float(body))
        if av >= MAGNITUDE_MIN and not _is_year(av):
            consts.append(float(body))
    else:
        for v in _additive_constants(raw):
            av = abs(v)
            if av < MAGNITUDE_MIN:
                continue
            if _is_year(av):
                continue
            if 40000 <= av <= 65000 and _DATE_FUNCS.search(raw):
                continue  # Excel/Workiva date serial, not a dollar plug
            consts.append(v)
    if not consts:
        return None
    shown = ", ".join(_fmt_num(c) for c in consts[:4])
    return {
        "kind": "hardcoded-constant-in-formula",
        "addr": cell["addr"],
        "detail": f"hardcoded constant in formula ({shown})",
        "severity": "high",
        "fixable": False,
        "fix_lane": "surfaced",
        "target": {"constants": consts},
    }


# --- detector 3: ROUND wrapper used for display rounding ----------------------

_ROUND_FUNC = re.compile(r"\b(?:ROUND|ROUNDUP|ROUNDDOWN)\s*\(", re.I)
_NEG_INT = re.compile(r"^-\s*\d+$")
_HAS_TEXT = re.compile(r"\bTEXT\s*\(", re.I)
_SCALE_1000 = re.compile(r"/\s*1000\b")


def _has_neg_precision_round(raw: str) -> bool:
    """True when some ROUND-family call's LAST argument is a negative integer (display rounding)."""
    for m in _ROUND_FUNC.finditer(raw):
        open_idx = m.end() - 1  # the regex ends on the '('
        args, _ = _arg_list(raw, open_idx)
        if len(args) >= 2 and _NEG_INT.match(args[-1].strip()):
            return True
    return False


def detect_round_wrapper(cell: dict) -> dict | None:
    raw = _formula_of(cell)
    if not raw:
        return None
    # Defer to display_wrapper ONLY for the TEXT(.../1000) scale shape it owns — not for any
    # formula that merely mentions TEXT (that silenced real ROUND(...,-N) cells).
    if _HAS_TEXT.search(raw) and _SCALE_1000.search(raw):
        return None
    if not _has_neg_precision_round(raw):
        return None
    return {
        "kind": "round-wrapper-workaround",
        "addr": cell["addr"],
        "detail": "ROUND(...,-N) display rounding — use valueFormat (shownIn) instead",
        "severity": "medium",
        "fixable": False,
        "fix_lane": "surfaced",
        "target": None,
    }


# --- detector 4: unbounded full-column range in a SUMIFS-family call ----------

# A full-COLUMN range only: letters ':' letters. A1:B2 won't match (a digit breaks the
# letter run); $1:$1 won't match (no letters). Mirrors the external full-column-range detector.
_FULLCOL_RANGE = re.compile(r"\$?[A-Z]{1,3}:\$?[A-Z]{1,3}")


def _arg_has_fullcol_range(arg: str) -> bool:
    """True when an argument carries a full-column range, ignoring sheet names + string literals.

    Strip 'Sheet'! refs first so a sheet name with a colon ('Budget A:Z'!) never reads as a
    range, then strip "..." literals so a quoted criterion ("A:Z") never does either.
    """
    cleaned = _DQ.sub(" ", _SHEETREF.sub(" ", arg))
    return bool(_FULLCOL_RANGE.search(cleaned))


def detect_unbounded_sumifs(cell: dict) -> dict | None:
    raw = _formula_of(cell)
    if not raw or not _IFS_FUNC.search(raw):
        return None
    # Inspect ONLY arguments of each IFS-family call — a full-column ref inside INDEX/VLOOKUP
    # is not the SUMIFS reimport defect and must not fire.
    hit = False
    for m in _IFS_FUNC.finditer(raw):
        j = m.end()
        while j < len(raw) and raw[j] == " ":
            j += 1
        if j >= len(raw) or raw[j] != "(":
            continue
        args, _ = _arg_list(raw, j)
        if any(_arg_has_fullcol_range(a) for a in args):
            hit = True
            break
    if not hit:
        return None
    return {
        "kind": "unbounded-full-column-sumifs",
        "addr": cell["addr"],
        # fixed signature -> all flagged cells on a sheet collapse to one per-sheet Review row
        "detail": "unbounded full-column range in SUMIFS-family call — #VALUE! on xlsx "
                  "reimport/roll-forward; bind ranges to rows ($1:$N)",
        "severity": "medium",
        "fixable": False,
        "fix_lane": "surfaced",
        "target": None,
    }


# --- detector 5: dead / unsupported function in a formula --------------------

# Functions Workiva's formula engine rejects on xlsx import (-> #NAME?). Mirrors the
# external corpus-audit dead-function list: the dynamic-array / spill family, INDIRECT, and the volatile
# positional ROW()/COLUMN()/OFFSET(). Longest names that share a prefix (SORTBY before SORT)
# come first so the canonical token is captured. Each must be a CALL (name + "(") via the
# trailing _DEAD_CALL anchor, so the supported count functions ROWS(/COLUMNS( and any longer
# name (OFFSETTOTAL() never match.
_DEAD_FUNCS = (
    "INDIRECT", "FILTER", "SORTBY", "SORT", "UNIQUE", "LAMBDA", "LET",
    "VSTACK", "HSTACK", "SEQUENCE", "TOROW", "TOCOL", "TAKE", "DROP",
    "EXPAND", "CHOOSEROWS", "CHOOSECOLS", "OFFSET", "COLUMN", "ROW",
)
_DEAD_CALL = re.compile(r"\b(" + "|".join(_DEAD_FUNCS) + r")\s*\(", re.I)
# TRUE()/FALSE() as a FUNCTION call (empty parens). Workiva accepts the TRUE/FALSE LITERALS but
# rejects the call form on import (a production engagement had thousands of FALSE() — its #1 import blocker). The
# empty-paren requirement keeps the bare literals (=IF(A1,TRUE,FALSE)) quiet.
_BOOL_CALL = re.compile(r"\b(TRUE|FALSE)\s*\(\s*\)", re.I)
# Internal-anchor HYPERLINK (HYPERLINK("#...)) evaluates to #NAME? (same corpus-audit finding). The
# "#" lives INSIDE the quoted arg, so this is tested on the raw formula before quotes are stripped.
_ANCHOR_HYPERLINK = re.compile(r"\bHYPERLINK\s*\(\s*\"#", re.I)


def detect_dead_functions(cell: dict) -> dict | None:
    raw = _formula_of(cell)
    if not raw:
        return None
    funcs: list[str] = []
    if _ANCHOR_HYPERLINK.search(raw):
        funcs.append('HYPERLINK("#")')
    # Strip sheet refs + string literals so a function name inside a sheet name
    # ('SORT detail'!A1) or a quoted criterion ("FILTER") never fires.
    cleaned = _DQ.sub(" ", _SHEETREF.sub(" ", raw))
    for m in _DEAD_CALL.finditer(cleaned):
        funcs.append(m.group(1).upper() + "()")
    for m in _BOOL_CALL.finditer(cleaned):
        funcs.append(m.group(1).upper() + "()")
    if not funcs:
        return None
    uniq = sorted(set(funcs))
    return {
        "kind": "dead-unsupported-function",
        "addr": cell["addr"],
        # signature names the function SET -> identical-pattern cells on a sheet collapse to one
        # Review row (thousands of FALSE() calls -> a single line, not thousands of per-cell noise)
        "detail": f"dead/unsupported function(s) in formula: {', '.join(uniq)} — "
                  "#NAME? on xlsx import/roll-forward",
        "severity": "high",
        "fixable": False,
        "fix_lane": "surfaced",
        "target": {"functions": uniq},
    }


# --- detector 6: degenerate placeholder formula ------------------------------

_CONST_EXPR = re.compile(r"^[\d\s.()+\-*/]+$")
_HAS_ARITH_OP = re.compile(r"[+\-*/]")


def _eval_constant_arithmetic(body: str) -> float | None:
    """Safely evaluate a tiny numeric-only Excel arithmetic expression."""
    expr = body.replace("^", "**")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    allowed_nodes = (
        ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd,
        ast.Load,
    )
    if not all(isinstance(node, allowed_nodes) for node in ast.walk(tree)):
        return None
    try:
        value = eval(compile(tree, "<constant-formula>", "eval"), {"__builtins__": {}}, {})
    except Exception:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _is_zero_literal(body: str) -> bool:
    return bool(re.fullmatch(r"[+\-]?0+(?:\.0+)?", body.strip()))


def detect_degenerate_placeholder_formula(cell: dict) -> dict | None:
    raw = _formula_of(cell)
    if not raw:
        return None
    body = raw[1:].strip()
    compact = re.sub(r"\s+", "", body)
    if not compact:
        return None
    # Strings, refs, function names, comparisons, concatenation, commas, and percentages are outside
    # this detector. Existing specialized lanes own those shapes.
    if not _CONST_EXPR.fullmatch(compact):
        return None
    value = _eval_constant_arithmetic(compact)
    if value is None:
        return None
    is_zero = _is_zero_literal(compact)
    has_op = bool(_HAS_ARITH_OP.search(compact[1:] if compact[:1] in "+-" else compact))
    if not is_zero and not has_op:
        return None  # bare =1 / =-1 row-control formulas are common and intentional
    av = abs(value)
    if av >= MAGNITUDE_MIN or _is_year(av):
        return None  # large constants and years are owned by other lanes / intentional headers
    return {
        "kind": "degenerate-placeholder-formula",
        "addr": cell["addr"],
        "detail": "degenerate placeholder formula (=0 or constant-only arithmetic) — "
                  "replace with governed pull/formula or documented input",
        "severity": "medium",
        "fixable": False,
        "fix_lane": "surfaced",
        "target": {"value": value},
    }


# --- scan entry --------------------------------------------------------------

_DETECTORS = (detect_text_criteria, detect_constant_in_formula, detect_round_wrapper,
              detect_unbounded_sumifs, detect_dead_functions, detect_degenerate_placeholder_formula)


def scan_formula_hygiene(cells: list[dict]) -> list[dict]:
    """Run every formula-hygiene detector over normalized cells; return findings list."""
    out: list[dict] = []
    for cell in cells:
        for det in _DETECTORS:
            f = det(cell)
            if f:
                out.append(f)
    return out


# --- self-test (deterministic, no network) -----------------------------------

def _selftest() -> bool:
    cases: list[tuple[str, bool]] = []

    def check(name, cond):
        cases.append((name, bool(cond)))

    # ---- text criteria
    f = detect_text_criteria({"addr": "R38", "formula": '=SUMIFS(A:A,B:B,"ACT-ADMIN")'})
    check("include criteria fires", f and f["kind"] == "hardcoded-text-criteria-in-formula")
    check("include is medium", f and f["severity"] == "medium")
    f = detect_text_criteria({"addr": "R39", "formula": '=SUMIFS(A:A,B:B,"<>EXP-EQUIP")'})
    check("exclusion criteria fires", f and f["severity"] == "high")
    f = detect_text_criteria({"addr": "R40", "formula": '=SUMIFS($F:$F,$H:$H,"Y",$K:$K,"GOV",$B:$B,"RCL")'})
    check("multi include codes group by code-set", f and f["target"]["codes"] == ["GOV", "RCL", "Y"])
    f = detect_text_criteria({"addr": "R47", "formula": '=SUMIFS($F:$F,$H:$H,"GOV",$B:$B,"<>EXP-EQUIP")'})
    check("mixed criteria labeled both + high", f and f["severity"] == "high" and "include + exclusion" in f["detail"])
    # positional FP guards — IFERROR fallback / IF branches are NOT criteria
    check("IFERROR sentinel N/A not a criterion",
          detect_text_criteria({"addr": "R41", "formula": '=IFERROR(SUMIFS(A:A,B:B,$C41),"N/A")'}) is None)
    check("IFERROR sentinel NA not a criterion",
          detect_text_criteria({"addr": "R48", "formula": '=IFERROR(SUMIFS(A:A,B:B,$C48),"NA")'}) is None)
    check("IF branch YES/NO not criteria",
          detect_text_criteria({"addr": "R49", "formula": '=IF(SUMIFS(A:A,B:B,$C49)>0,"YES","NO")'}) is None)
    check("IFERROR Mapping error fallback not a criterion",
          detect_text_criteria({"addr": "R42", "formula": '=IFERROR(SUMIFS(A:A,B:B,$C42),"Mapping error")'}) is None)
    check("empty fund-blank filter ok",
          detect_text_criteria({"addr": "R43", "formula": '=SUMIFS($F:$F,$L:$L,"")'}) is None)
    check("clean SUMIFS with cell criteria ok",
          detect_text_criteria({"addr": "R44", "formula": '=SUMIFS(A:A,B:B,$C44)'}) is None)
    check("non-IFS quoted string skipped",
          detect_text_criteria({"addr": "R45", "formula": '=IF(A1>0,"YES","NO")'}) is None)
    check("text criteria no formula no-op",
          detect_text_criteria({"addr": "R46", "calculatedValue": "100"}) is None)

    # ---- numeric constant
    f = detect_constant_in_formula({"addr": "D42", "formula": '=SUMIFS(B1:B100,C1:C100,"X")+12000000'})
    check("additive plug fires", f and f["kind"] == "hardcoded-constant-in-formula")
    check("plug value captured", f and 12000000 in f["target"]["constants"])
    check("subtraction plug fires", detect_constant_in_formula({"addr": "E10", "formula": "=Total-250000"}))
    check("sole-body literal fires", detect_constant_in_formula({"addr": "C5", "formula": "=12345"}))
    check("negative additive plug fires", detect_constant_in_formula({"addr": "G20", "formula": "=A1+B1-12500000"}))
    check("quoted numeric string skipped (display_wrapper)",
          detect_constant_in_formula({"addr": "B3", "formula": '="12000000"'}) is None)
    check("scale divisor /1000 skipped",
          detect_constant_in_formula({"addr": "C10", "formula": "=ROUND(A1/1000,0)"}) is None)
    check("ROUND precision arg skipped",
          detect_constant_in_formula({"addr": "D15", "formula": "=ROUND(SUM(A1:A50),-3)"}) is None)
    check("year literal skipped", detect_constant_in_formula({"addr": "E7", "formula": "=A1-2024"}) is None)
    check("cell-ref digits do not leak",
          detect_constant_in_formula({"addr": "I9", "formula": "=SUM(A12345:A20000)"}) is None)
    check("date-serial comparison skipped",
          detect_constant_in_formula({"addr": "A5", "formula": "=IF(A2>=44927,B2,0)"}) is None)
    check("MROUND multiple arg skipped",
          detect_constant_in_formula({"addr": "C6", "formula": "=MROUND(SUM(A1:A10),10000)"}) is None)
    check("date-serial in date-function additive skipped",
          detect_constant_in_formula({"addr": "F5", "formula": "=EDATE(B1,0)+44927"}) is None)
    check("small additive not flagged", detect_constant_in_formula({"addr": "H6", "formula": "=A1+100"}) is None)
    check("rate multiplier skipped", detect_constant_in_formula({"addr": "H7", "formula": "=A1*1.05"}) is None)
    check("constant no formula no-op",
          detect_constant_in_formula({"addr": "Z1", "calculatedValue": "12000000"}) is None)

    # ---- round wrapper
    check("ROUND(-3) display wrapper fires",
          detect_round_wrapper({"addr": "D1", "formula": "=ROUND(SUM(A1:A50),-3)"}))
    check("ROUND(SUM,-3) with nested paren still fires (paren-walk)",
          detect_round_wrapper({"addr": "D7", "formula": "=ROUND(SUM(A1:A50)+SUM(B1:B9),-3)"}))
    check("ROUND(...,0) not flagged", detect_round_wrapper({"addr": "D2", "formula": "=ROUND(A1/1000,0)"}) is None)
    # boundary-crossing FP: ROUND(x,0) next to an unrelated SUMIFS(...,-N) must NOT fire
    check("ROUND(x,0)+SUMIFS(...,-N) no boundary-cross FP",
          detect_round_wrapper({"addr": "D8", "formula": "=ROUND($A1,0)+SUMIFS($B:$B,$C:$C,-5000)"}) is None)
    check("ROUND(ref,0)+SUMIFS(...,-250000) no FP",
          detect_round_wrapper({"addr": "D9", "formula": "=ROUND(SUM(F:F),0)+SUMIFS(F:F,D:D,$C38,-250000)"}) is None)
    # false-negative fix: a real ROUND(-3) coexisting with an unrelated TEXT( must still fire
    check("ROUND(-3) with unrelated TEXT still fires",
          detect_round_wrapper({"addr": "D10", "formula": '=ROUND(SUM(A:A),-3)&" "&TEXT(B1,"#,##0")'}))
    check("ROUND(-3) inside IFERROR with TEXT fallback fires",
          detect_round_wrapper({"addr": "D11", "formula": '=IFERROR(ROUND(SUM(A:A),-3),TEXT(0,"#,##0"))'}))
    check("TEXT(.../1000) scale deferred to display_wrapper",
          detect_round_wrapper({"addr": "D3", "formula": '=TEXT(ROUND(A1/1000,0),"#,##0")'}) is None)
    check("round wrapper no formula no-op", detect_round_wrapper({"addr": "D4", "calculatedValue": "100"}) is None)

    # ---- unbounded full-column SUMIFS
    f = detect_unbounded_sumifs({"addr": "K17", "formula": '=SUMIFS($AC:$AC,$A:$A,"GOV")'})
    check("full-col SUMIFS fires", f and f["kind"] == "unbounded-full-column-sumifs")
    check("full-col SUMIFS is medium/surfaced", f and f["severity"] == "medium" and f["fix_lane"] == "surfaced" and f["fixable"] is False)
    check("A:A short-form full-col fires",
          detect_unbounded_sumifs({"addr": "K18", "formula": "=SUMIFS(A:A,B:B,$C18)"}))
    check("sheet-qualified full-col fires",
          detect_unbounded_sumifs({"addr": "K19", "formula": "=SUMIFS('Trial Balance'!$AC:$AC,'Trial Balance'!$A:$A,B5)"}))
    check("multi-col range A:AD fires",
          detect_unbounded_sumifs({"addr": "K20", "formula": '=COUNTIFS(A:AD,">0")'}))
    # FP guards
    check("bounded ranges do not fire",
          detect_unbounded_sumifs({"addr": "K21", "formula": '=SUMIFS($AC$2:$AC$100,$A$2:$A$100,"GOV")'}) is None)
    check("bounded ranges without row-$ do not fire",
          detect_unbounded_sumifs({"addr": "K22", "formula": "=SUMIFS($AC2:$AC100,$A2:$A100,B5)"}) is None)
    check("quoted range-looking criterion does not fire",
          detect_unbounded_sumifs({"addr": "K23", "formula": '=SUMIFS($B$2:$B$100,$A$2:$A$100,"A:Z")'}) is None)
    check("sheet name with colon does not fire",
          detect_unbounded_sumifs({"addr": "K24", "formula": "=SUMIFS('Budget A:Z'!$B$2:$B$100,'Budget A:Z'!$A$2:$A$100,B5)"}) is None)
    check("full-col in INDEX (not SUMIFS) does not fire",
          detect_unbounded_sumifs({"addr": "K25", "formula": "=INDEX(A:A,MATCH(B1,C:C,0))"}) is None)
    check("bounded SUMIFS + full-col INDEX does not fire",
          detect_unbounded_sumifs({"addr": "K26", "formula": "=SUMIFS($B$2:$B$100,$A$2:$A$100,B5)+INDEX(A:A,3)"}) is None)
    check("full-row range in SUMIFS does not fire",
          detect_unbounded_sumifs({"addr": "K27", "formula": "=SUMIFS($B$2:$B$100,$A$2:$A$100,MATCH(C1,$1:$1,0))"}) is None)
    check("non-IFS full-col formula no-op",
          detect_unbounded_sumifs({"addr": "K28", "formula": "=VLOOKUP(B1,A:D,2,0)"}) is None)
    check("unbounded SUMIFS no formula no-op",
          detect_unbounded_sumifs({"addr": "K29", "calculatedValue": "100"}) is None)

    # ---- dead / unsupported functions
    f = detect_dead_functions({"addr": "M1", "formula": "=INDIRECT(\"Sheet1!A\"&B1)"})
    check("INDIRECT fires", f and f["kind"] == "dead-unsupported-function")
    check("dead func is high/surfaced", f and f["severity"] == "high" and f["fix_lane"] == "surfaced" and f["fixable"] is False)
    check("INDIRECT named in target", f and f["target"]["functions"] == ["INDIRECT()"])
    check("FILTER fires", detect_dead_functions({"addr": "M2", "formula": "=FILTER(A1:A9,B1:B9>0)"}))
    check("OFFSET fires", detect_dead_functions({"addr": "M3", "formula": "=OFFSET(A1,1,0)"}))
    check("ROW() fires", detect_dead_functions({"addr": "M4", "formula": "=ROW()"}))
    check("COLUMN() fires", detect_dead_functions({"addr": "M5", "formula": "=COLUMN()"}))
    check("LET fires", detect_dead_functions({"addr": "M6", "formula": "=LET(x,A1,x*2)"}))
    check("SORTBY captured canonically (not SORT)",
          detect_dead_functions({"addr": "M7", "formula": "=SORTBY(A1:A9,B1:B9)"})["target"]["functions"] == ["SORTBY()"])
    f = detect_dead_functions({"addr": "M8", "formula": "=IF(ISNUMBER(A1),TRUE(),FALSE())"})
    check("TRUE()/FALSE() call form fires", f and f["target"]["functions"] == ["FALSE()", "TRUE()"])
    check("internal-anchor HYPERLINK fires",
          detect_dead_functions({"addr": "M9", "formula": '=HYPERLINK("#Sheet1!A1","go")'})["target"]["functions"] == ['HYPERLINK("#")'])
    check("multiple dead funcs grouped sorted",
          detect_dead_functions({"addr": "M10", "formula": "=OFFSET(INDIRECT(B1),0,0)"})["target"]["functions"] == ["INDIRECT()", "OFFSET()"])
    # FP guards
    check("ROWS() supported count fn does not fire",
          detect_dead_functions({"addr": "M11", "formula": "=ROWS(A1:A10)"}) is None)
    check("COLUMNS() supported count fn does not fire",
          detect_dead_functions({"addr": "M12", "formula": "=COLUMNS(A1:D1)"}) is None)
    check("bare TRUE/FALSE literals do not fire",
          detect_dead_functions({"addr": "M13", "formula": "=IF(A1>0,TRUE,FALSE)"}) is None)
    check("INDEX/MATCH (port baseline) does not fire",
          detect_dead_functions({"addr": "M14", "formula": "=INDEX(A:A,MATCH(B1,C:C,0))"}) is None)
    check("name starting with a dead-func prefix does not fire",
          detect_dead_functions({"addr": "M15", "formula": "=OFFSETTOTAL(A1)"}) is None)
    check("function name inside a sheet ref does not fire",
          detect_dead_functions({"addr": "M16", "formula": "='SORT detail'!A1+'ROW map'!B2"}) is None)
    check("function name inside a quoted criterion does not fire",
          detect_dead_functions({"addr": "M17", "formula": '=SUMIFS(A:A,B:B,"FILTER")'}) is None)
    check("normal external HYPERLINK does not fire",
          detect_dead_functions({"addr": "M18", "formula": '=HYPERLINK("https://x.io","go")'}) is None)
    check("plain SUMIFS does not fire",
          detect_dead_functions({"addr": "M19", "formula": '=SUMIFS($AC$2:$AC$9,$A$2:$A$9,"GOV")'}) is None)
    check("dead func no formula no-op",
          detect_dead_functions({"addr": "M20", "calculatedValue": "100"}) is None)

    # ---- degenerate placeholder formula
    f = detect_degenerate_placeholder_formula({"addr": "P1", "formula": "=0"})
    check("zero placeholder fires", f and f["kind"] == "degenerate-placeholder-formula")
    check("zero placeholder is medium/surfaced", f and f["severity"] == "medium" and f["fix_lane"] == "surfaced" and f["fixable"] is False)
    f = detect_degenerate_placeholder_formula({"addr": "P2", "formula": "=1+1"})
    check("1+1 placeholder fires with value", f and f["target"]["value"] == 2.0)
    check("constant arithmetic placeholder fires",
          detect_degenerate_placeholder_formula({"addr": "P3", "formula": "=(2+2)*0"}))
    check("bare row-control 1 skipped",
          detect_degenerate_placeholder_formula({"addr": "P4", "formula": "=1"}) is None)
    check("bare row-control -1 skipped",
          detect_degenerate_placeholder_formula({"addr": "P5", "formula": "=-1"}) is None)
    check("large literal owned by constant detector",
          detect_degenerate_placeholder_formula({"addr": "P6", "formula": "=12345"}) is None)
    check("year-like literal skipped",
          detect_degenerate_placeholder_formula({"addr": "P7", "formula": "=2025"}) is None)
    check("ref formula not placeholder",
          detect_degenerate_placeholder_formula({"addr": "P8", "formula": "=A1+0"}) is None)
    check("function formula not placeholder",
          detect_degenerate_placeholder_formula({"addr": "P9", "formula": "=ROUND(0,0)"}) is None)
    check("placeholder no formula no-op",
          detect_degenerate_placeholder_formula({"addr": "P10", "calculatedValue": "0"}) is None)

    # ---- scan entry: formula-less cells produce nothing
    check("clean cells produce no findings",
          scan_formula_hygiene([
              {"addr": "A1", "calculatedValue": "#REF!"},
              {"addr": "B2", "value": "Revenue", "calculatedValue": "Revenue"},
          ]) == [])

    passed = sum(1 for _, ok in cases if ok)
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(cases)} passed")
    return passed == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)

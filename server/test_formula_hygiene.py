#!/usr/bin/env python3
"""Unit tests for formula_hygiene -- no network, no Workiva credentials required.

Includes the four fixes from the 2026-06-19 adversarial review:
  - text-criteria reads only SUMIFS criteria positions (IFERROR/IF strings never fire)
  - round-wrapper uses a paren-walk (no boundary-crossing FP)
  - round-wrapper TEXT guard narrowed to TEXT(.../1000) scale shape (no false negative)
  - mixed include+exclusion criteria labeled correctly
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pytest

import formula_hygiene as fh


# ---- detector 1: hardcoded text criteria ----

@pytest.mark.parametrize("formula,fires,severity", [
    ('=SUMIFS(A:A,B:B,"ACT-ADMIN")', True, "medium"),          # include literal
    ('=SUMIFS(A:A,B:B,"<>EXP-EQUIP")', True, "high"),         # exclusion list = high
    ('=SUMIFS($F:$F,$H:$H,"Y",$K:$K,"GOV",$B:$B,"RCL")', True, "medium"),  # adjustments triplet
    ('=COUNTIFS(A:A,"REV-*")', True, "medium"),                # wildcard include
    ('=SUMIFS($F:$F,$H:$H,"GOV",$B:$B,"<>EXP-EQUIP")', True, "high"),  # mixed -> exclusion drives high
    # positional FP guards: result strings of IFERROR / IF are NOT criteria
    ('=IFERROR(SUMIFS(A:A,B:B,$C41),"N/A")', False, None),
    ('=IFERROR(SUMIFS(A:A,B:B,$C41),"NA")', False, None),
    ('=IF(SUMIFS(A:A,B:B,$C1)>0,"YES","NO")', False, None),
    ('=IFERROR(SUMIFS(A:A,B:B,$C41),"Mapping error")', False, None),
    ('=SUMIFS($F:$F,$L:$L,"")', False, None),                  # fund-blank filter
    ('=SUMIFS(A:A,B:B,$C43)', False, None),                    # criterion is a cell ref (correct)
    ('=IF(A1>0,"YES","NO")', False, None),                     # not a SUMIFS-family function
    ('=SUMIFS(A:A,B:B,">="&DATE(2023,1,1))', False, None),     # operator+concat, not a code literal
])
def test_text_criteria(formula, fires, severity):
    f = fh.detect_text_criteria({"addr": "R10", "formula": formula})
    if fires:
        assert f is not None and f["kind"] == "hardcoded-text-criteria-in-formula"
        assert f["severity"] == severity
        assert f["fix_lane"] == "surfaced" and f["fixable"] is False
    else:
        assert f is None


def test_text_criteria_groups_by_code_set():
    f = fh.detect_text_criteria({"addr": "R40", "formula": '=SUMIFS($F:$F,$H:$H,"RCL",$K:$K,"GOV")'})
    assert f["target"]["codes"] == ["GOV", "RCL"]
    assert "GOV, RCL" in f["detail"]


def test_text_criteria_mixed_label():
    f = fh.detect_text_criteria({"addr": "R47", "formula": '=SUMIFS($F:$F,$H:$H,"GOV",$B:$B,"<>EXP-EQUIP")'})
    assert f["severity"] == "high"
    assert "include + exclusion" in f["detail"]
    assert f["target"]["exclusion"] is True


def test_text_criteria_no_formula_noop():
    assert fh.detect_text_criteria({"addr": "A1", "calculatedValue": "100"}) is None


# ---- detector 2: hardcoded numeric constant ----

@pytest.mark.parametrize("formula,fires", [
    ('=SUMIFS(B1:B100,C1:C100,"X")+12000000', True),
    ("=Total-250000", True),
    ("=12345", True),
    ("=A1+B1-12500000", True),
    ('="12000000"', False),
    ("=ROUND(A1/1000,0)", False),
    ("=ROUND(SUM(A1:A50),-3)", False),
    ("=A1-2024", False),
    ("=SUM(A12345:A20000)", False),
    ("=IF(A2>=44927,B2,0)", False),
    ("=MROUND(SUM(A1:A10),10000)", False),
    ("=PRICE(44927,55151,0.04,0.035,100,2)", False),
    ("=EDATE(B1,0)+44927", False),
    ("=A1+100", False),
    ("=A1*1.05", False),
])
def test_constant_in_formula(formula, fires):
    f = fh.detect_constant_in_formula({"addr": "D10", "formula": formula})
    if fires:
        assert f is not None and f["kind"] == "hardcoded-constant-in-formula"
        assert f["severity"] == "high" and f["fix_lane"] == "surfaced"
    else:
        assert f is None


def test_constant_captures_value():
    f = fh.detect_constant_in_formula({"addr": "D42", "formula": "=SUMIFS(A:A,B:B,$C42)+12000000"})
    assert 12000000 in f["target"]["constants"]


def test_constant_no_formula_noop():
    assert fh.detect_constant_in_formula({"addr": "A1", "calculatedValue": "12000000"}) is None


# ---- detector 3: round wrapper ----

@pytest.mark.parametrize("formula,fires", [
    ("=ROUND(SUM(A1:A50),-3)", True),
    ("=ROUNDUP(A1+B1,-6)", True),
    ("=ROUND(SUM(A1:A50)+SUM(B1:B9),-3)", True),               # nested paren, paren-walk still fires
    ('=ROUND(SUM(A:A),-3)&" "&TEXT(B1,"#,##0")', True),        # unrelated TEXT -> still fires
    ('=IFERROR(ROUND(SUM(A:A),-3),TEXT(0,"#,##0"))', True),    # TEXT fallback -> still fires
    ("=ROUND(A1/1000,0)", False),
    ("=ROUND($A1,0)+SUMIFS($B:$B,$C:$C,-5000)", False),        # boundary-crossing FP killed
    ("=ROUND(SUM(F:F),0)+SUMIFS(F:F,D:D,$C38,-250000)", False),
    ('=TEXT(ROUND(A1/1000,0),"#,##0")', False),                # TEXT(.../1000) -> display_wrapper owns it
    ("=SUM(A1:A50)", False),
])
def test_round_wrapper(formula, fires):
    f = fh.detect_round_wrapper({"addr": "D10", "formula": formula})
    if fires:
        assert f is not None and f["kind"] == "round-wrapper-workaround"
        assert f["severity"] == "medium" and f["fix_lane"] == "surfaced"
    else:
        assert f is None


# ---- detector 4: unbounded full-column SUMIFS ----

@pytest.mark.parametrize("formula,fires", [
    ('=SUMIFS($AC:$AC,$A:$A,"GOV")', True),                    # full-col sum + criteria ranges
    ("=SUMIFS(A:A,B:B,$C18)", True),                           # short-form full-col
    ("=SUMIFS('Trial Balance'!$AC:$AC,'Trial Balance'!$A:$A,B5)", True),  # sheet-qualified
    ('=COUNTIFS(A:AD,">0")', True),                            # multi-col span, COUNTIFS family
    ("=AVERAGEIFS(C:C,A:A,$B2)", True),                        # AVERAGEIFS family
    # FP guards
    ('=SUMIFS($AC$2:$AC$100,$A$2:$A$100,"GOV")', False),       # bounded ranges
    ("=SUMIFS($AC2:$AC100,$A2:$A100,B5)", False),              # bounded, no row-$
    ('=SUMIFS($B$2:$B$100,$A$2:$A$100,"A:Z")', False),         # quoted criterion that looks like a range
    ("=SUMIFS('Budget A:Z'!$B$2:$B$100,'Budget A:Z'!$A$2:$A$100,B5)", False),  # colon in sheet name
    ("=INDEX(A:A,MATCH(B1,C:C,0))", False),                    # full-col outside any IFS call
    ("=SUMIFS($B$2:$B$100,$A$2:$A$100,B5)+INDEX(A:A,3)", False),  # bounded SUMIFS, full-col only in INDEX
    ("=SUMIFS($B$2:$B$100,$A$2:$A$100,MATCH(C1,$1:$1,0))", False),  # full-ROW range, not the #VALUE risk
    ("=VLOOKUP(B1,A:D,2,0)", False),                           # not an IFS-family function
])
def test_unbounded_sumifs(formula, fires):
    f = fh.detect_unbounded_sumifs({"addr": "K10", "formula": formula})
    if fires:
        assert f is not None and f["kind"] == "unbounded-full-column-sumifs"
        assert f["severity"] == "medium" and f["fix_lane"] == "surfaced" and f["fixable"] is False
        assert f["target"] is None
    else:
        assert f is None


def test_unbounded_sumifs_stable_signature_groups_per_sheet():
    # every flagged cell shares one detail -> group_findings collapses them to one per-sheet row
    cells = [
        {"addr": "K17", "formula": '=SUMIFS($AC:$AC,$A:$A,"GOV")'},
        {"addr": "K18", "formula": "=SUMIFS(A:A,B:B,$C18)"},
        {"addr": "K19", "formula": "=COUNTIFS(C:C,$D19)"},
    ]
    findings = [f for f in fh.scan_formula_hygiene(cells)
                if f["kind"] == "unbounded-full-column-sumifs"]
    assert len(findings) == 3
    assert len({f["detail"] for f in findings}) == 1  # one signature -> one grouped row


def test_unbounded_sumifs_no_formula_noop():
    assert fh.detect_unbounded_sumifs({"addr": "K1", "calculatedValue": "100"}) is None


# ---- detector 5: dead / unsupported function ----

@pytest.mark.parametrize("formula,fires,funcs", [
    ('=INDIRECT("Sheet1!A"&B1)', True, ["INDIRECT()"]),               # dynamic reference
    ("=FILTER(A1:A9,B1:B9>0)", True, ["FILTER()"]),                   # dynamic-array spill
    ("=SORT(A1:A9)", True, ["SORT()"]),                              # spill
    ("=SORTBY(A1:A9,B1:B9)", True, ["SORTBY()"]),                    # longest-prefix captured canonically
    ("=LET(x,A1,x*2)", True, ["LET()"]),                            # LET
    ("=OFFSET(A1,1,0)", True, ["OFFSET()"]),                        # volatile positional
    ("=ROW()", True, ["ROW()"]),                                    # positional
    ("=COLUMN()", True, ["COLUMN()"]),                              # positional
    ("=IF(ISNUMBER(A1),TRUE(),FALSE())", True, ["FALSE()", "TRUE()"]),  # call-form booleans (import blocker)
    ('=HYPERLINK("#Sheet1!A1","go")', True, ['HYPERLINK("#")']),     # internal-anchor hyperlink
    ("=OFFSET(INDIRECT(B1),0,0)", True, ["INDIRECT()", "OFFSET()"]),  # multiple, sorted union
    # FP guards
    ("=ROWS(A1:A10)", False, None),                                  # supported count fn
    ("=COLUMNS(A1:D1)", False, None),                                # supported count fn
    ("=IF(A1>0,TRUE,FALSE)", False, None),                           # bare literals are supported
    ("=INDEX(A:A,MATCH(B1,C:C,0))", False, None),                    # port baseline, not dead
    ("=OFFSETTOTAL(A1)", False, None),                               # name merely starts with a dead fn
    ("='SORT detail'!A1+'ROW map'!B2", False, None),                 # function names live in sheet refs
    ('=SUMIFS(A:A,B:B,"FILTER")', False, None),                      # function name inside a quoted criterion
    ('=HYPERLINK("https://x.io","go")', False, None),                # normal external hyperlink
    ('=SUMIFS($AC$2:$AC$9,$A$2:$A$9,"GOV")', False, None),           # plain bounded SUMIFS
])
def test_dead_functions(formula, fires, funcs):
    f = fh.detect_dead_functions({"addr": "M10", "formula": formula})
    if fires:
        assert f is not None and f["kind"] == "dead-unsupported-function"
        assert f["severity"] == "high" and f["fix_lane"] == "surfaced" and f["fixable"] is False
        assert f["target"]["functions"] == funcs
        assert "#NAME?" in f["detail"]
    else:
        assert f is None


def test_dead_functions_signature_groups_by_function_set():
    # same function set -> one signature -> one grouped row; different set -> a separate row
    cells = [
        {"addr": "M1", "formula": "=FALSE()"},
        {"addr": "M2", "formula": "=IF(A1,1,FALSE())"},   # same set {FALSE()} as M1
        {"addr": "M3", "formula": "=INDIRECT(B3)"},       # different set {INDIRECT()}
    ]
    findings = [f for f in fh.scan_formula_hygiene(cells)
                if f["kind"] == "dead-unsupported-function"]
    assert len(findings) == 3
    assert len({f["detail"] for f in findings}) == 2  # FALSE() cells collapse; INDIRECT separate


def test_dead_functions_no_formula_noop():
    assert fh.detect_dead_functions({"addr": "M1", "calculatedValue": "100"}) is None


# ---- detector 6: degenerate placeholder formula ----

@pytest.mark.parametrize("formula,fires,value", [
    ("=0", True, 0.0),
    ("=0.00", True, 0.0),
    ("=1+1", True, 2.0),
    ("=100-100", True, 0.0),
    ("=(2+2)*0", True, 0.0),
    # FP guards: common intentional row controls and shapes owned by other formula lanes
    ("=1", False, None),
    ("=-1", False, None),
    ("=12345", False, None),       # large literal owned by hardcoded-constant-in-formula
    ("=2025", False, None),        # year-like literal intentionally skipped
    ("=A1+0", False, None),        # real formula with a ref
    ("=ROUND(0,0)", False, None),  # function call, not a constant-only placeholder
    ("=2^3", False, None),         # pure power constants can be legitimate support math
    ('="0"', False, None),         # string/display wrapper territory
    ("=TRUE", False, None),
    ("=1/0", False, None),         # invalid arithmetic should stay quiet
])
def test_degenerate_placeholder_formula(formula, fires, value):
    f = fh.detect_degenerate_placeholder_formula({"addr": "P10", "formula": formula})
    if fires:
        assert f is not None and f["kind"] == "degenerate-placeholder-formula"
        assert f["severity"] == "medium" and f["fix_lane"] == "surfaced" and f["fixable"] is False
        assert f["target"]["value"] == value
    else:
        assert f is None


def test_degenerate_placeholder_signature_groups_per_sheet():
    cells = [
        {"addr": "P1", "formula": "=0"},
        {"addr": "P2", "formula": "=1+1"},
        {"addr": "P3", "formula": "=100-100"},
    ]
    findings = [f for f in fh.scan_formula_hygiene(cells)
                if f["kind"] == "degenerate-placeholder-formula"]
    assert len(findings) == 3
    assert len({f["detail"] for f in findings}) == 1


def test_degenerate_placeholder_no_formula_noop():
    assert fh.detect_degenerate_placeholder_formula({"addr": "P1", "calculatedValue": "0"}) is None


# ---- scan entry ----

def test_scan_runs_all_detectors():
    cells = [
        {"addr": "R10", "formula": '=SUMIFS(A:A,B:B,"ACT-ADMIN")'},  # text-criteria + unbounded (A:A,B:B)
        {"addr": "R11", "formula": "=SUMIFS(A:A,B:B,$C11)+12000000"},  # constant + unbounded
        {"addr": "R12", "formula": "=ROUND(SUM(A1:A50),-3)"},          # round-wrapper only
        {"addr": "R13", "formula": "=SUMIFS(A:A,B:B,$C13)"},           # unbounded only (full-col ranges)
        {"addr": "R14", "formula": "=SUMIFS($AC$2:$AC$9,$A$2:$A$9,$C14)"},  # fully clean (bounded)
        {"addr": "R15", "formula": "=INDIRECT(\"GF!\"&B15)"},          # dead/unsupported function only
        {"addr": "R16", "formula": "=1+1"},                             # degenerate placeholder only
    ]
    kinds = sorted({f["kind"] for f in fh.scan_formula_hygiene(cells)})
    assert kinds == [
        "dead-unsupported-function",
        "degenerate-placeholder-formula",
        "hardcoded-constant-in-formula",
        "hardcoded-text-criteria-in-formula",
        "round-wrapper-workaround",
        "unbounded-full-column-sumifs",
    ]


def test_scan_clean_cells_empty():
    assert fh.scan_formula_hygiene([
        {"addr": "A1", "calculatedValue": "#REF!"},
        {"addr": "B2", "value": "Revenue"},
    ]) == []


def test_scan_empty():
    assert fh.scan_formula_hygiene([]) == []


def test_module_selftest_passes():
    assert fh._selftest() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

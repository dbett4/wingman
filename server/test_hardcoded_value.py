#!/usr/bin/env python3
"""Unit tests for hardcoded_value -- no network, no Workiva credentials required."""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import pytest
import hardcoded_value as hv


# ---- helpers ----

def formula_cell(addr, val="1,234", formula="=SUMIFS(TB!D:D,TB!A:A,B5)"):
    return {"addr": addr, "calculatedValue": val, "formula": formula, "type": "plainText"}


def raw_cell(addr, val="5,678"):
    return {"addr": addr, "calculatedValue": val, "type": "plainText"}


def make_col(col="C", start=10, count=8, formula=True):
    """Generate a column of cells, mostly formula-driven."""
    cells = []
    for i in range(count):
        row = start + i
        addr = f"{col}{row}"
        if formula:
            cells.append(formula_cell(addr, val=f"{(row * 1000):,}"))
        else:
            cells.append(raw_cell(addr, val=f"{(row * 1000):,}"))
    return cells


# ---- _is_numeric_value ----

@pytest.mark.parametrize("val,expected", [
    ("1,234", True),
    ("(1,234)", True),
    ("-5678", True),
    ("$1,000", True),
    ("0", True),
    ("0.00", True),
    ("-", False),
    ("—", False),
    ("", False),
    ("Revenue", False),
    ("Total assets", False),
    ("#REF!", False),
    ("(abc)", False),
])
def test_is_numeric_value(val, expected):
    assert hv._is_numeric_value(val) == expected


# ---- _has_formula ----

def test_has_formula_standard():
    assert hv._has_formula({"formula": "=SUMIFS(A:A,B:B,1)"})


def test_has_formula_in_value_field():
    assert hv._has_formula({"value": "=SUM(A1:A5)"})


def test_has_formula_none():
    assert not hv._has_formula({"formula": None, "value": "5,000"})


def test_has_formula_empty_string():
    assert not hv._has_formula({"formula": "", "value": ""})


def test_has_formula_plain_number():
    assert not hv._has_formula({"value": "12345"})


# ---- _parse_addr ----

def test_parse_addr_simple():
    assert hv._parse_addr("C10") == ("C", 10)


def test_parse_addr_multi_letter():
    assert hv._parse_addr("AB200") == ("AB", 200)


def test_parse_addr_invalid():
    assert hv._parse_addr("") is None
    assert hv._parse_addr("10C") is None
    assert hv._parse_addr("123") is None


# ---- column_formula_density ----

def test_density_all_formulas():
    cells = [formula_cell(f"C{r}", val="1,000") for r in range(8, 15)]
    cells_by_addr = {c["addr"]: c for c in cells}
    d = hv.column_formula_density("C11", cells_by_addr)
    assert d["formula_count"] >= 2
    assert d["density"] == 1.0


def test_density_no_neighbors():
    cells_by_addr = {}
    d = hv.column_formula_density("C11", cells_by_addr)
    assert d["formula_count"] == 0
    assert d["total"] == 0
    assert d["density"] == 0.0


def test_density_label_neighbors_excluded():
    """Non-numeric (label) neighbors don't count toward total."""
    cells = [
        {"addr": "C9", "calculatedValue": "Revenue", "formula": "=IF(TRUE,\"Revenue\",\"\")"},
        formula_cell("C10", val="1,000"),
        formula_cell("C12", val="2,000"),
    ]
    cells_by_addr = {c["addr"]: c for c in cells}
    d = hv.column_formula_density("C11", cells_by_addr)
    # Only 2 numeric neighbors (C10, C12), both have formulas
    assert d["total"] == 2
    assert d["formula_count"] == 2


def test_density_mixed():
    """2 formula + 1 raw -> density 0.67."""
    cells = [
        formula_cell("C9", val="1,000"),
        formula_cell("C10", val="2,000"),
        raw_cell("C12", val="3,000"),
    ]
    cells_by_addr = {c["addr"]: c for c in cells}
    d = hv.column_formula_density("C11", cells_by_addr)
    assert d["formula_count"] == 2
    assert d["total"] == 3
    assert round(d["density"], 2) == 0.67


# ---- detect_hardcoded_face_value ----

def _with_context(cell, neighbors):
    """Build cells_by_addr from neighbors and call the detector."""
    cells_by_addr = {c["addr"]: c for c in neighbors}
    return hv.detect_hardcoded_face_value(cell, cells_by_addr=cells_by_addr)


def test_detects_plain_numeric_in_formula_column():
    """Core case: raw numeric cell surrounded by formula cells -> finding."""
    neighbors = [formula_cell(f"C{r}", val=f"{r * 1000:,}") for r in [9, 10, 12, 13]]
    target = raw_cell("C11", val="5,678")
    f = _with_context(target, neighbors)
    assert f is not None
    assert f["kind"] == "hardcoded-face-value"
    assert f["addr"] == "C11"
    assert f["severity"] == "high"
    assert f["fixable"] is False
    assert f["fix_lane"] == "surfaced"
    assert "4/4" in f["detail"]


def test_skips_cell_with_formula():
    """Cell that already has a formula -> no finding."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = formula_cell("C11", val="5,678")
    f = _with_context(target, neighbors)
    assert f is None


def test_skips_richtext():
    """richText cell -> no finding."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = {"addr": "C11", "type": "richText", "calculatedValue": "5,678"}
    f = _with_context(target, neighbors)
    assert f is None


def test_skips_linked_cell():
    """Linked cell is source-controlled, not hardcoded -> no finding."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = {
        "addr": "C11", "type": "plainText",
        "calculatedValue": "5,678", "isLinked": True,
    }
    f = _with_context(target, neighbors)
    assert f is None


def test_skips_merged_cell():
    """Merged cell is layout, not data -> no finding."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = {
        "addr": "C11", "type": "plainText",
        "calculatedValue": "5,678", "isMerged": True,
    }
    f = _with_context(target, neighbors)
    assert f is None


def test_skips_text_format():
    """TEXT-format cell is intentional string data-entry -> no finding."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = {
        "addr": "C11", "type": "plainText",
        "calculatedValue": "5,678",
        "valueFormat": {"valueFormatType": "TEXT"},
    }
    f = _with_context(target, neighbors)
    assert f is None


def test_skips_blank_cell():
    """Blank/label cell with no numeric value -> no finding."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = raw_cell("C11", val="Revenue")
    f = _with_context(target, neighbors)
    assert f is None


def test_skips_when_fewer_than_2_formula_neighbors():
    """Only 1 formula neighbor -> can't prove column is formula-driven -> no finding."""
    neighbors = [
        formula_cell("C10", val="1,000"),
        raw_cell("C12", val="2,000"),
        raw_cell("C13", val="3,000"),
    ]
    target = raw_cell("C11", val="5,678")
    f = _with_context(target, neighbors)
    assert f is None


def test_skips_when_no_neighbors():
    """No cells_by_addr context -> no finding (avoids false positives)."""
    target = raw_cell("C11", val="5,678")
    f = hv.detect_hardcoded_face_value(target)
    assert f is None


def test_skips_when_cells_by_addr_empty():
    """Empty cells_by_addr -> formula_count == 0 -> no finding."""
    target = raw_cell("C11", val="5,678")
    f = hv.detect_hardcoded_face_value(target, cells_by_addr={})
    assert f is None


def test_finding_target_fields():
    """Check target metadata is present and sensible."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13, 14]]
    target = raw_cell("C11", val="5,678")
    f = _with_context(target, neighbors)
    assert f is not None
    t = f["target"]
    assert "density" in t
    assert "formula_count" in t
    assert "neighbor_total" in t
    assert t["formula_count"] == t["neighbor_total"]  # all neighbors have formulas


def test_parenthesized_negative_is_numeric():
    """(1,234) should be detected as hardcoded numeric."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = raw_cell("C11", val="(1,234)")
    f = _with_context(target, neighbors)
    assert f is not None
    assert f["kind"] == "hardcoded-face-value"


def test_zero_value_is_numeric():
    """0 / 0.00 / 0,000 should fire when column is formula-driven."""
    neighbors = [formula_cell(f"C{r}") for r in [9, 10, 12, 13]]
    target = raw_cell("C11", val="0")
    f = _with_context(target, neighbors)
    assert f is not None


# ---- scan_hardcoded_values ----

def test_scan_finds_one_outlier():
    """6 formula cells + 1 raw numeric cell -> 1 finding."""
    cells = [formula_cell(f"C{r}", val=f"{r * 1000:,}") for r in range(5, 12)]
    cells[3] = raw_cell("C8", val="25,000")  # inject hardcoded cell mid-column
    findings = hv.scan_hardcoded_values(cells)
    assert len(findings) == 1
    assert findings[0]["addr"] == "C8"
    assert findings[0]["kind"] == "hardcoded-face-value"


def test_scan_all_formula_no_findings():
    cells = [formula_cell(f"C{r}", val="1,000") for r in range(5, 12)]
    assert hv.scan_hardcoded_values(cells) == []


def test_scan_no_enrichment_no_findings():
    """Without formula enrichment all cells look raw -> no formula neighbors -> no findings."""
    cells = [raw_cell(f"C{r}", val="1,000") for r in range(5, 12)]
    assert hv.scan_hardcoded_values(cells) == []


def test_scan_empty():
    assert hv.scan_hardcoded_values([]) == []


def test_scan_multiple_columns_independent():
    """Each column is evaluated independently; hardcoded in col D doesn't fire in col C."""
    c_cells = [formula_cell(f"C{r}", val="1,000") for r in range(5, 12)]
    d_cells = [formula_cell(f"D{r}", val="2,000") for r in range(5, 12)]
    d_cells[3] = raw_cell("D8", val="5,000")  # hardcoded in col D only
    findings = hv.scan_hardcoded_values(c_cells + d_cells)
    assert all(f["addr"].startswith("D") for f in findings)
    assert len(findings) == 1


def test_scan_detects_multiple_outliers_in_column():
    """Two consecutive raw cells in a formula column -> both flagged."""
    cells = [formula_cell(f"C{r}", val="1,000") for r in range(5, 15)]
    cells[3] = raw_cell("C8", val="8,000")
    cells[4] = raw_cell("C9", val="9,000")
    findings = hv.scan_hardcoded_values(cells)
    assert len(findings) == 2
    addrs = {f["addr"] for f in findings}
    assert addrs == {"C8", "C9"}


# ---- cap-boundary guard (formula_fetched markers) ----

def test_cap_boundary_unfetched_cell_skipped():
    """
    When the scan carries formula_fetched markers (partial enrichment past the cap), a cell we
    did NOT fetch a formula for must not be flagged as a literal -- it may be a real formula cell
    beyond WINGMAN_FORMULA_FETCH_CAP whose formula was simply never retrieved.
    """
    cells = [formula_cell(f"C{r}", val="1,000") for r in range(5, 12)]
    for c in cells:
        c["formula_fetched"] = True
    # a real (but un-fetched) formula cell beyond the cap: no formula, no marker
    target = {"addr": "C8", "calculatedValue": "25,000", "type": "plainText"}  # formula_fetched absent
    cells[3] = target
    findings = hv.scan_hardcoded_values(cells)
    assert findings == []


def test_cap_boundary_fetched_literal_still_fires():
    """A genuine literal we DID fetch (marker present, formula absent) still fires."""
    cells = [formula_cell(f"C{r}", val="1,000") for r in range(5, 12)]
    for c in cells:
        c["formula_fetched"] = True
    cells[3] = {"addr": "C8", "calculatedValue": "25,000", "type": "plainText", "formula_fetched": True}
    findings = hv.scan_hardcoded_values(cells)
    assert len(findings) == 1 and findings[0]["addr"] == "C8"


def test_guard_inert_without_markers():
    """No markers anywhere -> guard is inert (existing behavior preserved)."""
    cells = [formula_cell(f"C{r}", val="1,000") for r in range(5, 12)]
    cells[3] = raw_cell("C8", val="25,000")
    findings = hv.scan_hardcoded_values(cells)
    assert len(findings) == 1 and findings[0]["addr"] == "C8"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

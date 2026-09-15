#!/usr/bin/env python3
"""
Wingman Workiva read client (stdlib-only, self-contained).

Implements the OAuth2 client-credentials flow directly rather than depending on an
external client package — the service stays standalone and pip-install-free.

Read path only: auth -> sheetdata -> normalized cells the detectors consume.
The safe WRITE path (pre-read -> apply -> readback -> revert) lives in fixer.py.

Run a live scan:
    python3 server/wk_client.py <spreadsheetId> <sheetId>
Inspect the raw sheetdata shape (keys + one sample cell):
    python3 server/wk_client.py <spreadsheetId> <sheetId> --raw
"""
from __future__ import annotations

import json
import os
import pathlib
import ssl
import sys
import urllib.parse
import urllib.request

REGIONS = {"us": "https://api.app.wdesk.com", "eu": "https://api.eu.wdesk.com", "apac": "https://api.apac.wdesk.com"}
AUTH_PATH = "/iam/v1/oauth2/token"
CLIENT_ID_ENV = "WORKIVA_CLIENT_ID"
CLIENT_SECRET_ENV = "WORKIVA_CLIENT_SECRET"


def _base():
    return REGIONS.get(os.environ.get("WORKIVA_REGION", "us").lower(), REGIONS["us"])


def _ssl_context():
    ca = os.environ.get("WORKIVA_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if ca:
        return ssl.create_default_context(cafile=os.path.expanduser(ca))
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class AuthError(RuntimeError):
    """Workiva credentials missing or unusable.

    An ordinary Exception on purpose: the service's route dispatch catches Exception and
    returns a JSON error envelope. SystemExit (BaseException) would sail past that guard
    and kill the handler thread before a response is written. CLI entry points convert
    this to a clean SystemExit themselves.
    """


def _resolve_credentials():
    cid, sec = os.environ.get(CLIENT_ID_ENV, ""), os.environ.get(CLIENT_SECRET_ENV, "")
    if cid and sec:
        return cid, sec
    candidates = [
        pathlib.Path.cwd() / ".env",
    ]
    for env_file in candidates:
        if not env_file.is_file():
            continue
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip("\"'")
            if k.strip() == CLIENT_ID_ENV:
                cid = cid or v
            elif k.strip() == CLIENT_SECRET_ENV:
                sec = sec or v
        if cid and sec:
            return cid, sec
    raise AuthError(f"NO_CREDENTIALS: set {CLIENT_ID_ENV} + {CLIENT_SECRET_ENV}")


def get_token(ctx=None):
    ctx = ctx or _ssl_context()
    cid, sec = _resolve_credentials()
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
    req = urllib.request.Request(_base() + AUTH_PATH, data=form,
                                 headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}, method="POST")
    return json.loads(urllib.request.urlopen(req, context=ctx, timeout=30).read())["access_token"]


def _get(path, token, ctx, version=None):
    # Content API endpoints are versioned via the X-Version header (NOT workiva-api-version);
    # platform/v1 endpoints take no version header. Pass version only for content calls.
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if version:
        headers["X-Version"] = version
    req = urllib.request.Request(_base() + path, headers=headers)
    return json.loads(urllib.request.urlopen(req, context=ctx, timeout=60).read())


def _get_url(url, token, ctx, version=None):
    """GET an absolute URL (e.g. an `@nextLink`). Workiva returns @nextLink as a full URL; a
    relative path is tolerated by prefixing the region base."""
    full = url if url.startswith("http") else _base() + url
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if version:
        headers["X-Version"] = version
    req = urllib.request.Request(full, headers=headers)
    return json.loads(urllib.request.urlopen(req, context=ctx, timeout=60).read())


def get_sheetdata(spreadsheet_id, sheet_id, token, ctx):
    """sheetdata endpoint — values + effectiveFormats (fontColor/backgroundColor) + calculatedValue.
    Returns ONE page; page-bounded (`$maxcellsperpage`) so large sheets (e.g. TB) return 200 instead
    of a 'response body too large' 500. This is the single-page primitive — scan_sheet/iter_sheetdata
    page through every `@nextLink` so a large sheet is scanned in full. Fixer readback uses
    get_sheetdata_cell() with `$cellrange` so any scanned address resolves, not just page 1."""
    return _get(f"/platform/v1/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/sheetdata?$maxcellsperpage=20000",
                token, ctx)


def get_sheetdata_cell(spreadsheet_id, sheet_id, addr, token, ctx):
    """Fetch sheetdata for a single A1 cell via `$cellrange` (fixer dry-run/apply/readback)."""
    a = (addr or "").strip().upper().split(":")[0]
    if not a:
        raise ValueError(f"bad A1 address: {addr!r}")
    cellrange = urllib.parse.quote(f"{a}:{a}", safe="")
    return _get(
        f"/platform/v1/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/sheetdata"
        f"?$cellrange={cellrange}&$maxcellsperpage=1",
        token, ctx,
    )


# ---- A1 helpers ----

def col_to_letters(col):  # 0-based col index -> A, B, ... AA
    s = ""
    col += 1
    while col:
        col, r = divmod(col - 1, 26)
        s = chr(65 + r) + s
    return s


def a1(row, col):  # 0-based
    return f"{col_to_letters(col)}{row + 1}"


def rc_from_a1(addr):
    """A1 -> (row, col), both 0-based. Returns None for bad addresses."""
    import re
    m = re.match(r"^([A-Z]+)([0-9]+)$", (addr or "").strip().upper())
    if not m:
        return None
    letters, digits = m.groups()
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return int(digits) - 1, col - 1


def _formula_from_content_value(val, raw_value=None):
    """2026 formulas live in rawValue; retain the legacy string-formula shape."""
    if not isinstance(val, dict):
        return None
    if val.get("type") == "formula" or "formula" in val:
        f = raw_value if val.get("type") == "formula" and isinstance(raw_value, str) else val.get("formula")
        if isinstance(f, str) and f.strip():
            s = f.strip()
            return s if s.startswith("=") else f"={s}"
    return None


FORMULA_FETCH_CAP = int(os.environ.get("WINGMAN_FORMULA_FETCH_CAP", "500"))
# Default-on but visible-range-scoped: a smaller cap so the formula-hygiene detectors fire on
# the reviewer's current sheet at ~1 extra content-cells GET, instead of being silent out of the box.
FORMULA_FETCH_SCOPED_CAP = int(os.environ.get("WINGMAN_FORMULA_FETCH_SCOPED_CAP", "200"))

_FF_OFF = ("0", "false", "no", "off")
_FF_FULL = ("1", "true", "yes", "on", "full", "all", "max")


def formula_fetch_mode():
    """Tri-state formula-text fetch: 'off' | 'scoped' (default) | 'full'.

    Open decision #1 resolved — default-on, visible-range-scoped. An unset env var
    means 'scoped': formulas are fetched for the active sheet's used range up to
    FORMULA_FETCH_SCOPED_CAP, so the formula-hygiene wave (hardcoded-*, round-wrapper-workaround,
    formula-evaluates-blank) is live out of the box at one extra content-cells GET.
    WINGMAN_FORMULA_FETCH=off keeps it silent (cost escape hatch); =on/full uses the full cap.
    """
    raw = str(os.environ.get("WINGMAN_FORMULA_FETCH", "")).strip().lower()
    if raw in _FF_OFF:
        return "off"
    if raw in _FF_FULL:
        return "full"
    return "scoped"  # unset or unrecognized → fail-open to useful, not silent


def formula_fetch_enabled():
    return formula_fetch_mode() != "off"


def formula_fetch_cap():
    """Effective per-scan formula-enrichment cap for the current mode."""
    return FORMULA_FETCH_SCOPED_CAP if formula_fetch_mode() == "scoped" else FORMULA_FETCH_CAP


def build_formula_fetch_meta(*, enabled, cell_count, enriched_count, table_id_present,
                             cap=None, mode=None):
    """Describe formula-text coverage for the extension Scan tab chip."""
    if not enabled:
        return {
            "enabled": False,
            "partial": True,
            "mode": mode or "off",
            "reason": (
                "WINGMAN_FORMULA_FETCH off — sheetdata literals only; "
                "TEXT()/wrapper heuristics need the content cells API"
            ),
        }
    cap = FORMULA_FETCH_CAP if cap is None else cap
    if not table_id_present:
        return {
            "enabled": True,
            "partial": True,
            "mode": mode or "full",
            "reason": "No content table id — formula enrichment skipped",
            "cell_count": cell_count,
        }
    capped = cell_count > cap
    meta = {
        "enabled": True,
        "partial": capped,
        "mode": mode or "full",
        "cell_count": cell_count,
        "enriched_count": enriched_count,
        "cap": cap,
    }
    if capped:
        scope = " (visible-range scope)" if mode == "scoped" else ""
        meta["reason"] = (
            f"Formula fetch capped at {cap} cells{scope} "
            f"({cell_count} scanned)"
        )
    return meta


_LINK_OFF = ("0", "false", "no", "off")


def link_fetch_enabled():
    """Document range-link awareness (ADR-0008). Default ON; WINGMAN_LINK_FETCH=off disables it.
    One extra read-only GET per sheet scan when a content table id is available."""
    return str(os.environ.get("WINGMAN_LINK_FETCH", "")).strip().lower() not in _LINK_OFF


def fetch_range_links(table_id, token, ctx, *, api_version="2026-01-01", max_pages=50):
    """GET /content/tables/{tid}/rangeLinks → normalized [{id, type, revision, table, range}].

    `range` is {startRow, stopRow, startColumn, stopColumn} pulled from the entry's `source` or
    `destination` block (verified live: source entries carry `source.range`). Pages `@nextLink`.
    Fail-open: returns [] on any error — link awareness never breaks a scan (read-only enrichment)."""
    if not table_id:
        return []
    out = []
    path = f"/content/tables/{table_id}/rangeLinks"
    pages = 0
    while path and pages < max_pages:
        try:
            raw = (_get(path, token, ctx, version=api_version) if path.startswith("/")
                   else _get_url(path, token, ctx, version=api_version))
        except Exception:
            break
        data = raw.get("data", raw if isinstance(raw, list) else []) if isinstance(raw, (dict, list)) else []
        for d in data:
            if not isinstance(d, dict):
                continue
            block = d.get("source") or d.get("destination") or {}
            rng = block.get("range") if isinstance(block, dict) else None
            out.append({
                "id": d.get("id"),
                "type": d.get("type"),
                "revision": d.get("revision"),
                "table": d.get("table"),
                "range": rng,
            })
        path = raw.get("@nextLink") if isinstance(raw, dict) else None
        pages += 1
    return out


def enrich_cells_with_formulas(cells, table_id, token, ctx, *, api_version="2026-01-01", max_cells=None, raw_cells=None):
    """
    Merge formula strings from the content cells endpoint into normalized cells.
    Only fetches the bounding box of the supplied cell list; no-op when table_id
    is missing or cells is empty. When max_cells is set, only the first N cells
    are enriched (WINGMAN_FORMULA_FETCH cap). Pass raw_cells to reuse a prior GET.
    """
    if not table_id or not cells:
        return cells, 0
    work = cells[:max_cells] if max_cells is not None and max_cells >= 0 else cells
    # Mark the cells we actually fetch a formula for. hardcoded_value's face-value detector
    # trusts only these once any marker is present: a formula cell BEYOND the cap has
    # formula=None and would otherwise look like a bare literal next to in-cap formula
    # neighbors (a false positive).
    for _c in work:
        _c["formula_fetched"] = True
    coords = []
    by_addr = {}
    for c in work:
        rc = rc_from_a1(c.get("addr"))
        if rc is None:
            continue
        by_addr[c["addr"]] = c
        coords.append(rc)
    if not coords:
        return cells, 0
    min_r = min(r for r, _ in coords)
    max_r = max(r for r, _ in coords)
    min_c = min(c for _, c in coords)
    max_c = max(c for _, c in coords)
    if raw_cells is None:
        path = (
            f"/content/tables/{table_id}/cells"
            f"?startRow={min_r}&stopRow={max_r}&startColumn={min_c}&stopColumn={max_c}"
        )
        try:
            raw = _get(path, token, ctx, version=api_version)
        except Exception:
            return cells, 0
        rows = raw.get("data", [])
    else:
        rows = raw_cells
    if not isinstance(rows, list):
        return cells, 0
    for ri, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        rc = row.get("cells")
        if not isinstance(rc, list):
            continue
        for ci, cell in enumerate(rc):
            if not isinstance(cell, dict):
                continue
            addr = a1(min_r + ri, min_c + ci)
            norm = by_addr.get(addr)
            if not norm or norm.get("formula"):
                continue
            f = _formula_from_content_value(cell.get("value"), cell.get("rawValue"))
            if f:
                norm["formula"] = f
    enriched = sum(1 for c in work if c.get("formula"))
    return cells, enriched


def type_fetch_enabled():
    return str(os.environ.get("WINGMAN_TYPE_FETCH", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def fetch_content_cell_rows(cells, table_id, token, ctx, *, max_cells=None, api_version="2026-01-01"):
    """Single bounding-box GET for content/tables cells. Returns row list or None."""
    if not table_id or not cells:
        return None
    work = cells[:max_cells] if max_cells is not None and max_cells >= 0 else cells
    coords = [rc_from_a1(c.get("addr")) for c in work]
    coords = [rc for rc in coords if rc is not None]
    if not coords:
        return None
    min_r = min(r for r, _ in coords)
    max_r = max(r for r, _ in coords)
    min_c = min(c for _, c in coords)
    max_c = max(c for _, c in coords)
    path = (
        f"/content/tables/{table_id}/cells"
        f"?startRow={min_r}&stopRow={max_r}&startColumn={min_c}&stopColumn={max_c}"
    )
    try:
        raw = _get(path, token, ctx, version=api_version)
    except Exception:
        return None
    rows = raw.get("data", [])
    return rows if isinstance(rows, list) else None


# ---- normalization: sheetdata raw -> list of detector cell dicts ----

def normalize_sheetdata(raw):
    """
    Walk the sheetdata payload into the normalized cell shape detectors.py expects.

    Confirmed live shape (2026-06-15): data.cells is a 2D array — cells[rowIdx][colIdx],
    offset by data.range.start{Row,Column}. Each cell dict = {value, calculatedValue,
    effectiveFormats:{textFormat.fontColor, cellFormat.backgroundColor}}. sheetdata does
    NOT carry cell.type, destinationLink, or the formula string — those live on the
    content/tables/{tableId}/cells endpoint and the document side. So from sheetdata:
      - contrast (fontColor vs backgroundColor) and broken-ref (calculatedValue '#...') fire;
      - type is None -> contrast findings stay 'surfaced' until a type check confirms plainText
        (merged in by the fix path before any safe-auto write);
      - blank-linked and empty-calc do not fire here (need link/formula data from elsewhere).
    """
    data = raw.get("data", raw)
    if not isinstance(data, dict):  # defensive: a list-shaped 'data' would crash .get below
        return []
    rng = data.get("range", {}) or {}
    r0, c0 = rng.get("startRow", 0), rng.get("startColumn", 0)
    out = []
    for ri, row in enumerate(data.get("cells", []) or []):
        if not isinstance(row, list):
            continue
        for ci, cell in enumerate(row):
            if not isinstance(cell, dict):
                continue
            val = cell.get("value", "")
            cval = cell.get("calculatedValue", "")
            val_s = "" if val is None else str(val)
            cval_s = "" if cval is None else str(cval)
            formula = val_s if val_s.startswith("=") else None
            # Skip blank cells. A large default-size sheet returns thousands of empty cells
            # (the unused grid); no detector can fire on a cell with neither content nor a
            # calculated value (contrast needs text, broken-ref needs an error result, and
            # links are doc-side, not here), so normalizing them is wasted work. This keeps
            # the scan — and the reported cell count — to cells that actually carry content.
            if val_s == "" and cval_s == "":
                continue
            ef = cell.get("effectiveFormats", {}) or {}
            tf = ef.get("textFormat", {}) or {}
            cf = ef.get("cellFormat", {}) or {}
            vf = ef.get("valueFormat", {}) or {}
            out.append({
                "addr": a1(r0 + ri, c0 + ci),
                "type": cell.get("type"),            # absent in sheetdata -> None
                "value": val_s,
                "calculatedValue": cval_s,
                "formula": formula,                  # set when sheetdata value is formula text
                "fontColor": tf.get("fontColor"),
                "backgroundColor": cf.get("backgroundColor"),
                "valueFormat": vf if vf else None,
                "useParensForNegatives": vf.get("useParensForNegatives"),
                "valueFormatType": vf.get("valueFormatType"),
                "isMerged": bool(cell.get("merges")),
                "isLinked": False,                   # links are doc-side, not in sheetdata
                "linkCachedEmpty": False,
            })
    return out


MAX_SCAN_PAGES = 25  # ~25 * 20k = 500k cells; a backstop so a pathological sheet can't run away


def iter_sheetdata(spreadsheet_id, sheet_id, token, ctx, max_pages=MAX_SCAN_PAGES):
    """Yield each sheetdata page (raw dict), following Workiva's top-level `@nextLink`. Bounded by
    max_pages. Each page carries its own ABSOLUTE `range.startRow/startColumn` (verified live:
    page 2 of a 769-row page starts at row 769, not 0 — unlike the Values API, whose offsets are
    range-relative), so normalize_sheetdata addresses every page correctly without a running
    counter."""
    raw = get_sheetdata(spreadsheet_id, sheet_id, token, ctx)
    yield raw
    nxt = raw.get("@nextLink")
    pages = 1
    while nxt and pages < max_pages:
        raw = _get_url(nxt, token, ctx)
        pages += 1
        yield raw
        nxt = raw.get("@nextLink")


def scan_sheet(spreadsheet_id, sheet_id, token=None, ctx=None, max_pages=MAX_SCAN_PAGES,
               *, vision=False, cell_images=None, table_id=None):
    """Scan a whole sheet, paging through every `@nextLink` page (not just the first 20k cells).
    `truncated` is True only when the page cap was hit with pages still remaining — i.e. the scan
    is genuinely incomplete — not merely because the sheet spans multiple pages.

    When `table_id` is supplied, formula strings are merged from the content cells endpoint
    so display-wrapper detection can run on live API scans (Lane A).

    When `vision` is True and `cell_images` is a {A1: base64} map from the extension DOM layer,
    merges pixel findings (richText contrast, clipped text) after the API detectors run."""
    from detectors import apply_vision_layer, scan_cells
    ctx = ctx or _ssl_context()
    token = token or get_token(ctx)  # reuse the caller's cached token (service) or mint one (CLI)
    cells, last = [], None
    for raw in iter_sheetdata(spreadsheet_id, sheet_id, token, ctx, max_pages=max_pages):
        cells.extend(normalize_sheetdata(raw))
        last = raw
    formula_meta = None
    type_meta = None
    ff_on = formula_fetch_enabled()
    tf_on = type_fetch_enabled()
    enriched_f = enriched_t = 0
    if table_id and (ff_on or tf_on):
        eff_cap = formula_fetch_cap()
        cap = eff_cap if len(cells) > eff_cap else None
        rows = fetch_content_cell_rows(cells, table_id, token, ctx, max_cells=cap)
        if rows is not None:
            if ff_on:
                _, enriched_f = enrich_cells_with_formulas(
                    cells, table_id, token, ctx, max_cells=cap, raw_cells=rows,
                )
            if tf_on:
                from type_enrich import enrich_cells_with_types

                _, enriched_t = enrich_cells_with_types(
                    cells, table_id, token, ctx, max_cells=cap, raw_cells=rows,
                )
        formula_meta = build_formula_fetch_meta(
            enabled=ff_on,
            cell_count=len(cells),
            enriched_count=enriched_f,
            table_id_present=True,
            cap=eff_cap,
            mode=formula_fetch_mode(),
        )
        from type_enrich import build_type_fetch_meta

        type_meta = build_type_fetch_meta(
            enabled=tf_on,
            cell_count=len(cells),
            enriched_count=enriched_t,
            table_id_present=True,
        )
    else:
        formula_meta = build_formula_fetch_meta(
            enabled=ff_on,
            cell_count=len(cells),
            enriched_count=0,
            table_id_present=bool(table_id),
            cap=formula_fetch_cap(),
            mode=formula_fetch_mode(),
        )
        from type_enrich import build_type_fetch_meta

        type_meta = build_type_fetch_meta(
            enabled=tf_on,
            cell_count=len(cells),
            enriched_count=0,
            table_id_present=bool(table_id),
        )
    findings = scan_cells(cells)
    vision_meta = None
    if vision:
        cells_by_addr = {c["addr"]: c for c in cells}
        findings, vision_meta = apply_vision_layer(findings, cell_images, cells_by_addr)
    truncated = bool(last and last.get("@nextLink"))  # stopped at the cap with more pages left
    link_meta = None
    if table_id and link_fetch_enabled():
        import link_lane
        links = fetch_range_links(table_id, token, ctx)
        link_findings, link_meta = link_lane.analyze_links(cells, links, truncated=truncated)
        findings = list(findings) + link_findings
    return cells, findings, truncated, vision_meta, formula_meta, type_meta, link_meta


def list_sheets(spreadsheet_id, token, ctx):
    """[{id, name}] for every sheet in the workbook, in workbook order. Uses the CONTENT sheets
    list (X-Version header) — the same endpoint that resolves table ids; platform/v1 does not
    return sheet names."""
    raw = _get(f"/spreadsheets/{spreadsheet_id}/sheets?$maxperpage=500", token, ctx, version="2026-01-01")
    lst = raw.get("data", raw if isinstance(raw, list) else [])
    return [{"id": s.get("id"), "name": s.get("name")} for s in lst if s.get("id")]


def _sheet_table_ids(spreadsheet_id, token, ctx):
    """Map sheet id -> content table id (opaque token)."""
    raw = _get(f"/spreadsheets/{spreadsheet_id}/sheets?$maxperpage=500", token, ctx, version="2026-01-01")
    lst = raw.get("data", raw if isinstance(raw, list) else [])
    out = {}
    for s in lst:
        sid = s.get("id")
        tbl = (s.get("table") or {}).get("table")
        if sid and tbl:
            out[sid] = tbl
    return out


def scan_workbook(spreadsheet_id, token=None, ctx=None, max_sheets=60):
    """
    Scan every sheet in the workbook and roll the findings up. Reuses the per-sheet path
    (scan_sheet + the same detectors), so it inherits the single-sheet honesty: large sheets
    are page-bounded (`truncated`) and a sheet that errors is reported, never silently dropped.

    Returns {spreadsheetId, sheetCount, scanned, truncatedSheets, findingTotal, totals,
    sheets:[{sheetId, name, cellCount, truncated, findingCount, groups, error}]}.
    Sequential by design: one Workiva token, modest sheet counts, and a predictable load.
    """
    from detectors import group_findings
    import format_lane
    ctx = ctx or _ssl_context()
    token = token or get_token(ctx)
    sheets = list_sheets(spreadsheet_id, token, ctx)
    table_ids = _sheet_table_ids(spreadsheet_id, token, ctx)
    scan_list = sheets[:max_sheets]
    out_sheets, totals, finding_total = [], {}, 0
    for s in scan_list:
        sid, name = s["id"], s.get("name")
        entry = {"sheetId": sid, "name": name, "cellCount": 0, "truncated": False,
                 "findingCount": 0, "groups": [], "error": None}
        try:
            cells, findings, truncated, _vm, _fm, _tm, _lm = scan_sheet(
                spreadsheet_id, sid, token, ctx, table_id=table_ids.get(sid))
            entry["cellCount"] = len(cells)
            entry["truncated"] = truncated
            entry["groups"] = group_findings(findings, {c["addr"]: c for c in cells})
            format_lane.attach_gated_format_targets(entry["groups"], cells)
            # Drop no-evidence format-consistency noise, then roll counts up from the SURVIVING
            # groups so findingCount / totals / findingTotal stay honest (ADR-0004).
            entry["groups"], _dropped = format_lane.drop_no_evidence_format_groups(entry["groups"])
            sheet_count = 0
            for g in entry["groups"]:
                gc = int(g.get("count") or len(g.get("addrs") or []))
                totals[g["kind"]] = totals.get(g["kind"], 0) + gc
                sheet_count += gc
            entry["findingCount"] = sheet_count
            finding_total += sheet_count
        except Exception as e:  # one bad sheet must not sink the whole workbook scan
            entry["error"] = repr(e)
        out_sheets.append(entry)
    return {
        "spreadsheetId": spreadsheet_id,
        "sheetCount": len(sheets),
        "scanned": len(scan_list),
        "truncatedSheets": len(sheets) > len(scan_list),
        "findingTotal": finding_total,
        "totals": totals,
        "sheets": out_sheets,
    }


def _summarize_raw(raw):
    """Print structure + one sample cell so the live shape can be confirmed."""
    data = raw.get("data", raw)
    print("top-level keys:", list(raw.keys()))
    print("data keys:", list(data.keys()) if isinstance(data, dict) else type(data))
    print("range:", data.get("range") if isinstance(data, dict) else None)
    sample = None
    rows = data.get("rows") if isinstance(data, dict) else None
    if rows:
        print("rows: list len", len(rows), "| first row keys:", list(rows[0].keys()) if isinstance(rows[0], dict) else type(rows[0]))
        first = rows[0]
        rc = first.get("cells") if isinstance(first, dict) else first
        if isinstance(rc, dict) and rc:
            sample = next(iter(rc.values()))
        elif isinstance(rc, list) and rc:
            sample = rc[0]
    elif isinstance(data, dict) and data.get("cells"):
        sample = data["cells"][0]
    print("SAMPLE CELL:\n", json.dumps(sample, indent=2)[:1500] if sample else "(none found)")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        raise SystemExit("usage: wk_client.py <spreadsheetId> <sheetId> [--raw]")
    ss, sh = args[0], args[1]
    ctx = _ssl_context()
    try:
        tok = get_token(ctx)
    except AuthError as e:
        raise SystemExit(str(e))  # clean one-line exit for CLI use; the service catches AuthError itself
    raw = get_sheetdata(ss, sh, tok, ctx)
    if "--raw" in sys.argv:
        _summarize_raw(raw)
    else:
        from detectors import group_findings
        cells, findings, truncated, vm, _fm, _tm, _lm = scan_sheet(ss, sh)
        grouped = group_findings(findings, {c["addr"]: c for c in cells})
        vnote = f" vision={vm}" if vm else ""
        print(f"scanned {len(cells)} cells{' (partial, large sheet)' if truncated else ''}; "
              f"{len(findings)} findings in {len(grouped)} group(s){vnote}")
        for g in grouped:
            tgt = f" -> fix {g['target']['hex']} ({g['target']['ratio']}:1)" if g.get("target") else ""
            shown = ", ".join(g["addrs"][:8]) + (f" +{g['count'] - 8} more" if g["count"] > 8 else "")
            print(f"  [{g['fix_lane']}] {g['kind']} x{g['count']}: {g['signature']}{tgt}")
            print(f"        cells: {shown}")

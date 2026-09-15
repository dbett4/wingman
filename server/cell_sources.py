"""Direct source evidence for Inspect, not a dependency graph or accounting verdict.

Range-link contract: developers.workiva.com/2026-01-01/content-rangelinks-guide.html
Destinations occupy their entire table. source.range is zero-based and inclusive.
Source revisions identify publication, not proof of current downstream correctness.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlsplit

import wk_client as wk


_CELL = r"\$?[A-Z]{1,3}\$?[1-9][0-9]{0,6}"
_QUALIFIER = r"(?:'(?:[^']|'')+'|[A-Z_][A-Z0-9_.]*)!"
_REFERENCE = re.compile(
    rf"(?:{_QUALIFIER})?(?:{_CELL}(?::{_CELL})?|\$?[A-Z]{{1,3}}:\$?[A-Z]{{1,3}}|\$?[1-9][0-9]*:\$?[1-9][0-9]*)"
    r"(?![A-Z0-9_.!\[:#])", re.I,
)
_TOKENS = re.compile(
    r'(?P<string>"(?:[^"]|"")*")'
    r"|(?P<external>\[[^\]]*\][^+*/^&=<>(),;\s]*)"
    r"|(?P<structured>[A-Z_][A-Z0-9_.]*\[(?:[^\[\]]|\[[^\]]*\])*\])"
    r"|(?P<function>[A-Z_][A-Z0-9_.]*\s*(?=\())"
    r"|(?P<number>(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:E[+-]?[0-9]+)?)(?![0-9:])",
    re.I,
)


def formula_references(content):
    """Tokenize supported static A1 notation; never evaluate or fetch precedents.

    Strings/function names are not references. Preserve unresolved notation rather
    than extracting a misleading A1 suffix from an external/structured/3-D reference.
    This deliberately reports text_only even if every token is recognized.
    """
    if content.get("status") != "observed":
        return {"status": "unavailable"}
    formula = content.get("formula")
    if not formula:
        return {"status": "not_formula"}
    references, numbers, unresolved = [], [], []
    pos = 1 if formula.startswith("=") else 0
    while pos < len(formula):
        token = _TOKENS.match(formula, pos)
        if token:
            text, kind = token.group(), token.lastgroup
            if kind == "number":
                numbers.append(text)
            elif kind in ("external", "structured"):
                unresolved.append(text)
            elif kind == "function" and text.strip().upper() in ("INDIRECT", "OFFSET", "INDEX"):
                unresolved.append(text.strip() + "(…) — computed reference not resolved")
            pos = token.end()
            continue
        reference = _REFERENCE.match(formula, pos)
        if reference:
            text = reference.group()
            # Quoted external and 3-D qualifiers are not local sheet references.
            qualifier = text.rsplit("!", 1)[0] if "!" in text else ""
            if ":" in qualifier or "[" in qualifier:
                unresolved.append(text)
            else:
                references.append(text)
            pos = reference.end()
            continue
        if formula[pos].isspace() or formula[pos] in "+-*/^&=<>(),;%{}":
            pos += 1
            continue
        unknown = re.match(r"[^+*/^&=<>(),;%{}\s]+", formula[pos:])
        text = unknown.group() if unknown else formula[pos]
        if text.upper() not in ("TRUE", "FALSE"):
            unresolved.append(text)
        pos += len(text)
    return {"status": "text_only", "references": list(dict.fromkeys(references)),
            "literalNumbers": list(dict.fromkeys(numbers)),
            "unresolved": list(dict.fromkeys(unresolved))}


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Missing range-link identity")
    return value


def _bounds(source):
    bounds = source["range"]
    r0, r1, c0, c1 = [bounds[k] for k in ("startRow", "stopRow", "startColumn", "stopColumn")]
    if (any(type(v) is not int or not 0 <= v < 10_000_000 for v in (r0, r1, c0, c1))
            or r0 > r1 or c0 > c1):
        raise ValueError("Invalid source range")
    return r0, r1, c0, c1


def _range_text(bounds):
    r0, r1, c0, c1 = bounds
    start, stop = wk.a1(r0, c0), wk.a1(r1, c1)
    return start if start == stop else start + ":" + stop


def range_links(table_id, addr, token, ctx):
    """Read all table link pages or mark incomplete; only return links covering addr.

    Never use the legacy scan reader here: it returns [] on a failed page. Resolve
    destination ranges at their reported source revision, never at latest revision.
    No destination enumeration, source cell values, or writes are performed.
    """
    items = []
    try:
        table_id = _identity(table_id)
        row, col = wk.rc_from_a1(addr)
        path = f"/content/tables/{quote(table_id, safe='')}/rangeLinks"
        seen_paths, seen_ids = set(), set()
        for _ in range(50):
            if not isinstance(path, str):
                raise ValueError("Invalid pagination URL")
            url, base = urlsplit(path), urlsplit(wk._base())
            if ((url.netloc and url.netloc != base.netloc)
                    or (url.scheme and url.scheme != base.scheme) or path in seen_paths):
                raise ValueError("Untrusted or repeated pagination URL")
            seen_paths.add(path)
            page = wk._get_url(path, token, ctx, version="2026-01-01")
            if not isinstance(page.get("data"), list):
                raise ValueError("Incomplete range-link list")
            for link in page["data"]:
                identity = _identity(link["id"])
                if link["table"] != table_id or identity in seen_ids:
                    raise ValueError("Wrong table or repeated range link")
                seen_ids.add(identity)
                if link["type"] == "source":
                    bounds = _bounds(link["source"])
                    r0, r1, c0, c1 = bounds
                    if r0 <= row <= r1 and c0 <= col <= c1:
                        items.append({"direction": "source", "id": identity,
                                      "range": _range_text(bounds), "revision": link.get("revision")})
                elif link["type"] == "destination":
                    source = link["destination"]["source"]
                    items.append({"direction": "destination", "id": identity, "source": {
                        key: _identity(source[key]) for key in ("table", "rangeLink", "revision")},
                        "resolution": "not_inspected"})
                else:
                    raise ValueError("Unknown range-link direction")
            path = page.get("@nextLink")
            if path is None or path == "":
                break
        else:
            raise ValueError("Range-link pagination limit reached")
    except Exception:
        # Do not expose upstream diagnostics or convert failed reads into absence.
        return {"status": "partial" if items else "unavailable", "items": items}

    for item in items:
        if item["direction"] != "destination":
            continue
        source = item["source"]
        try:
            path = (f"/content/tables/{quote(source['table'], safe='')}/rangeLinks/"
                    f"{quote(source['rangeLink'], safe='')}?{urlencode({'$revision': source['revision']})}")
            raw = wk._get(path, token, ctx, version="2026-01-01")
            if (raw["type"] != "source" or raw["id"] != source["rangeLink"]
                    or raw["table"] != source["table"] or raw["revision"] != source["revision"]):
                raise ValueError("Source identity or revision mismatch")
            item.update(resolution="observed", sourceRange=_range_text(_bounds(raw["source"])))
        except Exception:
            item["resolution"] = "unavailable"
    return {"status": "observed", "items": items}

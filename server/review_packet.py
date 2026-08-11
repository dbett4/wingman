#!/usr/bin/env python3
"""
Wingman scan -> review packet (F1).

Closes the "Wingman finds issues but leaves no durable artifact" gap. Takes a
prioritized scan queue (diagnose.build_sheet_queue / build_workbook_queue, with or
without merged run_checks rows) and produces a structured, redacted handoff packet:
each finding is classified ACCEPT / BLOCKED / UNVERIFIED, carries its capability tier
(safe-auto | guided | surfaced) and a per-finding rollback path, and the packet rolls
up an overall verdict. The packet is designed to attach to an external task tracker or hand
to a verifier — Wingman never creates the external task itself (no external send).

Redaction (mirrors the wingman_log.py data boundary):
  - The raw Workiva spreadsheet id is pseudonymized to a SHA-256 `workbookHash`.
  - Client cell VALUES never enter the packet (cellValues is dropped; signatures have
    their numeric runs redacted to `#`).
  - Structural coordinates (A1 addrs) and sheet names ARE retained — the packet's whole
    purpose is to be actionable for a verifier; this is a deliberate, documented boundary
    distinct from the telemetry log (which pseudonymizes everything).

Pure logic — no network, no Workiva. Unit-tested without creds.

Public API:
  build_review_packet(queue, *, label=None, generated_at=None, addr_cap=25, redact=True)
  render_packet_md(packet) -> str
  write_packet(packet, *, out_dir=None, markdown=None) -> {"jsonPath","mdPath"}
  classify_disposition(item) -> "ACCEPT"|"BLOCKED"|"UNVERIFIED"
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

ACCEPT = "ACCEPT"
BLOCKED = "BLOCKED"
UNVERIFIED = "UNVERIFIED"
DISPOSITIONS = (ACCEPT, BLOCKED, UNVERIFIED)

# Overall verdict precedence (most-gating wins): a single BLOCKED finding gates the packet.
_VERDICT_ORDER = {BLOCKED: 3, UNVERIFIED: 2, ACCEPT: 1}

# Surfaced, non-fixable kinds that are nonetheless CONFIRMED defects (not heuristics) — they
# need a human action Wingman will not auto-do, so they belong in BLOCKED, not UNVERIFIED.
_BLOCKED_KINDS = frozenset({"broken-ref", "scan-error"})

CLIENT_READY_CAVEAT = (
    "A scan-derived packet is never client-ready proof. ACCEPT items still require the "
    "safe-auto write + readback; BLOCKED/UNVERIFIED items require human action and, for "
    "any client-visible delivery, a published export / rendered-PDF proof (tieout or "
    "revision agreement alone is not client-ready)."
)

# A numeric run carries client financial data; a #RRGGBB hex token (low-contrast findings)
# does not. Match both with the hex alternative FIRST so it is preserved while numbers redact.
_NUM_OR_HEX = re.compile(r"(#[0-9A-Fa-f]{3,8}\b)|([-+(]?\$?\d[\d,]*(?:\.\d+)?%?\)?)")


def _redact_numbers(text: str | None) -> str | None:
    """Redact numeric runs to `#` (client values) while preserving hex color tokens."""
    if not text:
        return text
    return _NUM_OR_HEX.sub(lambda m: m.group(1) if m.group(1) is not None else "#", str(text))


def _workbook_hash(spreadsheet_id: str | None) -> str:
    """Pseudonymous, stable per-workbook key — the raw Workiva id never leaves the machine."""
    return hashlib.sha256(f"{spreadsheet_id or ''}".encode()).hexdigest()[:16]


def classify_disposition(item: dict[str, Any]) -> str:
    """Classify one queue item.

    ACCEPT     — Wingman can fix it itself, safely and reversibly (safe-auto lane).
    BLOCKED    — a known remediation that needs human authority / source / a Workiva write
                 Wingman will not auto-do: guided fixes, fixable-but-not-safe-auto, run_checks
                 / tieout exceptions, anything carrying a guided lane, and confirmed
                 error-class defects (broken-ref, scan-error).
    UNVERIFIED — a surfaced read-only heuristic with no fix and no guided remediation; a
                 verifier must confirm it against the live source/export before acting.
    """
    fix_lane = item.get("fix_lane") or "surfaced"
    fixable = bool(item.get("fixable"))
    kind = item.get("kind") or ""
    dx = item.get("diagnosis") or {}
    if fixable and fix_lane == "safe-auto":
        return ACCEPT
    if (
        fix_lane == "guided"
        or fixable
        or item.get("check")
        or dx.get("guided_steps")
        or dx.get("guided_lane")
        or kind in _BLOCKED_KINDS
    ):
        return BLOCKED
    return UNVERIFIED


def disposition_reason(item: dict[str, Any], disposition: str) -> str:
    kind = item.get("kind") or ""
    dx = item.get("diagnosis") or {}
    if disposition == ACCEPT:
        return "Safe-auto fix — Wingman applies with readback + automatic revert on mismatch."
    if disposition == BLOCKED:
        if kind == "scan-error":
            return "Sheet scan failed — restore access / clear the lock before review."
        if item.get("check"):
            return "Tie-out / check exception — reconcile at source; Wingman never auto-writes tie-out."
        if dx.get("guided_lane") == "export-proof":
            return "Source fix is not client-visible — guided publish + export-proof required."
        if dx.get("guided_steps") or dx.get("guided_lane"):
            return "Guided fix — a human runs the checklist; no Wingman auto-write."
        if kind == "broken-ref":
            return "Formula error — needs manual formula repair; no safe auto-fix."
        if bool(item.get("fixable")):
            return "Fix available but outside the safe-auto lane — needs confirmation before write."
        return "Needs human action before any write."
    return "Surfaced heuristic — verify against the live source / export before acting."


def rollback_path(item: dict[str, Any], disposition: str) -> str:
    kind = item.get("kind") or ""
    addrs = item.get("addrs") or []
    if disposition == ACCEPT:
        where = f" of {addrs[0]}" if addrs else ""
        return (
            f"Automatic: the fixer captures the pre-fix value{where}, writes one cell, reads it "
            "back, and reverts to the prior value on any mismatch (ADR-0002/0003 safe-auto lane)."
        )
    if disposition == BLOCKED:
        if kind == "scan-error":
            return "n/a — no change proposed; resolve sheet access first."
        if item.get("check") or (item.get("diagnosis") or {}).get("guided_lane") == "export-proof":
            return "No Wingman write performed — revert any manual source change via Workiva version history."
        return (
            "No Wingman write performed — if a human applies the fix, revert via Workiva version "
            "history or the guided checklist."
        )
    return "n/a — surfaced only, no change proposed."


def _redact_finding(item: dict[str, Any], *, addr_cap: int, redact: bool) -> dict[str, Any]:
    """Curated, redacted handoff record for one queue item."""
    disposition = classify_disposition(item)
    dx = item.get("diagnosis") or {}
    addrs = list(item.get("addrs") or [])
    signature = item.get("signature")
    if redact:
        signature = _redact_numbers(signature)
    finding: dict[str, Any] = {
        "id": item.get("id"),
        "kind": item.get("kind"),
        "severity": item.get("severity"),
        "capabilityTier": item.get("fix_lane") or "surfaced",
        "fixable": bool(item.get("fixable")),
        "disposition": disposition,
        "dispositionReason": disposition_reason(item, disposition),
        "count": item.get("count"),
        "addrCount": len(addrs),
        "addrs": addrs[:addr_cap],
        "addrsTruncated": len(addrs) > addr_cap,
        "sheetName": item.get("sheetName"),
        "sheetId": item.get("sheetId"),
        "signature": signature,
        "pathwayId": dx.get("pathway_id"),
        "title": dx.get("title"),
        "suggestedAction": dx.get("suggested_action"),
        "trapRefs": list(dx.get("trap_refs") or []),
        "fixRefs": list(dx.get("fix_refs") or []),
        "rollback": rollback_path(item, disposition),
    }
    if item.get("check"):
        finding["check"] = item.get("check")
    if item.get("tieout_status"):
        finding["tieoutStatus"] = item.get("tieout_status")
    return finding


def _count_by(findings: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        v = str(f.get(key) or "unknown")
        out[v] = out.get(v, 0) + 1
    return out


def _coverage_from_queue(queue: dict[str, Any]) -> dict[str, Any]:
    """Summarize incomplete scan/enrichment surfaces so packets cannot look fully green.

    This is intentionally conservative: partial sheet scans, skipped formula/type/link enrichment,
    or enrichment errors become explicit coverage warnings. They do not invent findings, but they
    prevent a clean-looking packet from being treated as complete proof.
    """
    warnings: list[str] = []
    if queue.get("truncated"):
        warnings.append("sheet scan truncated; only the scanned page was evaluated")
    for key, label in (("formula_fetch", "formula enrichment"), ("type_fetch", "cell-type enrichment"), ("link_fetch", "link enrichment")):
        meta = queue.get(key) or {}
        if not isinstance(meta, dict) or not meta:
            continue
        if meta.get("error"):
            warnings.append(f"{label} error: {meta.get('error')}")
        skipped = meta.get("skipped") or meta.get("skip_reason")
        if skipped:
            warnings.append(f"{label} skipped: {skipped}")
        if meta.get("truncated") or meta.get("partial"):
            warnings.append(f"{label} partial/truncated")
        if meta.get("applied") is False and not skipped and not meta.get("error"):
            warnings.append(f"{label} not applied")
    checks = queue.get("checks") or {}
    if isinstance(checks, dict) and checks.get("error"):
        warnings.append(f"checks error: {checks.get('error')}")
    return {"complete": not warnings, "warnings": warnings}


def build_review_packet(
    queue: dict[str, Any],
    *,
    label: str | None = None,
    generated_at: str | None = None,
    addr_cap: int = 25,
    redact: bool = True,
) -> dict[str, Any]:
    """Build a structured, redacted ACCEPT/BLOCKED/UNVERIFIED review packet from a scan queue."""
    items = list(queue.get("items") or [])
    findings = [_redact_finding(it, addr_cap=addr_cap, redact=redact) for it in items]

    buckets: dict[str, list[dict[str, Any]]] = {ACCEPT: [], BLOCKED: [], UNVERIFIED: []}
    for f in findings:
        buckets[f["disposition"]].append(f)

    coverage = _coverage_from_queue(queue)
    overall = "CLEAN"
    for f in findings:
        if _VERDICT_ORDER[f["disposition"]] > _VERDICT_ORDER.get(overall, 0):
            overall = f["disposition"]
    if not coverage.get("complete") and overall == "CLEAN":
        overall = UNVERIFIED

    summary = {
        "total": len(findings),
        "byDisposition": {d: len(buckets[d]) for d in DISPOSITIONS},
        "bySeverity": _count_by(findings, "severity"),
        "byCapabilityTier": _count_by(findings, "capabilityTier"),
        "highSeverity": sum(1 for f in findings if f.get("severity") == "high"),
    }

    return {
        "artifact": "wingman-review-packet",
        "schema": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "label": label,
        "scope": queue.get("scope"),
        "workbookHash": _workbook_hash(queue.get("spreadsheetId")),
        "sheetId": queue.get("sheetId"),
        "sheetName": queue.get("sheetName"),
        "sheetCount": queue.get("sheetCount"),
        "redacted": bool(redact),
        "overall": overall,
        "clientReady": False,
        "caveat": CLIENT_READY_CAVEAT,
        "summary": summary,
        "checks": queue.get("checks"),
        "coverage": coverage,
        "dispositions": {
            ACCEPT: buckets[ACCEPT],
            BLOCKED: buckets[BLOCKED],
            UNVERIFIED: buckets[UNVERIFIED],
        },
    }


def _addr_str(finding: dict[str, Any]) -> str:
    addrs = finding.get("addrs") or []
    if not addrs:
        return "—"
    shown = ", ".join(addrs)
    if finding.get("addrsTruncated"):
        shown += f", … (+{finding['addrCount'] - len(addrs)} more)"
    return shown


def render_packet_md(packet: dict[str, Any]) -> str:
    """Human-readable markdown rendering of the packet (BLOCKED first — act on these)."""
    title = packet.get("label") or f"workbook {packet.get('workbookHash')}"
    lines = [
        f"# Wingman review packet — {title}",
        "",
        f"- **Overall:** {packet.get('overall')}  ·  **Client-ready:** "
        f"{'yes' if packet.get('clientReady') else 'no'}",
        f"- **Scope:** {packet.get('scope')}"
        + (f" · sheet `{packet['sheetName']}`" if packet.get("sheetName") else "")
        + (f" · {packet['sheetCount']} sheets" if packet.get("sheetCount") else ""),
        f"- **Workbook:** `{packet.get('workbookHash')}` (pseudonymous)"
        + (f"  ·  **Generated:** {packet['generatedAt']}" if packet.get("generatedAt") else ""),
    ]
    s = packet.get("summary") or {}
    bd = s.get("byDisposition") or {}
    lines.append(
        f"- **Findings:** {s.get('total', 0)} "
        f"(ACCEPT {bd.get(ACCEPT, 0)} · BLOCKED {bd.get(BLOCKED, 0)} · "
        f"UNVERIFIED {bd.get(UNVERIFIED, 0)}; high-severity {s.get('highSeverity', 0)})"
    )
    coverage = packet.get("coverage") or {}
    if coverage:
        lines.append("- **Coverage:** " + ("complete" if coverage.get("complete") else "partial / needs verification"))
        for warn in coverage.get("warnings") or []:
            lines.append(f"  - {warn}")
    lines += ["", f"> {packet.get('caveat')}", ""]

    sections = [
        (BLOCKED, "🛑 BLOCKED — needs human action / source / authority"),
        (UNVERIFIED, "❓ UNVERIFIED — verify against live source before acting"),
        (ACCEPT, "✅ ACCEPT — Wingman can apply (safe-auto + readback/revert)"),
    ]
    dispositions = packet.get("dispositions") or {}
    for key, header in sections:
        findings = dispositions.get(key) or []
        lines.append(f"## {header} ({len(findings)})")
        if not findings:
            lines += ["", "_none_", ""]
            continue
        lines.append("")
        for f in findings:
            sheet = f.get("sheetName") or f.get("sheetId") or "—"
            lines.append(
                f"- **[{f.get('severity')}] {f.get('kind')}** · tier `{f.get('capabilityTier')}` "
                f"· sheet `{sheet}` · cells: {_addr_str(f)}"
            )
            if f.get("title"):
                lines.append(f"  - {f['title']}")
            if f.get("dispositionReason"):
                lines.append(f"  - _Why:_ {f['dispositionReason']}")
            if f.get("suggestedAction"):
                lines.append(f"  - _Action:_ {f['suggestedAction']}")
            lines.append(f"  - _Rollback:_ {f.get('rollback')}")
            refs = (f.get("fixRefs") or []) + (f.get("trapRefs") or [])
            if refs:
                lines.append(f"  - _Refs:_ {', '.join(str(r) for r in refs)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def packet_dir() -> Path:
    """Where packets are written. Outside the repo by default; never committed."""
    return Path(os.environ.get("WINGMAN_PACKET_DIR", str(Path.home() / ".wingman" / "packets")))


def _slug(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-") or "packet"


def write_packet(
    packet: dict[str, Any],
    *,
    out_dir: str | os.PathLike | None = None,
    markdown: str | None = None,
) -> dict[str, str]:
    """Persist the packet as redacted JSON (+ markdown) to a local dir. Returns the paths."""
    d = Path(out_dir) if out_dir else packet_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = re.sub(r"[^0-9A-Za-z]", "", str(packet.get("generatedAt") or ""))[:15] or "now"
    base = f"wingman-packet-{packet.get('workbookHash', 'wb')}-{stamp}"
    json_path = d / f"{base}.json"
    md_path = d / f"{base}.md"
    json_path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(markdown if markdown is not None else render_packet_md(packet), encoding="utf-8")
    return {"jsonPath": str(json_path), "mdPath": str(md_path)}


# ---- self-test ----

def _selftest() -> bool:
    import tempfile

    cases: list[tuple[str, bool]] = []

    def check(name: str, cond: bool) -> None:
        cases.append((name, bool(cond)))

    # redaction
    check("redact financial value", _redact_numbers("Δ=+100.00") == "Δ=#")
    check("redact dollars/commas", _redact_numbers("plug of $1,234,567") == "plug of #")
    check("preserve hex color", _redact_numbers("#FFFFFF on #7F7F7F") == "#FFFFFF on #7F7F7F")
    check("redact none-safe", _redact_numbers(None) is None)

    safe = {"kind": "junk-decimal", "fix_lane": "safe-auto", "fixable": True, "severity": "medium",
            "addrs": ["B7"], "signature": "5 trailing dp", "diagnosis": {"pathway_id": "x"}}
    guided = {"kind": "negative-without-parens", "fix_lane": "guided", "fixable": True,
              "severity": "medium", "addrs": ["C3"], "diagnosis": {}}
    tie = {"kind": "check-tieout", "fix_lane": "surfaced", "fixable": False, "severity": "high",
           "check": "tieout", "addrs": [], "signature": "Gov_Funds assets Δ=+100.00",
           "diagnosis": {"guided_lane": "export-proof", "guided_steps": [1]}}
    broken = {"kind": "broken-ref", "fix_lane": "surfaced", "fixable": False, "severity": "high",
              "addrs": ["E1"], "diagnosis": {}}
    surfaced = {"kind": "display-wrapper", "fix_lane": "surfaced", "fixable": False,
                "severity": "medium", "addrs": ["F9"], "diagnosis": {}}

    check("classify ACCEPT", classify_disposition(safe) == ACCEPT)
    check("classify guided->BLOCKED", classify_disposition(guided) == BLOCKED)
    check("classify tieout->BLOCKED", classify_disposition(tie) == BLOCKED)
    check("classify broken-ref->BLOCKED", classify_disposition(broken) == BLOCKED)
    check("classify surfaced->UNVERIFIED", classify_disposition(surfaced) == UNVERIFIED)

    check("rollback ACCEPT mentions revert", "reverts" in rollback_path(safe, ACCEPT))
    check("rollback UNVERIFIED is n/a", rollback_path(surfaced, UNVERIFIED).startswith("n/a"))

    queue = {
        "scope": "sheet", "spreadsheetId": "ss-secret-123", "sheetId": "sh1", "sheetName": "TB",
        "items": [safe, guided, tie, broken, surfaced],
    }
    packet = build_review_packet(queue, label="ACFR preset", generated_at="2026-06-22T19:00:00+00:00")
    check("overall BLOCKED (most gating)", packet["overall"] == BLOCKED)
    check("client not ready", packet["clientReady"] is False)
    check("workbook pseudonymized", packet["workbookHash"] != "ss-secret-123" and len(packet["workbookHash"]) == 16)
    check("summary totals", packet["summary"]["total"] == 5)
    check("bucket counts", packet["summary"]["byDisposition"] == {ACCEPT: 1, BLOCKED: 3, UNVERIFIED: 1})
    blob = json.dumps(packet)
    check("no raw spreadsheet id", "ss-secret-123" not in blob)
    check("no client value leak", "100.00" not in blob and "+100" not in blob)
    check("redacted signature kept shape", any(f["signature"] == "Gov_Funds assets Δ=#"
                                               for f in packet["dispositions"][BLOCKED]))

    md = render_packet_md(packet)
    check("md has overall", "Overall:** BLOCKED" in md)
    check("md blocked first", md.index("BLOCKED —") < md.index("ACCEPT —"))
    check("md has rollback", "Rollback:" in md)
    check("md no client value", "100.00" not in md)

    with tempfile.TemporaryDirectory() as td:
        paths = write_packet(packet, out_dir=td, markdown=md)
        check("json written", Path(paths["jsonPath"]).exists())
        check("md written", Path(paths["mdPath"]).exists())
        reloaded = json.loads(Path(paths["jsonPath"]).read_text())
        check("json round-trips", reloaded["overall"] == BLOCKED)

    clean = build_review_packet({"scope": "sheet", "spreadsheetId": "x", "items": []})
    check("empty -> CLEAN", clean["overall"] == "CLEAN")

    no_redact = build_review_packet(queue, redact=False)
    check("redact=False keeps value", any("100.00" in (f.get("signature") or "")
                                          for f in no_redact["dispositions"][BLOCKED]))

    passed = sum(1 for _, ok in cases if ok)
    for name, ok in cases:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{passed}/{len(cases)} passed")
    return passed == len(cases)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)

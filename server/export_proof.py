#!/usr/bin/env python3
"""
Wingman Lane C — export-proof guided workflow (RED gate).

For "source fixed but not client-visible": user republishes links, exports PDF
(binary-safe), greps pdftotext for target label/value, then marks verdict.
No auto export API — user executes in Workiva UI.

Public API:
  export_proof_guided_steps(...) -> checklist steps (pure, unit-testable)
  attach_export_proof_to_diagnosis(diagnosis, row) -> mutates diagnosis in place
  export_proof_targets_from_row(row) -> {label, value, calc, pub} hints
"""
from __future__ import annotations

from typing import Any

EXPORT_PROOF_LANE = "export-proof"
EXPORT_PROVEN = "EXPORT_PROVEN"
NOT_PROVEN_VISUAL = "NOT_PROVEN_VISUAL"
VALID_VERDICTS = frozenset({EXPORT_PROVEN, NOT_PROVEN_VISUAL})
NOT_PROVEN_VISUAL_FOOTER = (
    "grep pass ≠ page flow / clipping / raster proof still required."
)

# RED — user executes publish + export in Workiva UI; Wingman guides only.
EXPORT_PROOF_GUIDED_STEPS: list[dict[str, Any]] = [
    {
        "id": "publish-spreadsheet",
        "title": "Publish spreadsheet links (ownLinks)",
        "detail": (
            "Publish this spreadsheet's links so destination caches pull current source values. "
            "Workiva UI: File → Publish links → own links. Required before doc tables refresh."
        ),
    },
    {
        "id": "publish-document",
        "title": "Publish document links (allLinks)",
        "detail": (
            "Publish the linked document's links so doc-table cells show updated values. "
            "Workiva UI: open the document → Publish links → all links."
        ),
    },
    {
        "id": "export-pdf",
        "title": "Export PDF (binary-safe)",
        "detail": (
            "Export the client-facing document as PDF using binary-safe download — not text-mode "
            "fetch. Save to a proof folder; note path and export timestamp for the receipt."
        ),
    },
    {
        "id": "grep-pdf",
        "title": "pdftotext grep proof for target",
        "detail": (
            "Run pdftotext on the fresh PDF and grep for the target label and expected value "
            "(see export targets below). Mismatch or missing text → NOT_PROVEN_VISUAL."
        ),
    },
    {
        "id": "mark-verdict",
        "title": "Mark export-proven vs NOT_PROVEN_VISUAL",
        "detail": (
            "Record EXPORT_PROVEN only when grep confirms the client-visible render matches the "
            "target of record. Otherwise mark NOT_PROVEN_VISUAL — source fix alone is not done."
        ),
    },
]


def export_proof_targets_from_row(row: dict[str, Any] | None) -> dict[str, str | None]:
    """Extract grep targets from a tieout FAIL row or queue context."""
    row = row or {}
    label = str(row.get("label") or "").strip() or None
    calc = str(row.get("calc") or "").strip() or None
    pub = str(row.get("pub") or "").strip() or None
    # Prefer calc (workbook target); fall back to pub for grep when calc absent.
    value = calc or pub
    detail = str(row.get("detail") or "")
    if not label and detail:
        # e.g. "Gov_Funds CY assets Total assets Δ=+100.00"
        parts = detail.split(" Δ=", 1)[0].strip().rsplit(" ", 1)
        if len(parts) == 2:
            label = parts[-1]
    return {"label": label, "value": value, "calc": calc, "pub": pub}


def _grep_detail(*, target_label: str | None, target_value: str | None) -> str:
    base = EXPORT_PROOF_GUIDED_STEPS[3]["detail"]
    hints: list[str] = []
    if target_label:
        hints.append(f'label "{target_label}"')
    if target_value:
        hints.append(f'value "{target_value}"')
    if hints:
        return base + " Grep for: " + ", ".join(hints) + "."
    return base


def export_proof_guided_steps(
    *,
    target_label: str | None = None,
    target_value: str | None = None,
) -> list[dict[str, Any]]:
    """Return a copy of the export-proof guided checklist (pure, unit-testable)."""
    steps = [dict(s) for s in EXPORT_PROOF_GUIDED_STEPS]
    steps[3] = {**steps[3], "detail": _grep_detail(target_label=target_label, target_value=target_value)}
    return steps


def attach_export_proof_to_diagnosis(
    diagnosis: dict[str, Any],
    row: dict[str, Any] | None = None,
    *,
    replace_pathway: bool = True,
) -> dict[str, Any]:
    """
    Attach Lane C export-proof metadata to a diagnosis dict (mutates and returns it).

    Used for tieout FAIL queue items where source may be fixed but client render unproven.
    When replace_pathway is False, keeps existing pathway title/explain and only adds checklist.
    """
    targets = export_proof_targets_from_row(row)
    if replace_pathway:
        diagnosis["guided_lane"] = EXPORT_PROOF_LANE
        diagnosis["pathway_id"] = "export-proof.guided"
        diagnosis["title"] = "Export proof required (source ≠ client-visible)"
        diagnosis["explain"] = (
            "The workbook calculation may differ from what the published document renders. "
            "Fixed at source is not client-ready until republished, re-exported, and grep-confirmed "
            "in a fresh PDF — never trust prior Corrected/PASS status without this proof."
        )
        diagnosis["judgment"] = "surfaced — RED export-proof gate; user publishes and exports in Workiva UI."
        diagnosis["suggested_action"] = (
            "Follow the export-proof checklist: ownLinks → allLinks → binary PDF export → "
            "pdftotext grep → mark EXPORT_PROVEN or NOT_PROVEN_VISUAL."
        )
    else:
        diagnosis["guided_lane"] = EXPORT_PROOF_LANE
    diagnosis["guided_steps"] = export_proof_guided_steps(
        target_label=targets.get("label"),
        target_value=targets.get("value"),
    )
    diagnosis["export_proof"] = {
        **targets,
        "verdict": None,
        "valid_verdicts": [EXPORT_PROVEN, NOT_PROVEN_VISUAL],
    }
    return diagnosis


def normalize_verdict(raw: str | None) -> str | None:
    """Return a valid verdict constant or None."""
    if not raw:
        return None
    v = str(raw).strip().upper().replace("-", "_")
    if v in VALID_VERDICTS:
        return v
    if v == "PROVEN":
        return EXPORT_PROVEN
    if v in ("NOT_PROVEN", "UNPROVEN", "FAIL"):
        return NOT_PROVEN_VISUAL
    return None

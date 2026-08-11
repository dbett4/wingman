#!/usr/bin/env python3
"""Wingman pathway catalogs — extracted from diagnose.py for maintainability."""
from __future__ import annotations

import re
from typing import Any

import display_wrapper

_ERROR_RE = re.compile(r"#(?:REF!|NAME\?|VALUE!|DIV/0!|N/A|NULL!|NUM!|SHEET!|ERROR)", re.I)


def _trap(key: str, note: str) -> dict:
    return {"source": "known-traps", "key": key, "note": note}


def _fix(key: str, note: str) -> dict:
    return {"source": "fix-recipes", "key": key, "note": note}


# Guided multi-step lane for blank-DL (RED — user executes in Workiva UI; no auto-publish).
# Encodes hard-won publish/link-cache behavior from an operational error/fix playbook.
BLANK_DL_GUIDED_STEPS: list[dict[str, Any]] = [
    {
        "id": "verify-source",
        "title": "Verify the linked source cell",
        "detail": (
            "Jump to a flagged cell, then trace its destination link to the source anchor in Workiva. "
            "Confirm the source shows the expected value — empty cached text can mean stale publish, "
            "anchor revision pinning, or a broken source (not always cache lag)."
        ),
    },
    {
        "id": "publish-spreadsheet",
        "title": "Publish spreadsheet links (ownLinks)",
        "detail": (
            "Publish this spreadsheet's links so destination caches pull current source values. "
            "Workiva UI: File → Publish links → own links. API: POST "
            "/spreadsheets/{ss}/links/publication {\"publishType\":\"ownLinks\"} — poll operation to completion."
        ),
    },
    {
        "id": "publish-document",
        "title": "Publish document links (allLinks)",
        "detail": (
            "Publish the linked document's links so doc-table cells refresh. Workiva UI: open the "
            "document → Publish links → all links. API: POST /documents/{doc}/links/publication "
            "{\"publishType\":\"allLinks\"} — poll to completion."
        ),
    },
    {
        "id": "re-walk-cache",
        "title": "Re-verify cache (empty-cached-text walk)",
        "detail": (
            "Final verification: walk the document for destination links with empty cached text — "
            "do NOT rely on resolve-based scope (15–25% false negatives at scale). Re-scan this sheet "
            "in Wingman; flagged cells should clear once caches refresh."
        ),
    },
]


def blank_dl_guided_steps() -> list[dict[str, Any]]:
    """Return a copy of the blank-DL guided checklist (pure, unit-testable)."""
    return [dict(s) for s in BLANK_DL_GUIDED_STEPS]


# Pathway catalog: kind (+ optional predicates) -> explain/judge metadata.
# Keys are (kind, fix_lane, subkind) where subkind narrows broken-ref errors etc.
PATHWAYS: dict[tuple[str, str, str | None], dict[str, Any]] = {
    ("low-contrast", "safe-auto", None): {
        "pathway_id": "format.contrast-plain-auto",
        "title": "Low contrast on plain cell",
        "explain": (
            "Text/background contrast is below WCAG AA (4.5:1). The cell is plainText, so a "
            "font-color write via applyFormats is safe when preceded by a full pre-read."
        ),
        "judgment": "safe-auto — mechanical color correction; reversible by re-writing before-state.",
        "suggested_action": "Preview the computed AA-passing color, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "applyFormats-richText-noop",
                "applyFormats textFormat on richText-populated cells silently no-ops — "
                "type guard required before any write.",
            ),
        ],
        "fix_refs": [
            _fix("fixer-contrast", "Minimal hue-preserving fontColor adjustment via applyFormats (ADR-0002)."),
        ],
    },
    ("low-contrast", "surfaced", None): {
        "pathway_id": "format.contrast-richtext-ui",
        "title": "Low contrast on rich or unknown cell type",
        "explain": (
            "Contrast fails AA, but the cell is richText (or type unknown from sheetdata). "
            "Font color writes silently no-op on richText runs — restyle requires Values rewrite or UI."
        ),
        "judgment": "surfaced — UI-required; auto-write would silently fail.",
        "suggested_action": "Open the cell in Workiva; restyle via Style Guide or rewrite richText value.",
        "trap_refs": [
            _trap(
                "applyFormats-richText-noop",
                "Existing richText runs keep their own font/italic/size; applyFormats only sets cell default.",
            ),
        ],
        "fix_refs": [
            _fix("fix-3-valueFormat", "Populated richText restyle pattern: Values API rewrite, not applyFormats."),
        ],
    },
    ("blank-linked-cell", "surfaced", None): {
        "pathway_id": "link.blank-dl-review",
        "title": "Blank destination-linked cell",
        "explain": (
            "Cell carries a destination link but renders empty cached text. Causes include stale "
            "publish, anchor revision pinning, or a broken source — not always a simple cache lag."
        ),
        "judgment": "surfaced — RED structural/link integrity risk; no reliable API undo.",
        "suggested_action": (
            "Verify source value + publish (ownLinks + allLinks). If still blank, walk empty-cached-text "
            "scan — do not naive re-link without structural pre-screen."
        ),
        "trap_refs": [
            _trap(
                "dl-resolve-unreliable",
                "GET /content/destinationLinks/{id} misses 15–25% at scale — use empty-cached-text walk.",
            ),
            _trap(
                "blank-dl-auto-fix-risk",
                "Blank DL auto-fix can publish wrong values into live ACFR docs without structural pre-screen.",
            ),
        ],
        "fix_refs": [
            _fix("fix-16-dl-publish", "Publish SS ownLinks + doc allLinks after source fix; verify cached text."),
        ],
        "guided_steps": BLANK_DL_GUIDED_STEPS,
        "guided_lane": "blank-dl",
    },
    ("empty-linked-range", "surfaced", None): {
        "pathway_id": "link.empty-linked-range",
        "title": "Empty document-linked block",
        "explain": (
            "An entire range-link source block on this sheet is blank, so the destination document "
            "table it feeds will render empty. Detected at the link level from "
            "GET /content/tables/{tid}/rangeLinks (block-level ranges, not per-cell)."
        ),
        "judgment": "surfaced — fixing the source/relinking is judgment; no reliable API undo.",
        "suggested_action": (
            "Confirm the source range should carry data; populate it, then publish "
            "(SS ownLinks + doc allLinks) so the destination refreshes. If the block is intentionally "
            "blank, the link itself may be unnecessary."
        ),
        "trap_refs": [
            _trap(
                "range-link-block-level",
                "Range links cover a rectangular block; a blank spacer cell inside a populated block "
                "is NOT a defect — only a wholly-empty block is flagged.",
            ),
        ],
        "fix_refs": [
            _fix("fix-16-dl-publish", "Publish SS ownLinks + doc allLinks after the source is populated."),
        ],
    },
    ("broken-ref", "surfaced", "ref"): {
        "pathway_id": "formula.broken-ref",
        "title": "Broken reference (#REF!)",
        "explain": "Formula points at a deleted/moved range or a sheet renamed outside API formula update.",
        "judgment": "surfaced — judgment fix; diagnose source of broken pointer before rewrite.",
        "suggested_action": "Trace the referenced range/sheet; repair formula text or restore the missing source.",
        "trap_refs": [
            _trap(
                "sheet-rename-api",
                "Sheet rename via UI auto-updates formulas; API-written formula strings keep old names → #REF!.",
            ),
        ],
        "fix_refs": [
            _fix("fix-1-remap", "When remap flows fail, patch TB/AcctMap cols and spot-read end-to-end."),
        ],
    },
    ("broken-ref", "surfaced", "name"): {
        "pathway_id": "formula.unsupported-name",
        "title": "Unsupported name (#NAME?)",
        "explain": (
            "Workiva rejected a function or reference — common: INDIRECT, internal HYPERLINK anchors, "
            "or dynamic sheet names."
        ),
        "judgment": "surfaced — requires formula rewrite or UI hyperlink.",
        "suggested_action": "Replace unsupported patterns with explicit sheet refs; use UI Insert Link for drill-downs.",
        "trap_refs": [
            _trap(
                "indirect-hyperlink-unsupported",
                "INDIRECT and internal-sheet-anchor HYPERLINK evaluate to #NAME? — hardcode sheet names or UI link.",
            ),
        ],
        "fix_refs": [],
    },
    ("broken-ref", "surfaced", "value"): {
        "pathway_id": "formula.value-error",
        "title": "Formula value error (#VALUE!)",
        "explain": "Arguments or range syntax incompatible with Workiva's formula engine (array constants, bad ranges).",
        "judgment": "surfaced — guided rewrite; not a one-click safe fix.",
        "suggested_action": "Inspect formula shape; split SUMPRODUCT/array patterns into chained SUMIFS.",
        "trap_refs": [
            _trap(
                "cross-sheet-range-qualifier",
                "SUM(STMT!B22:STMT!B25) returns #VALUE! — qualifier once: SUM(STMT!B22:B25).",
            ),
            _trap(
                "sumproduct-array",
                "SUMPRODUCT(SUMIFS(...,{arr},...)) with array constant → #VALUE!.",
            ),
        ],
        "fix_refs": [],
    },
    ("broken-ref", "surfaced", "sheet"): {
        "pathway_id": "formula.missing-sheet",
        "title": "Missing sheet (#SHEET!)",
        "explain": "Formula references a sheet that does not exist in the workbook.",
        "judgment": "surfaced — create sheet + re-PUT formula, or repoint reference.",
        "suggested_action": "Create the missing sheet or rewrite formula to an existing sheet name.",
        "trap_refs": [],
        "fix_refs": [],
    },
    ("broken-ref", "surfaced", "other"): {
        "pathway_id": "formula.error-other",
        "title": "Formula error",
        "explain": "Formula evaluates to an Excel-style error token.",
        "judgment": "surfaced — diagnose root cause before any write.",
        "suggested_action": "Read formula text (content cells endpoint) and trace upstream inputs.",
        "trap_refs": [],
        "fix_refs": [],
    },
    ("label-hygiene", "safe-auto", None): {
        "pathway_id": "format.label-hygiene-auto",
        "title": "Label whitespace trim (plain cell)",
        "explain": (
            "Display label has invisible leading/trailing or double internal spaces. The cell is "
            "plainText, so a trim write via the Values API is safe with readback/revert."
        ),
        "judgment": "safe-auto — mechanical trim; reversible by re-writing before-state.",
        "suggested_action": "Preview trimmed label, then confirm Apply in Wingman.",
        "trap_refs": [],
        "fix_refs": [
            _fix("fixer-label-trim", "Strip/collapse whitespace via Values PUT (ADR-0004 follow-on)."),
        ],
    },
    ("label-hygiene", "surfaced", None): {
        "pathway_id": "format.label-hygiene",
        "title": "Label whitespace defect",
        "explain": (
            "Display label has leading, trailing, or internal double spaces — invisible on screen but "
            "shows in exports and breaks alignment."
        ),
        "judgment": "surfaced — cosmetic; safe trim write is a future fast-follow, not v1 auto-fix.",
        "suggested_action": "Trim the label in Workiva UI or via a confirmed Values write after preview.",
        "trap_refs": [],
        "fix_refs": [],
    },
    ("negative-without-parens", "safe-auto", None): {
        "pathway_id": "format.negative-accounting-auto",
        "title": "Negative minus-prefix (homogeneous column)",
        "explain": (
            "A negative numeric value displays with a leading minus instead of ACCOUNTING parentheses. "
            "The column is homogeneous (all negatives use minus-prefix), so applyFormats valueFormat "
            "is safe with readback/revert."
        ),
        "judgment": "safe-auto — ACCOUNTING format with useParensForNegatives; not TEXT or NUMBER.",
        "suggested_action": "Preview the planned ACCOUNTING format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "accounting-prefix-string-bypass",
                "ACCOUNTING prefix applies only to numeric calculatedValue — TEXT()-wrapped "
                "strings bypass valueFormat.",
            ),
        ],
        "fix_refs": [
            _fix(
                "fix-50-accounting-parens",
                "applyFormats valueFormatType ACCOUNTING + useParensForNegatives:true "
                "(preserve prefix/precision when present).",
            ),
        ],
    },
    ("negative-without-parens", "surfaced", None): {
        "pathway_id": "format.negative-accounting-mixed",
        "title": "Negative format inconsistency",
        "explain": (
            "Negative values in this column mix minus-prefix and parentheses display — common when "
            "a partial format paste missed some rows. Auto-fix is unsafe until the column is homogeneous."
        ),
        "judgment": "surfaced — mixed column; judgment fix in Workiva UI or batch after review.",
        "suggested_action": (
            "Review the full column in Workiva; when homogenized, use Wingman "
            "\"Apply column format\" or apply ACCOUNTING + parentheses for negatives consistently."
        ),
        "trap_refs": [],
        "fix_refs": [
            _fix(
                "fix-50-accounting-parens",
                "Batch applyFormats ACCOUNTING + useParensForNegatives after manual column review.",
            ),
        ],
    },
    ("junk-decimal", "safe-auto", None): {
        "pathway_id": "format.junk-decimal-auto",
        "title": "Junk decimal precision (neighbor copy)",
        "explain": (
            "Cell uses absurd fixed decimal precision (formatting-audit junk pattern). "
            "Column neighbors agree on a sane valueFormat — applyFormats copy is safe when TEXT() "
            "preflight passes."
        ),
        "judgment": "safe-auto — copy neighbor valueFormat; readback/revert; TEXT() cells refused.",
        "suggested_action": "Preview before/after format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "TEXT()-wrapped display mirrors rewrite scale on valueFormat apply — W21 1000× trap.",
            ),
        ],
        "fix_refs": [
            _fix(
                "fix-junk-decimal-neighbor",
                "applyFormats valueFormat copied from 2-of-3 column neighbor consensus.",
            ),
        ],
    },
    ("junk-decimal", "surfaced", None): {
        "pathway_id": "format.junk-decimal-manual",
        "title": "Junk decimal precision (no neighbor consensus)",
        "explain": (
            "Absurd precision.value on this cell, but column neighbors do not agree on a replacement "
            "format — auto-copy would propagate ambiguity."
        ),
        "judgment": "surfaced — batch format paste in Workiva after column review.",
        "suggested_action": (
            "Review the column in Workiva; when homogenized, use Wingman "
            "\"Apply column format\" (gated dry-run → confirm → apply) or batch paste in Workiva."
        ),
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Never apply valueFormat on TEXT() display-mirror bands — rebuild numeric layer instead.",
            ),
        ],
        "fix_refs": [],
    },
    ("missing-thousands-separator", "safe-auto", None): {
        "pathway_id": "format.missing-thousands-auto",
        "title": "Missing thousands separator (neighbor copy)",
        "explain": (
            "Numeric value ≥ 1000 displays without comma grouping while column neighbors use "
            "showThousandsSeparator. Neighbor format consensus (2-of-3) makes applyFormats copy safe."
        ),
        "judgment": "safe-auto — copy neighbor valueFormat; readback/revert; TEXT() cells refused.",
        "suggested_action": "Preview before/after format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "TEXT()-wrapped display mirrors rewrite scale on valueFormat apply — W21 1000× trap.",
            ),
        ],
        "fix_refs": [
            _fix(
                "fixer-format-copy",
                "applyFormats valueFormat copied from column neighbor with thousands separator.",
            ),
        ],
    },
    ("missing-thousands-separator", "surfaced", None): {
        "pathway_id": "format.missing-thousands-manual",
        "title": "Missing thousands separator (no consensus)",
        "explain": (
            "Value should show comma grouping but column neighbors disagree on format — "
            "auto-copy would propagate ambiguity."
        ),
        "judgment": "surfaced — batch format paste in Workiva after column review.",
        "suggested_action": (
            "Review the column; when homogenized, use Wingman "
            "\"Apply column format\" or apply thousands separator consistently in Workiva."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("number-on-accounting-column", "safe-auto", None): {
        "pathway_id": "format.number-on-accounting-auto",
        "title": "NUMBER in ACCOUNTING column (neighbor copy)",
        "explain": (
            "Cell uses NUMBER/AUTOMATIC while homogeneous column neighbors use ACCOUNTING. "
            "Copying neighbor format preserves ACFR presentation without formula mutation."
        ),
        "judgment": "safe-auto — copy neighbor ACCOUNTING valueFormat; TEXT() cells refused.",
        "suggested_action": "Preview planned ACCOUNTING format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "accounting-prefix-string-bypass",
                "ACCOUNTING prefix applies only to numeric calculatedValue — TEXT()-wrapped "
                "strings bypass valueFormat.",
            ),
        ],
        "fix_refs": [
            _fix("fix-50-accounting-parens", "Neighbor ACCOUNTING valueFormat via applyFormats."),
        ],
    },
    ("number-on-accounting-column", "surfaced", None): {
        "pathway_id": "format.number-on-accounting-manual",
        "title": "NUMBER in mixed-format column",
        "explain": (
            "Cell uses NUMBER but column neighbors do not agree on ACCOUNTING — "
            "may be intentional (variance row, percent band)."
        ),
        "judgment": "surfaced — review before batch format paste.",
        "suggested_action": (
            "Confirm column intent; use Wingman \"Apply column format\" after homogenizing, "
            "or apply ACCOUNTING in Workiva if appropriate."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("precision-mismatch", "safe-auto", None): {
        "pathway_id": "format.precision-mismatch-auto",
        "title": "Decimal precision drift (neighbor copy)",
        "explain": (
            "Cell uses fixed decimal precision (1–4 places) while column neighbors use integer "
            "precision. Non-junk band — distinct from absurd junk-decimal precision."
        ),
        "judgment": "safe-auto — copy neighbor integer precision; readback/revert.",
        "suggested_action": "Preview before/after format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Never apply valueFormat on TEXT() display-mirror bands.",
            ),
        ],
        "fix_refs": [
            _fix("fixer-format-copy", "applyFormats valueFormat copied from column neighbor."),
        ],
    },
    ("precision-mismatch", "surfaced", None): {
        "pathway_id": "format.precision-mismatch-manual",
        "title": "Decimal precision drift (no consensus)",
        "explain": (
            "Cell decimal precision differs from neighbors but column lacks 2-of-3 integer "
            "precision agreement — may be intentional detail row."
        ),
        "judgment": "surfaced — review column before batch format paste.",
        "suggested_action": (
            "Review the column; use Wingman \"Apply column format\" after homogenizing, "
            "or align precision in Workiva if appropriate."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("prefix-mismatch", "safe-auto", None): {
        "pathway_id": "format.prefix-mismatch-auto",
        "title": "Missing $ prefix (neighbor copy)",
        "explain": (
            "Numeric amount cell lacks the column's currency prefix while 2-of-3 neighbors share "
            "the same prefix on ACCOUNTING/NUMBER formats. Copying neighbor valueFormat preserves "
            "ACFR presentation without formula mutation."
        ),
        "judgment": "safe-auto — copy neighbor valueFormat; readback/revert; TEXT() cells refused.",
        "suggested_action": "Preview before/after format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "accounting-prefix-string-bypass",
                "ACCOUNTING prefix applies only to numeric calculatedValue — TEXT()-wrapped "
                "strings bypass valueFormat.",
            ),
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Never apply valueFormat on TEXT() display-mirror bands.",
            ),
        ],
        "fix_refs": [
            _fix("fixer-format-copy", "applyFormats valueFormat copied from column neighbor prefix."),
        ],
    },
    ("prefix-mismatch", "surfaced", None): {
        "pathway_id": "format.prefix-mismatch-manual",
        "title": "Missing prefix (no neighbor consensus)",
        "explain": (
            "Amount cell lacks a prefix but column neighbors disagree — may be intentional "
            "(PERCENT/CURRENCY variance row, non-$ band)."
        ),
        "judgment": "surfaced — review before batch format paste.",
        "suggested_action": (
            "Confirm column intent; use Wingman \"Apply column format\" after homogenizing, "
            "or apply prefix in Workiva if appropriate."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("zero-display-mismatch", "safe-auto", None): {
        "pathway_id": "format.zero-display-auto",
        "title": "Zero shows 0 not em-dash (neighbor copy)",
        "explain": (
            "Zero numeric value renders as literal 0 while homogeneous ACCOUNTING column neighbors "
            "use displayZeroAs EM DASH. Copying neighbor valueFormat aligns ACFR zero presentation."
        ),
        "judgment": "safe-auto — copy neighbor ACCOUNTING zero format; TEXT() cells refused.",
        "suggested_action": "Preview before/after format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Never apply valueFormat on TEXT() display-mirror bands.",
            ),
        ],
        "fix_refs": [
            _fix("fixer-format-copy", "applyFormats valueFormat with displayZeroAs EM DASH from neighbor."),
        ],
    },
    ("year-automatic-coercion", "safe-auto", None): {
        "pathway_id": "format.year-period-auto",
        "title": "Year coerced by AUTOMATIC format (use PERIOD)",
        "explain": (
            "A 4-digit year stored as a number under AUTOMATIC value format is rendered with a "
            "thousands separator (2025 → '2.025' / '2,025') in document destination links. "
            "valueFormatType=PERIOD suppresses the separator — reversible, no value rescale."
        ),
        "judgment": "safe-auto — PERIOD valueFormat write; readback/revert; richText surfaced.",
        "suggested_action": "Preview before/after format, then confirm Apply in Wingman.",
        "trap_refs": [
            _trap(
                "year-period-consumer-propagation",
                "A formula year cell needs PERIOD on the cell itself; fix every coerced consumer, "
                "not only the source — each is flagged independently.",
            ),
        ],
        "fix_refs": [
            _fix("fix-15-year-period", "applyFormats valueFormatType=PERIOD; then republish links so doc refreshes."),
        ],
    },
    ("year-automatic-coercion", "surfaced", None): {
        "pathway_id": "format.year-period-richtext",
        "title": "Year coercion on a richText cell",
        "explain": (
            "Same AUTOMATIC-year coercion, but on a richText cell — a valueFormat write may not "
            "affect the styled runs, so it is surfaced for a manual fix in the Workiva UI."
        ),
        "judgment": "surfaced — set the value format to PERIOD in Workiva.",
        "suggested_action": "In Workiva, set the cell's number format to PERIOD (no separator).",
        "trap_refs": [],
        "fix_refs": [],
    },
    ("zero-display-mismatch", "surfaced", None): {
        "pathway_id": "format.zero-display-manual",
        "title": "Zero display drift (no consensus)",
        "explain": (
            "Zero renders as 0 but column neighbors do not agree on em-dash zero display — "
            "may be intentional detail row."
        ),
        "judgment": "surfaced — review column before batch format paste.",
        "suggested_action": (
            "Review the column; use Wingman \"Apply column format\" after homogenizing, "
            "or set display zero as em-dash in Workiva if appropriate."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("formula-evaluates-blank", "surfaced", None): {
        "pathway_id": "formula.evaluates-blank",
        "title": "Formula evaluates to blank",
        "explain": (
            "Cell has a formula string but calculatedValue is empty — not an error token (#REF!, etc.). "
            "Often a silent SUM/IF gap, empty source range, or DL cache lag; requires judgment."
        ),
        "judgment": "surfaced — no auto-fix; trace formula dependencies in Workiva.",
        "suggested_action": (
            "Open the formula bar; verify referenced ranges and linked sources; fix upstream blank "
            "or republish links if DL-related."
        ),
        "trap_refs": [
            _trap(
                "dl-cache-empty-cached-text",
                "Blank display on linked cells may be stale cache — verify source before rewriting formula.",
            ),
        ],
        "fix_refs": [],
    },
    ("clipped-text", "surfaced", None): {
        "pathway_id": "format.clipped-text-review",
        "title": "Clipped or overflowing text (vision)",
        "explain": (
            "Vision detected label or amount text clipped by row height or column width — "
            "sheetdata cannot see pixel overflow. Resize row/column or shorten text in Workiva."
        ),
        "judgment": "surfaced — layout fix in Workiva UI; no safe-auto write.",
        "suggested_action": (
            "Select the cell; drag row height or column width until full text is visible; "
            "re-export PDF to confirm."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("display-wrapper", "surfaced", "text-round-scale"): {
        "pathway_id": "formula.display-wrapper-scaled-round",
        "title": "TEXT(ROUND(.../1000)) display mirror",
        "explain": (
            "Formula converts a numeric source into formatted text via TEXT(ROUND(.../1000)). "
            "Presentation belongs in valueFormat or a same-scale helper layer — not a TEXT() band."
        ),
        "judgment": "surfaced — RED structural; native valueFormat on TEXT bands rewrites scale (W21 canary).",
        "suggested_action": (
            "Replace wrapper with numeric =src or =src/1000 plus Entered In/Shown In valueFormat; "
            "require fresh XLSX/export proof after any cleanup — do not trust content API alone."
        ),
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Applying NUMBER enteredIn=ONES shownIn=THOUSANDS to TEXT(B21/1000,...) rewrote "
                "divisor to /1 — e.g. exporting 12,340,000 instead of 12,340 (the W21 canary incident).",
            ),
            _trap(
                "display-mirror-xlsx-readback",
                "Content API readback can falsely pass mirror cleanup while XLSX proves wrong-scale export.",
            ),
        ],
        "fix_refs": [],
    },
    ("display-wrapper", "surfaced", "text-div-scale"): {
        "pathway_id": "formula.display-wrapper-scaled-text",
        "title": "TEXT(.../1000) scaled display mirror",
        "explain": (
            "Formula wraps a source with TEXT(.../1000) for thousands presentation. "
            "valueFormat auto-fix on this band is unsafe — Workiva may rewrite the formula divisor."
        ),
        "judgment": "surfaced — RED; never apply valueFormat auto-fix on existing TEXT mirror bands.",
        "suggested_action": (
            "Rebuild as numeric formula + valueFormat (Entered In/Shown In) or a proven helper block; "
            "verify with XLSX export, not content API effectiveValue alone."
        ),
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Native valueFormat on TEXT(.../1000) cells can change /1000 to /1 — 1000× export error.",
            ),
        ],
        "fix_refs": [],
    },
    ("display-wrapper", "surfaced", "isnumber-text-mirror-scaled"): {
        "pathway_id": "formula.display-wrapper-isnumber-scaled",
        "title": "ISNUMBER→TEXT scaled display mirror",
        "explain": (
            "IF(ISNUMBER(src), TEXT(ROUND(src/1000,...), ...)) band — the display-mirror screenshot pattern. "
            "Breaks the numeric-formula contract; DL/tieout need numeric calculatedValue."
        ),
        "judgment": "surfaced — RED structural formula judgment; no Wingman auto-fix.",
        "suggested_action": (
            "Replace mirror column with =src or scaled numeric ref; apply presentation via valueFormat "
            "on a separate layer. Do not mutate valueFormat on the TEXT band."
        ),
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "valueFormat writes on TEXT mirror bands rewrite formula scale — W21 canary proved 1000× export.",
            ),
            _trap(
                "accounting-prefix-string-bypass",
                "ACCOUNTING valueFormat applies only to numeric calculatedValue — TEXT()-wrapped "
                "strings bypass valueFormat.",
            ),
        ],
        "fix_refs": [],
    },
    ("display-wrapper", "surfaced", "isnumber-text-mirror"): {
        "pathway_id": "formula.display-wrapper-isnumber",
        "title": "ISNUMBER→TEXT display mirror",
        "explain": (
            "IF(ISNUMBER(src), TEXT(...)) converts numeric output to display text for commas/parens. "
            "The house architecture expects numeric cells with native valueFormat handling presentation."
        ),
        "judgment": "surfaced — RED; formula rewrite required, not format auto-fix.",
        "suggested_action": (
            "Point face/mirror cell at numeric source (=src) and set valueFormat in Workiva UI or "
            "a scoped applyFormats pass on the numeric layer — not on the TEXT wrapper."
        ),
        "trap_refs": [
            _trap(
                "accounting-prefix-string-bypass",
                "TEXT()-wrapped strings bypass valueFormat — format-only fixes silently fail or corrupt scale.",
            ),
        ],
        "fix_refs": [],
    },
    ("display-wrapper", "surfaced", "literal-numeric-string"): {
        "pathway_id": "formula.literal-numeric-string",
        "title": "Literal numeric string formula",
        "explain": (
            "Cell stores a hardcoded numeric display as a string formula (=\"12345\"). "
            "Not live-linked — breaks tieout and the numeric-formula contract."
        ),
        "judgment": "surfaced — RED; replace with numeric source or link.",
        "suggested_action": (
            "Replace literal string with a formula pointing at the live source cell; "
            "never substitute valueFormat on a string literal."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("display-wrapper", "surfaced", "format-inert-wrapper"): {
        "pathway_id": "formula.display-wrapper-format-inert",
        "title": "ACCOUNTING $ format inert under string wrapper",
        "explain": (
            "Cell carries an ACCOUNTING `$` (or CURRENCY) value format, but its formula "
            "returns a STRING (TEXT() / IF()-dash / literal). Workiva attaches the `$`, "
            "commas, parens, and em-dash ONLY to a numeric calculatedValue, so the format "
            "is silently inert and the cell/linked doc renders the raw string."
        ),
        "judgment": (
            "surfaced — RED; strip the wrapper so ACCOUNTING renders natively. Confirm the "
            "upstream ref is numeric first (pre-write safety)."
        ),
        "suggested_action": (
            "Strip the string wrapper so the cell emits a number; ACCOUNTING then renders `$`, "
            "commas, parens, and em-dash natively. Verify the upstream ref is itself numeric "
            "before rewriting. Never substitute another valueFormat on a string literal."
        ),
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Never apply a valueFormat auto-fix on a TEXT mirror band (W21 canary) — "
                "strip the wrapper instead.",
            ),
            _trap(
                "strip-then-residual-renders",
                "After stripping, single-value Total-col-only rows can render `$—` and "
                "doc-linked cells may still need a follow-up presentation pass — the strip alone is "
                "insufficient on those.",
            ),
        ],
        "fix_refs": [
            _fix("fix-strip-wrapper", "Strip TEXT()/literal wrapper so ACCOUNTING $ renders natively."),
        ],
    },
    ("display-wrapper", "surfaced", "other"): {
        "pathway_id": "formula.display-wrapper-other",
        "title": "Display-wrapper formula",
        "explain": "Formula uses TEXT/ISNUMBER display mirroring — review before any write.",
        "judgment": "surfaced — RED; no valueFormat auto-fix on TEXT bands.",
        "suggested_action": "Inspect formula text; rebuild on numeric layer with export proof.",
        "trap_refs": [
            _trap(
                "w21-native-valueformat-scale-rewrite",
                "Never apply valueFormat auto-fix on existing TEXT mirror bands (W21 canary).",
            ),
        ],
        "fix_refs": [],
    },
    ("hardcoded-face-value", "surfaced", None): {
        "pathway_id": "formula.hardcoded-face-value",
        "title": "Hardcoded face value (no formula)",
        "explain": (
            "Cell holds a raw numeric value with no formula while column neighbors are "
            "formula-driven. In ACFR face sheets this means the amount was manually entered "
            "instead of pulled from the trial balance / AcctMap SUMIFS layer -- it will drift "
            "when the TB updates and will not flow through to tieout or rollforward."
        ),
        "judgment": "surfaced -- structural formula gap; replace with formula or source link.",
        "suggested_action": (
            "Replace the hardcoded value with a formula referencing the appropriate AcctMap "
            "SUMIFS band or link the cell to a TB source cell. Do not patch with another "
            "hardcoded value -- that preserves the structural gap."
        ),
        "trap_refs": [
            _trap(
                "hardcoded-value-formula-patch",
                "Replacing a hardcoded amount with another hardcoded amount is not a fix -- "
                "the formula must route through AcctMap to be durable across TB updates.",
            ),
            _trap(
                "formula-consistency-col-scope",
                "Run formula_consistency on the statement sheet after correction to confirm "
                "no other rows in the same column were missed.",
            ),
        ],
        "fix_refs": [
            _fix(
                "fix-1-remap",
                "After AcctMap correction, face formula auto-updates via SUMIFS -- "
                "re-run tieout suite to confirm.",
            ),
        ],
    },
    ("hardcoded-text-criteria-in-formula", "surfaced", None): {
        "pathway_id": "formula.hardcoded-text-criteria",
        "title": "Hardcoded text criteria in formula",
        "explain": (
            "A SUMIFS-family formula filters on a literal CODE (e.g. \"ACT-ADMIN\") or an "
            "exclusion list (\"<>EXP-EQUIP\") instead of a row-control / mapping / Formula "
            "Reference cell. On rollforward the literal stops following its row, and an "
            "exclusion list silently captures every NEW account code added next year -- the "
            "statement breaks a year later with no audit trail."
        ),
        "judgment": "surfaced -- route the criterion through the architecture; never a literal.",
        "suggested_action": (
            "Replace the literal with the row's mapping cell ($C{row} STMT_LINE / DISPLAY_COL) "
            "or a Formula Reference cell. If the line needs an exclusion, that is a MAPPING "
            "design problem -- create/refine codes so the line selects positively."
        ),
        "trap_refs": [
            _trap(
                "exclusion-list-captures-new-codes",
                "A \"<>\" exclusion list routes any new sub-code added next year into the wrong "
                "line with no error. Positive selection via a code is the durable fix.",
            ),
        ],
        "fix_refs": [
            _fix(
                "fix-criteria-from-architecture",
                "Point the criterion at row controls / AcctMap / Formula Reference, then re-run "
                "tieout to confirm the value is unchanged.",
            ),
        ],
    },
    ("hardcoded-constant-in-formula", "surfaced", None): {
        "pathway_id": "formula.hardcoded-constant",
        "title": "Hardcoded constant in formula",
        "explain": (
            "A formula has a large numeric amount welded into it (=SUMIFS(...)+12000000, "
            "=Total-250000, or a sole-body =12345). These are usually compensating plugs -- a "
            "suppression in one cell offset by an addition elsewhere -- so the total ties while "
            "the line-level math is wrong. They freeze at preparer-time and do not move with the TB."
        ),
        "judgment": "surfaced -- trace the plug; fix the root mapping, never two offsetting wrongs.",
        "suggested_action": (
            "Trace what the SUMIFS would yield WITHOUT the constant and identify the GL accounts "
            "that contribute the difference. Audit the whole section as a stack -- removing one "
            "plug without its matched compensator changes totals (a compensating hardcode stack)."
        ),
        "trap_refs": [
            _trap(
                "compensating-hardcode-stack",
                "Embedded constants travel in pairs (a suppression + an add-back). Understand the "
                "set as a unit before changing any one; verify section total ties per step.",
            ),
        ],
        "fix_refs": [],
    },
    ("round-wrapper-workaround", "surfaced", None): {
        "pathway_id": "formula.round-wrapper",
        "title": "ROUND wrapper for display rounding",
        "explain": (
            "A formula is wrapped in ROUND(...,-N) to present thousands/millions. Workiva separates "
            "storage from display: the cell stores full precision and valueFormat (shownIn: "
            "THOUSANDS) renders the rounded view. A ROUND wrapper loses precision in storage and "
            "breaks downstream reference math."
        ),
        "judgment": "surfaced -- drop the ROUND wrapper; set shownIn on the cell's valueFormat.",
        "suggested_action": (
            "Rewrite as the plain aggregation (=SUM(detail)) and apply the section's shownIn "
            "presentation format. Only keep ROUND if a downstream CALCULATION needs the rounded "
            "value as an input (rare on financial statements)."
        ),
        "trap_refs": [],
        "fix_refs": [],
    },
    ("unbounded-full-column-sumifs", "surfaced", None): {
        "pathway_id": "formula.unbounded-full-column-sumifs",
        "title": "Unbounded full-column SUMIFS",
        "explain": (
            "A SUMIFS-family formula references a full COLUMN ($AC:$AC, A:A, A:AD) for its sum "
            "or criteria range. It computes correctly LIVE, but on xlsx export -> reimport / "
            "roll-forward Workiva throws #VALUE! on the full-column range -- the single most "
            "frequent portability defect. It is latent: the workbook looks fine until next year's "
            "roll-forward or any round-trip through Excel."
        ),
        "judgment": (
            "surfaced -- bind every full-column range to explicit rows ($1:$N). The rewrite is "
            "value-preserving when N >= the data extent, so the live result is unchanged."
        ),
        "suggested_action": (
            "Bind each full-column range to a uniform ceiling that covers the largest sheet "
            "($AC:$AC -> $AC$1:$AC$N). Use one M across all ranges in a call so SUMIFS dimensions "
            "stay equal. The external checks toolkit's bounded-SUMIFS write-plan generates the exact before/after "
            "and a gated live-apply pushes it with readback proof -- Wingman does not auto-write it."
        ),
        "trap_refs": [
            _trap(
                "unbounded-range-latent-reimport-debt",
                "Full-column SUMIFS passes every LIVE check, so it survives until the workbook is "
                "rolled forward or round-tripped through xlsx -- then it errors with no warning.",
            ),
        ],
        "fix_refs": [
            _fix(
                "fix-bound-full-column-ranges",
                "Bind ranges to rows 1..M (M >= the largest sheet) so the empty tail contributes "
                "0 and the result is unchanged; re-run tieout to confirm before/after parity.",
            ),
        ],
    },
    ("dead-unsupported-function", "surfaced", None): {
        "pathway_id": "formula.dead-unsupported-function",
        "title": "Dead / unsupported function in formula",
        "explain": (
            "A formula calls a function Workiva's engine REJECTS on xlsx import -> #NAME?, so "
            "the formula stops computing entirely. The dynamic-array / spill family "
            "(FILTER, SORT, UNIQUE, LAMBDA, LET, VSTACK, SEQUENCE, TAKE, DROP...), INDIRECT, the "
            "volatile positional ROW()/COLUMN()/OFFSET(), an internal-anchor HYPERLINK(\"#...), and "
            "the TRUE()/FALSE() CALL form are all unsupported. On one county FY25 engagement, "
            "thousands of FALSE() calls were the single biggest import blocker. It is the highest-weighted external corpus-audit detector "
            "because a #NAME? breaks the line completely, not just on round-trip."
        ),
        "judgment": (
            "surfaced -- rewrite each call to a supported equivalent; the replacement is a "
            "per-function judgment, never a blanket auto-rewrite."
        ),
        "suggested_action": (
            "Replace each unsupported call: INDIRECT -> an explicit sheet ref; dynamic-array "
            "spills (FILTER/SORT/UNIQUE/LET/LAMBDA) -> SUMIFS / INDEX+MATCH; ROW()/COLUMN()/OFFSET() "
            "-> a fixed reference or a row-control cell; TRUE()/FALSE() -> the bare TRUE/FALSE "
            "literal; internal-anchor HYPERLINK -> a UI Insert-Link. Re-run the formula scan after "
            "the rewrite to confirm the cell is clean."
        ),
        "trap_refs": [
            _trap(
                "indirect-hyperlink-unsupported",
                "INDIRECT and internal-sheet-anchor HYPERLINK evaluate to #NAME? on import -- "
                "hardcode sheet names or use the UI Insert-Link.",
            ),
            _trap(
                "true-false-call-form-import-blocker",
                "Workiva accepts the TRUE/FALSE LITERALS but rejects the TRUE()/FALSE() call form on "
                "import -- drop the parentheses (one production engagement: thousands of FALSE() calls blocked import).",
            ),
        ],
        "fix_refs": [],
    },
    ("degenerate-placeholder-formula", "surfaced", None): {
        "pathway_id": "formula.degenerate-placeholder",
        "title": "Degenerate placeholder formula",
        "explain": (
            "A governed formula cell contains only a placeholder constant shape (=0, =1+1, "
            "=100-100, or other constant-only arithmetic). It looks like a formula, so it can slip "
            "past formula-presence reviews and survive roll-forward, but it does not pull from the "
            "TB, map, Formula Reference, or a documented input."
        ),
        "judgment": (
            "surfaced -- replace the placeholder with the intended governed formula, or document "
            "and move the value to an input/support cell. Do not auto-rewrite; the right source is "
            "architecture-specific."
        ),
        "suggested_action": (
            "Trace the row/column formula pattern and compare neighboring governed rows. If this is "
            "a temporary zero or test stub, rebuild the formula from the section pattern; if it is an "
            "intentional manual input, move it out of the formula lane and document the input owner."
        ),
        "trap_refs": [
            _trap(
                "placeholder-formula-rollforward-debt",
                "Constant-only formulas look governed but do not update with source data; they are "
                "easy to carry into the next roll-forward unnoticed.",
            ),
        ],
        "fix_refs": [],
    },
}

# run_checks FAIL pathways (read-only diagnostics — no Wingman auto-fix lane).
CHECK_PATHWAYS: dict[str, dict[str, Any]] = {
    "acctmap_gaps": {
        "pathway_id": "data.acctmap-gaps",
        "title": "AcctMap gaps (unmapped GL)",
        "explain": (
            "Trial balance GL codes with material balances are not mapped in AcctMap. "
            "Statement tieouts and rollforwards will miss these amounts until mapped."
        ),
        "judgment": "surfaced — data integrity; map GL → SLC/SAE in AcctMap before tieout green.",
        "suggested_action": "Open AcctMap; add rows for listed GL codes; re-run data suite.",
        "trap_refs": [
            _trap(
                "acctmap-per-fund-miss",
                "Per-fund AcctMap gaps often hide behind aggregate TB green — scan by fund.",
            ),
        ],
        "fix_refs": [
            _fix("fix-acctmap-gaps", "Batch AcctMap writes one fund at a time with readback."),
        ],
    },
    "formula_consistency": {
        "pathway_id": "formula.formula-consistency",
        "title": "Formula consistency deviants",
        "explain": (
            "Rows on a statement sheet use a different formula pattern than their peers in the "
            "same column — often a copy/paste miss or a partial remap."
        ),
        "judgment": "surfaced — structural formula judgment; not a safe auto-fix.",
        "suggested_action": "Compare deviant rows to the dominant pattern; align formula text or restore source links.",
        "trap_refs": [
            _trap(
                "formula-consistency-col-scope",
                "Check the configured formula column (often D; face sheets may use G/I/K/M/O).",
            ),
        ],
        "fix_refs": [
            _fix("fix-1-remap", "After TB remap, re-run formula_consistency on affected stmt sheets."),
        ],
    },
    "validate_recon": {
        "pathway_id": "formula.validate-recon",
        "title": "Reconciliation validation FAIL",
        "explain": (
            "A recon sheet section total does not tie to its detail lines, or hardcoded values "
            "replace live formulas — common after manual overrides."
        ),
        "judgment": "surfaced — recon integrity; requires formula/source review.",
        "suggested_action": "Open the recon sheet; fix section formulas or replace hardcoded cells with links.",
        "trap_refs": [],
        "fix_refs": [],
    },
    "tieout": {
        "pathway_id": "tieout.suite-fail",
        "title": "Live tieout FAIL",
        "explain": (
            "TieoutSuite compared calculated workbook values to published document targets and "
            "found |delta| above the configured WARN threshold."
        ),
        "judgment": "surfaced — financial tieout break; diagnose before any write.",
        "suggested_action": "Inspect FAIL rows in tieout scorecard; trace calc vs pub for largest deltas first.",
        "trap_refs": [
            _trap(
                "tieout-publish-lag",
                "Stale publish can mimic tieout FAIL — verify ownLinks + doc allLinks before rewriting formulas.",
            ),
        ],
        "fix_refs": [
            _fix("fix-16-dl-publish", "After source fix, publish SS + doc links and re-run tieout suite."),
        ],
    },
    "checks_sheet": {
        "pathway_id": "checks.master-gate",
        "title": "CHECKS sheet master gate",
        "explain": (
            "The in-workbook CHECKS master gate is non-zero — structural validation flags are "
            "blocking delivery until cleared."
        ),
        "judgment": "surfaced — layer-0 gate; fix underlying CHECKS rows before tieout/export.",
        "suggested_action": "Open CHECKS sheet; resolve flagged rows; confirm MASTER_GATE reads 0.",
        "trap_refs": [],
        "fix_refs": [],
    },
    "no_xlsx_resolved": {
        "pathway_id": "config.no-xlsx",
        "title": "Snapshot not resolved",
        "explain": (
            "run_checks could not resolve an xlsx snapshot for data/formula suites — "
            "projects.json or the snapshot-loader path may be misconfigured."
        ),
        "judgment": "surfaced — config blocker; data/formula checks did not run.",
        "suggested_action": "Run pull_snapshot / load_live in the client repo; confirm projects.json client slug.",
        "trap_refs": [],
        "fix_refs": [],
    },
    "unknown": {
        "pathway_id": "checks.unknown",
        "title": "run_checks FAIL",
        "explain": "A run_checks suite reported FAIL — see detail line.",
        "judgment": "surfaced — review run_checks report.",
        "suggested_action": "Open the run_checks report for full context.",
        "trap_refs": [],
        "fix_refs": [],
    },
    # Hardening-gate evaluate() FAIL kinds (dry-run bridge).
    "hardening_gate_parity": {
        "pathway_id": "hardening-gate.parity-fail",
        "title": "Hardening gate parity FAIL",
        "explain": (
            "A parity anchor cell on the local snapshot does not match the expected post-"
            "migration value — hardening would break tieout if applied blindly."
        ),
        "judgment": "surfaced — value parity gate; diagnose before CONFIRM=1 hardening.",
        "suggested_action": "Trace the anchor cell formula inputs; fix root cause, re-pull snapshot, re-run.",
        "trap_refs": [
            _trap(
                "hardening-gate-parity-stale-snap",
                "Stale xlsx snapshot mimics parity FAIL — run pull_snapshot before evaluate.",
            ),
        ],
        "fix_refs": [],
    },
    "hardening_gate_patch_refs": {
        "pathway_id": "hardening-gate.patch-refs",
        "title": "Formulas still reference retired patch zone",
        "explain": (
            "Face formulas still reference cells in the retired helper patch bands — "
            "criteria must move to the row-local criteria columns before hardening."
        ),
        "judgment": "surfaced — structural hardening-gate migration debt.",
        "suggested_action": "Open listed region; remap criteria to the row-local criteria columns; clear the patch bank.",
        "trap_refs": [],
        "fix_refs": [
            _fix(
                "fix-hardening-gate",
                "Run workiva_hardening_gate.py dry-run first; CONFIRM=1 only after parity green.",
            ),
        ],
    },
    "hardening_gate_criteria_bank": {
        "pathway_id": "hardening-gate.criteria-bank",
        "title": "Criteria bank not retired",
        "explain": (
            "The combining statement still has non-empty cells in the legacy criteria-bank range — "
            "it must be cleared after row-local GL splits are wired."
        ),
        "judgment": "surfaced — helper-block retirement gate.",
        "suggested_action": "Confirm row-local criteria on affected rows; clear the legacy criteria-bank range.",
        "trap_refs": [],
        "fix_refs": [],
    },
    "hardening_gate_missing_snapshot": {
        "pathway_id": "hardening-gate.no-snapshot",
        "title": "ACFR preset snapshot missing",
        "explain": (
            "The hardening-gate evaluate() could not read the local xlsx snapshot — the snapshot pull "
            "must run in the ACFR preset repo before a hardening dry-run is meaningful."
        ),
        "judgment": "surfaced — config blocker; evaluate did not run.",
        "suggested_action": "Run the snapshot pull in the ACFR preset repo; re-run the hardening gate.",
        "trap_refs": [],
        "fix_refs": [],
    },
}


def _broken_subkind(detail: str) -> str:
    m = _ERROR_RE.search(detail or "")
    if not m:
        return "other"
    tok = m.group(0).upper()
    if tok == "#REF!":
        return "ref"
    if tok == "#NAME?":
        return "name"
    if tok == "#VALUE!":
        return "value"
    if tok == "#SHEET!":
        return "sheet"
    return "other"


def lookup_check_pathway(check: str, detail: str = "") -> dict[str, Any]:
    """Return pathway metadata for a run_checks FAIL row."""
    key = (check or "unknown").strip().lower()
    if key not in CHECK_PATHWAYS:
        key = "unknown"
    meta = dict(CHECK_PATHWAYS[key])
    if key == "tieout" and detail and "Δ=" in detail:
        meta = {
            **meta,
            "pathway_id": "tieout.line-fail",
            "title": "Tieout line FAIL",
            "explain": (
                "This statement line's calculated value differs from the published target beyond "
                "tolerance. Trace formula inputs, fund scope, and publish state."
            ),
            "suggested_action": f"Investigate: {detail}",
        }
    elif key.startswith("hardening_gate_") and detail:
        meta["suggested_action"] = detail
    elif detail and key == "unknown":
        meta["suggested_action"] = detail
    return meta


def lookup_pathway(
    kind: str,
    fix_lane: str,
    detail: str = "",
    *,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return pathway metadata for a detector kind + lane (+ optional detail/target for subkind)."""
    if kind == "broken-ref":
        subkind = _broken_subkind(detail)
    elif kind == "display-wrapper":
        subkind = display_wrapper.wrapper_subkind(detail, target)
    else:
        subkind = None
    key = (kind, fix_lane, subkind if kind in ("broken-ref", "display-wrapper") else None)
    meta = PATHWAYS.get(key)
    if meta is None and kind == "broken-ref":
        meta = PATHWAYS.get(("broken-ref", fix_lane, "other"))
    if meta is None and kind == "display-wrapper":
        meta = PATHWAYS.get(("display-wrapper", fix_lane, "other"))
    if meta is None:
        meta = {
            "pathway_id": f"unknown.{kind}",
            "title": kind.replace("-", " ").title(),
            "explain": "Unclassified detector finding — review manually.",
            "judgment": f"{fix_lane} — no playbook mapping yet.",
            "suggested_action": "Inspect in Workiva and classify before fix.",
            "trap_refs": [],
            "fix_refs": [],
        }
    return dict(meta)

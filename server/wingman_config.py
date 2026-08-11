#!/usr/bin/env python3
"""Wingman service config — presets and safe-fix lane contract for the extension."""
from __future__ import annotations

import client_preset

# Kinds accepted by POST /fix and POST /apply (ADR-0002 safe-auto lane).
SAFE_FIX_KINDS: tuple[str, ...] = (
    "low-contrast",
    "label-hygiene",
    "negative-without-parens",
    "junk-decimal",
    "missing-thousands-separator",
    "number-on-accounting-column",
    "precision-mismatch",
    "prefix-mismatch",
    "zero-display-mismatch",
    "year-automatic-coercion",
)


def service_config() -> dict:
    """Public GET /config payload."""
    ss = client_preset.acfr_preset_ss_id()
    return {
        "safe_fix_kinds": list(SAFE_FIX_KINDS),
        "presets": {
            "acfr": {
                "spreadsheetId": ss,
                "label": "ACFR preset",
            },
        },
    }

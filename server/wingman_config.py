#!/usr/bin/env python3
"""Wingman service config — presets and safe-fix lane contract for the extension."""
from __future__ import annotations

import os

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


def read_only_enabled() -> bool:
    """Explicit false values opt out; a misspelled nonempty value fails closed."""
    return os.environ.get("WINGMAN_READ_ONLY", "").strip().lower() not in ("", "0", "false", "no", "off")


def service_config() -> dict:
    """Public GET /config payload."""
    ss = client_preset.acfr_preset_ss_id()
    return {
        "read_only": read_only_enabled(),
        "safe_fix_kinds": [] if read_only_enabled() else list(SAFE_FIX_KINDS),
        "presets": {
            "acfr": {
                "spreadsheetId": ss,
                "label": "ACFR preset",
            },
        },
    }

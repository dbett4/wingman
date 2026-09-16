#!/usr/bin/env python3
"""Tests for server/wingman_config.py."""
from __future__ import annotations

import unittest

import client_preset
import wingman_config


class WingmanConfigTests(unittest.TestCase):
    def test_safe_fix_kinds(self):
        self.assertIn("low-contrast", wingman_config.SAFE_FIX_KINDS)
        self.assertIn("junk-decimal", wingman_config.SAFE_FIX_KINDS)
        self.assertIn("missing-thousands-separator", wingman_config.SAFE_FIX_KINDS)
        self.assertIn("prefix-mismatch", wingman_config.SAFE_FIX_KINDS)
        self.assertNotIn("zero-display-mismatch", wingman_config.SAFE_FIX_KINDS)
        self.assertIn("year-automatic-coercion", wingman_config.SAFE_FIX_KINDS)
        self.assertEqual(len(wingman_config.SAFE_FIX_KINDS), 9)

    def test_service_config_acfr_preset(self):
        cfg = wingman_config.service_config()
        self.assertEqual(
            cfg["presets"]["acfr"]["spreadsheetId"],
            client_preset.acfr_preset_ss_id(),
        )
        self.assertEqual(cfg["presets"]["acfr"]["label"], "ACFR preset")
        self.assertEqual(cfg["safe_fix_kinds"], list(wingman_config.SAFE_FIX_KINDS))


if __name__ == "__main__":
    unittest.main()

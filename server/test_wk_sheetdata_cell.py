#!/usr/bin/env python3
"""Unit tests for targeted sheetdata cell fetch ($cellrange)."""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wk_client as wk  # noqa: E402


class SheetdataCellTests(unittest.TestCase):
    def test_get_sheetdata_cell_builds_cellrange_query(self):
        with patch.object(wk, "_get", return_value={"data": {}}) as mock_get:
            wk.get_sheetdata_cell("ss", "sh", "c105", "tok", None)
        path = mock_get.call_args[0][0]
        self.assertIn("$cellrange=C105%3AC105", path)
        self.assertIn("$maxcellsperpage=1", path)

    def test_get_sheetdata_cell_strips_range_notation(self):
        with patch.object(wk, "_get", return_value={"data": {}}) as mock_get:
            wk.get_sheetdata_cell("ss", "sh", "B2:D10", "tok", None)
        path = mock_get.call_args[0][0]
        self.assertIn("$cellrange=B2%3AB2", path)


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

import wingman_receipts as wr


class WingmanReceiptTests(unittest.TestCase):
    def test_action_summary_redacts_cell_values(self):
        rec = wr.build_action_receipt(
            spreadsheet_id="ss-secret",
            sheet_id="sheet-secret",
            addr="B7",
            kind="label-hygiene",
            confirm=True,
            result={
                "status": "applied",
                "before": "Revenue 123.45 ",
                "after": "Revenue 123.45",
                "readback": "Revenue 123.45",
                "valueFormat": {"precision": {"value": 0}},
            },
            arid_status="Account/expected",
            safety_status="dummy-allowlisted",
        )
        blob = json.dumps(rec)
        self.assertNotIn("ss-secret", blob)
        self.assertNotIn("Revenue", blob)
        self.assertIn("workbookHash", rec)
        self.assertEqual(rec["status"], "applied")
        self.assertEqual(rec["result"]["before"]["redactedText"], True)

    def test_write_action_receipt_appends_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            rec = wr.build_action_receipt(
                spreadsheet_id="ss", sheet_id="sh", addr="A1", kind="low-contrast",
                confirm=False, result={"status": "dry-run", "before": "#111", "after": "#fff"},
            )
            path = Path(wr.write_action_receipt(rec, out_dir=td))
            self.assertTrue(path.exists())
            line = path.read_text(encoding="utf-8").strip()
            loaded = json.loads(line)
            self.assertEqual(loaded["artifact"], "wingman-action-receipt")
            self.assertEqual(loaded["phase"], "dry-run")


if __name__ == "__main__":
    unittest.main()

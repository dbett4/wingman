#!/usr/bin/env python3
"""Vision layer integration tests (mock cell crops — no Workiva network)."""
from __future__ import annotations

import base64
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import defect_detect as dd
import detectors


def _render(text, fg, bg, w=120, h=26, align="left"):
    from PIL import Image, ImageDraw, ImageFont
    font_path = "/System/Library/Fonts/Helvetica.ttc"
    try:
        f = ImageFont.truetype(font_path, 13)
    except OSError:
        raise unittest.SkipTest("Helvetica not available")
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    box = d.textbbox((0, 0), text, font=f)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = {"center": (w - tw) // 2, "left": 3, "rightflush": w - tw - 1}[align]
    d.text((x, (h - th) // 2 - box[1]), text, fill=fg, font=f)
    return img


def _b64(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@unittest.skipUnless(dd.vision_available(), dd.vision_unavailable_reason() or "vision deps missing")
class VisionIntegrationTests(unittest.TestCase):
    BLACK = (0, 0, 0)
    WHITE = (255, 255, 255)
    MID = (136, 136, 136)

    def test_contrast_finding_shape(self):
        img = _render("Revenue", self.MID, self.WHITE)
        findings, errors = dd.scan_cell_images(
            {"B3": img},
            {"B3": {"type": "richText", "value": "Revenue"}},
        )
        self.assertEqual(errors, {})
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["kind"], "low-contrast")
        self.assertEqual(f["addr"], "B3")
        self.assertEqual(f["source"], "vision")
        self.assertEqual(f["fix_lane"], "surfaced")
        self.assertFalse(f["fixable"])

    def test_clipped_finding(self):
        img = _render("WayTooLongForThisCell", self.BLACK, self.WHITE, w=55)
        findings, _ = dd.scan_cell_images(
            {"C7": img},
            {"C7": {"type": "plainText", "value": "WayTooLongForThisCell"}},
        )
        kinds = {f["kind"] for f in findings}
        self.assertIn("clipped-text", kinds)

    def test_unknown_cell_not_silently_dropped(self):
        # Regression: a cropped cell absent from cells_by_addr is UNKNOWN, not known-empty.
        # The old code defaulted meta to {} and skipped it (value==""), dropping exactly the
        # cells vision exists to catch. With no API knowledge, pixel analysis must still run.
        img = _render("Revenue", self.MID, self.WHITE)
        findings, errors = dd.scan_cell_images({"B3": img})  # no cells_by_addr
        self.assertEqual(errors, {})
        self.assertEqual([f["kind"] for f in findings], ["low-contrast"])

    def test_api_confirmed_empty_cell_still_skipped(self):
        # Preserved behavior: when the API confirms the cell is empty (value="" + no formula),
        # skip pixel analysis even if the crop has stray ink.
        img = _render("Revenue", self.MID, self.WHITE)
        findings, _ = dd.scan_cell_images({"B3": img}, {"B3": {"type": "plainText", "value": ""}})
        self.assertEqual(findings, [])

    def test_merge_skips_fixable_api_contrast(self):
        api = [{
            "kind": "low-contrast", "addr": "A1", "severity": "medium",
            "detail": "#FFFFFF on #7F7F7F = 3.5:1 (below AA 4.5:1)",
            "fixable": True, "fix_lane": "safe-auto", "target": {"hex": "#000000", "ratio": 4.5},
        }]
        vision = [{
            "kind": "low-contrast", "addr": "A1", "severity": "high",
            "detail": "vision WCAG 3.5:1 < 4.5:1", "fixable": False,
            "fix_lane": "surfaced", "target": None, "source": "vision",
        }]
        merged = dd.merge_vision_findings(api, vision)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["fixable"])

    def test_merge_keeps_vision_for_richtext(self):
        api = [{
            "kind": "low-contrast", "addr": "A2", "severity": "medium",
            "detail": "#FFFFFF on #FFFFFF = 1.0:1 (below AA 4.5:1)",
            "fixable": False, "fix_lane": "surfaced", "target": None,
        }]
        vision = [{
            "kind": "low-contrast", "addr": "A2", "severity": "high",
            "detail": "vision WCAG 2.1:1 < 4.5:1", "fixable": False,
            "fix_lane": "surfaced", "target": None, "source": "vision",
        }]
        merged = dd.merge_vision_findings(api, vision, {"A2": {"type": "richText", "value": "x"}})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "vision")

    def test_apply_vision_layer_graceful_no_images(self):
        api = [{"kind": "broken-ref", "addr": "E1", "severity": "high",
                "detail": "formula result is #REF!", "fixable": False,
                "fix_lane": "surfaced", "target": None}]
        merged, meta = detectors.apply_vision_layer(api, None, {})
        self.assertEqual(merged, api)
        self.assertIn("skipped", meta)

    def test_apply_vision_layer_with_b64(self):
        img = _render("Revenue", self.MID, self.WHITE)
        b64 = _b64(img)
        api = []
        cells = {"D4": {"type": "richText", "value": "Revenue"}}
        merged, meta = detectors.apply_vision_layer(api, {"D4": b64}, cells)
        self.assertTrue(meta.get("applied"))
        self.assertGreaterEqual(meta.get("visionFindingCount", 0), 1)
        self.assertTrue(any(f["addr"] == "D4" for f in merged))

    def test_vision_merge_stats_net_new_clipped(self):
        api = []
        vision = [{
            "kind": "clipped-text", "addr": "Z1", "severity": "medium",
            "detail": "overflow", "fixable": False, "fix_lane": "surfaced",
            "target": None, "source": "vision",
        }]
        merged = dd.merge_vision_findings(api, vision)
        stats = dd.vision_merge_stats(api, vision, merged)
        self.assertEqual(stats["netNew"], 1)
        self.assertEqual(stats["netNewByKind"].get("clipped-text"), 1)
        self.assertEqual(stats["duplicates"], 0)

    def test_vision_merge_stats_duplicate_contrast(self):
        api = [{
            "kind": "low-contrast", "addr": "A1", "severity": "medium",
            "detail": "api", "fixable": True, "fix_lane": "safe-auto", "target": {"hex": "#000"},
        }]
        vision = [{
            "kind": "low-contrast", "addr": "A1", "severity": "high",
            "detail": "vision", "fixable": False, "fix_lane": "surfaced",
            "target": None, "source": "vision",
        }]
        merged = dd.merge_vision_findings(api, vision)
        stats = dd.vision_merge_stats(api, vision, merged)
        self.assertEqual(stats["netNew"], 0)
        self.assertEqual(stats["duplicates"], 1)

    def test_vision_merge_stats_upgraded_richtext_not_net_new(self):
        api = [{
            "kind": "low-contrast", "addr": "A2", "severity": "medium",
            "detail": "api surfaced", "fixable": False, "fix_lane": "surfaced", "target": None,
        }]
        vision = [{
            "kind": "low-contrast", "addr": "A2", "severity": "high",
            "detail": "vision detail", "fixable": False, "fix_lane": "surfaced",
            "target": None, "source": "vision",
        }]
        merged = dd.merge_vision_findings(api, vision, {"A2": {"type": "richText"}})
        stats = dd.vision_merge_stats(api, vision, merged)
        self.assertEqual(stats["netNew"], 0)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "vision")

    def test_apply_vision_layer_meta_includes_delta_keys(self):
        img = _render("Revenue", self.MID, self.WHITE)
        b64 = _b64(img)
        merged, meta = detectors.apply_vision_layer([], {"D4": b64}, {"D4": {"type": "richText"}})
        self.assertTrue(meta.get("applied"))
        for key in ("netNew", "duplicates", "netNewByKind", "visionFindingCount"):
            self.assertIn(key, meta)
        if meta.get("visionFindingCount", 0) > 0:
            self.assertGreaterEqual(meta.get("netNew", 0), 1)
            self.assertTrue(any(f["addr"] == "D4" for f in merged))

    def test_group_clipped_text(self):
        grouped = detectors.group_findings([{
            "kind": "clipped-text", "addr": "Z1", "severity": "medium",
            "detail": "ink reaches cell right edge (overflow)",
            "fixable": False, "fix_lane": "surfaced", "target": None, "source": "vision",
        }])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["kind"], "clipped-text")


if __name__ == "__main__":
    unittest.main()

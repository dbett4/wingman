#!/usr/bin/env python3
"""
Wingman improvement digest — mines the activity log (wingman_log.py) into actionable
signals for tuning the tool.

What it surfaces:
  - Detector volume: which kinds fire most (candidates for noise review).
  - Fix health per kind: applied vs reverted/mismatch (false-positive signal) vs refused
    (scope/guard signal) vs style-locked (cells the API can't own).
  - Flags: kinds whose revert+mismatch rate or refused rate crosses a threshold — these are
    the detectors/fixers most worth a human/agent look next.
  - Drift + vision net-new contribution.

This is the "logs improve the tool" loop: scan/fix continuously → digest → flags point at the
weakest detector → fix it → the next digest should show the flag clear.

Pure analysis over JSON lines; no network, no Workiva. CLI:
    python3 server/wingman_log_digest.py [--dir DIR] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# A fix landing in one of these means the WRITE itself failed verification — the strongest
# false-positive / fixer-quality signal.
_BAD_FIX_STATUSES = {"mismatch-reverted", "revert-failed", "readback-failed-reverted",
                     "readback-page-miss", "style-locked"}
_GOOD_FIX_STATUSES = {"applied"}
# Refusal class = the fixer declined or could not act on a flagged cell. `not-in-scan-page` belongs
# here (the detector flagged a cell the fixer cannot reach), not silently outside the denominator.
_REFUSED_STATUSES = {"refused", "not-in-scan-page"}
# Excluded from the flag denominator BY DESIGN: `no-fix-needed` (benign — cell already passes) and
# `dry-run` (a preview, not a decision). Unknown statuses are also excluded so they cannot dilute rates.
# Thresholds above which a kind is flagged for review (only once it has enough samples).
_MIN_SAMPLES = 5
_BAD_RATE_FLAG = 0.30
_REFUSED_RATE_FLAG = 0.50


def load_events(log_directory: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(log_directory.glob("wingman-events-*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return events


def _merge_counts(dst: dict, src: dict) -> None:
    for k, v in (src or {}).items():
        try:
            dst[k] = dst.get(k, 0) + int(v)
        except (TypeError, ValueError):
            continue


def build_digest(events: list[dict]) -> dict:
    scans = [e for e in events if e.get("event") == "scan"]
    fixes = [e for e in events if e.get("event") == "fix"]

    by_kind: dict[str, int] = {}
    by_lane: dict[str, int] = {}
    vision_net_new = 0
    truncated_scans = 0
    for s in scans:
        _merge_counts(by_kind, s.get("byKind"))
        _merge_counts(by_lane, s.get("byLane"))
        if s.get("visionNetNew"):
            try:
                vision_net_new += int(s["visionNetNew"])
            except (TypeError, ValueError):
                pass
        if s.get("truncated"):
            truncated_scans += 1

    # fix health per kind
    fix_health: dict[str, dict] = {}
    for f in fixes:
        kind = f.get("kind") or "unknown"
        h = fix_health.setdefault(kind, {"total": 0, "applied": 0, "bad": 0, "refused": 0,
                                         "noFixNeeded": 0, "dryRun": 0, "byStatus": {}})
        h["total"] += 1
        status = f.get("status") or "unknown"
        h["byStatus"][status] = h["byStatus"].get(status, 0) + 1
        if f.get("phase") == "dry-run":
            h["dryRun"] += 1
        if status in _GOOD_FIX_STATUSES:
            h["applied"] += 1
        elif status in _BAD_FIX_STATUSES:
            h["bad"] += 1
        elif status in _REFUSED_STATUSES:
            h["refused"] += 1
        elif status == "no-fix-needed":
            h["noFixNeeded"] += 1

    flags: list[dict] = []
    for kind, h in fix_health.items():
        decided = h["applied"] + h["bad"] + h["refused"]
        if decided < _MIN_SAMPLES:
            continue
        bad_rate = h["bad"] / decided if decided else 0.0
        refused_rate = h["refused"] / decided if decided else 0.0
        if bad_rate >= _BAD_RATE_FLAG:
            flags.append({"kind": kind, "signal": "high-revert", "rate": round(bad_rate, 2),
                          "hint": "writes fail verification — review the detector's target or the fixer guard"})
        if refused_rate >= _REFUSED_RATE_FLAG:
            flags.append({"kind": kind, "signal": "high-refused", "rate": round(refused_rate, 2),
                          "hint": "often refused (richText/merged/linked/style-locked) — tighten the detector so it stops flagging unfixable cells"})

    return {
        "scanCount": len(scans),
        "fixCount": len(fixes),
        "findingsByKind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "findingsByLane": by_lane,
        "truncatedScans": truncated_scans,
        "visionNetNew": vision_net_new,
        "fixHealthByKind": fix_health,
        "flags": flags,
    }


def render(digest: dict) -> str:
    lines = ["Wingman improvement digest",
             f"  scans={digest['scanCount']}  fixes={digest['fixCount']}"
             f"  truncatedScans={digest['truncatedScans']}  visionNetNew={digest['visionNetNew']}",
             "", "Findings by kind (volume):"]
    if digest["findingsByKind"]:
        for kind, n in digest["findingsByKind"].items():
            lines.append(f"  {n:>6}  {kind}")
    else:
        lines.append("  (none logged yet)")

    lines += ["", "Fix health by kind:"]
    if digest["fixHealthByKind"]:
        for kind, h in sorted(digest["fixHealthByKind"].items()):
            lines.append(f"  {kind}: total={h['total']} applied={h['applied']} "
                         f"bad={h['bad']} refused={h['refused']} no-fix={h['noFixNeeded']} dry-run={h['dryRun']}")
    else:
        lines.append("  (no fixes logged yet)")

    lines += ["", "Flags (act on these next):"]
    if digest["flags"]:
        for fl in digest["flags"]:
            lines.append(f"  [{fl['signal']}] {fl['kind']} ({fl['rate']:.0%}) — {fl['hint']}")
    else:
        lines.append("  none — nothing crossed the review threshold")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mine the Wingman activity log into an improvement digest.")
    ap.add_argument("--dir", default=os.environ.get("WINGMAN_LOG_DIR",
                                                    str(Path.home() / ".wingman" / "logs")))
    ap.add_argument("--json", action="store_true", help="emit the digest as JSON")
    args = ap.parse_args(argv)
    events = load_events(Path(args.dir))
    digest = build_digest(events)
    print(json.dumps(digest, indent=2) if args.json else render(digest))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

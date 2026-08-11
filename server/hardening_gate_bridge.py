#!/usr/bin/env python3
"""
Bridge Wingman queue to the workbook hardening gate (dry-run + evaluate).

Runs `workiva_hardening_gate.py` without CONFIRM (read-only plan) and evaluates
the local xlsx snapshot for parity / patch-ref / criteria-bank FAILs.

Scoped to the configured ACFR preset workbook — other workbooks skip gracefully.

Configure:
  WINGMAN_HARDENING_REPO_ROOT — ACFR preset repo root (default: WINGMAN_DATA_DIR)
  WINGMAN_HARDENING_SCRIPT   — path to workiva_hardening_gate.py
  WINGMAN_HARDENING_TIMEOUT  — subprocess seconds (default 180)
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, Callable

import checks_bridge


_ACFR_PRESET_SS = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
_DRY_RUN_OPS = re.compile(r"^(?:DRY RUN|LIVE):\s+(\d+)\s+operations planned", re.MULTILINE)


def _norm_ss(ss: str) -> str:
    return checks_bridge._norm_ss(ss)


def resolve_hardening_repo_root() -> pathlib.Path | None:
    """Return ACFR preset client repo root or None."""
    for key in (
        "WINGMAN_HARDENING_REPO_ROOT",
        "ACFR_PRESET_REPO_ROOT",
    ):
        raw = os.environ.get(key)
        if raw:
            p = pathlib.Path(raw).expanduser()
            if p.is_dir():
                return p.resolve()
    default = checks_bridge.client_preset.data_root() / "clients" / "riverton"
    if default.is_dir():
        return default.resolve()
    return None


def resolve_hardening_script() -> pathlib.Path | None:
    """Return path to workiva_hardening_gate.py or None."""
    raw = os.environ.get("WINGMAN_HARDENING_SCRIPT")
    if raw:
        p = pathlib.Path(raw).expanduser()
        if p.is_file():
            return p.resolve()
    root = resolve_hardening_repo_root()
    if root:
        cand = root / "scripts" / "workiva_hardening_gate.py"
        if cand.is_file():
            return cand.resolve()
    return None


def is_acfr_preset_spreadsheet(spreadsheet_id: str | None) -> bool:
    """True when spreadsheetId resolves to the built-in ACFR preset SS."""
    if not spreadsheet_id:
        return False
    if _norm_ss(spreadsheet_id) == _norm_ss(_ACFR_PRESET_SS):
        return True
    resolved = checks_bridge.resolve_working_dir(spreadsheet_id)
    return bool(resolved and resolved[1] == "acfr")


def parse_dry_run_operations(stdout: str) -> int | None:
    """Extract planned operation count from hardening script stdout."""
    m = _DRY_RUN_OPS.search(stdout or "")
    return int(m.group(1)) if m else None


def evaluation_to_fail_rows(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn evaluate() payload into Wingman fail_rows."""
    rows: list[dict[str, Any]] = []
    if not ev:
        return rows

    if ev.get("error"):
        rows.append({
            "check": "hardening_gate_missing_snapshot",
            "detail": str(ev["error"]),
            "source": "hardening_gate_eval",
        })
        return rows

    for p in ev.get("parity") or []:
        if p.get("pass"):
            continue
        sheet = str(p.get("sheet") or "")
        cell = str(p.get("cell") or "")
        exp = p.get("expected")
        act = p.get("actual")
        rows.append({
            "check": "hardening_gate_parity",
            "detail": f"{sheet} {cell}: expected {exp}, actual {act}",
            "sheet": sheet,
            "cell": cell,
            "expected": exp,
            "actual": act,
            "source": "hardening_gate_eval",
        })

    for region, hits in (ev.get("patch_refs") or {}).items():
        count = int(hits or 0)
        if count <= 0:
            continue
        rows.append({
            "check": "hardening_gate_patch_refs",
            "detail": f"{region}: {count} formula ref(s) into the retired helper band",
            "region": region,
            "hits": count,
            "source": "hardening_gate_eval",
        })

    ba = int(ev.get("criteria_bank_nonempty_cells") or 0)
    if ba > 0:
        rows.append({
            "check": "hardening_gate_criteria_bank",
            "detail": (
                f"BA200:BB207 criteria bank has {ba} non-empty cell(s) — "
                "retire before hardening green"
            ),
            "hits": ba,
            "source": "hardening_gate_eval",
        })

    return rows


def _default_runner(cmd: list[str], *, cwd: str, timeout: float, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env or {**os.environ, "PYTHONUTF8": "1"},
    )


def _run_evaluate(root: pathlib.Path, runner: Callable[..., subprocess.CompletedProcess[str]], timeout: float) -> dict[str, Any]:
    cmd = [
        sys.executable,
        "-c",
        (
            "import json, sys; sys.path.insert(0, 'scripts'); "
            "from workiva_hardening_gate import evaluate; "
            "print(json.dumps(evaluate(), default=str))"
        ),
    ]
    proc = runner(cmd, cwd=str(root), timeout=timeout)
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return {"error": "evaluate() produced no output"}
    try:
        return json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return {"error": "evaluate() stdout was not valid JSON"}


def run_hardening_gate_suite(
    spreadsheet_id: str | None,
    *,
    timeout: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    """
    Dry-run hardening plan + snapshot evaluate for the workbook hardening gate.

    Read-only — never sets CONFIRM=1.
    """
    t0 = time.time()
    meta: dict[str, Any] = {
        "requested": True,
        "suite": "hardening_gate",
        "applied": False,
        "fail_count": 0,
        "fail_rows": [],
        "skipped": None,
        "error": None,
        "exit_code": None,
        "planned_operations": None,
        "working_dir": None,
        "project": "acfr",
        "elapsed_s": 0.0,
    }

    if not is_acfr_preset_spreadsheet(spreadsheet_id):
        meta["skipped"] = (
            "The hardening gate is ACFR-preset only — open the preset prod workbook "
            f"({_ACFR_PRESET_SS[:8]}…)"
        )
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    script = resolve_hardening_script()
    if script is None:
        meta["skipped"] = (
            "workiva_hardening_gate.py not found — set WINGMAN_HARDENING_REPO_ROOT or "
            "WINGMAN_HARDENING_SCRIPT"
        )
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    root = script.parent.parent
    meta["working_dir"] = str(root)

    timeout_s = float(timeout if timeout is not None else os.environ.get("WINGMAN_HARDENING_TIMEOUT", "180"))
    run = runner or _default_runner
    dry_env = {**os.environ, "PYTHONUTF8": "1"}
    dry_env.pop("CONFIRM", None)

    try:
        proc = run([sys.executable, str(script)], cwd=str(root), timeout=timeout_s, env=dry_env)
    except subprocess.TimeoutExpired:
        meta["skipped"] = f"hardening gate timed out after {timeout_s:.0f}s"
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta
    except OSError as exc:
        meta["skipped"] = f"hardening gate spawn failed: {exc}"
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    meta["applied"] = True
    meta["exit_code"] = proc.returncode
    meta["planned_operations"] = parse_dry_run_operations(proc.stdout or "")

    if proc.returncode not in (0, None):
        err = (proc.stderr or proc.stdout or "").strip()
        meta["error"] = err[-500:] if err else f"hardening script exit {proc.returncode}"

    try:
        ev = _run_evaluate(root, run, timeout_s)
    except subprocess.TimeoutExpired:
        meta["error"] = meta.get("error") or f"evaluate() timed out after {timeout_s:.0f}s"
        ev = {}
    except OSError as exc:
        meta["error"] = meta.get("error") or f"evaluate() spawn failed: {exc}"
        ev = {}

    fail_rows = evaluation_to_fail_rows(ev)
    meta["fail_rows"] = fail_rows
    meta["fail_count"] = len(fail_rows)
    meta["evaluation"] = {
        k: ev.get(k)
        for k in ("parity_pass", "criteria_bank_nonempty_cells", "error")
        if k in ev
    }
    meta["elapsed_s"] = round(time.time() - t0, 2)
    return meta

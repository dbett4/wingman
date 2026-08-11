#!/usr/bin/env python3
"""
Bridge Wingman queue to an external checks CLI (read-only subprocess).

Runs `run_checks.py --json --suite <suite>` in a client working directory resolved
from spreadsheetId (projects.json ss_id scan) or env overrides. Parses JSON stdout
and the markdown report for FAIL rows. Degrades gracefully when the checks toolkit
is not installed.

Configure:
  WINGMAN_CHECKS_SCRIPTS    — directory containing run_checks.py (default: WINGMAN_DATA_DIR)
  WINGMAN_CHECKS_WORKING_DIR — force client working dir (projects.json + wk.py)
  WINGMAN_CHECKS_PROJECT    — client slug when working dir is set via env only
  CHECKS_CLIENT_DIR         — alias for working dir (run_checks convention)
  WINGMAN_CHECKS_TIMEOUT    — subprocess seconds (default 120)
  WINGMAN_CHECKS_SS_MAP     — JSON map {spreadsheetId: workingDirPath}
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

import client_preset

import tieout_enrich

# Slug → working directory (under the Wingman data root; see client_preset.data_root).
_CLIENT_WORKING_DIRS: dict[str, str] = {
    "acfr": str(client_preset.data_root() / "clients" / "riverton"),
}

_FAIL_LINE = re.compile(r"^\s*\[FAIL\]\s+(.+)$")
_TIEOUT_FAIL_ROW = re.compile(
    r"^\|\s*(?P<stmt>[^|]+)\|\s*(?P<col>[^|]+)\|\s*(?P<label>[^|]+)\|\s*"
    r"(?P<calc>[^|]+)\|\s*(?P<pub>[^|]+)\|\s*(?P<delta>[^|]+)\|\s*$"
)


def _norm_ss(ss: str) -> str:
    return re.sub(r"[^0-9a-fA-F]", "", ss or "").lower()


def resolve_run_checks_script() -> pathlib.Path | None:
    """Return path to run_checks.py or None if not installed."""
    for key in ("WINGMAN_CHECKS_SCRIPTS", "ACFR_CHECKS_SCRIPTS"):
        raw = os.environ.get(key)
        if raw:
            p = pathlib.Path(raw).expanduser()
            cand = p / "run_checks.py" if p.is_dir() else p
            if cand.is_file():
                return cand.resolve()
    default = client_preset.data_root() / "checks" / "scripts" / "run_checks.py"
    if default.is_file():
        return default.resolve()
    return None


def _ss_map() -> dict[str, str]:
    merged = dict(client_preset.default_ss_map())
    raw = os.environ.get("WINGMAN_CHECKS_SS_MAP")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                merged.update({str(k): str(v) for k, v in data.items()})
        except json.JSONDecodeError:
            pass
    return merged


def resolve_working_dir(spreadsheet_id: str | None) -> tuple[str, str | None] | None:
    """
    Resolve (working_dir, project_slug) for run_checks.

    Priority: WINGMAN_CHECKS_WORKING_DIR / CHECKS_CLIENT_DIR → WINGMAN_CHECKS_SS_MAP →
    scan known client dirs for matching projects.json ss_id.
    """
    for key in ("WINGMAN_CHECKS_WORKING_DIR", "CHECKS_CLIENT_DIR"):
        wd = os.environ.get(key)
        if wd and pathlib.Path(wd).expanduser().is_dir():
            slug = os.environ.get("WINGMAN_CHECKS_PROJECT")
            return str(pathlib.Path(wd).expanduser().resolve()), slug

    if spreadsheet_id:
        mapped = _ss_map().get(spreadsheet_id) or _ss_map().get(_norm_ss(spreadsheet_id))
        if mapped and pathlib.Path(mapped).expanduser().is_dir():
            slug = os.environ.get("WINGMAN_CHECKS_PROJECT")
            if not slug and _norm_ss(spreadsheet_id) == _norm_ss(client_preset.acfr_preset_ss_id()):
                slug = "acfr"
            return str(pathlib.Path(mapped).expanduser().resolve()), slug

        target = _norm_ss(spreadsheet_id)
        if target:
            for slug, wd in _CLIENT_WORKING_DIRS.items():
                pj = pathlib.Path(wd) / "projects.json"
                if not pj.is_file():
                    continue
                try:
                    cfg = json.loads(pj.read_text(encoding="utf-8"))
                    ss = _norm_ss(str(cfg.get("ss_id") or cfg.get("spreadsheet_id") or ""))
                    if ss and ss == target:
                        return str(pathlib.Path(wd).resolve()), slug
                except (OSError, json.JSONDecodeError):
                    continue
    return None


def parse_report_fails(report_text: str) -> list[dict[str, Any]]:
    """Extract structured FAIL rows from a run_checks markdown report."""
    rows: list[dict[str, Any]] = []
    in_tieout_fails = False
    for line in (report_text or "").splitlines():
        m = _FAIL_LINE.match(line)
        if m:
            detail = m.group(1).strip()
            name = detail.split(":", 1)[0].strip()
            rows.append({
                "check": _normalize_check_name(name),
                "raw_check": name,
                "detail": detail,
                "source": "report",
            })
            in_tieout_fails = name.lower().startswith("tieout")
            continue
        if line.strip().startswith("## FAILs"):
            in_tieout_fails = True
            continue
        if in_tieout_fails and line.strip().startswith("## "):
            in_tieout_fails = False
            continue
        if in_tieout_fails:
            tm = _TIEOUT_FAIL_ROW.match(line)
            if tm and tm.group("stmt").strip() not in ("Stmt", "---"):
                rows.append({
                    "check": "tieout",
                    "raw_check": "tieout",
                    "detail": (
                        f"{tm.group('stmt').strip()} {tm.group('col').strip()} "
                        f"{tm.group('label').strip()} Δ={tm.group('delta').strip()}"
                    ),
                    "source": "tieout_table",
                    "stmt": tm.group("stmt").strip(),
                    "column": tm.group("col").strip(),
                    "label": tm.group("label").strip(),
                    "calc": tm.group("calc").strip(),
                    "pub": tm.group("pub").strip(),
                    "delta": tm.group("delta").strip(),
                })
    return rows


def _normalize_check_name(name: str) -> str:
    n = (name or "").strip().lower()
    if n.startswith("formula_consistency"):
        return "formula_consistency"
    if n.startswith("validate_recon"):
        return "validate_recon"
    if n.startswith("tieout"):
        return "tieout"
    if n.startswith("checkssheet") or n.startswith("checks sheet"):
        return "checks_sheet"
    return re.sub(r"[^a-z0-9_]+", "_", n).strip("_") or "unknown"


def _default_runner(cmd: list[str], *, cwd: str, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONUTF8": "1"},
    )


def ingest_scorecard(
    spreadsheet_id: str | None,
    *,
    suite: str = "scorecard",
    project: str | None = None,
    working_dir: str | None = None,
    scorecard_path: str | None = None,
) -> dict[str, Any]:
    """
    Surface an EXISTING tieout scorecard JSON as fail_rows — no subprocess, no live Workiva.

    This is the read-only "watcher/check output" bridge: scheduled ACFR check runs and
    tieout runs already persist a tieout scorecard JSON. When the live run_checks subprocess
    cannot run (script missing, no working dir, or timeout) Wingman can still surface the
    existing diagnostics as actionable queue items by reading that artifact directly.

    Returns the same metadata shape as run_checks_suite() so the result drops straight into
    diagnose.merge_queue_with_checks(). ``source`` is set to ``"scorecard"`` and ``exit_code``
    is None (no process was spawned).

    Configure: WINGMAN_TIEOUT_SCORECARD overrides the scorecard path.
    """
    t0 = time.time()
    suite = (suite or "scorecard").strip().lower()
    meta: dict[str, Any] = {
        "requested": True,
        "suite": suite,
        "source": "scorecard",
        "applied": False,
        "fail_count": 0,
        "fail_rows": [],
        "skipped": None,
        "error": None,
        "exit_code": None,
        "report_path": None,
        "working_dir": working_dir,
        "project": project,
        "elapsed_s": 0.0,
        "tieout_enrich": None,
    }

    # Resolve client context when the caller did not supply it.
    if project is None or working_dir is None:
        resolved = resolve_working_dir(spreadsheet_id)
        if resolved:
            wd, slug = resolved
            working_dir = working_dir or wd
            project = project or slug
            meta["working_dir"] = working_dir
            meta["project"] = project

    if scorecard_path:
        cand = pathlib.Path(scorecard_path).expanduser()
        path = cand.resolve() if cand.is_file() else None
    else:
        path = tieout_enrich.resolve_scorecard_path(working_dir, project)

    if path is None:
        meta["skipped"] = (
            "no tieout scorecard JSON found — set WINGMAN_TIEOUT_SCORECARD to the scorecard path"
        )
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    try:
        scorecard = tieout_enrich.load_scorecard(path)
    except (OSError, json.JSONDecodeError) as exc:
        meta["error"] = f"scorecard load failed: {exc}"
        meta["report_path"] = str(path)
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    fail_rows = tieout_enrich.scorecard_fail_rows(scorecard)
    meta["applied"] = True
    meta["report_path"] = str(path)
    meta["fail_rows"] = fail_rows
    meta["fail_count"] = len(fail_rows)

    fail_n = sum(1 for r in scorecard.get("results") or [] if r.get("status") == "FAIL")
    zero_n = sum(1 for r in scorecard.get("results") or [] if r.get("status") == "ZERO")
    enrich_meta: dict[str, Any] = {
        "applied": True,
        "source": "scorecard_ingest",
        "path": str(path),
        "scorecard_fail": fail_n,
        "scorecard_zero": zero_n,
        "enriched_count": sum(1 for r in fail_rows if r.get("jumpHint")),
    }
    gen_at = tieout_enrich.scorecard_generated_at(scorecard)
    if gen_at:
        meta["scorecard_generated_at"] = gen_at
        enrich_meta["scorecard_generated_at"] = gen_at
    meta["tieout_enrich"] = enrich_meta

    meta["elapsed_s"] = round(time.time() - t0, 2)
    return meta


def _scorecard_eligible(project: str | None) -> bool:
    """Auto-fallback to a saved scorecard only for ACFR, or when a path override is set."""
    if os.environ.get("WINGMAN_TIEOUT_SCORECARD"):
        return True
    return (project or "").strip().lower() == "acfr"


def _scorecard_fallback(
    spreadsheet_id: str | None,
    suite: str,
    project: str | None,
    working_dir: str | None,
    *,
    reason: str,
) -> dict[str, Any] | None:
    """Surface an existing scorecard when the live subprocess could not run; else None."""
    if not _scorecard_eligible(project):
        return None
    fb = ingest_scorecard(spreadsheet_id, suite=suite, project=project, working_dir=working_dir)
    if fb.get("applied"):
        fb["live_skipped"] = f"{reason} — surfaced existing scorecard JSON instead"
        return fb
    return None


def run_checks_suite(
    spreadsheet_id: str | None,
    *,
    suite: str = "tieout",
    timeout: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, Any]:
    """
    Run run_checks --json for the resolved client. Read-only — no Workiva writes
    from Wingman (run_checks may read live Workiva for tieout suite).

    Returns metadata dict suitable for diagnose.merge_queue_with_checks().
    """
    t0 = time.time()
    suite = (suite or "tieout").strip().lower()
    if suite == "scorecard":
        # Explicit "surface the existing scorecard, never run anything live" mode.
        return ingest_scorecard(spreadsheet_id, suite=suite)
    meta: dict[str, Any] = {
        "requested": True,
        "suite": suite,
        "applied": False,
        "fail_count": 0,
        "fail_rows": [],
        "skipped": None,
        "error": None,
        "exit_code": None,
        "report_path": None,
        "working_dir": None,
        "project": None,
        "elapsed_s": 0.0,
        "tieout_enrich": None,
    }

    script = resolve_run_checks_script()
    resolved = resolve_working_dir(spreadsheet_id)
    project_hint = resolved[1] if resolved else None
    wd_hint = resolved[0] if resolved else None

    if script is None:
        fb = _scorecard_fallback(
            spreadsheet_id, suite, project_hint, wd_hint,
            reason="external checks CLI (run_checks.py) not found",
        )
        if fb is not None:
            return fb
        meta["skipped"] = (
            "external checks CLI not found — set WINGMAN_CHECKS_SCRIPTS to its scripts/ dir"
        )
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    if not resolved:
        fb = _scorecard_fallback(
            spreadsheet_id, suite, project_hint, wd_hint,
            reason="no client working dir",
        )
        if fb is not None:
            return fb
        meta["skipped"] = (
            "no client working dir for this spreadsheet — set WINGMAN_CHECKS_WORKING_DIR "
            "or WINGMAN_CHECKS_SS_MAP"
        )
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    wd, project = resolved
    meta["working_dir"] = wd
    meta["project"] = project

    timeout_s = float(timeout if timeout is not None else os.environ.get("WINGMAN_CHECKS_TIMEOUT", "120"))
    cmd = [sys.executable, str(script), "--json", "--suite", suite]
    if project:
        cmd.extend(["--project", project])
    else:
        cmd.extend(["--working-dir", wd])

    run = runner or _default_runner
    try:
        proc = run(cmd, cwd=wd, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        fb = _scorecard_fallback(
            spreadsheet_id, suite, project, wd,
            reason=f"run_checks timed out after {timeout_s:.0f}s",
        )
        if fb is not None:
            return fb
        meta["skipped"] = f"run_checks timed out after {timeout_s:.0f}s"
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta
    except OSError as exc:
        meta["skipped"] = f"run_checks spawn failed: {exc}"
        meta["elapsed_s"] = round(time.time() - t0, 2)
        return meta

    meta["applied"] = True
    meta["exit_code"] = proc.returncode
    payload: dict[str, Any] = {}
    stdout = (proc.stdout or "").strip()
    if stdout:
        try:
            payload = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            meta["error"] = "run_checks stdout was not valid JSON"
            meta["stderr_tail"] = (proc.stderr or "")[-500:]

    meta["fail_count"] = int(payload.get("fail_count") or 0)
    meta["report_path"] = payload.get("report_path")

    report_text = ""
    rp = meta.get("report_path")
    if rp and pathlib.Path(rp).is_file():
        try:
            report_text = pathlib.Path(rp).read_text(encoding="utf-8")
        except OSError:
            pass

    fail_rows = parse_report_fails(report_text)
    if not fail_rows and payload.get("fail_checks"):
        for name in payload["fail_checks"]:
            fail_rows.append({
                "check": _normalize_check_name(str(name)),
                "raw_check": str(name),
                "detail": str(name),
                "source": "json",
            })

    if suite == "tieout" and project == "acfr":
        enriched, enrich_meta = tieout_enrich.enrich_fail_rows(
            fail_rows, working_dir=wd, project=project,
        )
        if enrich_meta:
            meta["tieout_enrich"] = enrich_meta
            if enrich_meta.get("scorecard_generated_at"):
                meta["scorecard_generated_at"] = enrich_meta["scorecard_generated_at"]
            fail_rows = enriched
            meta["fail_count"] = max(meta["fail_count"], len(fail_rows))

    meta["fail_rows"] = fail_rows
    if proc.returncode not in (0, 1) and not meta.get("error"):
        err = (proc.stderr or proc.stdout or "").strip()
        meta["error"] = err[-500:] if err else f"run_checks exit {proc.returncode}"

    meta["elapsed_s"] = round(time.time() - t0, 2)
    return meta

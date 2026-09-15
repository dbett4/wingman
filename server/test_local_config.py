"""Tests for the per-install Wingman service/extension token bootstrap."""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
CONFIGURE_PATH = REPO / "scripts" / "configure_local.py"

spec = importlib.util.spec_from_file_location("configure_local", CONFIGURE_PATH)
assert spec and spec.loader
configure_local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure_local)


def _token_from_env(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("WINGMAN_TOKEN="):
            return line.split("=", 1)[1]
    return ""


def _token_from_extension(path: Path) -> str:
    match = re.search(r"Object\.freeze\((\{.*\})\);", path.read_text(encoding="utf-8"))
    assert match
    return json.loads(match.group(1))["token"]


def test_configure_generates_and_preserves_one_shared_token(tmp_path):
    env_path = tmp_path / ".env"
    extension_path = tmp_path / "extension" / "local-config.js"

    assert configure_local.configure(env_path, extension_path) == "generated"
    first = _token_from_env(env_path)
    assert len(first) >= 32
    assert _token_from_extension(extension_path) == first

    assert configure_local.configure(env_path, extension_path) == "preserved"
    assert _token_from_env(env_path) == first
    assert _token_from_extension(extension_path) == first

    if os.name == "posix":
        assert env_path.stat().st_mode & 0o777 == 0o600
        assert extension_path.stat().st_mode & 0o777 == 0o600


def test_packaged_sources_have_no_nonempty_token_fallback():
    app_source = (REPO / "server" / "app.py").read_text(encoding="utf-8")
    background_source = (REPO / "extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert 'os.environ.get("WINGMAN_TOKEN", "")' in app_source
    assert 'importScripts("local-config.js")' in background_source
    assert "local token missing" in background_source
    assert not re.search(r'const WM_TOKEN\s*=\s*["\'][^"\']+["\']', background_source)


def test_service_credential_file_wins_and_missing_file_does_not_fall_back(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("  fictional-file-token\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WORKIVA_", "WINGMAN_"))}
    env.update(WINGMAN_TOKEN="wrong-environment-token", WINGMAN_TOKEN_FILE=str(token_file))
    command = [sys.executable, "-c", "import app; assert app.WINGMAN_TOKEN == 'fictional-file-token'"]
    result = subprocess.run(command, cwd=REPO / "server", env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    env["WINGMAN_TOKEN_FILE"] = str(tmp_path / "missing")
    result = subprocess.run(command, cwd=REPO / "server", env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "FileNotFoundError" in result.stderr
    assert "wrong-environment-token" not in result.stderr


def test_configure_pairs_systemd_credential_without_rotating_existing_token(tmp_path):
    env = tmp_path / ".env"
    extension = tmp_path / "extension" / "local-config.js"
    credential = tmp_path / "systemd" / "token"
    assert configure_local.configure(env, extension, credential) == "generated"
    original = _token_from_env(env)
    assert credential.read_text().strip() == original == _token_from_extension(extension)
    assert credential.stat().st_mode & 0o777 == 0o600
    assert configure_local.configure(env, extension, credential) == "preserved"
    assert credential.read_text().strip() == original

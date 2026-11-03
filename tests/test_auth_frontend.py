"""Node tests for the auth UI helpers (i18n, E.164, rate-limit copy)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_auth_js_helpers():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = ROOT / "tests" / "js" / "auth.test.js"
    r = subprocess.run([node, str(script)], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert "ok" in r.stdout

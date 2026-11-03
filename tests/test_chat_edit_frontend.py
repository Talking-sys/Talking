"""Node tests for the edit-mode state machine (ArrowUp, Esc, i18n)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_chat_edit_js_state_machine():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = ROOT / "tests" / "js" / "chat-edit.test.js"
    r = subprocess.run([node, str(script)], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert "ok" in r.stdout

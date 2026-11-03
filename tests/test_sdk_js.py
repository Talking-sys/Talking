"""Run the JS SDK unit tests when Node is available."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_js_sdk_unit_tests():
    r = subprocess.run(
        ["node", "--test", "sdk/js/tests/client.test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout + "\n" + r.stderr

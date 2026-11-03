import json
from pathlib import Path

import pytest

from talking.chat.gestures import GestureConfig, RecordGestureMachine

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gesture_scripts.json"
SCRIPTS = json.loads(FIXTURES.read_text(encoding="utf-8"))


def apply(machine: RecordGestureMachine, step: dict) -> dict:
    t = step.get("t", 0)
    x = step.get("x", 0)
    y = step.get("y", 0)
    op = step["op"]
    if op == "down":
        return machine.pointer_down(x, y, t)
    if op == "move":
        return machine.pointer_move(x, y, t)
    if op == "up":
        return machine.pointer_up(x, y, t)
    if op == "tick":
        return machine.tick(t)
    if op == "lock_send":
        return machine.lock_send()
    if op == "lock_stop":
        return machine.lock_stop()
    if op == "cancel":
        return machine.cancel()
    raise ValueError(f"unknown op {op}")


@pytest.mark.parametrize("script", SCRIPTS, ids=[s["name"] for s in SCRIPTS])
def test_gesture_script(script):
    machine = RecordGestureMachine(GestureConfig())
    snap = None
    for step in script["steps"]:
        snap = apply(machine, step)
    for key, expected in script["expect"].items():
        assert snap[key] == expected, snap


def test_js_gesture_scripts_match_python():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = Path(__file__).resolve().parent / "js" / "record-gestures.test.js"
    r = subprocess.run([node, str(js)], capture_output=True, text=True, timeout=15)  # nosec B603
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok" in r.stdout

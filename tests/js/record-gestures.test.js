"use strict";
/* Node runner for the same fixtures as tests/test_record_gestures.py */
const fs = require("fs");
const path = require("path");
const { RecordGestureMachine, GestureConfig } = require("../../talking/static/record-gestures.js");

const scripts = JSON.parse(
  fs.readFileSync(path.join(__dirname, "../fixtures/gesture_scripts.json"), "utf8")
);

function apply(machine, step) {
  const t = step.t != null ? step.t : 0;
  const x = step.x != null ? step.x : 0;
  const y = step.y != null ? step.y : 0;
  switch (step.op) {
    case "down": return machine.pointerDown(x, y, t);
    case "move": return machine.pointerMove(x, y, t);
    case "up": return machine.pointerUp(x, y, t);
    case "tick": return machine.tick(t);
    case "lock_send": return machine.lockSend();
    case "lock_stop": return machine.lockStop();
    case "cancel": return machine.cancel();
    default: throw new Error("unknown op " + step.op);
  }
}

let failed = 0;
for (const sc of scripts) {
  const machine = new RecordGestureMachine(new GestureConfig());
  let snap = null;
  for (const step of sc.steps) snap = apply(machine, step);
  for (const [k, v] of Object.entries(sc.expect)) {
    const got = snap[k];
    const ok = Object.is(got, v) || (got == null && v == null);
    if (!ok) {
      console.error("FAIL", sc.name, "expected", k, "=", v, "got", got, snap);
      failed += 1;
    }
  }
}
if (failed) {
  console.error(failed + " assertion(s) failed");
  process.exit(1);
}
console.log("ok", scripts.length, "gesture scripts");

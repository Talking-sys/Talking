/* Telegram-style record-button FSM.
 * Keep in sync with talking/chat/gestures.py
 * Fixtures: tests/fixtures/gesture_scripts.json
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.TalkingGestures = factory();
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var MODE_VOICE = "voice";
  var MODE_VIDEO = "video";
  var PHASE_IDLE = "idle";
  var PHASE_ARMED = "armed";
  var PHASE_RECORDING = "recording";
  var PHASE_LOCKED = "locked";
  var PHASE_PREVIEW = "preview";

  function GestureConfig(opt) {
    opt = opt || {};
    this.tap_ms = opt.tap_ms != null ? opt.tap_ms : 220;
    this.cancel_dx = opt.cancel_dx != null ? opt.cancel_dx : -72;
    this.lock_dy = opt.lock_dy != null ? opt.lock_dy : -72;
    this.max_ms = opt.max_ms != null ? opt.max_ms : 60000;
    this.slop_px = opt.slop_px != null ? opt.slop_px : 16;
  }

  function RecordGestureMachine(cfg) {
    this.cfg = cfg || new GestureConfig();
    this.mode = MODE_VOICE;
    this.phase = PHASE_IDLE;
    this.hint = null;
    this.elapsed_ms = 0;
    this._sx = 0;
    this._sy = 0;
    this._st = 0;
    this._x = 0;
    this._y = 0;
  }

  RecordGestureMachine.prototype.snapshot = function (action) {
    return {
      mode: this.mode,
      phase: this.phase,
      elapsed_ms: this.elapsed_ms | 0,
      hint: this.hint,
      action: action == null ? null : action,
    };
  };

  RecordGestureMachine.prototype.pointerDown = function (x, y, nowMs) {
    if (this.phase === PHASE_LOCKED || this.phase === PHASE_PREVIEW) return this.snapshot();
    if (this.phase !== PHASE_IDLE) return this.snapshot();
    this.phase = PHASE_ARMED;
    this.hint = null;
    this.elapsed_ms = 0;
    this._sx = this._x = x;
    this._sy = this._y = y;
    this._st = nowMs;
    return this.snapshot("arm");
  };

  RecordGestureMachine.prototype.pointerMove = function (x, y, nowMs) {
    if (this.phase !== PHASE_ARMED && this.phase !== PHASE_RECORDING) return this.snapshot();
    this._x = x;
    this._y = y;
    var action = this._advance(nowMs);
    if (this.phase === PHASE_RECORDING) {
      if (this._applyHints(x - this._sx, y - this._sy)) return this.snapshot("lock");
    }
    return this.snapshot(action);
  };

  RecordGestureMachine.prototype.pointerUp = function (x, y, nowMs) {
    if (this.phase === PHASE_LOCKED || this.phase === PHASE_PREVIEW) return this.snapshot();
    if (this.phase === PHASE_ARMED) {
      this._x = x;
      this._y = y;
      this._advance(nowMs);
      var dx = x - this._sx, dy = y - this._sy;
      var dist = Math.sqrt(dx * dx + dy * dy);
      if (this.phase === PHASE_ARMED && this.elapsed_ms < this.cfg.tap_ms && dist <= this.cfg.slop_px) {
        this.mode = this.mode === MODE_VOICE ? MODE_VIDEO : MODE_VOICE;
        return this._idle("toggle");
      }
      if (this.phase === PHASE_ARMED) this.phase = PHASE_RECORDING;
    }
    if (this.phase === PHASE_RECORDING) {
      var locked = this._applyHints(x - this._sx, y - this._sy);
      if (this.hint === "cancel") return this._idle("cancel");
      if (locked || this.phase === PHASE_LOCKED) return this.snapshot("lock");
      return this._idle("send");
    }
    return this.snapshot();
  };

  RecordGestureMachine.prototype.tick = function (nowMs) {
    if (this.phase !== PHASE_ARMED && this.phase !== PHASE_RECORDING && this.phase !== PHASE_LOCKED) {
      return this.snapshot();
    }
    var action = this._advance(nowMs);
    if ((this.phase === PHASE_RECORDING || this.phase === PHASE_LOCKED) && this.elapsed_ms >= this.cfg.max_ms) {
      return this._idle("send");
    }
    return this.snapshot(action);
  };

  RecordGestureMachine.prototype.lockSend = function () {
    if (this.phase === PHASE_LOCKED || this.phase === PHASE_PREVIEW) return this._idle("send");
    return this.snapshot();
  };

  RecordGestureMachine.prototype.lockStop = function () {
    if (this.phase === PHASE_LOCKED) {
      this.phase = PHASE_PREVIEW;
      this.hint = null;
      return this.snapshot("preview");
    }
    return this.snapshot();
  };

  RecordGestureMachine.prototype.previewSend = function () {
    return this.lockSend();
  };

  RecordGestureMachine.prototype.previewCancel = function () {
    if (this.phase === PHASE_PREVIEW) return this._idle("cancel");
    return this.snapshot();
  };

  RecordGestureMachine.prototype.cancel = function () {
    if (this.phase === PHASE_ARMED || this.phase === PHASE_RECORDING ||
        this.phase === PHASE_LOCKED || this.phase === PHASE_PREVIEW) {
      return this._idle("cancel");
    }
    return this.snapshot();
  };

  RecordGestureMachine.prototype._advance = function (nowMs) {
    this.elapsed_ms = Math.max(0, (nowMs - this._st) | 0);
    if (this.phase === PHASE_ARMED && this.elapsed_ms >= this.cfg.tap_ms) {
      this.phase = PHASE_RECORDING;
      return "start";
    }
    return null;
  };

  RecordGestureMachine.prototype._applyHints = function (dx, dy) {
    var ax = Math.abs(dx), ay = Math.abs(dy);
    this.hint = null;
    if (dx <= this.cfg.cancel_dx && ax >= ay) {
      this.hint = "cancel";
      return false;
    }
    if (dy <= this.cfg.lock_dy && ay > ax) {
      this.phase = PHASE_LOCKED;
      this.hint = null;
      return true;
    }
    return false;
  };

  RecordGestureMachine.prototype._idle = function (action) {
    this.phase = PHASE_IDLE;
    this.hint = null;
    this.elapsed_ms = 0;
    return this.snapshot(action);
  };

  return {
    GestureConfig: GestureConfig,
    RecordGestureMachine: RecordGestureMachine,
    MODE_VOICE: MODE_VOICE,
    MODE_VIDEO: MODE_VIDEO,
    PHASE_IDLE: PHASE_IDLE,
    PHASE_ARMED: PHASE_ARMED,
    PHASE_RECORDING: PHASE_RECORDING,
    PHASE_LOCKED: PHASE_LOCKED,
    PHASE_PREVIEW: PHASE_PREVIEW,
  };
});

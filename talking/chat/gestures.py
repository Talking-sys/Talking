"""Telegram-style record-button gesture state machine.

Keep in sync with ``talking/static/record-gestures.js``. Both are executed
against ``tests/fixtures/gesture_scripts.json``.

Interpretation (Telegram):
- Short tap toggles voice <-> round video note.
- Press-and-hold (past TAP_MS) starts recording.
- Release sends.
- Slide left (cancel zone) and release cancels.
- Slide up past the lock threshold locks hands-free; release does not send.
  Locked UI has Stop (preview) and Send.
- 60 s cap auto-sends.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODE_VOICE = "voice"
MODE_VIDEO = "video"

PHASE_IDLE = "idle"
PHASE_ARMED = "armed"
PHASE_RECORDING = "recording"
PHASE_LOCKED = "locked"
PHASE_PREVIEW = "preview"


@dataclass(frozen=True)
class GestureConfig:
    tap_ms: int = 220
    cancel_dx: int = -72
    lock_dy: int = -72
    max_ms: int = 60_000
    slop_px: int = 16
    # Chat notes tap-toggles voice↔video. Voice translation keeps a single
    # hold-to-talk mode and reports action "tap" instead.
    enable_toggle: bool = True


class RecordGestureMachine:
    def __init__(self, cfg: GestureConfig | None = None):
        self.cfg = cfg or GestureConfig()
        self.mode = MODE_VOICE
        self.phase = PHASE_IDLE
        self.hint: str | None = None
        self.elapsed_ms = 0
        self._sx = 0.0
        self._sy = 0.0
        self._st = 0.0
        self._x = 0.0
        self._y = 0.0

    def snapshot(self, action: str | None = None) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "phase": self.phase,
            "elapsed_ms": int(self.elapsed_ms),
            "hint": self.hint,
            "action": action,
        }

    def pointer_down(self, x: float, y: float, now_ms: float) -> dict[str, Any]:
        if self.phase in (PHASE_LOCKED, PHASE_PREVIEW):
            return self.snapshot()
        if self.phase != PHASE_IDLE:
            return self.snapshot()
        self.phase = PHASE_ARMED
        self.hint = None
        self.elapsed_ms = 0
        self._sx = self._x = x
        self._sy = self._y = y
        self._st = now_ms
        return self.snapshot("arm")

    def pointer_move(self, x: float, y: float, now_ms: float) -> dict[str, Any]:
        if self.phase not in (PHASE_ARMED, PHASE_RECORDING):
            return self.snapshot()
        self._x, self._y = x, y
        action = self._advance(now_ms)
        if self.phase == PHASE_RECORDING:
            locked = self._apply_hints(x - self._sx, y - self._sy)
            if locked:
                return self.snapshot("lock")
        return self.snapshot(action)

    def pointer_up(self, x: float, y: float, now_ms: float) -> dict[str, Any]:
        if self.phase == PHASE_LOCKED:
            return self.snapshot()
        if self.phase == PHASE_PREVIEW:
            return self.snapshot()
        if self.phase == PHASE_ARMED:
            self._x, self._y = x, y
            self._advance(now_ms)
            dx, dy = x - self._sx, y - self._sy
            dist = (dx * dx + dy * dy) ** 0.5
            if self.phase == PHASE_ARMED and self.elapsed_ms < self.cfg.tap_ms and dist <= self.cfg.slop_px:
                if not self.cfg.enable_toggle:
                    return self._idle("tap")
                self.mode = MODE_VIDEO if self.mode == MODE_VOICE else MODE_VOICE
                return self._idle("toggle")
            if self.phase == PHASE_ARMED:
                # Held past tap without a tick: treat as a completed hold.
                self.phase = PHASE_RECORDING
        if self.phase == PHASE_RECORDING:
            locked = self._apply_hints(x - self._sx, y - self._sy)
            if self.hint == "cancel":
                return self._idle("cancel")
            if locked or self.phase == PHASE_LOCKED:
                return self.snapshot("lock")
            return self._idle("send")
        return self.snapshot()

    def tick(self, now_ms: float) -> dict[str, Any]:
        if self.phase not in (PHASE_ARMED, PHASE_RECORDING, PHASE_LOCKED):
            return self.snapshot()
        action = self._advance(now_ms)
        if self.phase in (PHASE_RECORDING, PHASE_LOCKED) and self.elapsed_ms >= self.cfg.max_ms:
            return self._idle("send")
        return self.snapshot(action)

    def lock_send(self) -> dict[str, Any]:
        if self.phase in (PHASE_LOCKED, PHASE_PREVIEW):
            return self._idle("send")
        return self.snapshot()

    def lock_stop(self) -> dict[str, Any]:
        if self.phase == PHASE_LOCKED:
            self.phase = PHASE_PREVIEW
            self.hint = None
            return self.snapshot("preview")
        return self.snapshot()

    def preview_send(self) -> dict[str, Any]:
        return self.lock_send()

    def preview_cancel(self) -> dict[str, Any]:
        if self.phase == PHASE_PREVIEW:
            return self._idle("cancel")
        return self.snapshot()

    def cancel(self) -> dict[str, Any]:
        if self.phase in (PHASE_ARMED, PHASE_RECORDING, PHASE_LOCKED, PHASE_PREVIEW):
            return self._idle("cancel")
        return self.snapshot()

    def _advance(self, now_ms: float) -> str | None:
        self.elapsed_ms = max(0, int(now_ms - self._st))
        if self.phase == PHASE_ARMED and self.elapsed_ms >= self.cfg.tap_ms:
            self.phase = PHASE_RECORDING
            return "start"
        return None

    def _apply_hints(self, dx: float, dy: float) -> bool:
        """Return True if we transitioned into locked (hands-free)."""
        ax, ay = abs(dx), abs(dy)
        self.hint = None
        if dx <= self.cfg.cancel_dx and ax >= ay:
            self.hint = "cancel"
            return False
        if dy <= self.cfg.lock_dy and ay > ax:
            self.phase = PHASE_LOCKED
            self.hint = None
            return True
        return False

    def _idle(self, action: str) -> dict[str, Any]:
        self.phase = PHASE_IDLE
        self.hint = None
        self.elapsed_ms = 0
        return self.snapshot(action)

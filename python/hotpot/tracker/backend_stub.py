"""tracker/backend_stub.py — a hand detector with no model and no camera.

Doc section 19.4's `backend_stub.py`, and the thing that makes doc section
11.3's role assignment testable at all. Two modes, both deliberate:

- Scripted (`Stub(script=[...])`): a list of per-frame detection lists,
  played back one entry per `detect()` call, last entry repeating forever.
  This is how a test says "a left hand appears, then a right hand appears
  beside it, then the right one leaves" without a camera, a model, or a
  human. Every acceptance-test scenario in doc section 21's M5 list is a
  short script.
- Empty (`Stub()`): no hands, ever. The honest default for a rig with
  no model file — `tracker/main.py` falls back to this rather than
  refusing to start, because doc section 3.3 requires every process to
  come up and hold its link open regardless of what else is missing, and a
  tracker that exits on a missing model takes its pip red for a reason
  nobody can see from the table.

Deterministic on purpose. Nothing here samples a clock or a random number:
a tracker test that is flaky is a tracker test that gets deleted.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Sequence

from hotpot.tracker.backend import Backend, Detection


class Stub(Backend):

    name = "stub"

    def __init__(self, script: Optional[Sequence[Iterable[Detection]]] = None
                 ) -> None:
        self.script: List[List[Detection]] = [list(step) for step in
                                              (script or [])]
        self.calls = 0
        self.last_timestamp_ms = -1

    def detect(self, frame_bgr: Any, timestamp_ms: int) -> List[Detection]:
        # The timestamp contract is checked here rather than only in the
        # MediaPipe backend, so a caller that breaks it fails in the tests
        # instead of on the rig. MediaPipe's VIDEO mode raises on a
        # non-increasing timestamp; a stub that silently tolerated one
        # would let that bug ship.
        if timestamp_ms <= self.last_timestamp_ms:
            raise ValueError(
                f"timestamp went backwards: {timestamp_ms} after "
                f"{self.last_timestamp_ms}")
        self.last_timestamp_ms = timestamp_ms
        idx = self.calls
        self.calls += 1
        if not self.script:
            return []
        if idx >= len(self.script):
            return list(self.script[-1])
        return list(self.script[idx])

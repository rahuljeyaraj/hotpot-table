#!/usr/bin/env python3
"""tools/send_synthetic_hand.py — dev-only smoke test for FluidLayer/CursorLink,
with no camera, no tracker, and no table calibration involved.

Sends a fake single-hand cursorbus frame (python/hotpot/common/cursorbus.py,
the same protocol CursorLink.h parses) at ~30Hz, sweeping a circle around the
1920x1080 stage. Point is only to answer "does anything draw on the table
when a hand position exists" without needing a solved camera->stage
homography (doc section 12.6's calibration, not done on this dev machine —
see run.py's own "no camera->stage homography from core yet" warning).

Run from the repo root, with `of` (run.py's oF process) already up:

    python tools/send_synthetic_hand.py

Ctrl-C to stop. Sends to both oF and core (cursorbus.Sender's normal
targets) since there is no reason to special-case one out; core simply has
nothing that reacts to cursor frames yet.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = REPO_ROOT / "python"
sys.path.insert(0, str(PYTHON_DIR))

from hotpot.common import cursorbus  # noqa: E402

STAGE_W = 1920
STAGE_H = 1080
HZ = 30.0
PERIOD_S = 6.0   # one full lap of the circle
RADIUS_PX = 350.0


def main() -> None:
    sender = cursorbus.Sender()
    print(f"sending synthetic pointer hand to {sender.targets} at {HZ}Hz "
          f"(Ctrl-C to stop)")
    t0 = time.monotonic()
    try:
        while True:
            t = time.monotonic() - t0
            angle = 2.0 * math.pi * (t / PERIOD_S)
            x = STAGE_W / 2.0 + RADIUS_PX * math.cos(angle)
            y = STAGE_H / 2.0 + RADIUS_PX * math.sin(angle)
            hand = cursorbus.Hand(id=1, role=cursorbus.ROLE_POINTER,
                                   x=x, y=y, conf=1.0)
            sender.send([hand], ts=time.time())
            time.sleep(1.0 / HZ)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"sent {sender.sent}, failed {sender.failed}")
        sender.close()


if __name__ == "__main__":
    main()

"""Tracker process — M0 stub (doc section 21, build item 6).

Not the real tracker process yet. Doc section 11 gives `tracker` MediaPipe
Hands and hand-role assignment, and none of that exists until M5 — until
then this is exactly what common/stub.py does: connect to core, heartbeat,
print HOTPOT-READY, nothing else. M0's build list is explicit: do NOT
touch MediaPipe here.
"""

from hotpot.common import stub

if __name__ == "__main__":
    stub.main("tracker")

"""Classifier process — M0 stub (doc section 21, build item 6).

Not the real classifier process yet. Doc section 3.2 gives `classifier`
food classification and projected-dot detection, and none of that exists
until M7 — until then this is exactly what common/stub.py does: connect
to core, heartbeat, print HOTPOT-READY, nothing else.
"""

from hotpot.common import stub

if __name__ == "__main__":
    stub.main("classifier")

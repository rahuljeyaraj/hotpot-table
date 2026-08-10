"""Voice process — M0 stub (doc section 21, build item 6).

Not the real voice process yet. Doc section 16 gives `voice` the
microphone and keyword spotting, and none of that exists until M9 — until
then this is exactly what common/stub.py does: connect to core, heartbeat,
print HOTPOT-READY, nothing else.
"""

from hotpot.common import stub

if __name__ == "__main__":
    stub.main("voice")

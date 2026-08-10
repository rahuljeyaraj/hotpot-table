"""Camera process — M0 stub (doc section 21, build item 6).

Not the real camera process yet. Doc section 3 gives `camera` the frame
ring and the MJPEG server, and neither exists until M3 — until then this
is exactly what common/stub.py does: connect to core, heartbeat, print
HOTPOT-READY, nothing else. M0's build list is explicit: do NOT open the
camera here.
"""

from hotpot.common import stub

if __name__ == "__main__":
    stub.main("camera")

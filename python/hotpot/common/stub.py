"""What every M0 stub process does: connect to core, heartbeat, announce
readiness, do nothing else (doc section 21, M0 build item 6).

Four processes are exactly this file for exactly one milestone. `camera`,
`tracker`, `classifier` and `voice` all get their real bodies later (M3,
M5, M7, M9) and stop calling `main()` when they do — writing the connect
/heartbeat/ready sequence once here, rather than four times nearly
verbatim, is only a convenience for as long as the four stay identical.

Readiness is not connection
----------------------------
`log.ready()` fires as soon as the client and heartbeat are started, not
once core has actually said welcome. This has to be true for `camera`
specifically: doc section 10.3 makes camera tier 1 and core tier 2, so a
camera whose readiness waited on a core connection would deadlock the
start order against the very tier ordering that names it tier 1. Doc
section 3.3 already treats a client's job as keeping a link open, not
having succeeded, and at this milestone "genuinely serving" (doc section
10.2) means exactly that: the process is up and holding a reconnecting
link open, which is its one job here.

Host and port are hardcoded to the doc section 4.1 defaults rather than
read from `config/system.json`, because nothing in this repo loads that
file yet (open debt: config loading is not built until it has a reader
that needs more than one key).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TextIO

from hotpot.common import health, log, wire

CORE_HOST = "127.0.0.1"
CORE_PORT = 8765          # doc section 4.1: core.control_port default


@dataclass
class Stub:
    """The two running pieces, so a caller (tests, `main`) can stop them."""

    client: wire.Client
    heartbeat: health.Heartbeat

    def stop(self) -> None:
        self.heartbeat.stop()
        self.client.stop()


def start(who: str, host: str = CORE_HOST, port: int = CORE_PORT, *,
          log_stream: TextIO = None, ready_stream: TextIO = None) -> Stub:
    """Wire up and start the client and heartbeat. Returns immediately."""
    log.setup(who, stream=log_stream)
    client = wire.Client(host, port, who)
    heartbeat = health.Heartbeat(client.send, who=who)
    client.start()
    heartbeat.start()
    log.ready(who, ready_stream)
    return Stub(client=client, heartbeat=heartbeat)


def main(who: str, host: str = CORE_HOST, port: int = CORE_PORT) -> None:
    """Block until killed. What `python -m hotpot.<name>.main` runs."""
    stub = start(who, host, port)
    try:
        threading.Event().wait()
    finally:
        stub.stop()

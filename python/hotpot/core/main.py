"""Core process — M0 scope (doc section 21, build item 7).

What exists here: the one control server every other process dials into,
the client registry that turns hellos and heartbeats into the six status
pips, and a minimal staff view that pushes those pips to the browser over
a WebSocket. Everything else in doc section 9 — the FSM, cart, pricing,
scale — arrives with the milestone that needs it. M0 only has to prove
six processes can find each other and that a human can watch it happen.

**Do NOT** (M0 build list, doc section 21): open the camera, open the
serial port, touch MediaPipe, or write any oF code. This file does none
of those — it does not even know those things exist yet.

Host and port are hardcoded to the doc section 4.1 defaults, same as
common/stub.py and for the same reason: config loading is not built until
it has a reader that needs more than one key.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict

from hotpot.common import health, log, wire
from hotpot.core.web import server as web

_log = logging.getLogger("hotpot.core")

CONTROL_HOST = "127.0.0.1"     # doc 4.1: only sibling processes on this
                                # same machine ever dial the control port.
CONTROL_PORT = 8765            # doc 4.1: core.control_port default

# 0.0.0.0, not 127.0.0.1: the staff view is read from a tablet (doc
# section 12.1, "assume a tablet"), which reaches this over the LAN, not
# loopback. Binding to loopback would work on the dev machine and fail
# silently on the rig.
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080                # doc 4.1: core.web_port default

STATIC_ROOT = Path(__file__).resolve().parent / "web" / "static"


class Core:
    """Everything M0 wires up, held together so tests and main() can start
    and stop it as one unit rather than three separately-ordered pieces.
    """

    def __init__(
        self,
        control_host: str = CONTROL_HOST,
        control_port: int = CONTROL_PORT,
        web_host: str = WEB_HOST,
        web_port: int = WEB_PORT,
        static_root: Path = STATIC_ROOT,
    ) -> None:
        self.registry = health.Registry(on_change=self._on_pip_change)
        self.control = wire.Server(
            control_host, control_port,
            on_message=self._on_message,
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            name="core",
        )
        self.web = web.Server(web_host, web_port, static_root, on_join=self._pips_msg)
        # Core beats its own pip from its own loop rather than being
        # hardcoded green (common/health.py's rationale): a wedged main
        # loop with a live web thread is a real failure and this is what
        # makes it show up red instead of hiding behind the other five.
        self._self_beat = health.Heartbeat(self._beat_self, who="core")

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        self.registry.start()
        self.control.start()
        self.web.start()
        self._self_beat.start()

    def stop(self) -> None:
        self._self_beat.stop()
        self.web.stop()
        self.control.stop()
        self.registry.stop()

    @property
    def control_port(self) -> int:
        return self.control.port

    @property
    def web_port(self) -> int:
        return self.web.port

    # -- wire.Server callbacks -------------------------------------------

    def _on_connect(self, conn: wire.Connection) -> None:
        hello = conn.hello or {}
        pid = hello.get("pid")
        ver = hello.get("ver")
        self.registry.connected(
            conn.who,
            pid=pid if isinstance(pid, int) else None,
            ver=ver if isinstance(ver, int) else None,
        )

    def _on_message(self, conn: wire.Connection, msg: Dict[str, Any]) -> None:
        if self.registry.handle(conn.who, msg):
            return
        # M0 speaks nothing else on the control link yet. An unrecognised
        # `t` from a known process is worth a log line, not a dropped
        # link — wire.py's job is framing, not protocol enforcement.
        _log.debug("core: %s sent unhandled message type %r", conn.who, msg.get("t"))

    def _on_disconnect(self, conn: wire.Connection, reason: str) -> None:
        self.registry.disconnected(conn.who, reason)

    # -- self heartbeat ----------------------------------------------------

    def _beat_self(self, msg: Dict[str, Any]) -> bool:
        self.registry.beat("core", msg.get("ts"))
        return True

    # -- pushing pips to the staff view -------------------------------------

    def _pips_msg(self) -> Dict[str, Any]:
        return {"t": "pips", "pips": self.registry.snapshot()}

    def _on_pip_change(self, who: str, old: str, new: str) -> None:
        self.web.broadcast(self._pips_msg())


def start(**kwargs: Any) -> Core:
    """Wire everything up and start it. Returns immediately once every
    listener is bound — the caller decides what "ready" means from there.
    """
    core = Core(**kwargs)
    core.start()
    return core


def main() -> None:
    """Block until killed. What `python -m hotpot.core.main` runs."""
    log.setup("core")
    core = start()
    # After both ports are bound (doc section 10.2: "say it after the
    # port is bound"), not before — run.py's tier 3 is waiting on this
    # exact line to know core is genuinely serving.
    log.ready("core")
    try:
        threading.Event().wait()
    finally:
        core.stop()


if __name__ == "__main__":
    main()

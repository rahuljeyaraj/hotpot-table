"""The control link: newline-delimited JSON over TCP (doc section 4).

Three things live here and nothing else:

    encode / LineReader   framing
    Server                core's side. One listener, many clients.
    Client                everybody else's side. Reconnects forever.

Topology, restated from doc section 3.3 because every design choice below
follows from it: **core is the TCP server for every control link, everyone
else is a client and reconnects with backoff.** A client must never exit
because core is not there yet. That single rule is what makes process start
order an optimisation for tidy logs rather than a correctness requirement.

Heartbeats are deliberately NOT in this file. This module moves lines; who
is alive is health.py's job.

Threads
-------
Every link runs two threads: one reading, one writing. Neither ever calls
into application code while holding a lock. Callbacks fire on the reading
thread, so a handler that blocks stalls only its own link — but core's
handlers must still not block, because core must not block (I1/I3).

The drop rule
-------------
The doc says control messages must not vanish; a dropped "enter staff mode"
wedges the system. But a socket whose peer has stopped reading cannot be
written to without blocking, and core may not block. So there is exactly one
rule, applied on both sides:

    A line is either written to a live socket, or it is counted as dropped
    and send() returns False. It is never silently buffered forever.

Concretely: sending while the link is down drops immediately (there is
nowhere to put it). Sending while the link is up but the outbound queue is
full means the peer is not draining, so the link is closed — the peer will
reconnect, and recovery on reconnect is already specified: core resends full
state to `of` and re-issues a pending classifier scan (doc section 20.1).
A reset link is loud and recoverable. A silently swallowed command is not.

There are two ways to notice a peer that has stopped draining, and they
catch different failures: the queue filling (traffic is flowing in, nothing
is going out) and SEND_TIMEOUT (the socket itself is stuck). Both exist on
purpose, both are explicit, and DEFAULT_SEND_QUEUE is sized so that at the
60Hz state rate the queue is the one that trips. That sizing is load-bearing
— see the comment on it.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional

PROTOCOL_VERSION = 3

# A control line is small: the 60Hz state message with 8 bins and a few
# widgets is well under 4 KB. A megabyte means the peer is confused or
# hostile, and there is no honest way to resynchronise a stream whose framing
# we have lost, so the link is reset instead.
MAX_LINE_BYTES = 1 << 20

# How much unread outbound is allowed to pile up before the peer is declared
# wedged. Read this as a duration, not a count: at the 60Hz state rate it is
# 4.3 seconds of a peer not reading. It has to stay small enough to trip
# before SEND_TIMEOUT does, or the queue rule becomes unreachable code and
# the real behaviour ends up being an accident of kernel buffer sizes.
DEFAULT_SEND_QUEUE = 256

# Backstop for the other shape of the same failure: nothing queued behind us,
# but the socket itself will not drain. Deliberately long. A peer is allowed
# to stall — oF holds one core on a 4-core board and will occasionally miss a
# beat — and killing the table's link over a scheduler hiccup would be worse
# than the stall. Ten seconds is not a hiccup.
SEND_TIMEOUT = 10.0

# The reconnect ladder from doc section 20.2.
BACKOFF_START = 1.0
BACKOFF_MAX = 10.0

# A client that connects and is never welcomed is talking to something that
# is not core. Give up and retry rather than sit there looking connected.
WELCOME_TIMEOUT = 5.0

# Poll granularity for the read and write loops. Bounds how long stop()
# takes; small enough to feel instant, large enough to cost nothing.
TICK = 0.25

log = logging.getLogger("hotpot.wire")


# ---------------------------------------------------------------------------
# Framing
# ---------------------------------------------------------------------------

def encode(obj: Any) -> bytes:
    """One object to one UTF-8 line, newline terminated.

    ensure_ascii is off on purpose: bin labels are Chinese, and \\u codes
    would triple their size and make a tcpdump unreadable during bring-up.
    json.dumps escapes any newline inside a string, so the framing holds.
    """
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"


def decode(line: bytes) -> Optional[Dict[str, Any]]:
    """One line to one object, or None if the line is not a usable message.

    Returning None rather than raising is the point: a garbled line is
    skipped and counted, never allowed to kill a link that is otherwise
    healthy. A line that parses to a bare list or number is also None —
    every message in doc section 4 is an object with a `t` field.
    """
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


class LineReader:
    """Incremental newline splitter over arbitrary chunk boundaries.

    TCP gives no message boundaries: one recv() may hold three messages,
    half a message, or the tail of one and the head of the next. Feeding
    every chunk through here is the only place that is handled.
    """

    def __init__(self, max_line: int = MAX_LINE_BYTES) -> None:
        self._buf = bytearray()
        self._max_line = max_line
        self.overflowed = False

    def feed(self, chunk: bytes) -> List[bytes]:
        """Return whatever complete lines this chunk finished.

        Sets .overflowed if a single line exceeded the cap; the caller is
        expected to drop the link, because the stream can no longer be
        trusted to be framed.
        """
        if self.overflowed:
            return []
        self._buf.extend(chunk)
        lines: List[bytes] = []
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            if line.strip():          # blank keepalive lines are not messages
                lines.append(line)
        if len(self._buf) > self._max_line:
            self.overflowed = True
            self._buf.clear()
        return lines


# ---------------------------------------------------------------------------
# One socket, two threads. Shared by both sides of the link.
# ---------------------------------------------------------------------------

class _Link:
    """Read loop plus write loop over one connected socket.

    Not used directly. Server wraps it as Connection; Client owns one at a
    time and replaces it on every reconnect.
    """

    def __init__(self, sock: socket.socket, label: str, send_queue: int) -> None:
        self._sock = sock
        self._label = label
        self._out: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=send_queue)
        self._reader = LineReader()
        self._closing = threading.Event()
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None
        self.reason = ""

        self.sent = 0
        self.recv = 0
        self.dropped = 0
        self.malformed = 0
        self.last_rx_ts = 0.0

        # 60Hz of small messages is exactly the traffic Nagle was designed
        # to coalesce, and coalescing it would add a tick of latency to the
        # table for no benefit.
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        sock.settimeout(TICK)

    # -- lifecycle ---------------------------------------------------------

    def _start(self, on_line: Callable[[bytes], None], on_closed: Callable[[str], None]) -> None:
        self._on_line = on_line
        self._on_closed = on_closed
        self._rx_thread = threading.Thread(
            target=self._read_loop, name=f"wire-rx-{self._label}", daemon=True)
        self._tx_thread = threading.Thread(
            target=self._write_loop, name=f"wire-tx-{self._label}", daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()

    @property
    def alive(self) -> bool:
        return not self._closing.is_set()

    def close(self, reason: str = "closed") -> None:
        """Idempotent. Safe to call from any thread, including a callback.

        The first reason wins — it is the one that explains the close; every
        later one is a consequence of it.
        """
        if self._closing.is_set():
            return
        self._closing.set()
        self.reason = reason
        try:
            self._out.put_nowait(None)          # wake the writer
        except queue.Full:
            pass
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def join(self, timeout: float = 2.0) -> None:
        for t in (self._rx_thread, self._tx_thread):
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout)

    # -- io ----------------------------------------------------------------

    def send(self, obj: Any) -> bool:
        """Queue one message. Never blocks. False means it was dropped.

        See the drop rule in the module docstring: a full queue closes the
        link rather than discarding the message quietly.
        """
        if self._closing.is_set():
            self.dropped += 1
            return False
        try:
            self._out.put_nowait(encode(obj))
            return True
        except queue.Full:
            self.dropped += 1
            log.error("%s: send queue full (%d lines unread by peer) — resetting link",
                      self._label, self._out.maxsize)
            self.close("send queue overflow")
            return False

    def _read_loop(self) -> None:
        reason = "peer closed"
        try:
            while not self._closing.is_set():
                try:
                    chunk = self._sock.recv(65536)
                except socket.timeout:
                    continue
                except OSError as e:
                    reason = f"recv failed: {e}"
                    break
                if not chunk:
                    break
                self.last_rx_ts = time.time()
                for line in self._reader.feed(chunk):
                    msg = decode(line)
                    if msg is None:
                        self.malformed += 1
                        log.warning("%s: dropped an unparseable line (%d bytes)",
                                    self._label, len(line))
                        continue
                    self.recv += 1
                    try:
                        self._on_line(msg)
                    except Exception:
                        log.exception("%s: handler raised", self._label)
                if self._reader.overflowed:
                    reason = f"line exceeded {MAX_LINE_BYTES} bytes"
                    break
        finally:
            self.close(reason)
            self._on_closed(self.reason)

    def _write_loop(self) -> None:
        while True:
            try:
                item = self._out.get(timeout=TICK)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            if item is None:
                return
            if self._send_all(item):
                self.sent += 1
            else:
                return

    def _send_all(self, data: bytes) -> bool:
        """Write one whole line, or close the link and return False.

        Written by hand rather than with sendall() because the socket carries
        a short timeout for the reader's benefit, and sendall() after a
        timeout is unrecoverable — it does not report how many bytes went
        out, so retrying it would splice a duplicate fragment into the
        stream and destroy the framing. send() does report, so the offset can
        be carried across a timeout and the deadline can be ours rather than
        the socket's.
        """
        view = memoryview(data)
        deadline = time.time() + SEND_TIMEOUT
        while view:
            try:
                n = self._sock.send(view)
            except socket.timeout:
                if self._closing.is_set():
                    return False
                if time.time() > deadline:
                    log.error("%s: socket would not drain in %.0fs — resetting link",
                              self._label, SEND_TIMEOUT)
                    self.dropped += 1
                    self.close("peer stopped draining the socket")
                    return False
                continue
            except OSError as e:
                if not self._closing.is_set():
                    log.info("%s: send failed: %s", self._label, e)
                self.close(f"send failed: {e}")
                return False
            view = view[n:]
        return True


# ---------------------------------------------------------------------------
# Server — core's side
# ---------------------------------------------------------------------------

class Connection:
    """One connected client, from the server's point of view."""

    def __init__(self, link: _Link, peer: str) -> None:
        self._link = link
        self.peer = peer
        self.who: Optional[str] = None      # set by hello
        self.hello: Optional[Dict[str, Any]] = None
        self.connected_ts = time.time()

    def send(self, obj: Any) -> bool:
        return self._link.send(obj)

    def close(self, reason: str = "closed by core") -> None:
        self._link.close(reason)

    @property
    def alive(self) -> bool:
        return self._link.alive

    @property
    def stats(self) -> Dict[str, Any]:
        L = self._link
        return {"who": self.who, "peer": self.peer, "sent": L.sent, "recv": L.recv,
                "dropped": L.dropped, "malformed": L.malformed,
                "last_rx_ts": L.last_rx_ts, "reason": L.reason}

    def __repr__(self) -> str:
        return f"<Connection {self.who or '?'} {self.peer}>"


class Server:
    """The one TCP listener in the system. Core owns it; nobody else has one.

    Handshake handling is here rather than in core so that both halves of it
    live in one file: a client sends hello and waits for welcome, so the
    server must always answer hello. What goes *inside* the welcome is core's
    business, supplied by the welcome_cfg callback.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        on_message: Optional[Callable[[Connection, Dict[str, Any]], None]] = None,
        on_connect: Optional[Callable[[Connection], None]] = None,
        on_disconnect: Optional[Callable[[Connection, str], None]] = None,
        welcome_cfg: Optional[Callable[[Connection, Dict[str, Any]], Dict[str, Any]]] = None,
        name: str = "core",
        send_queue: int = DEFAULT_SEND_QUEUE,
    ) -> None:
        self.host = host
        self.port = port
        self.name = name
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._welcome_cfg = welcome_cfg
        self._send_queue = send_queue

        self._srv: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._conns: List[Connection] = []
        self.accepted = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> int:
        """Bind, listen, and return the bound port (port 0 picks one)."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(16)
        srv.settimeout(TICK)
        self.port = srv.getsockname()[1]
        self._srv = srv
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name=f"wire-accept-{self.name}", daemon=True)
        self._accept_thread.start()
        log.info("%s: control server listening on %s:%d", self.name, self.host, self.port)
        return self.port

    def stop(self) -> None:
        """Close the listener and every live connection. Idempotent."""
        self._stop.set()
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
        for c in self.clients():
            c.close("server stopping")
        if self._accept_thread is not None and self._accept_thread.is_alive():
            self._accept_thread.join(2.0)
        for c in self.clients():
            c._link.join()

    # -- clients -----------------------------------------------------------

    def clients(self) -> List[Connection]:
        with self._lock:
            return list(self._conns)

    def client(self, who: str) -> Optional[Connection]:
        with self._lock:
            for c in self._conns:
                if c.who == who:
                    return c
        return None

    def send_to(self, who: str, obj: Any) -> bool:
        c = self.client(who)
        return bool(c and c.send(obj))

    def broadcast(self, obj: Any, only: Optional[List[str]] = None) -> int:
        """Send to every welcomed client, or to the named ones. Returns the
        number that took it. Encoding happens per link, which is a little
        wasteful at 60Hz; if that ever shows up in a profile, pre-encode.
        """
        n = 0
        for c in self.clients():
            if c.who is None:
                continue                      # not through the handshake yet
            if only is not None and c.who not in only:
                continue
            if c.send(obj):
                n += 1
        return n

    # -- internals ---------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                sock, addr = self._srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peer = f"{addr[0]}:{addr[1]}"
            link = _Link(sock, f"{self.name}<-{peer}", self._send_queue)
            conn = Connection(link, peer)
            with self._lock:
                self._conns.append(conn)
                self.accepted += 1
            log.info("%s: accepted %s", self.name, peer)
            link._start(lambda m, c=conn: self._dispatch(c, m),
                        lambda r, c=conn: self._closed(c, r))

    def _dispatch(self, conn: Connection, msg: Dict[str, Any]) -> None:
        if msg.get("t") == "hello":
            self._handle_hello(conn, msg)
            return
        if conn.who is None:
            # Everything is keyed on who the peer is. A message before hello
            # cannot be attributed, so it cannot be acted on.
            log.warning("%s: %s sent %r before hello — ignored",
                        self.name, conn.peer, msg.get("t"))
            return
        if self._on_message is not None:
            self._on_message(conn, msg)

    def _handle_hello(self, conn: Connection, msg: Dict[str, Any]) -> None:
        who = msg.get("who")
        if not isinstance(who, str) or not who:
            conn.close("hello without a name")
            return
        ver = msg.get("ver")
        if ver != PROTOCOL_VERSION:
            # Loud, but not fatal: during a rolling change one process will
            # briefly be old, and a version mismatch that silently half-works
            # is worse than one that is in the log.
            log.warning("%s: %s speaks ver %r, we speak %d",
                        self.name, who, ver, PROTOCOL_VERSION)

        # A second connection claiming the same name is the normal shape of a
        # crash-restart: the new process is up before the OS has torn down the
        # old socket. The newcomer is the live one.
        stale = self.client(who)
        if stale is not None and stale is not conn:
            log.info("%s: %s reconnected from %s — closing the old link %s",
                     self.name, who, conn.peer, stale.peer)
            stale.close("superseded by a newer connection")

        conn.who = who
        conn.hello = msg
        cfg = {}
        if self._welcome_cfg is not None:
            try:
                cfg = self._welcome_cfg(conn, msg) or {}
            except Exception:
                log.exception("%s: welcome_cfg raised for %s", self.name, who)
        conn.send({"t": "welcome", "who": who, "cfg": cfg})
        if self._on_connect is not None:
            self._on_connect(conn)

    def _closed(self, conn: Connection, reason: str) -> None:
        with self._lock:
            if conn in self._conns:
                self._conns.remove(conn)
        log.info("%s: %s disconnected (%s)", self.name, conn.who or conn.peer, reason)
        if conn.who is not None and self._on_disconnect is not None:
            self._on_disconnect(conn, reason)


# ---------------------------------------------------------------------------
# Client — everybody else's side
# ---------------------------------------------------------------------------

class Client:
    """A control link to core that outlives core.

    start() returns immediately and the link comes up whenever core does.
    The ladder is doc section 20.2 exactly: on failure sleep(backoff),
    backoff doubles to a 10s ceiling; on success reset to 1s, send hello,
    wait for welcome, resume.
    """

    def __init__(
        self,
        host: str,
        port: int,
        who: str,
        *,
        on_message: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_connect: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None,
        hello_extra: Optional[Dict[str, Any]] = None,
        send_queue: int = DEFAULT_SEND_QUEUE,
    ) -> None:
        self.host = host
        self.port = port
        self.who = who
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._hello_extra = dict(hello_extra or {})
        self._send_queue = send_queue

        self._link: Optional[_Link] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._up = threading.Event()          # set once welcome has arrived
        self._welcomed = threading.Event()    # per-attempt handshake latch

        self.cfg: Dict[str, Any] = {}
        self.attempts = 0
        self.connects = 0
        self.dropped_while_down = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"wire-client-{self.who}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop reconnecting and drop the link. Idempotent."""
        self._stop.set()
        link = self._link
        if link is not None:
            link.close("client stopping")
        if self._thread is not None and self._thread.is_alive() \
                and self._thread is not threading.current_thread():
            self._thread.join(3.0)

    @property
    def connected(self) -> bool:
        """True only after welcome. Connected-but-unwelcomed is not usable."""
        return self._up.is_set()

    def wait_connected(self, timeout: float) -> bool:
        return self._up.wait(timeout)

    @property
    def stats(self) -> Dict[str, Any]:
        L = self._link
        return {"who": self.who, "connected": self.connected,
                "attempts": self.attempts, "connects": self.connects,
                "dropped_while_down": self.dropped_while_down,
                "sent": L.sent if L else 0, "recv": L.recv if L else 0,
                "dropped": L.dropped if L else 0,
                "last_rx_ts": L.last_rx_ts if L else 0.0}

    # -- io ----------------------------------------------------------------

    def send(self, obj: Any) -> bool:
        """Never blocks. False means the link was down and this was dropped."""
        link = self._link
        if link is None or not self._up.is_set():
            self.dropped_while_down += 1
            return False
        return link.send(obj)

    # -- internals ---------------------------------------------------------

    def _run(self) -> None:
        backoff = BACKOFF_START
        while not self._stop.is_set():
            if self._connect_once():
                backoff = BACKOFF_START
                # _connect_once returns when the link has gone away. Loop
                # straight back round and try again without a penalty wait:
                # this was a working link, not a failing one.
                continue
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _connect_once(self) -> bool:
        """One attempt. True if we got as far as a welcomed link."""
        self.attempts += 1
        try:
            sock = socket.create_connection((self.host, self.port), timeout=WELCOME_TIMEOUT)
        except OSError as e:
            log.debug("%s: connect to %s:%d failed: %s", self.who, self.host, self.port, e)
            return False

        self._welcomed.clear()
        gone = threading.Event()
        link = _Link(sock, f"{self.who}->core", self._send_queue)
        self._link = link
        link._start(self._on_line, lambda r: gone.set())

        # The hello must go directly rather than through send(), which is
        # gated on the handshake this hello is what completes.
        link.send({"t": "hello", "who": self.who, "pid": os.getpid(),
                   "ver": PROTOCOL_VERSION, **self._hello_extra})

        if not self._welcomed.wait(WELCOME_TIMEOUT):
            log.warning("%s: connected to %s:%d but no welcome in %.0fs — retrying",
                        self.who, self.host, self.port, WELCOME_TIMEOUT)
            link.close("no welcome")
            link.join()
            self._link = None
            return False

        self.connects += 1
        self._up.set()
        log.info("%s: control link up to %s:%d", self.who, self.host, self.port)
        if self._on_connect is not None:
            try:
                self._on_connect(self.cfg)
            except Exception:
                log.exception("%s: on_connect raised", self.who)

        while not gone.wait(TICK):
            if self._stop.is_set():
                link.close("client stopping")
                break
        self._up.clear()
        link.join()
        self._link = None
        if self._on_disconnect is not None and not self._stop.is_set():
            try:
                self._on_disconnect("link lost")
            except Exception:
                log.exception("%s: on_disconnect raised", self.who)
        return True

    def _on_line(self, msg: Dict[str, Any]) -> None:
        if msg.get("t") == "welcome":
            cfg = msg.get("cfg")
            self.cfg = cfg if isinstance(cfg, dict) else {}
            self._welcomed.set()
            return
        if self._on_message is not None:
            self._on_message(msg)

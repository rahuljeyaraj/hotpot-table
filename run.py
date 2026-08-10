#!/usr/bin/env python3
"""THE single entry point (doc section 10). Starts, supervises, and stops
every process in the system with one command.

    python run.py                 start everything, stream merged logs
    python run.py --stop          stop a detached instance
    python run.py --only core,of  start a subset (development)
    python run.py --no-restart    disable auto-restart (debugging a crash)

What this file owns, and nothing else
--------------------------------------
Launching, tiered start order, readiness gating, merged/prefixed logging,
crash restart with backoff, CPU affinity, and clean shutdown. It contains no
application logic (doc section 3's process table): it does not speak the
control protocol, does not know a bin from a price, and does not import
anything from the six processes it starts. It only watches their stdout for
one magic line.

Process groups and why they matter
-----------------------------------
Every child is launched in its own process group — `start_new_session=True`
on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows — so that killing the group
kills grandchildren too. An orphaned process holding a camera or a TCP port
is the single most annoying failure mode in a system like this, and the M0
acceptance test (`ps aux | grep hotpot` returns nothing after Ctrl-C) exists
to prove it does not happen.

Readiness, not a fixed delay
-----------------------------
Each child prints `HOTPOT-READY <name>` (common/log.py `ready()`) once it is
genuinely serving. This launcher waits for that line — not a sleep, not a
port probe — before starting the next tier (doc section 10.3). Because every
client reconnects to core with backoff regardless of start order (doc
section 3.3), a tier that times out is logged loudly and started anyway:
correctness never depends on tier order, only log tidiness does.

Restart and the failure ladder
-------------------------------
A crashed child is restarted with the same ladder every reconnecting client
uses (doc section 20.2): backoff starts at 1s, doubles, caps at 10s, and
resets to 1s once the child proves itself by reaching HOTPOT-READY again —
a process that dies instantly on every attempt must not get the same grace
as one that ran cleanly for an hour and crashed once. After 5 failures
inside a rolling 60s window the child is marked `failed` and this launcher
stops restarting it, loudly, rather than spinning forever on a build that
cannot start (doc section 20.2's last paragraph).
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent
PYTHON_DIR = ROOT / "python"
sys.path.insert(0, str(PYTHON_DIR))

from hotpot.common import atomicio  # noqa: E402
from hotpot.common import log as hlog  # noqa: E402
from hotpot.common import health  # noqa: E402


def _pidfile_path(root: Path) -> Path:
    return root / "state" / "run.pid"


# The reconnect ladder of doc section 20.2, reused verbatim: "the launcher
# restarts a crashed process with the same backoff."
BACKOFF_START = 1.0
BACKOFF_MAX = 10.0

# Doc section 20.2: "after 5 failures in 60s it marks the process failed."
FAILURE_LIMIT = 5
FAILURE_WINDOW = 60.0

# No number is given in the doc for the first-boot wait, only that the
# launcher waits for readiness. 30s is generous enough to cover a cold model
# load on the ODYSSEY (doc section 1.4) without hanging a dev iteration
# forever if a process is simply missing.
STARTUP_TIMEOUT = 30.0

# Doc section 10.2 exactly: "wait 5s, SIGKILL survivors."
GRACE_PERIOD = 5.0

# Doc section 10.4: OMP_NUM_THREADS=1 for the two libraries that default to
# one worker per visible core. TFLite's equivalent is not an env var — it is
# `Interpreter(num_threads=1)` — and has to be set inside tracker/classifier
# main.py itself when that code is written (M5/M7); it cannot be forced from
# here.
_SINGLE_THREAD_ENV = {"OMP_NUM_THREADS": "1", "OPENCV_NUM_THREADS": "1"}

# Doc section 10.3: tier 1 creates the frame ring and serves MJPEG, tier 2
# binds the control port everyone else dials into, tier 3 is every
# reconnecting client. Tiers are an optimisation for clean logs, not a
# correctness requirement — do not read anything else into this ordering.
_TIER = {"camera": 1, "core": 2, "tracker": 3, "classifier": 3, "voice": 3, "of": 3}


def _py_module_cmd(module: str) -> List[str]:
    # -u belt-and-braces: log.setup() already line-buffers stdout (doc
    # section 10.2 requires it), but this covers anything a process prints
    # before it gets that far, e.g. an import-time traceback.
    return [sys.executable, "-u", "-m", module]


def _of_binary() -> Path:
    """Best-effort default for the compiled openFrameworks app.

    Nothing builds this yet (M0 explicitly does not touch oF code) and the
    exact output name depends on a Visual Studio config that does not exist
    on disk yet either. Override with HOTPOT_OF_BIN if this guess is wrong
    once M1 actually builds it — a missing binary here fails loudly through
    the normal restart-and-give-up path (see Launcher._spawn) rather than
    blocking the other five processes (doc section 3.3).
    """
    override = os.environ.get("HOTPOT_OF_BIN")
    if override:
        return Path(override)
    base = ROOT / "of" / "hotpot-table" / "bin"
    return base / ("hotpot-table_debug.exe" if os.name == "nt" else "hotpot-table")


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    tier: int
    cmd: Tuple[str, ...]
    cwd: Path
    extra_env: Dict[str, str] = field(default_factory=dict)


def _build_processes() -> Tuple[ProcessSpec, ...]:
    # health.PROCESSES (doc section 4.2 / 12.2) is the one place the six
    # names are already enumerated in pip-draw order. Building this table by
    # walking it, rather than listing the six names a second time, means the
    # two files cannot quietly drift apart.
    cmds: Dict[str, List[str]] = {
        "camera": _py_module_cmd("hotpot.camera.main"),
        "tracker": _py_module_cmd("hotpot.tracker.main"),
        "classifier": _py_module_cmd("hotpot.classifier.main"),
        "voice": _py_module_cmd("hotpot.voice.main"),
        "core": _py_module_cmd("hotpot.core.main"),
        "of": [str(_of_binary())],
    }
    cwds: Dict[str, Path] = {"of": ROOT / "of" / "hotpot-table" / "bin"}
    envs: Dict[str, Dict[str, str]] = {"tracker": _SINGLE_THREAD_ENV,
                                        "classifier": _SINGLE_THREAD_ENV}
    return tuple(
        ProcessSpec(name=name, tier=_TIER[name], cmd=tuple(cmds[name]),
                    cwd=cwds.get(name, PYTHON_DIR), extra_env=envs.get(name, {}))
        for name in health.PROCESSES
    )


PROCESSES: Tuple[ProcessSpec, ...] = _build_processes()
PROCESS_BY_NAME: Dict[str, ProcessSpec] = {p.name: p for p in PROCESSES}

# Doc section 10.4, finalised against the J4125 (4 cores, no SMT): one
# process (or pair that is never hot simultaneously) per core, nothing
# pinned on core 0 but `of`. Linux only — see _apply_affinity.
AFFINITY_PLAN: Tuple[Tuple[int, Tuple[str, ...]], ...] = (
    (0, ("of",)),
    (1, ("tracker",)),
    (2, ("classifier", "voice")),
    (3, ("camera", "core")),
)


# ---------------------------------------------------------------------------
# Merged, prefixed, rotated logging
# ---------------------------------------------------------------------------

# Doc section 10.2: "prefixes each line with the process name and a colour."
_COLOUR = {
    "camera": "\033[36m", "tracker": "\033[32m", "classifier": "\033[33m",
    "voice": "\033[34m", "core": "\033[35m", "of": "\033[31m",
}
_RESET = "\033[0m"
_LAUNCHER_COLOUR = "\033[90m"


class MergedLog:
    """Every prefixed line, also written to logs/hotpot-<date>.log.

    "Rotation" here is the date in the filename (doc section 10.2): the file
    is reopened when the wall-clock date changes, which is enough for a
    process meant to run for one day at a time and needs no separate
    size-based scheme. A flush after every line matches common/log.py's own
    rule that a `kill -9` must not eat the lines explaining why it was
    worth killing; a full fsync is not needed here because a lost log line
    is an inconvenience, not the silent mis-bill atomicio.py exists to
    prevent.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._lock = threading.Lock()
        self._date = ""
        self._fh = None

    def write(self, line: str) -> None:
        with self._lock:
            today = time.strftime("%Y-%m-%d")
            if today != self._date:
                self._reopen(today)
            self._fh.write(line if line.endswith("\n") else line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None
                self._date = ""

    def _reopen(self, today: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        if self._fh is not None:
            self._fh.close()
        self._fh = open(self._dir / f"hotpot-{today}.log", "a",
                         encoding="utf-8", errors="backslashreplace")
        self._date = today


def _use_colour() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


# ---------------------------------------------------------------------------
# One supervised child
# ---------------------------------------------------------------------------

@dataclass
class ChildRuntime:
    spec: ProcessSpec
    ready: threading.Event = field(default_factory=threading.Event)
    proc: Optional[subprocess.Popen] = None
    pid: Optional[int] = None
    restarts: int = 0
    failed: bool = False
    fail_times: List[float] = field(default_factory=list)
    thread: Optional[threading.Thread] = None


def _terminate(pid: int, *, graceful: bool) -> None:
    """Kill one process group (POSIX) or process tree (Windows).

    Doc section 10.2: SIGTERM the group, and on Windows the closest
    equivalent to a group is the process group id created alongside
    CREATE_NEW_PROCESS_GROUP, targeted with CTRL_BREAK_EVENT. If that is not
    honoured — not a console process, or simply too slow — the caller's
    grace-period-then-hard-kill loop falls back to `taskkill /T /F`, which
    kills the whole tree without any extra dependency.
    """
    if os.name == "posix":
        sig = signal.SIGTERM if graceful else signal.SIGKILL
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            pass
        return

    if graceful:
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
            return
        except OSError:
            pass
        except SystemError:
            # Same failure as the OSError above — GenerateConsoleCtrlEvent
            # rejected the target (e.g. it shares no console with us, the
            # case `--stop` hits from a fresh process) — just surfaced as a
            # bare SystemError instead of a normal OSError, a known
            # CPython/Windows os.kill(..., CTRL_BREAK_EVENT) quirk. Without
            # this it propagates straight out of the caller's loop and
            # every remaining child in that loop never gets terminated at
            # all, graceful or otherwise — worse than the timeout this
            # fallback exists to handle.
            pass
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True)


def _pid_alive(pid: int) -> bool:
    """True if `pid` is a live process.

    POSIX: signal 0 is the documented no-op existence probe. Windows has no
    such thing — `os.kill(pid, 0)` there actually calls TerminateProcess, so
    using it as a liveness check would kill the very process being asked
    about. `tasklist` is the safe, dependency-free way to ask.
    """
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                          capture_output=True, text=True)
    return str(pid) in out.stdout


def _apply_affinity(
    children: Dict[str, ChildRuntime],
    print_fn: Callable[[str, str], None],
    *,
    is_posix: Optional[bool] = None,
    cpu_count: Optional[int] = None,
    setaffinity: Optional[Callable[[int, set], None]] = None,
) -> None:
    """Pin processes to cores per AFFINITY_PLAN, once everyone is up.

    Doc section 10.4: exactly four cores, no spare, so a machine with fewer
    than four skips pinning entirely rather than pinning badly — that branch
    is unreachable on the deploy target but stays for dev machines. The
    launcher itself is deliberately never pinned (same section): a
    supervisor that has claimed a core is competing with the thing it
    supervises.

    `is_posix`/`cpu_count`/`setaffinity` are injectable so this is testable
    on the Windows dev machine, which always takes the no-op path for real.
    """
    is_posix = (os.name == "posix") if is_posix is None else is_posix
    if not is_posix:
        return
    setaffinity = setaffinity or getattr(os, "sched_setaffinity", None)
    if setaffinity is None:
        return
    n = os.cpu_count() if cpu_count is None else cpu_count
    if not n or n < 4:
        print_fn("run.py", f"only {n} cores detected — skipping CPU affinity "
                            "(doc section 10.4 assumes exactly 4)")
        return
    for core, names in AFFINITY_PLAN:
        for name in names:
            child = children.get(name)
            if child is None or child.pid is None:
                continue
            try:
                setaffinity(child.pid, {core})
            except OSError as e:
                print_fn("run.py", f"could not pin {name} to core {core}: {e}")


# ---------------------------------------------------------------------------
# The launcher
# ---------------------------------------------------------------------------

class Launcher:

    def __init__(self, names: Sequence[str], *, no_restart: bool = False,
                 root: Path = ROOT) -> None:
        self.root = root
        self.no_restart = no_restart
        self.children: Dict[str, ChildRuntime] = {
            n: ChildRuntime(PROCESS_BY_NAME[n]) for n in names}
        self.mlog = MergedLog(root / "logs")
        self.pidfile = _pidfile_path(root)
        self._pidfile_lock = threading.Lock()
        self._stop = threading.Event()
        self._colour = _use_colour()

    # -- lifecycle -----------------------------------------------------

    def run(self) -> int:
        self._install_signal_handlers()
        self._write_pidfile()
        try:
            for tier in sorted({c.spec.tier for c in self.children.values()}):
                names = [n for n, c in self.children.items() if c.spec.tier == tier]
                for n in names:
                    self._start_supervisor(n)
                missing = self._wait_ready(names, STARTUP_TIMEOUT)
                if missing and not self._stop.is_set():
                    self._print("run.py", f"tier {tier} not ready after "
                                f"{STARTUP_TIMEOUT:.0f}s: {', '.join(missing)} "
                                "— continuing anyway (doc section 3.3)")
            if not self._stop.is_set():
                _apply_affinity(self.children, self._print)
                self._print("run.py", f"up: {', '.join(self.children)}")
            # Not a bare self._stop.wait(): on Windows that blocks inside a
            # single infinite WaitForSingleObject call, which never returns
            # control to the interpreter's bytecode loop, so a Ctrl-C
            # delivered by the console's separate control-handler thread
            # sits queued and is never actually acted on. Waking up on a
            # bounded timeout, repeatedly, is what lets Python check for
            # and run the pending SIGINT handler between waits — the same
            # reason time.sleep() is Ctrl-C-responsive on Windows and a
            # bare Lock.acquire()/Event.wait() is not.
            while not self._stop.wait(timeout=0.25):
                pass
        finally:
            self._shutdown()
        return 0

    def stop(self) -> None:
        """For tests and embedders: request shutdown without a signal."""
        self._stop.set()

    # -- starting --------------------------------------------------------

    def _start_supervisor(self, name: str) -> None:
        child = self.children[name]
        t = threading.Thread(target=self._supervise, args=(name,),
                             name=f"run-{name}", daemon=True)
        child.thread = t
        t.start()

    def _supervise(self, name: str) -> None:
        child = self.children[name]
        backoff = BACKOFF_START
        first = True
        while not self._stop.is_set() and not child.failed:
            if not first:
                child.restarts += 1
            first = False
            child.ready.clear()
            try:
                proc = self._spawn(child.spec)
            except OSError as e:
                self._record_failure(child, name, f"failed to start: {e}")
            else:
                child.proc = proc
                child.pid = proc.pid
                self._write_pidfile()
                self._print(name, f"[run.py] started pid {proc.pid}")
                reader = threading.Thread(target=self._pump, args=(name, proc),
                                          name=f"run-io-{name}", daemon=True)
                reader.start()
                code = proc.wait()
                reader.join(timeout=2.0)
                was_ready = child.ready.is_set()
                child.proc = None
                child.pid = None
                if self._stop.is_set():
                    return
                if was_ready:
                    backoff = BACKOFF_START
                self._print(name, f"[run.py] exited (code {code})")
                self._record_failure(child, name, f"exited with code {code}")

            if child.failed or self._stop.is_set():
                return
            if self.no_restart:
                self._print(name, "[run.py] not restarting (--no-restart)")
                return
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _spawn(self, spec: ProcessSpec) -> subprocess.Popen:
        env = dict(os.environ)
        env.update(spec.extra_env)
        kwargs = dict(cwd=str(spec.cwd), env=env, stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, bufsize=0)
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(list(spec.cmd), **kwargs)

    def _record_failure(self, child: ChildRuntime, name: str, reason: str) -> None:
        now = time.monotonic()
        child.fail_times.append(now)
        child.fail_times = [t for t in child.fail_times if now - t <= FAILURE_WINDOW]
        if len(child.fail_times) >= FAILURE_LIMIT:
            child.failed = True
            self._print(name, f"[run.py] FAILED — {reason}; {FAILURE_LIMIT} "
                        f"failures in {FAILURE_WINDOW:.0f}s, giving up "
                        "(doc section 20.2)")
        else:
            self._print(name, f"[run.py] {reason}")

    def _pump(self, name: str, proc: subprocess.Popen) -> None:
        child = self.children[name]
        # Not `for raw in proc.stdout:` — with bufsize=0 the pipe is a raw
        # io.FileIO, and iterating it blocks indefinitely on Windows instead
        # of yielding lines as they arrive (readline() on the same object
        # works fine). iter(readline, b"") sidesteps the iteration protocol
        # entirely and is what actually tails the child live.
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", errors="backslashreplace").rstrip("\r\n")
            if not line:
                continue
            if not child.ready.is_set() and hlog.is_ready_line(line) == name:
                child.ready.set()
            self._print(name, line)

    def _wait_ready(self, names: Sequence[str], timeout: float) -> List[str]:
        deadline = time.monotonic() + timeout
        pending = set(names)
        while pending and time.monotonic() < deadline and not self._stop.is_set():
            pending = {n for n in pending
                       if not (self.children[n].ready.is_set() or self.children[n].failed)}
            if pending:
                time.sleep(0.05)
        return sorted(pending)

    # -- stopping ----------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            # CPython can only register signal handlers on the main thread
            # of the main interpreter. run() is always there for the real
            # CLI; a caller that embeds Launcher on a worker thread (as the
            # tests do, to poll it while it runs) uses stop() directly and
            # was never going to receive an OS signal on that thread anyway.
            return

        def handler(signum, frame):
            self._stop.set()
        signal.signal(signal.SIGINT, handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, handler)
        if hasattr(signal, "SIGBREAK"):
            # Windows only. `cmd_stop` (--stop, "stop a detached instance")
            # runs as a separate process and has no cross-console way to
            # deliver SIGINT/SIGTERM to this one — CTRL_BREAK_EVENT via
            # _terminate() is the only thing that reaches us from outside.
            # Python maps it to SIGBREAK, but without a handler for it the
            # OS's default action is to kill this process immediately, with
            # no chance to run _shutdown() — every child then becomes a
            # real orphan, the exact failure this launcher exists to
            # prevent (module docstring). Treat it the same as SIGINT.
            signal.signal(signal.SIGBREAK, handler)

    def _shutdown(self) -> None:
        self._stop.set()
        live = [(n, c.proc) for n, c in self.children.items()
                if c.proc is not None and c.proc.poll() is None]
        for _, p in live:
            _terminate(p.pid, graceful=True)

        deadline = time.monotonic() + GRACE_PERIOD
        survivors = []
        for n, p in live:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                survivors.append((n, p))

        for n, p in survivors:
            self._print(n, "[run.py] did not stop within 5s, killing")
            _terminate(p.pid, graceful=False)
            try:
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass

        # Doc section 10.2: "report which processes needed the kill —
        # needing it is a bug to fix later."
        if survivors:
            self._print("run.py", "needed a hard kill for: "
                        f"{', '.join(n for n, _ in survivors)}")

        for c in self.children.values():
            if c.thread is not None:
                c.thread.join(timeout=2.0)

        _remove_pidfile(self.pidfile)
        self.mlog.close()

    # -- misc ----------------------------------------------------------

    def _print(self, name: str, line: str) -> None:
        prefix = f"{name:<10}| "
        colour = _COLOUR.get(name, _LAUNCHER_COLOUR)
        if self._colour:
            sys.stdout.write(f"{colour}{prefix}{_RESET}{line}\n")
        else:
            sys.stdout.write(f"{prefix}{line}\n")
        sys.stdout.flush()
        self.mlog.write(f"{prefix}{line}")

    def _write_pidfile(self) -> None:
        # Every tier-3 supervisor thread calls this the moment its child
        # spawns, all at once (doc's KNOWN ISSUE, CLAUDE.md). Without this
        # lock two threads' os.replace(tmp, dest) can land at the same
        # instant and Windows — unlike POSIX, where rename() atomically
        # replaces the destination — refuses the second one with WinError
        # 32 because it briefly has the destination handle open. Losing
        # that race doesn't just print an ugly traceback: it kills the
        # supervisor thread outright (the crash happens before the reader
        # thread starts), silently dropping restart-on-crash and log
        # capture for that child for the rest of the run. A single-writer
        # lock is cheap enough here that a queue is not worth it.
        with self._pidfile_lock:
            data = {
                "launcher_pid": os.getpid(),
                "started": time.time(),
                "children": {n: c.pid for n, c in self.children.items() if c.pid},
            }
            atomicio.write_json(self.pidfile, data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _select_names(only: Optional[str]) -> List[str]:
    if only is None:
        return [p.name for p in PROCESSES]
    valid = {p.name for p in PROCESSES}
    seen: set = set()
    out: List[str] = []
    unknown: List[str] = []
    for raw in only.split(","):
        n = raw.strip()
        if not n or n in seen:
            continue
        seen.add(n)
        if n in valid:
            out.append(n)
        else:
            unknown.append(n)
    if unknown:
        raise SystemExit(f"run.py: unknown process name(s): {', '.join(unknown)} "
                          f"(choices: {', '.join(sorted(valid))})")
    return out


def _read_pidfile(path: Path) -> Optional[dict]:
    """Read the pidfile, tolerating corruption instead of crashing the CLI.

    atomicio's "present but unparsable must raise" rule (its module
    docstring) is right for calibration files, where corruption silently
    mis-bills. It is too strict for this file: run.pid exists only to
    answer "is an instance already running," and before the lock added to
    Launcher._write_pidfile (see the KNOWN ISSUE this fixes), two
    supervisor threads racing on os.replace could tear it exactly like
    this. atomicio already logs the corruption loudly on its way past —
    this just stops that from also being a crash that blocks every future
    `python run.py` until someone finds and deletes the file by hand.
    """
    try:
        return atomicio.read_json(path, default=None)
    except ValueError:
        return None


def _running_launcher_pid(root: Path) -> Optional[int]:
    data = _read_pidfile(_pidfile_path(root))
    if not data:
        return None
    pid = data.get("launcher_pid")
    if isinstance(pid, int) and _pid_alive(pid):
        return pid
    return None


def cmd_stop(root: Path = ROOT) -> int:
    pidfile = _pidfile_path(root)
    data = _read_pidfile(pidfile)
    if not data:
        print("run.py: no pidfile — nothing to stop")
        return 0

    pids: Dict[str, int] = {}
    launcher_pid = data.get("launcher_pid")
    if isinstance(launcher_pid, int):
        pids["run.py"] = launcher_pid
    for name, pid in (data.get("children") or {}).items():
        if isinstance(pid, int):
            pids[name] = pid

    live = {n: pid for n, pid in pids.items() if _pid_alive(pid)}
    if not live:
        print("run.py: pidfile is stale — nothing running")
        _remove_pidfile(pidfile)
        return 0

    for pid in live.values():
        _terminate(pid, graceful=True)

    deadline = time.monotonic() + GRACE_PERIOD
    while time.monotonic() < deadline and any(_pid_alive(p) for p in live.values()):
        time.sleep(0.1)

    survivors = {n: pid for n, pid in live.items() if _pid_alive(pid)}
    for pid in survivors.values():
        _terminate(pid, graceful=False)
    if survivors:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and any(_pid_alive(p) for p in survivors.values()):
            time.sleep(0.1)

    _remove_pidfile(pidfile)
    still = [n for n, pid in survivors.items() if _pid_alive(pid)]
    if still:
        print(f"run.py: could not stop: {', '.join(still)}")
        return 1
    print(f"run.py: stopped ({', '.join(live)})")
    return 0


def _remove_pidfile(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="run.py", description="Start, supervise, and stop the hotpot table.")
    p.add_argument("--stop", action="store_true",
                    help="stop a detached instance")
    p.add_argument("--only", metavar="NAMES", default=None,
                    help="comma-separated subset to start, e.g. core,of")
    p.add_argument("--no-restart", action="store_true",
                    help="disable auto-restart (debugging a crash)")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.stop:
        return cmd_stop(ROOT)

    names = _select_names(args.only)

    existing = _running_launcher_pid(ROOT)
    if existing is not None:
        print(f"run.py: already running (pid {existing}) — stop it first with --stop")
        return 1

    launcher = Launcher(names, no_restart=args.no_restart)
    return launcher.run()


if __name__ == "__main__":
    raise SystemExit(main())

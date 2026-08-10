"""Tests for run.py (doc section 10).

Run from the repo root:

    python -m unittest discover -s python/tests -v

run.py lives at the repo root, not under python/hotpot/, because doc section
7 calls it out as "THE single entry point" — a launcher, not application
code. These tests reach it the same way the tests for the packaged modules
reach hotpot.common: sys.path is extended so `import run` works regardless
of the current working directory.

The integration tests below spawn real child processes — tiny scripts that
print HOTPOT-READY, crash on command, or ignore a graceful stop — rather
than mocking subprocess. run.py's entire job is process-group creation,
signal delivery and stdout tailing, all OS primitives that a mock would
have to reimplement to be worth trusting; a real child proves the thing
this module exists to prove. Every timing constant (backoff, grace period,
startup timeout) is monkeypatched down for the duration of a test so the
suite stays fast without changing what is being asserted.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import run  # noqa: E402


DEADLINE = 15.0          # generous: spawning real processes on a loaded box is slow


def wait_for(pred, timeout=DEADLINE, tick=0.05):
    """Poll until pred() is truthy. Returns the value, or False on timeout."""
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(tick)
    return False


CHILD_SCRIPT = '''
import os, signal, sys, time

name = sys.argv[1]
mode = sys.argv[2]


def ready():
    print("HOTPOT-READY " + name, flush=True)


def loop():
    while True:
        time.sleep(0.05)


if mode == "ready-loop":
    ready()
    loop()

elif mode == "crash":
    print("crashing", flush=True)
    sys.exit(1)

elif mode == "flaky":
    counter_file, fail_times = sys.argv[3], int(sys.argv[4])
    n = int(open(counter_file).read().strip()) if os.path.exists(counter_file) else 0
    n += 1
    open(counter_file, "w").write(str(n))
    if n <= fail_times:
        print("attempt %d failing" % n, flush=True)
        sys.exit(1)
    ready()
    loop()

elif mode == "always-crash":
    counter_file = sys.argv[3]
    n = int(open(counter_file).read().strip()) if os.path.exists(counter_file) else 0
    n += 1
    open(counter_file, "w").write(str(n))
    sys.exit(1)

elif mode == "ignore-stop":
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *a: None)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, lambda *a: None)
        except (ValueError, OSError):
            pass
    ready()
    loop()

else:
    raise SystemExit("unknown mode " + mode)
'''


class RunTestCase(unittest.TestCase):
    """Common fixture: an isolated root dir and a swappable process table.

    PROCESS_BY_NAME is module-global (it is run.py's single source of truth
    for what each of the six names launches), so tests that need a fake
    child patch entries into it and restore the originals on cleanup rather
    than threading a table through every call.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = self.root / "child.py"
        self.script.write_text(CHILD_SCRIPT, encoding="utf-8")

        self._orig_processes = dict(run.PROCESS_BY_NAME)
        self.addCleanup(self._restore_processes)
        self._orig_consts = {}
        self.addCleanup(self._restore_consts)

    def _restore_processes(self):
        run.PROCESS_BY_NAME.clear()
        run.PROCESS_BY_NAME.update(self._orig_processes)

    def _restore_consts(self):
        for k, v in self._orig_consts.items():
            setattr(run, k, v)

    def set_process(self, name, mode, *extra):
        cmd = (sys.executable, str(self.script), name, mode) + tuple(str(a) for a in extra)
        run.PROCESS_BY_NAME[name] = run.ProcessSpec(
            name=name, tier=run._TIER[name], cmd=cmd, cwd=self.root)

    def patch_const(self, name, value):
        self._orig_consts.setdefault(name, getattr(run, name))
        setattr(run, name, value)

    def log_text(self):
        p = self.root / "logs" / ("hotpot-" + time.strftime("%Y-%m-%d") + ".log")
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def start(self, launcher):
        """Run launcher.run() on a thread and guarantee it is torn down."""
        t = threading.Thread(target=launcher.run, daemon=True)
        t.start()

        def cleanup():
            launcher.stop()
            t.join(timeout=5.0)
        self.addCleanup(cleanup)
        return t


# ---------------------------------------------------------------------------
# --only : name selection
# ---------------------------------------------------------------------------

class TestSelectNames(unittest.TestCase):

    def test_none_returns_all_six_in_pip_order(self):
        self.assertEqual(run._select_names(None), list(run.health.PROCESSES))

    def test_subset_preserves_order_and_dedupes(self):
        self.assertEqual(run._select_names("core, of, core"), ["core", "of"])

    def test_unknown_name_raises_with_the_choices_listed(self):
        with self.assertRaises(SystemExit) as ctx:
            run._select_names("core,teapot")
        self.assertIn("teapot", str(ctx.exception))
        self.assertIn("core", str(ctx.exception))


class TestParseArgs(unittest.TestCase):

    def test_defaults(self):
        args = run.parse_args([])
        self.assertFalse(args.stop)
        self.assertIsNone(args.only)
        self.assertFalse(args.no_restart)

    def test_flags(self):
        args = run.parse_args(["--only", "core,of", "--no-restart"])
        self.assertEqual(args.only, "core,of")
        self.assertTrue(args.no_restart)
        self.assertTrue(run.parse_args(["--stop"]).stop)


# ---------------------------------------------------------------------------
# Merged, rotated logging
# ---------------------------------------------------------------------------

class TestMergedLog(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "logs"

    def test_writes_the_dated_file(self):
        mlog = run.MergedLog(self.dir)
        mlog.write("camera    | hello")
        mlog.close()
        path = self.dir / ("hotpot-" + time.strftime("%Y-%m-%d") + ".log")
        self.assertTrue(path.exists())
        self.assertIn("camera    | hello", path.read_text(encoding="utf-8"))

    def test_reopens_on_a_date_change(self):
        mlog = run.MergedLog(self.dir)
        real_strftime = time.strftime
        try:
            run.time.strftime = lambda fmt: "2026-01-01"
            mlog.write("day one")
            run.time.strftime = lambda fmt: "2026-01-02"
            mlog.write("day two")
        finally:
            run.time.strftime = real_strftime
            mlog.close()
        self.assertIn("day one", (self.dir / "hotpot-2026-01-01.log").read_text())
        self.assertIn("day two", (self.dir / "hotpot-2026-01-02.log").read_text())


# ---------------------------------------------------------------------------
# CPU affinity (doc section 10.4) — injected, since the dev box is Windows
# ---------------------------------------------------------------------------

class TestApplyAffinity(unittest.TestCase):

    def setUp(self):
        self.calls = []
        self.messages = []

    def _print(self, name, line):
        self.messages.append((name, line))

    def _fake_setaffinity(self, pid, cores):
        self.calls.append((pid, cores))

    def _children(self):
        c = {}
        for name, pid in [("of", 1), ("tracker", 2), ("classifier", 3),
                           ("camera", 5), ("core", 6)]:
            child = run.ChildRuntime(run.PROCESS_BY_NAME[name])
            child.pid = pid
            c[name] = child
        # voice deliberately has no pid: not yet up when affinity is applied.
        c["voice"] = run.ChildRuntime(run.PROCESS_BY_NAME["voice"])
        return c

    def test_skips_entirely_off_linux(self):
        run._apply_affinity(self._children(), self._print, is_posix=False,
                            cpu_count=4, setaffinity=self._fake_setaffinity)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.messages, [])

    def test_skips_with_a_warning_under_four_cores(self):
        run._apply_affinity(self._children(), self._print, is_posix=True,
                            cpu_count=2, setaffinity=self._fake_setaffinity)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.messages), 1)
        self.assertIn("2 cores", self.messages[0][1])

    def test_pins_per_plan_and_skips_the_unstarted_one(self):
        run._apply_affinity(self._children(), self._print, is_posix=True,
                            cpu_count=4, setaffinity=self._fake_setaffinity)
        self.assertEqual(sorted(self.calls), sorted([
            (1, {0}),   # of -> core 0
            (2, {1}),   # tracker -> core 1
            (3, {2}),   # classifier -> core 2
            (5, {3}),   # camera -> core 3
            (6, {3}),   # core -> core 3
        ]))
        # voice shares core 2 with classifier but has no pid yet — not an
        # error, just nothing to pin.
        self.assertEqual(len(self.calls), 5)


# ---------------------------------------------------------------------------
# pid liveness and termination — real processes, no mocks
# ---------------------------------------------------------------------------

def _spawn_group(cmd):
    kwargs = {}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)


class TestPidAliveAndTerminate(unittest.TestCase):

    def test_alive_then_dead_after_natural_exit(self):
        proc = _spawn_group([sys.executable, "-c", "pass"])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        proc.wait(timeout=DEADLINE)
        self.assertFalse(run._pid_alive(proc.pid))

    def test_graceful_terminate_stops_a_plain_sleeper(self):
        proc = _spawn_group([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self.assertTrue(wait_for(lambda: run._pid_alive(proc.pid)))
        run._terminate(proc.pid, graceful=True)
        self.assertTrue(wait_for(lambda: not run._pid_alive(proc.pid)))


# ---------------------------------------------------------------------------
# Launcher — tiered start, readiness gating
# ---------------------------------------------------------------------------

class TestTieredStart(RunTestCase):

    def test_waits_for_readiness_before_the_next_tier_and_reports_up(self):
        self.set_process("camera", "ready-loop")   # tier 1
        self.set_process("core", "ready-loop")     # tier 2
        launcher = run.Launcher(["camera", "core"], root=self.root)
        self.start(launcher)

        self.assertTrue(wait_for(lambda: "HOTPOT-READY camera" in self.log_text()))
        self.assertTrue(wait_for(lambda: "HOTPOT-READY core" in self.log_text()))
        self.assertTrue(wait_for(lambda: "up: camera, core" in self.log_text()))

    def test_only_starts_the_selected_subset(self):
        self.set_process("camera", "ready-loop")
        self.set_process("core", "ready-loop")
        launcher = run.Launcher(["core"], root=self.root)
        self.start(launcher)

        self.assertTrue(wait_for(lambda: "HOTPOT-READY core" in self.log_text()))
        time.sleep(0.3)
        self.assertNotIn("camera", launcher.children)
        self.assertNotIn("HOTPOT-READY camera", self.log_text())

    def test_a_slow_tier_is_reported_and_does_not_hang_the_launcher(self):
        self.patch_const("STARTUP_TIMEOUT", 0.3)
        self.set_process("camera", "crash")   # never prints HOTPOT-READY
        launcher = run.Launcher(["camera"], no_restart=True, root=self.root)
        self.start(launcher)

        self.assertTrue(wait_for(
            lambda: "not ready after" in self.log_text() and "camera" in self.log_text()))


# ---------------------------------------------------------------------------
# Launcher — crash restart and the failure ladder (doc section 20.2)
# ---------------------------------------------------------------------------

class TestRestart(RunTestCase):

    def setUp(self):
        super().setUp()
        self.patch_const("BACKOFF_START", 0.02)
        self.patch_const("BACKOFF_MAX", 0.05)

    def test_restarts_with_backoff_and_eventually_comes_up(self):
        counter = self.root / "attempts.txt"
        self.set_process("camera", "flaky", counter, 2)
        launcher = run.Launcher(["camera"], root=self.root)
        self.start(launcher)

        self.assertTrue(wait_for(lambda: "HOTPOT-READY camera" in self.log_text()))
        self.assertEqual(launcher.children["camera"].restarts, 2)

    def test_no_restart_stops_after_one_crash(self):
        self.set_process("camera", "crash")
        launcher = run.Launcher(["camera"], no_restart=True, root=self.root)
        self.start(launcher)

        self.assertTrue(wait_for(lambda: "not restarting" in self.log_text()))
        time.sleep(0.3)
        self.assertEqual(launcher.children["camera"].restarts, 0)

    def test_five_failures_in_the_window_marks_it_failed_and_stops(self):
        counter = self.root / "attempts.txt"
        self.set_process("camera", "always-crash", counter)
        launcher = run.Launcher(["camera"], root=self.root)
        self.start(launcher)

        self.assertTrue(wait_for(
            lambda: counter.exists() and counter.read_text().strip() == "5"))
        self.assertTrue(wait_for(lambda: launcher.children["camera"].failed))
        self.assertIn("giving up", self.log_text())

        # No sixth attempt sneaks in after being marked failed.
        time.sleep(0.3)
        self.assertEqual(counter.read_text().strip(), "5")


# ---------------------------------------------------------------------------
# Launcher — shutdown (doc section 10.2)
# ---------------------------------------------------------------------------

class TestShutdown(RunTestCase):

    def test_clean_stop_removes_the_pidfile_and_needs_no_hard_kill(self):
        self.set_process("camera", "ready-loop")
        launcher = run.Launcher(["camera"], root=self.root)
        self.start(launcher)
        self.assertTrue(wait_for(lambda: "HOTPOT-READY camera" in self.log_text()))

        launcher.stop()
        self.assertTrue(wait_for(lambda: not launcher.pidfile.exists()))
        self.assertNotIn("needed a hard kill", self.log_text())

    def test_a_process_that_ignores_the_graceful_stop_is_hard_killed(self):
        self.patch_const("GRACE_PERIOD", 0.3)
        self.set_process("camera", "ignore-stop")
        launcher = run.Launcher(["camera"], root=self.root)
        self.start(launcher)
        self.assertTrue(wait_for(lambda: "HOTPOT-READY camera" in self.log_text()))
        pid = launcher.children["camera"].pid

        launcher.stop()
        self.assertTrue(wait_for(lambda: "needed a hard kill" in self.log_text()))
        self.assertTrue(wait_for(lambda: not run._pid_alive(pid)))
        self.assertTrue(wait_for(lambda: not launcher.pidfile.exists()))


# ---------------------------------------------------------------------------
# --stop : recovering a detached instance from its pidfile
# ---------------------------------------------------------------------------

class TestCmdStop(RunTestCase):

    def setUp(self):
        super().setUp()
        self.patch_const("GRACE_PERIOD", 0.3)

    def test_no_pidfile_is_a_clean_no_op(self):
        self.assertEqual(run.cmd_stop(self.root), 0)

    def test_stale_pidfile_is_reported_and_removed(self):
        dead = _spawn_group([sys.executable, "-c", "pass"])
        dead.wait(timeout=DEADLINE)
        run.atomicio.write_json(run._pidfile_path(self.root),
                                {"launcher_pid": dead.pid, "children": {}})
        self.assertEqual(run.cmd_stop(self.root), 0)
        self.assertFalse(run._pidfile_path(self.root).exists())

    def test_stops_every_recorded_process_and_removes_the_pidfile(self):
        a = _spawn_group([sys.executable, "-c", "import time; time.sleep(60)"])
        b = _spawn_group([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(lambda: a.poll() is None and a.kill())
        self.addCleanup(lambda: b.poll() is None and b.kill())
        self.assertTrue(wait_for(lambda: run._pid_alive(a.pid) and run._pid_alive(b.pid)))

        run.atomicio.write_json(run._pidfile_path(self.root),
                                {"launcher_pid": a.pid, "children": {"camera": b.pid}})
        self.assertEqual(run.cmd_stop(self.root), 0)
        self.assertFalse(run._pidfile_path(self.root).exists())
        self.assertFalse(run._pid_alive(a.pid))
        self.assertFalse(run._pid_alive(b.pid))


if __name__ == "__main__":
    unittest.main()

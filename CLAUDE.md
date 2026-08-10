# HOTPOT TABLE

Interactive weigh-by-weight hot pot ingredient counter.
Seeed "Make a Sign" Interactive Signage Contest 2026.
Product name: Hot Pot (en) / 称重火锅 (zh).

## GROUND TRUTH
Read docs/HOTPOT_ARCHITECTURE_v3.md before doing anything.
It is authoritative. This file is only status + rules.

## STATUS
Architecture v3 adopted. Full rewrite in progress.
Stage 1-2 code is being replaced, not extended.
Current milestone: M0 (scaffold, launcher, transport).
Last completed step: M0.7 (core/main.py — control server, client
registry, minimal staff view; doc section 21 build item 7). Control
server and registry are hotpot.common.wire/health, reused as-is.
Staff view is core/web/server.py, built on the `websockets` package
(python/requirements.txt — first Python dependency in the repo) rather
than hand-rolled RFC 6455, serving core/web/static/index.html — header
only, six pips pushed live, dark UI per doc 12.1.
All 7 M0 build items are now code-complete.
Next step: M0's human acceptance test (doc section 21) — run
`python run.py`, confirm six pips green in the browser within 5s,
kill a child and confirm its pip goes red then green again, Ctrl-C
and confirm every process exits with no orphan (`tasklist` clean).
Not yet run: needs the human on the dev machine.
`core.web_port` moved from 8080 to 8090 (2026-08-10): on this dev
machine 8080 is perpetually claimed by a stale WSL2 portproxy relay
(`netsh interface portproxy show v4tov4`) to the Ubuntu distro's IP,
left behind by `iphlpsvc` even when that distro is stopped — not a
one-off squatter, so routing around it beats trying to clear it.

## FIXED (2026-08-10) — run.py pidfile race, and Ctrl-C not stopping it
Two bugs found running M0's acceptance test for real the first time
(earlier attempts never reached this code path — core kept failing to
bind 8080 and the launcher gave up on its own before anyone needed to
stop it):
- Pidfile race: concurrent tier-3 supervisor threads all called
  _write_pidfile at once; os.replace losing that race didn't just log —
  it killed the supervisor thread outright (crash lands before the
  reader thread starts), silently dropping restart-on-crash and log
  capture for that child, and could tear state/run.pid into two
  concatenated JSON objects, which then crashed every future
  `python run.py` on startup until someone deleted the file by hand.
  Fixed with a lock around _write_pidfile, plus _read_pidfile()
  tolerating an already-corrupt file instead of crashing the CLI.
- Ctrl-C did nothing: `self._stop.wait()` with no timeout blocks inside
  a single infinite Win32 wait call that never returns control to the
  interpreter, so a pending SIGINT is never actually acted on. Fixed
  with a bounded-wait loop. `--stop` had two more bugs on top of that,
  found verifying the fix: os.kill(..., CTRL_BREAK_EVENT) can raise a
  bare SystemError instead of OSError on Windows, which _terminate
  didn't catch, aborting the whole shutdown loop before later children
  got touched; and the launcher had no SIGBREAK handler, so a
  CTRL_BREAK_EVENT that did land killed it outright instead of running
  _shutdown(), orphaning every child. Both fixed; `--stop` now verified
  end-to-end (6/6 processes exit, pidfile removed, no orphans).

## HOW TO WORK HERE
- One step at a time. Commit. Stop and report back.
- The developer is dyslexic. Reports are short.
  One thing at a time. Confirmation questions must name
  the single specific thing being confirmed.
- Never assume an external API exists. Verify against the
  installed version. Items marked VERIFY in the doc are
  where this has already gone wrong.
- Every check must be capable of failing. Items marked
  TRAP in the doc are checks that pass by construction.
- Say whether evidence is a framebuffer capture or
  physical observation of the projected surface.

## HARD INVARIANTS (full list in doc section 2)
- Core owns all state. oF is a dumb renderer.
- Core never touches a frame.
- Price = (startWeight - liveWeight) / 100 * pricePer100g.
  Never sum per-event deltas. No put-back branch.
- The 10g deadband is display-only and SNAPS to truth.
- Re-baseline, never re-tare.
- Food position is not fixed. Bin map is live data.
- The projected field is the ILLUMINANT, not a background.
  Dark room, so the projector is the only light the
  camera has. Every tray cutout gets a flat pure-white
  patch at full level, stamped LAST so nothing can draw
  into it. Never black, never coloured, never patterned.
  Everything else stays above a white floor so the hand
  stays trackable. Only dot calibration inverts.
- Distinguish states by hue, never by brightness, and
  luminance-match the hues to each other.

## DEPLOY MACHINE
Seeed ODYSSEY-X86J4125800 v2. Settled.
Full spec and what it changes: doc section 1.4.
Short version:
- 4 cores, no SMT. Affinity plan fits exactly.
- NO AVX2. Every model must be proven on the board.
- UHD 600, 12 EU, shares RAM with the CPU.
  Fluid starts at sim_scale 8 and climbs.
- One USB 3 port. The camera gets it. Nothing else.
- Order with it: NVMe SSD, fan, 12V barrel PSU.
No features were cut to fit this board.

## TOP RISKS
- No AVX2. MediaPipe or a .eim may not run at all.
  Prove both on the board in M0.B.
- Throttling. 10W passive part beside a hot pot.
- 64GB eMMC too small. Install the OS on the SSD.
- Camera elevation angle never measured. Due in M3.

## NUMBERS OWED (write them here when measured)
- MediaPipe fps at model_complexity 0 and 1.
- Peak package temp and throttle count after soak.
- Camera elevation angle.

## BUILD
Dev: oF 0.12.1, Visual Studio 2026, toolset v145
(projectGenerator emits v143 - must be changed).
msbuild hotpot-table.sln /p:Configuration=Debug
        /p:Platform=x64 /m
Deploy: Linux x86_64. Makefile and config.make must
be generated on the board itself. Never copied.
Firmware: PlatformIO, firmware/loadcells/. Do not touch.
Python deps: pip install -r python/requirements.txt (once per machine).
Python tests: python -m unittest discover -s python/tests
Run them before every commit that touches python/.

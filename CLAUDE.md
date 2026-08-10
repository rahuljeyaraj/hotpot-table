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
Last completed step: M0.6 (stub main.py for camera, tracker,
classifier, voice; shared as common/stub.py, per doc section 10 item 6).
Next step: M0.7, core/main.py — control server, client registry,
and a minimal staff view serving only the header with six status
pips over a WebSocket.

## KNOWN ISSUE — run.py pidfile race on Windows
Starting 3+ children in the same tier concurrently (e.g.
`--only camera,tracker,classifier,voice`, all tier 3 apart from
camera) throws PermissionError from atomicio.write_json inside
_write_pidfile: each child's supervisor thread writes state/run.pid
independently and os.replace collides with a sibling's .tmp on
Windows. Processes still start, print ready, and shut down clean
with no orphans — only the pidfile write races. Not fixed here;
out of scope for M0.6 (stub main.py only). Worth a lock or a
single-writer queue in run.py before M0's own acceptance test.

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
Python tests: python -m unittest discover -s python/tests
Run them before every commit that touches python/.

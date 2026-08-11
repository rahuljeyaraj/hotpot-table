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
Current milestone: M2 (load cells). M1 is code-complete; its human
acceptance test on the rig is still owed.
M0's last completed step was M0.7 (core/main.py — control server,
client registry, minimal staff view; doc section 21 build item 7).
Control server and registry are hotpot.common.wire/health, reused as-is.
Staff view is core/web/server.py, built on the `websockets` package
(python/requirements.txt — first Python dependency in the repo) rather
than hand-rolled RFC 6455, serving core/web/static/index.html — header
only, six pips pushed live, dark UI per doc 12.1.
All 7 M0 build items are now code-complete.
M0's human acceptance test (doc section 21) passed on the dev machine
2026-08-10, including Ctrl-C exiting every process with no orphan —
the two bugs blocking that (pidfile race, Ctrl-C not stopping the
launcher) are in the FIXED section below. M0 is done; next is M1.
`core.web_port` moved from 8080 to 8090 (2026-08-10): on this dev
machine 8080 is perpetually claimed by a stale WSL2 portproxy relay
(`netsh interface portproxy show v4tov4`) to the Ubuntu distro's IP,
left behind by `iphlpsvc` even when that distro is stopped — not a
one-off squatter, so routing around it beats trying to clear it.
M1.1 (2026-08-10) added data/catalogue.json and data/locales/en.json
(doc section 21 build item 1). M1.2 (2026-08-10) added the five pure
domain modules build item 2 calls for: core/pricing.py (Catalogue,
loaded from catalogue.json, plus the doc 9.2 bin_price/total formulas),
core/cart.py (start/live/shown grams per bin, I6's reset_session, the
doc 9.2 display deadband — snaps, never creeps), core/binmap.py (doc
8.2's 8-bin state and doc 9.3's resolved() confidence-floor check),
core/i18n.py (doc 17's flat locale strings and per-locale currency
conversion), core/fsm.py (doc 9.1, deliberately scoped to BOOT / IDLE /
SELECTING only, per the build item — every other state arrives with
the milestone that needs it). All five are unit-tested (69 new tests,
python/tests/test_{pricing,cart,binmap,i18n,fsm}.py).
M1.3 (2026-08-10) is build item 3: wired all five into core/main.py and
added the 60Hz `state` broadcaster (doc section 4.3), sent to `of` only.
Core.__init__ now loads data/catalogue.json and data/locales/ (English
only, per build item 4), hand-seeds an 8-bin BinMap one-to-one from
Catalogue.ids() (new method — build item 2 didn't need enumeration
order, this does) at conf 1.0, and hand-seeds Cart with every bin at
MOCK_SEED_GRAMS (500g) so the mock pick/put-back cycle (build item 5,
not built yet) has weight to remove. Fsm.boot_complete() fires
immediately in start() since M1's BOOT always succeeds. The state
message's `fluid` and `widgets`/`overlay` fields are sent per the doc
4.3 shape but inert — style "mala" with enabled:false, empty widgets,
overlay "none" — since M8's renderer and M6's checkout states don't
exist yet to give them real content. 8 new tests
(python/tests/test_core_main.py's TestStateBroadcast, plus two in
test_pricing.py for the new ids() method) drive Cart/BinMap directly
(the developer-panel mock controls that will do this for real are build
item 4/5) and check the message shape, the seeded bins, seq monotonicity,
a mock pick reaching the next broadcast correctly priced, and that only
`of` receives it.
M1.4 (2026-08-10) is build item 4: the oF app rewritten around
StateLink (of/hotpot-table/src/StateLink.h/.cpp — TCP JSONL client on
its own thread, hello/welcome/heartbeat, reconnect backoff 1s->10s,
parses `state`), Stage (Stage.h/.cpp — one content FBO, floor lift and
light pass done with plain alpha-blended rects rather than a shader,
keystone warp onto the window from bin/data/keystone.json, defaulted
to the untransformed rectangle since no prior software keystone existed
to carry forward — VERIFIED against the pre-rewrite ofApp, which only
ever fullscreened onto a monitor), and UiLayer (UiLayer.h/.cpp — 8
plates on TableGeometry.h's CAD rects, name+detail text outside every
cutout, a Spring per tweened value per doc §13.3, running total, a
diner-facing connection-lost indicator, a small dev overlay). Added
ofxNetwork to addons.make and the vcxproj (VS projects don't glob —
new addons and new src files both had to be added to hotpot-table.vcxproj
by hand, which is what most of this step's build errors were).
Everything M0.1/M1's "delete outright" list named is gone: OSC hand
receiver, hover/dwell, the alignment nudge grid, the keyboard weight
mock, in-bin weight text, the calibration-dot pattern.
Two scope calls, both commented in the code where they bite: (1) the
odometer digit-ROLL render is deferred — Spring.h builds the tweened
value it would consume, but the glyph-clipping needed to draw it had no
precedent anywhere in this app (no shader, no scissor use) and M1's
acceptance test checks the settled number by arithmetic, not the roll.
(2) Inter (doc §13.4's `en` face) isn't in this repo or the oF
distribution; UiLayer uses the already-bundled DejaVuSans-Bold.ttf
until the real font file shows up — one line to swap.
Builds clean (msbuild, 0 errors, 0 warnings from any of the new files).
Not yet run against a live core — that's the acceptance test, on the
projected surface, still owed.
M1.5 (2026-08-10) is build item 5, the last of M1: the staff view's Live
tab shell (doc §12.3 — the MJPEG/canvas slot and the rects/labels/
weights/hands/dots toggle chips, all inert: no camera until M3, no bin
rects on the wire until M4) and the developer panel's mock pick/put-back
controls (doc §12.8), which is the piece M1's acceptance test actually
needs to be runnable by a human before load cells exist. web/server.py
gained `on_message` (incoming WS frames were discarded outright through
M1.4 — its own docstring said so); core/main.py wires it to
cart.mock_pick/mock_putback, the exact entry point
test_core_main.py's TestStateBroadcast already poked directly and said
outright it was bypassing. Bin index and grams are validated (bad input
logged and dropped, never crashes the link — same tolerance wire.py
gives a malformed line). The {45,6,120,3,25,80} g cycle is one shared
pointer across all 16 buttons (8 bins × pick/put-back), matching the
doc's singular "the cycle" and the acceptance test's own example.
Developer-panel toggle lives on the tab bar for now, not inside Setup
(§12.8's spec'd home) — Setup doesn't exist until M4; move it then.
14 new tests (test_web.py's TestOnMessage, test_core_main.py's
TestDeveloperPanelMockControls — WS-driven pick/put-back, the full
cycle in sequence, bad bin, negative grams), all passing, 228/228 total.
Manually confirmed core serves the updated index.html over real HTTP
with the new markup and UTF-8 title intact.
Last completed step: M1.5, then two fix passes on 2026-08-11.
**M1 build items 1-5 are all code-complete.** 239 tests pass.

Pass (a), commit 0d79f33 — bugs found running the acceptance test on the
rig: white-on-white labels, name overflow, missing ₹ glyph, dev overlay
always on, no total caption.

Pass (b) — an audit of every M0/M1 build item against the doc. Five
holes, all fixed, each with tests checked capable of failing by reverting
the fix and watching them go red:
- **The plate ring was drawn and then erased.** UiLayer stroked it on the
  bin rect; the light pass stamps white over the bin +10mm, last. The
  outline, the pick pop and the entire `hl` highlight never reached the
  projector, so I8 (hue carries state) had no rendering channel at all.
  The ring now frames the cutout from outside it, per §14.4's annulus
  rule. **Never seen on the table — only built.**
- **Doc §13.4's stroke rule was WRONG and has been corrected in the doc.**
  `ofPath::setStrokeWidth()` IS `ofSetLineWidth()` — verified in the
  installed `ofGLRenderer.cpp` — and is ignored outright by the
  programmable renderer M8's fluid will force. Rings must be filled
  geometry. **Read §13.4 before building M5's dwell ring or M8's halos.**
- Per-bin price came from true removed grams while the grams beside it
  came from the deadband: a plate could read "45g" next to the price of
  51g, and at M2 load-cell noise would have twitched the total while the
  grams sat still — the deadband failing at its one job.
  `pricing.shown_total()` is now what the table shows; `pricing.total()`
  is untouched and is still what bills. They converge at `finalize()`.
- Core had no lock around cart/binmap/fsm — the 60Hz broadcaster raced
  the tablet's WebSocket thread. `Core.state_lock`. One frame wide today;
  it matters at M2 (serial thread) and breaks M6 (finalize + order write
  + reset_session must be atomic against a read).
- Far-row labels lost their descenders to the light pass, and "/100g" was
  hardcoded English inside core.

Known gaps, named in the doc but in no M0/M1 build item — decide, don't
assume they were missed:
- `config/system.default.json` (§7's tree) does not exist and nothing
  loads config. Every port and threshold is a hardcoded doc default.
- run.py marks a child `failed` after 5 crashes in 60s (§20.2) but never
  tells core, so the staff view cannot tell `failed` from `down`.
  `health.Registry.mark_failed` exists, is tested, and nothing calls it.
- `welcome.cfg` is always `{}`. Nothing needs it before M5's tracker.

Still owed from M1: its human acceptance test (doc §21) on the physical
rig. The plate ring, the pick pop and the descender fix have never been
observed.

## M2 — LOAD CELLS (in progress)

M2.1 (2026-08-11) is build item 3, done **before** build item 2 because
the dependency runs the other way: settle detection is specified in grams
(§9.5, "within ±2g"), grams need a calibration, so the maths has to exist
before the thread that consumes it. `core/loadcell_cal.py` — §9.6's
two-point maths and §8.3's state file, deliberately with no serial port in
it, so the one number that can *silently mis-bill* is testable with no
XIAO attached. The sign is computed and the API has no parameter to
override it (M2's "Do NOT"); `tare()` preserves `counts_per_gram` and is
**not** I6's re-baseline (both documented at the top of the module);
an uncalibrated bin and a dead XIAO both read `None`, never `0.0`, so
neither can bill. 26 tests, checked capable of failing by three mutations.

### VERIFIED ON THE RIG — read before writing core/scale.py
Build item 1 says read `firmware/loadcells/src/main.cpp` and do not assume
§4.9. Both halves of §4.9 were checked against COM5 on 2026-08-11:
- **Format is right.** `raw <c0> ... <c7>\r\n` at 115200, 9 tokens.
- **Rate is WRONG in the doc. §4.9 says ~78Hz; the rig delivers 10.7Hz.**
  That is the HX711 at its default 10 SPS (RATE pin low). It changes every
  derived timing: a median-of-5 spans 465ms, not 64ms, and takes ~280ms to
  cross to a new value. Build the median window as a parameter, not a
  constant. §4.9 has NOT been corrected in the doc — it is still written
  as an assumption, which is what it was.
- **Per-channel noise, 10s uncalibrated, counts stdev:** bin0 789, bin1
  761, bin2 46, bin3 54, **bin4 1993**, bin5 1323, bin6 203, bin7 48.
  Bin 4 is 40x bin 3. Whether that matters in grams is unknown until
  calibration gives counts/gram — that is what §8.3's `noise_counts_rms`
  is for. Worth checking bin 4's wiring at the rig.
- Bins 0, 1, 3, 4 read large negative counts empty, so **inverted cells
  are the ordinary case on this hardware**, not an edge case.

Decided 2026-08-11, do not redo the analysis: **the 80 SPS jumper mod is
deferred, not rejected.** 10 SPS is the mode where the HX711 rejects 50Hz
and 60Hz mains simultaneously, and this table sits beside a hot pot that
may be induction — that rejection is worth more than the latency. 80 SPS
is also noisier per sample, and bin 4 is already the weak channel. If the
lag is visible on the table after M2.4, the first move is **median-of-3,
one line of Python**, not 8 irreversible board mods. Revisit only with
physical observation of the projected surface as evidence.

## HIDDEN LABELS (added 2026-08-11, doc §8.1)
`id` and `class_name` are hidden; `names` is the only thing a diner reads.
The label names a thing that is cheap to photograph and train on
(`soya_chunks`); the display name is the hot pot ingredient it stands in
for ("Fish Ball" / 鱼丸). `names` is **not** a translation of `id` — never
derive one from the other. Fixed the leak this rule existed to prevent:
`core/main.py` did `item.names.get(self.locale, item.id)`, which projected
the training label onto a plate for any item a locale had not translated.
Now `Item.display_name(locale)`, which cannot return `id` or `class_name`,
and `Catalogue.load()` refuses an item with no `en` name so that chain is
total. 12 tests; the call-site guard was confirmed to fail by restoring
the old line and watching a plate read `curly_noodle`.

**OWED — data, not code.** `data/catalogue.json`'s `names` are still
placeholders that simply restate the labels ("Soya Chunks", "Curly
Noodles"). The mechanism is built and tested; the actual mapping from each
of the 8 labels to the hot pot ingredient it represents is the developer's
to fill in, and was explicitly undecided as of 2026-08-11. Editing that
file is the whole job — no code change is needed to go with it.

Next: M2.2, `core/scale.py` — serial thread per §9.5, median-of-5 (as a
parameter, see the rate finding above), staleness, settle detection.
Needs `pyserial` added to python/requirements.txt.

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
  _shutdown(), orphaning every child. Both fixed; `--stop` verified
  end-to-end (6/6 processes exit, pidfile removed, no orphans), and the
  human then confirmed real Ctrl-C in an interactive terminal also
  exits clean — the thing neither of the above could stand in for.

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

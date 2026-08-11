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
Current milestone: **M3 (camera) — all 4 build items are code-complete**
— see the M3 section below. M3.1 (`common/framebus.py`), M3.2
(`camera/main.py` — real V4L2 capture, exposure/WB/focus lock, the shm
writer, the MJPEG server), M3.3 (staff view Live tab — `<img>`+canvas,
§5.4 scaling, the `camera` join message) and M3.4 (developer panel:
capture resolution, actual FPS, frame_id, shm slot, dropped frames) are
all code-complete. Still owed from M3: doc §21's human acceptance test
on the rig (real camera, `kill -9` recovery, camera elevation angle
measurement) — see the M3.4 section for the full list. Next milestone
per the doc's dependency graph: M4 (calibration and dataset capture).
Deferred, not started: demo-video recording (capturing the table video,
room audio, and optionally a Live-tab PIP overlay, for the contest
submission) is designed in `docs/DEMO_RECORDING_PLAN.md`. It's
deliberately out-of-band tooling (`tools/record_demo.py`, not a `run.py`
process) so it doesn't block or get built into M3. Pick it up whenever,
per that doc's §8.
M2.6 (mode) is code-complete (2026-08-11) — see the M2.6 section below.
M2's 5 build items (load cells) are all code-complete; M1, M2 and M2.6
each still owe their human acceptance test on the rig.
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

M2.2 (2026-08-11) is build item 2: `core/scale.py`, the serial thread.
Owns the port and nothing else does — parse, median, staleness, settle,
plus the 2s capture windows M2.1's docstring said would live here.
Lifecycle copies `wire.Client` (start/stop, composition) rather than
§9.5's `threading.Thread` subclass, so every long-running link in the
tree is driven the same way; the doc's snippet is `_read_forever`'s body.
`median_window` is a constructor parameter, never a constant, because of
the 10.7Hz finding above — median-of-5 spans 465ms here. Stale (>0.5s)
reports `counts=None`, which reaches `Calibration.grams_all(None)` and
comes back as eight `None`s: a dead XIAO and an uncalibrated cell reach
pricing by one route, and neither bills. The thread never dies — broad
`except` on read, doc §20.2's 1s→10s reopen ladder — because staleness
is only visible while something is still trying. `pyserial>=3.5` added
to python/requirements.txt, imported inside `_open_serial()` so the
maths stays testable with no XIAO and no pyserial.
Two things worth knowing at the next step: settle anchors each window to
the value it **opened** at, not the previous sample (a 1g-per-sample ramp
never breaks a ±2g step, so previous-sample comparison would photograph
a bin that is still filling), and `capture()` reduces **raw** samples,
not the median-filtered slot — noise measured through the smoother is
the smoother's noise, which is what would hide bin 4.
48 tests, 325 total, all passing. Five mutations checked; the first
version of the capture test was a TRAP that passed by construction
(alternating ±2000 survives a median-of-5 unchanged) and was rewritten
to a one-in-five spike before it could fail.
**Run against the live XIAO on COM5** (2026-08-11, after the commit —
the commit message's "no XIAO attached" line is wrong, correction here).
10s of real serial: 106 samples, 10.3Hz, **bad_lines exactly 1** — the
truncated first line after open that §4.9 predicted, dropped, stream
unaffected. `capture(2.0)` returned 21 samples, the number
MIN_CAPTURE_SAMPLES' comment predicted. Grams were eight `None`
throughout (nothing is calibrated yet) and `settled` eight `False`,
which is the designed behaviour and not a failure: unmeasurable is not
steady. After `stop()`, stale within 0.6s, still eight `None`.
Not observed on the rig: unplug-and-reopen (needs a hand on the cable),
and settle in grams (needs M2.3's calibration).

**Empty-bin counts and noise differ from the 2026-08-11 numbers above.**
Measured today over a 2s window, empty:

    bin        0        1      2       3        4       5       6      7
    counts -286992  163203  1513  -473574  -390799  46181  204281  -3617
    rms       1068    1191     33       40      979    1485      60     48

Two changes from the earlier record, both unexplained, neither
investigated: **bin 1 now reads large POSITIVE** (it was negative), and
**bin 4 is no longer the outlier** (1993 → 979, while bin 3 fell 54 → 40
and bin 5 rose 1323 → 1485). Do not treat either list as settled; take a
fresh capture before trusting a per-channel number.
**Risk for M2.3, flagged not concluded:** bins 0, 1, 4 and 5 sit at
1000-1500 counts rms. *If* counts/gram lands in the hundreds, as §8.3's
example (214.77) suggests, that is 5-7g of noise against a ±2g settle
band — those four bins would never settle. The first real calibration is
what turns that from a risk into a number.

M2.3 (2026-08-11) closes build item 3: `core/calibrator.py`, the seam
where a capture window becomes a saved calibration — `scale.capture()` →
`loadcell_cal.tare/calibrate` → `state/loadcell_cal.json`, and the only
writer of that file. Doc §12.4's two buttons call it. Nothing constructs
one yet; core/main.py is still untouched (that is build item 5, and the
wiring order it needs is in the module docstring).
Five things worth knowing before M2.4 draws the Bins tab:
- **One shared `Calibration`.** The Calibrator takes `reader.cal` and has
  no `cal` parameter, so it cannot calibrate a copy. A copy would save a
  perfectly good file that the live reading never picks up, and nothing
  would look broken until someone weighed a plate by hand.
- **"Bin 3 reads 500 g" is a second measurement**, read live through the
  calibration just saved — never the capture read back through its own
  fit. `(loaded - zero) / cpg` is the reference mass by construction, so
  that version would print "500 g" for a disconnected cell. A TRAP,
  avoided.
- **§12.4 step 1's "Done. Bin 3 reads 0 g." is unavailable on a
  first-ever tare** — an uncalibrated bin has no grams at all — so that
  screen says "Bin 3 is set as empty. Now place a known weight in it and
  tap Calibrate." A re-tare of a calibrated bin does read 0 g, which is
  the case the doc had in mind. **§12.4 has NOT been changed.**
- **Calibrate refuses a bin that was never tared.** The doc orders Tare
  then Calibrate but nothing enforced it, and this is not cosmetic: an
  untared bin has `zero_counts` 0 while an empty cell here sits near
  -287,000, so the fit comes out ~4x too steep, sails through §9.6's
  `abs(cpg) < 10` check, and under-reads every gram taken from that bin
  for the rest of the evening. "Never tared" is the bin still being
  byte-for-byte its first-boot default, not a new §8.3 field.
- **`noise_counts_rms` is taken from the tare capture only** (noise with
  a mass in the bin is the mass settling and the tray rocking, not the
  channel), and a failed *write* rolls the in-memory bin back so memory
  can never sit ahead of disk — that failure bills correctly all evening
  and comes back from a restart weighing against nothing.
19 tests, 344 total, all passing. Six mutations checked red, including
the two that would otherwise have passed by construction: verification
read from the capture, and calibrating a copy of the Calibration.
**Run against the live XIAO on COM5** (2026-08-11): tared all 8 bins, 21
samples per 2s window exactly as predicted, file written and reloaded
identical, and afterwards all 8 bins still uncalibrated with live grams
still eight `None` — a tare alone cannot bill. Calibrate on an empty bin
was refused in §12.4 step 3's words and wrote nothing.
**Empty-bin rms today, a third measurement and different again:** bin0
743, bin1 563, bin2 42, bin3 19, bin4 888, bin5 776, bin6 56, bin7 42
(bin 1 positive again; bin 3 down from 40). The settle risk flagged above
now has a shape: at §8.3's example 214.77 counts/gram those four loud
channels are 2.6-4.1g against a ±2g band. **The real counts/gram is
still unmeasured** — it needs the 500g mass.
Nothing needing a physical mass has been observed. **Still owed from M2:
§21's acceptance test** — 8 empty bins tared to 0 ±2g, bin 5 calibrated
with a known 500g mass reading 500 ±3g, the same on an inverted cell, and
the total checked by arithmetic.
Next: M2.4, build item 4 — the staff view's Bins tab (§12.4): 8 cards,
live grams, the noise indicator, and the one-screen-at-a-time Tare and
Calibrate wizard driving `calibrator.Calibrator`.

M2.4 (2026-08-11) closes build item 4: the Bins tab, both halves.
`core/main.py` now constructs `Calibration`/`ScaleReader`/`Calibrator`
always, at boot — not lazily when a tablet opens the tab — since an
unplugged XIAO is the ordinary boot state scale.py already tolerates.
The tab's grams come straight from `scale.read()`, **not** Cart; that
stays on M1's mock seed until build item 5 wires the two together, so a
bin can legitimately show "—" on the Bins tab while the Live tab still
reflects a mock pick. Wire protocol, all new: core pushes a `bins`
message (8 cards' worth: name, live grams, price/100g, a noise dot bar,
`tared`/`calibrated` flags) at 10Hz — a sixth of `state`'s 60Hz, reusing
that same loop's tick counter rather than a second timer — and a tablet
sends `tare`/`calibrate`, answered with `cal_result`. That reply is a
**broadcast to every tablet, not a direct answer** — `web/server.py`'s
`on_message` hands the callback the decoded frame only, with no
connection handle to answer just the asker, the same limitation
`_on_pip_change` already lives with. The noise dot bar (doc's "●●●●●●○○"
is a mockup, not a formula) is defined here as 8 dots spanning 2x the
settle tolerance, so a cell exactly at doc §9.5's settle boundary — the
number that actually matters — reads exactly half full.

**Found while wiring `Core`, not while building the tab:** this dev
machine's COM5 is a live XIAO right now (`serial.Serial('COM5', ...)`
opens and streams real counts), and `Core.__init__` had started
constructing a real `ScaleReader` against the hardcoded `SCALE_PORT`
default unconditionally. Every existing `Core`-based test — not just the
new ones — was one `core.start()` away from racing genuine hardware
counts against whatever a test fed in through `scale.feed()`. Fixed with
a `scale_open_port` constructor parameter threaded straight to
`ScaleReader`'s own test hook (its docstring: "the numbers in here can
silently mis-bill, so they have to be reachable from a test" — this is
that same argument one layer up), and every `Core`/`start()` call in
test_core_main.py now passes an opener that always raises. `cal_path` is
a matching parameter, defaulting to the real §8.3 file — `CoreCase` now
builds every test Core against a throwaway one, the same discipline
test_calibrator.py already had for a standalone `Calibrator`.
**A second, unrelated fixture bug this uncovered:** an early version of
the Bins tab tests fed an all-zero baseline as "empty" — but
`BinCal()`'s own first-boot default is `zero_counts=0.0`, so a tare
against literal zeros is byte-for-byte indistinguishable from "never
tared" by `BinCal.tared`'s own check, and `calibrate()` refused it as
untared. Not a production bug — no real cell reads exactly 0 counts
empty (CLAUDE.md's own per-channel table) — fixed by feeding §8.3's
example zero_counts (negated) instead of zero.
Also added, small: `math.isfinite()` on an incoming `ref_mass_g` — a NaN
or Infinity survives `ref_mass_g <= 0` (every comparison against NaN is
False) and would otherwise have reached `loadcell_cal.calibrate()` and
been written into `state/loadcell_cal.json`, doc §9.6's one number that
can silently mis-bill. And `BinCal.tared`, a property centralising the
"first-boot default" check calibrator.py already had inline, now shared
by both call sites (`calibrator.py`'s refusal, `main.py`'s card data).
13 new tests (3 for `BinCal.tared`, 10 for the Bins tab end to end over
the real WebSocket — boot shape, tare, calibrate-before-tare refused,
a full tare→calibrate cycle reaching a `bins` broadcast, a bad bin
index, a NaN ref mass), 357 total, all passing.
**Run against the live XIAO on COM5** (2026-08-11, after the fix above):
a `bins` broadcast over a real connection showed all 8 bins correctly
uncalibrated (`grams`/`noise_g`/`noise_dots` all `null`, catalogue names
and ₹/100g prices present), and a real `tare` on bin 0 returned
`ok: true` with the first-tare wording, against a scratch `cal_path` so
the repo's own (currently absent) calibration state was never touched.
`hz` read over 1000 briefly right after open — a burst of buffered
serial lines draining at once, not a real rate; §21's acceptance test
(a real 500g mass) is still owed and untouched by this step.
**Doc gap, named not resolved:** §9.5 and §21 both say "serial pip red"
on a dead XIAO, but §12.2's six header pips (camera·tracker·classifier·
voice·core·table) have no seventh slot for one — the serial link is a
thread inside core, not a process with its own hello/heartbeat, so it
cannot join `health.Registry` the way the other six do. Built instead,
and narrower: a plain-language "Load cells: connected, N Hz" / "no
connection" line at the top of the Bins tab, sourced from `scale.status()`.
Where a header-level indicator belongs is undecided; revisit when build
item 5 makes a dead link a billing-visible fault, not just a Bins-tab one.

M2.5 (2026-08-11) closes build item 5, the last of M2: real grams wired
into pricing. `core/main.py`'s new `_apply_scale_to_cart()` runs every
state tick — any bin `scale.read()` can currently weigh overwrites Cart's
live grams; a bin it cannot (uncalibrated, or the XIAO stale/missing —
`Reading.grams[i]` is `None` for both) is left exactly where the developer
panel's mock controls put it, doc §12.8's "stays forever as a test
harness" being the literal reason nothing was removed.
**The one real design problem this build item had to solve, not just
wire up:** a bin's first real reading arrives while `start_g` is still
wherever the M1 mock seed left it (500g). Running that through the
ordinary `set_live_grams()` would price the gap to a real weight — say
300g — as an instant 200g phantom pick. `cart.seed_live_grams(i, grams)`
is the fix: start_g/live_g/shown_g all snap to the real weight, once,
the first time a bin has one (`Core._scale_baselined`, a bool per bin);
every reading after that is an ordinary `set_live_grams()` call, same as
a mock pick. `cart.py`'s own docstring said "both go through
set_live_grams()" — that line was wrong, written before this problem was
worked through, and is corrected now.
**Also resolved, not deferred:** the doc gap two entries up, which
explicitly asked to be revisited here. `_overlay_msg()` sets
`state.overlay.kind = "error"` when a bin that has crossed into
`_scale_baselined` can no longer be read — never merely "uncalibrated",
which is the ordinary state of the M1 mock-only demo and must not
permanently cover the table in a fault screen. The condition is
per-bin-history, not "is the scale stale right now": a fresh boot with
no XIAO ever attached stays `overlay.kind: "none"` forever, and only a
bin that was genuinely billing from real weight going dark trips it. oF
already parses `overlay.kind` (M1.4's StateLink) but renders nothing for
any kind yet — recap/qr/calibrating/uncalibrated/error are all in the
same unbuilt state; drawing the fault screen itself is oF-renderer work
with no doc-given visual design and is not part of this build item.
9 new tests, 366 total, all passing. Two mutations checked red: routing
the first-reading case through `set_live_grams()` instead of
`seed_live_grams()` failed the two tests that check for a phantom pick
(200g showed up as a false `picked`, not 0); hardcoding `_overlay_msg()`
to always return `none` failed the one test that waits for `"error"`
after a baselined bin goes dark, and left the other three untouched —
each mutation was caught by exactly the tests aimed at it, not by
accident.
**Run against the live XIAO on COM5** (2026-08-11): `scale.status()`
showed `open: True, stale: False, hz: 10.65, bad_lines: 1` (the usual
truncated first line) with nothing calibrated — Cart correctly stayed on
the 500g mock seed and `overlay.kind` stayed `"none"`. Tared and
calibrated bin 0 against a synthetic 200 counts/gram fit (no real mass
was placed — see below) using bin 0's genuine live counts as the zero
point, then sampled `_state_msg()` five times over ~1s of real serial
noise: `grams` sat at 0-1, `picked` stayed 0 throughout, no crash — the
baseline-then-track path holds up against actual per-channel noise, not
just clean synthetic samples. A real two-point calibration was also
attempted with an honest `ref_mass_g=100` and no mass actually added
between tare and calibrate; `loadcell_cal.calibrate()` correctly refused
it (`abs(cpg) < 10`) rather than accepting a fit computed from noise —
the sanity check working as intended, not a bug.
**Not observed: the physical acceptance scenario itself** — a known mass
placed in a calibrated bin, then removed, with the total rising by the
correct amount on the table. That needs a hand and a scale weight, same
class of gap as M2.1-M2.4's "needs a hand on the cable" notes.
**M2 build items 1-5 are all code-complete.** Still owed from M2: §21's
human acceptance test on the physical rig — 8 empty bins tared to 0 ±2g,
bin 5 calibrated with a known 500g mass reading 500 ±3g, the same on an
inverted cell, ~100g removed from a calibrated bin with the total
checked by arithmetic, and unplugging the XIAO showing a fault overlay
with no further billing. Next milestone per the doc's dependency graph:
M3, the camera process (depends on M0, not M2 — can run in parallel).

**2026-08-11, drew the fault overlay** — the last thing blocking §21's
acceptance test from being runnable at all. `overlay.kind` was already on
the wire and already parsed (`StateLink::overlayKind`), but M2.5's own
notes said outright that oF "renders nothing for any kind yet"; bullet 5's
"table shows a fault overlay" had no visual to check. The doc gives no
design for `error` — no colour, layout, or text, unlike e.g. `recap`
("line items flying in, odometer total") — and this would be the first
overlay kind ever rendered, so it sets precedent for `uncalibrated`/
`calibrating`/`recap`/`qr` too. Proposed a design before writing any oF
code rather than guessing: `UiLayer::drawErrorOverlay()` is a 72px banner
strip along the top edge, reusing doc §14.5's own pattern for a
persistent whole-table state ("STAFF MODE" there) rather than inventing a
second mechanism, filled with the staff view's own fault colour
(`index.html`'s `--red` #e05d5d, ink #2a0000 — the same dark-red-on-red
pairing its red pip already uses) so the failure reads the same on both
surfaces. Text is "SCALES OFFLINE — NOT BILLING," English only (matches
UiLayer's current scope), drawn with the already-loaded 36px bold font —
no new atlas. Bins and the total keep drawing underneath, frozen, same
as §13.3's existing rule for a dead core link ("does not black out — a
frozen table is far better... than a dead one") extended to a dead scale
link. Sits clear of the nearest cutout (far row starts at mmToPxY(177mm)
=~ 209px) and would be safe even if that changed — Stage's light pass
runs after UiLayer and re-stamps every cutout white regardless (§13.2).
Only `error` is wired; the other four kinds remain unbuilt, same as
before.
Builds clean (msbuild, Debug x64, 0 errors). **Not yet observed**: this
has not been run against a live core with `overlay.kind` actually forced
to `"error"`, and the banner has not been seen on the projected surface —
that observation is §21's own remaining step, now unblocked rather than
impossible.

**2026-08-11, calibration weight entry changed from keypad to text field.**
Doc §12.4 specified a "big keypad" (12 on-screen digit buttons) for the
Calibrate step's reference mass. Replaced with a standard `<input
type="number">` in `index.html`'s wizard, prefilled with
`DEFAULT_REF_MASS_G` and auto-selected on open so typing overtypes it;
Enter submits, same as tapping Confirm. Reasoning: the staff view runs on
a tablet reachable by a physical keyboard, and typing digits is faster
and less error-prone than tapping a 12-button on-screen pad one digit at
a time. `calibrator.py`'s `DEFAULT_REF_MASS_G` and the sanity check in
`loadcell_cal.calibrate()` are unchanged — only the entry widget moved.
Doc §12.4 updated to match. No test changes needed; nothing tested the
keypad's markup specifically.

## M2.6 — MODE (SERVING / SETTING). CODE-COMPLETE 2026-08-11.
Built in four commits (M2.6a-d) from `docs/M2.6_MODE_PLAN.md`, which is
now a design record rather than a to-do. The doc never gave the mode its
own milestone — `fsm.py` scheduled STAFF for "M2 and M7" and M2's build
items never mentioned it — so the state that gates all billing was due
five milestones after the first thing that needed it. All three symptoms
are gone: `_state_msg()` derives `mode` from `fsm.state` instead of
hardcoding `"diner"`, the `Core._calibrating` freeze is deleted, and
`binmap.locked` finally has its writer.
The modes are **SERVING** and **SETTING** — the old names named who was
standing at the table, which is the wrong noun (staff are present in
both), and "staff mode" collided with "staff view", the tablet UI used in
both. **"staff view", the §12.6 Setup tab and `fsm.staff_start()` all keep
their names** — `staff_start()` means a *person* pressed Start, and the
rename is what makes that unambiguous. Do not re-litigate any of this.

**THE TRAP, and it bills wrong silently — read before touching
`fsm.exit_setting()`.** `_apply_scale_to_cart()` returns immediately in
SETTING, so at exit `live_g` still holds the weights from when the mode
was *entered*, and `reset_session()` does `start_g[i] = live_g[i]`. Swap a
tray during setting mode — the entire point of the mode — and exit
baselines to the tray that left, billing the next diner for the swap.
Exit refreshes every bin from the scale **before** `reset_session()`, then
locks the bin map; all three steps are inside `exit_setting()` so no
caller can do two of them and forget the third.

Five things worth knowing before M3, four of them the plan did not
anticipate:
- **The step ORDER is not observable in the outcome, only the omission
  is.** `_refresh_weights_from_scale()` uses `seed_live_grams()`, which
  sets `start_g` itself, so a refresh running *after* `reset_session()`
  lands on identical numbers — every outcome assertion passes with the
  two lines swapped. That is luck, not design: swap the callback to the
  ordinary `set_live_grams()` (a small, plausible edit — it is what every
  other weight update in the codebase uses) and a refresh running second
  prices the whole tray. The order is pinned by call sequence in its own
  test, the only test in the suite that catches the swap.
- **`fsm.cancel()` alone cannot clear the cart today**, so "Cancel the
  order first" would have been a button that cannot fix the refusal it is
  offered for. `cancel()` is SELECTING -> IDLE and *nothing yet drives
  IDLE -> SELECTING* — `hand_present()` is M5's tracker, `staff_start()`
  has no button. A diner picking 50 g leaves the cart active with the FSM
  in IDLE. `_handle_cancel_order` calls doc §9.1's own shared
  `reset_session()` when `cancel()` no-ops on an active cart; that
  fallback becomes unreachable once M5 lands.
- **`cart.is_active()` reads `shown_g`, never `removed_grams()`.** The raw
  number moves with load-cell noise (this file's own per-channel table:
  four bins at 500-1500 counts rms), which would hold the refusal true
  permanently and make setting mode **unreachable on the rig**. Accepted
  cost: a sub-deadband pick survives entry and is discarded by exit's
  re-baseline — about ₹0.60, and invisible on the table.
- **The `mode` reply is a broadcast to every tablet**, so a refusal would
  have popped a modal on tablets nobody was holding. Only the tablet with
  a `set_mode` in flight opens the dialog; all of them track the state.
- **Banner precedence is now a general rule in §14.5, not a special
  case:** the state that changes what the table is DOING outranks a fault
  report from a subsystem that state has already disabled. So SETTING wins
  over `error` — nothing bills in setting mode, so "SCALES OFFLINE — NOT
  BILLING" would warn about a risk that cannot occur while displacing the
  message that is true. `calibrating` (M4) and `recap`/`qr` (M6) land on
  the same strip and are settled by the same rule.

Deleted, and the deletion is the signal the mode was the right
abstraction: `Core._calibrating`, `CAL_FREEZE_TIMEOUT_S`,
`_handle_cal_session`, and the `cal_begin`/`cal_end` wire messages at both
ends. They were M2.4's per-bin stand-in for a mode-wide "not billing"
state, complete with their own dropped-tablet timeout.

405 tests pass. 12 mutations checked red, each caught only by the tests
aimed at it: the weight refresh deleted (at both the Fsm and the Core
level — caught by the trap test alone); the refresh moved after
`reset_session()`; `is_active()` on `removed_grams()`; the `locked` write
dropped; `reset_session()` dropped; `can_enter_setting()` never refusing;
the SETTING gate dropped from `_apply_scale_to_cart()` and from
`_handle_cal()`; `mode` hardcoded back; the on-change check dropped;
`on_join` back to one message; `on_join`'s list sent as one frame.

**Found while running the suite, pre-existing and NOT fixed:**
`test_calibrator.TestTheVerificationReading` `.test_a_stale_link_reports_
no_number_but_still_saves` is flaky, roughly 1 run in 12. Reproduced at
the same rate on `788ed9e` (pre-M2.6), so it is not caused by this
milestone. A green suite here is not quite the guarantee it looks like —
worth fixing before it hides something real.

The staff view was verified without adding a browser toolchain to a repo
that has none: `node --check` on the extracted script, every
`getElementById` target cross-checked against the DOM, the page fetched
over real HTTP from a live Core, and the whole IIFE driven against a
throwaway DOM shim over 22 assertions (the pre-warn, the refusal, the
not-mine-refusal case, confirm-then-cancel, keep-the-order sending
nothing, and both button and chip states). The shim is scratchpad-only
and not committed.

**STILL OWED — doc §21's M2.6 acceptance test on the rig, in full.
Nothing in this list has been observed; all of it ran only in tests or a
framebuffer.** Somebody has to physically swap a tray:
- `ENTER SETTING MODE` with an empty cart → chip amber, amber banner on
  the table, unmistakable from three metres.
- Pick ~50 g, tap `ENTER SETTING MODE` → refused, readable reason, and a
  "Cancel the order first" that actually clears the way.
- In setting mode, lift a whole tray out and put a different one back →
  the total does not move and no pick registers.
- Exit → **the total is 0 and stays 0.** This is the trap. A large phantom
  pick here means the weight refresh is missing.
- After exit, `state/bin_map.json` has `"locked": true`.
- Tare and Calibrate unreachable in serving mode, working in setting mode.
- Unplug the XIAO while in setting mode → the banner still reads
  `SETTING — NOT BILLING`, not the scales-offline one. **The precedence
  rule has never been seen to fire.**
The banner has also never been drawn on the projected surface at all —
the same gap as M2's fault overlay, which is still owed too.

**Chinese strings for both modes remain undecided and must not be
invented** — `zh` locale data does not exist and §17.3 says Chinese judges
will read them. §14.5 now carries that as an explicit note, and the banner
is English-only until a native speaker confirms both.

**Two doc phrases the plan's §4 vocabulary map did not cover**, left alone
deliberately rather than swept: "the staff grid" (§12.7's 100 mm
calibration grid) and "staff scanning" (§20's failure table). Both now
describe setting-mode activities in the old vocabulary. Change them only
as a deliberate decision, never as a find/replace — the map was applied
literally on purpose, because a broad sweep on "staff" is exactly what
would have destroyed "staff view".

### M2.6e — seven fixes from the first run on the dev machine
**2026-08-11, and the first six are all things only running it showed.**
The milestone was code-complete and fully tested before any of these were
visible, which is the point: none of them is a logic bug and every one of
them made the thing worse to use.

1. **The banner said `SETTING — NOT BILLING`, which is jargon aimed at
   nobody who reads it.** The table is the one surface with no operator
   filter on it — a diner is looking at it. Both banners now lead with
   the same headline, `NOT SERVING`, with the state as a subline
   (`setting the table` / `scales offline`) and the hue carrying the rest
   (I8). The headline is the only part a diner needs and is equally true
   of both.
2. **The banner covered the far row's item names.** §14.5 said "a
   persistent banner strip along the top edge" and that is what was
   built, 72px full width. But the far row's labels are drawn *upward*
   into the 177mm far margin and a two-line wrapped name — which several
   catalogue names are, at 36px in a 200mm box — puts ink as high as
   ~50px. **Staff have to read those names to confirm which tray is
   which, during setting mode above all, which is exactly when the
   banner is up.** It defeated the mode it was announcing. The panel now
   sits in the centre column (the pot gap, between bin 1's right edge
   and bin 2's left edge) — the one horizontal span on the table with no
   bin and no label in it, by construction, derived from the bin rects
   rather than hardcoded. Narrower, so taller and two-line; ~440x88mm of
   amber is still unmissable from three metres, and the strip shape was
   only ever one way to get there. **§14.5 has been corrected.**
3. **Tare/Calibrate refused only after Confirm** — i.e. after the
   operator had emptied the bin, opened the wizard and tapped through.
   The answer arrived at the last possible moment, having wasted the one
   step that takes physical work. Both buttons are now disabled outside
   setting mode with the reason on the card and in a `title`. Core still
   refuses independently: the rule about what is safe to do to a bin
   belongs on the side that owns the cart, and a stale page must not be
   able to tare a serving table — but no operator should ever see it now.
4. **The system had two words for one idea.** The banner said "billing"
   while the mode was called "serving". One word now, "serving", and it
   is the mode's own name — banner, refusals, hints. `NOT_IN_SETTING_MSG`
   is the single source for the refusal sentence. "Billing" survives only
   in code comments about the cart, where it is the accurate word.
5. **Tare had no bulk option and is the one step that can have one.**
   Setting the table means eight empty bins at once; taring them singly
   is eight trips through a wizard whose whole content is "the bin is
   empty". `Tare all 8 bins` does every bin from **one** capture window —
   `scale.capture()` already reduces all eight channels over the same
   window — so 2s rather than 8x2s, and, more than the speed, every
   bin's zero comes from the same instant, so a board-wide drift lands
   on all of them identically instead of smeared over sixteen seconds.
   Each bin still takes its own zero out of that window; a shared one
   would mis-weigh seven bins. Saved once, all eight rolled back together
   if the write fails. **Calibrate cannot have a bulk form** — each bin
   needs its own reference mass physically in it.
6. **Mode status was top-left, its toggle bottom-left.** Two unrelated
   things, with nothing saying which control drives the state you are
   reading. The toggle moved up beside the chip. Keeping a consequential
   toggle out of the bottom thumb-rest also makes it harder to mis-tap —
   the same concern that ruled out a two-position switch in the first
   place. `Cancel order` stayed in the action bar, which is now cleanly
   order-scoped while the header is system-scoped. **§12.2's mockup has
   been corrected**, and it is the second thing in this list that the
   mockup got wrong and the table showed.

413 tests (8 new). 5 more mutations checked red: `tare_all` taring only
bin 0; `tare_all` reimplemented as 8 separate captures (caught by the
one-window test, and it ran 8s slower); the bulk rollback dropped;
"billing" restored in the refusal; `tare_all`'s setting-mode gate
dropped. The staff-view shim harness grew to 44 assertions covering the
gating, the disabled-tap doing nothing, and the whole bulk-tare flow.
Builds clean, 0 errors and 0 warnings from `hotpot-table/src`.

**Still owed, unchanged and now larger:** doc §21's M2.6 acceptance test
on the rig. **None of the six fixes above has been seen on the projected
surface either** — in particular the banner's new position and wording
have only ever been reasoned about from the label geometry, never
looked at. The one measurement that would settle #2 is a photograph of
the far row with a two-line name and the banner up.

### M2.6f — staff-view chrome, from looking at it on the tablet
**2026-08-11.** Not logic, not billing — the same class as M2.6e: things
only visible once a person was reading the page.
- **The mode was said twice, side by side.** M2.6e's item 6 put the chip
  and its button adjacent, and adjacent is exactly where the redundancy
  showed: a chip reading `Setting` next to a button reading `EXIT SETTING
  MODE`, with only the chip coloured. Both are gone, replaced by **one
  slide switch** — left half SETTING, right half SERVING, knob on the
  half the table is in. §12.1's "one primary action per screen" argued
  for a button over a switch and the mis-tap worry behind that is real,
  so **the two things that made it safe are unchanged**: entry is still
  refused with a reason while an order is live, and exit still refreshes
  every bin from the scale before `reset_session()`. This is a control
  change only, no protocol or FSM change.
- **Both modes now have a hue; neither is green or red.** Setting keeps
  **amber** because the table's own banner is amber and I8 says both
  surfaces say the same thing in the same hue — that one was not ours to
  re-pick. Serving is **teal** (`--serving` #2fb0b8), deliberately not the
  pips' green: a mode is not a health state, and green/red would make
  serving read as a passing check and setting as a fault. Colour is still
  never alone — the knob's position and the lit label both say it.
- **The second strip of tabs is gone.** Live/Bins ride the top bar beside
  the brand, where every site the operator already uses puts them.
- **The six process pips are developer-only now**, riding the existing
  Developer toggle with the mock panel. An operator cannot act on
  "tracker is amber", and six coloured dots in the corner of every screen
  train them to ignore colour in the one UI where colour carries the mode.

Verified the same way M2.6 was — `node --check` on the extracted script,
every `getElementById` target cross-checked against the DOM (a script,
not by eye), 413 tests still passing. **Not observed on the tablet yet.**

### M2.6g — the plate label: smaller, and symmetric about the pot
**2026-08-11.**
- **The near row and the far row drew the same two rows of text in
  opposite orders.** Far row was price-then-name reading outward; near row
  was name-then-price. Both now read **ring → price/grams → name** going
  outward from the pot, so the two halves mirror each other the way the
  bins do. It is a mirror of the far row, not a copy of its
  top-to-bottom order.
- **Doc §13.4's 36px/26px were a guess made before any name had been
  measured in a bin, and it was wrong.** At 36px DejaVuSans-Bold only
  **3 of the 8 catalogue names fit on one line** inside a bin's own 200mm
  (252px) footprint, so five plates carried a two-line name and the label
  block ran ~136px into a 209px margin. Now **28px/22px**: 7 of 8 fit on
  one line, block ~112px, cap height still ~17mm on the plywood. `kNamePx`
  / `kDetailPx`, one edit. **§13.4 needs correcting to match — not done.**
- **The face itself is still DejaVuSans-Bold and is still the
  placeholder.** Measured, not guessed: DejaVu is the **widest** of every
  face tested, which is most of why names wrapped at all. Candidates
  measured for ₹ (U+20B9) coverage and one-line fit: Inter Bold (§13.4's
  own choice, 7/8), Poppins Bold (7/8), Baloo 2 Bold (8/8 — narrowest with
  a rupee). **Fredoka and Barlow Semi Condensed are ruled out: neither has
  a ₹ glyph**, which is the bug M1's first rig run already hit once. The
  choice is the developer's and is open.

**Builds clean** (msbuild, Debug x64, 0 errors, 0 warnings from
`hotpot-table/src`) — linked once the demo holding the exe was gone.
Nothing here has been seen on the projected surface, including whether
28px is still comfortable at three metres, which only a photograph
settles.

**OPEN, decided by the developer, not by code:** the project needs a
name and a logo (it is what the contest announces), and the brand block
in the header is built as a logo slot + wordmark so both drop in with one
edit. `Hot Pot` and a placeholder mark are in there meanwhile.

**Not fixed, and it is a real complaint:** the live weight readout
jitters. Options are written up in the session notes rather than here
because none has been chosen; the short version is that the display
deadband (I5) only suppresses *drift below 10g*, and nothing yet
suppresses the 1-2g rms wobble on four of the eight channels
(`noise_counts_rms` is measured and stored but is used only to colour the
Bins tab's dot bar). The candidates are a median-of-3 on top of the
existing median window, a settle-gated display that freezes the shown
number until `scale`'s own settle detector says the bin is steady, and
quantising the displayed grams to 5g. **The settle detector already
exists and is already computed per bin, and is currently used by
nothing** — that is the cheapest place to start.

## M3 — CAMERA (in progress)

M3.1 (2026-08-11) is build item 1: `common/framebus.py`, doc §6's
shared-memory frame ring — writer and reader, seqlock, staleness. No
camera yet (that is build item 2); this is the transport alone, and it
is deliberately testable with neither a camera nor a second process
attached, the same discipline `core/scale.py` uses for the XIAO:
`FrameWriter`/`FrameReader` share a ring inside one test process.
`core` never imports this module — I3 ("core never touches a frame") is
enforced by that omission, the same way I2 keeps pricing out of `of/`.
Layout, magic/version, and both the write and read algorithms are byte-
for-byte doc §6.1–§6.4: pixels, then the slot header, then
`write_counter`, in that order, because `write_counter` becoming visible
is the definition of "published"; a reader reads `frame_id` before and
after copying pixels and retries on a mismatch (a torn read — the writer
lapped it), giving up after `max_retries` rather than spinning forever.
`FrameReader._torn_read_hook` is a testing seam, not a production
feature — called between the copy and the second `frame_id` check, so a
test can force a real interleaved write (`slot_count=1` guarantees the
collision) rather than trust the retry path works. §6.4's staleness is
`is_stale()`/`peek_ts_ns()`, the latter reading only the slot header so
a consumer can poll liveness every tick without copying a full frame.
18 new tests, 431 total, all passing. Three mutations checked red:
dropping the `write_counter` publish (a write that "completes" but is
never visible — no reader would ever see it), collapsing the retry loop
to return on the first attempt regardless of tearing (the exact TRAP
doc §5.3 warns about generally — a check that cannot fail proves
nothing, and this is the one place in the module where that trap was
buildable), and flipping `is_stale`'s comparison direction.
**Not run against a real camera** — nothing to run it against until
build item 2. `python/requirements.txt` untouched: this module needs
nothing beyond the standard library.
Next: M3 build item 2, `camera/main.py` — V4L2 open, MJPG format
preference (§6.6), exposure/WB/focus lock, the shm writer, and the MJPEG
HTTP server. This is also where the AVX2/board risk (CLAUDE.md's own
"NO AVX2" line) first becomes reachable in code, and where the
never-measured camera elevation angle (I10) has to be measured before M4.

M3.2 (2026-08-11) is build item 2. Four new files: `common/config.py` plus
committed `config/system.default.json` (doc §8.6's exact schema) — deferred
since M0's stub.py, and M3.2 is the first reader that needs more than one
key; `camera/capture.py` — the `Capture` backend split (`V4L2Capture` /
`FakeCapture`, doc §19.4's "backend abstraction — mandatory" discipline
applied to camera, the same reason `classifier`/`voice` have
`backend_ei.py`/`backend_stub.py`); `camera/mjpeg.py` — a plain
`http.server.ThreadingHTTPServer`, no new dependency, serving
`/stream.mjpg` (multipart, push-not-poll via a `Condition`-backed
`LatestFrame`), `/snapshot.jpg`, `/info.json`; and `camera/main.py`
rewritten around `CameraProcess`, replacing the M0 stub.
New dependency: `opencv-python-headless` (VideoCapture + imencode/resize;
the "no AVX2" risk is about ML inference, not plain JPEG codec paths, so
this doesn't carry it — see `capture.py`'s docstring).
Three decisions worth not re-deriving: (1) **readiness fires later than
`stub.py`'s did** — `run.py`'s own tier comment says tier 1 "creates the
frame ring and serves MJPEG", so `camera/main.py` builds its own
`wire.Client`/`health.Heartbeat` instead of calling `stub.start()`, which
bakes `log.ready()` into client-start time. (2) **the capture loop runs on
the main thread, not a daemon one** — doc §20.1's table has camera's
restart "recreate shm, consumers re-attach", which is a process restart;
30 consecutive failed reads raises `CameraError` out of `run_forever()`
rather than looping, and only the main thread makes that reach `run.py`'s
supervisor instead of dying quietly in `log.py`'s `threading.excepthook`.
(3) **exposure/WB/focus lock has two paths** — if `state/camera_settings.json`
already holds values (a prior rig sweep, doc §6.6), they're applied
verbatim; on a first run with nothing recorded, auto-exposure/WB/focus are
left on for `AUTO_SETTLE_S` (1.5s, unmeasured) then locked at whatever they
converged to, and *that* becomes the recorded baseline — never a number
invented in code. `config.of.field_level` is mirrored into the same file
per §6.6. All control locking goes through `v4l2-ctl` (shelled out, the
same tool doc §6.6 names for format enumeration), not OpenCV's own V4L2
property mapping, which is inconsistent across drivers.
40 new tests (`test_config.py`, `test_camera_capture.py` — control-locking
logic with `subprocess.run` faked, `test_camera_mjpeg.py` — a real
`MjpegServer` hit with real HTTP, `test_camera_main.py` — `CameraProcess`
end to end against `FakeCapture` and a real `wire.Server`, `test_stub.py`-
style), 471 total, all passing.
**Not run against a real camera** — `V4L2Capture` is unverified against
hardware, the same honest gap M3.1 left for the ring it now feeds. Two
physical items from doc §21 remain open regardless of code: the format
enumeration log (`v4l2-ctl --list-formats-ext`) has never seen a real
device, and the camera elevation angle (I10) is still unmeasured.
Build items 3 (staff view Live tab) and 4 (developer panel) are next.

M3.3 (2026-08-11) closes build item 3: the staff view's Live tab, doc
§12.3. `core/web/static/index.html`'s placeholder slot from M1.5 is now a
real `<img id="liveImg">` over `/stream.mjpg` plus a `<canvas
id="liveOverlay">` sized to `naturalWidth × naturalHeight` and CSS-scaled
identically (doc §5.4) — the rule build item 3 asked to have implemented
even though nothing is drawn on the canvas yet, since no bin rects (M4),
hand cursor (M5), or dot pattern (M4) exist on the wire. `drawOverlay()`
exists and is wired to the five toggle chips (now clickable and
localStorage-persisted, matching the developer panel's own precedent) but
currently only clears the canvas — one place for M4/M5 to add real
drawing rather than a second path.
**Core learned where the camera's MJPEG server lives**, which it did not
know before: `core/main.py` gained a `camera` join message (`_camera_msg()`,
third in `_join_msgs()` after `pips`/`mode`) carrying `host`/`port`, sourced
from doc §8.6's `camera.host_for_browser`/`mjpeg_port`. Camera and core are
separate processes with separate HTTP listeners (M3.2's own design — core
never touches a frame, I3), so the tablet has to be told the URL rather than
core proxying it. `main()` is the one place that calls `config.load()` for
this — the second real reader after `camera/main.py`, following that
module's own comment that config loading waits for a reader needing more
than one key. `Core.__init__` takes `camera_host`/`camera_port` as
constructor parameters defaulting to the same values as `config.py`'s
committed default (localhost:8081), the same split `cal_path`/
`scale_open_port` already use so a test-built Core never touches
`config/system.json`.
The `<img>` retries on its own (`onerror` -> 3s -> re-fetch with a
cache-busting query string) since a dead MJPEG multipart connection does
not reliably fire a DOM event a browser will act on by itself — this is
what doc §21's "the feed resumes automatically after restart" needs, on
top of the red pip the health link already gives.
2 new Python tests (`TestCameraJoinMessage`), 473 total, all passing;
`test_a_joining_tablet_is_told_the_mode_as_well_as_the_pips` now drains and
checks all three join messages instead of two. The staff view itself was
verified the same way M2.6 was: `node --check` on the extracted script,
every `getElementById`/`data-toggle` target cross-checked against the DOM
by script, and the whole IIFE driven against a throwaway DOM shim (not
committed) that clicked a toggle chip and confirmed it persisted and
redrew, fed a fake `naturalWidth`/`naturalHeight` `load` event and
confirmed the canvas sized to match, and pushed a `camera` message through
a stubbed WebSocket and confirmed `<img>.src` came out
`http://odyssey.local:8081/stream.mjpg?t=...`.
**Not observed: a real browser against a real camera stream.** Nothing here
has been opened in an actual browser tab, and `V4L2Capture` is still
unverified against hardware (M3.2's own gap, unchanged). The `kill -9`
camera / feed-resumes-automatically half of doc §21's M3 acceptance test,
and the camera elevation-angle measurement, are both still owed and
untouched by this step. Developer panel (capture resolution, actual FPS,
frame_id, shm slot — build item 4) is next; `/info.json` already returns
all four fields (M3.2), so that build item is wiring, not new data.

M3.4 (2026-08-11) closes build item 4, the last of M3: the developer
panel's Camera section, doc §12.8's "capture resolution, actual FPS,
frame_id, shm slot in use, dropped frames." Wiring, not new data, as
M3.3 predicted — `/info.json` already had everything except a name for
"dropped frames"; that one is `read_failures` (a capture read that did
not produce a frame), reusing `camera/main.py`'s own field name on the
wire rather than inventing a second one for the same number.
**Fetched straight from the camera process's own `/info.json`**, the same
split the Live tab's `<img>` already uses (I3, "core never touches a
frame") — core has no reason to proxy numbers it doesn't own, and
`applyCamera()` already had the host/port from the `camera` join message,
so building the second URL alongside `cameraUrl` was the natural spot.
**This crosses an origin `<img>` never had to**, and it is a real browser
rule, not a style choice: an `<img src>` load is CORS-exempt, but a
`fetch()` from the staff view's own origin (core's :8090) to camera's
:8081 is a genuine cross-origin request, and a browser drops the response
before JS ever sees it without `Access-Control-Allow-Origin`.
`camera/mjpeg.py`'s `_info()` now sends a wildcard — no credentials flow
through this endpoint, so a wildcard costs nothing and is simpler than
echoing the request origin. `/stream.mjpg` and `/snapshot.jpg` are
untouched; neither is ever reached with `fetch()`.
Polled every 1s, and **only while the panel is open** — `setDevOpen()`
now starts/stops the interval alongside the pips it already toggles,
since nobody reads these numbers with the panel closed and there is no
reason to keep hitting camera's HTTP server for a page nobody has open.
A failed fetch (camera down, CORS misconfigured, network blip) paints
"camera unreachable" across all five fields rather than leaving stale
numbers on screen or throwing.
No Python behaviour changed beyond the one CORS header; the wiring itself
is browser JS with no server round-trip through core. Verified the same
way M2.6/M3.3 were: `node --check` on the extracted script, every
`getElementById` target (five new: `camRes`/`camFps`/`camFrameId`/
`camShmSlot`/`camDropped`) cross-checked against the DOM by script. One
new Python test, `TestInfo.test_allows_cross_origin_fetch`, checking the
header directly over real HTTP — the same `ServerCase` real-socket
approach the rest of `test_camera_mjpeg.py` uses, not a mock that would
pass regardless of whether the header was ever sent. 474 tests, all
passing.
**Not observed: the panel against a real camera process or a real
browser.** Nothing here has been opened in an actual browser tab, the
poll-only-while-open behaviour has only been read, not watched start and
stop, and "camera unreachable" has never been triggered by an actual dead
link — only reasoned through from the `fetch().catch()` path. Same class
of gap as M3.2/M3.3's unverified-against-hardware notes.
**M3 build items 1-4 are all code-complete.** Still owed from M3: doc
§21's human acceptance test on the physical rig in full — the live feed
at configured resolution/rate, `kill -9` camera showing red pips and a
stalled banner within 1s then resuming automatically, and the camera
elevation angle (I10) measured and written into this file's NUMBERS OWED
section. Next milestone per the doc's dependency graph: M4 (calibration
and dataset capture), which depends on M1 (done) and M3 (now
code-complete).

**2026-08-12, added `capture.py`'s `WindowsCapture` and ran the pipeline
against a real webcam — not a doc build item.** The dev machine is
Windows; `V4L2Capture` (Linux/`v4l2-ctl`-only, by design) cannot open a
device here at all, which is what M3.2/M3.3's "not run against real
camera hardware" notes above were always going to mean until the rig was
available. Rather than wait, `WindowsCapture` — OpenCV's DirectShow
backend, addressed by device index instead of a `/dev/videoN` path — gives
this machine a *real* capture backend alongside `FakeCapture`, chosen
automatically by `sys.platform` in a new `camera/main._build_capture()`
(doc §8.6 gained one optional, rig-irrelevant config key,
`camera.windows_device_index`, documented there as exactly that).
**Exposure/WB/focus locking is best-effort here, explicitly not the
`v4l2-ctl` guarantee** — no equivalent tool exists on Windows, so
`WindowsCapture` uses OpenCV's own inconsistent `CAP_PROP_*` mapping and
reads back whatever the driver actually reports after every `set()`,
never the number asked for; an unsupported control reads `None`
(`_readback()` also treats OpenCV's own 0/-1 "unsupported" sentinels as
`None`, not a fabricated real reading). **Do not point M4's dataset
capture at this backend and trust its recorded exposure the way §6.6
trusts `V4L2Capture`'s** — say so in the class docstring too.
**Run for real, on this machine, against the actual physical rig table**:
`WindowsCapture` opened index 0 at 1920x1080@30fps (fourcc negotiated
YUY2, not the requested MJPG — OpenCV decodes to BGR internally
regardless, so this doesn't block anything downstream), locked-and-read-
back `exposure=None, wb=6500, focus=None` (this webcam's driver honestly
doesn't expose exposure or focus controls to DirectShow — not a bug),
and a frame read back exactly `width*height*3` bytes. Wired into the full
`CameraProcess` + real `core/main.py`, `/info.json` and `/snapshot.jpg`
both hit over real HTTP, and the snapshot was a genuine JPEG of the
physical table — 8 bins, load-cell wiring, visible in frame. **This is
real evidence the framebus/mjpeg/dev-panel pipeline (M3.1-M3.4) works
against actual camera hardware**, not synthetic frames — but it is
DirectShow evidence, not V4L2 evidence: format enumeration
(`v4l2-ctl --list-formats-ext`), the Linux open path, and MJPG
negotiation on the actual driver remain unverified and are still owed on
the rig, unchanged from M3.2's own gap.
12 new tests (`TestWindowsCaptureOpen`, `TestWindowsCaptureLockControls`
— a fake `cv2.VideoCapture` object, same seam `subprocess.run`-faking
gives `V4L2Capture`'s tests, real `cv2` constants since opencv-python-
headless is already a hard dependency; `TestBuildCapture` — the platform
branch, with `sys.platform` patched). 486 total, all passing.
Also fixed while wiring this in: `_write_camera_settings()` was reading
`cam_cfg.get("device")` unconditionally, which would have written
`"/dev/video0"` into `state/camera_settings.json` on a Windows run that
never touches that value — now reads `self._cap.device`, the backend's
own real identifier (a V4L2 path or a DirectShow index), falling back to
config only for a backend (`FakeCapture`) that has no `.device` at all.

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

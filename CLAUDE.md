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
Next: M2.5, build item 5 — wire real grams into pricing (Cart reads
`scale.read()` each tick instead of the M1 mock seed), with the mock
pick/put-back controls gated behind the developer panel rather than
removed.

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

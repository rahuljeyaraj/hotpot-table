# HOTPOT TABLE

Interactive weigh-by-weight hot pot ingredient counter.
Seeed "Make a Sign" Interactive Signage Contest 2026.
Product name: Hot Pot (en) / 称重火锅 (zh).

## GROUND TRUTH
Read docs/HOTPOT_ARCHITECTURE_v3.md before doing anything.
It is authoritative. This file is only status + rules.

**Open feedback queue:** `docs/RIG_FEEDBACK_2026-08-12.md` — eleven items
from the first real M5 rig run, each scoped for one fresh session. Items
1, 2, 8, 9, 11 are resolved (2 by a workaround, not code). **Item 11
(pointer lags/snaps on a fast hand move): DONE, rig-confirmed 2026-08-13.**
`tracker.max_hands` is `1` on this rig (two-hand tracking measured
unstable, disabled the same day) — `tracking.py`'s old doc-11.3 two-hand
role/match/hysteresis machinery (three separate fixes to it, each real,
none sufficient) was deleted outright, developer's call, because it was
answering "which of two hands is this" on a rig that only ever has one.
`tracking.py` is now a ~140-line single-hand smoothing filter, no
identity matching, no role assignment — developer watched the projected
table on a fast move and confirmed it fixed. The RIG_FEEDBACK item 11
diagnostic (the raw-skeleton overlay, `skeletonbus.py`/`SkeletonLink`)
is disabled the same session, not deleted — `ofApp.cpp`'s `kDrawSkeleton`
kill switch and `tracker/main.py` simply not calling
`skeleton_sender.send()` any more, both one-line re-enables. **Read item
11's own section before touching any of this again — it has the full
reasoning and what was ruled out along the way.**
**Items 4-7 (the Done/Cancel/Language widgets) and 10 (devToggle/devPanel
folded into the Developer tab) are built (2026-08-13), neither yet
rig/browser-confirmed.** `core/hover.py`'s `widgets_for()` now always
returns no widgets — the three were the developer's own placeholders
("all these buttons are not expected to be here"); `layout()`/`Widget`/
`DwellTracker`/`_fire_widget`'s dispatch table are untouched and unused,
ready for a real widget set and for item 3's bin dwell. Item 3 (bin dwell
+ food-item window) has a developer decision recorded but is not started.
Check that file before starting new M5 work.

## STATUS
Architecture v3 adopted. Full rewrite in progress.
Stage 1-2 code is being replaced, not extended.
Current milestone: **M4 (calibration and dataset capture)**, but its
calibration approach changed after the "code-complete" line below was
written — **read M4k through M4n-fix (near the end of the M4 section,
just before "FIXED") before the M4.1-M4.7 build-item entries or the old
"STILL OWED FROM M4" list, both of which describe the deleted
dot-projection wizard.** Dot calibration was removed outright on
2026-08-12 and replaced by a manual 4-corner drag tool, which has now
been run in a real browser against a real camera (M4l) — the first M4
work to be, and it immediately surfaced 4 real bugs, 3 fixed same-day,
one (the bin-rect editor's own reorientation) explicitly deferred to a
**"bin boxes"** session, done same day as M4m (below). M4n (2026-08-12)
built the projector grid `core/bin_grid.py` always said was coming and
flipped oF's `kUseCoreRects` switch back on to read it; M4n-fix (same
day) is where it actually ran on the rig for the first time — the stale
running process caught outright, the UI rebuilt as a select-a-line/
arrow-key-nudge tool per developer feedback, and Verify dropped from
both grids — **still not confirmed: nobody has watched a nudge actually
move a line on the projected table.** M4n-fix's own section says exactly
what is and is not verified. M3 (camera) is
also code-complete and also still owes its acceptance test on the rig.
The paragraph below (M4's 7 build items, from before the dot-calibration
deletion) is kept as a record of that milestone's original shape —
**every mention of dot projection, `calibrating`, or `classifier/dots.py`
in it is stale; M4k/M4l/M4m/M4n/M4n-fix are current.**
Next milestone per the doc's dependency graph: M5 (tracker, hover, dwell)
— but note it depends on M4's homography being *real*, not merely
computed, so M4's acceptance test is on M5's critical path in a way M3's
was not.
M3.1 (`common/framebus.py`), M3.2
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

**RESOLVED 2026-08-13.** `data/catalogue.json`'s 8 placeholder items
(`curly_noodle`/`long_noodle`/`dried_prawns`/`soya_chunks`/`tofu`/
`baby_corn`/`mushroom`, plus `egg`) are replaced with 8 real ingredients
photographed the same day: `instant_noodles`, `hand_pulled_noodles`,
`fried_tofu_roll`, `fish_balls`, `dried_shrimp`, `beef_balls`, `egg`
(id unchanged), `button_mushrooms`. **This paragraph's "id/class_name
and display name now usually coincide" claim is WRONG and is corrected
below (same day, third pass) — most of these are photographed as a
substitute prop after all, not the real ingredient.** Left here as a
record of the assumption at the time, not deleted.
Schema bumped 3 → 4: `Item` gained an optional `pinyin` field (en-locale
romanisation of the `zh` name, defaults to `""` for any file that omits
it) — a display aid, not a translation, so it rides beside `names["zh"]`
rather than inside it. Nothing renders it yet (oF/staff view are
untouched). `base_currency` is now `"USD"` (was `"INR"`) and
`data/locales/en.json`'s `_currency.symbol` matches (`$`, was `₹`) —
prices are placeholder-sensible, not sourced from a real menu, per the
developer's explicit call.
Two non-food capture-tab labels, `empty_tray`/`no_tray`, replace the
single placeholder `"empty"` in `core/main.py`'s `NON_FOOD_CAPTURE_LABELS`
— unioned into the Capture tab's label dropdown the same way `"empty"`
always was, **not** added to `catalogue.json`: neither is ever billable
(no price, never `resolved()` by BinMap), and `Catalogue.load()` requires
a price on every entry it holds, so a non-food state has no home there by
construction, not by omission.
902 tests pass (`python -m unittest discover -s python/tests`).

**Same day, second pass: 4 more real ingredients added — the catalogue is
now 12 items against 8 physical bins, and that gap is intentional, not a
bug to close.** `dried_eel_strips`, `shrimp_cake`, `potato_slices`,
`lotus_root_slices` (same USD-pricing/pinyin shape as the other 8).
`pricing.Catalogue`'s own docstring already says the catalogue is "every
item that could ever be in a bin… not which bin it is in" — BinMap's job
— so nothing forced catalogue size to equal `binmap.NUM_BINS`; only two
tests in `test_pricing.py` had baked in the coincidence that they were
equal so far (`test_real_catalogue_file_loads_and_has_eight_items`,
`test_real_catalogue_has_exactly_eight_ids_for_the_mock_bin_seed`), and
both are rewritten to check "at least `NUM_BINS`, no duplicates" instead
of "exactly 8". `core/main.py._seed_binmap` already only ever reads the
first `NUM_BINS` ids off `catalogue.ids()` (`if i < len(ids)`), so the 4
new items sit in the catalogue unseeded into any bin at boot — available
to the Capture tab's label dropdown (`_capture_msg`'s `choices`, which
unions every catalogue `class_name`) for photographing training data
before any bin ever carries them for real. 907 tests pass.

**Same day, third pass: `id`/`class_name` renamed off the display name
and onto what was actually photographed — `docs/INGREDIENT_SUBSTITUTES.md`,
developer-supplied, is why.** That table makes explicit what the first
pass above assumed away: 9 of these 12 items are photographed as a
**substitute prop**, not the real ingredient — e.g. what's in front of
the camera for the "Fish Balls" plate is literally Soya Chunks, chosen
for a similar shape. Doc §8.1's hidden-label rule says `class_name`
names the thing that was actually photographed, precisely so the model
can emit a label for what it was trained on — a folder called
`fish_balls` full of soya-chunk photos is a folder the model can never
honestly produce a label for. Every `id`/`class_name` in the catalogue
is now a slug of the substitute prop instead: `instant_noodle_block`,
`loose_straight_noodles`, `white_rusk`, `soya_chunks`, `dried_small_
shrimps`, `small_round_rusk`, `chicken_eggs` (was `egg` — the one
surprising rename, since "Chicken Eggs" the prop and "Egg" the display
both look like the same thing in English; the table says otherwise so
the code follows it), `button_mushrooms`, `dried_mango_strips`,
`flat_round_cookies`, `yellow_rusk`, `lotus_root_slices` (the last two
of these, plus `button_mushrooms`, are unchanged from pass two — their
substitute IS the real ingredient). `names.en`/`names.zh`/`pinyin` are
all untouched — the diner-facing side of doc §8.1's split was already
correct.
**Zero migration cost, checked before doing this:** no `state/
bin_map.json` exists on disk (mock seed derives fresh from
`catalogue.ids()` every boot — nothing persists a stale item_id across
this rename) and `datasets/captures/` holds only `.gitkeep` (no captured
photos yet, so no folder to rename to match). Had either existed, this
rename would have needed to touch it too.
`test_pricing.py`'s one integration assertion tied to a real id
(`cat.item("egg")`) is now `cat.item("chicken_eggs")` — everywhere else
that mentioned an old id (`test_binmap.py`, `test_classifier_main.py`,
`test_core_main.py`'s capture `LABELS` list) was using it as an
arbitrary opaque string, unconnected to the real catalogue file, and
needed no change. Tests still pass.

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

**Both bullets above about the switch are SUPERSEDED by M2.6h below** —
the two-half segmented switch and setting's amber half are gone. The rest
of M2.6f (tabs, pips) stands.

### M2.6h — one `Serving` on/off switch, off being setting mode
**2026-08-12, from the developer looking at M2.6f's switch on the tablet:
"the setting serving slide button is awful."** Third control for this
mode, and the first one that asks a question with a single noun in it.

The two-half `Setting|Serving` segmented switch made the operator read two
words and work out which half was lit before knowing what the table was
doing. A switch answers a question, and the question is **is the table
serving, yes or no**. So: one switch labelled `Serving`, knob right for
on, knob left for off, and **off IS setting mode** — `fsm`, the `set_mode`
wire value, `mode` on the `state` message and every refusal are untouched.
**Control change only, no protocol or FSM change**, same as M2.6f was.

- **Off is the ordinary greyed-out off** (`#3a3e46`), the shape every
  tablet switch already has — chosen by the developer over a version that
  kept setting's amber in the off position. The cost is real and is worth
  knowing rather than rediscovering: **M2.6f's I8 pairing is broken on
  purpose.** The table's setting-mode banner is still amber; this corner
  of the tablet no longer is. What carries "setting" here instead is the
  knob position, the dead track, and the action hint in words. `--serving`
  teal is the only mode hue left in the page; `--serving-ink`,
  `--serving-deep`, `--setting-ink` and `--setting-deep` are deleted, they
  existed only to put text on the two coloured halves.
- **The operator-facing copy now names the CONTROL, never the mode**, and
  this is the part that would have quietly broken had only the CSS
  changed: the header no longer contains the word "setting" anywhere, so
  every `Enter setting mode to …` hint was pointing at a control that no
  longer exists. All of them, plus core's own `NOT_IN_SETTING_MSG` (shown
  verbatim on the tablet), now read `Turn Serving off to …`. This extends
  M2.6e item 4's one-word rule rather than bending it — "serving" is still
  the single word, and it is now also the name of the thing you touch.
  `setting` stays the FSM state, the wire value and the doc's word.
- **The five tests that asserted the literal substring `"setting mode"`
  in a refusal now assert equality with `coremain.NOT_IN_SETTING_MSG`.**
  Strictly stronger, and aimed at the invariant that actually matters
  (M2.6e item 4: one source for the refusal sentence) rather than at
  wording that is free to change.
- **Unknown is now a distinct third rendering, not a blank one.** Off used
  to be "no half filled"; off is a real knob position now, so before the
  join seed the switch drops the knob entirely and sinks the track below
  the off grey. A switch drawn in a position it does not know is a lie.

**§12.2 has been corrected**, including the ASCII mockup, which still
showed the chip + `ENTER SETTING MODE` action-bar button from before
M2.6e — it had never been updated for M2.6f's switch either, so it was two
designs stale. The paragraph claiming the six pips are in the header was
corrected at the same time (M2.6f moved them behind Developer).

Verified the same way M2.6/M2.6f were — `node --check` on the extracted
script, every `getElementById` target cross-checked against the DOM by
script, and the three switch states rendered from the shipped CSS in a
browser. Suite: 821 passed, 1 failed —
`TestHoverAndDwellOverTheWire.test_the_staff_view_is_told_where_the_hands_
are`, a timing flake unrelated to this change (passes on its own and on
re-run; same class as the `test_calibrator` flake noted under M2.6).
**Not observed on the tablet.**

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

**2026-08-12, real bug found by the same real-browser run: `/stream.mjpg`
404ed on every single load, and it was never the camera's fault.**
`camera/mjpeg.py`'s `do_GET` matched `self.path` with `==` against the
bare route string. `self.path` is the raw request target including the
query string, and `index.html`'s own `loadLiveImg()` (M3.3) has always
appended a cache-busting `?t=<timestamp>` — so every request has been
`/stream.mjpg?t=...`, never equal to `/stream.mjpg`, and every one 404ed.
`curl http://localhost:8081/stream.mjpg` (no query string) worked fine,
which is exactly why this stayed invisible through M3.2-M3.4: nothing
before now had actually loaded the Live tab in a real browser, the one
client that always sends the query string. The symptom was
indistinguishable from a dead camera — `<img>`'s `onerror` fires on a 404
exactly like it fires on a refused connection, so "Camera offline —
retrying…" looked identical either way and pointed at the wrong half of
the system. Fixed by parsing the path with `urllib.parse.urlsplit`
before the route comparison, in `do_GET` itself so `/snapshot.jpg` and
`/info.json` are covered too, even though nothing queries those with a
query string yet. 2 new tests (one per affected route; `/snapshot.jpg`
wasn't given one since nothing exercises it with a query string, but the
fix covers it identically), 488 total, all passing.
**Any camera process started before this fix is still running the old
code in memory — restart it (Ctrl-C, then `python -m hotpot.camera.main`
again) for the fix to take effect; reloading the browser tab alone does
nothing.**

**2026-08-12, same session: the feed also looked visibly worse than
Windows's own camera app — a real bug, not "expected, dark-room design
assumes different lighting" as first guessed.** `WindowsCapture.
_lock_controls()` forced `CAP_PROP_AUTO_WB`/`CAP_PROP_AUTOFOCUS` off
unconditionally on every open with no prior calibration, no convergence
wait — so it locked white balance onto whatever the driver happened to
be sitting at the instant right after `open()`, before its ISP had
converged anything, which is close to a random value. The OS camera app
never does this: it leaves auto-WB running continuously. Fixed by
leaving every control alone (all autos stay on, `_readback()` just
reports whatever the driver already has) whenever there is no prior
`state/camera_settings.json` value to set a control *to* — the same
"cannot verify V4L2Capture's `AUTO_SETTLE_S`-then-lock sequence is safe
here" reasoning the class docstring already gave for exposure is now
applied consistently to WB and focus too, rather than stopping short of
them. Confirmed visually: a snapshot taken through the fixed path shows
richer, correct-looking colour (saturated blue/red bins) against the
same table the broken lock made look washed out. 1 test updated
(asserted the old forced-off behaviour; now asserts nothing touches any
control's auto mode with no prior settings).

**Human-confirmed on this machine, 2026-08-12: exposure now auto-adjusts
correctly in the Live tab.** Both bugs above are fixed and observed, not
just reasoned about. This is real evidence the M3.1-M3.4 pipeline works
end to end with a live camera — but it is `WindowsCapture` evidence, a
dev-only backend, not `V4L2Capture`. **M3 is still not done by doc §21's
own acceptance test**, which is specified against the rig: the live-feed
half is now de-risked (real frames, correct colour, confirmed by a
human) but untested on Linux/V4L2 itself, and two items haven't been
touched at all — `kill -9` camera recovery (and the "camera stalled"
table banner doc §21 expects for it, which was never built — see M3.3's
notes, only the pip and the Live tab's own placeholder exist), and the
camera elevation angle (I10), for which no measurement tool exists yet
either (`tools/measure_camera_angle.md` is referenced in the doc but was
never written).

**2026-08-13, developer-panel camera controls — not a doc build item, added
because the yellow/green cast came back and the developer needed the same
manual knobs the OS camera app has, not another guess about auto-lock
policy.** `state/camera_settings.json` was found holding
`"locked": true, "white_balance_temperature": 6500` — a genuine lock from a
past run's convergence (2026-08-12's fixes were never wrong; a lock taken
under one lighting condition simply stops being correct once the room's
light changes), with no way to change it short of deleting the file and
restarting. `capture.py`'s `Capture` protocol gains three runtime methods
— `list_controls()`/`get_control_states()`/`set_control()` — implemented
for all three backends: `FakeCapture` (two fixture controls, enough to
drive `camera/main.py`'s and `mjpeg.py`'s own tests with no hardware),
`WindowsCapture` (ten controls — white balance/exposure/focus plus
brightness/contrast/saturation/gain/sharpness/hue/backlight-compensation —
table-driven off `cv2.CAP_PROP_*`, ranges hardcoded and approximate since
DirectShow has no min/max query API, `_lock_controls` itself untouched),
and `V4L2Capture` (the same ten, parsed from real `v4l2-ctl --list-ctrls`
output — actual device ranges, not a guess, though still unverified
against the rig like every other V4L2Capture method). `camera/main.py`'s
`CameraProcess.set_control()` is the one path both a dev-panel POST and
`start()`'s own replay-on-boot go through: the original three fields keep
their existing top-level schema in `camera_settings.json` with `"locked"`
now meaning "are all three still manual right now" (turning even one back
to Auto drops the aggregate, so a restart re-converges fresh rather than
re-freezing the other two next to a control just deliberately unlocked —
no per-field lock exists in the on-disk schema, doc §6.6 predates
per-control independence); every other knob goes into an additive
`"controls"` dict. `camera/mjpeg.py` gained `GET /controls.json`/
`POST /control`, same cross-origin/ownership split `/info.json` already
established (camera owns this state, core never proxies it).
`index.html`'s Developer tab gained a "Camera controls" card — an Auto/
Manual chip plus a slider per control, and a one-click "Auto white
balance" button for the specific bug in hand. 46 new Python tests (three
files), 901 total, all passing.

**Run for real on this machine, against the live app already running
(camera/tracker/classifier/voice/core/oF, all started earlier this
session) — not just reasoned through:**
- `GET /controls.json` against the real webcam returned all ten controls
  with real readbacks — confirmed `white_balance` was in fact
  `auto: false, value: 6500` at that moment, the diagnosis above made
  concrete rather than inferred from the file alone. `focus`/`gain`/`hue`/
  `backlight_compensation` all read back `null` — this driver honestly
  doesn't expose them over DirectShow, not a bug.
- **The "Auto white balance" fix is CONFIRMED, measured, not just
  requested.** A snapshot taken immediately before `POST
  {"name":"white_balance","auto":true}` and another ~4s after (same
  R/B-ratio method M4i used) went from R/B 1.128, G/B 1.554 (the green
  cast the user's own screenshots showed) to R/B 1.028, G/B 1.080 — much
  closer to neutral. `state/camera_settings.json` correctly flipped to
  `"locked": false` afterward.
- **`contrast` genuinely moves the driver** (set 30, read back 30) —
  confirmed with a real value round-trip, then restored to its original
  16.
- **`saturation` does NOT** — requested 40, the driver silently kept 10,
  and `set_control`'s "always report the real readback, never the
  requested value" design caught this correctly instead of lying about
  it, exactly the failure mode `WindowsCapture`'s own docstring already
  warned every control could hit.
- **Exposure's auto-restore (the `CAP_PROP_AUTO_EXPOSURE=0.75` guess) is
  UNVERIFIED, not confirmed.** The call succeeds and the readback reports
  `auto: true`, but nothing here changed the room's light to watch the
  picture actually adapt — the one way to tell "the trigger worked" from
  "the driver silently ignored it and the readback is just echoing the
  bookkeeping back," the same gap `saturation` above fell into. Treat this
  control's Auto position as unconfirmed until someone watches the
  picture react to a real light change.
- **Operational lesson, not a code bug, worth recording so it isn't
  rediscovered:** killing only the camera process (to load this new code)
  left tracker/classifier/oF still holding open handles to the old
  `hotpot_frames` Windows shared-memory segment, so every restart attempt
  hit `FileExistsError` and `run.py`'s supervisor gave up after 5 failures
  in 60s (doc §20.2, working exactly as designed). Windows does not
  release named shared memory until every handle closes, unlike POSIX
  `shm_unlink`. Restarting the *whole* stack (`python run.py --replace`)
  is what actually clears it — restarting camera alone during development
  does not.
- **Not observed: the Developer tab's new card in an actual browser
  click-through.** Verified short of that — `node --check` on the
  extracted script, every new `getElementById` target cross-checked
  against the DOM by hand, and the full `/controls.json`/`/control` round
  trip confirmed directly over HTTP above — but nobody has looked at the
  sliders on screen yet. The app is live on this machine right now; that
  observation is a browser tab away, not a rebuild.

**2026-08-13, same day, follow-up — the click-through above actually
happened, and found two real problems the HTTP-only verification could
not have caught.**

- **Every slider/button failed with "Failed to fetch."** `postControl()`
  sends a JSON `Content-Type` header, which makes `POST /control` a
  non-simple CORS request — the browser sends an `OPTIONS` preflight
  first, and `mjpeg.py` had no handler for it, so `BaseHTTPRequestHandler`
  501'd the preflight and the browser never even attempted the real POST.
  `curl -X POST` from this same session's own earlier verification never
  hit this, because `curl` doesn't preflight — a `curl`-only check of a
  browser-only bug was never going to catch it. Fixed with a `do_OPTIONS`
  handler answering the three `Access-Control-Allow-*` headers the
  preflight needs; confirmed against the restarted live process (`204`
  with the right headers, then the real POST applying `white_balance:
  auto=true`).
- **The explanatory paragraph and the "Auto white balance" button were
  redundant/unwanted UI**, removed at the user's request. The button in
  particular turned out to be genuine duplication, not just clutter: the
  white_balance row's own Auto/Manual chip already posts the identical
  `{auto: true}` request, so the dedicated button was a leftover from
  before the generic per-control UI existed.
- **Added a "Reset to camera defaults" button** (the user's next ask,
  once the sliders actually worked): `ControlSpec` gained a `default`
  field. Auto-capable controls (white_balance/exposure/focus) don't use
  it — resetting them means `auto: true`, not pinning a value. Manual-only
  controls do: on `V4L2Capture` it's `v4l2-ctl --list-ctrls`'s own real
  `default=` field; on `WindowsCapture`, which has no default-query API
  (same limitation as the min/max ranges), it's a **boot snapshot** —
  whatever each manual-only control read back immediately after `open()`,
  before `_lock_controls` or `_restore_extra_controls` touch anything.
  Documented explicitly, in `ControlSpec`'s own docstring and here, as a
  best-available stand-in, not a verified factory/EEPROM default — some
  drivers/OS layers persist the last-used value across app restarts, in
  which case this "default" would actually be whatever a previous manual
  session left behind, not what the camera ships with. Verified via unit
  tests (`FakeCapture`'s fixture default, V4L2's parsed `default=` field,
  Windows's boot-snapshot behaviour including that a later manual change
  doesn't move the reported default) — not yet re-observed against the
  live browser a second time.

## M4 — CALIBRATION AND DATASET CAPTURE (in progress)

M4.1 (2026-08-12) is build item 1: `common/geometry.py` and
`core/geometry_store.py`.
`geometry.py` is split by what it depends on, not by topic. `fit()` is
the only function that needs OpenCV, and `cv2`/`numpy` are imported
**inside** it — the same seam `core/scale.py` uses for pyserial, so
`apply`/`apply_rect`/`invert`/`rms_px` all run on a machine with no
OpenCV, which is what lets core load and use a saved homography with no
camera anywhere. RANSAC rather than plain least squares, and the reason
is a specific failure: the thing that actually goes wrong on a rig is
**one dot mis-paired** (a reflection, a tray highlight, an off-by-one
row), not fifteen slightly noisy ones. Least squares smears that one bad
pair across every point and quietly moves all eight rects; RANSAC drops
it and says how many it dropped. `rms_px` is measured over the inliers
only — over all points it would be dominated by the outlier RANSAC just
decided to ignore, and every rig with one bad dot would read as a failed
calibration.
`geometry_store.py` is doc §5.3's contract as a class: camera rects are
the stored ground truth, stage rects are derived through `H` and never
written to disk, and both files go through `atomicio`.
Five things worth not re-deriving:
- **There is no `verify()` and there must not be one.** Reprojecting the
  derived stage rects back through the same `H` returns the camera rects
  by construction — that is what "inverse" means — so such a method
  passes on a homography that is upside down. A test asserts the method
  does not exist, which is the only way to stop it reappearing. Doc
  §5.3's TRAP, and this is where it lives.
- **`mark_verified()` records that a human answered, nothing more.**
  `verified_at` is new in `state/bin_rects.json`; **§8.4 has been updated
  in the same commit.**
- **A homography maps a rectangle to a quadrilateral, not a rectangle.**
  The derived stage rect is the bounding box of that quad. Measured: 26%
  larger against the tests' harsh synthetic camera, and **the dominant
  term is camera rotation, not perspective** — drop the perspective to a
  realistic near-vertical value and it only falls to 10%. Larger is the
  safe direction (I9: a cutout that is too small leaves a dark crescent
  on the food). Expect the projected cutout to be a few percent bigger
  than the tray on a camera that is not square to the table. Recorded in
  §8.4 too, so it is recognised as geometry rather than debugged as a
  rendering bug.
- **`docs/legacy/bin_offsets.json`'s shape is a reconstruction, not a
  spec**, and is documented as one. The oF code that wrote it was deleted
  at M0.1. The reading — 4 horizontal bin edges, 8 vertical ones, plus a
  global offset — is inferred from the array lengths and produces rects
  192-197mm wide and 245-247mm tall against a 200x255 nominal, i.e. real
  cutouts a few mm inside the drawing, which is what a saw does. It is
  only ever a **seed** for the operator to drag from; if the reading is
  wrong the symptom is visibly offset starting rects, not a mis-bill.
- **`TableGeometry.h`'s CAD numbers now exist twice** (C++ cannot import
  Python). `test_geometry_store.py` mirrors that header's own
  `static_assert` chains — both walks across the table must sum to the
  table dimension — so an edit made on one side and not the other fails a
  test instead of moving four trays 50mm on the rig.
65 new tests (`test_geometry.py`, `test_geometry_store.py`), 553 total,
all passing. Five mutations checked red, each caught only by the tests
aimed at it: `findHomography` switched to least squares (the mis-paired-
dot test and the inliers-only-rms test); `apply_rect` returning the
transformed origin plus the original size instead of the quad's bounding
box; `match_nearest` made greedy in list order instead of distance order;
`save_rects` allowed to write a partial set; stage rects persisted
alongside the camera rects.
Nothing here has touched a camera or a projector. Every homography in
these tests is written out by hand or built by projecting a synthetic
point set — the same no-hardware discipline `core/loadcell_cal.py` has
for the other number in this system that can go silently wrong.

M4.2 (2026-08-12) is build item 2, plus the process that has to answer:
`classifier/dots.py` and a real `classifier/main.py`, which was the M0
stub until now.
`dots.py` is doc §21's build item verbatim and nothing else — threshold,
contour, centroid, area filter, camera-space points out. **No ordering
and no fitting**, deliberately: pairing a detected set against an
expected one is `common/geometry`'s job on the core side, and the
classifier must not need to know what pattern was drawn.
Four rejection rules, and each is a real thing a rig produces on an
inverted field. **The two the doc does not name are the ones that would
have bitten:**
- **An area ceiling.** Doc §4.7 gives `min_area` only. On a black field
  any large bright region is a blob — a steel tray catching the
  projector, or somebody turning the room lights on mid-solve — and its
  centroid joins the fit as a confident, wildly wrong point.
- **An aspect check.** A 200x4 sliver of light along a table edge is
  800 px², comfortably inside the area window, and is not a dot. Shape
  throws it out where size cannot.
- A blob touching the frame border is dropped: its centroid is pulled
  inward by the missing part and *nothing about the blob shows that* —
  right area, right shape, wrong position. One correspondence lost beats
  a biased solve.
- **Centroids come from image moments, not bounding-box centres.** A box
  centre is the middle of the extremes, so one bright speck stuck to a
  dot's edge moves it by half the speck's reach; the moment centroid
  moves by the speck's share of the *area*, which is almost nothing. It
  is also sub-pixel, which is where "under ~3px RMS" has to come from.
`classifier/main.py` is now doc §3's "vision process": frame ring,
`detect_dots` and `capture` from §4.7, `stop`. Three notes:
- **Work runs on a worker thread, not the link's read thread.** A capture
  burst is 10 frames over 5s (§12.7); doing that inline would stall the
  heartbeat and core would mark the process dead (§4.2) *during a
  successful capture*.
- **`classify` is answered with an error, not ignored.** It needs M7's
  backend and a model. Core waits on a reply, and silence is a wizard
  hung on a screen with nothing to look at.
- **The ring is opened lazily and re-opened after any failure.** Camera
  restarts with a *new* segment (§20.1); a classifier holding the corpse
  would never work again after the first camera restart.
Labels are sanitised before becoming directory names — a label is
operator input and `../..` must not become a path.
46 new tests (`test_dots.py`, `test_classifier_main.py`), 599 total, all
passing. Eight mutations checked red: bounding-box centroid instead of
moments; the area ceiling dropped; the aspect check dropped; the
edge-touching rule dropped; `crop()` returning the whole frame; the burst
gap set to zero (caught by the spread test AND the cancel test); `stop`
queued behind the work instead of setting the cancel flag; the staleness
check and the reader-drop removed.
No camera and no shared memory anywhere in these tests: every frame is a
numpy array of white discs on black, and `RingSource` takes an
`open_reader` callable for exactly the reason `ScaleReader` takes
`open_port`.

M4.3 (2026-08-12) is build item 3, the dot calibration wizard:
`core/dotcal.py`, its wiring into `core/main.py`, and the `calibrating`
overlay drawn on the table.

**Doc §24.1's open decision is now made — and it was made from the
table's geometry, not from a camera, because there is no rig here. It
needs a sanity check on real hardware.** §24.1 has been rewritten with
the full reasoning; the short version:
- **Two passes.** Four big corner dots first, used only to *order* the
  second pass's 5x3 grid — each expected grid position is projected
  through the coarse fit and matched nearest-neighbour. The one-pass
  alternative is to sort the detected grid row-major, which works until
  the camera is a few degrees off square, at which point the rows
  interleave, the pairing goes off by one, and **the fit still reports an
  excellent RMS**. Ordering is the only step in the solve with no
  numerical safety net.
- **The rows avoid the bin cutouts, and that is what shapes the
  pattern.** A dot landing on a tray is displaced by
  `height / tan(elevation)` — at I10's worst allowed 70 degrees, a tray
  40mm down moves the dot ~19px, six times the whole error budget. The
  three rows sit in the only bin-free bands: far margin 85mm, row gap
  457mm, near margin 830mm.
- **15 points.** 8 DOF needs 4 pairs; 15 leaves RANSAC room to drop one.
  Denser risks dots merging under projector defocus into one blob in the
  wrong place.
- **The middle row is the tight one — check it on the rig.** Its band is
  30mm; a 13px dot spans ~22mm of it, ~4mm each side. If projector
  alignment is worse than that, set `calibration.grid_rows` to 2.
Every number is a `calibration.*` config key (§8.6, added).

**Found by a test, not reasoned out in advance, and it changes what "a
good calibration" means:** the RMS alone is not a verdict. Feed 6px of
centroid jitter against a 3px RANSAC threshold and RANSAC does exactly
its job — finds the largest subset agreeing within 3px, which came out as
5 of 15 dots, and reports a beautiful sub-pixel RMS over them. **That
would have passed doc §21's "under ~3px" acceptance test while being the
worst solve the rig could produce.** The verdict now needs both the error
and the inlier count (70% of the pattern, never under 6). §24.1 records
this too.

Three more things worth not re-deriving:
- **Core sends the dot POSITIONS, oF does not know the pattern.** I2 at
  its sharpest: if oF held the layout and core assumed it, one edit on
  either side would have core solving against dots that were never where
  it thought — with a perfect RMS, because the fit only ever sees core's
  copy. `overlay.dots` is `[[x,y,r],...]` in stage space; **§4.3 updated.**
- **`calibrating` suppresses every banner, and that is a lighting rule,
  not a UI preference.** The field inverts to black (I9's one exception)
  and the camera sits at a dark exposure hunting bright blobs — a banner
  is a bright shape on a black field, which is exactly what
  `classifier/dots.py` is looking for. **§14.5's precedence table now
  reads calibrating > uncalibrated > setting > error**, with
  `uncalibrated` above `setting` because it survives setting mode: an
  operator who exits still cannot serve, and `setting` would mask the one
  message that is still true.
- **The same inverted-field flag goes to both `Stage::beginContent` and
  `Stage::compositeAndWarp`.** Passing it to one and not the other is the
  failure worth knowing: begin-only leaves the light pass stamping eight
  white rectangles across the pattern; composite-only draws the dots onto
  a white field where nothing can see them. One local in `ofApp::draw`,
  used twice. The keystone warp still runs in both cases — §5.2 says
  `H_cam→stage` implicitly contains the keystone, so solving through an
  un-warped pattern would give a homography for a table nobody projects
  onto.
Classifier replies are correlated **by command id**, not "the next reply
that arrives": a late coarse-pass answer handed to the fine pass would
solve 15 labelled points against 4 real ones.
39 new tests (`test_dotcal.py` 29, plus 10 in `test_core_main.py`'s
`TestDotCalibrationOverTheWire` — a stand-in classifier over a real
socket that projects whatever core just put in the overlay through a
known matrix, so the recovered homography is checked against a reference
core never saw). 638 total, all passing. Six mutations checked red:
nearest-neighbour pairing replaced by a row-major sort (caught by the
rotated-camera test); the verdict taken from RMS alone; the overlay left
up after a solve; the overlay left up after a *failed* solve; the human
Verify answer not cleared by a re-solve.
Builds clean (msbuild, Debug x64, 0 errors, 0 warnings from
`hotpot-table/src`). **Nothing here has been seen on the projected
surface**: the black field, the dots, and whether a real camera can find
them are all still owed.

M4.4 (2026-08-12) is build item 4: the staff view's Setup tab (doc
§12.6) — the wizard, rect dragging on the live feed, Save, and Verify —
plus the plumbing that makes Verify mean anything: **core now sends the
derived stage rect for every bin in `state.bins[].rect`, and oF draws its
plates and light-pass cutouts on them.**

**The TRAP, and how it was answered.** Doc §12.6 and §5.3 both say the
only verification that can fail is a human looking at the trays. So:
- **There is no "projection mode" to switch on for Verify, and that is
  the design, not a shortcut.** The outlines are already on the table
  every frame — core sends the rects, oF frames each ring and each cutout
  with them, falling back to `TableGeometry.h`'s CAD layout when core has
  none. Verify is two buttons and a recorded answer. A separate
  projection path would have been a second renderer whose agreement with
  the real one nobody could check.
- `verified_at` is cleared by a re-solve, by a rect edit, and by a "No".
  The outlines the operator said were on the trays are not these outlines
  any more.
- **There is no test anywhere that reprojects the saved rects through the
  same H.** `test_the_store_has_no_verify_method` (M4.1) is the guard
  against one appearing.

Three things worth not re-deriving:
- **oF's bin rect is now core's when core has one, CAD when it does
  not**, and the absence has to stay an absence: a rect at the origin
  would look like a placed rect nobody placed, while the CAD layout is
  visibly approximately right. `cutoutRectPx()` grows whatever rect it
  gets by `CUTOUT_MARGIN_MM` in px rather than in mm, because a core-sent
  rect has no mm form — it came from a camera through a homography, not
  from the drawing.
- **The rects are cached in `UiLayer::update()`, not read at draw time,
  and not tweened.** `cutoutRectsPx()` is called by `ofApp` after
  `endContent()`, with no `state` in scope. Springing a bin rect would
  smear the light-pass cutout across the table for 150ms after every
  save — a bin rect is rig calibration, not animation.
- **Doc §5.4's scaling trap is the load-bearing part of the dragging.**
  Rects are held and sent in capture-space pixels, never display pixels;
  every pointer coordinate goes through one `toCam()`. The shim harness
  serves a 1920x1080 "camera" into a 480x270 layout box, so a version
  that forgot the conversion is off by exactly 4x.
Rect validation on the core side rejects a NaN (which survives every
`> 0` comparison and would reach `state/bin_rects.json` and then oF as an
undrawable rect — the same hole M2.4 closed for `ref_mass_g`), a
zero-width rect (crops an empty image, which the classifier answers
confidently and wrongly), and a short list. All eight rects are sent on
every Save, never a delta, so a dropped message cannot leave core holding
six new rects and two old ones.
**Not built, and named rather than quietly skipped:** snap-to-grid (doc
§12.6 called it optional; nothing about a hand-dragged tray position
wants quantising, and it would be a setting to explain), and the tab's
other settings — swap hands, dwell, broth/spice, deadband, conf floor —
each of which belongs to the milestone that gives it something to change.
**§12.6 has been updated** to say all of this.
12 new Python tests (`TestSetupTabRects`), 650 total, all passing. Four
Python mutations checked red: the setting-mode gate dropped from
`set_rects`; NaN tolerated in a rect; the stage rect not put on the
`state` message; the verification not cleared by a rect edit.
The staff view was verified the same way M2.6/M3.3/M3.4 were, with no
browser toolchain added: `node --check` on the extracted script, all 46
`getElementById` targets and every `querySelectorAll` selector
cross-checked against the DOM by script (plus tab buttons against tab
panels), and the whole IIFE driven against a throwaway DOM shim over **40
assertions** — the §5.4 scaling, a drag, Undo, a Save with a hole in it,
the calibrate button's three replies, both Verify answers, dragging being
inert in serving mode, and a `geometry` broadcast arriving mid-drag not
yanking the rect out from under the operator. Three JS mutations checked
red: `toCam` scaling removed, the mid-drag guard removed, the
setting-mode gating removed. The shim is scratchpad-only, not committed.
Builds clean (msbuild, Debug x64, 0 errors, 0 warnings from
`hotpot-table/src`).
**Nothing here has been seen in a browser or on the projected surface.**
The Verify answer in particular is a mechanism with no observation behind
it yet — that is exactly the acceptance step still owed.

M4.5 (2026-08-12) closes build item 5: `state/bin_rects.json` seeded from
`docs/legacy/bin_offsets.json`, converted to camera space.
Most of it already existed — `geometry_store.legacy_bin_rects_stage()`
and `seed_cam_rects_from_table()` landed with M4.1, and the Setup tab's
`Load measured layout` button with M4.4. What this step adds is the one
place it has to happen by itself: **a table that has just acquired its
first homography and has no rects yet is seeded automatically**, so the
operator opens the rect editor onto eight rectangles roughly on the trays
rather than an empty canvas.
Two conditions on that, both of which a test would otherwise not force:
- **Only when there are none.** A re-solve on a working table must not
  throw away rects somebody spent five minutes dragging onto the trays.
  The homography moved by a pixel or two; the trays did not.
- **Not saved.** Doc §12.6's "Save is explicit" applies to a seed more
  than to anything else, because nobody has looked at it yet.
The check that can actually fail here is the direction: the seed goes
stage -> camera through `H^-1`, so deriving it back must land on the mm
layout the legacy offsets describe. The reference is the independently
computed millimetre geometry, **not the rects themselves** — which is
what keeps this out of doc §5.3's TRAP. A mutation swapping `H^-1` for
`H` was checked red by exactly that test.
4 new tests, 654 total, all passing. Three mutations checked red: the
seed made unconditional (caught by the hand-dragged-rects test); the seed
removed entirely; the seed sent through `H` instead of `H^-1`.
**Still a reconstruction, and still owed:** whether the four keys in
`bin_offsets.json` mean what M4.1 inferred has never been checked against
a real table. The symptom of a wrong reading is visibly offset starting
rects that the operator then drags — not a mis-bill.

M4.6 (2026-08-12) is build item 6: `UNCALIBRATED` in the FSM, working
from a fresh clone with an empty `state/`.
`Fsm` takes an `is_calibrated` **callable** — not a bool and not a
`GeometryStore`. Not a bool because the answer has to be re-asked at
every setting-mode exit rather than sampled once at boot; not the store
because this module still knows nothing about state files, the same
reason `refresh_weights` is a callback.
Four things worth not re-deriving, and the second is a trap the doc's own
diagram walks into:
- **`fsm.serving` is a predicate, and the scale is gated on it, not on
  `state is not SETTING`.** Doc §9.1 makes serving unreachable in
  UNCALIBRATED too — a table that does not know which tray is which must
  not weigh food out of one and charge for it. One predicate, so a state
  added later cannot start billing by omission.
- **Setting-mode exit returns to UNCALIBRATED, not IDLE, on a table that
  still has no geometry.** §9.1's diagram writes that edge as
  SETTING → IDLE, which is right for the ordinary case and wrong for the
  first-boot one: **calibration is a setting-mode activity**, so the
  operator is *in* setting mode while doing it, and an exit that always
  landed on IDLE would open a table with no geometry at all. Re-asked at
  exit, because the whole point of the mode being left is that the
  operator may have just fixed it. **§9.1 has been corrected.**
- **Setting mode stays reachable from UNCALIBRATED, and must** — blocking
  entry would make the state unescapable, since calibration happens
  inside it.
- **`calibration_complete()` re-checks the geometry itself** rather than
  trusting the caller. Core calls it after every geometry write, and a
  write that saved a homography but no rects must not open the table.
On the table: a third banner, **violet `#7c5cd6`**, headline
`NOT SERVING`, subline `not set up yet`. A third hue rather than reusing
amber or red because I8 distinguishes states by hue and this genuinely is
a third state — not a subsystem fault (red), not staff working on a table
that is otherwise fine (amber), but a table that has never been set up.
§14.5's precedence table now names it in second place.
In the staff view: its own violet banner and a **one-time** jump to the
Setup tab. One-time, not a lock — once the operator has been taken there
they must be able to walk to Live and stay, and every `mode` broadcast
re-selecting the tab under their hands would be worse than not jumping at
all.
**Every existing Core test had to be given a calibrated fixture**, and
that is the change worth noticing: `CoreCase` now writes a homography and
eight rects into its throwaway directory before starting Core, because
otherwise every test about pricing, the mode or the `state` message would
have been testing a table that refuses to serve. M1-M3 assumed a table
that always served because no other possibility existed. The two classes
that want an empty `state/` opt out with `calibrated_fixture = False`.
10 new Python tests (`TestUncalibratedBoot` — boots uncalibrated, the
table and the tablet are both told, nothing bills, a hand cannot start a
session, setting mode still reachable, exit returns to UNCALIBRATED,
saving the geometry opens the table, a half-saved geometry does not),
664 total, all passing.
**One of those ten exists because a mutation found nothing.** Dropping
`uncalibrated` from `_publish_mode`'s on-change key was checked and *no
test went red*: the `mode` message is broadcast on change, and leaving
the field out of the comparison key means a table that becomes
calibrated without its mode or cart also changing never tells the
tablet, which then shows "this table has not been set up yet" over a
table that has been, clearable only by a reload. Latent today because
the ordinary path also flips `mode`; not latent the moment anything else
completes calibration. `test_the_tablet_is_told_when_the_table_stops_
being_uncalibrated` is that test, and the mutation is red now.
Six Python mutations checked red in total, and two more JS ones (the tab
jump removed; the one-time latch removed, which drags the operator back
to Setup on every broadcast). The shim harness is now 45 assertions.
Builds clean (msbuild, Debug x64, 0 errors, 0 warnings from
`hotpot-table/src`).
**Not observed:** the violet banner has never been on the projected
surface, and no fresh clone has actually been booted on a rig.

M4.7 (2026-08-12) closes build item 7, the last of M4: the staff view's
Capture tab (doc §12.7) and `tools/export_edgeimpulse.py`.

**Doc §21's acceptance list turns the lighting requirement into a rule
about DESIGN, not behaviour** — "If the Capture tab has its own lighting
path, that is a bug to fix before collecting a single image, not after" —
so the way it is satisfied is by what the code does *not* contain:
- **Neither the tab nor core's capture handler has any lighting control
  at all.** There is nothing to keep in sync, so nothing can drift. The
  crops the operator previews are drawn from the **same live `<img>`** the
  Live and Setup tabs show, at the bin's own camera-space rect — same
  image, same rectangle, no second endpoint to disagree.
- **The one moment the field is not what serving mode shows is dot
  calibration's black-field inversion, and a capture is refused outright
  while it is up.** That single check is what makes the rule unbreakable
  rather than merely written down. A burst overlapping a solve writes
  photographs of food in the dark, and they look perfectly plausible
  sitting in a folder.
- Setting mode is required — **not for the lighting** (§14.5 makes
  setting mode's field identical to serving mode's) but because the
  operator is reaching over trays, which in serving mode is a pick and
  would bill.
- **The rects come from the geometry store, not from the tablet**, so an
  un-saved drag can never reach the dataset.
Three smaller decisions:
- **The label default is the item's `class_name`, not its display name.**
  §8.1's hidden-label rule runs the *other* way here than on the table: a
  training folder called "Fish Ball" is one the model can never emit a
  label for.
- **The session counter is read off the filesystem**, not held in memory.
  An operator who restarts core mid-collection must not see it reset.
- The classifier writes each JPEG through `atomicio` — a capture session
  interrupted by a power cut must not leave a half-written image that the
  export later uploads as training data (§20.4's rule applied to the
  dataset).
`tools/export_edgeimpulse.py` exists for three reasons beyond rearranging
a tree that is already folder-per-label: **the sidecars must not be
uploaded** (they are provenance, not training data), **filenames must
survive being flattened** (two bins captured in the same millisecond
differ only by their `_bin<i>` suffix), and somebody has to print how thin
the thin classes are. It also counts **distinct days** separately from
images — §19.2 asks for 4+ sessions, and 600 photographs of one tray under
one arrangement of the light is one session's worth of information however
many files it is. It copies; it never moves or deletes.
**§12.7 has been updated** with all of the above.
26 new tests (`test_export_edgeimpulse.py` 16, `TestCaptureTab` 10), 690
total, all passing. Eight Python mutations checked red: capture allowed
during calibration; capture allowed in serving mode; blank labels
tolerated; the bin index dropped from the rects (which is what puts
`_bin<i>` in the filename); the label default switched to the display
name; the export copying sidecars; the export not prefixing the label;
sessions counted as images rather than days. Four more JS mutations red:
a rect save not moving the crops (a whole session cropped off the trays);
the setting-mode gating dropped; the crop buffer not sized to the rect;
the burst not clamped. The shim harness is now **71 assertions**.
**Found and fixed while writing this:** the `Write` tool put a literal NUL
byte inside a `join(" ")` in `index.html`, which `node --check` caught
immediately. Worth knowing that the JS verification pass is not
ceremonial — it caught a corruption no Python test could see.
**Nothing here has been run against a real camera, a real projector or a
real browser.** No image has been captured, so the export tool has never
processed a real dataset.

**M4 build items 1-7 are all code-complete.**

### STILL OWED FROM M4 — the physical acceptance test, in full
**SUPERSEDED by the calibration-approach change below (M4k) — kept as a
record, not as the current owed list.** Everything in this list assumes
dot-projection calibration, which was deleted outright the same day
(2026-08-12) after three failed rig sessions (M4h/M4i/M4j) could not get
past room-light contamination. It was replaced by the manual 4-corner
drag tool. **See M4k and M4l below for what is actually owed on M4 now**
— the dot-pattern bullets here (the middle-row band, `grid_rows`,
`field_level` sweep tied to the dot solve) no longer apply to anything in
the codebase; the UNCALIBRATED-boot and Verify bullets are still real
checks, just against the new tool instead of the old one.

**Nothing in this list has been observed. All of M4 ran in tests, a
framebuffer, or a DOM shim.** Doc §21's M4 acceptance list, plus the gaps
each build item recorded:

**On the rig, with a camera and a projector:**
- Fresh clone with an empty `state/` boots to UNCALIBRATED, **the table
  says so** (the violet banner, never seen on the projected surface), and
  the staff view opens on Setup.
- Run dot calibration → the field actually inverts to black, the dots are
  actually visible to the camera at its dark exposure, and **the RMS comes
  back under ~3 px over at least 10 of the 15 dots**. Everything about the
  dot pattern is a reasoned default derived from the table's geometry with
  no camera present — see the M4.3 entry and doc §24.1. **First attempt
  2026-08-12 failed at exactly this step (1.0 px over 6 dots, 4 not
  found); see M4h below for the three fixes, none of them yet re-run.**
- **Specifically check the middle dot row.** Its band is only 30 mm tall
  (the 442-472 mm row gap); a 13 px dot spans ~22 mm of it. If the
  projector's alignment is off by more than ~4 mm the dots clip the tray
  edges and the row should be dropped — `calibration.grid_rows` to 2.
- Drag the 8 rects on the feed, Save, then **Verify: do the projected
  outlines sit on the real trays?** This is the only check in the whole
  milestone that can fail (§5.3's TRAP). Answer honestly. Nothing in the
  code can substitute for it and nothing has been built that pretends to.
- Nudge the keystone → the staff view raises "calibration stale". The
  fingerprint plumbing is wired end to end in tests; a real projector
  nudge has never produced one.
- **Sweep `field_level` against camera exposure (§6.6), pick the pair,
  freeze it, and confirm it is written to `state/camera_settings.json`.**
  Then look at a bin crop: evenly lit, no colour cast, no visible edge
  from a UI element. **Untouched by M4 — no sweep has been done.**
- Capture 20 images per class and export → a folder-per-label tree. The
  export tool has never processed a real image.
- **Confirm every capture is taken with the bin patches lit exactly as
  serving mode lights them.** The design makes this true by having no
  lighting path at all (M4.7), but that is an argument, not an
  observation.

**Also still owed, and it caps everything above:** the **camera elevation
angle** (I10) is still unmeasured — open since Stage 1, due in M3, and
`tools/measure_camera_angle.md` still does not exist. Below 70 degrees it
is a hardware problem to fix before M5, and it directly bounds how much
of the dot-pattern reasoning holds.

**Reconstructions that a rig would confirm or refute:**
- `docs/legacy/bin_offsets.json`'s four keys mean what M4.1 inferred
  (4 horizontal bin edges, 8 vertical, plus a global offset). Symptom of
  a wrong reading: visibly offset starting rects the operator then drags.
  Not a mis-bill.
- `dotcal.SETTLE_S` (0.6 s between showing a pattern and asking the
  classifier to look) is a guess with a rationale, not a measurement.

### M4h — three fixes from the FIRST rig run of the wizard (2026-08-12)
The run reported **"1.0 px average error, 6 of 15 dots used, 4 not
found"** and a yellow cast on the Live tab. Three separate causes, all
fixed, none of them visible from any amount of reading before somebody
ran it. **Note what the good RMS was worth: 1.0 px over 6 dots was the
worst calibration the rig could produce, and the number looked ideal.**

**1. The white balance froze itself, and it was a loop, not a value.**
`camera/capture.py` writes exposure/WB/focus to
`state/camera_settings.json` after every open, and the next run reads
that file back as `prior_settings` and applies it. Nothing recorded
*which kind* of value it held. So: run 1 left auto-WB on (correctly —
that was 2026-08-12's earlier fix) and read back 6500 K; main.py saved
it; run 2 read it and turned auto-WB **off** to pin 6500 K under
projector light. Daylight WB on a warm scene is exactly a yellow cast,
and every run after inherited it, so the fault looked like the camera.
`CaptureInfo.controls_locked` now records whether the backend actually
put the device into manual mode, main.py writes it as `"locked"`, and
both backends apply a prior only when it is set. **A file written before
the flag existed reads as false**, so the poisoned one on disk stopped
being applied without being deleted. The earlier fix was half of this;
half a fix left the loop running.

**2. The 180-degree trap came back, silently.** M4.3 dropped the marker
dot and used `geometry.order_quad`, which labels the four corner blobs by
where they sit *in the camera image*. **This rig's camera is mounted at
180 degrees** — measured 2026-08-08, commit `b847c0f`, which added a
marker dot to the then-current solver for precisely this. At 180 every
corner pairs with its opposite, and **four points always fit a homography
exactly, so the inverted answer comes back with ZERO error**. The fine
pass then inherits the flip and matches happily. Nothing in the code
could catch it; only the human Verify step, at the far end of the wizard.
The coarse pass now draws corner 0 at radius 40 against 24;
`geometry.identify_marker` finds it by area and refuses rather than
guessing; `geometry.order_quad_marker_first` takes the cyclic order from
angles about the centroid (rotation-invariant) and starts it at the
marker. **`order_quad` is still in the tree with a warning on it — do not
put it back into calibration.** The test that pins this probes a point
the solver never saw: under the old pairing it lands 520 px away with
`rms_px` still ~0.

**3. The detector was weaker than the one it replaced, against numbers
already measured on this rig.** `tools/calibration/solve_homography.py`
is still in the repo and records: the plywood runs **~29 to ~58 grey
across one frame**, while a dot sits only **~25-50 above whatever is
under it**. Those ranges overlap — a dim dot on the dark end is darker
than bare board on the bright end — so no single threshold finds them
all. That is the "4 not found". Restored all three of the old solver's
answers: a **white top-hat** to flatten the board (kernel must exceed the
biggest dot or it eats the dot — core sizes it from the pattern, since
the classifier is never told the dot size, I2); a **threshold sweep**
choosing the level with the longest unbroken run at the expected count
(the old solver let the homography break ties between levels; detection
cannot fit anything, so stability breaks them instead); and **40-frame
averaging**, because that file measured dot contrast as "the same order
as this sensor's frame-to-frame noise. Averaging is what makes the outer
dots separable at all."

708 tests pass. New config: `calibration.marker_dot_radius_px`,
`min_marker_ratio`, `tophat_scale`, `average_frames`. **oF is untouched
and needs no rebuild** — per-dot radius was already the overlay payload's
shape and already what `UiLayer::drawCalibrationDots` consumes.

**NONE of this has been run on the rig.** Every fix is reasoned from the
old solver's measurements plus the one failure line the wizard printed.
Specifically unverified: whether the cast is actually gone, whether the
40 px marker is separable at the camera's dark calibration exposure, and
whether the sweep now finds all 15.

**Still open from that run, not started:** the camera view is not square
to the table edge, so the feed wants rectifying and cropping to the table
plus a border. Nothing like that exists. **The design trap to settle
first:** rects dragged on a rectified feed live in rectified space while
`H` maps *raw* camera to stage — so either keep the rectification
display-only, or compose it into `H`, but never half of each. It belongs
with the bin-box selection work, which is the same screen.

### M4i — M4h actually run on the rig, same day (2026-08-12)

**Verdict up front: 2 of 3 M4h fixes confirmed correct on real hardware.
The third (detector) was wrong as shipped and has been corrected, still
short of a working calibration, and the real fix (ROI crop or background
subtraction) is not built. Bin-square rendering is force-disabled below
until that's done — do not re-enable it as a side effect of other work.**

**1. White balance — CONFIRMED FIXED, and the "still there" report was a
different thing.** Measured directly: table lit by the app's own white
field, R/B=0.96 (neutral). The projector as illuminant (I9) means the
table's colour IS the room's colour whenever the app isn't actually
projecting the field — the earlier "still yellow" report was almost
certainly the sepia desktop wallpaper showing through with no process
running, not the fix failing. `state/camera_settings.json` now correctly
shows `"locked": false` for an unlocked prior. No code change needed.

**2. The 180-degree marker fix — CONFIRMED WORKING, exactly as designed.**
Log from a real coarse pass: `marker dot is blob 0 of 4 (area 682 px2) —
camera orientation resolved from it, not from error`, immediately after
`detect_dots found all 4 dots`. Photographed too: the marker drawn
top-left in stage space appears bottom-right in the camera frame, which
is the 180-degree mount made visible. Do not touch this mechanism.

**3. The threshold sweep — WRONG, reverted.** Never should have shipped
without running it. Ground truth from a real coarse-pass frame with 4
known corner dots: a PLAIN FIXED threshold of 150, top-4-by-area, found
4 of 4 real corners. `detect_best`'s sweep — with or without the top-hat —
found 0-1 of 4. Its "longest stable run" tie-break was locking onto a
room lamp's plateau, not the dots'. `classifier/main.py._detect_dots` no
longer calls `detect_best` automatically; `dots.detect_best` and
`dots.flatten_background` are UNTOUCHED and still tested, just not wired
in. `DEFAULT_THRESHOLD` moved 200 -> 150, also measured: the fine pass's
smaller 13px grid dots did not reliably clear 200 even with exposure
locked (a real run found only 11 of 15 at 200; 150 found 14 plausible
grid-sized blobs on the same frame).

**Also found and fixed on the same rig session, not part of M4h:**
- **Camera was running at 4.2 fps.** `WindowsCapture.open()` asked for
  MJPG, then set resolution, then fps — and setting resolution OR fps
  each independently reset DirectShow back to its default format (YUY2 on
  this camera), which at 1920x1080 is ~6 MB/frame and saturates USB to
  4.2 fps. FOURCC must be the LAST `set()` call. Fixed and measured:
  4.19 -> 26.26 fps, same resolution, same camera. This was silently
  capping frame averaging (40 frames at 4 fps timed out at 15) and is
  directly on the tracker's MediaPipe budget.
- **Exposure convergence now genuinely locks, on Windows too.** The
  2026-08-12-morning fix left every auto running forever on Windows as a
  safe stop-gap, because the DirectShow manual-exposure trigger value was
  unverified and an unconverged lock had looked worse than the OS camera
  app. Both blockers tested directly on this rig, same day: `.set(
  CAP_PROP_EXPOSURE, v)` forces manual mode by itself (no separate
  trigger call needed — `CAP_PROP_AUTO_EXPOSURE`'s readback is always -1
  on this driver regardless), and a plain `AUTO_SETTLE_S` sleep with no
  reads during the wait converges identically to reading during it
  (DirectShow keeps the capture graph running independently). Watched 5s
  post-lock: 40.3-40.4 mean, dead stable, where the auto had been visibly
  ramping (70 -> 92 over ~2s) during a calibration run minutes earlier.
  `WindowsCapture` now mirrors `V4L2Capture`'s converge-then-lock exactly.

**End-to-end result, both fixes together, measured, not reasoned:**
coarse pass perfect (4/4, marker resolved). Fine pass **6 of 15 dots
agree** (up from 4, and this time `rms_px=0.95` — a real fit over
multiple points, not the earlier `0.0` degenerate 4-point case) — still
short of the 10 the code requires. **Root cause identified, not fixed:** a
room lamp outside the table, at the extreme edge of the camera's field of
view, fragments into several blobs once the field inverts to black, and
those fragments span the same size range as a real 13px grid dot. No
threshold separates them — they are not brighter or dimmer than real
dots, just differently shaped and positioned. `min_area`/`max_area` don't
catch it (same size range); the frame-edge filter doesn't either (the
lamp is fully in frame). **Two real fixes, neither built:** an ROI crop
to the table (`classifier/dots.py`'s module docstring now names this,
2026-08-12) removes the lamp from the image entirely and is also wanted
for the tracker's own MediaPipe performance — cropping the area of
interest is the same piece of work serving both M4's calibration and
M5's tracking; or subtract a black-field reference frame from the pattern
frame so only what changed (the dots) survives.

**Bin-square rendering force-disabled, deliberately, pending the above.**
`UiLayer::binRectPx` had a `kUseCoreRects = false` kill-switch added
2026-08-12 — the table was projecting squares computed from exactly the
`rms_px: 0.0, n_points: 4` TRAP calibration M4i's own section 3
diagnosed, i.e. squares that do not correspond to anything real. Forces
the CAD-layout fallback (M4 build item 6's own "visibly approximately
right rather than visibly broken" design) unconditionally. **Flip
`kUseCoreRects` back to `true` only after:** a fresh dot calibration
reports enough real inliers (not a 4-point degenerate fit), AND a human
has run the Verify step and answered honestly that the projected outlines
sit on the real trays. Builds clean, msbuild, 0 errors.

709 tests pass, 8 new/changed (`WindowsCapture` lock behaviour). All
python-side changes verified against real hardware this session — fps,
exposure stability, marker resolution, and the coarse/fine pass counts
are measured numbers, not reasoning. The oF change is unverified beyond
"builds clean" — nobody has looked at the projected table since it was
added.

**Next session starts here, per the developer's own framing:** the ROI
crop / background-subtraction work above, which unblocks both the
remaining calibration gap and the bin-box rectification work already
queued in the M4h note above. They are the same screen and share the
same coordinate-space decision (display-only rectification vs composing
into `H`) — do them together, not as two separate passes.

### M4j — the ROI crop, built and run on the rig; bin-box reorientation
built alongside it; a NEW lighting problem found, not fixed (2026-08-12)

**The coordinate-space decision, made explicit before any code:
display-only.** `H_cam->stage` still maps raw camera pixels to stage
pixels, exactly as doc §5.3 says. Every rect `core` stores, sends to the
classifier, or accepts from `set_rects` is still raw camera-space —
nothing about how a rect is held or matched changed. The crop only
changes what the Setup tab's browser DRAWS for the operator to look at
while dragging. This is the decision M4h's note asked for before writing
any of this; the alternative (composing the crop into `H` itself) was not
built, and nothing here is half of each.

**1. The ROI crop that closes M4i's gap.** `core/dotcal.py`'s fine pass
now computes the table's own footprint in camera pixels — `geometry.
apply_rect(stage_to_cam, (0,0,stage_w,stage_h))`, padded by
`roi_margin_px` (config `calibration.roi_margin_px`, default tied to
`MATCH_GATE_PX` = 120px, **not picked independently**: a real dot can
legitimately land up to the match gate away from where the coarse fit
expected it, so a tighter ROI would crop away a dot the matcher was still
willing to accept) — and sends it as `roi:[x,y,w,h]` on the fine pass's
`detect_dots` command only. **Never the coarse pass**: there is no table
footprint to crop to before the coarse pass has found one. `classifier/
main.py._detect_dots` crops the frame to it (`crop_rect`, a small
refactor of the existing `capture()` crop that now also returns the
offset it actually clamped to) before running the detector, then adds
that offset back onto every returned point — camera-space in, camera-space
out, same contract as before, ROI invisible to anything downstream. The
bounding-box helper (`dotcal.pad_rect`, promoted from a private
`_padded_bbox` once a second caller needed it — see part 2) is pure
Python, no camera, fully covered by `test_dotcal.py`'s two new tests
against an independently-computed reference (not `apply_rect` itself,
to avoid the fix passing by construction against its own code path).

**Measured on the rig, three consecutive real solves, same lighting:**
10, then 11, then 11 of 15 fine-grid dots agreed (`good: true` every
time), RMS 0.95–1.15 px. Up from M4i's own measured 6 of 15. This is the
actual acceptance bar (doc §21: "under ~3 px over at least 10 of 15")
being met on real hardware for the first time in M4, not reasoned about.

**Confirmed NOT a fluke by an A/B, not just repetition:** `ROI_MARGIN_PX`
was temporarily set to 5000 px (large enough that `crop_rect`'s clamping
makes the "requested" ROI cover the whole frame regardless — equivalent
to no cropping at all), core restarted, and the calibration was re-run.
It still failed, 4 of 15 — proving the failure that showed up a few
minutes later (below) was not the ROI cropping away real dots.

**2. A NEW problem, found by testing, not by reasoning: a room light
came on (or brightened) partway through this session and broke
calibration again — independent of the ROI fix.** After the three good
runs, four further attempts all failed (4 of 15, then progressively
fewer real blobs — 10, then 7, then 6 — even as coarse-pass noise stayed
high). A snapshot grabbed mid-solve (`classifier`'s own `<img>`, fetched
directly, not reasoned about) shows why: a bright light is visible at the
extreme top-left edge of the frame, and the "black" field the projector
is supposed to be showing reads as a light cream colour, not black — the
room's ambient light is now bright enough to wash out the projector's
black field broadly, not just contaminate one corner. That collapses
dot-to-board contrast (already only ~25–50 grey levels per `dots.py`'s
own docstring) across the WHOLE frame, which an ROI crop cannot fix — ROI
removes a light source that is spatially outside the table, it cannot
restore contrast that ambient light has removed everywhere. The A/B
above (part 1) confirms this reading: disabling the crop entirely did
not change the failure at all.
**This is the same room lamp M4i named**, photographed for the first
time rather than merely inferred, now apparently brighter than it was
during the three good runs. **Not fixed here — it is a physical/
operational problem, not a code one.** Recommended before the next
calibration attempt: turn the light off, shield it, or point the camera
away from it. No further code mitigation was attempted this session;
background subtraction (M4i's other named option) would help the
spatially-local case but not a broad contrast collapse either, and
burning more time on detector tuning against a room light that might
simply be a mistake (someone left it on) was judged not worthwhile
before that's ruled out.
**Consequence for `state/homography.json` on disk right now:** the file
holds the FIRST of the three good runs (10 points, rms 0.95 px),
deliberately restored there after the later bad-lighting runs overwrote
it with a degenerate 4-point solve — `dotcal.py` saves whatever it
solves regardless of `good`, which is correct (an operator retrying needs
to see the last attempt's numbers) but means a string of bad attempts
left a genuinely bad homography on disk until this was noticed and fixed
by hand. **This is real data from this session, not synthetic** — but it
predates the room-light problem and has not been re-verified since ROI
work landed. Doc §12.6's Verify step (a human looking at the real trays)
is still owed, is still the only check that can catch a wrong-direction
homography (§5.3's TRAP), and matters more than usual right now given
how the file got into its current state.

**3. The bin-box reorientation/cropping work, built on the same ROI.**
Doc §5.4's `toCam()` staff-view scaling relied on the raw, un-rotated
`<img>` naturally sizing itself; that still works with no `camera_roi`
(unchanged fallback — a table with no homography yet shows the raw feed
exactly as M4.4 built it). With one:
- `core/main.py._geometry_msg()` gains `camera_roi: [x,y,w,h] | null` —
  **the SAME padded bounding box the classifier's ROI crop uses**
  (`dotcal.pad_rect` again, called from `_camera_roi_msg` against the
  saved `H`, not a second computation that could quietly drift from the
  first). `null` before any homography exists.
- The Setup tab's `<canvas id="setupOverlay">` — previously a transparent
  rect-only overlay drawn on top of the visible `<img>` — now draws the
  video itself when a `camera_roi` is present: crop to the ROI, flip 180
  degrees (`ctx.scale(-1,-1)`, exact, no trig — the camera's mount is
  exactly 180°, not approximately, per M4i / commit `b847c0f`), into a
  canvas sized to the ROI rather than the full frame. `setupImg` is
  hidden (`visibility`, not `display`, so it keeps decoding the MJPEG
  stream the canvas is sampling from) while this is active. A
  `setInterval` at ~10fps redraws it — MJPEG has no per-frame JS hook to
  redraw from, unlike the old scheme where the browser painted the
  visible `<img>` natively with zero JS involved.
- **Rects stay in raw camera space on the wire, exactly as doc §5.4
  requires** — `toCam()` now undoes the same flip (`cameraRoi[0]+
  cameraRoi[2]-lx, cameraRoi[1]+cameraRoi[3]-ly`), and drawing goes
  through the equivalent forward transform (`toDisplay`/`rectToDisplay`).
  Nothing about `rects`, `set_rects`, or what reaches core changed.
- **Explicitly NOT full perspective rectification.** The camera is not
  square to the table edge (M4h's still-open note) and a pure 180-degree
  flip does not fix that — the table still appears slightly rotated in
  the cropped view, visible in the verification screenshot below. Full
  unwarping via the homography was considered and deliberately not
  built: it needs either a continuous per-pixel canvas warp or a CSS
  `matrix3d` derived from `H`, materially more code and risk, for a
  cosmetic improvement over what shipped. Named here so it reads as a
  scope decision, not a forgotten half-measure.

**Verification actually performed, and what was not:**
- 10 new/changed Python tests (`test_dotcal.py` +2, `test_classifier_
  main.py` +6, `test_core_main.py` +1 new method +1 assertion), 718
  total, all passing.
- The 180-degree flip's closed-form algebra (`toDisplay`/`rectToDisplay`/
  `toCam`) checked standalone in Node against hand-picked cases and a
  16-point round-trip grid — independent of the DOM, no shim needed for
  pure math.
- `node --check` on the extracted script — clean (doc's own M4.7 note:
  this check alone caught a real corrupted-file bug once).
- **Actually opened in a real browser** (headless Chrome via the Chrome
  DevTools Protocol — no `chromium-cli` or Playwright installed on this
  machine, so a small throwaway CDP driver script did the `nav`/`click`/
  `screenshot` loop by hand) **against the real running server and real
  camera feed.** Screenshot confirms: the table renders right-way-up
  (the "Curly Noodles"/"Long Noodles" labels and the logo read correctly,
  where the raw feed is upside-down), background clutter is mostly
  cropped away, and all eight green bin-rect outlines sit correctly on
  the physical trays — which only happens if `rectToDisplay` has the
  correct sign, so this is real evidence for the transform, not just the
  video crop.
- **Not verified: an actual pointer drag through the browser.**
  `toCam()`'s ROI branch is the algebraic inverse of `toDisplay` (proved
  standalone) and `toDisplay` is now empirically confirmed correct by the
  screenshot above, so `toCam` is correct by construction — but no
  session actually dragged a rect on a live cropped view and watched it
  track the cursor. A CDP-driven synthetic drag was attempted and
  abandoned: the app's `rects`/`mode`/`toCam` live inside the page's IIFE
  closure, invisible to `Runtime.evaluate`'s global scope, and reaching
  them without adding debug-only globals to production code would have
  needed pixel-scanning the canvas instead of reading state directly —
  judged not worth the added script complexity given the algebraic proof
  already in hand. Owed before this is trusted with a real drag.
- oF (`UiLayer.cpp`) untouched. `kUseCoreRects` stays `false` — **do not
  flip it**, both because that policy already required a fresh calibration
  with enough real inliers AND a human Verify, and because the homography
  on disk right now predates the room-light discovery above and has not
  been re-verified since.

**Next session starts here:** fix the room light (or confirm it was a
one-off — turn it off, re-run calibration several times, not just once,
before trusting the number), then doc §12.6's Verify step on whatever
calibration results. Once both are done: flip `kUseCoreRects`. Separately
owed, lower priority: an actual browser-driven drag test, and a decision
on whether `roi_margin_px` (currently borrowed from `match_gate_px`) needs
its own tuning once several clean runs exist to tune it against — the one
run that came in at exactly 10 (the floor) suggests the margin is not
generous to spare.

**The above ("next session starts here") was never picked up — M4k below
made it moot the same day by removing dot calibration outright instead of
continuing to fight the room light.**

## M4k — dot calibration deleted outright; replaced by a manual
4-corner drag tool (2026-08-12)
Same day as M4j, later. Rather than keep fighting the room-light
contamination M4i/M4j diagnosed, the whole dot-projection approach —
`core/dotcal.py`, `classifier/dots.py`, their tests, the dot-cal wire
messages, the `calibrating` overlay, `config.calibration` — was **deleted
outright, not disabled** (six commits, `92b3fdb`..`4d16ec3`). Replaced by
an operator placing the table's 4 real corners directly on the live feed:
`GeometryStore.fit_from_corners()` solves `H` from those 4 correspondences
against the 4 known stage corners — no pattern, no detector, no dark room,
nothing that a room lamp can contaminate. `docs/HOTPOT_ARCHITECTURE_v3.md`
was updated throughout in the same pass (wire protocol, config schema,
§12.6/§12.7, the I9 lighting exception, the banner precedence table, the
M4 build-item list, §24.1 marked superseded rather than rewritten away —
read §24.1 before assuming dot calibration still exists anywhere).
**`kUseCoreRects` in `UiLayer.cpp` and its whole "flip it only after a
real dot-cal RMS and a human Verify" condition are gone with it** — the
oF-side dead dot-overlay render path was deleted in the same pass
(`484eeb9`).
Built in 4 more steps the same day: the math (`fit_from_corners`, correspondence
pinned to a **fixed front-left/front-right/back-right/back-left click
order**, never inferred from screen position — the exact 180°-mount trap
`order_quad` hit in M4i, avoided by construction this time), the wire
message (`manual_calibrate`, synchronous — no classifier round trip, so
it can't block a tablet's thread), a first click-4-points UI (superseded
the same day, see M4l), and persistence (`corner_points`/
`view_rotation_deg` added to `state/homography.json` and a new
`state/view_rotation.json`, so a future UI has something to seed itself
from and re-open a tool that fixes one corner rather than starting over).
666 tests passing at the end of this run.
**Not observed anywhere in this section** — none of M4k was run against a
real camera or a real browser; that first happened in M4l below.

## M4l — the drag-corner Setup tab UI, run for real, and the bugs that
run found (2026-08-12)
Closes the drag-corner rebuild plan M4k started: the click-4-points tool
was replaced by a **persistent draggable quad** (`4d16ec3`) — 4
fixed-role handles (near-left/near-right/far-right/far-left, same order
as `fit_from_corners`), a ~4x magnifier while dragging a handle, Cancel
discarding a drag with nothing sent to core, Confirm sending the same
`manual_calibrate` shape as before, and a Rotate control (0→90→180→270)
so the operator wasn't working from an upside-down feed on this rig's
known 180° mount. Once corners are confirmed, the Setup tab's resting
view became a 2-triangle affine warp of the last confirmed quad into a
flattened rectangle (`drawRectifiedPreview`) — display-only, independent
of the real projective `H_cam->stage`.
**This was the first UI in the whole drag-corner rebuild actually opened
in a browser, and it immediately surfaced three real bugs** (`7812321`):
(1) the magnifier drew almost entirely black — `drawRotatedVideo`'s
`drawImage` call hardcoded its destination size to the *source* rect,
ignoring the requested zoom, so a 52px crop painted at 1x into one corner
of the 208px magnifier canvas; (2) the rectified preview came out
upside-down — traced to operators having no way to tell which of the 4
identical handles was which role, compounded by `defaultCornerPoints()`
building its default layout with no rotation compensation; fixed (partly)
by labelling each handle on-canvas; (3) Rotate/Confirm were visible even
before "Set table corners" was tapped — the `.hide` class the JS toggled
onto them had no matching CSS rule at all, so the gating was a no-op.
**A second real-browser pass the same day found the label fix in (2) was
papering over the actual bug, not fixing it** (`e82bb86`): the developer
re-tested and the inversion was still there. The real cause was
`drawRectifiedPreview`'s own fixed mapping — it put "near you" at the
rectangle's TOP and "far side" at the BOTTOM, backwards from the floor-
plan convention (near = towards the viewer = bottom) every other view in
this app uses. No amount of accurate dragging could have produced a
right-way-up result; the per-handle labels from bug (2) were a genuine
fix for a real but secondary problem. Corrected by flipping the
destination rectangle's Y, verified standalone in Node against
`affineFrom3Points` directly (not `drawRectifiedPreview` itself, so the
check couldn't pass by construction against its own code path).
**Also per the developer's explicit direction in this same pass, not a
bug fix:** Rotate is gone entirely — corner editing now shows the
camera's real, unmodified orientation with no display transform, and an
operator reads physical near/far/left/right off the real table (the same
principle the fixed-role handles already relied on, extended to the
display itself). `rotatePoint`/`unrotatePoint`/`rotatedDims`, the
90°/270°/180° cases of what was `drawRotatedVideo`, and the
`set_view_rotation` send are all **deleted outright** client-side, not
left dormant — this codebase's usual rule for a removed feature. Server-
side `view_rotation_deg`/`set_view_rotation` (`GeometryStore`,
`core/main.py`) are untouched and simply unused now; nothing client-side
sends the message any more. "Set table corners" now hides itself while a
quad is open, replaced by Cancel (discard) and Confirm, rather than all
three buttons showing at once. And — the biggest functional addition —
**the Live tab now shows the same flattened rectangle the Setup tab's
resting view does**, once corners are confirmed: reuses
`drawRectifiedPreview` directly rather than a second implementation, so
the two tabs can never disagree about what "flattened" looks like.
`docs/HOTPOT_ARCHITECTURE_v3.md` §12.6/§12.3 were **not** updated in this
pass — owed, see below.
Verification for both bug-fix commits: `node --check`, every
`getElementById` cross-checked against the DOM by script, the near/far
mapping fix checked standalone against `affineFrom3Points` (near/far
edge midpoints land exactly on the rectangle's bottom/top, both triangles
agreeing at the seam), and a throwaway DOM shim (not committed) driving
the real extracted script through actual button clicks — confirmed
Cancel/Confirm start hidden and reappear/hide correctly around a real
`cornersBtn.click()`/`cornersCancelBtn.click()`. `python -m unittest
discover -s python/tests` — 666/666 passing throughout (no Python
touched by M4l). **Nothing in M4l has been verified on the rig** — every
fix and the Live-tab flattening are reasoned from screenshots and node/
DOM-shim checks, not a real camera and a real browser session.
**Doc gap — closed same day, after the bug fixes above:** §12.6 and
§12.3 now describe the Cancel/Confirm quad and the Live-tab flattening.
**Next session (per the developer): bin boxes.** M4.4's own commit
message already named this — "the green boxes will look cosmetically
misaligned against the warped picture underneath until a later, separate
step reworks bin-rect editing into shared grid lines instead of 8
independent boxes" — and it is now the last visibly-unfinished piece of
the Setup tab: 8 rectangles are still dragged independently on a feed
that, since M4l, can be either the raw camera view or the flattened
rectangle depending on whether corners are confirmed yet, and the bin
rects have not been re-examined against that change at all.

## M4m — bin boxes reworked: two independent grids replace 8 shared rects
(2026-08-12)
Answers M4l's "next session" note, but not the way it was framed. Brainstormed
with the developer first (this session opened on "we need to fix the bin
boxes" and went through several rounds before any code moved — worth reading
the conversation, not just this summary, if the reasoning below seems to
arrive fully formed). Three developer decisions, in the order they were made:

1. **Bin crop (classifier + hand hit test) and bin projection (halo/fluid)
   are two genuinely separate geometries, not one derived into two spaces.**
   The old model (doc §5.3 as originally written) had core own camera-space
   rects as ground truth and derive stage-space rects through `H_cam→stage`
   for oF. That already carried a known failure mode (M4h/M4i's `rms_px: 0.0,
   n_points: 4` TRAP) and a known artifact (the seed growing rects ~26% from
   boxing through a homography twice). The developer's call: stop deriving
   one from the other at all. A camera-space grid and a projector-space grid,
   each independently authored by a human looking at the space it actually
   feeds — the same TRAP discipline §5.3 already argued for, just applied
   twice instead of once. `H_cam→stage` stays, but only as infrastructure for
   the table-crop warp, not as a bridge between two bin geometries.
2. **Grid lines, not 8 independent rects, in both spaces.** The developer's
   reasoning: independent rects can't be kept level with each other — nothing
   stops bin 1's top edge from disagreeing with bin 0's, invisible in a list
   of numbers, visible as one bin sitting higher than its row-mates on the
   real table. 4 horizontal + 8 vertical lines (matching the physical 2-row,
   4-column layout) make that impossible by construction: every bin in a row
   shares that row's top/bottom line, every bin in a column shares that
   column's left/right line. This is also, unprompted, a much better fit for
   `docs/legacy/bin_offsets.json`'s `hLineDeltaMM`(4)/`vLineDeltaMM`(8) shape
   than the rect version ever was — the legacy file was describing a grid the
   whole time.
3. **The camera grid needs the camera; the projector grid does not, ever.**
   The camera grid is dragged on the "table crop" — the raw frame warped
   through the corner-calibrated `H_cam→stage` via the new
   `common.geometry.warp_frame_to_stage` (`cv2.warpPerspective`), so a pixel
   in it sits where the projector's own same-coordinate pixel lights. Once
   warped, that frame is what MediaPipe will run on (M5, not built),
   what the classifier crops from, and what core will hit-test a hand cursor
   against for "entered bin i" (M5 again). The projector grid (a later,
   separate step — not built this session) is dragged by watching the actual
   light on the actual table, no camera, no homography, closing its own
   Verify loop independently.

**Built this session, camera-grid side only — the projector grid is
deliberately out of scope, per the developer's own phasing ("crop rectangle
first, then projector space lines").**

- `common/geometry.py`: `warp_frame_to_stage(frame, h, size)` —
  `cv2.warpPerspective`, imported locally like `fit()`. 3 new tests.
- `core/bin_grid.py` (new): `BinGrid` (4 h-lines + 8 v-lines → 8 rects, every
  bin in a row/column sharing its line by construction), `BinGridStore`
  (load/save/seed/verify, one file per grid, generic — instantiated once
  today for `state/bin_grid_camera.json`, ready for a second instantiation
  against `state/bin_grid_projector.json` when that step happens),
  `cad_bin_grid_stage()`/`legacy_bin_grid_stage()` (pure line arithmetic —
  **no homography needed to seed any more**, unlike the old rect version's
  `H^-1` round trip, and no more 26%-growth artifact since there is no rect
  to box twice). 27 new tests.
- `core/geometry_store.py`: stripped down to the homography, corner points
  and view rotation only — `cam_rects`/`stage_rects`/`set_cam_rect(s)`/
  `seed_cam_rects_from_table`/`has_rects`/`calibrated`/rect persistence all
  removed, not left dormant (this codebase's usual rule). Its own module
  docstring now points at `bin_grid.py` for where bin ownership went.
- `core/main.py`: `self.camera_grid` (a `BinGridStore`) sits beside
  `self.geometry` (homography only) — two stores, neither knowing about the
  other; `is_calibrated` is now a lambda combining both. Wire messages
  renamed `set_rects`/`seed_rects`/`verify_rects` → `set_grid`/`seed_grid`/
  `verify_grid`, payload `rects:[...]` → `h_lines:[4]`/`v_lines:[8]`, replies
  `rects_result` → `grid_result`. `_handle_capture` now sends `h` and
  `stage_size` alongside the grid-derived `rects` (core still never touches
  a frame — the classifier is what warps). `state.bins[].rect` sent to oF is
  unconditionally `null` for now — that field is the projector grid's job,
  not built yet; oF already treats `null` as "fall back to
  `TableGeometry.h`'s CAD layout" so this is unchanged behaviour, not a
  regression.
- `classifier/main.py`: `_capture` validates `h`/`stage_size`, warps the raw
  frame before cropping. New test (`test_capture_warps_before_cropping`)
  checks an actual non-identity homography moves the crop content, not just
  that cropping still works at identity.
- Web UI (Setup tab): the 8 independently-dragged rects and their
  resize-handle-at-bottom-right-only drag model are gone. The bin grid is now
  edited by dragging lines directly on the RECTIFIED preview canvas —
  `toStage()`/`hitTestLine()` replace `toCam()`'s rect branch/`hitTest()` —
  and **editing is only possible once table corners are confirmed** (gated
  in `applySetupGating`, `onPointerDown`). This is also what fixes the
  original bug report that started this session ("the green box and the
  captured image are not the same"): the grid is now drawn and dragged in
  exactly the one canvas it is defined in, never overlaid on a different
  picture than the one it was placed against. Verify's own copy changed to
  match — "look at the rectified picture above, not the physical table",
  since this grid does not reach oF yet.
- Web UI (Capture tab): `drawCrops()` now samples from an offscreen
  `rectifiedCanvas` (built the same way the Setup/Live tabs' own rectified
  preview is, via the existing `drawRectifiedPreview`) instead of the raw
  `setupImg` — the other half of the "box ≠ captured image" bug, since crop
  rects are measured in the rectified canvas's space. Documented as still
  only an *approximation* of the server's true crop: the browser warps with
  a 2-triangle affine, the classifier warps with a real OpenCV projective
  transform. `.crop-grid`'s CSS changed from `auto-fill, minmax(200px,1fr)`
  (developer report: "should be 2×4, a single row breaks into two") to a
  fixed `repeat(4, 1fr)` (2 columns under 820px) — auto-fill picked its
  column count from container width independently of the 8-card, 2-row-of-4
  physical layout the cards are appended in, which could split unevenly
  instead of ending cleanly at the row boundary.
- `docs/HOTPOT_ARCHITECTURE_v3.md` §5.3 and §4.7: addenda marking the old
  "one shared rect, two derived spaces" model superseded, with the new one
  and the reasoning, rather than a line-edit pass through the whole doc
  (matching M4k's own precedent for a change this size — §8.4/§12.6/§12.7
  still describe the old rect UI and are *not* updated this session, flagged
  explicitly in the §5.3 addendum so nobody mistakes silence for currency).

**Verified:** `python -m unittest discover -s python/tests` — 682/682
passing, across `test_geometry.py` (+3), the new `test_bin_grid.py` (27),
and rewrites of `test_geometry_store.py`/`test_core_main.py`/
`test_classifier_main.py` to drop the old rect-based cases and cover the
grid instead — the starting count from before this session was not recorded,
so no net delta is claimed here, only the final passing number, actually run.
`node --check` on the extracted `<script>` block of `index.html`. **Not run
on the rig, not opened in a real browser.** Every claim about the Setup/
Capture tabs' actual on-screen behaviour is reasoned from the code and the
node syntax check, the same honest gap M4l flagged for its own drag-corner
UI before a real-browser pass caught three bugs static checks couldn't have.
A real-browser pass on this grid UI is owed before it is trusted, same as
M4l's was.

**Not done, deliberately, per the developer's own phasing:** the projector
grid (`state/bin_grid_projector.json`), oF wiring to read it, and M5's
hand-hit-test against the camera grid. `core/bin_grid.py`'s `BinGridStore`
and `BinGrid` are already generic enough that the projector grid should be a
second instantiation plus an oF-side reader, not a redesign — that is the
next session's starting point.

## M4n — the projector grid: `BinGridStore`'s second instantiation, wired
to oF (2026-08-12)
Answers M4m's own "next session's starting point" note, exactly as scoped
there: a second `BinGridStore` instantiation plus an oF-side reader, not a
redesign. `core/bin_grid.py` needed zero code changes — it was already
generic enough, per its own docstring.

- `core/main.py`: `self.projector_grid = bin_grid.BinGridStore(
  projector_grid_path)` sits beside `self.camera_grid`, neither knowing
  about the other, matching `bin_grid.py`'s "never derived from each
  other" rule. Three new wire handlers, `set_grid_projector`/
  `seed_grid_projector`/`verify_grid_projector`, are `_handle_set_grid`/
  `_handle_seed_grid`/`_handle_verify_grid`'s own template aimed at the
  new store, replying `grid_projector_result` — same setting-mode gate,
  same validation, same "Save is explicit" rule. `_bin_msg`'s `rect` field,
  hardcoded `None` since M1, now reads `self.projector_grid.rects()[i]` —
  **this is the one line M4n exists to change.** A new `_projector_grid_msg()`
  (`t: "projector_grid"`, no homography fields — this grid was never
  solved from anything) is sent on join and after every change.
- **One design call worth flagging, not asked about first because it
  follows directly from a rule already on the books:** whether `_bin_msg`
  should gate on `projector_grid.verified_at` rather than plain `has_grid`,
  the way the old dot-calibration-derived rect arguably should have.
  Decided against — `has_grid` is what every other consumer in this
  codebase gates on (`_handle_capture`, `_check_calibration_complete`),
  `verified_at` is documented everywhere else as a human-confidence flag
  that must never become a functional gate (`_handle_verify_grid`'s own
  docstring), and unlike the old rect, nothing here is *derived*: a
  human types a number while looking directly at the table it moves on,
  which is the doc §5.3 TRAP's own cure, not a new instance of it. Also
  practical: gating on `verified_at` would have broken the live-nudge
  workflow the projector grid exists for — `seed`/`set` update the
  in-memory grid immediately and `_bin_msg` reads it on the very next
  ~16ms state tick, which is the only "preview" a no-camera grid can have
  (watching the real light move). Gating that preview on a verification
  that can only happen *after* someone has watched it move would be
  circular.
- `of/hotpot-table/src/UiLayer.cpp`: `kUseCoreRects` — OFF since
  2026-08-12 as a kill-switch against the dot-calibration TRAP
  (`geometry.calibrated: true, rms_px: 0.0, n_points: 4` computed from an
  unresolved camera mount) — is flipped back to `true`. The switch's
  original justification no longer applies to what `_coreRects` carries:
  dot calibration is deleted outright (M4k) and the projector grid has no
  homography in its chain at all, so "core has a rect" and "core's rect
  is trustworthy" cannot come apart the way they did for the old one. The
  comment at the switch is rewritten to explain this rather than just
  flipping the bool silently — read it before touching this again.
  `TableGeometry.h`'s top comment (referencing the long-gone
  `state/bin_rects.json`) is also corrected to describe the two current
  grids and when oF actually uses a core-sent rect vs. its own CAD
  fallback.
- Staff view (`index.html`): a fourth Setup-tab card, **Projector grid**,
  below Verify. No canvas — per `bin_grid.py`'s docstring there is no
  camera image to drag a line onto, so this is 12 plain number inputs (4
  rows, 8 columns, labelled by bin_grid.py's own line order) plus Save /
  Load measured layout / Verify Yes-No, following the camera grid card's
  buttons one-for-one. Gated on setting mode only — **not** on confirmed
  table corners, which the camera grid card needs and this one does not.
  A `pgDirty` flag (typing's equivalent of the camera grid's `gridDrag`)
  stops an incoming `projector_grid` broadcast from overwriting a field
  mid-keystroke, cleared on Save, on Seed, and on leaving setting mode
  (so an abandoned edit is not carried into the next session as a stuck
  flag that blocks every future broadcast from ever populating the
  fields).
- 15 new tests in a new `TestProjectorGrid` class (`test_core_main.py`),
  built as `TestSetupTabGrid`'s own template with no homography installed
  in `setUp()` at all (deliberately — a test that installed one anyway
  could hide a handler that wrongly required it). Two cases that class
  does not have: `test_the_saved_grid_reaches_the_state_message_rect`
  and its mirror `test_the_camera_grid_alone_does_not_reach_the_rect`,
  which is the M4m-era `test_stage_rect_stays_none_on_the_state_message`
  still passing unchanged — the camera grid still never reaches
  `state.bins[].rect`, only the projector grid does now. Also
  `test_a_seeded_but_unsaved_grid_still_reaches_the_state_rect`, which is
  the live-nudge-preview design decision above, made falsifiable. Adding
  a sixth join message (`projector_grid`, between `geometry` and
  `capture_info`) broke three pre-existing tests that hardcoded the join
  seed's message count or fixed positions
  (`test_a_joining_tablet_is_told_the_mode_as_well_as_the_pips`,
  `test_the_join_seed_tells_a_tablet_the_geometry`,
  `test_the_join_seed_carries_the_capture_tabs_defaults`) — all three
  updated to expect six, not five, and every other `for _ in range(5):`
  join-drain loop in the file bumped to 6 for the same reason, even where
  the extra unread message would have been harmless (later code filters
  by type). 697 tests pass, `python -m unittest discover -s python/tests`.
  `node --check` on the extracted `<script>` block, plus a small script
  cross-checking every `getElementById` call against the HTML's actual
  ids — no drift.

**What is NOT verified, and matters more here than in most sessions,
because this change flips a switch the previous session deliberately
left off:**
- **No real projector, no real camera, no real table.** Everything above
  is reasoned from code, unit tests, and a syntax check — the same honest
  gap M4l and M4m both flagged for their own UI work before a real pass
  caught bugs static checks could not. A human has never dragged this
  card, never watched a rect move on the actual table, and never run
  Verify against real light.
- **The oF build was only compile-verified, not link-verified.** Debug
  x64 msbuild compiled every changed file (`UiLayer.cpp`, `ofApp.cpp`)
  with 0 errors, twice. The link step failed both times because
  `bin\hotpot-table_debug.exe` was held open by a running instance that
  `run.py`'s supervisor immediately respawned after it was closed once
  (with the developer's confirmation) — stopping the supervisor stack
  itself to force a clean link was not asked for and was not done. A full
  clean build producing a fresh `hotpot-table_debug.exe` is still owed
  before `kUseCoreRects = true` is trusted on the rig.
- **`verify_grid_projector` recording an answer is not the same claim as
  the grid being right**, same as it has never been for the camera grid —
  see `_handle_verify_grid_projector`'s own docstring.

**Still not built, unchanged from M4m's own list:** M5's hand-hit-test
against the camera grid.

## M4n-fix — the first real run, three developer-caught problems (2026-08-12)
M4n was reasoned from code and tests only. It was then actually opened on
the rig for the first time, and — same pattern as M4l and M4m before it —
that immediately surfaced problems no static check could have:

1. **"Not getting updated when I save."** Not a code bug: `run.py`'s
   supervisor (PID tree rooted at the standing `python run.py`) had
   `hotpot.core.main` and `hotpot-table_debug.exe` running from BEFORE
   M4n landed on disk — Python doesn't hot-reload a running process, and
   the exe was never actually relinked (M4n's own session log already
   flagged the failed link, caused by this same stale exe holding its
   own output file open). Fixed with `python run.py --stop`, a clean
   `msbuild` (linked this time, nothing had the file open), and
   `python run.py` again. **Lesson for next time a change touches
   `core/main.py` or oF: check `Get-CimInstance Win32_Process` for a
   live `run.py` tree before concluding a wire or render change "isn't
   working" — it may just not be running yet.**
2. **The number-input UI was bad — developer's word.** M4n's 12 plain
   `<input type=number>` fields are gone. This project already built and
   deleted exactly the right UX for this once (git history:
   `640ec7a` "Nudge the whole bin pattern by hand", `5b152c3` "Adjust the
   bins as a grid of lines, not eight rectangles" — arrow keys nudge a
   selected line, highlighted, with Shift for a bigger step) — deleted at
   the M0.1/M1.4 rewrite because it was oF-local keyboard input, and
   "core owns all state, oF is a dumb renderer" is a hard invariant that
   local keyboard state in oF violates outright. The UX is back, rebuilt
   on the wire instead: the Projector grid card is now 12 selectable rows
   (click, or Tab between them — real focusable elements, so Tab order is
   free), and the selected row's arrow keys (↑/↓ for a row line, ←/→ for
   a column line, Shift for ×10 the step) send `set_grid_projector`
   immediately on every press — no separate Save. `pg-line-list`/
   `pg-line-row` in `index.html`, gated the same way every other Setup-tab
   control is (`pgRowsOn`, checked inside the row handlers since a `<div>`
   has no native `.disabled`).
3. **"Why do we need a Verify confirmation — saving already means it
   lined up?"** Asked about both grids; the developer's call, after
   hearing the distinction, was to drop Verify from both. Removed
   entirely, not hidden: `bin_grid.py`'s `BinGridStore.verified_at`/
   `mark_verified()`/`clear_verified()` are gone (this codebase's usual
   rule — not left dormant), `verify_grid`/`verify_grid_projector` wire
   messages and their four handlers are gone, `grid_verified_at` is off
   the `geometry` message and `verified_at` off `projector_grid`, and the
   Setup tab's whole standalone "Verify" card is deleted along with the
   projector-grid card's Yes/No pair. **The reasoning for why this was
   safe to drop is worth keeping, because it is not the same for both
   grids and a future change should not assume it generalizes:** the
   camera grid's Verify existed for a real, doc §5.3 TRAP reason — an
   operator drags on the RECTIFIED PICTURE, which is a genuinely
   different space from the REAL TABLE, and a bad homography can make
   that picture look perfectly fine while being wrong (the exact
   `rms_px: 0.0, n_points: 4` incident CLAUDE.md's M4h/M4i already
   recorded). Dropping it there is a real, named tradeoff — one fewer tap
   against losing a guard that has already caught a real failure once —
   made on the developer's explicit call, not a code-side realization
   that it was always unnecessary. The projector grid never had that
   two-space gap: the operator is looking at the real table while
   nudging it, not a proxy for it, so there was nothing left for a
   separate step to check — dropping it there cost nothing. **Doc
   §12.6 still describes a Verify step for both grids and has not been
   updated to match** (same "flagged, not line-edited" precedent M4k/M4m
   both used for a change this size).

Verified: 689/689 tests (`python -m unittest discover -s python/tests` —
8 fewer than M4n's 697, the removed verify-path tests, all deleted rather
than weakened). `node --check` plus the same getElementById/id
cross-check script, and a grep confirming no leftover reference to any
removed identifier (`verifyYesBtn`, `pgSaveBtn`, `pgVerified`, etc.).
**Run on the rig this time, not just reasoned about:** `run.py --stop`,
a real `msbuild` that actually linked, `python run.py` again — camera,
core (COM5 open), tracker, classifier, voice and `of` all reached
HOTPOT-READY / StateLink-connected in the merged log.

**Physically confirmed, after the restart above:** the developer opened
the Setup tab, saved the projector grid, and watched it reach the real
table — "Projector grid saved," a line actually moving under an arrow
key, on the projected surface, not a framebuffer. This is the first
thing in the whole M4n/M4n-fix arc confirmed by physical observation
rather than reasoned from code or a syntax check.

**Also physically confirmed, same session:** the camera grid (drag lines
on the rectified feed, Setup tab, M4m — "not opened in a real browser"
was M4m's own caveat, now closed) and the Capture tab (photograph bins,
export a labelled dataset, M4.7/§12.7). Both "all good," developer's
words. Of doc §21's M4 acceptance list, what remains open: the
`field_level`/camera-exposure sweep (exposure is already locked at -6
per the boot log, but nobody has done the deliberate "look at a bin crop
under swept light" check the doc asks for — see the answer given
in-session, since `field_level` is not actually wired into oF's render
at all yet, only into Python-side dataset metadata, which the developer
now has that explanation of); the keystone-stale warning (mechanism is
built and believed correct — `geometry_store.keystone_is_stale` compares
oF's live keystone fingerprint against the one recorded at the last
homography solve — but the developer sets `keystone.json` by hand,
once, with no in-app tool to bump it, so the scenario this warns about
does not arise in normal use and has not been deliberately triggered to
confirm the warning actually fires); and Edge Impulse training, blocked
on hardware on order, not a code gap.

## M4o — burst capture: frames+interval instead of frames+total period,
plus a live counter and countdown (2026-08-13)
Developer feedback after using the Capture tab for real: the burst was
"a good idea, but very difficult to use" — the tablet asked for frames
and a **total period**, so raising the frame count silently shrank every
gap, backwards from what an operator reaching for more frames usually
wants (more time per gap to rearrange the tray, not less). And there was
no feedback at all while a burst ran — no count, no sense of when the
next photo would land.
- **Wire:** `capture`'s `seconds` (total period) is now `interval`
  (seconds BETWEEN shots, no division). `classifier/main.py`'s old
  `gap = seconds / burst` is gone; `gap = interval` directly.
  `DEFAULT_INTERVAL_S = 2.0`, `MAX_INTERVAL_S = 30.0`. §4.7/§12.7 updated.
- **New message, `capture_progress`** (classifier → core → every
  tablet): one per shot, `{shot, burst, interval}`, sent only for a real
  burst (`burst > 1` — "Capture all" has nothing to count up to). **Not**
  the command's reply — `core/main.py`'s `_send_classifier_cmd` waiter
  only resolves on `captured`; a new `_on_message` branch broadcasts
  `capture_progress` straight to the web tablets instead, the same
  pattern `landmarks` already used for a live aside that isn't state.
- **Interruptible cancel, found while wiring the progress message in:**
  the gap wait was `time.sleep(gap)`, uninterruptible for up to 30s now
  that `interval` can be set that high. Changed to `self._cancel.wait(gap)`
  so a `stop` lands within a tick instead of waiting out the rest of a
  long gap.
- **Tablet UI:** the "over N seconds" field is now "every N seconds"
  (`burstInterval`, replacing `burstSecs`), and a counter+countdown row
  (`#captureProgress`) appears under the burst controls once a real burst
  starts — "Shot 4 of 10" plus a locally-ticking "next photo in 2s",
  reset on every `capture_progress` message rather than trusted to stay
  in sync with the server on its own. Hidden via this codebase's usual
  `.hide` class, not the `hidden` attribute — the earlier draft used
  `hidden` with a `display:flex` class rule already on the same element,
  which is the exact "no matching CSS rule" class of bug M4l's own
  `.hide` note was written to avoid, and it would have shown the row
  permanently regardless of the attribute.
6 new Python tests (5 in `test_classifier_main.py` — per-shot progress
content, more-frames-does-not-shrink-the-gap, the interruptible cancel,
both clamps; 1 in `test_core_main.py` — `capture_progress` relayed live
without resolving the reply), plus 5 existing ones updated for the
`seconds`→`interval` rename. 913 tests pass,
`python -m unittest discover -s python/tests`. `node --check` on the
extracted `<script>` block, plus a script cross-checking every new
`getElementById` id against the DOM — clean.
**Not observed on the rig or in a real browser** — same honest gap every
Capture-tab change before this one has carried; reasoned from tests and
the syntax/id checks only.

## M4p — camera no longer auto-locks WB/exposure/focus on every boot
(2026-08-13, not a doc build item — a dev-loop bug fix)
Developer report: every `run.py` start opened the camera with a yellow
tint, fixable only by manually going to the dev panel and resetting white
balance — and it came back on the next restart.

**Root cause: `_lock_controls` (both `WindowsCapture` and `V4L2Capture`,
`camera/capture.py`) auto-locked on every open with no explicit prior
lock, not just once.** With no `"locked": true` in
`state/camera_settings.json`, it let auto-exposure/WB/focus run for
`AUTO_SETTLE_S` (1.5s), then froze whatever they'd reached at that instant
and wrote `"locked": true`. 1.5s does not reliably let white balance
settle, so a boot could freeze a bad (yellow) value. Fixing it via the dev
panel's "Auto white balance" writes `"locked": false` back to the file —
which just re-armed the *next* boot to repeat the same 1.5s-converge-
then-lock cycle. A loop, not a one-off bad value — the same shape as the
2026-08-12 yellow-cast bug (M4h) but one layer further in: that fix
stopped a *stale* unlocked value from being reapplied; it never questioned
locking fresh from a short settle window on every single boot.

**Fix: no prior lock means nothing is touched at all.** Every control is
left exactly as `open()` finds it — continuous auto, same as the OS
camera app — until a human deliberately locks one (dev panel, or a future
dataset-capture flow). A genuine `"locked": true` prior is still applied
verbatim, unchanged. Removed `AUTO_SETTLE_S` and the `time` import
entirely from `capture.py` — nothing in the module sleeps any more.
**Trade-off, explicit, developer's call:** doc §6.6's "sweep on the rig,
then freeze" reproducibility guarantee for M4's dataset capture now
depends entirely on a deliberate lock (dev panel or a future explicit
flow) — nothing auto-freezes a baseline on first boot any more. Applied to
both backends for consistency (they're built to mirror each other); the
V4L2 half is unverified against real hardware, same as the rest of that
class.
17 tests in `test_camera_capture.py` rewritten to match (no more
`time.sleep` patches — `capture.py` no longer imports `time` at all): the
converge-then-lock tests became leave-running tests, `TestWindowsCapture
Controls`'s shared fixture now reports auto state as `None` (unknown)
right after `open()` since nothing was ever set, matching the new honest-
readback behaviour. 912 tests pass,
`python -m unittest discover -s python/tests`.
**Not yet re-observed on the rig** — reasoned from the code and the
2026-08-13 evidence trail (M3's own session notes) that a short settle
window can converge to a bad value; the next `run.py` on this machine is
the actual check.

## FLUID SIM — corner-clip bug found and fixed (2026-08-14, not an M8
build item — M8 (the renderer/fluid milestone) has not formally started;
this was debugged via `kFluidDebugMouseOnly`, the mouse-stands-in-for-a-
hand isolation switch `ofApp.cpp` already had for exactly this kind of
tuning)

**Symptom:** the fire/fluid sim only ever rendered into a fixed
640x360-ish sub-rectangle in the top-left of a 1920x1080 window — a
*hard* 1px edge (confirmed by direct pixel readback: solid flame colour
right up to the boundary, pure white one pixel later), not a soft
diffusion falloff. Reproduced identically across every monitor tested.

**Root cause, and it is NOT in this repo.** `ftJacobiDiffusionShader`
(ofxFlowTools) ships two fragment shaders — `glFour()` (GLSL 410) and
`glTwo()` (GLSL 120) — picked at construction by
`ofIsGLProgrammableRenderer()`. hotpot-table's `main.cpp` never calls
`setGLVersion`, so oF hands it the fixed-function 2.1 renderer and
**`glTwo()` is what actually runs.** fireTest's own `main.cpp` calls
`setGLVersion(4, 1)`, so it has only ever run `glFour()` and has never
exercised this path — which is why porting fireTest's fluid code
byte-for-byte here still hit the bug.
`glTwo()` declares a `scale` uniform and never uses it: obstacle lookups
sample the 640x360 SIM-resolution obstacle textures at raw
DENSITY-space `st` (0..1280, 0..720) with no scaling. `GL_TEXTURE_RECTANGLE`
clamps to edge, and `ftFluidFlow::initObstacle()` paints the texture's
outer 1px border to `1.0` (blocked). Every fragment past x=640 or y=360
therefore clamps onto that border, hits `if (oC == 1.0) { gl_FragColor =
vec4(0.0); return; }`, and writes hard zero — 20x/frame, since
`viscosity.density = 1.0` runs the diffuse loop every frame. That is the
exact clip: a 640x360 region, a hard 1px edge, scaling proportionally
with any monitor's resolution, `viscosity.density = 0` making it
disappear (no diffuse pass = no clipped writes), and the advect pass
unaffected (its own `glTwo()`, in `ftAdvectShader.h`, already scales
obstacle lookups correctly, same as `glFour()` does).
**This is also why the investigation kept measuring "correct" at every
layer** (viewport, FBO dims, quad vertices, even a hardcoded-solid-colour
shader body): every one of those experiments was performed on
`ftJacobiDiffusionShader::glFour()`, which is dead code in this binary
and was never compiled in. The C++/oF-API layer really was correct;
the bug was in the sibling shader source nobody was looking at.

**Fixed in `/c/openframeworks/addons/ofxFlowTools`** — a local editable
copy, **not vendored/pinned and not tracked by this repo's git history**:
`ftJacobiDiffusionShader.h`'s `glTwo()` now scales obstacle lookups by
`scale`, matching `glFour()` and `ftAdvectShader`'s own `glTwo()`
byte-for-byte. A diagnostic dedicated shader instance
(`jacobiDiffusionShaderDensity`, added mid-investigation to rule out a
shared-quad theory, which it did rule out) was reverted back to the
single shared `jacobiDiffusionShader` instance.
**Two known-related bugs in the same addon, named but NOT fixed:**
fireTest and fluidTest each carry their own separate ofxFlowTools copies
under their own `addons/` folders, both still with this exact `glTwo()`
bug — latent in fireTest only because it runs the GL4 path, not because
it was fixed there. And `ftBuoyancyShader`'s `glTwo()` also reads the
1280x720 density texture at unscaled sim-space coordinates — a real bug,
but a different symptom (biases/weakens buoyancy across the density
field, does not clip anything) — left alone as out of scope for this fix.

**This repo's changes**, both commits on `main`:
- `957ffbd` — removed all `DIAG` logging/GPU-readback diagnostics added
  during the investigation (`ofApp.cpp`, `FluidLayer.cpp`); kept two real
  fixes found along the way that are not the corner-clip bug itself:
  `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` in `main.cpp`
  (correct practice regardless of this bug), and `bin/data/display.txt`
  changed to `"1"` (the actual projector monitor index — GLFW's monitor
  enumeration order shifts as monitors are connected/disconnected; if
  this drifts again, check the startup log's "Detected N monitor(s)"
  listing and ask which physical monitor is the projector).
- `5977172` — `kFluidDebugMouseOnly` back to `false`, after the fix was
  **physically confirmed on the projected surface**: flame fills the
  whole 1920x1080 window, no corner clip. This is a real observation,
  not a framebuffer/reasoned check — the developer watched the table.

**Still owed:** the ofxFlowTools fix lives in an untracked local copy —
if that directory is ever reset, reinstalled, or replaced by a fresh
clone of the upstream addon, this fix is lost silently and the corner
clip comes back with no diff to explain why. Worth vendoring/patching
properly, or at minimum noting the fix needs reapplying, before that
happens. fireTest's and fluidTest's own ofxFlowTools copies still carry
the bug, unfixed, and would corner-clip the moment either one is built
with a non-programmable renderer.

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

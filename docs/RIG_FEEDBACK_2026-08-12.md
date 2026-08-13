# Rig feedback, 2026-08-12 — M5 tracker/hover/dwell, first real run

Developer feedback from running M5 (tracker, hover, dwell) on the physical
rig for the first time. Each item below is scoped to be picked up in one
fresh session, independent of the others — read CLAUDE.md and
`docs/HOTPOT_ARCHITECTURE_v3.md` first as always, then this file for the
one item you're working, per this repo's "one thing at a time" rule
(CLAUDE.md "HOW TO WORK HERE"). Cross-references between items are noted
where fixing one could affect another.

Positive, no action needed: **tracking response is pretty good** — the
tracker itself (landmark 9, hysteresis, role assignment) is not in
question here. Also **not a bug, confirmed correct**: the pointer role
appears on either hand, even with a bowl held in it — that's doc §11.3's
design (role follows handedness/incumbency, not "empty hand"); nothing to
fix.

---

## 1. Cursor hidden by the hand's own shadow — DONE, commit 0ef1fe9

Cursor sat exactly at the tracked point (landmark 9, palm centre) and the
hand's own body blocked the projector light there, so the cursor was
invisible under a visible hand. Fixed by offsetting the stage-space point
70mm toward the far edge in `tracker/main.py`'s `_to_stage` (upstream of
both core's hit test and oF's rendering, so the visible dot and whatever
it's hovering stay in agreement). **Not yet physically confirmed on the
rig** — the 70mm magnitude was chosen by reasoning (less than a hand
length, so it shouldn't overshoot a bin or widget), not measured. If it's
still wrong after a rig run, this is the file/constant
(`CURSOR_SHADOW_CLEARANCE_MM`) to retune — see the commit message for the
full reasoning before changing the direction or approach.

## 2. Cursor doesn't appear when the hand is near the table edges — RESOLVED (workaround), 2026-08-13

Developer applied a physical/operational workaround on the rig; no code
change landed for this item and the root-cause candidates below were
never investigated. Left in place as reference in case the workaround
doesn't hold under different conditions.

Only shows once the hand moves toward the centre, even though the hand is
clearly visible to the operator. Not yet diagnosed. Candidates to check,
roughly in order of likely cause:
- `tracker/tracking.py`'s 150px stage-space gate (`TRACK_GRACE_S` area,
  doc §11.3 step 1) — if a hand near the edge is jumping across that gate
  frame to frame (homography distortion is often worst at the edges of
  its fitted region), a track could be dropped and re-created instead of
  followed, which could look like "no cursor" if role/promotion timing
  doesn't catch up in time.
- The homography itself (`state/homography.json`, solved from 4 corner
  clicks) — accuracy is worst furthest from the fitted corners is not
  generally true, but worth ruling out with a printed/logged stage (x,y)
  for a hand at the edge vs the centre before assuming it's a tracking
  bug rather than a calibration one. CLAUDE.md's M4n-fix/M5 commit history
  has one already-found near/far axis bug in this exact homography path
  (commit caf79dd) — a reminder that spatial bugs here have precedent and
  need a physical, not just algebraic, check.
- Whether MediaPipe's own detection confidence drops for a hand partially
  off-frame at the camera's edge, upstream of everything above — check
  `backend_mediapipe.py`'s confidence thresholds
  (`min_hand_detection_confidence` etc.) against a logged `conf` value at
  the edge.
- Interaction with item 1's new offset: an edge hand pushed further
  off-stage by the 70mm shift could be a contributing factor now, not
  before. Check this after item 1 is confirmed on the rig, not before.

## 3. No dwell-select progress shown while hovering a bin — DECISION MADE, 2026-08-13, not yet built

**Developer's decision, resolving the "needs a product decision" question
below:** bins ARE dwell targets after all. Hover-red-on-enter stays
(unchanged). Continued dwell should accumulate progress, and on
completing dwell a window should open explaining the food item. This
reverses this item's original reading ("bins are not dwell targets at
all") — `core/hover.py`'s `bin_under`/dwell machinery exists and is
generic (see items 4-7's note below), so the accumulator itself doesn't
need inventing, but a bin has never been wired into `DwellTracker` as a
dwellable target (only the three widgets are today), and the
"window... explaining the food item" has no design yet (staff-view
modal? oF-rendered overlay? what content, sourced from where —
`Catalogue`/`data/catalogue.json` presumably). Scoped for its own
session; not started.

The bin outline goes red on hover (working as designed — hover-on-bin is
feedback only, doc §9.4: "Hover on a *bin* is feedback only. It never
bills."), and the cursor disappearing on top of a bin cutout is also
correct (`Stage`'s light pass stamps the cutout white, last — nothing can
render into it). But **bins are not dwell targets at all** — only the
three widgets (Done/Cancel/Language, `core/hover.py`) accumulate dwell.
Before treating this as a bug: confirm with the developer whether bins
were ever meant to be dwell-selectable (they aren't in the current
architecture — food is picked by physically removing weight, not by
dwelling), or whether the ask is a *different* kind of feedback on
hover (e.g. a lighter fill or pulse distinct from the red outline) rather
than a literal filling ring. This item needs a product decision before
code.

## 4. Cancel button clears the cart

`core/main.py`'s `_fire_widget` (~line 1145): Cancel calls
`fsm.cancel()`, and if that no-ops on an active cart, falls back to
`cart.reset_session()` — which is the same "clear everything" path the
staff view's own Cancel-order button uses, and is the documented M2.6
behaviour (CLAUDE.md: "`_handle_cancel_order` calls doc §9.1's own shared
`reset_session()`"). So today Cancel clearing the cart **is the
intended, coded behaviour**, not a stray bug — the feedback is that the
intent itself is wrong for the widget's actual purpose. Needs a decision
first: what should the Cancel *widget* (as opposed to the staff view's
Cancel-order control) actually do — abandon the current dwell without
touching the cart? Something else? Then implement against `hover.CANCEL`
in `_fire_widget`.

## 5. Done button does nothing

This is expected, not a bug: `_fire_widget`'s `DONE` branch
(`core/main.py` ~line 1155) explicitly logs "Checkout is M6 — no state
change yet" — the SELECTING -> BROTH/checkout flow is a future milestone
(M6), not built. The dwell ring filling and firing correctly (which is
what's being observed — "loading... is observed") is M5's actual scope
and is working. Nothing to fix here until M6 is scheduled; flagging so a
fresh session doesn't spend time hunting for a bug that isn't one.

## 6. Language box is non-responsive

Also expected, not a bug: `core/main.py`'s `_cycle_locale` and
`hover.widgets_for` disable the Language widget whenever fewer than 2
locales are loaded (`self.locales.available()`), and today only
`data/locales/en.json` exists — `zh.json` was never built (CLAUDE.md: "zh
locale data does not exist and must not be invented"). A disabled widget
does not accumulate dwell by design. This becomes actionable once a
second locale file exists; until then there's no code bug to chase.

## 7. Current widget set (Done/Cancel/Language) isn't the final one

Developer's own note: "all these buttons are not expected to be here, we
can change it later." No action — just don't over-invest in polishing
today's three widgets' exact placement/styling; `core/hover.py`'s
`widgets_for` is the one place their set and layout come from, so
changing the roster later is contained there plus `_fire_widget`'s
dispatch table.

**DECISION, items 4-7, 2026-08-13: remove all three buttons (Done,
Cancel, Language) outright; keep the dwell mechanism for later use.**
The developer's own item-7 note above already called them placeholders.
`core/hover.py`'s `widgets_for()` is the one place to change — return no
widgets (or none of these three) — and `Widget`/`layout()`/
`DwellTracker`/`_fire_widget`'s dispatch table stay intact and unused,
ready for whatever real widget set replaces them and for item 3's bin
dwell (which uses the same `DwellTracker`, just fed a bin instead of a
widget). Not yet built.

## 8. Pointer is jittery, needs smoothing — DONE, commit 9854a5e, not yet rig-confirmed

Fixed in `tracker/tracking.py`: `HandTracker._smoothed()`, a per-track EMA
folded into the existing `_match` update (`Track.x/y`), time-based
(`alpha = 1 - exp(-dt/tau)`) rather than a fixed per-frame blend, because
`tracker/main.py`'s own measured camera rate ranges 4-30Hz on this rig — a
constant alpha would over- or under-smooth depending on which rate a
given tick happened to be running at. `tracker.smoothing_tau_s` in
`config/system.default.json`, default 0.10s, a reasoned starting point
(see the constant's own comment) — **not yet tuned by watching it on the
rig**, per this item's own instruction; retune there, not by guessing
again. A new track (a hand's first sighting) is never smoothed — no
history exists yet to blend against.

Placement decision, made and documented in `tracking.py`'s module
docstring: the filter sits downstream of item 1's shadow-clearance
offset (`tracker/main.py`'s `_to_stage`), not upstream of it. This
doesn't matter in practice — the offset is a fixed per-axis constant, and
EMA is linear, so filtering before or after an additive constant gives
the identical output — and downstream is what let the filter reuse the
per-track `last_seen` state `tracking.py` already keeps, rather than
inventing a second per-hand history in `main.py` before track identity
exists.

## 9. Live tab doesn't render the MediaPipe hand overlay — RESOLVED, 2026-08-13

Resolved by a design change rather than by wiring `draw()` as this item
originally scoped: the developer decided the Live tab does not need a
hand-tracking view at all. It stays the plain rectified picture. The raw
MediaPipe skeleton (21 landmarks) moved to the new Developer tab instead
(item 10's "first slice" — `#devImg`/`#devOverlay`, `index.html` ~line
799), independent of calibration by design since MediaPipe runs upstream
of the homography. Confirmed working by the developer on the rig.

`core/web/static/index.html`'s Live tab has a `hands` toggle chip
(~line 595) and canvas overlay plumbing already built, but the comment
at ~line 190 says the draw function "is structurally wired to the toggle
chips but currently paints nothing" because at the time it was written
"no hands (M5)... exist on the wire yet." **That's now stale** — M5 is
code-complete and `core/main.py`'s `_hands_msg` (~line 1204) already
puts hand data on the wire for the staff view. The remaining work is
wiring the Live tab's overlay `draw()` to actually paint the hands data
now that it exists, the same way the `rects`/`labels` chips will need
their own wiring once M4's bin-rect data is confirmed flowing (check
whether those are also still stubs while in there).

## 10. Add a Developer tab: process status, live view (+ optional
    MediaPipe overlay), weight, classification output — PARTIALLY BUILT

**Since this item was written, a "first slice" landed** (`index.html`
data-tab="developer", ~line 785): its own raw-camera `<img>`/`<canvas>`
pair drawing the full 21-point MediaPipe hand skeleton, independent of
calibration. That closed item 9 the way item 9's own resolution note
above describes. **But the OLD top-right toggle/panel (`devToggle`/
`devPanel`, ~line 604/823) is still there too, alongside the new tab, not
folded into it** — it still gates the six process pips' visibility, the
camera-stats block (doc §12.8: resolution/fps/frame_id/shm slot/
dropped), and the "Mock picks/put-backs" grid (the {45,6,120,3,25,80}g
cycle — the "fake weight addition" below).

**DECISION, 2026-08-13:** remove the top-right `devToggle` button and
`devPanel` entirely. Everything they gated moves into the Developer tab,
each as its OWN separate control rather than one master switch turning
all of it on together — specifically the mock pick/put-back grid ("fake
weight addition") and the six process pips ("active process") must be
independently toggleable, not bundled. Camera stats and the MediaPipe
skeleton view already live in/near the tab; fold them in on the same
one-control-per-thing basis. Live per-bin weight and classifier output
(the doc §12.4 Bins tab already has live weight; classifier output was
never checked — see the original note above, still true) remain
unscoped past that. Not yet built.

## 11. Pointer lags behind a fast hand move, and sometimes sticks then
    snaps to the new location — DONE, root cause confirmed by a rig log
    plus a matching synthetic reproduction, not yet rig-confirmed fixed,
    2026-08-13

Developer's report from the same rig session: the MediaPipe overlay
(Developer tab, item 9/10) tracks a fast hand move with no trouble, but
the actual cursor drawn on the projected table lags, and on a fast move
sometimes freezes in place for a moment, disappears, then reappears
already at the new hand location — not a smooth slide there.

**Fixed, same read of `tracker/tracking.py` this item's diagnosis came
from — but not yet confirmed against the actual stuck/reappear symptom
on the rig, per this file's own rule (item 8's own status line is the
same shape).** `MATCH_GATE_PX` (150px stage-space,
`tracking.py` ~line 102) is a *fixed* per-frame distance, unlike item 8's
EMA smoothing right next to it, which was deliberately made time-based
(`alpha = 1 - exp(-dt/tau)`) because this process's own frame interval
ranges 4-30Hz. The gate wasn't given the same treatment: at the low end
of that range (4Hz, dt=250ms) a hand moving at an ordinary reach speed
(~1 m/s) covers ~250mm between frames — already past the 150px gate,
which the code's own comment computed against a 33ms (30Hz) frame. A
detection that misses the gate does not get matched to the existing
track (`_match`); it becomes a brand-new, **unsmoothed** track instead
(`_appear` — item 8's own docstring: "a brand new track is never
smoothed"). The old track isn't retired yet (`TRACK_GRACE_S` = 500ms
grace), so if it held the pointer role it sits frozen at the old
position — visible as "stuck" — until it retires, while the real hand's
new position exists only as an *ambient* track (a pointer already
existed, so step 2 doesn't promote it) until `PROMOTE_DELAY_S` (another
500ms) lets it inherit the role — visible as the cursor "disappearing"
then "reappearing" already at the new spot, matching the report closely.
Fixed by making the gate time-based too — `_match_gate_px()` returns
`max(MATCH_GATE_PX, MATCH_SPEED_PX_S * dt)` per track (`MATCH_SPEED_PX_S
= 3000.0`, 3 m/s, reasoned in the constant's own comment) — rather than
widening the fixed number outright, since a bigger fixed gate at every
frame rate would cost more of the two-hands-passing-close protection
`tracking.py`'s own docstring already flags as the one failure this
module exists to prevent. The widening only takes over on the slow
frames this rig has actually measured (4Hz); at a normal ~30Hz rate the
150px floor still governs and every gate test written before this item
is unchanged (checked: full suite green, and the widened-gate tests fail
under a reverted mutation, so they're not passing by construction).
`common/geometry.match_nearest` gained the ability to take a per-point
gate list (still accepts a single float, existing callers unchanged) so
each track's own gate — which depends on that track's own time since
last seen — can differ within one call.

**2026-08-13, later: developer restarted `run.py` (twice) and the match
gate above made no difference — real signal, but not yet ruled OUT**
(the gate can only help when a detection exists to match against; it
does nothing for a genuine gap in detections reaching `tracking.py`).
Left in place — it is still a correct, tested widening for the case it
targets, just evidently not the (whole) cause here.

**2026-08-13, later still: a second theory (a fast hand outrunning
`tracker/main.py`'s per-hand acquisition crop, decision 7) was built,
tested with a diagnostic log first, then a fix (chasing the hand's
predicted position on a miss instead of leaving the crop frozen) — and
the developer's own rig test showed NO EFFECT. That fix (commit
7f33248) has been REVERTED (commits 4fef920, d05c449) rather than left
in place not working.** The developer's counter-argument is the reason
to trust the revert, not just the negative test result: the raw
MediaPipe skeleton on the Developer tab (item 9/10) is fed from the
EXACT SAME per-tick detections as the cursor (`tracker/main.py`'s
`tick()` — one `detect()` call serves both `_maybe_send_landmarks` and
`tracker.update()`), and `_maybe_send_landmarks` sends explicitly even
on an empty detection (see its own docstring — silence would read as
the tracker being dead). If the acquisition window were really losing
the hand for up to a second, the Developer tab's skeleton should blank
out for that same second — and the developer reports the opposite: the
skeleton stays smooth, even looking faster than the video itself, with
the actual hand seeming to trail behind it. **The diagnostic log from
that theory (three real "lost after 1.03s" events, matching the
reported stuck duration closely) was real data, but real data pointing
at a real but probably COINCIDENTAL event, not the trigger** — window
losses evidently happen on this rig without producing every stuck-cursor
report, or without being the specific mechanism behind the ones being
watched for.

**Where this leaves it, 2026-08-13, not yet resolved:** two reasoned,
tested-in-isolation, rig-tested-and-found-wanting theories is enough
guessing — the next step is a diagnostic that answers the one question
that actually splits the search space, rather than a third theory.
Added (commit — see below): `tracker/main.py` now logs every time the
POINTER ROLE moves to a different track id (or appears/disappears),
together with how many raw detections existed that same tick. Read on
the next rig reproduction:
- **If the log shows `-> None` with `0 raw detections`** at the moment
  the cursor sticks, `tracking.py` is genuinely losing the track because
  no data reached it that tick — a real upstream gap, contradicting the
  smooth-skeleton observation, and worth checking whether the Developer
  tab's own 10Hz throttle (`LANDMARKS_HZ`) or a browser-side rendering
  quirk is masking a gap that real detection does have.
- **If the log shows no transition at all while the cursor is visibly
  stuck** (the pointer track id never changes), the freeze is happening
  AFTER this process entirely — the cursorbus UDP send, `CursorLink` on
  the oF side (`of/hotpot-table/src/CursorLink.cpp` — drain-to-latest,
  sequence-gated, `kCursorHoldSeconds=0.35f`), or oF's own render loop —
  and the fix belongs there, not in the Python tracker.
- **If the log shows a transition to a DIFFERENT id with detections
  still present that tick**, that is a distinct bug in `tracking.py`'s
  own matching (an id swap, which `_match`'s own docstring already
  treats as the one failure that module exists to prevent) — not a data
  gap at all.

Ask the developer to restart `run.py` once more, reproduce the fast-move
stuck cursor, then read `logs/hotpot-<date>.log` for `pointer track`
lines around that moment.

**2026-08-13, later still: done, and the log answered the question —
it is #3, `tracking.py`'s own matching, NOT a data gap and NOT
downstream of this process.** ~20s of one rig session
(`logs/hotpot-2026-08-13.log` lines 735-758) shows the pointer track
dying and a brand-new one taking over **12+ times**, roughly every
1-3 seconds, almost every single time with **1 raw detection present on
the very tick the old track dies** — not 0. Only the LAST of
these transitions coincides with an actual `"acquisition window ...
lost"` log line; all the others have no matching acquisition-loss event
at all, ruling that theory out too, independently of the developer's own
already-sound counter-argument above. Two further things the pattern itself gives away, both consistent
with a single mechanism:
- **Every `None -> newid` line lands almost exactly `PROMOTE_DELAY_S`
  (0.5s) after the preceding `id -> None`** — the "new" id was not new
  at all, it is the SAME ambient track quietly created (and never logged,
  since creating an ambient track doesn't change who the pointer is) the
  moment the old one first failed to match, now promoted on schedule.
- **Several transitions jump id-to-id with NO `-> None` in between at
  all** — `tracking.py._appear`'s one sanctioned same-tick role swap,
  "a hand MediaPipe currently calls Right arriving beside an existing
  pointer takes over immediately." Firing this often on what should be
  one continuous hand strongly suggests MediaPipe's own handedness label
  is flickering on an overhead camera, exactly the unreliability
  `tracking.py`'s own opening paragraph already assumes and the reason
  role is locked to a track id rather than re-read every frame — except
  here it is deciding which track WINS in the first place, upstream of
  that lock.
Both symptoms point at the same root: **something keeps failing to match
one continuous hand's consecutive detections to its own existing track,
even though detections keep arriving on the very same, never-freed
acquisition window.** `_log_pointer_transition` (this same commit) now
also logs the actual arithmetic a real match attempt would have used —
the distance from the outgoing pointer's last position to the nearest
`staged` (stage-space) point THIS tick, against the gate that applied —
whenever a transition happens with a previous pointer to compare against.
**Still not fixed — the next rig test's log will show whether the
reported distance is a genuinely large jump (a real, if surprising, hand
speed or a `tracking.py` gate bug) or something that looks like a
coordinate bug (e.g. a small camera-space wobble coming out amplified in
stage space) — read that number before writing any more code here.**

**2026-08-13, later still: done, and the distance/gate log answered it —
CONFIRMED, `tracking.py`'s match compares a new detection against a
track's own SMOOTHED position, which lags the true hand by design (that
lag is the entire point of item 8's EMA), and under sustained motion the
LAG ALONE grows past the gate.** The rig log's own numbers show it:
`outgoing pointer was at (1073,802), nearest staged point at (1081,980),
178px away, gate was 150px` — a bare few pixels over, not a wild jump.
**Reproduced synthetically, not just inferred:** a hand moving at a
steady, plausible 2 m/s (2000px/s at this rig's ~1px=1mm scale) for
under a fifth of a second, fed through the UNMODIFIED tracker, spawns a
SECOND competing track by the 5th tick — the smoothed position lags by
up to ~370px before the widened gate (item 11's own earlier fix)
eventually recaptures it, and while stuck the pointer id freezes; worse,
once a second ("ghost") track exists it can itself win the next match if
it happens to sit closer than the real pointer track that tick, and the
pointer role starts trading between competing ghost tracks — which is
exactly the rapid, erratic id churn the rig log showed (12+ ids in 20s).
This is the same simulation script, kept for reference (not committed —
scratch only):

    t = tracking.HandTracker()
    now, x = 0.0, 0.0
    for _ in range(40):
        now += 0.033; x += 2000.0 * 0.033
        hands = t.update([Detection(x=x, y=500.0, conf=0.9,
                                    handedness=None)], now=now)
        # unfixed: len(t.tracks) grows past 1 within ~5 ticks

**Fixed (this commit):** `tracking.Track` now keeps `raw_x`/`raw_y` —
the last REAL detection, never smoothed — separately from `x`/`y` (the
EMA output still sent on the wire, unchanged, still smooth). `_match`
matches new detections against `raw_x`/`raw_y`, not `x`/`y`. The same
synthetic 2 m/s run now stays on ONE track id for the full second-plus
tested, with a small, constant, non-growing lag instead of a periodic
freeze-then-snap. This does not replace the earlier time-based gate
widening (still in place) — the two fix different things: the widened
gate helps when the CAMERA'S OWN frame rate is genuinely slow; this fix
stops the gate from being broken by the smoothing filter's own lag
regardless of frame rate.

2 new tests in `tracking.py` (a sustained-motion run staying on one
track id and reporting exactly one hand throughout, never a second
ghost; `raw_x`/`raw_y` provably unsmoothed while `x`/`y` still is), the
first confirmed to fail under a reverted mutation (matching put back on
`x`/`y`). 868 tests pass.

**Not yet confirmed on the rig** — the synthetic reproduction matches
the rig log's own numbers closely enough to trust, but nobody has
watched the actual cursor stop sticking on the projected table yet. Ask
the developer to restart `run.py` and try the fast-move case once more;
if the stuck/reappear symptom is gone, this item is finally done — if
any of it remains, read `logs/hotpot-<date>.log`'s `pointer track` lines
again before guessing further, the same discipline that got here.

---

**Order isn't prescribed** — pick whichever item the developer wants
worked next. Resolved, no action needed: 1, 2 (workaround), 8, 9. Done,
not yet rig-confirmed: 11 (pointer lag/snap on fast moves). Decided,
ready to build: 3 (bin dwell + food-item window), 4-7 (remove the three
widgets, keep the dwell machinery), 10 (fold the top-right developer
toggle into the Developer tab, one control per thing).

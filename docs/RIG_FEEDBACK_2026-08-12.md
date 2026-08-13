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
    snaps to the new location — DONE, not yet rig-confirmed, 2026-08-13

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
last seen — can differ within one call. **Not yet confirmed against the
actual stuck/reappear symptom on the rig, and `MATCH_SPEED_PX_S` is a
starting point, not a measurement — same caveat item 8's own tau has.**

---

**Order isn't prescribed** — pick whichever item the developer wants
worked next. Resolved, no action needed: 1, 2 (workaround), 8, 9. Done,
not yet rig-confirmed: 11 (pointer lag/snap on fast moves). Decided,
ready to build: 3 (bin dwell + food-item window), 4-7 (remove the three
widgets, keep the dwell machinery), 10 (fold the top-right developer
toggle into the Developer tab, one control per thing).

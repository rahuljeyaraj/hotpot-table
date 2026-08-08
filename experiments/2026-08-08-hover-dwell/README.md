# 2026-08-08 — hover dwell threshold

**Question:** how long must a hand stay inside a bin before that bin counts
as hovered — i.e. what should `HOVER_DWELL_MS` be?

**Answer: PROVISIONAL. 1000 ms stands, unchanged, because the data that
would have moved it is not trustworthy yet.** The measurement it rests on
predates a known hand-tracking fault and must be re-run from scratch, not
extended. See "why the answer is provisional" below.

---

## Why there is a threshold at all

A hand travelling from one bin to another passes over the bins in between.
Those pass-overs are real detections inside a real bin rect and are
indistinguishable from an intentional hover **except by how long they last**.
The threshold is the entire discrimination.

Too low and the table reacts to every bin on the way to the one the diner
wanted. Too high and a deliberate hover feels broken.

1000 ms was a guess, written into `TableGeometry.h` and commented as a
rig-tunable starting value rather than a derived dimension — there is no
chain it falls out of and nothing to calculate it from. This experiment
existed to replace the guess with a measurement.

## What was established first: the timer is correct

Separate question from the value, and settled independently.

30 hover cycles across all 8 bins. Every `IDLE -> HOVERED` fired between
**1000 and 1050 ms**. The overshoot is one tracker frame (33 ms at 30 fps),
with occasional two-frame steps where a dropped detection was credited its
real 66 ms of wall clock.

That spread is the proof the accumulator measures wall clock between
detections rather than app frames, and that the ~500 ms render hold is not
feeding it. Had the two timers been crossed, a 1000 ms hover would have
taken roughly 2000 ms of real time. It did not.

**The mechanism is sound. Only the number is in question.**

## The measurement, and why it needed instrumentation

A rejected pass-over is completely silent — it writes nothing, because
nothing happens. So a clean log is the expected shape of a threshold that
is working *and* of one set so high that nothing ever reaches it. Those are
not the same thing and the log could not tell them apart.

`DWELLING -> IDLE` logging was added, reporting the time banked at the
moment of reset. Resets under 100 ms are dropped: a hand clipping the corner
of a rect banks a few tens of ms, and those are the edge being touched at
all, not a pass-over.

### Result, n = 16

```
100 101 116 150 150 283 301 433 434 518 566 598 600 700 851 883

min       100 ms
median    434 ms
mean      424 ms
2nd max   851 ms
max       883 ms
```

Margin at 1000 ms: **117 ms**, about 3.5 tracker frames. Thin. And the max
is the statistic most likely to grow with more samples, not less.

On this data alone the recommendation was 1500 ms.

## Why the answer is provisional

**The accumulator resets on `/hand/none`.** That message is the tracker
positively reporting an empty frame, and it zeroes every bin immediately —
which is correct behaviour, and is what makes pass-over rejection work at
all.

But it means a detection dropout mid-crossing does not pause a sample. It
**truncates** it and starts a new one. Consequences, all in the same
direction:

- every logged pass-over is a **lower bound** on the real crossing duration
- the max **understates**, so the true worst case is ≥ 883 ms
- a crossing split into fragments under the 100 ms floor disappears
  entirely, so **n is undercounted too**

So unreliable tracking does not add noise around the true value — it biases
every sample short. That makes 1000 ms *less* safe than 117 ms of margin
suggests, not more.

Tracking is known to be unreliable right now for two independent reasons
(see the next-session notes): fixed camera exposure that does not compensate
when the room dims, and closed-fist poses that MediaPipe cannot resolve at
an overhead angle. Neither is fixed.

**Re-run 2g from scratch after tracking is fixed. Do not extend this data
set — samples taken before and after the fix are not the same measurement.**

## Decided along the way

### The §9 split, resolved

§9 records "colour merges instantly on hover — no dwell timer, because the
load cell confirms the actual pick", which appeared to contradict building a
dwell timer for bins at all. It does not. The two are different signals:

- **Instant colour on entry.** Commits to nothing, so it cannot be wrong —
  the load cell, not the hover, confirms the pick. No dwell required.
- **Dwelled progress drives the popup.** The popup is the committing
  action, and a popup firing on every bin a hand crosses is exactly the
  failure the threshold exists to prevent.

Only the second needs to clear the pass-over distribution.

### Cyan failed, and the reason generalises

Progress was first built as dim cyan on a white outline. **Rejected at the
table: very difficult to see at standing height.**

The failure was not the hue but the axis. A dimmed colour differs from the
white idle outline mostly in **brightness**, and brightness is what
projector light and ambient light are already fighting over on a white
plywood surface.

**Rule, now general to this project: distinguish states by HUE at full
value, never by brightness.**

Replaced with red on entry, green replacing the red along the same line as
progress — so the leftover red reads as the work remaining, and red-to-green
carries the meaning with no legend. Colour *values* remain unconfirmed on
the rig; the structure and the rule are what this experiment settled.

## Next

1. Fix hand tracking. Two separate problems — do not conflate them:
   fixed exposure with no compensation when dark, and closed-fist poses at
   ~80° overhead, which is not a lighting problem.
2. Measure the camera elevation angle (open since stage 1).
3. Re-run the pass-over measurement from scratch.
4. Run the legibility test that was never done: mid-reach, does the
   red-to-green ratio say how close to committing you are? If it only reads
   near-complete, that is an argument against the fill as an encoding, not
   for a longer threshold.

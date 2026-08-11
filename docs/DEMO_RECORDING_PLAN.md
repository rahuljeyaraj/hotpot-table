# DEMO RECORDING — plan (not yet built)

**Status: PLANNED.** Written 2026-08-11, during M3 (camera) work, on the
correct discipline of not derailing M3.2 (`camera/main.py`) to build this.
Nothing in this file is implemented. When it is built, fold the settled
decisions into `docs/HOTPOT_ARCHITECTURE_v3.md` (repo layout §7, a new
tools/ entry) the way M2.6's plan was folded in — this file stays as the
design record afterward, per that same convention.

---

## 1. THE PROBLEM

The contest (Seeed "Make a Sign" Interactive Signage Contest 2026) is judged
substantially through a **submitted demo video**, not a live visit. Nothing
in the architecture currently produces one. This plan settles how footage of
a real interaction gets from the rig to an mp4, decided now so that whoever
is filming during M8/M9 acceptance testing isn't inventing the pipeline
under time pressure the night before the deadline.

Two things need capturing:

1. **The video and audio of an interaction itself** — what a diner sees on
   the table and hears standing at it.
2. **Optionally**, composited alongside it: the staff view's **Live** tab
   (§12.3) — the raw camera feed with its canvas overlay of bin rects,
   labels, weights, and hand markers — as a picture-in-picture. This is the
   "look, the tracking is real" footage a technical judge cares about that
   the diner-facing table video alone can't show.

---

## 2. DECISIONS — SETTLED

| # | Decision | Rationale |
|---|---|---|
| 1 | Recording is an **external, out-of-band tool**, not new code in any of the six live processes | §3 |
| 2 | It captures by **window/screen capture**, not by re-deriving the composited image from the wire | §3 |
| 3 | Audio is **one real microphone in the room**, not a digital mix of oF's audio bus | §3 |
| 4 | Two output modes: **solo** (table only) and **pip** (table + staff-view Live tab inset) | §4 |
| 5 | The tool lives at `tools/record_demo.py`, alongside `render_tts.py` and `export_edgeimpulse.py` — offline tooling, not a `run.py` process | §5 |
| 6 | It has no pip, no heartbeat, no wire-protocol participation | it is not one of the six processes and must never be mistaken for one on the staff view header |

---

## 3. REJECTED ALTERNATIVES

**Reimplementing the Live-tab overlay server-side** (read camera frames from
the shm ring or MJPEG stream directly, read bin rects / hand positions off
the wire, draw the same rects/labels/hands in Python/OpenCV, mux with
video) — rejected. This is a second implementation of exactly what
`core/web/static/`'s canvas already draws (§12.3), and the doc's whole
discipline around single sources of truth (one homography, one shared
pick/put-back cycle, `core` never touching a frame) argues against a second
one that can silently drift from the first. Screen-capturing the actual
browser tab guarantees the recording shows precisely what staff saw, by
construction, for zero extra drawing code.

**Recording inside `of` itself** (it already renders every frame to an FBO
and owns the audio device — §13.1, §15.1 — so it's the one process that
already has "what the diner sees" in hand every frame). Rejected for this
use, even though the ownership argument is real: `of` is on M1's real-time
budget (I1) and mid-build through M8/M9; adding an encoder in its render
loop is exactly the kind of change that risks the fluid's frame budget for
a feature that only needs to run a handful of times, on demand, for
filming. An external capture costs `of` nothing.

**Mixing oF's `AudioBus` output digitally instead of miking the room.**
Rejected: it would only capture sound effects and TTS (§15), missing the
diner's own voice and reactions — the actual soul of a demo reel — and it
would require piping audio out of a real-time process (§15.1's whole
argument for `of` owning the device is to avoid exactly this kind of
external tap). A single mic near the table hears the SFX through the
speakers *and* the person interacting, already mixed and already in sync
with the picture, for free. This is the same "measure the real thing
instead of synthesizing an idealised signal" instinct §12.7 and §9.6 apply
elsewhere (the illuminant is measured, not assumed; calibration uses a real
reference mass, not a formula).

**A staff-view UI button to start/stop recording.** Rejected for now — it
would need a new wire message, a new §12 surface, and staff-view real
estate for something that runs a handful of times total, by a person who is
already standing next to a laptop running the capture script. A CLI
invocation is the cheaper tool (same reasoning as §12.1's "no build step"
and §22's general bias toward the cheapest thing that works). Revisit only
if filming turns out to need hands-free start/stop.

---

## 4. VOCABULARY

- **Table video** — the oF window's rendered output: fluid, plates, text,
  keystone-warped. What a diner standing at the table sees.
- **Live-tab PIP** — the staff view's Live tab (§12.3), window-captured and
  composited as a small inset, typically bottom-corner, over the table
  video. Shows the camera feed plus bin-rect/label/hand overlay — the
  tracking pipeline visibly working.
- **Solo mode** — table video + room audio, no inset. The default; most
  submission footage should be this, because an inset is a distraction from
  the product for anyone not evaluating the CV pipeline.
- **PIP mode** — table video + Live-tab inset + room audio. B-roll for
  technical credibility, not the main cut.

---

## 5. BUILD (when this is picked up)

### 1. `tools/record_demo.py`

A thin CLI wrapping `ffmpeg`, not a new long-running process:

```
python tools/record_demo.py solo  --out captures/solo_2026-08-20_1.mp4
python tools/record_demo.py pip   --out captures/pip_2026-08-20_1.mp4  [--duration 90]
```

- **solo**: one window-capture input (the oF window) + one audio input (the
  configured mic), muxed straight through.
- **pip**: two window-capture inputs (oF window, staff-view browser window)
  + one audio input, composited with ffmpeg's `overlay` filter — Live-tab
  window scaled and placed in a corner (default bottom-right, configurable)
  — then muxed with audio.
- Ctrl-C stops and finalizes the file cleanly (ffmpeg's own signal handling
  covers this; the script must not double-handle it and truncate the
  container).
- Window/device selection is **platform-specific** and must not be
  hardcoded: `gdigrab` + a named window title on Windows (today's dev
  machine), `x11grab`/`wayland` capture + a window id on the Linux rig
  (the actual deploy target, per §1.4). The script detects platform and
  picks the right ffmpeg input flags; it does not pretend to be
  cross-platform magic. Fails loudly with the ffmpeg stderr if a window
  title/id can't be found, rather than silently recording a blank screen.

### 2. `config/system.json` — recording section (new, optional keys)

```json
"recording": {
  "mic_device": null,
  "pip_corner": "bottom-right",
  "pip_scale": 0.28
}
```

`mic_device: null` means "let ffmpeg use the OS default input" — fine for
solo dev testing; the real mic gets pinned here once it's chosen for the
rig, the same pattern §8.6 already uses for other human-tuned values.

### 3. Window titles

`of`'s window title and the staff view browser's tab/window title both need
to be **stable and greppable** so the capture script can find them without
per-machine configuration. Confirm `of/hotpot-table`'s window title (set in
`main.cpp`'s window settings) is fixed and distinctive; if the staff view is
opened in a generic browser, recommend a dedicated kiosk-mode window
(`--app=` / equivalent) with a title that includes "Hot Pot" so it doesn't
collide with any other open tab.

### 4. `tools/record_demo.md` (or a section in this file, TBD at build time)

A one-page "how to film a demo take" runbook: mic placement, which mode to
use for which shot, how to check the file actually has audio before ending
a take (an audio-less recording is a mistake discovered too late, the same
category of failure the doc is generally allergic to — see §9.6's Bins-tab
sanity check for the same instinct applied to calibration).

---

## 6. ACCEPTANCE (human, when built)

- `solo` mode produces an mp4 with the table video and audible room sound
  in sync, playable in a normal video player, no green/black frames from a
  mis-titled window.
- `pip` mode produces the same, with a visibly correct, correctly-scaled
  Live-tab inset that updates live (not a frozen frame).
- Recording start/stop does not perturb `of`'s FPS or the tracker's
  emit rate — confirm via the developer panel (§12.8) during a take.
- Ctrl-C mid-recording yields a playable file, not a corrupt one.
- Works on the actual rig (Linux, x11grab/wayland path), not only on the
  Windows dev machine.

---

## 7. NOT IN THIS PLAN

- Any in-app (`of` or staff-view) recording UI or wire message.
- Editing, titling, or assembling the final submission cut — this plan only
  gets raw, in-sync footage off the rig.
- Recording the classifier's or tracker's internal debug views — only what
  the Live tab already exposes (§12.3's own toggle chips govern what's
  visible in the PIP, same as they govern what staff sees).
- Multi-camera or multi-angle capture (e.g. a separate phone/tripod shot of
  the whole table from outside) — a real filming concern for the
  submission, but a production/staging question, not a software one, and
  out of scope here.

---

## 8. WHERE THIS SITS RELATIVE TO THE MILESTONES

Not a milestone in §21's numbered sense — it doesn't gate or get gated by
M4–M9, and building it doesn't require any of them to exist first (`solo`
mode against the table video is testable as soon as `of` renders anything
real, i.e. after M1). But it's only worth *building* once there's an
interaction worth filming, and only worth *using for real footage* once
M8 (fluid/sound) and M9 (voice) land — before that, the table video is
placeholder motion, not demo material. Practical placement: build the
tool itself in a slack moment (it's small and fully decoupled), shoot the
actual submission takes last, once the whole loop is acceptance-tested.

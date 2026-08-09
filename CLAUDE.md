# CLAUDE.md — Interactive Hotpot Ingredient Table

Ground truth for any Claude Code instance starting without context.
**Read this fully before writing code. Several "obvious" approaches here are known dead ends.**

---

## 0. ARCHITECTURE — the Python core owns state, oF renders

> **This section supersedes the "openFrameworks is the hub" assumption that runs
> through the rest of this file.** Sections written before it — §2, §4, §10,
> §11, §14, §17, §22 — still describe *behaviour* correctly and were written
> against observed results, but several of them attribute that behaviour to the
> wrong process. **Where a section and this one disagree about which process
> owns a thing, this one wins. Where they disagree about what the thing does,
> the older section wins.**
>
> Numbered §0 rather than inserted as a new §2 so that the ~44 existing `§n`
> cross-references elsewhere in this file stay valid.

### 0.1 The split

openFrameworks is no longer the hub. A single **Python core process** owns all
state and all logic. openFrameworks becomes a **renderer**.

| Process | Owns |
|---|---|
| **Python core** — one process | MediaPipe hand tracking, the food classifier, and **all session truth**: start weights, current weights, removed grams, the cart, prices, the bin map |
| **openFrameworks** | pixels on the table, and nothing else |
| **XIAO ESP32S3** | reads 8 load cells and prints raw counts. Nothing more (§0.7) |

**One Python process, not three.** Hand tracking and the classifier sit inside
the core rather than beside it because both feed decisions the core has to make:
the classifier is gated on a settled-weight signal, and that signal comes out of
the same loop that owns the weights. Splitting them would mean shipping the
weights back out to whoever owned the gate.

### 0.2 What oF receives, and what it is allowed to keep

oF receives **semantic state** — per bin: item name, price, weight, highlight
state; plus the cart total — and turns it into pixels. It does not compute any
of those values and does not know how they were derived.

The only state oF may hold is **frame-to-frame tweening**: highlight fades,
cursor easing, the fluid sim. Every item on that list exists to smooth this
frame into the next and is meaningless to anyone else.

**The rule: oF holds no state with memory that another consumer would want to
read.** The test is not "is it small" or "is it cheap to recompute" — it is
"would a second reader ever ask for this value". A cart total, a start weight, a
deadbanded reading all fail that test, because the staff view (§0.5) is exactly
that second reader. A halo's current alpha passes it.

### 0.3 The 10 g display deadband MOVES to Python

The deadband described in §14 is unchanged in *behaviour*. It changes *process*.

**Why it has to move:** the staff view shows the true weight while the projector
shows the deadbanded value. Those are two renderings of one number, so the
number must have one owner. With the deadband living in oF, the projector would
hold the only copy of the deadbanded value and the staff view would have no way
to ask what the diner is currently being shown.

Everything §14 says about it still holds and still gets broken the same way:

- It is **display only**. It never enters the price arithmetic.
- It gates *when* the displayed value catches up, and **snaps to the current
  true weight** — never to the threshold, never to the old value plus a step.
- Nothing is discarded, only delayed, so **small picks accumulate**: two 6 g
  picks hold at 6, then cross at 12, and the display snaps the full 12 g.

### 0.4 Table geometry in mm is shared truth, owned by neither side

The bin layout in millimetres is the one thing both processes need and neither
owns. Each side owns its own mapping out of it:

| Owner | Artefact |
|---|---|
| openFrameworks | the **projector** homography, and `bin_offsets.json` (§17) |
| Python core | the **camera** homography (`tools/calibration/homography.json`, §16) |
| neither — shared | the table geometry in mm (`BINS[]` in `TableGeometry.h`, §17) |

**Camera-space bin rectangles serve two consumers at once**: the classifier's
ROI crops (§10) and the staff view's overlay (§0.5). That is one derivation
inside Python, used twice — not two independent guesses at where a bin is.

§17's rules are untouched by this. `BINS[]` is still CAD and still must never be
edited to fix physical alignment; `bin_offsets.json` is still as-built rig state
on the projector side, and re-solving the projector homography still invalidates
it from scratch.

### 0.5 The staff view — a browser page served by the Python core

A web page served by the core, opened on a normal screen. It is both the
operator's window into the table and the control surface for staff mode.

**Video:** raw camera **MJPEG at full resolution and full frame rate**, with an
HTML `<canvas>` overlay drawn client-side on top. Nothing is composited
server-side — the core sends frames and sends state, and the browser draws the
overlay.

**The canvas must be sized to the camera resolution and scaled with CSS. Never
size it to display pixels.** The overlay is drawn in camera coordinates, which
is what the bin rectangles in §0.4 are already in. Sizing the canvas to the
element's on-screen size makes every overlay coordinate depend on the browser
window, so the boxes drift off the bins when the window is resized — and it
looks like a calibration fault, not a layout bug.

Shows:
- per-bin **item name** and **current weight**
- **cart contents**
- **low stock alerts**
- toggles for **hand tracking output** and **classifier output**
- the **training capture button** (§10)

### 0.6 Staff mode no longer needs projected UI

Staff mode now lives on the staff view, so the projector does not need a staff
interface at all. **The projector's entire staff-mode obligation is a "closed,
please wait" state.**

This is a straight reduction in the deferred staff mode scope: no projected
menus, no dwell-timer admin buttons, no second UI mode to lay out on the table.

### 0.7 Load cell firmware is one-directional and as dumb as possible

XIAO ESP32S3 over USB serial. It **reads 8 cells and prints raw counts,
continuously.** That is the whole firmware.

**Not in the firmware, deliberately:** no tare, no command handling, no NVS, no
calibration factors, no settled detection.

**Nothing ever sends anything to the XIAO.** The link is one-directional. A
firmware with no input has no command parser to get out of sync with the host,
no stored state to go stale against a reflash, and no mode the host can put it
into and then forget about.

- **Per-cell calibration factors live in a JSON on the Python side**, next to
  `bin_map.json` (§11). Recalibrating is editing a file the host already reads,
  not reflashing a microcontroller.
- **Python decides what counts as settled**, because Python also owns the
  classifier gate that consumes that signal (§0.1, §10). Settled detection in
  the firmware would put the trigger in one process and its consumer in another.

---

## 1. What this project is

A self-serve hotpot ingredient table for the Seeed Interactive Signage Contest 2026.

A diner stands at a table with 8 open bins of raw ingredients. An overhead
projector paints UI directly onto the table surface — halos around bins,
ingredient names, prices, a running cart, and a fluid/steam visual layer.
An overhead camera tracks the diner's hand. Load cells under each bin weigh
what gets removed. Price is charged by weight.

The diner picks ingredients into a bowl, then hands the selection off to the
kitchen. **There is no live cooking at the table.**

Contest judging axes: Technology, Interaction, Materials, Function.

---

## 2. Hardware

| Part | Role |
|---|---|
| Host PC (dev) / reComputer x86 (target) | Everything: Python core (state, tracking, vision, voice, staff view) + oF renderer (§0) |
| 1× USB camera, mounted vertically overhead | Hand tracking AND food classification AND staff view video AND mic |
| 8× CZL-611N 1 kg cantilever load cells | Weight per bin |
| 8× HX711 amplifier boards | Load cell ADC |
| XIAO ESP32S3 | Load cell aggregator → raw counts over USB serial to host, one-directional (§0.7) |
| Overhead projector | UI output |

**One camera only.** No Grove Vision AI modules — they were dropped from the
build. The XIAO is the only Seeed product in the hardware stack (plus the
reComputer as deployment target).

**Raspberry Pi 4B and Pico are owned but not in the current architecture.**

---

## 3. Camera geometry — non-negotiable

The camera must be near-vertical (≥80° elevation). This is not a preference.

Hand-position error on the table plane = `hand height / tan(elevation angle)`

The homography maps the table **surface**, but the hand floats **above** it.

- At 40° → error is 1.19 × hand height. A hand 100 mm up lands 119 mm off.
  A tray is 205 mm wide. Wrong bin selected.
- At 80° → error is 0.18 × hand height. 18 mm. Acceptable.

Second failure mode at shallow angles: lifting the hand vertically and pulling
it toward the camera produce identical image motion, so the cursor drifts when
the hand merely lifts.

### Confirmed on the rig, stage 1e
Observed with a real hand and a live dot: **raise the hand and the dot slides
outward, toward the edge of the table; lower it and the dot returns under the
fingers.** That is exactly the parallax above, seen for the first time on
hardware rather than on paper. It is not a calibration fault and re-solving the
homography will not remove it — the homography maps the table surface, and the
hand is not on the table surface.

It also confirms the camera is not perfectly vertical yet, since a true nadir
view would drift far less. **Measure the elevation angle before stage 3.**

This is the concrete reason fluid comes last (§14). Had the halos been built
first, this would have read as a calibration bug and cost a re-solve.

Camera shadow from the projector is **not** a concern. The projector's light
cone starts at its lens and spreads outward; a camera at lens height, 100–200 mm
to the side, sits outside the cone. General rule: whatever the projector can
light, a camera beside it can see.

---

## 4. Software stack

- **Python core** — one process, owns all state and logic (§0)
- **openFrameworks 0.12.1** — renderer, C++
- **ofxFlowTools** — GPU fluid simulation
- **ofxGui** — required, ofxFlowTools references it
- **MediaPipe Hands** (inside the Python core) — two hands, lightest model
- **OSC** — Python core → openFrameworks. It now carries **semantic state**
  (§0.2), not just hand coordinates
- **HTTP + MJPEG** — Python core → staff view browser page (§0.5)
- **JSONL over USB CDC** — XIAO → host, raw counts, one-directional (§0.7)
- Voice: openWakeWord or Porcupine on host, using the webcam's own mic

**MediaPipe is no longer a separate service talking to oF over OSC.** It is a
component of the core, and the core is what talks to oF. `track_hands.py` (§16,
§21) is where that code and its exposure rules live today; folding it into the
core does not change any of the camera rules in §16.

### `mediapipe.solutions.hands` does not exist any more
Every tutorial, and an earlier version of this file, says to use
`mp.solutions.hands.Hands(max_num_hands=2, model_complexity=0)`. Google removed
that API. Measured on this machine: present in mediapipe **0.10.14**, gone by
**0.10.35** and in **1.0.0**, where the whole package exposes only
`Image`, `ImageFormat` and `tasks`.

Pinning back to 0.10.14 also drags in jax, jaxlib and scipy, which is a lot of
dependency for a weaker reComputer to carry.

Use the **Tasks API**. The old settings map across exactly:

| legacy | Tasks API |
|---|---|
| `max_num_hands=2` | `num_hands=2` |
| `model_complexity=0` | the "lite" `hand_landmarker.task` bundle |

Landmark indices are unchanged, so **9 is still the palm centre**
(middle-finger MCP — steadier than any fingertip, since it barely moves as the
fingers open and close).

Running mode is **VIDEO**, not IMAGE or LIVE_STREAM. IMAGE re-detects from
scratch every frame. LIVE_STREAM is callback-based and drops frames to keep up,
which would hide exactly the jitter and latency that stage 1 exists to measure.

The `.task` bundle (~7.8 MB) is fetched from Google on first run and gitignored.
Google's copy is authoritative; vendoring it only adds a binary to clone.

---

## 5. openFrameworks — confirmed facts

### Setup
- oF installed at `C:\openframeworks` (short path, no spaces — required)
- This repo lives at `C:\openframeworks\apps\myApps\hotpot-table\`
  and **cannot be moved** — project files use relative paths to `../../../libs`
- Add addons via projectGenerator, not by hand
- Addon examples must be copied into `apps/myApps/` and re-imported via
  projectGenerator's Import tab before they will build

### projectGenerator CLI — the platform is `vs`, NOT `winvs`
The GUI is not needed; the CLI can re-import a project. It lives at
`C:\openframeworks\projectGenerator\resources\app\app\projectGenerator.exe`
(the top-level `projectGenerator.exe` is the Electron wrapper).

**A wrong `-p` value makes it segfault with no error message.** It prints as far
as `project path is: [...]` and dies — exit `0xC0000005` / 139. It looks
identical to a broken install, and it fails the same way on a brand-new empty
project, which sends you hunting in the wrong place. `-p"winvs"` is the wrong
value even though it is the platform name used elsewhere in oF. The correct
value is `vs`, as in `resources/app/settings.json`'s `defaultPlatform`.

Paths must be relative to the exe's own directory, and the exe must run from
there:
```
cd C:/openframeworks/projectGenerator/resources/app/app
./projectGenerator.exe -p"vs" -o"../../../../" \
    -a"ofxFlowTools,ofxGui,ofxOsc" "../../../../apps/myApps/hotpot-table"
```
Absolute Windows paths get mangled by the argument parser.

**Every re-import resets `PlatformToolset` to `v143`.** Re-apply `v145` in all
configurations afterwards or the next build fails with MSB8020. This is not a
one-time fix — it is the cost of every addon change.
- VS 2026 retarget is **manual**: Solution Explorer right-click, or the Setup
  assistant's "Retarget all". No automatic prompt appears.
- Build from a Developer Command Prompt for VS so compiler paths are inherited
- **VS 2026 uses platform toolset `v145`, not `v143`.** Its MSBuild path is
  `MSBuild\Microsoft\VC\v180\`.
- projectGenerator emits `v143`, which fails with **MSB8020** on a
  VS 2026-only machine.
- Fix applied: `hotpot-table.vcxproj` was edited from `v143` to `v145`
  in all configurations, and that edit is committed. It must be re-applied after
  every projectGenerator run — see above. `openframeworksLib.vcxproj` was
  already retargeted separately and is outside this repo.
- Build command, from a Developer Command Prompt for VS 2026:
  ```
  msbuild hotpot-table.sln /p:Configuration=Debug /p:Platform=x64 /m
  ```
- If a toolset error appears on a different machine, list valid toolset names
  with:
  ```
  dir "<VS install>\MSBuild\Microsoft\VC\v180\Platforms\x64\PlatformToolsets"
  ```

### Two required C++17 patches to ofxFlowTools
The addon predates C++17 and will not compile without these:

1. `ftPixelFlow.h` lines 50–51 — `min()` → `std::min()`
2. `ftAverageFlow.cpp` line 186 — `std::bind2nd` was removed in C++17:
   ```cpp
   std::transform(_v.begin(), _v.end(), diff.begin(),
                  [mean](float x) { return x - mean; });
   ```

TODO: fork ofxFlowTools with these applied, add as a git submodule, so a fresh
Linux build on the reComputer does not hit them again.

### Validated performance
~60 FPS at 1280×720, simulation scale 2, on Intel Core Ultra 5 125H with Arc
integrated graphics. Both headline risks (0.10-era addon on 0.12.1; integrated
GPU) are cleared on the dev machine.

**Open risk:** the reComputer's GPU is weaker and unquantified. Confirm the
exact model before committing further to the fluid approach.

---

## 6. ofxFlowTools API — read this before touching the fluid

### `addTemporalForce()` DOES NOT EXIST
It belonged to `ftFluidSimulation` in the 0.10 era. An entire earlier plan was
built on it and was wrong. Verify against current master, never old docs.

### The current class is `ftFluidFlow`
Inputs are **textures**, not points:
- `addDensity(tex)`
- `addVelocity(tex)`
- `addTemperature(tex)` — unused in this project

### Proven pattern for injecting hand coordinates
1. Draw a white filled circle at the hand position into a density FBO →
   `fluidFlow.addDensity()`
2. Compute the per-frame position delta, encode as
   `ofFloatColor(d.x * 2.0, d.y * 2.0, 0)` into a **separate** velocity FBO
   (`GL_RG32F`, `OF_BLENDMODE_DISABLED`) → `fluidFlow.addVelocity()`

Density alone gives a static blob. **Velocity is what makes it flow.**

### `ftOpticalFlow` is a dead end here
It only resolves small frame-to-frame displacements in real video. Synthetic
drawn circles produce no output at any threshold setting.

Delete the entire `opticalFlow` + `combinedBridgeFlow` chain. This is not just
tidiness — it removes several full-resolution GPU passes per frame, which is
the main insurance against a weaker reComputer GPU.

### One solver, not several
There is a single Stable Fluids solver. Behaviour changes via parameters:
dissipation, viscosity, vorticity, buoyancy. Steam rings here = high vorticity,
**buoyancy off**.

---

## 7. openFrameworks rendering gotchas

- ofxFlowTools leaves `OF_BLENDMODE_ADD` set. Call `ofEnableAlphaBlending()`
  before drawing UI or text washes out.
- Halo rings: use `ofPath` + `setStrokeWidth()`.
  **Never `ofSetLineWidth()`** — drivers cap it at 1 px.
- `ofTrueTypeFont` must be loaded at final display size. Never scale up.
- Buttons are rect regions + a dwell timer fed by hand coords. No widget library.
- Key `p` saves a timestamped screenshot to `bin/data/screenshots/` (git
  ignored), so a Claude Code instance can inspect visual output. **Not `s`** —
  that saves the bin alignment, and a mistyped screenshot must not cost a
  dialled-in alignment.
- Grab the screen at the end of `draw()`, never from `keyPressed` — after the
  buffer swap the back buffer holds an undefined frame.

### Layer order
As built today, stage 2 — no fluid yet, and no black rects (§8):
```
flat white field → bin outlines → UI text (names, prices) → hand dot
```
Outlines before text is not arbitrary: the outline is the one line that reports
hover state, so nothing may be drawn across it afterwards.

The stage 4 target, once fluid lands:
```
fluid → tray cutout treatment (§8) → dark scrim plates → UI text/halos
```

---

## 8. CRITICAL — the bins need a known constant illuminant

> **STATUS: the rule below is no longer what the code does, and this section is
> owed a rewrite (§22). It is kept rather than deleted because the observation
> in it is real and the reasoning still applies to a lit room.** The app draws
> **no black rects** as of `56047b6`; the cutouts are part of a flat white
> field. Read this section together with §14's stage 2 result before changing
> anything about what the projector puts into a bin.

**The surviving rule, which neither version violates: whatever the camera sees
in a bin must be a KNOWN CONSTANT.** Everything below is one answer to that,
correct for one room.

Observed failure: projected content spilled into the tray cutouts (a cloud
image washed pink/white over the food), which contaminates the classifier's
input — it was trained on plain ingredients under ambient light.

**In a LIT room the oF UI must draw a solid black rectangle over every tray
cutout.** The camera then sees food under ambient light only. This is the
original rule and it is right whenever §21's ambient light is present.

**In a DARK room it inverts.** With no ambient there is no "leave the food
alone" option — a black rect over a cutout is not neutral, it is the food in
total darkness, which starves the classifier rather than protecting it. The
choice is projector light or no light, so the whole table becomes a flat white
field and the cutouts are simply part of it. What §8 originally observed was a
*coloured, patterned* image washing over the food; flat white is neither.

**The flat field is only a controlled illuminant if it is actually flat, and
that has never been measured.** See §22 item 1 — this gates the retrain
capture.

---

## 9. Interaction design (decided)

**Hand = obstacle**, not a steam source. Stamp a hard boundary into the
velocity FBO.

**Cursor**: metaball blob with a ~5-point spring chain → comet-tail motion and
an underdamped wiggle on stop. Fragment shader fakes surface normals for a
3D mercury/water-droplet look.

**Blob colour is fixed.** Colour change is reserved *exclusively* for progress
indication. Do not use colour for anything else.

**Tray halos**: radial outward velocity injection + vorticity, no buoyancy.
Colour merges instantly on hover — no dwell timer, because the load cell
confirms the actual pick.

**Buttons**: radial sweep dwell timer. The halo fills with the blob's colour,
then a shockwave on completion.

**Merging**: blob and bin halo merge via a shared metaball field. That field
must store RGBA so colours blend across the merged neck.

**Two hands**: the tongs hand is active; the bowl hand renders as a dim ghost.

Prototype the blob spring chain in a stripped separate project before merging
it into the main app.

---

## 10. Food classification — per-bin, event-driven only

Vision runs inside the Python core (§0.1), on the host CPU/GPU shared with
MediaPipe, and on a host that is also running the fluid sim in the oF process.
**Never run per-frame inference across the whole table.**

Pattern:
```
load cells report a settled weight change on bin N   (settled decided in Python, §0.7)
  → crop bin N's ROI from the latest camera frame    (camera-space bin rect, §0.4)
  → run ONE inference
```

Roughly one 224×224 inference per pick, instead of 30/sec across 8 bins.

Resolution is sufficient: at 1080p each tray occupies ~145 × 215 px.

### It is CLASSIFICATION now, not object detection

**The model gets 8 separate cropped images and answers "what is in this
picture". It does not draw bounding boxes and is never asked where anything is.**

Bin positions are already known geometrically (§0.4, §17) to within a few mm.
A detector spends its capacity re-deriving a location the table has measured,
on a crop where there is only ever one thing to find — and then that derived box
becomes a second, worse answer to a question `bin_offsets.json` has already
answered. Localisation is geometry here, not vision.

**A confidence floor gates billing.** Below the floor the bin is treated as
**unresolved**: it draws empty and it never bills. That lands it in exactly the
existing unresolved path in §11 — no name, no price, no cart line — which is
already the behaviour for a `null` map entry and for an unknown id. A low
confidence guess is the same category of thing as an empty bin, and both must
cost the diner nothing.

### Classes

Roboflow project `tray-detector`, workspace `rahuls-workspace-mqtgo`.

8 classes: `bowl`, `curly_noodle`, `long_noodle`, `dried_prawns`, `mushroom`,
`egg`, `soya_chunks`, `tray` (= empty bin).

- **`tray` stays.** It is not a leftover from the detector — an empty tray is
  how a bin reads as unresolved, so the model needs a way to say "nothing here"
  that is not a low-confidence guess at food.
- **`bowl` likely drops.** The bowl never appears inside a bin crop, so under
  per-bin classification it is a class that can never be the right answer.
- A `tongs` class was tried and **removed** — pickup and put-back are detected by
  load cell weight change, not vision.

### The existing 206 images convert, they do not need recapturing

The annotated set (206 images, §10 *Annotation*) becomes a classification
dataset by **cropping each bounding box out into a class folder**. The boxes
that are being thrown away as an output are still perfectly good as a *cropping
instruction* for building the new dataset.

Detector training history, kept because it is what the conversion inherits:
- Run 1 (25 epochs, YOLO Small, COCO pretrained): mAP@50 46.9%, P 29.2%, R 55.5%
- Run 2 (~100 effective epochs, early-stopped): mAP@50 88.9%, P 100%, R 86.5%, F1 90%

### Training capture, from the staff view

The staff view (§0.5) carries a **training capture button**. One press saves
**all 8 bin crops to a single flat folder**, with the bin index and a timestamp
in each filename.

**The crops are NOT auto-labelled from `bin_map.json`.** Ingredients get
rearranged during a capture run — that is the point of a capture run — so the
bin map is **stale by definition** at exactly the moment the button is useful.
Auto-labelling would produce a dataset that is confidently wrong in a way
nothing downstream can detect. Sorting into class folders is manual, afterwards.

Flat folder, not per-bin subfolders, for the same reason: bin index is capture
provenance, not a label, and a directory tree that looks like a label invites
someone to treat it as one.

**Blocked on the in-bin weight text coming out first — see §22 CARRIED DEBT.**

### Annotation
SAM3 Auto-Label **failed** — it matches shape and texture, not identity, so
visually similar small food items were mislabelled. Fully manual polygon
annotation (206 images) was both faster and more accurate.

### SSCMA / Colab pipeline
- Requires a Python **3.10** conda env via `condacolab` (not 3.12)
- Prefix commands with `MPLBACKEND=Agg conda run -n sscma`
- Roboflow's COCO export injects a placeholder category at **ID 0** (the project
  name). Strip it from **all three** annotation splits before training, or you
  get a CUDA index-out-of-bounds crash.
- "Run All" needs two passes — `condacolab.install()` restarts the kernel

---

## 11. Load cells

- CZL-611N is TAL220 form factor: 80 × 12.7 × 12.7 mm body,
  M4 tapped at the loaded end, M5 at the fixed end
- The 1 kg variant outputs **1.0 ±0.1 mV/V**, not the typical 2.0 —
  tighter signal margin at the HX711
- HX711 VCC and VDD both at 3.3 V from the XIAO's onboard regulator
  (~40 mA total, confirmed sufficient)
- Shared SCK + 8 individual DT pins, `HX711-multi` library
- SCK/VCC/GND wired as a **2-4-8 tree**, not a daisy chain — minimises clock skew
- PCT-215/218 parallel bus terminal blocks for shared nets;
  individual isolated-pole connectors for DT lines
- Load cell side direct-soldered (permanent); MCU side pluggable terminal blocks
- Each of the 8 modules is **fully independent** — no shared rigid base — so
  real plywood cutout positions can deviate from CAD. Use slotted mounting holes.

### Firmware — see §0.7
The XIAO reads the 8 cells and prints **raw counts**, continuously, and does
nothing else. No tare, no commands, no NVS, no calibration factors, no settled
detection, and nothing is ever sent *to* it.

**Per-cell calibration factors are a JSON on the Python side, next to
`bin_map.json`.** Counts → grams is a host-side multiply, so recalibration is a
file edit, never a reflash. Taring is likewise host-side: it is an assignment
into `startWeightGrams[]` (§14), which is why that array is named separately.

**Python decides settled**, because Python owns the classifier gate that
consumes the signal (§10).

### Pricing
- Priced per gram, displayed rounded to 25 g steps
- Quantise **cumulative removed weight**, not individual picks
- Hysteresis at step boundaries to stop flicker
- ~10 g display deadband — **owned by Python** (§0.3), display only, never in
  the price arithmetic

### The menu is TWO files: a catalogue and a bin map

`ingredients.json` is **DATA, not rig state** — unlike `bin_offsets.json` (§17)
it describes the menu, not this particular table, and it is edited by hand
rather than by nudge keys. It is the **only** place names, prices and the
currency live.

`bin_map.json` is the opposite: it is **as-built state**, in the same category
as `bin_offsets.json`. It says what is in each bin *right now*.

| file | is | committed? | written by |
|---|---|---|---|
| `bin/data/ingredients.json` | catalogue: everything the table can price | yes | hand |
| `bin/data/bin_map.default.json` | the seed for the live map | yes | hand |
| `bin/data/bin_map.json` | the live map | **no, gitignored** | seeded from the default on first run; the classifier from stage 3 |
| load cell calibration JSON, alongside `bin_map.json` | counts → grams, per cell | as-built rig state, same category as `bin_offsets.json` | hand, from a calibration run (§0.7) |

**Under §0 the catalogue, the map and the calibration file are all read by the
Python core.** Prices and resolved names reach oF as semantic state (§0.2); oF
no longer loads or reasons about any of the three. §11's rules below describe
the loading behaviour that must survive that move — the no-fallback rule and the
unresolved-bin path are about *what happens on bad data*, not about which
process reads the file, and both still stand verbatim in Python.

```json
// ingredients.json — order carries NO bin meaning, length is NOT 8
{ "currency": "$", "items": [ { "id": "curly_noodle", "name": "...", "pricePer100g": 0.0 }, ... ] }

// bin_map.json — index IS the bin number, exactly 8 entries, null = empty bin
{ "bins": ["curly_noodle", null, ...] }
```

**Split so that a bin's contents can change without touching a price.** Before
step 2a one array did both jobs: entry order was the menu *and* the bin
assignment, so the classifier could not say "bin 3 now holds mushroom" without
rewriting the prices alongside it. The catalogue is now the thing that changes
rarely and by hand; the map is the thing a machine overwrites.

**Ids match the Roboflow class names** (§10) wherever a class exists —
`curly_noodle`, `long_noodle`, `dried_prawns`, `mushroom`, `egg`,
`soya_chunks`. That is what lets the stage 3 classifier write a bare class
string into the map and have it resolve with no translation table in between,
and a translation table is exactly the thing that would let the model and the
menu drift apart unnoticed. Tofu and Baby Corn have no class yet, so they carry
plain ids in the same style — nothing in the file marks them as different, so
the day those classes are trained the ids are already right.

**`bin_map.json` is gitignored on purpose.** Committing it would make every run
of the table produce a diff, and a merge would be two machines disagreeing
about what was in a bin. The default is committed; it is the seed, and it is
the only one of the pair anybody edits.

**The loader is `loadIngredients()` in `src/ofApp.cpp`, over `readCatalogue()`
and `readBinMap()`, and it has no C++ fallback. Do not add a default.** Every
*structural* failure — missing file, wrong shape, missing or empty `currency`,
a bad or duplicated id, a map that is not exactly 8 entries — leaves all eight
labels undrawn and logs the reason. A fallback is a second source of truth that
silently wins exactly when the first is broken, which is exactly when nobody is
watching. A blank label strip is a visible fault; the wrong price is not.

**An unresolved bin is NOT a structural failure.** A `null` map entry, or an id
the catalogue does not have, means that one bin draws no name and no price,
prices at zero, and never reaches the cart — `Ingredient::resolved` is false and
both `drawBinLabels()` and `drawCart()` skip it. An empty bin is a thing a real
table has. One log line per unresolved bin at startup, and only at startup.
The weight readout still draws: there is a load cell under an empty bin too, and
what it reads is not menu data.

**Currency is one top-level field, never per-ingredient.** A table cannot be
priced in two currencies at once, so eight copies would be eight chances to
disagree — invisible until someone reads two bins side by side — and a
per-entry field reads as permission to vary the one thing that must not vary.

The file was a bare top-level array until step 2k; it was wrapped in an object
to give the currency somewhere to live that is not inside one of the entries.
Any loader written against the old shape needs updating.

**All eight prices are PLACEHOLDERS, not a priced menu.** They were 0.0 until
step 3b needed something to multiply — every line and every total read $0.00,
so the grams were exercised and the multiply was not. The current numbers
(0.80–3.20 per 100 g) exist to make the arithmetic visible and are not costed
against anything. **Overwrite them before anyone reads a price off the table.**

The price line is `currency + price + " / 100g"` with two decimals, and the two
decimals are load-bearing: the label strip and the cart's price column are both
sized off these strings, so a price allowed to drop its trailing zeros would let
the menu's digits move the layout.

**The currency is still `"$"` while the table is being built in Kerala.** Nobody
has decided whether the demo prices in rupees or dollars; the field takes either
and only the one field has to change.

---

## 12. Physical build

- 12× 3D-printed hollow tapered pillars: 40 mm base, 30 mm top, 2 mm wall,
  ~70 mm tall, printed vertically — raise the 5.6 mm plywood to tray height
- Tray carrier bracket: flat base plate ~208 × 263 mm, short rising tabs at the
  four mid-sides (not corners — tray corner radii are too large).
  1 mm per-side clearance. Tabs 10–15 mm tall, below the plywood underside.
  Plywood cutout ~214 × 269 mm.
- Thin plywood webs between holes within islands (40 mm) are weak: glue or screw
  30 mm wide × 12–18 mm thick wooden battens to the underside of all four
  internal strips.
- **Do not mount load cells on thermocol.** It compresses and adds noise.
  Rigid substrate only (plywood / acrylic / MDF); thermocol is cosmetic surround.
- Print one test piece, measure with calipers, then batch-print the rest from
  the same spool.

---

## 13. Voice

Validated on the webcam mic with the projector running — no separate USB mic needed.

Measured on Windows: speech −22.6 dBFS, peak −2.0 dBFS, zero clipped samples,
overall SNR 63 dB. Per band: 62 dB (300 Hz–1 kHz), 60 dB (1–3 kHz), 53 dB (3–6 kHz).
No fan whine, no mains hum — the strongest low tone is 156 Hz, i.e. voice pitch.

**Caveat:** the −86 dBFS noise floor is below the physical self-noise of any
small MEMS mic (~−65 to −70 dBFS), so Windows' driver-side noise suppression is
cleaning the signal. On Linux through ALSA the audio is raw — expect 25–35 dB.
Still ample (keyword spotting needs ~10–15 dB) but **do not budget against 63 dB.**

---

## 14. Development order

The system must be demoable at stage 2, and stay demoable through every later swap.

| Stage | Hand | Weight | Ingredient ID | Visuals |
|---|---|---|---|---|
| 1 Loop ✅ | real | — | — | one dot |
| 2 Mocks ✅ | real | keyboard 1–8 | hardcoded | flat bins |
| 3 Sensors | real | load cells | classifier | flat bins |
| 4 Polish | real | load cells | classifier | fluid, blob, voice |

Hand tracking is real from day one because it is the one thing that cannot be
mocked — the whole question is whether the camera/projector loop feels right.

Fluid comes **last**. If the halo lands 30 mm off the bin, you need to already
know it isn't calibration.

**§0 lands inside stage 3.** Stage 3 was always the stage that adds the load
cells and the classifier; it now also moves session truth out of oF into the
Python core (§22 CARRIED DEBT B) and adds the staff view (§0.5). The demoability
rule is unchanged and is the constraint on how that move is sequenced: the table
must still price a pick at every point during it.

### Stage 1 result (complete)
Real hand, real dot, closed loop. Tracker 30 fps (camera-capped), oF 60 fps,
both running together. Two hands tracked and told apart correctly.

**Jitter is a few mm unsmoothed and was judged acceptable, so no filter is
justified yet.** Do not add a one-euro filter until something actually demands
it — the raw number is now known, which is the whole point of having sent
positions unfiltered.

Landmark 9 lands on the knuckle line, between the bases of the fingers, not in
the middle of the palm. Fine for a cursor; revisit when tongs arrive.

Outstanding from this stage: the height-dependent drift in §3.

### Stage 2 UI substrate (complete) — `7ac6756` → `47281e6`

Four commits close the visual substrate stage 2 sits on. The weight mocks
themselves are **not** done and are the next item of work (§22).

| commit | what |
|---|---|
| `7ac6756` | names and prices onto the label strips, from `ingredients.json` |
| `56047b6` | UI inverted: flat white field, **black rects gone**, black text |
| `2c9118c` | currency to `"$"`, `ingredients.json` becomes an object (§11) |
| `47281e6` | stage 1a test pattern off |

What the table now draws: flat white field, eight bin outlines, a name and a
price per bin, the hand dot. Nothing else.

**The test pattern was a bare unconditional draw block** in `ofApp::draw()`,
sitting between `ofBackground()` and `drawBinOutlines()` — no flag, no key
toggle, nothing to switch. Grid, corner-to-corner diagonals, centre crosshair,
corner dots, inset border. It is **commented out and left in place**, not
deleted: it is the instrument that answers "is the projector filling the table,
square and in focus", which is a question that returns every time the projector
moves or a new machine drives it. Uncomment it, use it, comment it out again.

It had to go now rather than at stage 4 because its own comment already called
the conflict known and accepted: the diagonals and grid crossed four of the
eight cutouts, and dark lines over a cutout are the patterned shadow the field
exists to avoid. What made that acceptable was the black rects absorbing it —
drawn *after* the pattern for exactly that reason — and those went with
`56047b6`. **`drawBinOutlines()` must stay after the commented block**, so that
uncommenting it cannot silently put a diagonal back across the hover line.

**Verified in the framebuffer only.** The projected surface has NOT been
inspected by eye — see §22 item 1 for everything that check still owes.

### Stage 2 weight mocks and pricing (complete) — `dc4fc55` → `0c30ab2`

| commit | what |
|---|---|
| `dc4fc55` | keyboard mock weight source, one current weight per bin |
| `0c30ab2` | pricing from cumulative removed weight, 10 g display deadband, cart |

**Verified on the projected surface**, not just the framebuffer — the first
thing in this project that has been.

Keys: `1`–`8` pick from bins 0–7, `q w e r t y u i` put back into the same
eight. Unshifted on purpose — oF hands `keyPressed` the layout-translated
codepoint, so `Shift+1` arrives as `!` on a US layout and as something else
elsewhere, and a shifted-number mapping would need one symbol table per
keyboard.

**Event sizes cycle through 45, 6, 120, 3, 25, 80 g** rather than being one
constant, because every pricing decision is about the size of a weight change.
3 and 6 sit under the deadband, 25 lands exactly on a future quantiser step,
120 is nearly five steps at once. Six sizes against eight bins so the phases
differ. A single-constant mock lets all of that be written and none of it
exercised.

**The model is cumulative and there is no put-back branch.** `removedGrams` is
`startWeightGrams[i] - weight`, clamped at 0 — two absolute weights subtracted,
so a dropped or doubled event cannot bake in. A put-back raises the weight,
lowers the difference, lowers the total. The refund IS the arithmetic. If a
refund branch ever looks necessary, something upstream has started tracking a
running total instead of a weight, and that is the bug.

`startWeightGrams[]` is its own named array precisely because it is what a tare
overwrites: with real load cells, taring is an assignment into it, not an edit.

**This logic is scheduled to move to Python (§0.1, §22 CARRIED DEBT).** The
behaviour below is settled and observed on the table; only its home changes.
Nothing in the pricing model changes with the move — cumulative, linear, no
put-back branch.

### The 10 g deadband is DISPLAY ONLY, and this is the part that gets broken

**Moving to Python — see §0.3 for why.** It gates *when* `displayedWeightGrams[]` catches up to the true weight. It never
enters the price arithmetic. The version it keeps getting "simplified" into:

```cpp
if(fabs(delta) < 10) ignore the event      // WRONG
```

throws small movements away. The real one only makes them **wait**. Two 6 g
picks: the per-event version discards both and charges nothing; the real one
holds at a 6 g gap, then the second makes it 12 g, which crosses, and the
display snaps to the full 12 g. Nothing is discarded — it arrives late.

The snap is to the **current true weight**, never to the threshold or to the old
value plus a step. That is what keeps every stored value a weight that really
existed, so pricing off it is exact arithmetic on a slightly old reading rather
than approximate arithmetic on a reading nobody ever took.

**Observed on the table:** Dried Prawns read 452 g in the bin while the cart
said 45 g removed, i.e. 500 − 455. Three grams of true movement being held. That
is the deadband doing its job, and it is the only way to see it working.

**It has no visible upside yet.** With a keyboard there is no noise, so it only
ever looks like lag. It earns itself when the HX711s arrive and the alternative
is a total twitching at 60 fps on sensor jitter. 10 g is §11's guess and is owed
a measurement of the real noise floor.

---

## 15. Calibration

Primary method: **projected-dot calibration**, not physical markers.

Project 4 bright dots at known screen coordinates with the black tray rectangles
temporarily off. The camera sees them. That yields projector-pixel ↔ camera-pixel
directly — which is the mapping actually needed to draw a halo under a hand.

Self-corrects if the projector shifts. No printing or measuring.

Optional later addition: 4 matte ArUco markers at the table corners, placed
**outside** the projected area and flat on the table surface (not on a raised
tray rim — different plane, wrong homography). Their only job is a startup drift
check: "camera has moved 40 px since last calibration, re-run it."

---

## 16. Calibration conventions — solved, do not re-derive

### Dot 0 is deliberately oversized. Do not make the dots uniform.
Calibration dot 0 (table mm x=44, y=86 — the top-left target) is drawn at
radius **30**. The other eight are radius **20**.

This is load-bearing, not cosmetic. The nine centres are evenly spaced on both
axes, so the pattern maps exactly onto itself under a 180° rotation. The
0° and 180° hypotheses therefore reproject with **identical** error — by
construction, not by coincidence — and cannot be told apart by the error
figure, by RANSAC inlier count, or by eye on the annotated overlay.

The size marker is the only thing that breaks the tie. `solve_homography.py`
picks the orientation whose row-major sort puts the largest blob at index 0.
Equalising the radii silently reintroduces a 50/50 coin flip on orientation,
and a 180°-wrong homography looks plausible until a hand moves the wrong way.

### Solved result
- Camera is mounted **180° relative to the projector**.
- Mean reprojection error **3.66 px**, max **14.98 px** (≈11.7 mm at table scale).
- `tools/calibration/homography.json` holds the matrix. It maps
  **camera pixels → projector pixels** on the RAW, UNROTATED frame
  (`cv2.findHomography(camera_pts, projector_pts)`, src→dst). Consumers need
  that direction as-is — do **not** invert it.

### The max-error point is lens distortion, not a detection fault
The 14.98 px point sits at a frame corner. That is radial lens distortion, and
detection is doing its job. The fix, if it is ever needed, is chessboard
intrinsics plus `cv2.undistort` before detection — not tighter blob tuning.

**Not currently needed.** 11.7 mm is well inside the 205 mm tray width.

### Camera backend must be MSMF on Windows
DSHOW forces auto-exposure, ignores `CAP_PROP_EXPOSURE` entirely, and clips the
white table top to 255 — which buries the dots (measured: board 249, dot 253).
MSMF holds a fixed exposure and the same dots come back with real contrast.

Every script that opens this camera must use MSMF at 1920×1080, so that what
the tracker sees matches the frame the homography was solved on.

### Calibration and tracking need OPPOSITE exposures — set it explicitly
Never rely on the driver default. On this rig it yields a frame averaging
**27/255**: a hand is obvious to the eye, and MediaPipe finds nothing in it at
any rotation or confidence threshold. Raising exposure puts the average near
**121** and the same hand is detected instantly.

This is the failure mode that looks like a broken tracker — the pipeline runs,
FPS is normal, and it reports zero hands forever.

| Script | Wants | Why |
|---|---|---|
| `solve_homography.py` | dark | projected dots must stay separable from a white table |
| `track_hands.py` | bright | the table itself has to be lit to see a hand on it |

`track_hands.py` sets `CAP_PROP_AUTO_EXPOSURE` to 0.25 (manual) and
`CAP_PROP_EXPOSURE` to −4, and prints the achieved mean grey at startup with a
warning below 60. Manual rather than auto deliberately: auto works in a dark
room but will hunt once the projector paints bright UI, and a hunting exposure
changes the image mid-pick.

**Measured, with the oF app projecting:** under `--auto-exposure` the mean grey
drifted 105 → 110 → 116 inside a minute, and that was under mostly-black UI.
The hunt in §16 is not hypothetical.

`--auto-exposure` exists as a diagnostic only — it answers "does the sensor
produce a usable frame when it chooses for itself". Those runs are tagged
`expauto` in the log filename and say so in red in the debug window, so they
cannot be confused with pinned runs. Passing `--exposure` alongside it is
refused rather than ignored, so an exposure sweep cannot silently measure the
same auto-exposed camera at every point.

**MSMF does honour `CAP_PROP_EXPOSURE`** — it is DSHOW that ignores it. The
driver keeps *reporting* −4 whatever you set, so read back frame brightness
rather than the property to tell whether a change took.

---

## 17. As-built rig state — `bin/data/bin_offsets.json`

**`bin_offsets.json` is AS-BUILT RIG STATE, NOT SOURCE.** It describes this
physical table, the way `homography.json` does. It is committed so the dialled-in
alignment survives a clone, not because it is something to design against.

### BINS[] in `TableGeometry.h` stays CAD
`BINS[]` is the drawing. `bin_offsets.json` is the correction from the drawing to
the plywood. **Never edit `BINS[]` — or its chain terms, or the static_asserts —
to fix physical alignment.** Doing that destroys the only record of what was
intended, and the layout asserts stop meaning anything the moment the numbers in
them are measurements rather than a design.

Every alignment fix goes into `bin_offsets.json`, via the on-screen nudge keys.

### What is currently in it
- **Global offset: −4.0, 3.0 mm.** Moves all twelve grid lines together.
- **Per-edge deltas** on top of that, giving corrected bin rects of:

  (These were nudged when the rects were filled black. The fill is gone (§8) but
  the geometry is unchanged — the same rects now drive the bin outlines, the
  label clearance and the hover hit test, all via `binRectPx()`.)

| | measured | CAD cutout (§12) |
|---|---|---|
| widths | 216, 214, 212, 217 mm (cols 1–4) | ~214 mm |
| heights | 267 mm far row, 265 mm near row | ~269 mm |

The CAD baseline is the **plywood cutout in §12**, not `BIN_W_MM`/`BIN_H_MM`. It
comes off the tray chain: tray 205 mm wide (§3) → carrier base plate ~208 × 263
at 1 mm per-side clearance → cutout ~214 × 269. That is the only place those two
numbers are defined, and they have been in this file since the first commit.
Do not compare the measured rects against the 220 × 275 nominal fill rect
(`BIN + 2 × CUTOUT_MARGIN_MM`) — that is the drawn rect, not the hole.

The ~5 mm spread across the columns is real. The eight tray carriers are
independent with no shared rigid base (§11), so the cutouts sit where the build
put them.

Because the nudging aligned the black to the **actual openings**, these measured
sizes are effectively a measurement of the as-built cutouts. Read that way, both
rows came out 2–4 mm shorter than the ~269 mm nominal, and the columns straddle
~214 mm. Widths were cut closer to drawing than heights.

### These offsets absorb TWO errors at once
Cutout position error **and** residual homography error, mixed together and not
separable after the fact.

**Re-solving the homography INVALIDATES `bin_offsets.json`.** The numbers in it
are partly a correction for the old matrix. There is no way to carry them across,
scale them, or subtract the difference out. After any re-solve the file must be
**re-nudged from scratch** — zero it and redo the alignment against the real
cutouts. Same after any change to the plywood or the projector mount.

### Physical check that was passed
Shallow-angle check from the diner side, all 8 bins: black covers the full
opening, and the white band on the plywood around each cutout is still intact.
That is the pair of conditions that matters — black short of the opening lets
projector light into the food (§8), black overrunning onto the plywood eats the
surround the UI needs.

---

## 18. Repo layout

```
hotpot-table/
├── CLAUDE.md
├── README.md
├── src/                    oF renderer
├── bin/data/               fonts, shaders, calibration.json, menu + rig JSON
├── services/
│   ├── hands/              MediaPipe — folds into the core (§0.1)
│   ├── vision/             classifier, event-driven, per-bin (§10)
│   └── voice/              keyword spotting
├── firmware/loadcells/     PlatformIO, XIAO ESP32S3 — raw counts only (§0.7)
├── calibration/
├── experiments/            one folder per experiment, dated
├── models/
└── docs/decisions.md
```

**Every `experiments/` folder needs a README with two lines: the question, and
the answer.** Including failures. That is how the `addTemporalForce` and optical
flow dead ends stop costing time twice.

---

## 19. Docker

**Not used for the oF app.** CPU overhead is not the issue — the issue is that
the app needs GPU, display, camera, USB serial and audio passthrough, which
means running effectively privileged and keeping none of the isolation. The GPU
driver is also the one thing Docker cannot isolate, and it is exactly what
differs between dev machine and reComputer.

The app must be recompiled for Linux regardless, so Docker shortcuts nothing.

Python services: `requirements.txt` + venv is sufficient.

---

## 20. Working style

- **One action at a time.** Wait for confirmation before the next step.
- Short responses, dyslexia-friendly formatting, no long text blocks.
- **Always explain the source of every dimension before locking it.**
  Numbers appearing without reasoning will be challenged.
- Validate end-to-end before optimising. Working > optimal but broken.
- Prototype risky subsystems in isolation first.
- Mock-first: phase 1 UI is entirely hardware-independent.

---

## 21. CRITICAL — the room must be lit

> **UNRESOLVED CONTRADICTION, FLAGGED NOT SETTLED.** The dark-room demo the UI
> was inverted for (`56047b6`, §14) makes the projector's white field the
> illuminant for the food — which is precisely what the third row of the table
> below says it can never be. The two cannot both be law. This section still
> stands as written for the *lit* room, and the tracking failure in it was
> observed on the rig and is not in doubt; what has not been decided is which
> room the build actually targets. **Do not resolve this by editing one section
> to match the other.** §22 item 1 is the measurement that informs it.

**Ambient light is a hard requirement of the build, not a preference about
how the demo looks.** Turning the room lights off breaks three subsystems at
once, for three unrelated reasons. It was observed on the rig: lights off,
hand tracking stopped completely.

This has its own section rather than a line in §8, §10 or §16 because no one
of those owns it. Each of them fails on its own, and reading any one of them
alone makes this look like a tracking problem, which it is not.

| Subsystem | Why a dark room breaks it |
|---|---|
| Hand tracking (§16) | `track_hands.py` sets a **fixed** manual exposure of −4. Fixed means the camera does not compensate when the room dims — the frame collapses toward the 27/255 average that MediaPipe finds nothing in. |
| Food classification (§10) | The classifier was trained on plain ingredients under ambient light. Unlit food is out of distribution. |
| The black rectangles (§8) | *In the lit-room design.* The projector paints solid black over every cutout *on purpose*, so it can put near-zero light into the bins. **The projector therefore cannot be the light source for the food.** That is the design working, not a gap in it. |

The third is the one that closes the trap. Brightening the UI cannot rescue
the other two, because the one place light is needed is the one place the
projector is deliberately forbidden from lighting.

### The exposure is fixed on purpose — do not "fix" it with auto
§16 explains why: auto-exposure works in a still room but hunts once the
projector starts painting bright UI, and a hunting exposure changes the image
mid-pick. Manual is the decision.

Auto also rescues none of the other two failures in the table above — the
classifier and the blacked-out cutouts do not care what the camera does with
its own exposure — so even a well-behaved auto would leave two of the three
standing.

If a dimmer room is genuinely wanted, the lever is `--exposure` (−4 → −3
or −2), re-checking that the reported mean grey stays above 60. The cost is
sensor noise and motion blur at 30 fps. Auto is not the lever.

### The mean-grey warning goes stale the moment it prints
`track_hands.py` samples mean grey **once, during warmup**, prints it, and
never looks again:

```
exposure         : mean grey 117.3/255
```

So a room dimmed *after* startup — someone killing the lights for the
projector mid-demo — fails completely silently. The startup line still says
117, FPS stays a healthy 30, and the tracker reports zero hands forever.
That is §16's "looks like a broken tracker" failure mode with its own
warning disarmed.

**Fix owed:** re-sample mean grey every few seconds and warn on crossing the
floor, rather than trusting one warmup reading for the whole session.

### Python buffers the warning away when logged to a file
Unrelated but found the same way, and it hides everything above. Python
block-buffers stdout when it is not a tty, so `python track_hands.py > log`
holds the entire startup banner — including the mean-grey line and its
warning — in an unflushed buffer, while MediaPipe's C++ stderr goes straight
through. The log looks like the tracker printed nothing but warnings.

**Always run it as `python -u track_hands.py` when redirecting to a file.**

---

## 22. Open questions

### Next session, in this order

**1. Eight-bin illuminant measurement. GATES THE RETRAIN CAPTURE.**
Grey card in each cutout in turn, room lights off, captured through the same
webcam, compare mean grey across all eight.

The flat white field is only a *controlled* illuminant if it is actually flat,
and nobody has measured it. A centre hot-spot with edge fall-off means bins 0
and 7 are lit differently from bins 3 and 4, and the classifier will learn a
position-dependent brightness gradient as a feature — which is the §8
contamination failure arriving as a slow gradient instead of a pink wash. **If
the spread exceeds the tracking margin the fix is per-bin brightness correction
in the UI, not a retrain.**

Also unconfirmed by eye, and cheap to check at the same time: keystone, focus,
stray desktop or cursor on that output, projector black-level glow. Nothing on
the table has ever been inspected physically — every check so far has been a
framebuffer grab, which cannot see any of these.

**2. Split `labelPlacementLogged`, and prove the width check can fire.**
One flag is shared by the advisory "label is wider than its bin" warning and
the fatal "label landed in a cutout" error, so whichever fires first
permanently silences the other.

The check has never been observed firing, which has **two indistinguishable
explanations**: nothing overhangs, or the check is dead. Temporarily shrink one
bin's width until it must trip, confirm the log line appears, revert. Owed
before labels move and before the dataset is captured — the failure it catches
is black text landing on food, which contaminates the capture directly.

**3. ~~Keyboard fake picks, cumulative pricing, deadband, cart.~~ DONE** —
`dc4fc55`, `0c30ab2`, written up in §14. Note the framing in the old version of
this item was wrong in one place and it is worth keeping the correction: it said
put-back "must refund, not be ignored", which reads as a branch to write. It is
not. Under a cumulative model the refund is what the subtraction already does,
and writing the branch would be the bug.

**What is left of the original item:**
- **25 g quantiser on the displayed cumulative figure.** Deliberately not built.
  Price is linear in weight today, and the quantiser is a change to what is
  *shown*, never to the arithmetic — same rule the deadband follows.
- **Hysteresis at the step boundaries.** Only meaningful once the quantiser
  exists; there are no step boundaries to sit on before then.
- **Hover popup on `HOVERED`, and order finalisation.** Untouched. `HoverState`
  still exists and still reaches `HOVERED`; nothing consumes it.

**New debt this work created, both known and deliberate:**
- **The in-bin weight readout draws ink inside the cutouts** — see CARRIED DEBT
  below, where this item now has a second and harder deadline.
- **Holding a put-back key inflates a bin past its start weight.** oF fires
  `keyPressed` on key auto-repeat, so a held `q` puts back dozens of times;
  Curly Noodles was driven to ~2827 g this way. The cart correctly drops the
  line (removed clamps to 0), so nothing is mispriced — but it is worth knowing
  before demoing, and a real load cell cannot be pumped this way.

### CARRIED DEBT

Work that is owed, accepted, and not yet done. Both items below are consequences
of §0 or gate something in it.

**A. The in-bin weight text must come out before the training capture button
exists.** The weight readout draws ink inside the tray cutouts, which is the one
thing §8 forbids. It already owed removal before the retrain capture (item 1
above); §10's capture button makes that deadline harder and earlier, because the
button saves the bin crops *as they appear*. Ship the button first and every
image in the folder has a number burnt across the food — permanently, in the
dataset, discoverable only after training. **Order is: remove the text, then
build the button.** Not the reverse, and not the same commit.

**B. Stage 2 pricing logic currently in oF is scheduled to move to Python.**
`startWeightGrams[]`, `removedGrams`, the 10 g display deadband and the cart all
live in `src/ofApp.cpp` today (§14). Under §0 they are session truth and belong
in the core. **The rework is accepted and is not yet done.** It is real work
being knowingly deferred, not an oversight, and it is not a rewrite of the
model — the arithmetic moves across unchanged:

> `removed = startWeight - currentWeight`, clamped at 0;
> `price = (removed / 100.0) * pricePer100g`.
> **Cumulative and linear. No put-back branch, no refund branch.**

If the move ever appears to need a refund branch, the port has started tracking
a running total instead of two absolute weights, and that is the bug — §14 says
so about the original and it is equally true of the translation.

### Still unresolved (not blocking)

- **Bins 6 and 7 are Tofu and Baby Corn.** Neither survives weeks at room
  temperature in Kerala, and neither has a Roboflow class (§10 lists eight
  classes, and these are not among them). The menu and the model disagree.
  Step 2a made the disagreement *addressable* rather than fixing it: the two
  now carry catalogue ids (`tofu`, `baby_corn`) in the Roboflow style, so
  either the classes get trained under those names or the bins get mapped to
  something else in `bin_map.json` — and neither move touches a price. Until
  then a classifier reporting a class the catalogue lacks lands a bin in the
  unresolved path (§11), which draws empty rather than guessing.
- **§8 needs rewriting, not deleting.** The surviving rule is that bins need a
  known constant illuminant. Black was correct for a lit room, flat white is
  correct for a dark one, and the section currently states only the first as
  law. It carries a status banner in the meantime.
- **Lit room or dark room? §21 and the dark-room demo now contradict each
  other** — §21 says the projector can never be the food's light source, and
  the flat white field makes it exactly that. Both sections carry banners and
  neither has been edited to agree with the other. This is one decision that
  settles §8, §21 and the retrain capture conditions together, so it should be
  made deliberately rather than absorbed into whichever section is edited next.
- **Classifier retrain is mandatory** — ambient-lit weights, the tongs deletion,
  and now the move from detection to per-bin classification (§10). Capture under
  projector white, *after* item 1 above and *after* CARRIED DEBT A.
- Brightness floor is 50%. 25% has not been measured.
- MediaPipe takes ~1 s to acquire a hand, against `HOVER_DWELL_MS` of 1000 ms.
  Those two numbers are the same size, which makes the dwell threshold
  unmeasurable until acquisition is separated from dwell.
- 22 mm labels at 1.4 m have never been checked by eye — the size is derived
  from signage ratios (§7 font sizes), not observed.
- The 2 g pass-over data (n=16) must be **re-run, not extended**.
- Frame rate drops 30 → 15 fps with no hand in frame. Cause unknown.
- Camera elevation angle — measure it. Open since stage 1, and the
  height-dependent drift in §3 caps how accurate any halo can be.
- **reComputer exact model and GPU capability — biggest unresolved risk.**
- Hand id is the tracker's per-frame detection index, so two hands can swap ids
  and therefore colours. Needs a real identity before ids mean anything.
- FBO layering test, once fluid exists: fluid → cutout treatment (§8) → scrim → UI
- Bench test: does the bowl-holding hand false-trigger bin hover zones?
- Bench test: do tongs in hand degrade MediaPipe palm confidence?
- Bench test: do thermocol cubes clear the load cell detection floor?
- Rotating hint line ("say show veg") for filter discoverability — undecided
- Order finalisation: how does "diner is done" get signalled?
- Kitchen hand-off: how does the finished list reach staff? The staff view (§0.5)
  is now the obvious place, but nothing about the hand-off itself is decided.
- Confidence floor value for the classification gate (§10) — the *rule* is
  settled, the *number* is not, and it cannot be picked before the retrain.
- Broth selection: one-time at session start, or changeable mid-session?

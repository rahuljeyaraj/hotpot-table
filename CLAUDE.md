# CLAUDE.md — Interactive Hotpot Ingredient Table

Ground truth for any Claude Code instance starting without context.
**Read this fully before writing code. Several "obvious" approaches here are known dead ends.**

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
| Host PC (dev) / reComputer x86 (target) | Everything: UI, fluid sim, hand tracking, vision, voice |
| 1× USB camera, mounted vertically overhead | Hand tracking AND food classification AND mic |
| 8× CZL-611N 1 kg cantilever load cells | Weight per bin |
| 8× HX711 amplifier boards | Load cell ADC |
| XIAO ESP32S3 | Load cell aggregator → USB serial to host |
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

Camera shadow from the projector is **not** a concern. The projector's light
cone starts at its lens and spreads outward; a camera at lens height, 100–200 mm
to the side, sits outside the cone. General rule: whatever the projector can
light, a camera beside it can see.

---

## 4. Software stack

- **openFrameworks 0.12.1** — main app, C++
- **ofxFlowTools** — GPU fluid simulation
- **ofxGui** — required, ofxFlowTools references it
- **MediaPipe Hands** (Python) — `max_num_hands=2`, `model_complexity=0`
- **OSC** — MediaPipe → openFrameworks
- **JSONL over USB CDC** — XIAO → host
- Voice: openWakeWord or Porcupine on host, using the webcam's own mic

---

## 5. openFrameworks — confirmed facts

### Setup
- oF installed at `C:\openframeworks` (short path, no spaces — required)
- This repo lives at `C:\openframeworks\apps\myApps\hotpot-table\`
  and **cannot be moved** — project files use relative paths to `../../../libs`
- Add addons via projectGenerator, not by hand
- Addon examples must be copied into `apps/myApps/` and re-imported via
  projectGenerator's Import tab before they will build
- VS 2026 retarget is **manual**: Solution Explorer right-click, or the Setup
  assistant's "Retarget all". No automatic prompt appears.
- Build from a Developer Command Prompt for VS so compiler paths are inherited

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
- Bind key `s` to save a screenshot, so a Claude Code instance can inspect
  visual output.

### Layer order
```
fluid → solid black tray rectangles → dark scrim plates → UI text/halos
```

---

## 8. CRITICAL — black rectangles over tray cutouts

The projector must put near-zero light into the bins.

Observed failure: projected content spilled into the tray cutouts (a cloud
image washed pink/white over the food), which contaminates the classifier's
input — it was trained on plain ingredients under ambient light.

**The oF UI must draw a solid black rectangle over every tray cutout.**
The camera then sees food under ambient light only.

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

## 10. Food classification — event-driven only

Vision runs on the host CPU/GPU shared with the fluid sim and MediaPipe.
**Never run per-frame inference across the whole table.**

Pattern:
```
load cell reports settled weight change on bin N
  → crop bin N's ROI from the latest camera frame
  → run ONE inference
```

Roughly one 224×224 inference per pick, instead of 30/sec across 8 bins.

Resolution is sufficient: at 1080p each tray occupies ~145 × 215 px.

### Model
Roboflow project `tray-detector`, workspace `rahuls-workspace-mqtgo`.

8 classes: `bowl`, `curly_noodle`, `long_noodle`, `dried_prawns`, `mushroom`,
`egg`, `soya_chunks`, `tray` (= empty bin).

A `tongs` class was tried and **removed** — pickup and put-back are detected by
load cell weight change, not vision.

Training history:
- Run 1 (25 epochs, YOLO Small, COCO pretrained): mAP@50 46.9%, P 29.2%, R 55.5%
- Run 2 (~100 effective epochs, early-stopped): mAP@50 88.9%, P 100%, R 86.5%, F1 90%

Next: retrain after tongs deletion; `tray` class is the weak one.

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

### Pricing
- Priced per gram, displayed rounded to 25 g steps
- Quantise **cumulative removed weight**, not individual picks
- Hysteresis at step boundaries to stop flicker
- ~10 g detection deadband

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
| 1 Loop | real | — | — | one dot |
| 2 Mocks | real | keyboard 1–8 | hardcoded | flat bins |
| 3 Sensors | real | load cells | classifier | flat bins |
| 4 Polish | real | load cells | classifier | fluid, blob, voice |

Hand tracking is real from day one because it is the one thing that cannot be
mocked — the whole question is whether the camera/projector loop feels right.

Fluid comes **last**. If the halo lands 30 mm off the bin, you need to already
know it isn't calibration.

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

## 16. Repo layout

```
hotpot-table/
├── CLAUDE.md
├── README.md
├── src/                    oF app
├── bin/data/               fonts, shaders, calibration.json
├── services/
│   ├── hands/              MediaPipe → OSC
│   ├── vision/             classifier, event-driven
│   └── voice/              keyword spotting
├── firmware/loadcells/     PlatformIO, XIAO ESP32S3
├── calibration/
├── experiments/            one folder per experiment, dated
├── models/
└── docs/decisions.md
```

**Every `experiments/` folder needs a README with two lines: the question, and
the answer.** Including failures. That is how the `addTemporalForce` and optical
flow dead ends stop costing time twice.

---

## 17. Docker

**Not used for the oF app.** CPU overhead is not the issue — the issue is that
the app needs GPU, display, camera, USB serial and audio passthrough, which
means running effectively privileged and keeping none of the isolation. The GPU
driver is also the one thing Docker cannot isolate, and it is exactly what
differs between dev machine and reComputer.

The app must be recompiled for Linux regardless, so Docker shortcuts nothing.

Python services: `requirements.txt` + venv is sufficient.

---

## 18. Working style

- **One action at a time.** Wait for confirmation before the next step.
- Short responses, dyslexia-friendly formatting, no long text blocks.
- **Always explain the source of every dimension before locking it.**
  Numbers appearing without reasoning will be challenged.
- Validate end-to-end before optimising. Working > optimal but broken.
- Prototype risky subsystems in isolation first.
- Mock-first: phase 1 UI is entirely hardware-independent.

---

## 19. Open questions

- reComputer exact model and GPU capability — **biggest unresolved risk**
- FBO layering test: fluid → black tray rects → scrim → UI
- Bench test: does the bowl-holding hand false-trigger bin hover zones?
- Bench test: do tongs in hand degrade MediaPipe palm confidence?
- Bench test: do thermocol cubes clear the load cell detection floor?
- Rotating hint line ("say show veg") for filter discoverability — undecided
- Put-back handling: weight *increase* should refund, not be ignored
- Order finalisation: how does "diner is done" get signalled?
- Kitchen hand-off: how does the finished list reach staff?
- Broth selection: one-time at session start, or changeable mid-session?

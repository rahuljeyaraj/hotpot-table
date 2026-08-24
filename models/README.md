# Model weights — provenance log

**Weight files are not in git.** No Git LFS either — Roboflow already versions
the weights, so duplicating them here costs quota and breaks clones made on
machines without LFS installed. `.gitignore` drops `*.pt`, `*.onnx`, `*.tflite`,
`*.pth`, `*.weights` and `*.bin` inside this folder. This file is the only
tracked thing in `models/`.

## Where to re-download

Roboflow — workspace `rahuls-workspace-mqtgo`, project `tray-detector`.
Open the project, pick the dataset version listed in the table below, and export
the trained weights for that version. Drop the file into this folder under the
filename in the table; the app expects it there and git will ignore it.

8 classes: `bowl`, `curly_noodle`, `long_noodle`, `dried_prawns`, `mushroom`,
`egg`, `soya_chunks`, `tray` (= empty bin). A `tongs` class was tried and
removed — pickup and put-back are detected by load cell weight change, not
vision.

## How to use this log

One entry per weight file that lands in this folder. Never overwrite an entry —
append a new one and move the **Current** marker. If a field is genuinely not
known, write `(unrecorded)` rather than a guess, so a wrong number never gets
laundered into a fact.

---

## Summary

| # | Filename | Dataset ver. | Epochs | Base model | mAP@50 | P | R | F1 | Date | Current |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | (unrecorded) | (unrecorded) | 25 | YOLO Small, COCO pretrained | 46.9% | 29.2% | 55.5% | — | (unrecorded) | no |
| 2 | (unrecorded) | (unrecorded) | ~100 effective, early-stopped | (unrecorded) | 88.9% | 100% | 86.5% | 90% | (unrecorded) | **yes** |

---

## Run 1 — baseline

- **Filename:** (unrecorded)
- **Roboflow:** workspace `rahuls-workspace-mqtgo`, project `tray-detector`,
  dataset version (unrecorded)
- **Training config:** 25 epochs, YOLO Small, COCO pretrained weights
- **Metrics:** mAP@50 46.9%, precision 29.2%, recall 55.5%.
  F1 not reported; derived from P/R it would be ~38.3%.
- **Date:** (unrecorded)
- **Deployed:** no — superseded by Run 2

Precision of 29.2% means roughly two out of three detections were false. Not
usable.

## Run 2 — current

- **Filename:** (unrecorded)
- **Roboflow:** workspace `rahuls-workspace-mqtgo`, project `tray-detector`,
  dataset version (unrecorded)
- **Training config:** ~100 effective epochs, early-stopped.
  Base model and pretrained weights (unrecorded) — confirm before retraining.
- **Metrics:** mAP@50 88.9%, precision 100%, recall 86.5%, F1 90%
- **Date:** (unrecorded)
- **Deployed:** **yes — this is the current model**

Trained on 206 manually polygon-annotated images. SAM3 Auto-Label was tried and
failed: it matches shape and texture rather than identity, so visually similar
small food items were mislabelled.

Note: the reported F1 of 90% does not match the value derived from the reported
P/R (~92.8%), which usually means the headline figures were read at different
confidence thresholds. Worth pinning down the threshold on the next run.

---

---

## `hand_landmarker.task` — MediaPipe Hands (M5's tracker)

Not a trained-here model: a **stock Google-published MediaPipe Tasks bundle**,
so it has no dataset, no epochs and no metrics of ours to record. What matters
about it instead is where it came from and what the code expects.

| | |
|---|---|
| Filename | `models/hand_landmarker.task` |
| Source | `https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task` |
| Size | 7,819,105 bytes |
| Downloaded | 2026-08-12 |
| Used by | `python/hotpot/tracker/backend_mediapipe.py` |
| Current | **yes** |

**Re-download with:**

```
curl -L -o models/hand_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
```

Gitignored (`models/**/*.task`), same rule as every other weight file here.
A missing bundle is **not** a crash: `tracker/build_backend()` falls back to
`backend_stub` and logs a line pointing at this file, because doc §3.3 requires
every process to come up and hold its link open regardless of what is absent.

**The API this is loaded through is not the one the architecture doc describes.**
Doc §11.1–§11.3 are written against `mediapipe.solutions.hands`, which **does not
exist** in the installed mediapipe 1.0.0 (`mp.solutions` is gone entirely —
verified, not remembered). The Tasks API replaces it, and it takes this `.task`
bundle rather than a `model_complexity` integer. See `backend_mediapipe.py`'s
module docstring for the full list of consequences.

**Doc §11.2's model ladder therefore has one rung on this rig.** `model_complexity
0 → 1` selected between two bundled `.tflite` landmark models in the old API;
here the rung *is* which bundle you point at, and Google publishes one. The
tracker's probe still measures and logs the achieved rate, and
`backend_mediapipe.MODEL_RUNGS` is the ordered ladder a second bundle would slot
into — but nothing pretends a one-rung ladder was climbed.

**Measured on the dev machine, 2026-08-12, and this number does NOT transfer to
the deploy board:** 11.1 ms median per inference at 480×270 with no hands in
frame (~90 fps). This machine has AVX2; the ODYSSEY J4125 does not (doc §1.4b),
which is precisely the instruction set MediaPipe's x86 inference leans on. The
`SIGILL` check doc §11.2 requires is still owed and can only be done on the board.

---

---

## `hotpot-ingredients` — Edge Impulse (doc §19.2, M7)

**Not an `.eim` file.** Deployed as a **C++ library** instead of the
`Linux (x86_64)` `.eim` doc §19.2 originally specified — see
`tools/eim_cpp/CMakeLists.txt`'s top comment for the full reasoning
(short version: this dev machine is Windows, `edge_impulse_linux`'s
`ImageImpulseRunner` needs Linux, and the C++ library is the one export
that compiles on both this machine and the ODYSSEY, doc §1.4, from the
identical source). `classifier/backend_ei.py` (`EiCppBackend`) shells out
to the compiled `tools/eim_cpp/build/classify[.exe]` binary rather than
loading a `.eim` at runtime.

| | |
|---|---|
| EI project | `hotpot-ingredients`, id `1087506`, owner `rahuljeyaraj` |
| EI deploy version | 2 |
| Deployment target | C++ library, EON Compiler, quantized (int8) |
| Input | 160×160 RGB, squash resize |
| Classes (8) | `button_mushrooms`, `chicken_eggs`, `instant_noodle_block`, `loose_straight_noodles`, `lotus_root_slices`, `small_round_rusk`, `soya_chunks`, `white_rusk` |
| Validation accuracy | (unrecorded) — read it off the Studio training page and fill this in |
| Confusion matrix | (unrecorded) |
| Dataset session ranges | One capture session, 2026-08-13 — see `tools/export_edgeimpulse.py`'s own dry-run output for exact per-class counts. **Well short of doc §19.2's ≥150 images/class across ≥4 sessions; treat this deploy as a toolchain check, not a trained model.** |
| Downloaded | 2026-08-13, `models/hotpot-table-cpp-mcu-v2-impulse-#1.zip` (gitignored, `models/*.zip`) |
| Current | no — superseded by project `1095598` below |

### Entry 2 — project `1095598`, 13 classes (2026-08-24)

A different Studio project, not a retrain of `1087506`. Four projects on
this account are now called `hotpot-ingredients` (`1087506`, the two empty
duplicates `1095239`/`1095356` that the panel's "Create new project"
button made, and this one) — **the id is the only thing that tells them
apart**, which is why the staff view's "Linked to ..." line now prints it.

| | |
|---|---|
| EI project | `hotpot-ingredients`, id `1095598`, owner `rahuljeyaraj` |
| EI deploy version | 1 (job `53034211` metadata, impulse #1) |
| Deployment target | C++ library, EON Compiler, quantized (int8) — unchanged |
| Input | 224×224 RGB, squash resize — **doc §19.2 was amended to match this** (developer's call, 2026-08-24). It previously said 160×160, which is what entry 1 used; the size was raised by hand when this impulse was configured, and 224×224 is now the spec, not a drift from it. `tools/eim_cpp/` reads the size out of the export either way. |
| Classes (13) | `button_mushrooms`, `chicken_eggs`, `dried_mango_strips`, `empty_tray`, `flat_round_cookies`, `instant_noodle_block`, `loose_straight_noodles`, `lotus_root_slices`, `no_tray`, `small_round_rusk`, `soya_chunks`, `white_rusk`, `yellow_rusk` — read out of the export's own `model-parameters/model_variables.h`, and an exact match for the 13 `datasets/captures/` folders |
| Trained | 2026-08-24T12:12:13Z (EI learn block 3, `Classifier`), 10 cycles, learning rate 0.0005, transfer-learning "visual" mode |
| Validation accuracy | **99.69%** int8 (loss 0.0138); float32 is the same 99.69% (loss 0.0143). Both variants exist and EI recommends int8 — which is what this deploy uses. |
| Confusion matrix | 323 validation samples, **one** off-diagonal cell: `dried_mango_strips` → `small_round_rusk`, 1 sample. Every other class is clean. Read off EI's own learn-block metadata, not eyeballed from the Studio page. |
| Dataset session ranges | 2034 local captures, 150–192 per class (`datasets/captures/`), every class at or above doc §19.2's ≥150 images/class. The ≥4-sessions-on-different-days half of that target is (unrecorded). Closes entry 1's `dried_mango_strips` / `flat_round_cookies` / `yellow_rusk` gap; `dried_small_shrimps` is still absent, now alongside the rest of the 12-item catalogue's untouched entries. |
| Downloaded | 2026-08-24 17:52:38 local, `models/hotpot-ingredients.zip`, 7,372,368 bytes (gitignored, `models/*.zip`). Fetched by the staff view's own Download path (`ei_client.download_model()`), from build job `53034211`; EI reports this project's `zip`/`int8` deployment at version 2. |
| Current | **yes** |

**2026-08-24, later the same day:** was sitting downloaded-but-not-deployed
for hours before this was noticed — the staff view's Download button wrote
the zip and stopped there, and unzipping it over `tools/eim_cpp/vendor/` +
rebuilding `classify.exe` was a separate manual step nothing prompted for.
`_handle_ei_download` (`core/main.py`) now does both automatically as part
of the same click (`classifier/ei_deploy.py`); see that module's docstring.
`backend_ei.py` also stopped hardcoding the model's input size as a Python
constant that had to be bumped by hand alongside the rebuild — it now reads
`EI_CLASSIFIER_INPUT_WIDTH`/`HEIGHT` out of the freshly-unzipped
`model_metadata.h` on every classify() call, so a classifier process
already running when a redeploy finishes picks up the new model on its
very next live pass, no restart required. Pressing Download is now the
whole redeploy, not half of it.

**Known gap, not yet closed: `dried_small_shrimps` is not one of the 8
classes.** The catalogue has 12 items; only 9 have any captures at all
(`datasets/captures/`), and `dried_small_shrimps` had only 4 images at
export time — thin enough that it did not make it into this training run.
A bin actually holding dried shrimp will be classified as one of the
other 8 (most likely `soya_chunks` or `button_mushrooms` — doc §22's own
"known-hard set"), not as "unrecognised". Capture more shrimp images,
re-export (`tools\upload_edgeimpulse.ps1`), retrain, and redeploy before
trusting a shrimp bin's label. `dried_mango_strips`, `flat_round_cookies`
and `yellow_rusk` (3 more catalogue items) have no captures at all yet and
have the same gap.

**Re-download:** the staff view's Capture tab now has an "Edge Impulse"
panel (doc §19.2/19.5) that does the login/link, image upload, and
build+download steps below over EI's REST API — see
`python/hotpot/classifier/ei_client.py`/`ei_store.py` and
`core/main.py`'s `_handle_ei_link`/`_handle_ei_upload`/
`_handle_ei_download`. It no longer stops at saving `models/<project
name>.zip` — unzipping over `tools/eim_cpp/vendor/` and rebuilding
`classify.exe` happen automatically as part of the same click
(`classifier/ei_deploy.py`, added 2026-08-24). Training itself still stays
manual in Studio — the
panel does not configure the impulse's image input/DSP/MobileNetV2
transfer-learning blocks (a fresh Link creates a bare project; wire up
the impulse once by hand, same as `hotpot-ingredients` already is,
before the first Upload trains anything useful) or click Train.

Equivalently, by hand: Studio → `hotpot-ingredients` project → Deployment
→ `C++ library`, same EON/int8 settings as above, then unzip over
`tools/eim_cpp/vendor/` and rebuild (that directory's own CMakeLists.txt
has the MSVC/nmake steps this was last built with). `tools/export_edgeimpulse.py`
+ `tools/upload_edgeimpulse.ps1` (the Node `edge-impulse-uploader` CLI
path) still work too and are unchanged — the staff-view panel is a second
way to do the same upload, not a replacement that removes the first.

## Known next step

Retrain after the `tongs` class deletion. `tray` (empty bin) is the weak class.
When that run lands, add it as entry 3 and move the **Current** marker.

## Unfilled fields

Filenames, dataset version numbers and dates were not recorded for either run —
they are not in the project notes these entries were seeded from. Both are
recoverable from the Roboflow project's version history; fill them in when
convenient, because "which dataset version produced the deployed weights" is
exactly the question this log exists to answer.

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

## Known next step

Retrain after the `tongs` class deletion. `tray` (empty bin) is the weak class.
When that run lands, add it as entry 3 and move the **Current** marker.

## Unfilled fields

Filenames, dataset version numbers and dates were not recorded for either run —
they are not in the project notes these entries were seeded from. Both are
recoverable from the Roboflow project's version history; fill them in when
convenient, because "which dataset version produced the deployed weights" is
exactly the question this log exists to answer.

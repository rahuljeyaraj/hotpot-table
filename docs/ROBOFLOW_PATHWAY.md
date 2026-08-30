# ROBOFLOW PATHWAY — a second training/deploy path beside Edge Impulse

**Status, updated 2026-08-26: steps 1-7 of §6's build are code-complete
(one session, developer instruction: "implement the whole feature before
reporting back" — so this ran through without the usual per-step
stop-and-report). Step 8 (provenance) has an honest placeholder in
`models/README.md` rather than a real entry, and step 9 (the rig) has not
been touched at all. See "SESSION LOG — 2026-08-26" at the end of this
file for exactly what was built, what is still only reasoned from
documentation, and what is still owed. Nothing here has been run against
a live Roboflow account, a real camera, or the rig — every VERIFY note
this document and the new code carry is still open.**

Developer instruction (2026-08-26): *"instead of edge impulse make a
pathway to roboflow, u can keep the edge impulse interface as it is, but
add a new interface to do the same with roboflow."*

So this is **additive**. Every `ei_*` file, the `EiCppBackend`, the Capture
tab's Edge Impulse card and `tools/eim_cpp/` stay exactly as they are and
keep working. Roboflow arrives as a sibling set of files behind the same
`ClassifierBackend` Protocol (doc §19.4), selected by one config value.
Nothing upstream of `classifier/build_backend()` learns that a second
option exists.

---

## 0. Why this is worth doing at all

`classifier.enabled` has been `false` in `config/system.default.json`
since 2026-08-14 because the Edge Impulse model's live accuracy was not
good enough to trust, and the fallback built for it — the Bins tab's
manual item override (2026-08-24) — exists precisely because a wrong
label had no other cure. The EI model reports **99.69% validation
accuracy** (`models/README.md`, project `1095598`) and still is not
trusted live. That gap between studio accuracy and table accuracy is the
actual problem, and a second platform is a reasonable thing to try
against it.

**This project already has Roboflow history.** `models/README.md` records
workspace `rahuls-workspace-mqtgo`, project `tray-detector` — 8 classes,
206 polygon-annotated images, run 2 at mAP@50 88.9%. That is an **object
detection** project from Stage 1/2, not what is needed here. See §3.1.

---

## 1. THE ONE DECISION TO MAKE BEFORE ANY CODE IS WRITTEN

Edge Impulse's chain ends in an artifact this repo owns outright: a C++
library zip → `tools/eim_cpp/vendor/` → a compiled `classify.exe` that
runs with no network and no account. **Roboflow has no free equivalent of
that**, and this is the single fact that shapes the whole build.

Two paths. Pick one before step 1.

### Path A — `inference` package, weights cached locally (RECOMMENDED)

Roboflow's own supported local-deployment route.
`inference.get_model(model_id, api_key)` downloads the trained weights on
first use and caches them; **subsequent runs are offline**
([Roboflow Inference — offline weights](https://inference.roboflow.com/using_inference/offline_weights_download/)).
`MODEL_CACHE_DIR` pins the cache somewhere that survives a reboot —
without it the default is `/tmp/cache`, which does not.

| | |
|---|---|
| Cost | Works on the free tier |
| Internet | Once per model version, then offline |
| Artifact | An opaque cache directory, not a file this repo versions |
| Deploy-board risk | **Real.** See §4.1 |
| Dependency weight | Heavy. See §4.3 |

### Path B — ONNX export + `onnxruntime`

Download the trained weights as ONNX, drop them in `models/`, run them
with plain `onnxruntime`. Fully offline forever, a single file this repo
controls, near-identical in spirit to the EI zip.

**Blocked on cost:** *"Manual weights download is only available for paid
users on Core plans and certain Enterprise customers"*
([Roboflow Docs — Download Model Weights](https://docs.roboflow.com/models/model-weights/download-roboflow-model-weights)).
Roboflow also states outright that it *"does not provide technical
support for model weights used outside of the Roboflow Inference
ecosystem."*

| | |
|---|---|
| Cost | **Paid plan required** |
| Internet | Once, ever, to fetch the file |
| Artifact | One `.onnx` in `models/`, gitignored, logged in `models/README.md` |
| Deploy-board risk | Lower. See §4.1 |
| Dependency weight | Light (`onnxruntime` only) |

### Recommendation

**Start on Path A.** It is free, it is the route Roboflow supports, and
the ODYSSEY is not in hand yet (CLAUDE.md: Edge Impulse training was
"blocked on hardware on order") so the board risk in §4.1 cannot be
measured today anyway.

**But build `backend_rf.py` with two classes behind one Protocol from the
start** (§6.1), so Path B is a config value and a file drop, not a
rewrite. The whole reason doc §19.4 made the backend split "mandatory"
was so a choice like this could be made per machine — this is that
mechanism being used a second time, exactly as intended.

**Do not** use Roboflow's hosted inference API (a network round trip per
classify). The table is a dim-room installation with no guaranteed
route off the network; `core/scale.py` and every other subsystem here is
built to keep working when something is unplugged, and a classifier that
needs the internet at 2 Hz is not.

---

## 2. THE TEMPLATE — what already exists, file by file

Read these five files before writing anything. The Roboflow path is a
deliberate mirror of them, and every design argument in their docstrings
applies again unless this document says otherwise.

| Edge Impulse file | What it does | Roboflow sibling to write |
|---|---|---|
| `classifier/ei_client.py` | REST: login, create/adopt project, upload images, build, download | `classifier/rf_client.py` |
| `classifier/ei_store.py` | `state/ei_project.json` — the saved link + API key, written 0600 | `classifier/rf_store.py` |
| `classifier/ei_deploy.py` | Turns the downloaded zip into a runnable artifact (unzip + rebuild) | `classifier/rf_deploy.py` |
| `classifier/backend_ei.py` | `ClassifierBackend` — `classify(bgr_crop) -> (label, conf)` | `classifier/backend_rf.py` |
| `core/main.py` `_handle_ei_*` | Wire handlers + broadcasts to the staff view | `_handle_rf_*` |

Plus: `core/web/static/index.html`'s `#eiCard` (the Capture tab panel),
`config/system.default.json`'s `classifier.backend`, and
`classifier/main.py`'s `build_backend()`.

### 2.1 The contract that must not change

`classifier/main.py`'s `_classify()` calls, per bin, concurrently:

```python
label, conf = self.backend.classify(patch)
```

`patch` is a **BGR numpy array**, already cropped to the bin rect out of
a frame that has already been warped to stage space by
`geometry.warp_frame_to_stage`. The backend does the resize to whatever
its own model wants and nothing else. It raises
`backend_ei.ClassifierBackendError` on failure — and that exception type
is caught by name in `_classify`, so `backend_rf.py` must raise **that
same class**, not a new one, or one bin's failure will crash the whole
pass instead of leaving that bin unresolved (doc §9.3).

That is the entire integration surface. Everything else in this document
is about getting a trained model to the point where that one method can
be written.

---

## 3. WHAT ROBOFLOW CHANGES vs EDGE IMPULSE

### 3.1 A new project is needed — the existing one is the wrong type

`tray-detector` is **object detection**. What this needs is
`single-label-classification`: one bin crop in, one label out. Roboflow's
project types are fixed at creation and are not convertible.

Create a new project of type `single-label-classification`. Suggested
name `hotpot-ingredients` to match the EI side; the workspace is the
existing `rahuls-workspace-mqtgo`.

### 3.2 The dataset layout is already correct — this is the big win

`datasets/captures/<label>/*.jpg` is *exactly* Roboflow's folder-per-class
convention for single-label classification. Edge Impulse needed
`upload_captures()` to invent a `<label>.<filename>` prefix and pass the
label in an `x-label` HTTP header; Roboflow takes the class name directly.

The Roboflow Python SDK's `project.upload()` takes, for a classification
project, *"the annotation parameter can be a class name, such as
`dog`"* — so the label is a plain argument, no header, no prefix
gymnastics.

**The one rule that carries over unchanged:** the sidecar `.json` files
`classifier/main.py::_capture` writes beside every image are
**provenance, not training data** and must never be uploaded.
`ei_client.iter_label_images()` already filters to
`IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")` — reuse that function
directly rather than writing a second one that could drift from it.

### 3.3 Training is a real API call, not a manual Studio step

This is the genuine improvement over Edge Impulse. `ei_client.py`'s
module docstring explains at length why it deliberately does **not**
configure the impulse — guessing EI's image-transfer-learning JSON shape
risked silently producing a broken impulse — so training stayed a manual
Studio step forever.

Roboflow exposes the whole chain:

1. **Generate a dataset version** (preprocessing + augmentation settings).
2. **Train**: `POST /{workspace}/{project}/{version}/train` with a
   `model_type` body field.
3. **Poll**: `GET /{workspace}/{project}/jobs/{jobId}` — the train call
   returns when the job has *started*, not when it has finished, same as
   `ei_client.wait_for_job()` already handles for EI.

So the Roboflow panel can plausibly do link → upload → **train** →
deploy, where the EI panel does link → upload → *(go do it by hand)* →
deploy. Build it that way.

### 3.4 There is no rebuild step

`ei_deploy.rebuild()` shells out to `rebuild.bat` because the EI artifact
is C++ source that has to be compiled. Neither Roboflow path compiles
anything: Path A caches weights, Path B writes one `.onnx`.
`rf_deploy.py` is therefore much smaller than `ei_deploy.py` — mostly
"fetch, verify, put it where the backend looks, report what happened."

`ei_deploy.py`'s wipe-then-extract reasoning still applies in spirit:
**never leave the previous model's file sitting next to the new one.**
The EI bug that argument was written against — two `tflite_learn_*.cpp`
files both picked up by a glob — has a direct analogue here if
`models/` ends up with `roboflow-v3.onnx` and `roboflow-v4.onnx` and
anything resolves by pattern instead of by an explicit recorded name.
Record the exact filename in `state/rf_project.json`; never glob.

---

## 4. CONSTRAINTS AND RISKS — read before committing to Path A

### 4.1 AVX2 on the deploy board

CLAUDE.md's TOP RISKS, unchanged since M0: *"No AVX2. MediaPipe or a
.eim may not run at all. Prove both on the board in M0.B."* The ODYSSEY
X86J4125 has no AVX2.

Roboflow Inference uses **OpenVINO** as its CPU execution provider on
Intel hardware. Intel's own documentation states that *"starting with
OpenVINO release 2026.0, the CPU plugin will require support for the AVX2
instruction set as a minimum system requirement. The SSE instruction set
will no longer be supported"*
([OpenVINO CPU device docs](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/cpu-device.html)).

**Consequence, stated plainly:** Path A on the ODYSSEY is at genuine risk
of not running at all, in exactly the way MediaPipe already is. Path B's
plain `onnxruntime` CPU execution provider has no such hard requirement —
it falls back to slower generic kernels — which is why §1 says to keep
Path B one config value away.

This is **unmeasurable today** (no board). Do not spend time on it now;
do write it into the NUMBERS OWED list so it is measured the day the
board arrives, alongside the MediaPipe `SIGILL` check that is owed on the
same hardware for the same reason.

### 4.2 Free tier limits

Hosted training on the free tier is credit-limited. Confirm the account's
remaining credits **before** step 3, not after uploading 2000 images. If
credits are exhausted the fallback is to train in a Roboflow-provided
notebook and upload the weights back — which is a different flow than
this document describes and would need its own plan.

### 4.3 Installing `inference` may break the running app — VERIFY FIRST

This is the most likely way this work goes wrong on day one, and it has
nothing to do with Roboflow's API.

The rig's active interpreter is `C:\pio-core\penv` (a PlatformIO venv —
see CLAUDE.md's 2026-08-24 session notes, and the standing memory that
`penv` runs the app while `Python310` only has pytest). It currently
carries **numpy 2.4.6, opencv 5.0.0, mediapipe** — and the app depends on
all three at runtime.

`inference` pulls a large dependency tree (torch, transformers,
onnxruntime, opencv, numpy pins). **A pip install that downgrades numpy
or opencv silently breaks the tracker and the camera process**, and the
symptom would be a rig that stops tracking hands with no obvious
connection to the Roboflow work.

**Do not `pip install inference` into `penv` as the first move.** Install
into a throwaway venv first, confirm the inference call works there,
*then* decide whether it can safely share `penv` — and if it cannot, that
is itself an argument for Path B, whose only new dependency is
`onnxruntime`.

### 4.4 Offline Mode is an enterprise feature

Roboflow's *"Offline Mode"* — the one with a 30-day weight lease that
renews — is an **Enterprise** feature for their hosted inference server.
It is not what Path A uses and not what this project needs. Path A's
offline property comes from the ordinary local weight cache. Do not
confuse the two when reading their docs.

---

## 5. VERIFY BEFORE YOU BUILD

CLAUDE.md: *"Never assume an external API exists. Verify against the
installed version. Items marked VERIFY in the doc are where this has
already gone wrong."*

`ei_client.py` is the standing proof of why. Its `download_model()`
docstring records a live failure where a build job finished successfully,
the download 500'd with *"No deployment exists, did you build yet?"*, and
the real cause was one missing `modelType` query parameter. That cost a
session. Every endpoint below is written from documentation, **not from a
live call**, and must be probed before code is written against it.

Run these against the real account, from a scratch script, and **write
the actual responses into this file** before step 3.

| # | What to confirm | How |
|---|---|---|
| V1 | API key works, workspace/project resolve | `GET https://api.roboflow.com/?api_key=...` |
| V2 | The classification project exists and reports its type | `GET https://api.roboflow.com/{workspace}/{project}?api_key=...` |
| V3 | A single image uploads **with a class label attached** | one `project.upload(image_path, annotation="<class>", split="train")` — then look at it in the Roboflow UI and confirm the class actually stuck |

**V1 and V2 run for real, 2026-08-26 — closing both notes.** Real
responses, this account:

```
V1  GET https://api.roboflow.com/?api_key=...  ->
{
  "welcome": "Welcome to the Roboflow API.",
  "instructions": "You are successfully authenticated.",
  "docs": "https://docs.roboflow.com",
  "workspace": "rahuls-workspace-mqtgo",
  "workspaceId": "Kd3Xd8xZW6Wlp1jlKPfadCwrM5y2"
}

V2  GET https://api.roboflow.com/{workspace}/{project}?api_key=...  ->
{
  "workspace": {"name": "...", "url": "rahuls-workspace-mqtgo"},
  "project": {
    "id": "rahuls-workspace-mqtgo/<slug>",
    "type": "object-detection" | "classification",   <- NESTED under "project", not top-level
    "multilabel": false,   <- only present on a classification project;
                               distinguishes single- vs multi-label
    ...
  },
  "versions": [...]
}
```

**This V2 shape is what a real, live incident was found against, the
same day.** `core/main.py._handle_rf_link`'s original type check read
`project.get("type")` at the TOP level, which is always `None` against
this real shape (the field is one level down, under `project`) — so the
check never once refused a wrong-type project. This table ended up
linked to a real, pre-existing project, `rahuls-workspace-mqtgo/
food-classifier` — **object detection** (Baked Potato/Burger/Crispy
Chicken/Donut, unrelated to this feature) — with no refusal, and every
subsequent image upload against it failed 100% of the time (the SDK's
classification-shaped `annotation=<class name>` upload is the wrong
shape for an object-detection project). Fixed: the check now reads the
nested `project.type`/`project.multilabel` fields. Also newly confirmed:
the literal string `"single-label-classification"` (this doc's own
assumed value, §3.1) **never appears in a GET response at all** — only
as a `type` value accepted by the CREATE endpoint. A real single-label
project reports plain `type: "classification"` with `multilabel: false`
over GET. See `rf_client.get_project`'s and `_handle_rf_link`'s own
docstrings for the full account.

A correct project (`rahuls-workspace-mqtgo/hotpot-ingredients`, type
`single-label-classification`) was created via the CREATE endpoint the
same day (`POST /{workspace}/projects?api_key=...`, body
`{"name", "type": "single-label-classification", "license", "annotation"}`
— **not yet wrapped in `rf_client.py`**, called ad hoc; wrapping it as a
proper function is still owed if project creation ever needs to happen
from the app itself rather than by hand).
| V4 | Version generation returns a version number | SDK `generate_version()` |
| V5 | Train starts and returns a job id | `POST /{workspace}/{project}/{version}/train` |
| V6 | Job polling reports finished/failed distinguishably | `GET /{workspace}/{project}/jobs/{jobId}` |
| V7 | **Path A**: `get_model()` loads and `infer()` returns a class + confidence for one bin crop | throwaway venv, one real JPEG from `datasets/captures/` |
| V7b | **Path A**: it still works with the network off, after `MODEL_CACHE_DIR` is set | pull the ethernet / disable wifi and re-run |
| V8 | **Path B only**: weights download is actually permitted on this account's plan | `model.download()` |

**V3 and V7 are the two that can sink the whole plan.** V3 because a
classification upload that silently lands unlabelled produces a dataset
that looks full and trains to nothing; V7 because if inference does not
run on this machine there is no point building the eight files around it.
Do both first.

---

## 6. THE BUILD — one step per session, commit after each

Ordering note: this deliberately builds the **backend first**, before any
of the REST plumbing. That inverts the EI build order and it is on
purpose — the same reasoning M2.1 used when it built `loadcell_cal.py`'s
maths before the serial thread that consumes it ("the dependency runs the
other way"). If `backend_rf.py` cannot run a model on this machine, every
other file here is wasted work. De-risk first.

Each step below ends with: run `python -m unittest discover -s python/tests`,
commit, **stop and report back**.

---

### Step 1 — `classifier/backend_rf.py`

**Goal:** one bin crop in, `(label, confidence)` out, no network, no core,
no wire.

**Files:** `python/hotpot/classifier/backend_rf.py`,
`python/tests/test_backend_rf.py`

**Design:**

- Two classes behind the one Protocol, per §1's recommendation:
  - `RoboflowInferenceBackend` — Path A. Holds a `get_model()` handle,
    loaded **lazily on first `classify()`**, not in `__init__`. The EI
    backend's precedent applies: a fresh clone with no model must still
    boot a classifier process (doc §3.3), so construction must never be
    what fails.
  - `RoboflowOnnxBackend` — Path B. `onnxruntime.InferenceSession` over a
    file in `models/`, plus the class-name list.
- **Both raise `backend_ei.ClassifierBackendError`** — see §2.1. Import
  it; do not define a parallel exception. (If that import direction feels
  wrong, the correct fix is to move the class into a shared module and
  re-export it from `backend_ei` for compatibility — but do that as its
  own commit, not folded into this one.)
- **Import the heavy dependency inside the method, not at module scope** —
  the same seam `core/scale.py` uses for pyserial and `common/geometry.py`
  uses for cv2, and for the same reason: the module must stay importable
  and testable on a machine with neither `inference` nor `onnxruntime`
  installed. This is what lets `test_backend_rf.py` run in CI-less
  isolation on any clone.
- **A test seam for the model object**, the same role `run` plays in
  `EiCppBackend` and `open_port` plays in `ScaleReader`: a constructor
  parameter that a test passes a fake through, so the whole crop →
  resize → predict → `(label, conf)` path is exercised with no real model
  on disk.
- **Read the class list from the artifact, never hardcode it.**
  `backend_ei.py` learned this the hard way — its `_InputDims` class
  exists because a hand-maintained input-size constant let a redeployed
  224×224 model sit on disk while the code kept resizing to the previous
  model's 160×160. Same trap, same cure: whatever ships alongside the
  Roboflow model that names its classes is the source of truth, re-read
  when its mtime changes.
- **Colour order.** The crop arrives BGR (OpenCV). Roboflow models are
  trained on RGB. `backend_ei.py` does `cv2.cvtColor(resized,
  cv2.COLOR_BGR2RGB)` and its docstring flags the raw-RGB-not-BGR
  requirement in capitals. Get this wrong and the model returns confident
  wrong labels with no error anywhere — the worst failure mode in this
  whole feature. **Write a test that would fail if the conversion were
  removed.**

**Tests:** identity/fake-model path, the BGR→RGB conversion, the resize to
the model's own input size, a missing model file raising
`ClassifierBackendError` (not `FileNotFoundError`), the class list being
re-read after the artifact changes.

**Mutation checks (this codebase's standard — every check must be capable
of failing):** delete the BGR→RGB conversion → the colour test goes red.
Hardcode the class list → the re-read test goes red. Return the raw
model output instead of the argmax label → the label test goes red.

---

### Step 2 — `classifier/rf_store.py`

**Goal:** `state/rf_project.json` — the saved link.

**Files:** `python/hotpot/classifier/rf_store.py`,
`python/tests/test_rf_store.py`

Copy `ei_store.py` almost verbatim. It is 86 lines and every decision in
it is already argued. Differences:

- Fields: `workspace`, `project` (Roboflow uses string slugs, not an
  integer id — this is a real shape difference from EI), `api_key`,
  `version` (the trained dataset version currently deployed),
  `model_file` (Path B: the exact filename, per §3.4's never-glob rule).
- Same `atomicio` write-then-fsync-then-rename (doc §20.4).
- Same best-effort `os.chmod(path, 0o600)` with the same "no-op on
  Windows" tolerance — **the API key can spend account training credits**,
  which is the same argument `ei_store.py`'s docstring makes about EI
  build jobs.
- Same "missing file is a first boot, not an error" rule.
- `state/` is already gitignored. Confirm `rf_project.json` is covered by
  the existing rule rather than adding a new one.

---

### Step 3 — `classifier/rf_client.py`

**Goal:** the REST calls. **Do not start this until §5's probes are done
and their real responses are pasted into this file.**

**Files:** `python/hotpot/classifier/rf_client.py`,
`python/tests/test_rf_client.py`

**Design — carry these over from `ei_client.py` unchanged, because each
one was paid for:**

- **`_urlopen` module-level test seam.** No test makes a real network
  call. Tests swap it and restore it in `tearDown`.
- **`_bounded_call()`.** `ei_client.py` documents a live 2026-08-24 hang
  where `link()` sat on "Linking…" indefinitely with nothing in the log,
  because `urlopen`'s timeout does not bound DNS resolution —
  `getaddrinfo()` is a plain blocking call with no timeout parameter in
  the stdlib. Every public network call in that module is wrapped for
  that reason. **Roboflow's client has the identical exposure.** Copy the
  mechanism.
- **A `RFClientError` that wraps the platform's own error message**,
  surfaced to the staff view largely verbatim — Roboflow's messages, like
  EI's, are already operator-facing text.
- **HTTP status is the authoritative success signal**, plus a check on
  whatever success/error field the JSON actually carries (V1–V6 will show
  its shape).
- **Batched, concurrent uploads.** `upload_captures()`'s reasoning holds:
  each round trip is dominated by server latency, not payload, so
  hundreds of images one request at a time is painfully slow. Reuse
  `ei_client.iter_label_images()` for the file walk (§3.2) rather than
  writing a second walker.
- **A partial upload is reported, not fatal.** `{"uploaded": {label:
  count}, "failures": [...]}` — local disk stays the source of truth and
  the next Upload fixes a partial remote state.
- **An `on_progress` callback**, so core can broadcast a live count to the
  tablet the way `ei_upload_progress` already does.

**New, with no EI equivalent:** `train()` and `wait_for_training()`
(§3.3). Model `wait_for_training()` on `ei_client.wait_for_job()` — same
poll interval, same generous timeout, same `on_poll` callback so the
tablet gets a "still training" tick, same explicit distinction between
*finished successfully* and *finished*. Training takes far longer than an
EI build; pick the timeout accordingly and make it a parameter, not a
constant.

---

### Step 4 — `classifier/rf_deploy.py`

**Goal:** get the trained model from "it exists in Roboflow" to "the
backend can load it," and say so.

**Files:** `python/hotpot/classifier/rf_deploy.py`,
`python/tests/test_rf_deploy.py`

Much smaller than `ei_deploy.py` (§3.4). Path A: trigger the weight
download and cache-warm so the first real `classify()` is not the thing
that discovers there is no network — call `get_model()` once here, at
deploy time, while the operator is standing there watching a progress
line. Path B: fetch the `.onnx`, write it through `atomicio`, record the
exact filename into `rf_store`.

**The failure this module exists to prevent** is the one
`ei_deploy.py`'s docstring opens with: a model sitting downloaded on
disk for hours while the live app kept classifying with the old one,
because deploying it was a separate manual step nothing prompted for.
Pressing Download must be the **whole** redeploy, not half of it. Whatever
Path is chosen, the operator must not be left with a second step to
remember.

`ei_deploy.py`'s **zip-slip guard** is not needed on Path B (no archive),
but if a Roboflow export ever arrives as a zip, port that guard with it.

---

### Step 5 — `core/main.py` wire handlers

**Goal:** the staff view can drive it.

**Files:** `core/main.py`, `python/tests/test_core_main.py`

Mirror `_handle_ei_link` / `_handle_ei_upload` / `_handle_ei_download` /
`_handle_ei_unlink` and `_ei_msg`. New wire messages, all `rf_`-prefixed
so nothing collides:

| In | Out |
|---|---|
| `rf_link` | `rf_link_result` |
| `rf_upload` | `rf_upload_progress`, `rf_upload_result` |
| `rf_train` | `rf_train_progress`, `rf_train_result` |
| `rf_deploy` | `rf_deploy_progress`, `rf_deploy_result` |
| `rf_unlink` | `rf_unlink_result` |
| *(join)* | `rf_status` |

**Carry over, each for a stated reason:**

- **Dependency injection.** `Core.__init__` takes `ei_client`/`ei_deploy`
  as constructor parameters defaulting to the real modules, so tests
  never touch the network. Add `rf_client`/`rf_deploy` the same way, and
  a `rf_project_path` alongside `ei_project_path` so a test Core never
  reads or writes the real `state/rf_project.json` — same split
  `cal_path`, `bin_map_path` and `scale_filter_path` already have.
- **One job at a time.** `self._ei_active` refuses a second job while one
  runs. Add `self._rf_active` — and consider whether an EI job and a
  Roboflow job should be allowed to run **simultaneously**. They touch
  different files and different accounts, so probably yes; but a Roboflow
  deploy and an EI deploy both racing to change which model the
  classifier uses is a genuine conflict. **Decide and write the decision
  in the code, do not leave it accidental.**
- **Every handler needs its own catch-all `except Exception`.** Every
  `_handle_ei_*` has one, with a comment explaining why: an unhandled
  exception on the web thread leaves the tablet on a spinner forever with
  the reason only in the log.
- **Replies are broadcasts, not direct answers.** `web/server.py`'s
  `on_message` hands the callback the decoded frame with no connection
  handle, so every reply goes to every tablet. This is a known,
  documented limitation, not something to work around here.
- **Log the workspace/project/version on every path.** `_handle_ei_*`
  gained this after a rig report ("pressed Download and the training was
  gone") could not be reconstructed at all, because the log could not say
  which of four same-named projects each click had gone to. Roboflow's
  slugs are more legible than EI's numeric ids, but log them anyway.

---

### Step 6 — the staff view panel

**Goal:** a Roboflow card beside the Edge Impulse card on the Capture tab.

**Files:** `core/web/static/index.html`

Copy `#eiCard`'s structure to `#rfCard`, placed directly below it inside
the same `.capture-shell` so it inherits the 1100px cap and stacked-card
spacing. Fields: workspace, project, API key (Roboflow has one account
key, so there is **no username/password/TOTP flow at all** — the whole
`#eiNewFields` / `#eiTotpRow` branch has no analogue and must not be
copied over). Buttons: Link, Upload captures, **Train**, Deploy, Unlink.

**Verification, the way every staff-view change in this repo is verified**
(there is no browser toolchain here and none should be added):

1. `node --check` on the extracted `<script>` block.
2. Every new `getElementById` id cross-checked against the DOM **by
   script**, not by eye.
3. A full-file tag-balance parse.
4. Drive the real extracted IIFE against a throwaway DOM shim.

Two recorded traps to avoid, both of which have already cost a session
here:

- **Use the `.hide` class, never the `hidden` attribute.** M4l found a
  `.hide` class the JS toggled that had **no matching CSS rule at all**,
  so the gating was a silent no-op; M4o found the mirror bug, `hidden`
  used on an element that already had a `display:flex` rule beating it.
- **`node --check` is not ceremonial.** M4.7 records it catching a
  literal NUL byte the `Write` tool put inside a string — a corruption no
  Python test could see.

**Static checks do not substitute for opening it.** M4l, M4m and the
camera-controls session each shipped a UI that passed every static check
and broke on first real click (a CORS preflight nobody had a handler
for; a magnifier drawing black; buttons whose gating never applied).
Budget a real browser pass.

---

### Step 7 — config and `build_backend()`

**Files:** `config/system.default.json`, `config/system.json`,
`classifier/main.py`, `python/tests/test_classifier_main.py`

Add to `classifier`:

```json
"backend": "stub",
"roboflow_model": "models/hotpot-ingredients-rf.onnx"
```

and extend `build_backend()` with `"roboflow"` (Path A) and
`"roboflow_onnx"` (Path B). **Keep the existing fallback behaviour
exactly:** an unknown value logs a loud warning and returns
`StubBackend()` rather than crashing the process a typo would otherwise
take down. `"stub"` stays the committed default.

Add `onnxruntime` (Path B) or `inference` (Path A) to
`python/requirements.txt` **with a comment in the house style** — every
entry in that file explains why it is there, what imports it lazily, and
what version it was verified against. Note §4.3's environment risk
directly in that comment; the file already carries a `mediapipe` entry
added precisely because "it was installed by hand on the dev machine,
which is exactly how a rig ends up with a dependency nobody has written
down."

---

### Step 8 — provenance

**Files:** `models/README.md`

Add an entry in the existing table format: workspace, project, project
type, dataset version, model type, class list **read out of the artifact
rather than assumed**, training config, validation metrics, confusion
matrix, date, artifact filename and byte size, and a **Current** marker.

`models/README.md`'s own rule: *"Never overwrite an entry — append a new
one and move the Current marker. If a field is genuinely not known, write
`(unrecorded)` rather than a guess, so a wrong number never gets
laundered into a fact."*

Note explicitly whether the class list matches
`data/catalogue.json`'s `class_name` values. The EI entry documents a
real gap here — `dried_small_shrimps` had 4 images and did not make the
training run, so a bin holding dried shrimp is classified as something
else entirely rather than as "unrecognised." Check the same thing before
trusting any Roboflow model.

---

### Step 9 — the rig

Nothing above is done until this is. Everything in steps 1–8 is code,
tests and static checks; this project's own history is a long list of
changes that passed all three and broke on first contact with the table.

**Acceptance:**

1. Fresh `run.py` — the classifier process boots with
   `classifier.backend` set to the Roboflow value and does **not** crash
   when the model is missing (doc §3.3).
2. Capture tab → Roboflow card → Link. Confirm the "Linked to…" line
   names the right workspace/project.
3. Upload. Confirm the image count in the Roboflow UI matches
   `datasets/captures/`, and **spot-check that the labels stuck** (§5, V3).
4. Train. Watch the progress ticks. Confirm it finishes.
5. Deploy. Confirm the artifact lands and the log says so.
6. Set `classifier.enabled: true`, enter SETTING mode, put a real tray in
   a bin, and **watch the Bins tab resolve it to the right item.**
7. **Pull the network cable and repeat step 6.** This is the check that
   matters for a dark-room installation, and it is the one Path A could
   fail.
8. Compare against Edge Impulse honestly: same trays, same lighting, both
   backends, count the wrong answers. That comparison is the entire point
   of building this, and skipping it would leave two half-trusted models
   instead of one trusted one.

Report which of these is a framebuffer observation and which is physical
observation of the projected surface / real hardware — CLAUDE.md's
standing rule.

---

## 7. TRAPS — things that will pass a test while being wrong

- **BGR/RGB.** §6.1. Confident wrong labels, no error anywhere.
- **A label that silently does not stick on upload.** §5, V3. The dataset
  looks full in the UI and trains to nothing useful.
- **A stale artifact.** §3.4, §6.4. The exact bug the EI side already hit:
  studio accuracy and live accuracy diverge with no error anywhere,
  because the running app is using the previous model.
- **Class list drift.** `data/catalogue.json`'s `class_name` values, the
  `datasets/captures/` folder names, and the trained model's class list
  must be the same set. They have already drifted once here — the
  2026-08-13 substitute-prop rename moved every `class_name` — and
  `core/main.py::_load_binmap` now drops an `item_id` the catalogue no
  longer holds specifically because of it.
- **Testing the backend against its own resize.** Any test that computes
  the expected input by calling the same helper the production path calls
  passes by construction. Doc §5.3's TRAP rule: the reference must be
  computed independently.
- **A "verify" that reprojects through the thing being verified.**
  `geometry_store.py` has a test asserting a `verify()` method *does not
  exist*, because reprojecting derived values back through the same
  transform returns the inputs by construction. If a Roboflow confidence
  check ever grows a self-check, this is the shape to avoid.

---

## 8. WHAT THIS PLAN DELIBERATELY DOES NOT DO

- **Does not touch Edge Impulse.** Developer instruction. Every `ei_*`
  file, `tools/eim_cpp/`, `#eiCard` and `EiCppBackend` stay.
- **Does not remove `StubBackend`.** Doc §19.4: *"The stub is not
  throwaway code — it stays forever as the offline test path and as the
  fallback if a model file is missing on demo day."*
- **Does not change the `ClassifierBackend` Protocol**, the wire shape of
  `classify`, or anything in `classifier/main.py::_classify` beyond
  `build_backend()`'s dispatch.
- **Does not auto-switch backends.** Which model classifies is a
  deliberate config decision a human makes, not something that follows
  from which model was deployed most recently.
- **Does not update `docs/HOTPOT_ARCHITECTURE_v3.md`.** §19.2/19.4/19.5
  will describe only Edge Impulse until someone decides Roboflow is the
  path rather than an experiment. Flagged, not done — this repo's own
  precedent for a change of this size.

---

## 9. OPEN QUESTIONS FOR THE DEVELOPER

1. **Path A or Path B?** (§1) — turns on whether the Roboflow account is
   on a paid Core plan. This is the blocking one.
2. **Is Roboflow replacing Edge Impulse eventually, or is this a
   bake-off?** Changes whether step 9's comparison is the deliverable or
   just a checkpoint.
3. **New project, or reuse the `rahuls-workspace-mqtgo` workspace?**
   (§3.1 — the *project* must be new regardless; the workspace need not
   be.)
4. **Should a Roboflow job and an Edge Impulse job be allowed to run at
   the same time?** (§6.5)

---

## Appendix — endpoint reference

**Every line below is from documentation, not from a live call. Treat all
of it as VERIFY until §5's probes have been run and the real responses
pasted in here.**

```
Base                 https://api.roboflow.com
Structure            /:workspace/:project/:version

Upload one image     POST https://api.roboflow.com/dataset/{project}/upload
                          ?api_key=...&name=...&split=train|valid|test&batch=...
                     (classification: the class is attached as the
                      annotation — SDK: project.upload(image_path,
                      annotation="<class>", split="train"))

Generate a version   SDK: project.generate_version(settings)

Train                POST /{workspace}/{project}/{version}/train?api_key=...
                     body: { "model_type": "..." }
                     returns when the job has STARTED, not finished

Poll a job           GET  /{workspace}/{project}/jobs/{jobId}?api_key=...

Download weights     SDK: rf.workspace().project(P).version(N).models()[0].download()
                     PAID PLANS ONLY — see §1 Path B

Local inference      from inference import get_model
                     model = get_model(model_id="{project}/{version}",
                                       api_key="...")
                     env: MODEL_CACHE_DIR=<persistent path>
```

### Sources

- [Roboflow Inference — offline weights download](https://inference.roboflow.com/using_inference/offline_weights_download/)
- [Roboflow Docs — Download Model Weights](https://docs.roboflow.com/models/model-weights/download-roboflow-model-weights)
- [Roboflow Docs — Train a Model](https://docs.roboflow.com/models/train/train-a-model)
- [Roboflow Docs — Upload a Dataset](https://docs.roboflow.com/datasets/create-and-upload/upload-a-dataset)
- [Roboflow Docs — uploading classification datasets](https://help.roboflow.com/en_US/get-started/uploading-classification-datasets)
- [roboflow-python — Project.upload](https://github.com/roboflow/roboflow-python/blob/main/roboflow/core/project.py)
- [Roboflow blog — Deploy Computer Vision Models Offline](https://blog.roboflow.com/deploy-computer-vision-models-offline/)
- [OpenVINO — CPU device requirements (AVX2 from 2026.0)](https://docs.openvino.ai/2025/openvino-workflow/running-inference/inference-devices-and-modes/cpu-device.html)
- [roboflow/inference on GitHub](https://github.com/roboflow/inference)

---

## SESSION LOG — 2026-08-26

Developer instruction: implement §6's whole build in one pass rather than
the usual one-step-per-session/stop-and-report rhythm this file's own
build order asks for. This log is that rhythm compressed into one entry
instead of nine, so a future session can still tell what happened and in
what order.

**Built, all code-complete, one commit per pair of steps:**

- **Step 1** — `classifier/backend_rf.py`: `RoboflowInferenceBackend`
  (Path A) and `RoboflowOnnxBackend` (Path B), both raising
  `backend_ei.ClassifierBackendError`, both lazy-loaded, both re-reading
  their class list from the artifact rather than hardcoding it. 34 tests
  (`test_backend_rf.py`), including the BGR->RGB and resize-to-input-size
  mutation checks §6 step 1 asked for by name.
- **Step 2** — `classifier/rf_store.py`: `state/rf_project.json`, ported
  from `ei_store.py` per that step's own instruction. 10 tests.
- **Step 3** — `classifier/rf_client.py`: REST (`_urlopen` test seam,
  `_bounded_call`'s DNS-hang backstop) for link verification and
  train/poll, plus a `roboflow`-SDK factory seam for image upload and
  dataset-version generation (the two operations Roboflow only documents
  as SDK calls). 24 tests.
- **Step 4** — `classifier/rf_deploy.py`: Path A cache-warms
  (`RoboflowInferenceBackend.warm()`, added for this) at deploy time;
  Path B fetches weights, best-effort extracts a bundled class list into
  a `<model>.classes.json` sidecar, wipes the previous deploy's files on
  a filename change. 12 tests.
- **Step 5** — `core/main.py`: `_handle_rf_link`/`_handle_rf_upload`/
  `_handle_rf_train`/`_handle_rf_deploy`/`_handle_rf_unlink`, `rf_status`
  on join, same DI shape (`rf_client=`/`rf_deploy=`/`rf_project_path=`)
  every other network-touching subsystem in this file already has. The
  §6 step 5 "decide and write the decision" prompt on concurrent EI/RF
  jobs is answered in the code, next to `self._rf_active`: independent
  guards, may run at once. 25 tests (`TestRoboflowTab`).
- **Step 6** — `core/web/static/index.html`'s `#rfCard`, directly below
  `#eiCard`, no username/password/TOTP fields (one account key). Verified
  the way this file's history says every staff-view change here is:
  `node --check` on the extracted script (clean), every `getElementById`
  cross-checked against the DOM by script (102 calls, 0 missing), a
  full-file tag-balance parse (clean) — and this pass is what caught
  `#rfUnlinked.hide`/`#rfLinked.hide` missing their own CSS rule before
  it shipped, the exact "`.hide` toggled with no matching selector" bug
  this file's own CLAUDE.md history already recorded twice (M4l, M4o).
- **Step 7** — `build_backend()` gained `"roboflow"`/`"roboflow_onnx"`,
  falling back to `"stub"` on anything else exactly as before. 6 new
  tests (`TestBuildBackend` — this codebase had no dedicated test for
  `build_backend()`'s dispatch at all before this, EI values included;
  now all five are covered). `config/system.default.json` gained
  `classifier.roboflow_model`. `python/requirements.txt` gained
  `roboflow`/`inference`/`onnxruntime` entries in the house comment
  style — **commented out, not installed**, both because §5's probes
  were never run to confirm a version floor and because §4.3's own
  warning (`inference`'s dependency tree can silently downgrade `penv`'s
  numpy/opencv/mediapipe pins) means installing either should be a
  deliberate, separate action, never a side effect of a routine
  `pip install -r requirements.txt`.
- **Step 8** — `models/README.md` gained a section saying plainly that
  there is no trained model and therefore no entry, not a fabricated one
  with placeholder metrics. See that file for what step 9 needs to fill
  in once a real model exists.

`python -m unittest discover -s python/tests`: 1298 tests, same ~9-10
pre-existing unrelated failures this repo's test-suite history already
documents (the spice-icon `TestCheckoutFlow`/`test_hover` cases,
`test_calibrator`'s documented stale-link flake).

**Not done, and this is the load-bearing caveat for the whole session:**

- **§5's V1-V8 probes were never run.** No Roboflow account/API key was
  available in this session. Every REST/SDK shape in `rf_client.py` and
  `rf_deploy.py` below a VERIFY comment is reasoned from Roboflow's
  published docs only — the exact position this repo's own `ei_client.py`
  was in before a live run found and fixed one missing query parameter
  (that module's own docstring, still the standing proof of why this
  matters). Do not trust a real Link/Upload/Train/Deploy click against
  this code until those probes have been run for real and the real
  responses pasted into §5's table above.
- **§1's Path A vs Path B decision was not made** — both were built, per
  this document's own §1 recommendation, so the choice stays a config
  value (`classifier.backend`) rather than a rewrite. Still open question
  #1 in §9.
- **No `inference`/`onnxruntime`/`roboflow` package has been installed
  anywhere, including in a throwaway venv** — §4.3's own "verify first"
  step is untouched. `test_backend_rf.py`/`test_rf_client.py` prove the
  logic around each package's API, not the real package.
- **Step 9, the rig, is completely untouched** — no camera, no real
  account, no physical trays, nothing projected. Every one of step 9's
  nine acceptance items is still open.
- The staff view's `#rfCard` has not been opened in a real browser or
  driven through a DOM shim — static checks only (see step 6 above).

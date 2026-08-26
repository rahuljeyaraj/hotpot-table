"""classifier/rf_client.py — Roboflow REST + SDK client: verify a link,
push labeled capture images, generate a dataset version, train, poll, and
(Path B only) fetch weights. The Roboflow sibling of `ei_client.py`, per
`docs/ROBOFLOW_PATHWAY.md` §6 step 3.

**Do NOT trust anything in this file's request/response shapes until doc
§5's probes (V1-V8) have been run against a real account and the real
responses pasted into that doc, per its own explicit instruction.** This
was written from Roboflow's published docs and the `roboflow`/`inference`
package's own documented entry points, exactly as the plan doc's appendix
already flags: "Every line below is from documentation, not from a live
call." `ei_client.py`'s own module docstring is the standing proof of why
that matters — its `download_model()` docstring records a real, costly
failure from one missing query parameter, found only by a live call.
Nothing in *this* module has been run against Roboflow at all. Every
function below carries its own VERIFY note where the shape is genuinely
uncertain, and the test seams exist so a wrong guess is a one-function fix,
not a rewrite.

**Two tracks, not one, because Roboflow's API is two different surfaces
for the operations this module needs:**

- **Plain REST** (`urllib.request`, no dependency) for everything with a
  documented HTTP shape: checking a project resolves, kicking off a train
  job, polling it. Same `_urlopen` module-level test seam `ei_client.py`
  uses, same `_bounded_call()` DNS/connect-hang backstop (that module's own
  docstring explains why: `urlopen`'s timeout does not bound
  `getaddrinfo()`, confirmed live once already on this exact link/upload
  shape for Edge Impulse — Roboflow's client has the identical exposure,
  so the mechanism is copied verbatim, not re-derived).
- **The `roboflow` SDK** for the two operations Roboflow's own docs give
  only as SDK calls with no documented raw-REST equivalent: uploading a
  labeled image (`project.upload(path, annotation=<class>, split=...)`)
  and generating a dataset version (`project.generate_version(settings)`).
  Guessing a raw multipart shape for CLASSIFICATION uploads specifically
  (as opposed to object detection, which commonly uses a documented
  annotation-file form) is exactly the risk doc §5 flags as the one that
  can sink the whole plan (V3: "a classification upload that silently
  lands unlabelled"), so this module uses the shape Roboflow actually
  documents instead. `_roboflow_client` is the test seam for it — a
  module-level factory, the same role `_urlopen` plays for the REST half,
  swapped and restored the same way in tests. No test in this module ever
  imports the real `roboflow` package.

**Both raise `RFClientError`, wrapping the platform's own error message**
— surfaced to the staff view largely verbatim, the same convention
`ei_client.EIClientError` already uses ("Roboflow's messages, like EI's,
are already operator-facing text" — doc §6 step 3).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from hotpot.classifier.ei_client import iter_label_images  # noqa: F401 — re-exported
# ^ Doc §3.2/§6 step 3: reuse ei_client's own file-walk directly rather
# than writing a second one that could drift from it. `IMAGE_SUFFIXES`
# filters out the *.json sidecars classifier/main.py's `_capture` writes
# beside every image (provenance, not training data) — the one rule that
# carries over unchanged between the two platforms.

API_BASE = "https://api.roboflow.com"
_TIMEOUT_S = 30
_CALL_TIMEOUT_S = _TIMEOUT_S + 10   # see _bounded_call's own docstring

_JOB_POLL_INTERVAL_S = 10.0
# Training a real model takes far longer than an Edge Impulse build (doc
# §6 step 3's own note) — an unmeasured, generous starting guess, made a
# parameter (not a constant) on wait_for_training() below for the same
# reason ei_client.wait_for_job()'s timeout is one.
_TRAIN_TIMEOUT_S = 3600.0 * 4

_UPLOAD_CONCURRENCY = 8


class RFClientError(Exception):
    """Wraps Roboflow's own error message — see this module's docstring."""


# ---------------------------------------------------------------------------
# REST half — same shape as ei_client.py's _urlopen/_bounded_call/_request
# ---------------------------------------------------------------------------

# Test seam: swapped and restored in tests, never touches the network in
# a test run. Same role as ei_client._urlopen.
_urlopen = urllib.request.urlopen


def _bounded_call(fn: Callable[[], object], timeout_s: float = _CALL_TIMEOUT_S) -> object:
    """Identical mechanism to `ei_client._bounded_call` — see that
    function's docstring for why a plain `urlopen(timeout=...)` is not
    enough (DNS resolution is not covered by it). Duplicated rather than
    imported: this module must keep working with no dependency on
    `ei_client.py` beyond the one explicitly-reused file-walk above, so a
    future change that touches only the Edge Impulse side can never
    silently also change Roboflow's network behaviour.
    """
    import threading

    box: list = []

    def run() -> None:
        try:
            box.append((True, fn()))
        except BaseException as e:  # noqa: BLE001 - re-raised verbatim below
            box.append((False, e))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        raise RFClientError(
            f"Roboflow did not respond within {timeout_s:.0f}s — check "
            "the network connection and try again")
    ok, value = box[0]
    if ok:
        return value
    raise value


def _do_request(method: str, url: str, headers: Dict[str, str],
                 body: Optional[bytes]) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with _urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise RFClientError(f"{url}: could not reach Roboflow ({e.reason})")


def _request(method: str, url: str, headers: Optional[Dict[str, str]] = None,
             body: Optional[bytes] = None) -> dict:
    """HTTP status is the authoritative success signal (doc §6 step 3),
    plus a check on whatever error field the JSON actually carries —
    VERIFY: Roboflow's own field name for a REST error is unconfirmed
    (`error` is tried first, matching EI's convention, since neither has
    been probed live yet).
    """
    status, raw = _bounded_call(
        lambda: _do_request(method, url, headers or {}, body))
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}
    if status < 200 or status >= 300:
        error = (parsed.get("error") if isinstance(parsed, dict) else None) \
            or raw[:200].decode("utf-8", "replace") or f"HTTP {status}"
        raise RFClientError(f"{url}: {error}")
    return parsed if isinstance(parsed, dict) else {"result": parsed}


# ---------------------------------------------------------------------------
# V1/V2 — link verification (doc §5)
# ---------------------------------------------------------------------------

def check_api_key(api_key: str) -> dict:
    """V1: `GET https://api.roboflow.com/?api_key=...` — confirms the key
    works and a workspace resolves. Returns the parsed response verbatim;
    callers that need a specific field should read this doc's own §5
    table once V1 has actually been run and its real shape recorded here.
    """
    return _request("GET", f"{API_BASE}/?api_key={api_key}")


def get_project(workspace: str, project: str, api_key: str) -> dict:
    """V2: `GET https://api.roboflow.com/{workspace}/{project}?api_key=...`
    — confirms the project exists and reports its type
    (`single-label-classification`, doc §3.1 — the existing `tray-detector`
    project is object detection and is the WRONG type for this feature;
    check the returned type before saving a link to it).
    """
    return _request(
        "GET", f"{API_BASE}/{workspace}/{project}?api_key={api_key}")


# ---------------------------------------------------------------------------
# SDK half — upload + version generation (doc §5 V3/V4, §3.2)
# ---------------------------------------------------------------------------

def _default_roboflow_client(api_key: str):
    from roboflow import Roboflow  # noqa: WPS433 — local, see module docstring
    return Roboflow(api_key=api_key)


# Test seam: a module-level factory, same role _urlopen plays for the REST
# half above. Tests swap this for one returning a fake
# workspace()/project() chain and restore it in tearDown; no test in this
# module imports the real `roboflow` package.
_roboflow_client = _default_roboflow_client


def _project_handle(workspace: str, project: str, api_key: str):
    """**Bug found live, 2026-08-26**: `_roboflow_client(api_key)` used to
    be called OUTSIDE this function's try/except, so a missing `roboflow`
    package (`ModuleNotFoundError`, the ordinary state on a rig that
    hasn't installed it yet — doc §4.3's own caution against doing that
    casually) escaped as a raw traceback into the log instead of the clean
    `RFClientError` message every other failure in this module gives the
    operator. Confirmed on the rig: `core/main.py`'s outer catch-all still
    caught it (core did not crash), but the tablet only ever saw "hit an
    internal error — see the log", not the actual, simple cause. Both the
    import and the workspace()/project() call are now inside the same try.
    """
    try:
        client = _roboflow_client(api_key)
        return client.workspace(workspace).project(project)
    except RFClientError:
        raise
    except ModuleNotFoundError as e:
        raise RFClientError(
            "the 'roboflow' package is not installed in this Python "
            "environment — see python/requirements.txt's own comment "
            "before installing it (do not pip install it into penv "
            "without reading that first)") from e
    except Exception as e:  # noqa: BLE001 - any other SDK failure
        raise RFClientError(
            f"could not open Roboflow project {workspace}/{project}: {e}") from e


def upload_image(workspace: str, project: str, api_key: str,
                  image_path: str, class_name: str, *,
                  split: str = "train") -> None:
    """V3: one image, with a class label attached. Per the plan doc's own
    appendix (and the roboflow-python SDK source it quotes): "the
    annotation parameter can be a class name, such as `dog`" — so the
    label is a plain SDK argument, no header, no filename-prefix
    gymnastics (unlike `ei_client.upload_samples`'s `x-label` header,
    which Roboflow has no equivalent of).

    **V3 is the check that can sink this whole plan** (doc §5): a
    classification upload that silently lands unlabelled produces a
    dataset that looks full and trains to nothing. Nothing in THIS
    function can catch that — only a human looking at the uploaded image
    in the Roboflow UI and confirming the class actually stuck can, per
    doc §5's own instruction. Do that before trusting a real Upload click.
    """
    proj = _project_handle(workspace, project, api_key)
    try:
        proj.upload(image_path, annotation=class_name, split=split)
    except Exception as e:  # noqa: BLE001 - any SDK failure
        raise RFClientError(f"{image_path}: upload failed: {e}") from e


def upload_captures(workspace: str, project: str, api_key: str, captures_dir,
                     *, split: str = "train",
                     concurrency: int = _UPLOAD_CONCURRENCY,
                     on_progress: Optional[Callable[..., None]] = None
                     ) -> Dict[str, object]:
    """Uploads every image under `captures_dir/<label>/*.jpg` — same
    walk `ei_client.upload_captures` uses (`iter_label_images`, reused
    directly, doc §3.2), same partial-upload-is-reported-not-fatal
    tolerance (`{"uploaded": {label: count}, "failures": [...]}` — local
    disk stays the source of truth, a partial remote state just gets
    fixed by the next Upload).

    One SDK call per image (`upload_image` above), run concurrently — the
    SDK's `project.upload()` has no batched/multipart form the way EI's
    ingestion API does, so "concurrent single uploads" is the whole of
    what this can do to avoid a multi-hundred-image directory going up one
    request at a time.
    """
    from concurrent.futures import ThreadPoolExecutor
    import os
    import threading

    by_label = iter_label_images(captures_dir)
    if not by_label:
        raise RFClientError(f"no captured images under {captures_dir!r} to upload")

    total = sum(len(paths) for paths in by_label.values())
    uploaded_count = 0
    failures: List[str] = []
    uploaded: Dict[str, int] = {}
    lock = threading.Lock()

    def progress(**extra) -> None:
        if on_progress:
            on_progress(uploaded=uploaded_count, total=total,
                        failures=list(failures), **extra)

    progress()
    for label, paths in by_label.items():

        def send(path: str, label=label) -> None:
            nonlocal uploaded_count
            try:
                upload_image(workspace, project, api_key, path, label, split=split)
            except RFClientError as e:
                with lock:
                    failures.append(f"{os.path.basename(path)}: {e}")
                    progress()
                return
            with lock:
                uploaded_count += 1
                uploaded[label] = uploaded.get(label, 0) + 1
                progress()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(send, paths))

    return {"uploaded": uploaded, "failures": failures}


def generate_version(workspace: str, project: str, api_key: str,
                      settings: Optional[dict] = None) -> str:
    """V4: `SDK project.generate_version(settings)` — kicks off dataset
    preprocessing/augmentation and returns the new version number.
    Returned as a **string**, matching `rf_store.RFProject["version"]`'s
    type — Roboflow versions are small integers in practice but nothing
    here has confirmed the SDK's own return type, so this coerces rather
    than assumes.
    """
    proj = _project_handle(workspace, project, api_key)
    try:
        version = proj.generate_version(settings or {})
    except Exception as e:  # noqa: BLE001
        raise RFClientError(f"generate_version failed: {e}") from e
    if version is None:
        raise RFClientError(
            "generate_version() returned nothing — could not read a "
            "version number back")
    return str(version)


# ---------------------------------------------------------------------------
# Train + poll (doc §5 V5/V6, §3.3) — genuinely new vs. Edge Impulse
# ---------------------------------------------------------------------------

def train(workspace: str, project: str, version: str, api_key: str,
          model_type: str) -> str:
    """V5: `POST /{workspace}/{project}/{version}/train?api_key=...`,
    body `{"model_type": "..."}`. Returns when the job has STARTED, not
    finished — same as `ei_client.build_model()`. The job id field name in
    the response is unconfirmed (tried in order: `id`, `job_id`, `jobId`);
    VERIFY against a real response and simplify this once V5 is run.
    """
    body = json.dumps({"model_type": model_type}).encode("utf-8")
    resp = _request(
        "POST",
        f"{API_BASE}/{workspace}/{project}/{version}/train?api_key={api_key}",
        {"Content-Type": "application/json"}, body)
    for key in ("id", "job_id", "jobId"):
        if key in resp:
            return str(resp[key])
    raise RFClientError(
        f"train() started (HTTP ok) but the response had no job id field "
        f"this module recognises: {resp!r} — see rf_client.train()'s own "
        "VERIFY note")


def job_status(workspace: str, project: str, api_key: str, job_id: str) -> dict:
    """V6: `GET /{workspace}/{project}/jobs/{jobId}?api_key=...`."""
    return _request(
        "GET", f"{API_BASE}/{workspace}/{project}/jobs/{job_id}?api_key={api_key}")


# Status strings this module treats as "still running" / "succeeded" /
# "failed" — none of them confirmed against a live response (doc §5, V6:
# "job polling reports finished/failed distinguishably" is exactly the
# thing to confirm and correct this against). Kept as module-level tuples,
# not inline, so a live-verified correction is a one-line diff.
_JOB_DONE_OK = ("completed", "success", "finished", "done")
_JOB_DONE_FAILED = ("failed", "error", "cancelled")


def _job_finished(job: dict) -> Optional[bool]:
    """None while still running; True/False once the shape above (or
    Edge-Impulse-style `finished`/`finishedSuccessful` booleans, tried as
    a fallback in case Roboflow's own job status shape turns out closer
    to that than to a bare status string) says which."""
    status = str(job.get("status", "")).lower()
    if status in _JOB_DONE_OK:
        return True
    if status in _JOB_DONE_FAILED:
        return False
    if job.get("finished"):
        return bool(job.get("finishedSuccessful", True))
    return None


def wait_for_training(workspace: str, project: str, api_key: str, job_id: str,
                       on_poll: Optional[Callable[[], None]] = None,
                       poll_interval_s: float = _JOB_POLL_INTERVAL_S,
                       timeout_s: float = _TRAIN_TIMEOUT_S) -> None:
    """Blocks the calling thread, polling `job_status()` until Roboflow
    reports the training job finished — modelled on
    `ei_client.wait_for_job()` (same poll-interval/on_poll/explicit
    finished-vs-succeeded shape), except the timeout defaults far larger:
    training a real model takes much longer than an Edge Impulse on-device
    build. `core/main.py`'s `_handle_rf_train` runs this on the tablet's
    own WebSocket thread, same "blocking here is safe, the thread's whole
    job right now is showing a working step" reasoning `wait_for_job`'s
    own caller already relies on.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        job = job_status(workspace, project, api_key, job_id)
        finished = _job_finished(job)
        if finished is True:
            return
        if finished is False:
            raise RFClientError(
                f"training job {job_id} on {workspace}/{project} did not "
                "finish successfully")
        if time.monotonic() > deadline:
            raise RFClientError(
                f"training job {job_id} on {workspace}/{project} timed "
                f"out after {timeout_s:.0f}s")
        if on_poll:
            on_poll()
        time.sleep(poll_interval_s)


# ---------------------------------------------------------------------------
# Path B only — weights download (doc §5 V8, §1)
# ---------------------------------------------------------------------------

def download_weights(workspace: str, project: str, version: str,
                      api_key: str, dest_dir: str) -> str:
    """**Path B only, and blocked on cost per doc §1**: "Manual weights
    download is only available for paid users on Core plans and certain
    Enterprise customers." Per the appendix: `SDK: rf.workspace().
    project(P).version(N).models()[0].download()`.

    **V8, unverified — the least-confirmed function in this whole
    module.** The SDK's own `.download()` is documented to save a file
    (commonly a zip) into the *current working directory* by default in
    some SDK versions, not return raw bytes the way `ei_client.
    download_model()` does — so this function changes into `dest_dir`
    only for the duration of the call (a plain `os.chdir`, restored in a
    `finally`) and returns whatever new file appeared there, rather than
    guessing a `location=` keyword argument the installed SDK version may
    or may not accept. Confirm this against a real paid-plan account (V8)
    before trusting it — if the installed SDK exposes an explicit
    destination parameter instead, prefer that over this chdir workaround.
    """
    import os

    proj = _project_handle(workspace, project, api_key)
    before = set(os.listdir(dest_dir)) if os.path.isdir(dest_dir) else set()
    cwd = os.getcwd()
    os.makedirs(dest_dir, exist_ok=True)
    os.chdir(dest_dir)
    try:
        version_handle = proj.version(version)
        models = version_handle.models()
        if not models:
            raise RFClientError(
                f"{workspace}/{project} version {version} has no trained "
                "model to download weights from")
        models[0].download()
    except RFClientError:
        raise
    except Exception as e:  # noqa: BLE001
        raise RFClientError(f"download_weights failed: {e}") from e
    finally:
        os.chdir(cwd)

    after = set(os.listdir(dest_dir))
    new_files = sorted(after - before)
    if not new_files:
        raise RFClientError(
            f"download() returned but no new file appeared in {dest_dir} "
            "— see download_weights()'s own VERIFY note")
    return os.path.join(dest_dir, new_files[0])

"""classifier/ei_client.py — Edge Impulse REST client: log in, create (or
adopt) the `hotpot-ingredients` project, push labeled capture images via
the ingestion API, and build + download the trained deployment.

Ported from a sibling project's `pipeline/ei_client.py`, which built the
same three rounds (login/create, upload, build/download) against EI's
Studio + ingestion APIs for CSV feature-vector samples. The shape carries
over — plain stdlib `urllib.request`, HTTP status as the authoritative
success signal, `x-api-key`/`x-jwt-token` auth, `EIClientError` wrapping
EI's own `error` message — but the payload does not: this project uploads
**images** (the Capture tab's `datasets/captures/<label>/*.jpg`, doc
section 12.7), not precomputed feature vectors, and its deployment target
is a **C++ library** (EON compiler, int8 — `models/README.md`'s
"hotpot-ingredients" entry), not a bare `.tflite`.

**What this module does NOT do, on purpose:** configure the impulse's
input/DSP/transfer-learning blocks (doc section 19.2: 160x160 image input,
image DSP block, MobileNetV2 alpha=0.35 transfer learning). The sibling
project's own `create_impulse()`/`_impulse_body()` for a *feature-vector*
project went through two wrong turns against a live account before
landing on a working shape (see that file's docstring) — even for the
better-documented, simpler CSV case. Image transfer-learning's impulse
config is a different, less-travelled corner of EI's API and guessing its
JSON shape here would risk silently producing a broken impulse a human
then has to notice and fix by hand in Studio anyway. So `link()` below
creates a bare project (Studio's own default, no impulse) if one is not
already linked; wiring up the impulse blocks the first time is a one-off
manual Studio step, same as it already was for the existing
`hotpot-ingredients` project (id 1087506).

`build_model()`'s `engine`/`modelType` body fields were this module's
best-effort reading of EI's public API docs and carried a "not confirmed
against a live account" caveat here until **2026-08-24, when a live run
against project 1095598 confirmed all three**: EI's own job-status
metadata came back `{"deploymentType": "zip", "engine": "tflite-eon",
"modelType": "int8", "impulseId": 1}`, i.e. it accepted and used exactly
what was sent.

What that same run also showed is that the triple is a *key*, not just a
request: EI files the built deployment under (deploymentType, engine,
modelType), and `download_model()` was fetching with only two thirds of
it. See its docstring — the failure mode is worth knowing about, because
it reads as "the build never ran" when the build in fact succeeded.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable, Dict, List, Optional, Tuple

STUDIO_BASE = "https://studio.edgeimpulse.com/v1"
INGESTION_BASE = "https://ingestion.edgeimpulse.com/api"

_TIMEOUT_S = 30

# Conservative default carried over from the sibling project's
# ei_client.py, itself matching its tools/ei_upload.sh's validated batch
# size for the ingestion API's one-"data"-part-per-sample multipart
# upload. hotpot-table's own tools/upload_edgeimpulse.ps1 chunks at 100,
# but that chunk size is just an argument-list-length safety margin for
# the `edge-impulse-uploader` CLI, not a validated ingestion-API limit —
# not reused here for that reason.
UPLOAD_BATCH_SIZE = 25
_UPLOAD_CONCURRENCY = 8

# doc section 19.5 / models/README.md's locked deployment settings for the
# `hotpot-ingredients` project: "C++ library, EON Compiler, quantized
# (int8)". `type=zip` is the deployment-target query param (the C++
# library target); `engine`/`modelType` in the job body are what select
# EON + int8 specifically, per EI's build-ondevice-model docs — the one
# pair of names in this module least confirmed against a live account
# (see module docstring).
DEPLOY_TYPE = "zip"
DEPLOY_ENGINE = "tflite-eon"
DEPLOY_MODEL_TYPE = "int8"

_JOB_POLL_INTERVAL_S = 5.0
_JOB_TIMEOUT_S = 1800.0

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


class EIClientError(Exception):
    """Wraps Edge Impulse's own `error` message -- surfaced to the staff
    view largely verbatim, same convention as the sibling project's
    ei_client.py: EI's messages are already operator-facing text."""


class EITotpRequiredError(EIClientError):
    """Raised when EI's login response starts with
    ERR_TOTP_TOKEN_IS_REQUIRED, so core/main.py's `_handle_ei_link` can
    tell "wrong password" apart from "need a TOTP code" and re-prompt
    instead of just failing."""


# Test seam: a module-level fake standing in for urllib.request.urlopen,
# the same role `run` plays in backend_ei.EiCppBackend (that file's own
# docstring) -- module-level rather than a parameter threaded through
# every public function here, since this file is plain functions, not a
# class with a constructor to inject through. Tests swap this and restore
# it in tearDown; no test needs a real network call.
_urlopen = urllib.request.urlopen

# `urlopen(req, timeout=_TIMEOUT_S)`'s timeout does NOT reliably bound DNS
# resolution -- CPython's socket.create_connection() only applies the
# timeout to the socket it creates AFTER getaddrinfo() already returned,
# and getaddrinfo() itself is a plain blocking call with no timeout
# parameter in the stdlib. On a machine with a broken/absent route to
# Edge Impulse this can hang past _TIMEOUT_S with no exception ever
# raised -- confirmed live 2026-08-24: a link() call sat on "Linking..."
# indefinitely with nothing in core's log, because nothing had failed yet,
# it just never returned. _bounded_call() below is the backstop: every
# public network call in this module is wrapped in it, so none of them
# can block a caller past _CALL_TIMEOUT_S regardless of which layer is
# actually stuck.
_CALL_TIMEOUT_S = _TIMEOUT_S + 10


def _bounded_call(fn: Callable[[], object], timeout_s: float = _CALL_TIMEOUT_S) -> object:
    """Runs fn() on a throwaway daemon thread and waits up to timeout_s for
    it to finish. Raises EIClientError on timeout; re-raises whatever fn()
    raised otherwise. The thread is daemon and simply abandoned on
    timeout -- it does not block process exit even if fn() never returns
    (unlike a bare concurrent.futures.ThreadPoolExecutor, whose non-daemon
    worker threads get joined at interpreter shutdown and would hang the
    process the same way)."""
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
        raise EIClientError(
            f"Edge Impulse did not respond within {timeout_s:.0f}s -- "
            "check the network connection and try again")
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
        # DNS failure, connection refused, TLS error, timeout -- never got
        # as far as an HTTP response. A device/dev-machine offline is the
        # single most likely way any of this fails on the actual rig, so
        # it deserves a real message rather than a raw exception bubbling
        # all the way up to the tablet.
        raise EIClientError(f"{url}: could not reach Edge Impulse ({e.reason})")


def _request(method: str, url: str, headers: Dict[str, str],
             body: Optional[bytes] = None) -> dict:
    status, raw = _bounded_call(lambda: _do_request(method, url, headers, body))

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}

    if status < 200 or status >= 300 or parsed.get("success") is False:
        error = parsed.get("error") or raw[:200].decode("utf-8", "replace") or f"HTTP {status}"
        if error.startswith("ERR_TOTP_TOKEN_IS_REQUIRED"):
            raise EITotpRequiredError(error)
        raise EIClientError(f"{url}: {error}")
    return parsed


def _json_post(url: str, headers: Dict[str, str], payload: dict) -> dict:
    headers = {**headers, "Content-Type": "application/json"}
    return _request("POST", url, headers, json.dumps(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# Link -- login + create (or adopt) a project
# ---------------------------------------------------------------------------

def login(username: str, password: str, totp: Optional[str] = None) -> str:
    """Returns a JWT usable (as `x-jwt-token`) for create_project() below.
    Never persisted -- core/main.py's `_handle_ei_link` holds the
    username/password/JWT in memory only for the duration of one link()
    call, the same rule the sibling project's module docstring states for
    its own login()."""
    body = {"username": username, "password": password}
    if totp:
        body["totpToken"] = totp
    resp = _json_post(f"{STUDIO_BASE}/api-login", {}, body)
    return resp["token"]


def create_project(jwt_token: str, project_name: str,
                    project_visibility: str = "public") -> Tuple[int, str]:
    """Returns (project_id, project_api_key).

    `project_visibility` defaults to "public" (visible in Studio, not
    end-user-facing) rather than EI's own private-by-default: the sibling
    project hit "Private projects quota exceeded" live on this same EI
    account (rahuljeyaraj) creating its own project, so the same limit is
    the likelier outcome here than not. Pass "private" to try that first
    if the quota has since been raised."""
    resp = _json_post(f"{STUDIO_BASE}/api/projects/create",
                       {"x-jwt-token": jwt_token},
                       {"projectName": project_name, "createApiKey": True,
                        "projectVisibility": project_visibility})
    return resp["id"], resp["apiKey"]


def get_project(api_key: str, project_id: int) -> dict:
    """Fetches an existing project's own record by (project_id, api_key) --
    `GET /api/{projectId}`, EI's documented "get project info" endpoint,
    the pair also doubles as validating the api_key against that exact
    project before anything is saved locally (a copy-pasted key for the
    WRONG project id fails here with EI's own error message, rather than
    getting silently linked and only failing later on the first Upload).

    Used by core/main.py's `_handle_ei_link` "link to an EXISTING
    project" path (pasting a project id + API key straight from Studio's
    Dashboard -> Keys, as an alternative to login()+create_project()'s
    "always creates a brand new project" behaviour) -- the whole reason
    this exists: create_project() has no way to *adopt* an
    already-trained project like `hotpot-ingredients` (id 1087506), only
    to make a new, empty one that happens to share its name.

    Returns the `project` sub-object (id, name, and other metadata EI
    includes) -- not the raw response, since every other function in this
    module already unwraps to the fields callers actually want."""
    resp = _request("GET", f"{STUDIO_BASE}/api/{project_id}", {"x-api-key": api_key})
    return resp.get("project", resp)


# ---------------------------------------------------------------------------
# Upload -- push datasets/captures/<label>/*.jpg via the ingestion API
# ---------------------------------------------------------------------------

def _multipart_body(samples: List[Tuple[str, bytes]]) -> Tuple[bytes, str]:
    """One "data" part per (filename, image_bytes) sample -- same
    contract the ingestion API uses for CSV samples in the sibling
    project's ei_client.py (one sample per attached "data" part)."""
    boundary = uuid.uuid4().hex
    parts = []
    for filename, data in samples:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="data"; filename="{filename}"\r\n'
            f"Content-Type: {mimetypes.guess_type(filename)[0] or 'application/octet-stream'}\r\n\r\n"
            .encode("utf-8") + data + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def upload_samples(api_key: str, category: str, label: str,
                    samples: List[Tuple[str, bytes]]) -> int:
    """category: "training", "testing", or "split" (EI's own auto 80/20 --
    what tools/upload_edgeimpulse.ps1's `--category split` already asks
    the CLI uploader for, reused here rather than replicating a client-side
    train/test split). samples: [(filename, image_bytes), ...], already
    batched by the caller to at most UPLOAD_BATCH_SIZE. Returns the number
    of samples uploaded in this call."""
    if not samples:
        return 0
    body, boundary = _multipart_body(samples)
    headers = {
        "x-api-key": api_key,
        "x-label": label,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    _request("POST", f"{INGESTION_BASE}/{category}/files", headers, body)
    return len(samples)


def _batched(items: list, batch_size: int = UPLOAD_BATCH_SIZE):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def iter_label_images(captures_dir) -> Dict[str, List[str]]:
    """label -> sorted list of image paths under
    captures_dir/<label>/*.{jpg,jpeg,png} -- the sidecar .json files
    classifier/main.py's `_capture` writes beside every image (bin index,
    rect, lighting -- doc section 12.7) are provenance, not training data,
    same exclusion tools/export_edgeimpulse.py already makes for the CLI
    upload path."""
    out: Dict[str, List[str]] = {}
    if not os.path.isdir(captures_dir):
        return out
    for label in sorted(os.listdir(captures_dir)):
        label_dir = os.path.join(captures_dir, label)
        if not os.path.isdir(label_dir):
            continue
        images = sorted(
            os.path.join(label_dir, name) for name in os.listdir(label_dir)
            if os.path.splitext(name)[1].lower() in IMAGE_SUFFIXES)
        if images:
            out[label] = images
    return out


def upload_captures(api_key: str, captures_dir, *,
                     category: str = "split",
                     concurrency: int = _UPLOAD_CONCURRENCY,
                     on_progress: Optional[Callable[..., None]] = None
                     ) -> Dict[str, object]:
    """Uploads every image under captures_dir/<label>/*.jpg, batched and
    sent concurrently (same reasoning as the sibling project's
    EIController.upload(): each HTTP round-trip is dominated by EI's own
    server-side latency, not the payload, so a handful of hundred-sample
    directories at one request per sample would be painfully slow).

    Filenames are prefixed with their label (`<label>.<original name>`),
    the same uniqueness convention tools/export_edgeimpulse.py already
    uses -- two different bins' crops in the same millisecond only differ
    by their `_bin<n>` suffix, which is only unique within one label's own
    folder.

    Returns {"uploaded": {label: count}, "failures": [str, ...]} -- one
    bad batch is reported, not fatal, same "local disk stays the source of
    truth either way, a partial remote state just gets fixed by the next
    Upload" tolerance the sibling project's upload() documents."""
    from concurrent.futures import ThreadPoolExecutor
    import threading

    by_label = iter_label_images(captures_dir)
    if not by_label:
        raise EIClientError(f"no captured images under {captures_dir!r} to upload")

    total = sum(len(paths) for paths in by_label.values())
    uploaded_count = 0
    failures: List[str] = []
    uploaded: Dict[str, int] = {}
    progress_lock = threading.Lock()

    def progress(**extra) -> None:
        if on_progress:
            on_progress(uploaded=uploaded_count, total=total,
                        failures=list(failures), **extra)

    progress()
    for label, paths in by_label.items():
        samples = []
        for path in paths:
            with open(path, "rb") as f:
                data = f.read()
            samples.append((f"{label}.{os.path.basename(path)}", data))
        batches = list(_batched(samples))

        def send(batch) -> None:
            nonlocal uploaded_count
            try:
                sent = upload_samples(api_key, category, label, batch)
            except EIClientError as e:
                with progress_lock:
                    failures.append(f"{label}: {e}")
                    progress()
                return
            with progress_lock:
                uploaded_count += sent
                uploaded[label] = uploaded.get(label, 0) + sent
                progress()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            list(pool.map(send, batches))

    return {"uploaded": uploaded, "failures": failures}


# ---------------------------------------------------------------------------
# Download -- build the C++ library deployment, then fetch the ZIP
# ---------------------------------------------------------------------------

def build_model(api_key: str, project_id: int, *,
                 engine: str = DEPLOY_ENGINE,
                 model_type: str = DEPLOY_MODEL_TYPE) -> int:
    """Kicks off the on-device-model build job for whatever is currently
    trained in Studio (training itself stays a manual Studio step -- see
    module docstring). Returns the job id for wait_for_job()."""
    resp = _json_post(
        f"{STUDIO_BASE}/api/{project_id}/jobs/build-ondevice-model?type={DEPLOY_TYPE}",
        {"x-api-key": api_key}, {"engine": engine, "modelType": model_type})
    return resp["id"]


def job_status(api_key: str, project_id: int, job_id: int) -> dict:
    resp = _request("GET", f"{STUDIO_BASE}/api/{project_id}/jobs/{job_id}/status",
                     {"x-api-key": api_key})
    return resp.get("job", resp)


def wait_for_job(api_key: str, project_id: int, job_id: int,
                  on_poll: Optional[Callable[[], None]] = None,
                  poll_interval_s: float = _JOB_POLL_INTERVAL_S,
                  timeout_s: float = _JOB_TIMEOUT_S) -> None:
    """Blocks the calling thread, polling job_status() until EI reports
    the build finished. core/main.py's `_handle_ei_download` runs this on
    the tablet's own WebSocket thread (same "blocking here is safe, this
    thread's whole job right now is showing a working... step" reasoning
    `_send_classifier_cmd` already documents), passing on_poll to
    broadcast a "still building" tick each round."""
    deadline = time.monotonic() + timeout_s
    while True:
        job = job_status(api_key, project_id, job_id)
        if job.get("finished"):
            if job.get("finishedSuccessful", True):
                return
            raise EIClientError(f"build job {job_id} on project {project_id} "
                                 "did not finish successfully")
        if time.monotonic() > deadline:
            raise EIClientError(f"build job {job_id} on project {project_id} "
                                 f"timed out after {timeout_s:.0f}s")
        if on_poll:
            on_poll()
        time.sleep(poll_interval_s)


def download_model(api_key: str, project_id: int, *,
                    engine: str = DEPLOY_ENGINE,
                    model_type: str = DEPLOY_MODEL_TYPE) -> bytes:
    """Downloads the deployment ZIP built by build_model() (must already
    be finished -- wait_for_job() first). Returns the raw ZIP bytes,
    exactly the "Studio -> Deployment -> C++ library -> Download" file an
    operator currently saves by hand (models/README.md's
    "hotpot-ingredients" entry) -- unzip it over tools/eim_cpp/vendor/ and
    rebuild, same as that manual step; this module does not do either,
    since consuming the export is `tools/eim_cpp/`'s concern, not the
    fetch's.

    `model_type` MUST match what build_model() built, and defaults to the
    same DEPLOY_MODEL_TYPE for that reason: EI keys a stored deployment by
    (type, engine, modelType), so asking for one triple and building
    another finds nothing. This is not theory -- it is what the first live
    run of this function did on 2026-08-24 against project 1095598, and
    the failure is thoroughly misleading: the build job finishes
    SUCCESSFULLY (39s), then the download 500s with EI's own
    "No deployment exists, did you build yet?", which reads as "the build
    never ran". It had. `modelType` was simply missing from this query,
    so EI looked in the default (float32) slot while build_model() had
    filled the int8 one. Probed live, same project, same finished build:

        type=zip&engine=tflite-eon                  -> 500 no deployment
        type=zip&engine=tflite-eon&modelType=int8   -> 200, 7372366 bytes
        type=zip&modelType=int8                     -> 200, 7372366 bytes
        type=zip                                    -> 500 no deployment

    (`engine` turns out not to matter on THIS endpoint -- `modelType` is
    what selects the slot -- but it is still sent, since matching every
    param build_model() used is the rule that keeps the two in step, and
    the sibling project's own download_model() sends it too.)

    The sibling project never hit this: its build_model() posts
    `{"engine": engine}` with no modelType at all, so both halves land on
    EI's default and agree by accident. The int8 requirement is this
    project's own (models/README.md's locked "quantized (int8)"), which is
    what made the mismatch possible here and not there."""
    url = (f"{STUDIO_BASE}/api/{project_id}/deployment/download"
           f"?type={DEPLOY_TYPE}&engine={engine}&modelType={model_type}")

    def do() -> Tuple[int, bytes]:
        req = urllib.request.Request(url, headers={"x-api-key": api_key}, method="GET")
        try:
            with _urlopen(req, timeout=_TIMEOUT_S) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except urllib.error.URLError as e:
            raise EIClientError(f"{url}: could not reach Edge Impulse ({e.reason})")

    # A larger bound than _request()'s -- this downloads the deployment
    # ZIP's body, not a small JSON reply, so it needs more slack above
    # _TIMEOUT_S before the same DNS/connect-hang backstop kicks in (see
    # _bounded_call's docstring).
    status, raw = _bounded_call(do, timeout_s=_CALL_TIMEOUT_S * 2)
    if status < 200 or status >= 300:
        raise EIClientError(f"{url}: HTTP {status} - {raw[:200].decode('utf-8', 'replace')}")
    return raw

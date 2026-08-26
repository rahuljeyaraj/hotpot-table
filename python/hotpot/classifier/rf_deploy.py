"""classifier/rf_deploy.py — gets a trained Roboflow model from "it exists
in Roboflow" to "the backend can load it", and says so. The Roboflow
sibling of `ei_deploy.py`, per `docs/ROBOFLOW_PATHWAY.md` §6 step 4.

**Much smaller than `ei_deploy.py` (doc §3.4)** — neither Path compiles
anything. Path A (`deploy_path_a`) triggers the weight download and
cache-warm so the first real live `classify()` is not the thing that
discovers there is no network. Path B (`deploy_path_b`) fetches the
`.onnx`, writes it (and, best-effort, a `.classes.json` sidecar) through
`atomicio`, and records the exact filename into `rf_store` — never a
directory glob (§3.4's own rule, the one the Edge Impulse side already
learned the hard way: two `tflite_learn_*.cpp` files both matching one
glob).

**The failure this module exists to prevent** is the one `ei_deploy.py`'s
docstring opens with: a model sitting downloaded on disk for hours while
the live app keeps classifying with the old one, because deploying it was
a separate manual step nothing prompted for. Pressing Deploy is the WHOLE
redeploy for both paths — `RoboflowInferenceBackend`/`RoboflowOnnxBackend`
(`backend_rf.py`) both re-check their own artifact on every `classify()`
call (a store re-read for Path A, an mtime check for Path B's sidecar), so
neither needs the classifier process restarted for a fresh deploy to take
effect.

**Never leave the previous model's file sitting next to the new one**
(§3.4, same argument as `ei_deploy.py`'s wipe-then-extract): Path B's
deploy wipes any previously-deployed `.onnx`/`.classes.json` pair before
writing the new one, so `models/` can never end up with two Roboflow
exports only one of which `rf_project.json` actually names.
"""

from __future__ import annotations

import json
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from hotpot.classifier import rf_client, rf_store
from hotpot.classifier.backend_rf import RoboflowInferenceBackend
from hotpot.common import atomicio

_log = logging.getLogger("hotpot.classifier.rf_deploy")

# Plausible filenames a bundled export might carry the class order under —
# UNVERIFIED (doc §5, V8 covers only whether the download itself is
# permitted, not the export's internal shape). Tried in order; if none of
# these exist inside the download, deploy_path_b still deploys the model
# but returns a loud warning instead of silently leaving classify()
# permanently broken with no explanation (see that function's docstring).
_CLASS_LIST_CANDIDATES = ("class_names.json", "classes.json", "labelmap.txt",
                          "class_names.txt")


class RfDeployError(RuntimeError):
    """Always carries a message meant to be shown to the operator as-is —
    same convention as `ei_deploy.EiDeployError`/`rf_client.RFClientError`.
    """


# ---------------------------------------------------------------------------
# Path A — cache-warm
# ---------------------------------------------------------------------------

def deploy_path_a(*, project_path: Path = rf_store.DEFAULT_PATH,
                   cache_dir: Optional[Path] = None,
                   backend: Optional[RoboflowInferenceBackend] = None,
                   on_progress: Optional[Callable[[str], None]] = None
                   ) -> None:
    """Loads (and, on a fresh cache, downloads) the linked project's
    weights right now, on the operator's own Deploy click, rather than
    leaving it for whatever bin happens to be classified first. Raises
    `RfDeployError` on any failure — a Path A deploy that appears to
    succeed but never actually warmed the cache is worse than an honest
    error, because the failure would otherwise resurface as a live-table
    classify() timeout with no clear cause.

    `backend` is a test seam: pass a `RoboflowInferenceBackend` built with
    a fake `model_factory` to drive this with no `inference` package and
    no network, the same DI shape `core/main.py` already gives
    `ei_client`/`ei_deploy`.
    """
    project = rf_store.load_project(project_path)
    if project is None:
        raise RfDeployError(
            f"{project_path} has no linked Roboflow project — link one "
            "first")
    if on_progress:
        on_progress("loading")
    b = backend or RoboflowInferenceBackend(
        project_path=project_path,
        **({"cache_dir": cache_dir} if cache_dir is not None else {}))
    try:
        b.warm()
    except Exception as e:  # noqa: BLE001 - ClassifierBackendError or SDK failure
        raise RfDeployError(f"could not warm the Roboflow model cache: {e}") from e
    if on_progress:
        on_progress("done")


# ---------------------------------------------------------------------------
# Path B — fetch the .onnx
# ---------------------------------------------------------------------------

def _find_class_list(downloaded_path: Path) -> Optional[list]:
    """Best-effort: if `downloaded_path` is a zip, look for one of
    `_CLASS_LIST_CANDIDATES` inside it and parse a list of names out of
    whichever is found first. Returns None (not an error) if the download
    is a bare `.onnx` with no bundled manifest, or if nothing recognisable
    was found — see `deploy_path_b`'s own docstring for what happens then.
    """
    if downloaded_path.suffix.lower() != ".zip":
        return None
    try:
        with zipfile.ZipFile(downloaded_path) as zf:
            names = {Path(n).name: n for n in zf.namelist()}
            for candidate in _CLASS_LIST_CANDIDATES:
                if candidate not in names:
                    continue
                raw = zf.read(names[candidate]).decode("utf-8", "replace")
                if candidate.endswith(".json"):
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                    if isinstance(parsed, dict):
                        # {"0": "cls_a", "1": "cls_b", ...} — ordered by key
                        return [str(parsed[k]) for k in sorted(parsed, key=int)]
                else:
                    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                    if lines:
                        return lines
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, ValueError) as e:
        _log.warning("rf_deploy: could not read a class list out of %s: %s",
                     downloaded_path, e)
    return None


def _extract_onnx(downloaded_path: Path) -> bytes:
    """Returns the raw `.onnx` bytes, whether the download was a bare
    `.onnx` file or a zip bundling one alongside a manifest. Zip-slip is
    not a concern for a single in-memory `read()` (no path is ever
    extracted to disk from inside the zip — see `ei_deploy.py`'s own guard
    for the pattern to port if a future Roboflow export ever needs
    `extractall()` instead)."""
    if downloaded_path.suffix.lower() != ".zip":
        return downloaded_path.read_bytes()
    with zipfile.ZipFile(downloaded_path) as zf:
        onnx_names = [n for n in zf.namelist() if n.lower().endswith(".onnx")]
        if not onnx_names:
            raise RfDeployError(
                f"{downloaded_path} does not contain a .onnx file")
        if len(onnx_names) > 1:
            raise RfDeployError(
                f"{downloaded_path} contains more than one .onnx file "
                f"({onnx_names!r}) — do not guess which one is current")
        return zf.read(onnx_names[0])


def deploy_path_b(workspace: str, project: str, version: str, api_key: str,
                   *, project_path: Path = rf_store.DEFAULT_PATH,
                   models_dir: Path,
                   model_filename: str = "hotpot-ingredients-rf.onnx",
                   client: Any = rf_client,
                   on_progress: Optional[Callable[[str], None]] = None
                   ) -> dict:
    """Downloads the trained weights (`client.download_weights`, doc §5
    V8 — **paid Roboflow plan required**, §1), writes them to
    `models_dir/model_filename` through `atomicio`, and records that exact
    filename into `rf_store` — never a glob (§3.4).

    Returns `{"model_file": <name>, "class_list_written": bool}`.
    `class_list_written` is False when no bundled class-name manifest
    could be found in the download (`_find_class_list`'s own best-effort,
    every candidate name UNVERIFIED) — the model is still deployed (the
    `.onnx` is on disk and `rf_project.json` names it), but
    `backend_rf.RoboflowOnnxBackend.classify()` will raise
    `ClassifierBackendError` on every call until a `<model>.classes.json`
    sidecar exists beside it, because there is no honest way to guess a
    class order this module was not given. Callers (`core/main.py`'s
    `_handle_rf_deploy`) must surface that in the result message rather
    than reporting a bare `ok: true` — a "successful" deploy that cannot
    actually classify anything is not the whole redeploy this module
    exists to guarantee.
    """
    if on_progress:
        on_progress("downloading")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            downloaded = client.download_weights(
                workspace, project, version, api_key, tmp)
        except Exception as e:  # noqa: BLE001 - RFClientError or SDK failure
            raise RfDeployError(f"could not download weights: {e}") from e
        downloaded_path = Path(downloaded)

        if on_progress:
            on_progress("extracting")
        try:
            onnx_bytes = _extract_onnx(downloaded_path)
        except RfDeployError:
            raise
        except OSError as e:
            raise RfDeployError(f"could not read {downloaded_path}: {e}") from e
        class_list = _find_class_list(downloaded_path)

    models_dir = Path(models_dir)
    dest = models_dir / model_filename
    classes_dest = dest.with_suffix("").with_suffix(".classes.json")

    # Wipe the previous deploy's files before writing the new ones (§3.4) —
    # only if this deploy is landing under a DIFFERENT filename than
    # whatever was previously recorded; the ordinary case (same filename,
    # atomicio overwrite) already can't leave two files behind.
    previous = rf_store.load_project(project_path)
    if previous and previous.get("model_file") and previous["model_file"] != model_filename:
        for stale in (models_dir / previous["model_file"],
                      (models_dir / previous["model_file"]).with_suffix("").with_suffix(".classes.json")):
            try:
                stale.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                _log.warning("rf_deploy: could not remove stale %s: %s", stale, e)

    if on_progress:
        on_progress("writing")
    atomicio.write_bytes(dest, onnx_bytes)
    class_list_written = False
    if class_list:
        atomicio.write_json(classes_dest, class_list)
        class_list_written = True
    else:
        _log.warning(
            "rf_deploy: no class-name manifest found in the Roboflow "
            "download — %s must be created by hand (a JSON list of class "
            "names, in the model's own output order) before "
            "RoboflowOnnxBackend can classify anything", classes_dest)

    rf_store.save_project(
        project_path, workspace, project, api_key,
        version=version, model_file=model_filename)

    if on_progress:
        on_progress("done")
    return {"model_file": model_filename, "class_list_written": class_list_written}

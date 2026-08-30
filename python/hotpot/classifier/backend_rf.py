"""classifier/backend_rf.py — the Roboflow sibling of `backend_ei.py`, per
`docs/ROBOFLOW_PATHWAY.md` §6 step 1.

Two classes behind the same `ClassifierBackend` Protocol (doc §19.4),
picked by `classifier/main.py`'s `build_backend()` off
`config.classifier.backend`, per the plan doc's §1 recommendation to build
both from the start so Path A vs Path B is a config value, not a rewrite:

- `RoboflowInferenceBackend` — Path A, the `inference` package.
  `inference.get_model(model_id, api_key)` downloads and caches weights on
  first use; later calls are offline (`MODEL_CACHE_DIR`). The model handle
  is loaded lazily on first `classify()`, never in `__init__` — the
  same rule `EiCppBackend` follows and for the same reason (doc §3.3): a
  fresh clone with no model built/cached yet must still boot a classifier
  process, and construction must never be what fails.
- `RoboflowOnnxBackend` — Path B, a plain `onnxruntime.InferenceSession`
  over a file in `models/` (§3.4's "never glob" — the exact filename comes
  from `rf_store.py`, not a directory listing).

Both raise `backend_ei.ClassifierBackendError`, never a parallel
exception type — doc §2.1: `classifier/main.py`'s `_classify` catches
that class by name, so a different exception here would crash the whole
pass instead of leaving one bin unresolved. Imported, not redefined.

The heavy dependency (`inference` or `onnxruntime`) is imported inside
the method that needs it, never at module scope — the same seam
`core/scale.py` uses for pyserial and `common/geometry.py` uses for cv2,
so this module (and `test_backend_rf.py`) stays importable and testable on
a machine with neither package installed. Doc §4.3 flags `inference`'s
dependency weight and the real risk of a `pip install` silently downgrading
`penv`'s numpy/opencv/mediapipe pins — nothing in this file ever imports
either package unless `classify()` is actually called.

Colour order. The crop arrives BGR (OpenCV, `common/geometry.
warp_frame_to_stage`'s output). Roboflow models, like Edge Impulse's, are
trained on RGB. Both backends below convert with `cv2.cvtColor(...,
cv2.COLOR_BGR2RGB)` before the model ever sees a pixel — get this wrong and
the model returns confident wrong labels with no error anywhere, the worst
failure mode in this whole feature (doc §6.1). `test_backend_rf.py` has a
test that would go red if either conversion were deleted.

Read the class list from the artifact, never hardcode it.
`backend_ei.py`'s `_InputDims` learned this the hard way (its own module
comment: a hand-maintained input-size constant let a redeployed model sit
on disk while the code kept resizing to the previous one's dimensions).
Same trap, same cure, applied to both backends here — see each class's own
docstring for exactly where its class list and input size come from.

Everything below marked VERIFY is reasoned from Roboflow's published
docs and the `inference`/`onnxruntime` package APIs, not from a live call
or a live account — doc §5's own rule: "Never assume an external API
exists. Verify against the installed version." Neither §5's probes (V1-V8)
nor a real Roboflow account were available while this file was written.
Read each VERIFY comment before trusting the shape it describes; the
constructor overrides next to each one exist so a wrong guess is a
one-line fix at the call site, not a rewrite of this module.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from hotpot.classifier.backend_ei import ClassifierBackendError
from hotpot.classifier import rf_store

_log = logging.getLogger("hotpot.classifier.backend_rf")

_ROOT = Path(__file__).resolve().parents[3]

# Roboflow's own supported local-deployment default, per
# https://inference.roboflow.com/using_inference/offline_weights_download/ —
# `MODEL_CACHE_DIR` unset defaults to `/tmp/cache`, which does not survive a
# reboot (doc §1, Path A's own table). Pinned under `state/` so it sits
# beside every other piece of persistent, gitignored, per-machine state this
# app already keeps there.
DEFAULT_CACHE_DIR = _ROOT / "state" / "rf_model_cache"


def _bgr_to_rgb(bgr_crop: Any):
    """`cv2.cvtColor(..., cv2.COLOR_BGR2RGB)`, imported locally — see this
    module's own docstring on why cv2 never loads at import time. Pulled
    out as its own function so both backends call the exact same
    conversion rather than two copies that could drift apart.
    """
    import cv2  # noqa: WPS433 — local, see module docstring

    return cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# Path A — inference.get_model(), weights cached locally
# ---------------------------------------------------------------------------

class RoboflowInferenceBackend:
    """Path A (doc §1). Holds no model handle until the first `classify()`
    call — `_ensure_model()` below is the only place `inference.get_model()`
    is ever called, and it is called again whenever the linked project
    (`state/rf_project.json`, via `rf_store.py`) has changed since the last
    load, the same "re-read the artifact, don't trust a cached copy" rule
    `backend_ei._InputDims` already enforces for Edge Impulse.

    VERIFY (doc §5, V7/V7b) — none of this has run against a live
    account or a live model. `inference.get_model(model_id, api_key)` and
    `model.infer(image)` are Roboflow's documented entry points
    (appendix's "Local inference" block); the exact shape of what
    `infer()` returns, and whether the returned model object exposes
    `class_names`/an input size at all, have not been confirmed. See
    `_extract_top()` and `_input_size()` below for the fallbacks this
    class uses when a guessed attribute is missing, and confirm the real
    shape with a throwaway script (V7) before trusting a live label.
    """

    def __init__(self, *,
                 project_path: Path = rf_store.DEFAULT_PATH,
                 cache_dir: Path = DEFAULT_CACHE_DIR,
                 model_factory: Optional[Callable[..., Any]] = None) -> None:
        self.project_path = Path(project_path)
        self.cache_dir = Path(cache_dir)
        # Test seam: a fake standing in for inference.get_model, the same
        # role `run` plays in EiCppBackend and `open_port` plays in
        # ScaleReader — a test must be able to drive the whole
        # crop -> resize -> predict -> (label, conf) path with neither the
        # `inference` package nor a real Roboflow account.
        self._model_factory = model_factory
        self._model: Optional[Any] = None
        # What the model was last loaded FOR — (workspace, project,
        # version, api_key). Re-checked on every classify() (a cheap local
        # JSON read, doc §2's rf_store.py), so a redeploy that bumps
        # `version` is picked up on the very next classify() call with no
        # process restart — the same guarantee backend_ei.py's mtime-keyed
        # `_InputDims` gives Edge Impulse.
        self._loaded_key: Optional[tuple] = None
        self._class_names: Optional[List[str]] = None

    def _ensure_model(self) -> Any:
        project = rf_store.load_project(self.project_path)
        if project is None:
            raise ClassifierBackendError(
                f"{self.project_path} has no linked Roboflow project — "
                "link one from the Capture tab's Roboflow card first")
        key = (project["workspace"], project["project"], project["version"],
               project["api_key"])
        if self._model is not None and key == self._loaded_key:
            return self._model

        # model_id is "{project}/{version}", NOT workspace-prefixed — this
        # is the one line the appendix's own "Local inference" block gives
        # verbatim; the workspace is inferred from the api_key, per that
        # same block. VERIFY (V7): confirm this against a live call before
        # trusting it on the rig.
        model_id = f"{project['project']}/{project['version']}"
        try:
            if self._model_factory is not None:
                model = self._model_factory(model_id=model_id,
                                            api_key=project["api_key"])
            else:
                import os  # noqa: WPS433 — local, env var only needed here
                os.environ.setdefault("MODEL_CACHE_DIR", str(self.cache_dir))
                from inference import get_model  # noqa: WPS433
                model = get_model(model_id=model_id,
                                  api_key=project["api_key"])
        except ClassifierBackendError:
            raise
        except Exception as e:  # noqa: BLE001 - any SDK/network failure
            raise ClassifierBackendError(
                f"could not load Roboflow model {model_id!r}: {e}") from e

        class_names = getattr(model, "class_names", None)
        if not class_names:
            raise ClassifierBackendError(
                f"the loaded Roboflow model {model_id!r} has no "
                "class_names — cannot turn a prediction into a label")

        self._model = model
        self._loaded_key = key
        self._class_names = list(class_names)
        return model

    def warm(self) -> None:
        """Forces the model to load right now rather than on the first
        `classify()` call — what `rf_deploy.py`'s Path A deploy step calls
        so the first real classify() after a deploy is not also the thing
        that discovers there is no network (doc §6 step 4: "trigger the
        weight download and cache-warm... while the operator is standing
        there watching a progress line").
        """
        self._ensure_model()

    def classify(self, bgr_crop: Any) -> Tuple[str, float]:
        model = self._ensure_model()
        rgb = _bgr_to_rgb(bgr_crop)

        size = _input_size(model)
        if size is not None:
            import cv2  # noqa: WPS433
            rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_LINEAR)

        try:
            result = model.infer(rgb)
        except Exception as e:  # noqa: BLE001 - any SDK failure
            raise ClassifierBackendError(
                f"Roboflow inference failed: {e}") from e

        return _extract_top(result, self._class_names)


def _input_size(model: Any) -> Optional[Tuple[int, int]]:
    """(width, height) if the loaded model exposes one, else None — in
    which case `classify()` passes the RGB crop through unresized and
    trusts `inference`'s own documented behaviour of accepting an
    arbitrary-sized image and preprocessing it internally. VERIFY:
    which of these two paths is actually correct for a real Roboflow
    classification model has not been confirmed (doc §5, V7). Every
    attribute name tried here is a guess, not a confirmed one — a bare
    `getattr` chain rather than one hardcoded name, so a wrong guess fails
    open (no resize) instead of raising.
    """
    for attr in ("input_size", "image_size", "img_size"):
        size = getattr(model, attr, None)
        if isinstance(size, (tuple, list)) and len(size) == 2:
            return int(size[0]), int(size[1])
        if isinstance(size, int):
            return size, size
    return None


def _extract_top(result: Any, class_names: Optional[List[str]]
                  ) -> Tuple[str, float]:
    """Turns whatever `model.infer()` returned into `(label, confidence)`.

    VERIFY (doc §5, V7): Roboflow's documented classification HTTP
    response carries top-level `top`/`confidence` fields
    (`{"predictions": [...], "top": "<class>", "confidence": 0.98}`), and
    the `inference` package's own response objects are widely described as
    mirroring that shape — but nothing here has been run against a real
    `infer()` call. Three shapes are tried, in order, so a plausible
    variation in what the installed `inference` version actually returns
    does not raise outright; if none match, this is a bug in THIS function
    to fix once V7 shows the real shape, not a bin to leave unresolved.
    """
    # dict-shaped (or dict-like: a pydantic model with .dict()/.model_dump())
    obj = result
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    elif hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        obj = obj.dict()

    if isinstance(obj, dict):
        if "top" in obj and "confidence" in obj:
            return str(obj["top"]), float(obj["confidence"])
        preds = obj.get("predictions")
        if preds:
            return _best_of(preds)
    elif isinstance(obj, list) and obj:
        return _best_of(obj)
    else:
        top = getattr(obj, "top", None)
        conf = getattr(obj, "confidence", None)
        if top is not None and conf is not None:
            return str(top), float(conf)
        preds = getattr(obj, "predictions", None)
        if preds:
            return _best_of(preds)

    raise ClassifierBackendError(
        f"could not read a label/confidence out of the Roboflow response "
        f"({type(result).__name__!r}) — see backend_rf._extract_top's own "
        "VERIFY comment")


def _best_of(preds: List[Any]) -> Tuple[str, float]:
    def conf_of(p: Any) -> float:
        if isinstance(p, dict):
            return float(p.get("confidence", 0.0))
        return float(getattr(p, "confidence", 0.0))

    def label_of(p: Any) -> str:
        if isinstance(p, dict):
            return str(p.get("class") or p.get("class_name") or p.get("label"))
        return str(getattr(p, "class_name", None) or getattr(p, "class_", None))

    best = max(preds, key=conf_of)
    return label_of(best), conf_of(best)


# ---------------------------------------------------------------------------
# Path B — ONNX export + onnxruntime
# ---------------------------------------------------------------------------

class _ClassListFile:
    """Caches the class-name list read from a JSON file (a plain list of
    strings), re-reading only when the file's mtime changes — the exact
    role `backend_ei._InputDims` plays for `model_metadata.h`, generalised
    to a list of names instead of two ints.

    This file's format is this repo's own convention, not Roboflow's —
    doc §1 Path B: Roboflow does not support weights used outside its own
    Inference ecosystem, so there is no vendor-shipped sidecar to read.
    `rf_deploy.py` writes it (`<model>.classes.json`, doc §3.4's "record
    the exact filename, never glob") alongside the `.onnx` at deploy time,
    reading the class order from whatever manifest Roboflow's own weights
    download response carries — VERIFY that shape (doc §5, V8) before
    trusting the file this class reads.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._mtime: Optional[float] = None
        self._names: Optional[List[str]] = None

    def get(self) -> List[str]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError as e:
            raise ClassifierBackendError(
                f"{self._path} does not exist — deploy a Roboflow ONNX "
                "model first (rf_deploy.py writes this alongside the "
                ".onnx file)") from e
        if self._names is None or mtime != self._mtime:
            try:
                names = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                raise ClassifierBackendError(
                    f"{self._path} is not readable as a JSON list of "
                    f"class names: {e}") from e
            if not isinstance(names, list) or not names:
                raise ClassifierBackendError(
                    f"{self._path} does not hold a non-empty list of "
                    "class names")
            self._names = [str(n) for n in names]
            self._mtime = mtime
        return self._names


class RoboflowOnnxBackend:
    """Path B (doc §1). `onnxruntime.InferenceSession` over a single
    `.onnx` file in `models/` — the exact filename comes from
    `state/rf_project.json`'s `model_file` field (`rf_store.py`), never a
    directory glob (§3.4's "never glob" rule, the same trap the EI side
    already hit with two `tflite_learn_*.cpp` files both matching one
    glob).

    The session is built lazily on first `classify()`, same reasoning as
    `RoboflowInferenceBackend` and `EiCppBackend`: construction must never
    be what fails a fresh clone with no model deployed yet.

    VERIFY: the input tensor's shape/layout (NCHW vs NHWC) is read off
    `session.get_inputs()[0]` at classify() time — real `onnxruntime` API,
    not a Roboflow-specific guess — but has not been checked against a
    real Roboflow-exported ONNX classification model (doc §5, V8 covers
    only whether the download is permitted, not the export's own shape).
    """

    def __init__(self, *,
                 project_path: Path = rf_store.DEFAULT_PATH,
                 models_dir: Path = _ROOT / "models",
                 session_factory: Optional[Callable[..., Any]] = None
                 ) -> None:
        self.project_path = Path(project_path)
        self.models_dir = Path(models_dir)
        # Test seam, same role as RoboflowInferenceBackend's model_factory:
        # a fake standing in for onnxruntime.InferenceSession.
        self._session_factory = session_factory
        self._session: Optional[Any] = None
        self._loaded_model_file: Optional[str] = None
        self._classes: Optional[_ClassListFile] = None
        self._input_name: Optional[str] = None

    def _ensure_session(self) -> Any:
        project = rf_store.load_project(self.project_path)
        if project is None or not project.get("model_file"):
            raise ClassifierBackendError(
                f"{self.project_path} has no deployed Roboflow ONNX model "
                "— deploy one from the Capture tab's Roboflow card first")
        model_file = project["model_file"]
        onnx_path = self.models_dir / model_file
        if not onnx_path.exists():
            raise ClassifierBackendError(
                f"{onnx_path} does not exist — {self.project_path} names "
                "it, but the file is missing (deploy again?)")

        if self._session is not None and model_file == self._loaded_model_file:
            return self._session

        try:
            if self._session_factory is not None:
                session = self._session_factory(str(onnx_path))
            else:
                import onnxruntime  # noqa: WPS433 — local, see module docstring
                session = onnxruntime.InferenceSession(str(onnx_path))
        except ClassifierBackendError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ClassifierBackendError(
                f"could not load {onnx_path}: {e}") from e

        self._session = session
        self._loaded_model_file = model_file
        self._input_name = session.get_inputs()[0].name
        self._classes = _ClassListFile(
            onnx_path.with_suffix("").with_suffix(".classes.json"))
        return session

    def classify(self, bgr_crop: Any) -> Tuple[str, float]:
        import numpy as np  # noqa: WPS433

        session = self._ensure_session()
        class_names = self._classes.get()
        rgb = _bgr_to_rgb(bgr_crop)

        height, width, nchw = _onnx_input_shape(session)
        if height and width:
            import cv2  # noqa: WPS433
            rgb = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_LINEAR)

        tensor = rgb.astype(np.float32) / 255.0
        if nchw:
            tensor = np.transpose(tensor, (2, 0, 1))
        tensor = np.expand_dims(tensor, axis=0)

        try:
            outputs = session.run(None, {self._input_name: tensor})
        except Exception as e:  # noqa: BLE001
            raise ClassifierBackendError(f"onnxruntime inference failed: {e}") from e
        if not outputs:
            raise ClassifierBackendError("onnxruntime returned no outputs")

        scores = np.asarray(outputs[0]).reshape(-1)
        if len(scores) != len(class_names):
            raise ClassifierBackendError(
                f"model output has {len(scores)} scores but "
                f"{len(class_names)} class names are known — the .onnx "
                "and its .classes.json sidecar have drifted apart")
        idx = int(np.argmax(scores))
        # Softmax over the raw scores rather than trusting them to already
        # be probabilities (a classification head's output may be raw
        # logits) — cheap, numerically stable, and a no-op if the scores
        # already summed to ~1.
        exp = np.exp(scores - np.max(scores))
        probs = exp / exp.sum()
        return class_names[idx], float(probs[idx])


def _onnx_input_shape(session: Any) -> Tuple[Optional[int], Optional[int], bool]:
    """(height, width, is_nchw) off the session's own first input, or
    (None, None, False) if the shape is symbolic/unusable — in which case
    `classify()` skips resizing entirely rather than guessing.
    """
    shape = list(session.get_inputs()[0].shape)
    dims = [d for d in shape if isinstance(d, int)]
    if len(shape) != 4:
        return None, None, False
    # NCHW: dim 1 is a small channel count (1 or 3). NHWC: dim 1/2 are
    # spatial, dim 3 is the channel count. Preferring an explicit check on
    # the channel-sized dimension over positional assumption, since a
    # Roboflow export's layout is unverified (doc §5).
    if isinstance(shape[1], int) and shape[1] in (1, 3):
        h, w = shape[2], shape[3]
        nchw = True
    elif isinstance(shape[3], int) and shape[3] in (1, 3):
        h, w = shape[1], shape[2]
        nchw = False
    else:
        return None, None, False
    if not (isinstance(h, int) and isinstance(w, int) and h > 0 and w > 0):
        return None, None, False
    return h, w, nchw

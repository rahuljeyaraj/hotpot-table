"""classifier/backend_ei.py — doc section 19.4's real `ClassifierBackend`.

Doc section 19.4 names this file's implementation `ImageImpulseRunner`
(the `edge_impulse_linux` Python package wrapping a Linux `.eim`). That
package needs Linux — this dev machine is Windows (CLAUDE.md), and the
whole reason doc section 19.4 made the backend split "mandatory" in the
first place was so a choice like this could be made per machine without
touching `classifier/main.py` or anything upstream of it. What is here
instead is the same idea EON's own C++ export makes possible directly:
compile the impulse into a small native CLI (`tools/eim_cpp/`, see that
directory's own README-equivalent — its CMakeLists.txt's top comment) and
shell out to it, the same subprocess-call shape `camera/capture.py`
already uses for `v4l2-ctl` rather than trusting an inconsistent library
binding. Built once with MSVC on this machine; the identical `vendor/`
source cross-compiles with gcc for the ODYSSEY (doc section 1.4) when that
board exists, which is *why* the C++ library was the deployment target
picked over the Linux-only `.eim` route in the first place — see the
conversation that led here for the reasoning, or just: this backend is
the one that does not require a specific board to already be in hand.

The binary's own contract (see tools/eim_cpp/main.cpp's top comment):
stdin is not used; one argv, a path to a small raw file — int32 width,
int32 height, then width*height*3 raw **RGB** (not BGR) bytes, already
resized to the model's exact input size — and stdout is one line of JSON,
`{"labels":[{"label":"...","value":0.83}, ...]}` in the model's own class
order. This module owns every step of getting a `bgr_crop` numpy array
into that shape; the binary refuses (loudly, doc's own error path) rather
than guessing if the size is wrong, so a mismatch here is a bug in this
file, not a silent misclassification.
"""

from __future__ import annotations

import json
import logging
import platform
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

_log = logging.getLogger("hotpot.classifier.backend_ei")

# tools/eim_cpp/main.cpp -> repo root is parents[3] from this file
# (python/hotpot/classifier/backend_ei.py); kept as a module constant, not
# recomputed per call, for the same reason classifier/main.py's CAPTURES_DIR
# is one.
_ROOT = Path(__file__).resolve().parents[3]

# Input size is read out of model-parameters/model_metadata.h at classify()
# time instead of hardcoded here (see _InputDims below) — a hand-maintained
# constant was exactly the bug that let 2026-08-24's redeploy (project
# 1095598, 224x224, 13 classes) sit downloaded and unzipped while this file
# kept resizing crops to the PREVIOUS model's 160x160, three separate
# things (unzip vendor/, bump these numbers, rebuild classify.exe) that had
# to be done together by hand and silently weren't. classify.exe itself
# already refuses a size mismatch loudly (tools/eim_cpp/main.cpp's own
# EI_CLASSIFIER_RAW_SAMPLE_COUNT check) — reading the header here removes
# the one place a mismatch could happen without either side noticing.

DEFAULT_TIMEOUT_S = 3.0


class _InputDims:
    """Caches (width, height) parsed from vendor/model-parameters/
    model_metadata.h, re-reading only when that file's mtime changes — so a
    classifier process that has been running since before a redeploy picks
    up the new model's input size on its very next classify() call, with no
    restart, the moment tools/eim_cpp/rebuild.bat finishes (ei_deploy.py).
    """

    def __init__(self, metadata_path: Path) -> None:
        self._path = metadata_path
        self._mtime: Optional[float] = None
        self._dims: Optional[Tuple[int, int]] = None

    def get(self) -> Tuple[int, int]:
        try:
            mtime = self._path.stat().st_mtime
        except OSError as e:
            raise ClassifierBackendError(
                f"{self._path} does not exist — unzip an Edge Impulse "
                "export over tools/eim_cpp/vendor/ first") from e
        if self._dims is None or mtime != self._mtime:
            self._dims = self._parse()
            self._mtime = mtime
        return self._dims

    def _parse(self) -> Tuple[int, int]:
        text = self._path.read_text(encoding="utf-8", errors="replace")
        width = _find_define(text, "EI_CLASSIFIER_INPUT_WIDTH", self._path)
        height = _find_define(text, "EI_CLASSIFIER_INPUT_HEIGHT", self._path)
        return width, height


def _find_define(text: str, macro: str, path: Path) -> int:
    import re  # noqa: WPS433 — local, only needed for this one-shot parse
    m = re.search(rf"#define\s+{re.escape(macro)}\s+(\d+)", text)
    if not m:
        raise ClassifierBackendError(
            f"{path} has no '#define {macro} <n>' line — not a valid "
            "Edge Impulse model_metadata.h")
    return int(m.group(1))


def _default_binary_path() -> Path:
    """`tools/eim_cpp/build/classify[.exe]` — Windows gets the `.exe` this
    dev machine's own MSVC build produces; anything else (the ODYSSEY,
    doc section 1.4, once it exists) gets the extension-less gcc build
    from the identical `vendor/` source, per this module's own top
    comment on why the C++ library route was chosen at all.
    """
    name = "classify.exe" if platform.system() == "Windows" else "classify"
    return _ROOT / "tools" / "eim_cpp" / "build" / name


def _default_metadata_path() -> Path:
    return (_ROOT / "tools" / "eim_cpp" / "vendor" / "model-parameters"
            / "model_metadata.h")


class ClassifierBackendError(RuntimeError):
    """Raised for anything that stops a real answer being possible: the
    binary is missing (not built yet), it exited non-zero, it timed out,
    or it printed something that is not the JSON this module expects.
    Never swallowed here — `classifier/main.py` is the layer that turns
    this into doc section 4.7's `{"ok": false, "error": ...}` reply, the
    same split `ClassifierError` already has for every other failure mode
    in that file.
    """


class EiCppBackend:
    """Shells out to the compiled `classify` CLI once per `classify()`
    call. One process per bin per pass rather than a long-lived worker the
    SDK stays loaded in — simpler, and cheap enough at doc section 8.6's
    `live_hz: 2`: `config.classifier.live_hz` puts an entire 8-bin pass on
    a multi-hundred-millisecond budget, not a per-frame one.
    """

    def __init__(self, *, binary_path: Optional[Path] = None,
                 metadata_path: Optional[Path] = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 run: Optional[callable] = None) -> None:
        self.binary_path = Path(binary_path) if binary_path else _default_binary_path()
        self.timeout_s = timeout_s
        self._dims = _InputDims(
            Path(metadata_path) if metadata_path else _default_metadata_path())
        # Test seam: a fake standing in for subprocess.run, the same role
        # `video_capture_factory` plays for WindowsCapture — a test must be
        # able to drive this with no compiled binary on disk at all.
        self._run = run or subprocess.run

    def classify(self, bgr_crop) -> Tuple[str, float]:
        import cv2       # noqa: WPS433 — local, see capture.py's own reasoning
        import numpy as np  # noqa: WPS433

        if not self.binary_path.exists():
            raise ClassifierBackendError(
                f"{self.binary_path} does not exist — build it first "
                "(tools/eim_cpp/CMakeLists.txt's top comment has the "
                "MSVC/nmake steps this was built with)")

        width, height = self._dims.get()

        # Squash resize (EI_CLASSIFIER_RESIZE_MODE, doc section 19.2) — a
        # plain resize to the model's exact input size, not a crop-
        # preserving one, because the crop this method receives already
        # IS the bin rect (§19.2's first bullet: "the bin rect already
        # localises the food"); there is no extra frame around it to crop
        # into a square from.
        resized = cv2.resize(bgr_crop, (width, height),
                             interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)

        with tempfile.NamedTemporaryFile(
                suffix=".raw", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(struct.pack("<ii", width, height))
            tmp.write(rgb.tobytes())

        try:
            try:
                proc = self._run([str(self.binary_path), str(tmp_path)],
                                 capture_output=True, text=True,
                                 timeout=self.timeout_s, check=False)
            except subprocess.TimeoutExpired as e:
                raise ClassifierBackendError(
                    f"{self.binary_path.name} took longer than "
                    f"{self.timeout_s}s") from e
            if proc.returncode != 0:
                raise ClassifierBackendError(
                    f"{self.binary_path.name} exited {proc.returncode}: "
                    f"{proc.stderr.strip()}")
            try:
                parsed = json.loads(proc.stdout)
                labels = parsed["labels"]
                best = max(labels, key=lambda entry: entry["value"])
                return str(best["label"]), float(best["value"])
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                raise ClassifierBackendError(
                    f"{self.binary_path.name} printed something this "
                    f"module could not parse: {proc.stdout!r}") from e
        finally:
            # Best-effort — a leaked temp .raw file is disk, not a
            # correctness problem, and must never be what turns a
            # classify() failure into a second, more confusing one.
            try:
                tmp_path.unlink()
            except OSError:
                pass

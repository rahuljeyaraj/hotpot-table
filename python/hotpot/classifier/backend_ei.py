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

# **Must match model-parameters/model_metadata.h's EI_CLASSIFIER_INPUT_
# WIDTH/HEIGHT for whichever .zip tools/eim_cpp/vendor/ currently holds.**
# Not read out of that header at runtime — this module has no C preprocessor
# — so a retrained model with a different input size needs this constant
# updated by hand alongside rebuilding tools/eim_cpp. Checked against the
# current export (2026-08-13, hotpot-ingredients project, deploy v2): 160x160.
#
# **A newer export is already downloaded and NOT yet vendored:**
# models/hotpot-ingredients.zip (2026-08-24, project 1095598, 13 classes)
# is 224x224. These constants are right for what tools/eim_cpp/vendor/
# holds TODAY (project 1087506, 160x160, 8 classes -- checked) and must
# not be bumped on their own: unzipping that ZIP over vendor/, changing
# these two numbers to 224, and rebuilding tools/eim_cpp are one change,
# not three. Doing any of them alone gives a binary whose preprocessing
# silently disagrees with its own model.
INPUT_WIDTH = 160
INPUT_HEIGHT = 160

DEFAULT_TIMEOUT_S = 3.0


def _default_binary_path() -> Path:
    """`tools/eim_cpp/build/classify[.exe]` — Windows gets the `.exe` this
    dev machine's own MSVC build produces; anything else (the ODYSSEY,
    doc section 1.4, once it exists) gets the extension-less gcc build
    from the identical `vendor/` source, per this module's own top
    comment on why the C++ library route was chosen at all.
    """
    name = "classify.exe" if platform.system() == "Windows" else "classify"
    return _ROOT / "tools" / "eim_cpp" / "build" / name


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
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 run: Optional[callable] = None) -> None:
        self.binary_path = Path(binary_path) if binary_path else _default_binary_path()
        self.timeout_s = timeout_s
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

        # Squash resize (EI_CLASSIFIER_RESIZE_MODE, doc section 19.2) — a
        # plain resize to the model's exact input size, not a crop-
        # preserving one, because the crop this method receives already
        # IS the bin rect (§19.2's first bullet: "the bin rect already
        # localises the food"); there is no extra frame around it to crop
        # into a square from.
        resized = cv2.resize(bgr_crop, (INPUT_WIDTH, INPUT_HEIGHT),
                             interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb, dtype=np.uint8)

        with tempfile.NamedTemporaryFile(
                suffix=".raw", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(struct.pack("<ii", INPUT_WIDTH, INPUT_HEIGHT))
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

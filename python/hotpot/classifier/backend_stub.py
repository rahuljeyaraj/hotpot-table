"""classifier/backend_stub.py — doc section 19.4's `ClassifierBackend`
Protocol, satisfied with no model and no camera at all.

"The stub is not throwaway code — it stays forever as the offline test
path and as the fallback if a model file is missing on demo day" (doc
section 19.4). `config.classifier.backend == "stub"` is the committed
default (config/system.default.json) precisely so a fresh clone with no
`.eim`/no compiled `classify` binary still boots a classifier that answers
every `classify` command instead of one M7 quietly can't run.

Doc section 19.4's own docstring describes this class: "returns the label
already in bin_map with conf 0.99, or cycles labels deterministically when
asked to." The classifier process has no access to `core`'s bin_map at all
(a hard process boundary — doc section 3) so "the label already in
bin_map" cannot mean literally that; what is implementable, and what this
does, is the second half — cycle a fixed, deterministic label list one
step per call. Deterministic on purpose: a test asserting "bin 3 comes
back labelled X" must get the same X every run, not whatever a random
model would have said.
"""

from __future__ import annotations

import threading
from typing import List, Sequence, Tuple

# A plausible-looking default rather than an arbitrary "a"/"b"/"c" — so a
# staff view exercised against the stub during a demo shows real catalogue
# names, not placeholder junk. Doc section 8.1's `class_name` values.
DEFAULT_LABELS: Sequence[str] = (
    "button_mushrooms", "chicken_eggs", "soya_chunks",
)

DEFAULT_CONF = 0.99


class StubBackend:
    """Cycles through `labels`, one step per `classify()` call, at a fixed
    confidence. `labels`/`conf` are constructor parameters, not module
    constants a test would have to monkeypatch, for the same reason
    `FakeCapture`'s frame queue is a constructor parameter (capture.py).
    """

    def __init__(self, labels: Sequence[str] = DEFAULT_LABELS,
                 conf: float = DEFAULT_CONF) -> None:
        if not labels:
            raise ValueError("StubBackend needs at least one label")
        self.labels: List[str] = list(labels)
        self.conf = conf
        self._n = 0
        # classifier/main.py's `_classify` now dispatches every bin's
        # `classify()` call concurrently (a thread pool, see that method's
        # own docstring) — without this, two threads racing
        # `self._n % len(self.labels)` / `self._n += 1` could read the same
        # `_n` before either writes it, handing out a duplicate label
        # (and, depending on timing, only advancing the cycle by one
        # instead of two). Which physical bin gets which step of the cycle
        # is still not orderable under concurrency — nothing here ever
        # promised that — but the increment itself is now atomic.
        self._lock = threading.Lock()

    def classify(self, bgr_crop) -> Tuple[str, float]:
        # `bgr_crop` is accepted and ignored — the Protocol's whole point
        # (doc section 19.4) is that a caller cannot tell which backend is
        # behind it from the call site, so this must take the same
        # argument backend_ei.py's real implementation does.
        with self._lock:
            label = self.labels[self._n % len(self.labels)]
            self._n += 1
        return label, self.conf

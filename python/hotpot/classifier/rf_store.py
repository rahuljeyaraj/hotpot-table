"""classifier/rf_store.py — on-disk record of the linked Roboflow project
(`state/rf_project.json`), the Roboflow sibling of `ei_store.py` (doc
`ROBOFLOW_PATHWAY.md` §6 step 2).

Copied from `ei_store.py` almost verbatim per that step's own instruction
("it is 86 lines and every decision in it is already argued") — same
`atomicio` write-then-fsync-then-rename, same best-effort 0600, same
"missing file is a first boot, not an error" rule. Two real shape
differences from Edge Impulse's `EIProject`, both named in the plan doc:

- `workspace`/`project` are string slugs (Roboflow's own naming),
  not a single integer id like EI's `project_id`.
- `version`/`model_file` track what is actually DEPLOYED right now —
  `version` is the trained dataset version currently live (Path A: what
  `backend_rf.RoboflowInferenceBackend` asks `inference.get_model()` for);
  `model_file` is the exact `.onnx` filename in `models/` for Path B, per
  §3.4's "never glob" rule — the same trap that bit the Edge Impulse side
  once already (two `tflite_learn_*.cpp` files both picked up by one glob).
  Both are optional (`None` until a train/deploy has actually happened) —
  a linked-but-not-yet-trained project is a real, ordinary state.

The API key here can spend real Roboflow account training credits (doc
§4.2), the same argument `ei_store.py`'s own docstring makes for EI build
jobs — hence the same 0600 best-effort (a no-op on Windows, this dev
machine, CLAUDE.md).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, TypedDict, Union

from hotpot.common import atomicio

PathLike = Union[str, "os.PathLike[str]"]

# python/hotpot/classifier/rf_store.py -> repo root is four `.parent`s up.
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = _ROOT / "state" / "rf_project.json"


class RFProject(TypedDict):
    workspace: str
    project: str
    api_key: str
    version: Optional[str]
    model_file: Optional[str]


def load_project(path: PathLike = DEFAULT_PATH) -> Optional[RFProject]:
    """None if nothing has been linked yet — mirrors every other store in
    this codebase's "missing file is a first boot, not an error" rule
    (atomicio.py's own docstring, and ei_store.load_project's identical
    one).
    """
    data = atomicio.read_json(path, None)
    if not data:
        return None
    # Tolerate a file written before `version`/`model_file` existed (a
    # link with no training/deploy yet) rather than raising KeyError —
    # same "an absent field is a real, ordinary state" reasoning
    # RFProject's own docstring gives.
    data.setdefault("version", None)
    data.setdefault("model_file", None)
    return data


def save_project(path: PathLike, workspace: str, project: str,
                  api_key: str, *, version: Optional[str] = None,
                  model_file: Optional[str] = None) -> None:
    """Writes the whole record every time — never a partial update — so a
    dropped field can never silently persist from a previous write. A
    caller updating just `version` (e.g. after a train) must first
    `load_project()` and pass the rest back through, the same discipline
    `core/main.py`'s bin-grid saves already use for "all eight rects on
    every Save, never a delta."
    """
    atomicio.write_json(path, {
        "workspace": workspace, "project": project, "api_key": api_key,
        "version": version, "model_file": model_file,
    })
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Best-effort, same tolerance ei_store.save_project gives: Windows
        # has no real equivalent of owner-only Unix perms, and failing to
        # save the link over it would be worse than the secret being only
        # as protected as the rest of state/ (already gitignored).
        pass


def remove_project(path: PathLike = DEFAULT_PATH) -> bool:
    """Drops the saved link — e.g. the Roboflow project was deleted by
    hand and there is nothing left locally worth keeping. Returns whether
    there was anything to remove. Never calls Roboflow's own API — same
    "purely local, no surprising blast radius" rule `ei_store.
    remove_project`'s docstring already states.
    """
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True

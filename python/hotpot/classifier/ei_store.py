"""classifier/ei_store.py — on-disk record of the linked Edge Impulse
project (`state/ei_project.json`), sibling to `ei_client.py`.

Single project, not a device_type-keyed mapping: unlike the project this
was ported from (which links a separate EI project per device type),
hotpot-table has exactly one image-classification project
(`hotpot-ingredients`, doc section 19.2) today. Kept as its own tiny
module rather than folded into `ei_client.py` for the same reason that
project keeps `ei_projects.py` separate from `ei_client.py`: this is
on-disk state, not a network call, and the two change for different
reasons.

Holds the one secret this app persists that was typed into the staff view
rather than committed config: `api_key` can trigger real Edge Impulse
build jobs and spend account compute, so the file is written 0600
(owner-only) where the platform supports it — best-effort, a no-op on
Windows (this dev machine, CLAUDE.md), same "Windows is the dev box,
Linux is the deploy target" carve-out `common/atomicio.py`'s `_fsync_dir`
already makes. The EI username/password/JWT used to *create* the project
are never written here or anywhere else — core/main.py's
`_handle_ei_link` holds them in memory only for the duration of one link
call.

Uses `common/atomicio`'s write-then-fsync-then-rename, this project's own
durable-write convention (doc section 20.4), rather than the bare
tmp-write-replace the ported-from project's `ei_projects.py` used —
`state/` here already goes through atomicio for every other file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, TypedDict, Union

from hotpot.common import atomicio

PathLike = Union[str, "os.PathLike[str]"]

# python/hotpot/classifier/ei_store.py -> repo root is four `.parent`s up.
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = _ROOT / "state" / "ei_project.json"


class EIProject(TypedDict):
    project_id: int
    api_key: str
    project_name: str


def load_project(path: PathLike = DEFAULT_PATH) -> Optional[EIProject]:
    """None if nothing has been linked yet -- mirrors every other store in
    this codebase's "missing file is a first boot, not an error" rule
    (atomicio.py's own docstring)."""
    data = atomicio.read_json(path, None)
    if not data:
        return None
    return data


def save_project(path: PathLike, project_id: int, api_key: str,
                  project_name: str) -> None:
    atomicio.write_json(path, {
        "project_id": project_id, "api_key": api_key,
        "project_name": project_name,
    })
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Best-effort, same tolerance atomicio._fsync_dir gives a
        # filesystem that refuses fsync: Windows has no real equivalent of
        # owner-only Unix perms, and failing to save the link over it
        # would be worse than the secret being merely as protected as the
        # rest of state/ (already gitignored, doc section 8).
        pass


def remove_project(path: PathLike = DEFAULT_PATH) -> bool:
    """Drops the saved link -- e.g. the Studio project was deleted by
    hand and there is nothing left locally worth keeping. Returns whether
    there was anything to remove."""
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True

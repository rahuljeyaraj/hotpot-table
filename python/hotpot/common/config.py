"""config/system.json over config/system.default.json (doc section 8.6).

`common/stub.py` deferred this outright: "config loading is not built until
it has a reader that needs more than one key." M3 build item 2 is that
reader — `camera/main.py` needs `device`, `capture`, `fps`, `mjpeg_port`,
`mjpeg_width`, `mjpeg_fps` and `host_for_browser` all at once, so hand-
threading them one at a time the way `stub.py` hardcodes `CORE_HOST`/
`CORE_PORT` stops being the cheaper option here.

The hard rule, restated from `.gitignore`'s own comment: **the default is
committed, the live file is not.** `load()` seeds `config/system.json` from
`config/system.default.json` on first run, then deep-merges the live file
over the default so a `system.json` written against an older doc revision
still picks up any key a newer default adds, instead of `KeyError`-ing the
first process that reads it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional, Union

from hotpot.common import atomicio

PathLike = Union[str, "Path"]

# python/hotpot/common/config.py -> repo root is four `.parent`s up.
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = _ROOT / "config" / "system.default.json"
CONFIG_PATH = _ROOT / "config" / "system.json"


def load(path: Optional[PathLike] = None,
         default_path: Optional[PathLike] = None) -> Dict[str, Any]:
    """The effective config: live file deep-merged over the committed default.

    Seeds `path` from `default_path` if `path` does not exist yet — the
    "seeded from default on first run" doc section 7 promises. Uses
    `atomicio` for both, so a first-boot seed is as crash-safe as any other
    state write and a half-written `system.json` can never be read back.
    """
    cfg_path = Path(path) if path is not None else CONFIG_PATH
    def_path = Path(default_path) if default_path is not None else DEFAULT_PATH

    default = atomicio.read_json(def_path)
    if not cfg_path.exists():
        atomicio.write_json(cfg_path, default)
        return copy.deepcopy(default)

    local = atomicio.read_json(cfg_path)
    return _deep_merge(default, local)


def get(cfg: Dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    """`get(cfg, "camera.mjpeg_port")` instead of three `.get()`s in a row.

    Missing at any level returns `default` rather than raising — the same
    tolerance `atomicio.read_json`'s caller-supplied default gives a missing
    file, applied one level down.
    """
    node: Any = cfg
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _deep_merge(base: Any, override: Any) -> Any:
    """`override` wins at every leaf; dicts merge key-by-key, recursively.

    A non-dict `override` (including one that is simply absent, `None` from
    a missing key) always wins outright rather than merging — a config value
    is either a scalar/list a human set, or it is not there, never a partial
    structure to reconcile field-by-field.
    """
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)
    out = dict(base)
    for key, value in override.items():
        out[key] = _deep_merge(base.get(key), value)
    return out

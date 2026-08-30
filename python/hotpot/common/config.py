"""config/system.json over config/system.default.json (doc section 8.6).

`common/stub.py` deferred this outright: "config loading is not built until
it has a reader that needs more than one key." M3 build item 2 is that
reader — `camera/main.py` needs `device`, `capture`, `fps`, `mjpeg_port`,
`mjpeg_width`, `mjpeg_fps` and `host_for_browser` all at once, so hand-
threading them one at a time the way `stub.py` hardcodes `CORE_HOST`/
`CORE_PORT` stops being the cheaper option here.

The hard rule, restated from `.gitignore`'s own comment: the default is
committed, the live file is not. `load()` seeds `config/system.json` from
`config/system.default.json` on first run, then deep-merges the live file
over the default so a `system.json` written against an older doc revision
still picks up any key a newer default adds, instead of `KeyError`-ing the
first process that reads it.
"""

from __future__ import annotations

import copy
import logging
import socket
from pathlib import Path
from typing import Any, Dict, Optional, Union

from hotpot.common import atomicio

log = logging.getLogger("hotpot.config")

PathLike = Union[str, "Path"]

# Doc section 8.6's `camera.host_for_browser`, and the values that mean
# "work it out" rather than naming a host.
#
# Loopback counts as `auto`, and that is the point of this list. The
# key's whole job is to name the host a browser on SOMEBODY ELSE'S device
# types — a tablet, a diner's phone scanning the projected QR — and that
# browser is never on this machine, so `localhost` there cannot be a
# deliberate choice; it is the placeholder that shipped in
# `config/system.default.json` and then sat in every live `system.json`
# unnoticed. Developer, 2026-08-25: "the qr code is showing some local
# host url which is not reachable in my phone even if it is in same wifi
# network."
#
# A real hostname or IP is still honoured verbatim — that is how a rig
# with a DNS name or a pinned static address opts out of the guess.
AUTO_HOSTS = ("", "auto", "localhost", "127.0.0.1", "::1")

# TEST-NET-1 (RFC 5737): reserved, never routed, and nothing is listening
# on it. A UDP `connect()` sends no packet — it only asks the kernel to
# pick the source address it WOULD use — so this resolves the default
# route's own interface with no traffic, no DNS, and no timeout.
_ROUTE_PROBE = ("192.0.2.1", 9)


def lan_ip() -> Optional[str]:
    """This machine's address on the network a phone would reach it from,
    or None if that cannot be worked out.

    `socket.gethostbyname(gethostname())` is NOT the first choice: on
    Windows it commonly answers `127.0.0.1`, and on a multi-homed box it
    answers whichever address the resolver likes rather than the one the
    default route uses. Asking the routing table directly is the only
    version that answers the question actually being asked.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(_ROUTE_PROBE)
            ip = s.getsockname()[0]
        finally:
            s.close()
    except OSError as e:               # no route at all, e.g. cable out
        log.debug("config: no default route to probe for a LAN ip: %s", e)
        ip = ""
    if not ip or ip.startswith("127."):
        # Last resort, and it is allowed to fail: a machine genuinely off
        # the network has no answer to give, and inventing one would put
        # an unreachable address in a QR just as confidently as
        # `localhost` did.
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            return None
    return ip if ip and not ip.startswith("127.") else None


def resolve_browser_host(configured: Any) -> str:
    """`camera.host_for_browser` as something a phone can actually reach.

    Returns the configured value untouched unless it is one of
    `AUTO_HOSTS`, in which case this machine's LAN address is
    substituted. Falls back to `"localhost"` — loudly — when there is no
    LAN address to substitute, because a wrong-but-visible answer beats a
    silent one: the log line is what tells an operator why the QR on the
    table is the one that does not work.
    """
    host = str(configured or "").strip()
    if host.lower() not in AUTO_HOSTS:
        return host
    found = lan_ip()
    if found is None:
        log.warning(
            "config: host_for_browser is %r and this machine has no LAN "
            "address — falling back to localhost, so the projected QR and "
            "the Live tab will only work on this machine", configured)
        return "localhost"
    log.info("config: host_for_browser %r resolved to %s", configured, found)
    return found

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

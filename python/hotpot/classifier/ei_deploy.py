"""classifier/ei_deploy.py -- turns a downloaded Edge Impulse "C++ library"
export into a rebuilt `tools/eim_cpp/build/classify[.exe]`, so pressing the
staff view's Download button (doc section 19.5, `core/main.py`'s
`_handle_ei_download`) is the whole redeploy, not half of it.

Before this module existed, `_handle_ei_download` stopped at writing
`models/<project>.zip` -- unzipping it over `tools/eim_cpp/vendor/` and
rebuilding was a manual step (models/README.md's "Re-download" section, and
the note under the 2026-08-24 / project 1095598 entry: "Not yet unzipped
... not yet rebuilt, so nothing in the running app uses it yet"). That gap
is exactly why a 99.69%-validation-accuracy model can sit downloaded on
disk while the live app keeps classifying with whatever old model
`tools/eim_cpp/build/classify.exe` was last compiled against -- Studio
accuracy and live accuracy silently diverge with no error anywhere.

`unzip_over_vendor()` wipes `vendor/` entirely rather than extracting over
it: the zip's `tflite-model/*.cpp` filename is
`tflite_learn_<project>_<n>_compiled.cpp` (models/README.md's provenance
log has two different names for two different projects), and
`tools/eim_cpp/CMakeLists.txt` globs that directory for `*.cpp` -- an
in-place extract would leave the PREVIOUS model's compiled source sitting
next to the new one, both picked up by the glob, both linked into
`classify`, and `run_classifier()` would use whichever the SDK's own build
picks first. Wipe-then-extract makes "vendor/ mirrors the last downloaded
zip, exactly" an invariant instead of something that happens to be true if
every past deploy's files also happened to get overwritten.

`rebuild()` shells out to the sibling `rebuild.bat` (see that file's own
top comment for why a batch script and not a direct cmake subprocess call:
vcvars64.bat's environment only applies within the process that sourced
it, so vcvars and the build must run in one cmd.exe invocation).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable, Optional

REBUILD_TIMEOUT_S = 300


class EiDeployError(RuntimeError):
    """Unzipping or rebuilding failed. Always carries a message meant to be
    shown to the operator as-is (the staff view's `ei_download_result`),
    same convention as `ei_client.EIClientError`.
    """


def unzip_over_vendor(zip_bytes: bytes, vendor_dir: Path) -> None:
    """Replace `vendor_dir` with exactly what `zip_bytes` (an Edge Impulse
    "C++ library" deployment) contains -- see this module's docstring for
    why a wipe, not an in-place extract.
    """
    import io

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        # Zip-slip guard: an EI export is not attacker-controlled today
        # (it's our own account's build artifact over an authenticated
        # API), but "downloaded bytes get extracted to a fixed path" is
        # exactly the shape that guard exists for, so it costs nothing to
        # check rather than assume.
        for name in names:
            member_path = (vendor_dir / name).resolve()
            if vendor_dir.resolve() not in member_path.parents and member_path != vendor_dir.resolve():
                raise EiDeployError(
                    f"refusing to unzip: {name!r} would extract outside "
                    f"{vendor_dir}")
        if vendor_dir.exists():
            shutil.rmtree(vendor_dir)
        vendor_dir.mkdir(parents=True, exist_ok=True)
        zf.extractall(vendor_dir)


def rebuild(eim_cpp_dir: Path, *,
            on_output: Optional[Callable[[str], None]] = None) -> None:
    """Run `eim_cpp_dir/rebuild.bat` (Windows only -- the ODYSSEY's gcc
    cross-build, doc section 1.4, is a separate by-hand flow that this
    staff-view panel does not drive). Raises `EiDeployError` with the
    script's own stdout/stderr on any non-zero exit, since a build failure
    here means `classify.exe` is stale or missing and every subsequent
    live classification would be silently wrong or erroring -- the caller
    must not treat this as a soft failure.
    """
    if platform.system() != "Windows":
        raise EiDeployError(
            "ei_deploy.rebuild() only knows how to drive the MSVC build "
            "(tools/eim_cpp/rebuild.bat) -- this process is not on "
            "Windows")

    script = eim_cpp_dir / "rebuild.bat"
    if not script.exists():
        raise EiDeployError(f"{script} does not exist")

    try:
        proc = subprocess.run(
            [str(script)], cwd=str(eim_cpp_dir),
            capture_output=True, text=True, timeout=REBUILD_TIMEOUT_S,
            check=False)
    except subprocess.TimeoutExpired as e:
        raise EiDeployError(
            f"rebuild.bat took longer than {REBUILD_TIMEOUT_S}s") from e

    if on_output is not None:
        if proc.stdout:
            on_output(proc.stdout)
        if proc.stderr:
            on_output(proc.stderr)

    if proc.returncode != 0:
        tail = (proc.stdout or "") + (proc.stderr or "")
        raise EiDeployError(
            f"rebuild.bat exited {proc.returncode}:\n{tail[-2000:]}")

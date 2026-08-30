#!/usr/bin/env python3
"""tools/export_edgeimpulse.py — doc section 12.7's "Export for Edge
Impulse", producing a folder-per-label tree ready for
`edge-impulse-uploader` (doc section 21, M4 build item 7).

Run from the repo root:

    python tools/export_edgeimpulse.py                    # datasets/export_ei
    python tools/export_edgeimpulse.py --dry-run
    python tools/export_edgeimpulse.py --out /tmp/upload --min-per-class 150

Then, per doc section 19.1's workflow:

    edge-impulse-uploader --category split datasets/export_ei/<label>/*.jpg

Why this exists at all when the captures are *already* one folder per
label
----------------------------------------------------------------------
Three reasons, and none of them is "rearrange the tree":

1. The sidecars must not be uploaded. `classifier/main.py` writes a
   `.json` beside every crop carrying the bin index, the rect, and the
   lighting the image was taken under (doc section 12.7). That file is
   the dataset's provenance and it is not training data; pointing the
   uploader at `datasets/captures/<label>/*` would send both.
2. Filenames have to survive being flattened. Two crops from
   different bins in the same millisecond are `<ms>_bin0.jpg` and
   `<ms>_bin6.jpg` — distinct, but only because of the bin suffix, and
   only within their own label folder. The export prefixes the label so
   a name is unique across the whole upload.
3. Somebody has to say how thin the thin classes are. Doc section
   19.2 asks for ">=150 images per class across >=4 sessions"; the whole
   point of doc section 12.7's session counter is that an operator can
   see they have 40 mushrooms and 6 prawns and go and collect more
   prawns. This prints that table, and says so loudly rather than
   exporting a lopsided set in silence.

It copies from `captures/`; it never moves or deletes there.
`datasets/captures/` is the only copy of hours of rig time, and an
export is a thing people re-run. `datasets/export_ei/`, the
destination, is the opposite: freely reproducible from `captures/`, so
each run wipes and rebuilds it — a capture deleted or renamed since the
last export must not leave a stale copy behind to be re-uploaded.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# tools/export_edgeimpulse.py -> repo root
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "datasets" / "captures"
DEFAULT_OUT = ROOT / "datasets" / "export_ei"

# Doc section 19.2: "Target >=150 images per class across >=4 sessions on
# different days." Only ever a warning — an export of a half-collected
# set is a legitimate thing to want mid-collection.
DEFAULT_MIN_PER_CLASS = 150

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


class ExportResult:
    def __init__(self) -> None:
        self.per_label: Dict[str, int] = {}
        self.copied: List[Path] = []
        self.skipped_sidecars = 0
        self.thin: List[Tuple[str, int]] = []
        # None if `out` didn't exist yet (nothing to wipe); otherwise the
        # count of files the previous export left behind, wiped before
        # this run started copying. main() logs it so a wipe is never
        # silent — see export()'s own wipe-first comment for why it
        # happens at all.
        self.wiped_files: Optional[int] = None

    @property
    def total(self) -> int:
        return sum(self.per_label.values())


def export(src: Path = DEFAULT_SRC, out: Path = DEFAULT_OUT, *,
           dry_run: bool = False,
           min_per_class: int = DEFAULT_MIN_PER_CLASS) -> ExportResult:
    """Copy every capture into `out/<label>/<label>.<original-name>`.

    Returns the counts rather than printing them, so this is callable
    from a test and from `main()` without the two disagreeing about what
    happened.
    """
    src = Path(src)
    out = Path(out)
    result = ExportResult()
    if not src.is_dir():
        raise FileNotFoundError(
            f"{src} does not exist — nothing has been captured yet "
            "(doc section 12.7's Capture tab writes it)")

    # Wipe first so `out` is always a clean mirror of `src` — otherwise a
    # capture deleted or renamed after a previous export leaves its stale
    # copy behind forever, silently re-uploaded on every future run.
    if not dry_run and out.exists():
        result.wiped_files = sum(1 for p in out.rglob("*") if p.is_file())
        shutil.rmtree(out)

    for label_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        label = label_dir.name
        images = sorted(p for p in label_dir.iterdir()
                        if p.suffix.lower() in IMAGE_SUFFIXES)
        result.skipped_sidecars += sum(
            1 for p in label_dir.iterdir() if p.suffix.lower() == ".json")
        if not images:
            continue
        dest_dir = out / label
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
        for img in images:
            # `<label>.<name>` so a name is unique across the whole
            # upload, not merely within its own folder — see the module
            # docstring's point 2.
            dest = dest_dir / f"{label}.{img.name}"
            if not dry_run:
                shutil.copy2(img, dest)
            result.copied.append(dest)
        result.per_label[label] = len(images)
        if len(images) < min_per_class:
            result.thin.append((label, len(images)))

    return result


def sessions_per_label(src: Path = DEFAULT_SRC) -> Dict[str, int]:
    """How many distinct capture *days* each label has, read out of the
    sidecars' timestamps.

    Doc section 19.2 asks for ">=4 sessions on different days", and that
    is a different question from the image count: 600 photographs of one
    tray under one arrangement of the light is one session's worth of
    information, however many files it is. Reported separately for
    exactly that reason.

    A label with no sidecars reports 0 rather than raising — old captures
    from before the sidecar existed are still usable images.
    """
    import datetime

    out: Dict[str, int] = {}
    src = Path(src)
    if not src.is_dir():
        return out
    for label_dir in sorted(p for p in src.iterdir() if p.is_dir()):
        days = set()
        for side in label_dir.glob("*.json"):
            try:
                data = json.loads(side.read_text(encoding="utf-8"))
                ts = float(data.get("ts", 0.0))
            except (ValueError, OSError, TypeError):
                continue
            if ts > 0:
                days.add(datetime.date.fromtimestamp(ts).isoformat())
        out[label_dir.name] = len(days)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true",
                    help="count and report, copy nothing")
    ap.add_argument("--min-per-class", type=int, default=DEFAULT_MIN_PER_CLASS,
                    help=f"warn below this (doc 19.2: {DEFAULT_MIN_PER_CLASS})")
    args = ap.parse_args(argv)

    try:
        result = export(args.src, args.out, dry_run=args.dry_run,
                        min_per_class=args.min_per_class)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if result.wiped_files is not None:
        print(f"Removed {result.wiped_files} file(s) from the previous "
              f"export at {args.out} before rebuilding it\n")

    sessions = sessions_per_label(args.src)
    print(f"{'label':<20}{'images':>8}{'days':>7}")
    print("-" * 35)
    for label in sorted(result.per_label):
        print(f"{label:<20}{result.per_label[label]:>8}"
              f"{sessions.get(label, 0):>7}")
    print("-" * 35)
    print(f"{'total':<20}{result.total:>8}")
    if result.skipped_sidecars:
        print(f"\n{result.skipped_sidecars} sidecar .json files left behind "
              "(provenance, not training data)")

    if result.thin:
        print(f"\nThin classes — doc 19.2 wants {args.min_per_class}+ each:")
        for label, n in result.thin:
            print(f"  {label}: {n}")
    thin_days = [(k, v) for k, v in sessions.items() if 0 < v < 4]
    if thin_days:
        print("\nClasses captured on fewer than 4 different days "
              "(doc 19.2). More photographs of the same session are not "
              "more information:")
        for label, days in sorted(thin_days):
            print(f"  {label}: {days}")

    if args.dry_run:
        print("\n(dry run — nothing was copied)")
    else:
        print(f"\n{len(result.copied)} images -> {args.out}")
        print(f"Upload with:  edge-impulse-uploader --category split "
              f"{args.out}/<label>/*.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

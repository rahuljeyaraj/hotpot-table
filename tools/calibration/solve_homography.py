#!/usr/bin/env python3
"""Solve the projector-pixel <-> camera-pixel homography from the nine projected
calibration dots.

Run the oF app, press 'c' to show the calibration pattern, then run this.

The output matrix H maps CAMERA pixels -> PROJECTOR pixels, on the raw
(unrotated) camera frame. That is the direction the app needs: a hand found in
the camera image becomes a projector coordinate to draw a halo at.

Camera orientation is not assumed. Detection always runs on the raw frame; the
four 90-degree rotations are only hypotheses about which detected dot is which.

Orientation is settled by the marker dot, not by reprojection error. The nine
centres are evenly spaced on both axes, so the pattern is symmetric under a
180-degree rotation and the flipped homography reprojects exactly as well - by
construction, not by coincidence. Neither the error nor the annotated overlay
can break that tie. ofApp therefore draws dot 0, the top-left target, at radius
30 against 20 for the other eight, and the hypothesis that puts the largest blob
at index 0 is the right one.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

# --- locked table geometry -------------------------------------------------
# Mirrors src/TableGeometry.h and the dot centres in src/ofApp.cpp. Kept in mm
# here for the same reason the C++ does: a projector or table change is a
# one-place edit, and no projector pixel value is written down twice.
TABLE_W_MM = 1524.0
TABLE_H_MM = 914.4
PROJ_W_PX = 1920
PROJ_H_PX = 1080

CALIB_X_MM = (44.0, 762.0, 1480.0)
CALIB_Y_MM = (86.0, 457.0, 828.0)

SCALE_X = PROJ_W_PX / TABLE_W_MM
SCALE_Y = PROJ_H_PX / TABLE_H_MM

EXPECTED_DOTS = 9

HERE = Path(__file__).resolve().parent


class CalibrationError(RuntimeError):
    """Anything that means the operator has to go and fix something."""


# --- 1. capture ------------------------------------------------------------

def open_camera(camera_index, backend_name, width, height):
    backends = {
        "msmf": cv2.CAP_MSMF,
        "dshow": cv2.CAP_DSHOW,
        "any": cv2.CAP_ANY,
    }
    cap = cv2.VideoCapture(camera_index, backends[backend_name])
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    return cap


def grab_frame(args):
    """Open the USB camera and return one settled, noise-averaged frame.

    Backend choice is not cosmetic on Windows. Through DSHOW this camera runs
    auto-exposure that ignores CAP_PROP_EXPOSURE entirely and clips the white
    table top to 255, which buries the dots (measured: board 249, dot 253).
    MSMF holds a fixed exposure and the same dots come back with real contrast.
    """
    cap = open_camera(args.camera, args.backend, args.width, args.height)
    if cap is None:
        raise CalibrationError(
            f"could not open camera index {args.camera} "
            f"with the {args.backend} backend"
        )

    try:
        # The first frames out of a UVC camera are stale or half-exposed.
        for _ in range(max(1, args.warmup)):
            cap.read()

        # Dot-over-board contrast here is only ~25-50 grey levels, which is the
        # same order as this sensor's frame-to-frame noise. Averaging is what
        # makes the outer dots separable at all; nothing is warped or shifted,
        # so the result is still a raw camera frame.
        acc = None
        n = 0
        for _ in range(max(1, args.average)):
            ok, f = cap.read()
            if not ok:
                continue
            acc = f.astype(np.float32) if acc is None else acc + f.astype(np.float32)
            n += 1
        if acc is None:
            raise CalibrationError("camera opened but returned no frames")

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if (actual_w, actual_h) != (args.width, args.height):
            print(
                f"WARNING: asked for {args.width}x{args.height}, camera gave "
                f"{actual_w}x{actual_h}"
            )
        print(f"averaged {n} frames from the {args.backend} backend")
        return (acc / n).astype(np.uint8)
    finally:
        cap.release()


# --- 2. detection ----------------------------------------------------------

def preprocess(frame, tophat_size):
    """Grey, background-flattened image in which a dot is a local bright spot.

    The projector does not light the table evenly and the camera sees it at an
    angle, so the board itself runs from ~29 to ~58 grey across the frame while
    a dot only sits ~25-50 above whatever is under it. A single global threshold
    therefore cannot separate all nine. A white top-hat subtracts anything
    larger than the structuring element, which removes the board gradient and
    leaves the dots standing on a flat floor.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if tophat_size <= 0:
        return gray
    size = tophat_size | 1  # cv2 wants an odd kernel
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, kernel)


def find_blobs(prepared, threshold, args):
    """Centroids and contour areas of every small round bright blob at one
    threshold. Areas are what identify the oversized marker dot later."""
    h, w = prepared.shape
    _, binary = cv2.threshold(prepared, threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    centroids = []
    areas = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < args.min_area or area > args.max_area:
            continue

        perimeter = cv2.arcLength(c, True)
        if perimeter <= 0:
            continue

        # 1.0 for a perfect circle. A projected dot smeared by defocus stays
        # round; a reflection on a tray rim or a strip of window glare does not.
        circularity = 4.0 * np.pi * area / (perimeter * perimeter)
        if circularity < args.min_circularity:
            continue

        x, y, bw, bh = cv2.boundingRect(c)
        if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
            continue  # a calibration dot is never clipped by the frame edge

        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        centroids.append((m["m10"] / m["m00"], m["m01"] / m["m00"]))
        areas.append(area)

    return (np.array(centroids, dtype=np.float64).reshape(-1, 2),
            np.array(areas, dtype=np.float64))


def candidate_sets(prepared, args):
    """Every threshold level that yields exactly nine round blobs.

    More than one level can, and does, produce nine: on this rig a bright window
    reflection off the left edge of frame is round enough to pass, so at one
    threshold the "nine" is eight real dots plus the window. Rather than guess a
    level, every nine-blob set is handed to the solver and the one that actually
    fits a homography wins. A stray blob cannot fake a projective 3x3 grid.
    """
    if args.threshold is not None:
        levels = [args.threshold]
    else:
        levels = list(range(
            args.threshold_max, args.threshold_min - 1, -args.threshold_step
        ))

    sets = []
    attempts = []
    for t in levels:
        pts, areas = find_blobs(prepared, t, args)
        attempts.append((t, len(pts)))
        if len(pts) == EXPECTED_DOTS:
            sets.append((t, pts, areas))

    if not sets:
        counts = ", ".join(f"t={t}:{n}" for t, n in attempts)
        raise CalibrationError(
            f"expected exactly {EXPECTED_DOTS} dots, no threshold found that "
            f"many.\n"
            f"  blob counts per threshold: {counts}\n"
            f"  check: is the oF app in calibration mode (press 'c')?\n"
            f"  check: is the whole table in frame?\n"
            f"  check: does capture_raw.png show nine separated dots?\n"
            f"  tune with --threshold / --min-area / --max-area / "
            f"--min-circularity / --tophat"
        )

    print(f"{len(sets)} threshold level(s) yielded exactly {EXPECTED_DOTS} "
          f"blobs: {[t for t, _, _ in sets]}")
    return sets


# --- 3. projector targets --------------------------------------------------

def projector_targets():
    """The nine dot centres in projector pixels, row-major, top row first."""
    pts = []
    for mm_y in CALIB_Y_MM:
        for mm_x in CALIB_X_MM:
            pts.append((mm_x * SCALE_X, mm_y * SCALE_Y))
    return np.array(pts, dtype=np.float64)


# --- 4. correspondence, fixed by the marker dot ----------------------------

def identify_marker(areas, min_ratio):
    """Index of the oversized marker dot among the nine blobs.

    ofApp draws dot 0 at radius 30 and the rest at radius 20, so the marker
    should come back at roughly (30/20)^2 = 2.25x the area of its neighbours.
    The ratio is checked against the median of the other eight rather than the
    mean, so one fat or thin blob cannot drag the baseline.
    """
    marker = int(np.argmax(areas))
    others = np.delete(areas, marker)
    median_other = float(np.median(others))

    if median_other <= 0:
        raise CalibrationError("the eight non-marker blobs have no area")

    ratio = areas[marker] / median_other
    if ratio < min_ratio:
        raise CalibrationError(
            f"no dot stands out as the marker: largest blob is "
            f"{areas[marker]:.0f} px2, median of the other eight is "
            f"{median_other:.0f} px2, ratio {ratio:.2f} < {min_ratio}.\n"
            f"  all areas: {np.sort(areas)[::-1].round(0).tolist()}\n"
            f"  check: is the oF app the build that draws dot 0 at radius 30?\n"
            f"  check: is the marker dot overexposed and blooming into the\n"
            f"         others, or clipped by a tray cutout?\n"
            f"  orientation cannot be resolved without it - see --min-marker-ratio"
        )

    return marker, float(areas[marker]), median_other


def rotate_points(pts, quarter_turns):
    """Rotate a point set about the origin by k * 90 degrees.

    Only ever used to decide ordering. The absolute position after rotation is
    irrelevant, so no centring or translation is needed.
    """
    x, y = pts[:, 0], pts[:, 1]
    if quarter_turns == 0:
        out = (x, y)
    elif quarter_turns == 1:
        out = (-y, x)
    elif quarter_turns == 2:
        out = (-x, -y)
    elif quarter_turns == 3:
        out = (y, -x)
    else:
        raise ValueError(quarter_turns)
    return np.column_stack(out)


def row_major_order(pts):
    """Indices that put a 3x3 grid in reading order: top row left-to-right."""
    by_y = np.argsort(pts[:, 1], kind="stable")
    order = []
    for row_start in (0, 3, 6):
        row = by_y[row_start:row_start + 3]
        order.extend(row[np.argsort(pts[row, 0], kind="stable")])
    return np.array(order, dtype=int)


def reprojection_error(H, camera_pts, target_pts):
    """Per-point distance in projector pixels after mapping camera -> projector."""
    mapped = cv2.perspectiveTransform(
        camera_pts.reshape(-1, 1, 2), H
    ).reshape(-1, 2)
    return np.linalg.norm(mapped - target_pts, axis=1)


def solve_best(sets, targets, args):
    """Fit each candidate set at the one orientation its marker dot allows.

    Orientation is decided geometrically, not by error. Projector dot 0 is the
    top-left target and is drawn oversized, so the correct hypothesis is the one
    whose row-major sort puts the marker at index 0. Exactly one of the four
    rotations can do that for a given set, since each puts a different corner
    first - which is what makes this immune to the 180-degree symmetry of the
    grid. Error still chooses between thresholds, so a set containing a stray
    blob is still rejected by how badly it fits.
    """
    best = None
    for threshold, detected, areas in sets:
        marker, marker_area, median_other = identify_marker(
            areas, args.min_marker_ratio
        )

        for k in range(4):
            # The rotation decides the ORDER only. The homography is always
            # fitted against the raw, unrotated camera coordinates, so H
            # consumes frames exactly as the camera delivers them.
            order = row_major_order(rotate_points(detected, k))
            if order[0] != marker:
                # marker did not land top-left, so this orientation is wrong
                continue
            ordered = detected[order]

            H, mask = cv2.findHomography(
                ordered, targets, cv2.RANSAC, args.ransac_thresh
            )
            if H is None:
                print(f"  t={threshold:3d}  rotation {k * 90:3d} deg: "
                      f"marker at index 0, but no homography found")
                continue

            errors = reprojection_error(H, ordered, targets)
            inliers = int(mask.sum()) if mask is not None else EXPECTED_DOTS
            print(
                f"  t={threshold:3d}  rotation {k * 90:3d} deg: "
                f"mean {errors.mean():9.2f} px  max {errors.max():9.2f} px  "
                f"inliers {inliers}/{EXPECTED_DOTS}  "
                f"marker {marker_area:.0f} px2 vs median {median_other:.0f} px2"
            )

            if best is None or errors.mean() < best["errors"].mean():
                best = {
                    "quarter_turns": k,
                    "threshold": threshold,
                    "H": H,
                    "detected": detected,
                    "ordered": ordered,
                    "errors": errors,
                    "inliers": inliers,
                    "marker_area": marker_area,
                    "median_other_area": median_other,
                }

    if best is None:
        raise CalibrationError(
            "no orientation put the marker dot top-left with a solvable "
            "homography - the detected blobs are probably not the nine "
            "calibration dots"
        )
    return best


# --- output ----------------------------------------------------------------

def annotate(frame, detected, targets, H):
    """Green circles on what was detected, red crosses on where the projector
    targets land when pulled back into camera space. They should coincide."""
    out = frame.copy()

    back = cv2.perspectiveTransform(
        targets.reshape(-1, 1, 2), np.linalg.inv(H)
    ).reshape(-1, 2)

    for (x, y) in detected:
        cv2.circle(out, (int(round(x)), int(round(y))), 18, (0, 255, 0), 2)

    for i, (x, y) in enumerate(back):
        x, y = int(round(x)), int(round(y))
        cv2.line(out, (x - 14, y), (x + 14, y), (0, 0, 255), 2)
        cv2.line(out, (x, y - 14), (x, y + 14), (0, 0, 255), 2)
        cv2.putText(
            out, str(i), (x + 18, y - 18),
            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2
        )

    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--camera", type=int, default=0, help="camera index")
    p.add_argument("--backend", choices=("msmf", "dshow", "any"),
                   default="msmf" if sys.platform == "win32" else "any")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--warmup", type=int, default=20,
                   help="frames to discard before capture")
    p.add_argument("--average", type=int, default=40,
                   help="frames to average; 1 for a single raw frame")
    p.add_argument("--image", type=Path,
                   help="skip the camera and solve against an existing frame")
    p.add_argument("--tophat", type=int, default=61,
                   help="background-flattening kernel in px; 0 disables")
    p.add_argument("--threshold", type=int,
                   help="fixed binary threshold; default is a sweep")
    p.add_argument("--threshold-min", type=int, default=8)
    p.add_argument("--threshold-max", type=int, default=200)
    p.add_argument("--threshold-step", type=int, default=4)
    p.add_argument("--min-area", type=float, default=40.0)
    p.add_argument("--max-area", type=float, default=20000.0)
    p.add_argument("--min-circularity", type=float, default=0.65)
    p.add_argument("--min-marker-ratio", type=float, default=1.6,
                   help="how much bigger the marker dot must be than the "
                        "median of the other eight; nominal is 2.25")
    p.add_argument("--ransac-thresh", type=float, default=5.0,
                   help="RANSAC inlier threshold in projector px")
    p.add_argument("--out-dir", type=Path, default=HERE)
    args = p.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.out_dir / "capture_raw.png"
    annotated_path = args.out_dir / "capture_annotated.png"
    json_path = args.out_dir / "homography.json"

    # 1. capture
    if args.image:
        frame = cv2.imread(str(args.image))
        if frame is None:
            raise CalibrationError(f"could not read {args.image}")
        print(f"using existing frame {args.image}")
    else:
        frame = grab_frame(args)
    h, w = frame.shape[:2]
    cv2.imwrite(str(raw_path), frame)
    print(f"captured {w}x{h} -> {raw_path}")

    # 2. detect on the RAW frame
    prepared = preprocess(frame, args.tophat)
    sets = candidate_sets(prepared, args)

    # 3. projector-pixel targets
    targets = projector_targets()

    # 4. orientation from the marker dot, threshold from the fit
    print("searching candidate sets, marker dot fixes the orientation:")
    best = solve_best(sets, targets, args)

    rotation_deg = best["quarter_turns"] * 90
    H = best["H"]
    errors = best["errors"]
    ratio = best["marker_area"] / best["median_other_area"]

    # 5. report
    print()
    print(f"chosen rotation  : {rotation_deg} deg")
    print(f"mean error       : {errors.mean():.2f} px")
    print(f"max error        : {errors.max():.2f} px")
    print(f"marker blob area : {best['marker_area']:.0f} px2")
    print(f"median other 8   : {best['median_other_area']:.0f} px2  "
          f"(ratio {ratio:.2f}, nominal 2.25)")
    print("homography (camera px -> projector px):")
    for row in H:
        print("  [{:14.6f} {:14.6f} {:14.6f}]".format(*row))

    # 6. persist
    payload = {
        "description": "maps raw camera pixels to projector pixels",
        "matrix": H.tolist(),
        "rotation_deg": rotation_deg,
        "rotation_source": "marker dot at projector target 0",
        "errors_px": {
            "mean": float(errors.mean()),
            "max": float(errors.max()),
            "per_point": [float(e) for e in errors],
        },
        "capture_resolution": {"width": int(w), "height": int(h)},
        "detection": {
            "backend": args.backend,
            "frames_averaged": int(args.average),
            "tophat": int(args.tophat),
            "threshold": int(best["threshold"]),
            "inliers": best["inliers"],
            "marker_area_px2": best["marker_area"],
            "median_other_area_px2": best["median_other_area"],
            "marker_area_ratio": ratio,
            "camera_points": best["ordered"].tolist(),
            "projector_points": targets.tolist(),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {json_path}")

    # 7. eyeball check
    cv2.imwrite(
        str(annotated_path), annotate(frame, best["detected"], targets, H)
    )
    print(f"wrote {annotated_path}")


if __name__ == "__main__":
    try:
        main()
    except CalibrationError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        sys.exit(1)

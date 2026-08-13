"""Tests for tools/export_edgeimpulse.py — M4 build item 7, doc sections
12.7 and 19.2.

Run from the repo root:

    python -m unittest discover -s python/tests -v

Everything runs against a throwaway capture tree, never the repo's own
`datasets/captures/` — which on a real rig is the only copy of hours of
tray-swapping and photographing.
"""

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_TOOL = (Path(__file__).resolve().parents[2] / "tools"
         / "export_edgeimpulse.py")
_spec = importlib.util.spec_from_file_location("export_edgeimpulse", _TOOL)
export_ei = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_ei)


class ExportCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.src = Path(self.dir.name) / "captures"
        self.out = Path(self.dir.name) / "export_ei"

    def capture(self, label, n=1, bins=(0,), day_offset=0, sidecar=True):
        d = self.src / label
        d.mkdir(parents=True, exist_ok=True)
        ts = time.time() - day_offset * 86400
        for k in range(n):
            for b in bins:
                stamp = int((ts + k) * 1000)
                (d / f"{stamp}_bin{b}.jpg").write_bytes(b"\xff\xd8fake-jpeg")
                if sidecar:
                    (d / f"{stamp}_bin{b}.json").write_text(
                        json.dumps({"bin": b, "label": label, "ts": ts + k,
                                    "rect_cam": [0, 0, 10, 10],
                                    "lighting": {"field_level": 1.0}}),
                        encoding="utf-8")


class TestTheTree(ExportCase):

    def test_a_folder_per_label_comes_out(self):
        self.capture("mushroom", n=3)
        self.capture("tofu", n=2)
        result = export_ei.export(self.src, self.out)
        self.assertEqual(result.per_label, {"mushroom": 3, "tofu": 2})
        self.assertTrue((self.out / "mushroom").is_dir())
        self.assertEqual(len(list((self.out / "tofu").glob("*.jpg"))), 2)

    def test_sidecars_are_left_behind(self):
        # THE reason this tool exists rather than pointing the uploader
        # straight at datasets/captures. A sidecar is the dataset's
        # provenance, not training data.
        self.capture("mushroom", n=2)
        result = export_ei.export(self.src, self.out)
        self.assertEqual(len(list((self.out / "mushroom").iterdir())), 2)
        self.assertEqual(list((self.out / "mushroom").glob("*.json")), [])
        self.assertEqual(result.skipped_sidecars, 2)

    def test_names_are_prefixed_so_they_survive_being_flattened(self):
        self.capture("mushroom", n=1)
        export_ei.export(self.src, self.out)
        name = next((self.out / "mushroom").glob("*.jpg")).name
        self.assertTrue(name.startswith("mushroom."), name)

    def test_two_bins_captured_in_the_same_millisecond_stay_distinct(self):
        # `<ms>_bin0.jpg` and `<ms>_bin6.jpg` differ only by the bin
        # suffix, which is exactly why the suffix is in the name.
        self.capture("mushroom", n=1, bins=(0, 6))
        export_ei.export(self.src, self.out)
        names = {p.name for p in (self.out / "mushroom").glob("*.jpg")}
        self.assertEqual(len(names), 2)

    def test_the_source_is_never_touched(self):
        # datasets/captures is the only copy of hours of rig time, and an
        # export is a thing people re-run.
        self.capture("mushroom", n=3)
        before = sorted(p.name for p in (self.src / "mushroom").iterdir())
        export_ei.export(self.src, self.out)
        after = sorted(p.name for p in (self.src / "mushroom").iterdir())
        self.assertEqual(before, after)

    def test_running_twice_is_harmless(self):
        self.capture("mushroom", n=2)
        export_ei.export(self.src, self.out)
        export_ei.export(self.src, self.out)
        self.assertEqual(len(list((self.out / "mushroom").glob("*.jpg"))), 2)

    def test_a_stale_export_is_not_left_behind(self):
        # A capture later deleted from `captures/` must not leave its old
        # export copy sitting in `export_ei/` forever, re-uploaded on
        # every future run.
        self.capture("mushroom", n=2)
        self.capture("prawn", n=2)
        first = export_ei.export(self.src, self.out)
        self.assertIsNone(first.wiped_files)  # nothing to wipe yet
        self.assertTrue((self.out / "prawn").exists())

        for f in (self.src / "prawn").iterdir():
            f.unlink()
        (self.src / "prawn").rmdir()
        second = export_ei.export(self.src, self.out)

        self.assertFalse((self.out / "prawn").exists())
        self.assertEqual(len(list((self.out / "mushroom").glob("*.jpg"))), 2)
        self.assertEqual(second.wiped_files, 4)  # 2 mushroom + 2 prawn

    def test_a_dry_run_copies_nothing_but_still_counts(self):
        self.capture("mushroom", n=4)
        result = export_ei.export(self.src, self.out, dry_run=True)
        self.assertEqual(result.per_label["mushroom"], 4)
        self.assertFalse(self.out.exists())

    def test_a_label_folder_with_only_sidecars_is_skipped(self):
        (self.src / "ghost").mkdir(parents=True)
        (self.src / "ghost" / "x.json").write_text("{}", encoding="utf-8")
        result = export_ei.export(self.src, self.out)
        self.assertNotIn("ghost", result.per_label)

    def test_a_missing_capture_tree_says_so_rather_than_exporting_nothing(self):
        with self.assertRaises(FileNotFoundError):
            export_ei.export(self.src / "nope", self.out)


class TestReporting(ExportCase):
    """Doc section 12.7's session counter, doc section 19.2's targets."""

    def test_thin_classes_are_named(self):
        self.capture("mushroom", n=3)
        self.capture("prawn", n=1)
        result = export_ei.export(self.src, self.out, min_per_class=2)
        self.assertEqual(result.thin, [("prawn", 1)])

    def test_a_full_class_is_not_named(self):
        self.capture("mushroom", n=5)
        result = export_ei.export(self.src, self.out, min_per_class=2)
        self.assertEqual(result.thin, [])

    def test_days_are_counted_not_images(self):
        # Doc section 19.2 asks for ">=4 sessions on different days", and
        # that is a different question from the image count: 600 photos of
        # one tray under one arrangement of the light is one session's
        # worth of information however many files it is.
        self.capture("mushroom", n=50, day_offset=0)
        self.capture("mushroom", n=50, day_offset=3)
        days = export_ei.sessions_per_label(self.src)
        self.assertEqual(days["mushroom"], 2)

    def test_captures_with_no_sidecar_report_zero_days_not_a_crash(self):
        self.capture("legacy", n=2, sidecar=False)
        self.assertEqual(export_ei.sessions_per_label(self.src)["legacy"], 0)

    def test_a_corrupt_sidecar_does_not_stop_the_count(self):
        self.capture("mushroom", n=1)
        (self.src / "mushroom" / "broken.json").write_text("{oh no",
                                                           encoding="utf-8")
        self.assertEqual(export_ei.sessions_per_label(self.src)["mushroom"], 1)

    def test_main_runs_end_to_end_and_reports(self):
        self.capture("mushroom", n=2)
        rc = export_ei.main(["--src", str(self.src), "--out", str(self.out),
                             "--min-per-class", "1"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.out / "mushroom").is_dir())

    def test_main_on_a_missing_tree_exits_non_zero(self):
        self.assertEqual(
            export_ei.main(["--src", str(self.src / "nope")]), 1)


if __name__ == "__main__":
    unittest.main()

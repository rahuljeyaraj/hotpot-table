"""Tests for common/atomicio.py.

Run from the repo root:

    python -m unittest discover -s python/tests -v

The point of this module is what happens when a write does *not* finish, so
most of these tests break it on purpose: an object that cannot be serialised,
an os.replace that raises. The thing being asserted in both cases is the same
and it is the whole contract — the previous contents of the file are still
there and still parse.

Those two tests are also the ones that fail against the obvious wrong
implementation. `json.dump(obj, open(path, "w"))` passes every round-trip
test in this file and destroys the destination in both of them.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hotpot.common import atomicio  # noqa: E402


class Unserialisable:
    """json.dumps refuses this, halfway through the object containing it."""


class TempDirCase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "homography.json")

    def listdir(self):
        return sorted(os.listdir(self.dir.name))


# ---------------------------------------------------------------------------
# The ordinary path
# ---------------------------------------------------------------------------

class TestWriteJson(TempDirCase):

    def test_round_trip(self):
        obj = {"schema": 3, "H": [[1.0, 0.0, 2.5], [0.0, 1.0, 0.0]],
               "rms_px": 0.8, "locked": True, "note": None}
        atomicio.write_json(self.path, obj)
        self.assertEqual(atomicio.read_json(self.path), obj)

    def test_keeps_chinese_unescaped(self):
        """Doc section 8.1: bin labels are Chinese. \\u9999 helps nobody."""
        atomicio.write_json(self.path, {"names": {"zh": "香菇"}})
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("香菇", text)
        self.assertNotIn("\\u", text)

    def test_is_human_readable_and_newline_terminated(self):
        atomicio.write_json(self.path, {"a": 1, "b": 2})
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("\n", text.strip())        # indented, not one long line
        self.assertTrue(text.endswith("\n"))

    def test_creates_missing_parent_directories(self):
        """A fresh clone has no state/ — it is gitignored (doc section 8)."""
        deep = os.path.join(self.dir.name, "state", "nested", "bin_map.json")
        atomicio.write_json(deep, {"bins": []})
        self.assertEqual(atomicio.read_json(deep), {"bins": []})

    def test_overwrites_in_place(self):
        atomicio.write_json(self.path, {"v": 1})
        atomicio.write_json(self.path, {"v": 2})
        self.assertEqual(atomicio.read_json(self.path), {"v": 2})

    def test_leaves_no_temp_file_behind(self):
        atomicio.write_json(self.path, {"v": 1})
        self.assertEqual(self.listdir(), ["homography.json"])

    def test_temp_file_is_a_sibling(self):
        """os.replace cannot cross a filesystem, so the temp must be local.

        Asserted by watching the call rather than by racing the write: on
        this box /tmp and the repo are usually the same device, so a temp in
        the system temp dir would pass a functional test and fail on the
        board, where they are not (doc section 1.4 — OS on the SSD).
        """
        seen = []
        real_replace = os.replace

        def spy(src, dst, *a, **kw):
            seen.append((src, dst))
            return real_replace(src, dst, *a, **kw)

        os.replace = spy
        self.addCleanup(setattr, os, "replace", real_replace)
        atomicio.write_json(self.path, {"v": 1})

        self.assertEqual(len(seen), 1)
        src, dst = seen[0]
        self.assertEqual(os.path.dirname(src), os.path.dirname(dst))
        self.assertNotEqual(src, dst)

    def test_write_text_and_bytes(self):
        p = os.path.join(self.dir.name, "note.txt")
        atomicio.write_text(p, "one\ntwo\n")
        with open(p, "rb") as f:
            self.assertEqual(f.read(), b"one\ntwo\n")   # no CRLF translation

        p2 = os.path.join(self.dir.name, "blob.bin")
        atomicio.write_bytes(p2, b"\x00\xff\x01")
        with open(p2, "rb") as f:
            self.assertEqual(f.read(), b"\x00\xff\x01")


# ---------------------------------------------------------------------------
# The path this module exists for
# ---------------------------------------------------------------------------

class TestFailedWrites(TempDirCase):

    def setUp(self):
        super().setUp()
        self.good = {"schema": 3, "rms_px": 0.8}
        atomicio.write_json(self.path, self.good)

    def test_unserialisable_object_leaves_the_old_file_intact(self):
        """The typo version of a power cut, and far more likely than one."""
        with self.assertRaises(TypeError):
            atomicio.write_json(self.path, {"H": Unserialisable()})
        self.assertEqual(atomicio.read_json(self.path), self.good)
        self.assertEqual(self.listdir(), ["homography.json"])

    def test_failure_during_replace_leaves_the_old_file_intact(self):
        real_replace = os.replace

        def boom(src, dst, *a, **kw):
            raise OSError(13, "denied")

        os.replace = boom
        self.addCleanup(setattr, os, "replace", real_replace)

        with self.assertRaises(OSError):
            atomicio.write_json(self.path, {"schema": 4})

        os.replace = real_replace
        self.assertEqual(atomicio.read_json(self.path), self.good)
        self.assertEqual(self.listdir(), ["homography.json"])

    def test_a_stale_temp_file_does_not_block_the_next_write(self):
        """What a crash between the write and the rename leaves behind."""
        with open(self.path + atomicio.TEMP_SUFFIX, "w") as f:
            f.write("{ half a homo")
        atomicio.write_json(self.path, {"schema": 5})
        self.assertEqual(atomicio.read_json(self.path), {"schema": 5})
        self.assertEqual(self.listdir(), ["homography.json"])


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class TestReadJson(TempDirCase):

    def test_missing_returns_the_default(self):
        """First boot with an empty state/ is normal (doc section 9.1)."""
        self.assertIsNone(atomicio.read_json(self.path, None))
        self.assertEqual(atomicio.read_json(self.path, {"bins": []}),
                         {"bins": []})

    def test_missing_without_a_default_raises(self):
        with self.assertRaises(FileNotFoundError):
            atomicio.read_json(self.path)

    def test_corrupt_raises_even_when_a_default_was_given(self):
        """Missing and corrupt are opposite facts (doc section 20.4).

        A default here would turn a truncated calibration into a plausible
        one, and the table would price food off it without a word.
        """
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"schema": 3, "H": [[1.0, 0.0')
        with self.assertRaises(json.JSONDecodeError):
            atomicio.read_json(self.path, {"safe": True})

    def test_empty_file_is_corrupt_not_missing(self):
        open(self.path, "w").close()
        with self.assertRaises(json.JSONDecodeError):
            atomicio.read_json(self.path, None)


# ---------------------------------------------------------------------------
# The journal (doc section 19.3)
# ---------------------------------------------------------------------------

class TestJsonLines(TempDirCase):

    def setUp(self):
        super().setUp()
        self.journal = os.path.join(self.dir.name, "session.jsonl")

    def test_append_and_read_back(self):
        rows = [{"t": "session_start", "mode": "diner"},
                {"t": "snapshot", "total": 41.2},
                {"t": "session_end"}]
        for r in rows:
            atomicio.append_json_line(self.journal, r)
        self.assertEqual(atomicio.read_json_lines(self.journal), rows)

    def test_one_line_per_record(self):
        atomicio.append_json_line(self.journal, {"t": "a", "zh": "香菇"})
        atomicio.append_json_line(self.journal, {"t": "b"})
        with open(self.journal, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(text.count("\n"), 2)
        self.assertIn("香菇", text)

    def test_missing_journal_reads_as_empty(self):
        self.assertEqual(atomicio.read_json_lines(self.journal), [])

    def test_a_torn_final_line_is_discarded(self):
        """Exactly what a kill -9 mid-append leaves (doc section 19.3)."""
        atomicio.append_json_line(self.journal, {"t": "session_start"})
        atomicio.append_json_line(self.journal, {"t": "snapshot", "total": 41.2})
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write('{"t":"snap')
        self.assertEqual(atomicio.read_json_lines(self.journal),
                         [{"t": "session_start"},
                          {"t": "snapshot", "total": 41.2}])

    def test_a_corrupt_middle_line_raises(self):
        """Not a torn write. Something else wrote to the journal."""
        with open(self.journal, "w", encoding="utf-8") as f:
            f.write('{"t":"session_start"}\nnonsense\n{"t":"session_end"}\n')
        with self.assertRaises(json.JSONDecodeError):
            atomicio.read_json_lines(self.journal)


if __name__ == "__main__":
    unittest.main()

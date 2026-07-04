import json
import os
import tempfile
import unittest

import server


class BlobStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        server.DATA_DIR = self.tmp

    def test_write_then_read_roundtrip(self):
        server.write_blob("mybin", {"todo": [1, 2], "done": []})
        self.assertEqual(server.read_blob("mybin"), {"todo": [1, 2], "done": []})

    def test_read_missing_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            server.read_blob("nope")

    def test_write_is_atomic_no_tmp_left(self):
        server.write_blob("mybin", {"a": 1})
        leftovers = [f for f in os.listdir(self.tmp) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_bin_id_regex(self):
        self.assertTrue(server.BIN_ID_RE.match("abc-123_DEF"))
        self.assertFalse(server.BIN_ID_RE.match("../etc"))
        self.assertFalse(server.BIN_ID_RE.match("has/slash"))
        self.assertFalse(server.BIN_ID_RE.match(""))


if __name__ == "__main__":
    unittest.main()

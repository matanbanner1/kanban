import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

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


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        server.DATA_DIR = cls.tmp
        server.MASTER_KEY = "testkey"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def req(self, method, path, body=None, key="testkey", ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {}
        if key is not None:
            headers["X-Master-Key"] = key
        if body is not None:
            headers["Content-Type"] = ctype
        conn.request(method, path, body, headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_put_then_get_roundtrip(self):
        resp, _ = self.req("PUT", "/b/board1", json.dumps({"todo": [1]}))
        self.assertEqual(resp.status, 200)
        resp, data = self.req("GET", "/b/board1/latest")
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(data)["record"], {"todo": [1]})

    def test_get_unknown_bin_404(self):
        resp, _ = self.req("GET", "/b/ghost/latest")
        self.assertEqual(resp.status, 404)

    def test_bad_key_401(self):
        resp, _ = self.req("GET", "/b/board1/latest", key="wrong")
        self.assertEqual(resp.status, 401)

    def test_missing_key_401(self):
        resp, _ = self.req("GET", "/b/board1/latest", key=None)
        self.assertEqual(resp.status, 401)

    def test_path_traversal_id_400(self):
        resp, _ = self.req("PUT", "/b/..%2Fetc", json.dumps({"x": 1}))
        self.assertIn(resp.status, (400, 404))

    def test_invalid_json_400(self):
        resp, _ = self.req("PUT", "/b/board1", "{not json")
        self.assertEqual(resp.status, 400)

    def test_body_too_large_413(self):
        big = json.dumps({"x": "a" * 1_000_001})
        resp, _ = self.req("PUT", "/b/board1", big)
        self.assertEqual(resp.status, 413)

    def test_options_preflight_cors(self):
        resp, _ = self.req("OPTIONS", "/b/board1", key=None)
        self.assertEqual(resp.status, 204)
        self.assertEqual(
            resp.getheader("Access-Control-Allow-Origin"),
            "https://matanbanner1.github.io",
        )
        self.assertIn("X-Master-Key", resp.getheader("Access-Control-Allow-Headers"))

    def test_post_creates_bin(self):
        resp, data = self.req("POST", "/b", json.dumps({"todo": []}))
        self.assertEqual(resp.status, 200)
        new_id = json.loads(data)["metadata"]["id"]
        self.assertTrue(server.BIN_ID_RE.match(new_id))
        resp, data = self.req("GET", f"/b/{new_id}/latest")
        self.assertEqual(json.loads(data)["record"], {"todo": []})

    def test_put_empty_body_stores_empty_object(self):
        resp, data = self.req("PUT", "/b/emptybin", body=None)
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(data), {"metadata": {"id": "emptybin"}})
        self.assertEqual(server.read_blob("emptybin"), {})

    def test_bad_content_length_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        headers = {
            "X-Master-Key": "testkey",
            "Content-Type": "application/json",
            "Content-Length": "not-a-number",
        }
        conn.request("PUT", "/b/board1", "{}", headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(data), {"message": "bad content-length"})


if __name__ == "__main__":
    unittest.main()

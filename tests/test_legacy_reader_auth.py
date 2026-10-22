"""C6/C7/C8/C11: localhost API against synthetic archive and credentials."""
import http.client
import json
import threading
import unittest

from support.legacy_env import LegacyEnvironment, backend


class ReaderAuthContract(unittest.TestCase):
    def setUp(self):
        self.env = LegacyEnvironment().__enter__()
        self.env.archived_tweet()
        self.server = backend.ThreadingHTTPServer(("127.0.0.1", 0), backend.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.env.__exit__(None, None, None)

    def request(self, method, path, payload=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        hdrs = dict(headers or {})
        if cookie:
            hdrs["Cookie"] = cookie
        if isinstance(payload, dict):
            payload = json.dumps(payload).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if payload is not None:
            hdrs["Content-Length"] = str(len(payload))
        try:
            conn.request(method, path, body=payload, headers=hdrs)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def login(self):
        status, headers, _ = self.request("POST", "/api/login", {"password": "synthetic-test-password"})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def test_whole_site_auth_session_logout(self):
        self.assertEqual(self.request("GET", "/api/tweets")[0], 401)
        status, headers, _ = self.request("GET", "/media/exampleuser/2001_0.jpg")
        self.assertEqual((status, headers["Location"]), (302, "/login"))
        self.assertEqual(self.request("POST", "/api/login", {"password": "wrong-synthetic"})[0], 401)
        self.login()
        self.assertEqual(self.request("GET", "/api/tweets", cookie=self.cookie)[0], 200)
        self.assertEqual(self.request("POST", "/api/logout", cookie=self.cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/tweets", cookie=self.cookie)[0], 401)

    def test_timeline_detail_media_comments_projection(self):
        self.login()
        _, _, body = self.request("GET", "/api/tweets", cookie=self.cookie)
        result = json.loads(body)
        self.assertEqual(result["total"], 1)
        item = result["tweets"][0]
        self.assertEqual((item["tweet_id"], item["user"], item["media"][0]["file"]),
                         ("2001", "exampleuser", "2001_0.jpg"))
        status, _, body = self.request("GET", "/api/tweet/2001", cookie=self.cookie)
        detail = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(detail["media"][0]["file"], "2001_0.jpg")
        self.assertEqual(detail["comments"]["count"], 1)
        self.assertEqual(detail["comments"]["items"][0]["parent_id"], "2001")

    def test_media_range_and_missing_or_unsafe_path(self):
        self.login()
        status, headers, body = self.request("GET", "/media/exampleuser/2001_0.jpg",
                                             cookie=self.cookie, headers={"Range": "bytes=0-2"})
        self.assertEqual(status, 206)
        self.assertEqual(headers["Content-Range"], "bytes 0-2/12")
        self.assertEqual(body, b"\xff\xd8\xff")
        self.assertEqual(self.request("GET", "/media/exampleuser/missing.jpg", cookie=self.cookie)[0], 404)
        self.assertEqual(self.request("GET", "/media/exampleuser/bad/name.jpg", cookie=self.cookie)[0], 403)

    def test_synthetic_x_cookie_upload_filters_other_domains(self):
        self.login()
        synthetic = ("# Netscape HTTP Cookie File\n"
                     "example.invalid\tFALSE\t/\tFALSE\t0\tauth_token\tFAKE_ONLY\n"
                     ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tFAKE_ONLY\n").encode()
        status, _, _ = self.request("POST", "/api/admin/x-auth", synthetic, cookie=self.cookie)
        self.assertEqual(status, 200)
        stored = (self.env.gdl / "cookies" / "cookies.txt").read_text(encoding="utf-8")
        self.assertIn(".x.com", stored)
        self.assertNotIn("example.invalid", stored)
        self.assertEqual(self.request("GET", "/api/admin/x-auth", cookie=self.cookie)[0], 200)


if __name__ == "__main__":
    unittest.main()

"""core.update — 버전 비교 + 로컬 가짜 GitHub API 서버로 check_latest 시험(네트워크 없음)."""
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import update  # noqa: E402
from core.version import REPO, VERSION, LATEST_RELEASE_URL  # noqa: E402


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        srv = self.server
        srv.hits.append((self.path, dict(self.headers)))
        if srv.delay:
            time.sleep(srv.delay)
        status, body, ctype = srv.reply
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class FakeApi:
    def __init__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.srv.daemon_threads = True
        self.srv.block_on_close = False
        self.srv.hits = []
        self.srv.delay = 0
        self.srv.reply = (200, {}, "application/json")
        self.t = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05},
                                  daemon=True)
        self.t.start()
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def reply(self, body, status=200, ctype="application/json"):
        self.srv.reply = (status, body, ctype)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def rel(tag, **kw):
    d = {"tag_name": tag, "html_url": "https://github.com/%s/releases/tag/%s" % (REPO, tag),
         "body": "변경 내역", "prerelease": False, "draft": False}
    d.update(kw)
    return d


class VersionCompare(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(update.parse_version("v1.2.3")[:4], (1, 2, 3, 1))
        self.assertEqual(update.parse_version("1.2")[:3], (1, 2, 0))
        self.assertEqual(update.parse_version(" V2 ")[:3], (2, 0, 0))
        for junk in (None, "", "latest", "v", "1.x", "1.2.3.4", "release-1.2", "1.2.3-", 12, "v1..2"):
            self.assertIsNone(update.parse_version(junk), junk)

    def test_newer(self):
        self.assertTrue(update.is_newer("1.1.0", "1.0.0"))
        self.assertTrue(update.is_newer("v1.0.1", "1.0.0"))
        self.assertTrue(update.is_newer("1.10.0", "1.9.9"))      # 문자열 비교가 아니다
        self.assertTrue(update.is_newer("2", "1.99.99"))
        self.assertFalse(update.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(update.is_newer("1.0", "1.0.0"))
        self.assertFalse(update.is_newer("0.9.9", "1.0.0"))
        self.assertFalse(update.is_newer("garbage", "1.0.0"))
        self.assertFalse(update.is_newer("1.1.0", "garbage"))

    def test_prerelease_order(self):
        self.assertFalse(update.is_newer("1.0.0-rc.1", "1.0.0"))
        self.assertTrue(update.is_newer("1.0.0", "1.0.0-rc.1"))
        self.assertTrue(update.is_newer("1.0.0-rc.2", "1.0.0-rc.1"))
        self.assertTrue(update.is_newer("1.0.0-rc.10", "1.0.0-rc.2"))
        self.assertTrue(update.is_newer("1.0.0-beta", "1.0.0-7"))   # 문자 > 숫자

    def test_build_metadata_ignored(self):
        self.assertFalse(update.is_newer("1.0.0+abc", "1.0.0"))


class CheckLatest(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k) for k in ("DRIVEBOARD_NO_UPDATE_CHECK", "DRIVEBOARD_UPDATE_BASE")
                     if k in os.environ}
        self.api = FakeApi()

    def tearDown(self):
        self.api.close()
        os.environ.pop("DRIVEBOARD_NO_UPDATE_CHECK", None)
        os.environ.pop("DRIVEBOARD_UPDATE_BASE", None)
        os.environ.update(self._env)

    def check(self, **kw):
        kw.setdefault("timeout", 3)
        return update.check_latest(base_url=self.api.base, **kw)

    def test_newer_release(self):
        self.api.reply(rel("v9.1.0"))
        r = self.check(current="1.0.0")
        self.assertEqual(r["latest"], "9.1.0")
        self.assertTrue(r["newer"])
        self.assertEqual(r["notes"], "변경 내역")
        self.assertTrue(r["url"].startswith("https://github.com/%s/releases/" % REPO))
        path, headers = self.api.srv.hits[0]
        self.assertEqual(path, "/repos/%s/releases/latest" % REPO)
        ua = {k.lower(): v for k, v in headers.items()}.get("user-agent", "")
        self.assertIn("DriveBoard/" + VERSION, ua)

    def test_same_or_older(self):
        self.api.reply(rel("v1.0.0"))
        self.assertFalse(self.check(current="1.0.0")["newer"])
        self.api.reply(rel("0.5.0"))
        self.assertFalse(self.check(current="1.0.0")["newer"])

    def test_default_current_is_version(self):
        self.api.reply(rel("v" + VERSION))
        self.assertFalse(self.check()["newer"])

    def test_junk_tag_not_newer(self):
        self.api.reply(rel("nightly-build"))
        r = self.check(current="1.0.0")
        self.assertIsNotNone(r)
        self.assertFalse(r["newer"])

    def test_prerelease_flag_not_newer(self):
        self.api.reply(rel("v9.0.0", prerelease=True))
        self.assertFalse(self.check(current="1.0.0")["newer"])
        self.api.reply(rel("v9.0.0", draft=True))
        self.assertFalse(self.check(current="1.0.0")["newer"])

    def test_foreign_url_replaced(self):
        self.api.reply(rel("v9.0.0", html_url="https://evil.example/releases/x"))
        self.assertEqual(self.check(current="1.0.0")["url"], LATEST_RELEASE_URL)
        self.api.reply(rel("v9.0.0", html_url="https://github.com/someone/else/releases/tag/v9"))
        self.assertEqual(self.check(current="1.0.0")["url"], LATEST_RELEASE_URL)
        self.api.reply(rel("v9.0.0", html_url=None))
        self.assertEqual(self.check(current="1.0.0")["url"], LATEST_RELEASE_URL)

    def test_notes_truncated_and_optional(self):
        self.api.reply(rel("v9.0.0", body="가" * 10000))
        self.assertEqual(len(self.check()["notes"]), update.NOTES_MAX)
        self.api.reply(rel("v9.0.0", body=None))
        self.assertEqual(self.check()["notes"], "")

    def test_failures_return_none(self):
        cases = [
            (404, {"message": "Not Found"}),          # 릴리스가 아직 없음
            (403, {"message": "rate limit"}),
            (500, "oops"),
            (200, "not json{"),
            (200, ["list"]),
            (200, {"name": "no tag"}),
            (200, {"tag_name": 123}),
            (200, {"tag_name": "  "}),
        ]
        for status, body in cases:
            self.api.reply(body, status=status)
            self.assertIsNone(self.check(), (status, body))

    def test_oversized_body(self):
        self.api.reply(json.dumps(rel("v9.0.0", body="x" * (update.MAX_BODY + 10))))
        self.assertIsNone(self.check())

    def test_timeout(self):
        self.api.reply(rel("v9.0.0"))
        self.api.srv.delay = 1.5
        t0 = time.monotonic()
        self.assertIsNone(self.check(timeout=0.3))
        self.assertLess(time.monotonic() - t0, 1.4)

    def test_connection_refused(self):
        base = self.api.base
        self.api.close()
        self.api = FakeApi()        # tearDown 용
        self.assertIsNone(update.check_latest(timeout=1, base_url=base))

    def test_disabled_by_env(self):
        self.api.reply(rel("v9.0.0"))
        os.environ["DRIVEBOARD_NO_UPDATE_CHECK"] = "1"
        self.assertIsNone(self.check())
        self.assertEqual(self.api.srv.hits, [])

    def test_env_base(self):
        self.api.reply(rel("v9.0.0"))
        os.environ["DRIVEBOARD_UPDATE_BASE"] = self.api.base
        r = update.check_latest(timeout=3)
        self.assertTrue(r and r["newer"])

    def test_rejects_plain_http_remote(self):
        self.assertIsNone(update.check_latest(timeout=1, base_url="http://example.com"))
        self.assertIsNone(update.check_latest(timeout=1, base_url="ftp://127.0.0.1"))
        self.assertIsNone(update.check_latest(timeout=1, base_url="not a url"))

    def test_checker(self):
        self.api.reply(rel("v9.0.0"))
        c = update.Checker(base_url=self.api.base, current="1.0.0", timeout=3).start()
        self.assertTrue(c.wait(5))
        self.assertTrue(c.done)
        self.assertEqual(c.state()["latest"], "9.0.0")
        c.start()                                   # 두 번 불러도 한 번만 조회
        time.sleep(0.1)
        self.assertEqual(len(self.api.srv.hits), 1)

    def test_checker_not_newer_state_none(self):
        self.api.reply(rel("v0.0.1"))
        c = update.Checker(base_url=self.api.base, current="1.0.0").start()
        c.wait(5)
        self.assertIsNone(c.state())
        self.assertFalse(c.result["newer"])

    def test_checker_disabled(self):
        self.api.reply(rel("v9.0.0"))
        c = update.Checker(enabled=False, base_url=self.api.base).start()
        self.assertTrue(c.wait(1))
        self.assertIsNone(c.result)
        self.assertEqual(self.api.srv.hits, [])


if __name__ == "__main__":
    unittest.main()

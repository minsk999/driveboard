"""app.py 회귀 시험 — 리뷰에서 확정된 결함들(작은 가짜 드라이브, 프로세스 안 App).

①중복 「내용 같음」 이 다시 내보낸 파일·새로 나타난 같은 이름 파일에 남던 것 ②영구 삭제가 확인창보다 많이 지우던 것
③팀 설정 미리보기가 모르는 UNC 호스트를 stat 하던 것 ④매핑 문자 + 등록 NAS 가 인덱스 하나를 같이 쓰던 것
⑤작업 뒤 끝난 전체 스캔이 지운 항목을 되살리던 것 ⑦부모 규칙이 자식 규칙을 덮던 것 + 관문·open_url·잠금 등.
"""
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import app as app_mod                                   # noqa: E402
from core import categories as C                        # noqa: E402
from core import dupes, model, scanner, store, winapi   # noqa: E402
from core import settings as settings_mod               # noqa: E402
import make_fake_drives                                 # noqa: E402

MB = 1 << 20
GB = 1 << 30
TOKEN = "test-token-fixes"


def _write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


class Base(unittest.TestCase):
    DRIVES = {"C": {"kind": "ssd"}, "D": {"kind": "hdd"}, "E": {"kind": "hdd"}}
    SETTINGS = None

    def prep(self):
        pass

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db-fix-")
        self._env = {k: os.environ.get(k) for k in ("DRIVEBOARD_HOME", "DRIVEBOARD_NO_UPDATE_CHECK")}
        os.environ["DRIVEBOARD_HOME"] = os.path.join(self.tmp, "home")
        os.environ["DRIVEBOARD_NO_UPDATE_CHECK"] = "1"
        self.fake = os.path.join(self.tmp, "fake")
        for L, meta in self.DRIVES.items():
            os.makedirs(os.path.join(self.fake, L))
            m = {"label": L, "total": 100 * GB}
            m.update(meta)
            with open(os.path.join(self.fake, L, "_drive.json"), "w") as f:
                json.dump(m, f)
        self.prep()
        winapi.set_fake_root(self.fake)
        if self.SETTINGS:
            settings_mod.save(self.SETTINGS)
        self.app = app_mod.App()
        self.app.drives(force=True)
        self.srv = None

    def tearDown(self):
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
        self.app.shutdown()
        winapi.set_fake_root(None)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def P(self, *parts):
        return os.path.join(self.fake, *parts)

    def scan(self, L, timeout=30):
        started, _sk = self.app.start_scans([L])
        self.assertEqual(started, [L])
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = self.app.scans.get(L)
            if s and s.get("phase") == "done":
                return self.app.indexes[L]
            if s and s.get("phase") == "error":
                self.fail(s)
            time.sleep(0.02)
        self.fail("scan timeout")

    def wait(self, jid, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.app.queue.get(jid)
            if j and j["status"] not in ("queued", "running"):
                return j
            time.sleep(0.02)
        self.fail("job timeout")

    def serve(self):
        self.srv = app_mod.make_server(self.app, 0, token=TOKEN)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self.srv.server_address[1]

    def req(self, method, path, body=None):
        port = self.srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        h = {"X-DB-Token": TOKEN, "Host": "127.0.0.1:%d" % port}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, json.loads(raw.decode("utf-8"))


# ─────────────────────────────────────────────── ① 중복 확인 결과의 지문
def _sparse(p, size, head=b"", mid=b""):
    """큰 희소 파일 — 앞(head)·가운데(mid)만 실제 바이트(quick_hash 가 읽는 곳)."""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        make_fake_drives.mark_sparse(f)
        f.truncate(size)
        if head:
            f.write(head)
        if mid:
            f.seek(size // 2 - len(mid) // 2)
            f.write(mid)
    return p


class TestDupesVerifiedFingerprint(Base):
    SZ = 60 * MB                                    # find_dupes 기본 하한(50MB) 위

    def prep(self):
        self.head = os.urandom(MB)
        self.a = _sparse(self.P("D", "render", "final.wav"), self.SZ, self.head)
        self.b = _sparse(self.P("E", "backup", "final.wav"), self.SZ, self.head)

    def verify(self):
        j = self.wait(self.app.submit_verify(None))
        self.assertEqual(j["status"], "done", j)
        return self.app.dupe_groups()

    def test_reexport_same_size_drops_badge(self):
        self.scan("D")
        self.scan("E")
        gs = self.verify()
        self.assertEqual(len(gs), 1)
        self.assertIs(gs[0]["verified"], True)
        self.assertIs(self.app.dupe_groups()[0]["verified"], True)         # 그대로면 유지
        # 편집자가 같은 길이로 다시 내보낸다: 크기는 같고 내용·수정 시각만 다르다
        _sparse(self.a, self.SZ, self.head, mid=os.urandom(MB))
        t = time.time() + 60
        os.utime(self.a, (t, t))
        self.scan("D")
        g = self.app.dupe_groups()[0]
        self.assertEqual(g["size"], self.SZ)
        self.assertIsNone(g["verified"], "옛 「내용 같음」 이 바뀐 파일에 남았다")
        self.assertIs(dupes.verify_group(dict(g, items=[dict(x) for x in g["items"]])), False)

    def test_new_member_is_not_verified(self):
        self.scan("D")
        self.scan("E")
        self.assertIs(self.verify()[0]["verified"], True)
        # 같은 이름·크기의 다른 내용 파일이 새로 나타난다(새로 스캔한 드라이브·다시 붙은 NAS)
        _sparse(self.P("C", "x", "final.wav"), self.SZ, os.urandom(MB))
        self.scan("C")
        g = self.app.dupe_groups()[0]
        self.assertEqual(len(g["items"]), 3)
        self.assertIsNone(g["verified"], "한 번도 읽지 않은 파일이 「내용 같음」 을 물려받았다")

    def test_fingerprint_helpers(self):
        g = {"size": 10, "items": [{"path": "/a/x", "mtime": 5.0, "disk_size": 10, "disk_mtime": 5.0},
                                   {"path": "/b/x", "mtime": 7.0, "disk_size": 10, "disk_mtime": 7.0}]}
        fp = dupes.fingerprint(g)
        cur = {"size": 10, "items": [{"path": "/a/x", "mtime": 5.0}, {"path": "/b/x", "mtime": 7.0}]}
        self.assertTrue(dupes.fingerprint_matches(fp, cur))
        cur["items"][1]["mtime"] = 8.0
        self.assertFalse(dupes.fingerprint_matches(fp, cur))
        cur["items"][1]["mtime"] = 7.0
        cur["items"].append({"path": "/c/x", "mtime": 1.0})
        self.assertFalse(dupes.fingerprint_matches(fp, cur))
        self.assertFalse(dupes.fingerprint_matches(None, cur))
        bad = dupes.fingerprint({"size": 10, "items": [{"path": "/a/x", "disk_size": None, "disk_mtime": None}]})
        self.assertFalse(dupes.fingerprint_matches(bad, {"size": 10, "items": [{"path": "/a/x", "mtime": 5.0}]}))


# ─────────────────────────────────────────────── ② 영구 삭제는 확인창이 본 것보다 많이 지우지 않는다
class TestDeleteGrowthGuard(Base):
    def prep(self):
        _write(self.P("D", "old", "a.mov"), b"a" * (2 * MB))
        _write(self.P("D", "same", "a.mov"), b"a" * (2 * MB))

    def test_folder_grew_after_scan(self):
        self.scan("D")
        self.serve()
        old = self.P("D", "old")
        s, ins = self.req("POST", "/api/inspect", {"paths": [old]})
        self.assertEqual((ins["total"], ins["total_files"]), (2 * MB, 1))
        for i in range(3):                                     # 스캔 뒤 동료가 렌더를 더 넣었다
            _write(self.P("D", "old", "new%d.mov" % i), b"n" * MB)
        s, d = self.req("POST", "/api/ops/delete", {"paths": [old], "confirm": True})
        self.assertEqual(s, 409, d)
        self.assertEqual(d["error"], "changed")
        self.assertEqual(d["paths"], [old])
        self.assertEqual(d["grown"][0]["files"], 4)
        self.assertTrue(os.path.exists(self.P("D", "old", "a.mov")), "아무것도 지우지 않아야 한다")
        self.assertEqual(len(os.listdir(old)), 4)

    def test_expect_from_sheet_is_honoured(self):
        self.scan("D")
        self.serve()
        p = self.P("D", "same")
        # UI 가 확인창에 0바이트를 보였다면(그 사이 인덱스가 새로 고쳐졌어도) 그보다 많이 지우지 않는다
        s, d = self.req("POST", "/api/ops/delete",
                        {"paths": [p], "confirm": True, "expect": [{"path": p, "bytes": 0, "files": 0}]})
        self.assertEqual(s, 409, d)
        self.assertTrue(os.path.exists(p))

    def test_unchanged_and_shrunk_folders_delete(self):
        self.scan("D")
        self.serve()
        os.remove(self.P("D", "old", "a.mov"))                  # 줄어든 것은 괜찮다
        s, d = self.req("POST", "/api/ops/delete",
                        {"paths": [self.P("D", "old"), self.P("D", "same")], "confirm": True,
                         "expect": [{"path": self.P("D", "same"), "bytes": 2 * MB, "files": 1}]})
        self.assertEqual(s, 200, d)
        j = self.wait(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertFalse(os.path.exists(self.P("D", "same")))
        self.assertFalse(os.path.exists(self.P("D", "old")))


# ─────────────────────────────────────────────── ③ 팀 설정 미리보기는 모르는 호스트를 건드리지 않는다
class TestTeamImportNoUncProbe(Base):
    def prep(self):
        _write(self.P("D", "proj", "a.mov"), b"x" * (2 * MB))

    def test_preview_never_stats_foreign_paths(self):
        self.scan("D")
        known = self.P("D", "proj")
        team = {"format": settings_mod.TEAM_FORMAT, "version": 1, "rules": [
            {"path": "\\\\attacker.example\\share\\x", "cat": "cache"},
            {"path": "\\\\attacker@80\\dav\\y", "cat": "cache"},
            {"path": "//evil/s/y", "cat": "cache"},
            {"path": "/not/a/known/drive/z", "cat": "cache"},
            {"path": known, "cat": "archive"}]}
        calls = []
        real_exists, real_isdir = os.path.exists, os.path.isdir

        def spy_exists(p):
            calls.append(p)
            return real_exists(p)

        def spy_isdir(p):
            calls.append(p)
            return real_isdir(p)
        with mock.patch("os.path.exists", spy_exists), mock.patch("os.path.isdir", spy_isdir):
            r = self.app.team_import(json.dumps(team), apply=False)
        touched = [c for c in calls if isinstance(c, str) and ("attacker" in c or "evil" in c or "/not/a" in c)]
        self.assertEqual(touched, [], "가져온 파일의 모르는 경로를 디스크에서 찾았다")
        ex = {e["path"]: e["exists"] for e in r["diff"]["rules"]["added"]}
        self.assertTrue(ex[os.path.normpath(known)])
        self.assertEqual(sum(1 for v in ex.values() if v), 1)
        self.assertEqual(r["diff"]["rules"]["missing_paths"], 4)

    def test_settings_default_skips_unc(self):
        team = {"format": settings_mod.TEAM_FORMAT, "version": 1,
                "rules": [{"path": "\\\\host\\share\\x", "cat": "cache"}, {"path": "//h/s/x", "cat": "cache"}]}
        with mock.patch("os.path.exists", side_effect=AssertionError("stat")):
            _new, diff = settings_mod.import_team({}, team)
        self.assertEqual(diff["rules"]["missing_paths"], 2)


# ─────────────────────────────────────────────── ④ 같은 공유를 문자로도, 「NAS 추가」로도
class TestSameShareTwice(Base):
    UNC = "\\\\ORICO\\share"
    DRIVES = {"C": {"kind": "ssd"}, "D": {"kind": "hdd"},
              "Z": {"kind": "network", "unc": "\\\\ORICO\\share"},
              "N-x": {"kind": "network", "unc": "\\\\ORICO\\share"}}
    SETTINGS = {"network_locations": [{"path": "\\\\ORICO\\share"}]}

    def prep(self):
        for k in ("Z", "N-x"):                    # 같은 공유의 두 모습 — 실제로는 같은 폴더다
            _sparse(self.P(k, "proj", "clip.mov"), 80 * MB)

    def test_one_drive_one_index_no_self_dupes(self):
        ds = self.app.drives(force=True)
        keys = [d["letter"] for d in ds]
        serials = [d["serial"] for d in ds if d.get("unc")]
        self.assertEqual(len(serials), 1, keys)
        self.assertIn("Z", keys)                                  # 문자 드라이브를 남긴다
        z = next(d for d in ds if d["letter"] == "Z")
        self.assertTrue(z.get("also_as"))
        self.scan("Z")
        self.app._resync()
        idx_ids = [id(i) for i in self.app.indexes.values()]
        self.assertEqual(len(idx_ids), len(set(idx_ids)), "인덱스 하나가 두 화면 키에 붙었다")
        self.assertEqual(self.app.dupe_groups(), [])
        nets = [s for s in self.app.drive_states() if s.get("unc")]
        self.assertEqual(len(nets), 1)
        self.assertEqual(nets[0]["scanned_bytes"], 80 * MB)

    def test_remove_hidden_location_keeps_mapped_index(self):
        self.scan("Z")
        sk = self.app._key(self.app.drive("Z"))
        r = self.app.remove_location(winapi.network_key(self.UNC))
        self.assertTrue(r["ok"])
        self.assertIsNotNone(store.load_index(sk), "숨은 등록 위치를 지우며 Z: 의 인덱스까지 지웠다")

    def test_find_dupes_drops_same_path(self):
        _sparse(self.P("D", "a", "big.mov"), 60 * MB)
        idx = scanner.scan_subtree(self.P("D"), {"letter": "D", "root": self.P("D")}, {})
        self.assertEqual(dupes.find_dupes({"D": idx, "Y": idx}), [])


# ─────────────────────────────────────────────── ⑤ 스캔 중 작업
class TestScanVsOps(Base):
    def prep(self):
        for n in ("keep", "victim"):
            _sparse(self.P("D", n, "clip.mov"), 80 * MB)

    def test_finished_scan_does_not_resurrect_removed_folder(self):
        self.scan("D")
        victim = self.P("D", "victim")
        orig = scanner._finalize
        seen = {}

        def fin(idx):
            if not seen:
                seen["x"] = True
                # 걷기가 이미 지나간 폴더를 사용자가 지운다(작업 → on_removed 훅)
                shutil.rmtree(victim)
                self.app._hook_refresh(victim)
            return orig(idx)
        with mock.patch.object(scanner, "_finalize", fin):
            idx = self.scan("D")
        self.assertTrue(seen)
        self.assertIsNone(model.find_dir(idx, victim), "지운 폴더가 스캔 결과로 되살아났다")
        self.assertEqual(idx.d_size[0], 80 * MB)
        self.assertEqual(model.check_invariants(idx), [])
        self.assertEqual(self.app._scan_dirty, {})

    def test_copy_into_passed_folder_survives(self):
        self.scan("D")
        dst = self.P("D", "keep", "copied.mov")
        orig = scanner._finalize
        seen = {}

        def fin(idx):
            if not seen:
                seen["x"] = True
                _sparse(dst, 70 * MB)
                self.app._hook_refresh(dst)
            return orig(idx)
        with mock.patch.object(scanner, "_finalize", fin):
            idx = self.scan("D")
        self.assertIsNotNone(model.find_item(idx, dst))
        self.assertEqual(idx.d_size[0], 230 * MB)


# ─────────────────────────────────────────────── ⑦ 가장 깊은 규칙이 이긴다
class TestNestedRules(Base):
    def prep(self):
        _sparse(self.P("D", "X", "Y", "a.mp4"), 60 * MB)
        _sparse(self.P("D", "X", "b.mp4"), 60 * MB)

    def cats(self, idx, rel):
        return model.cat_totals(idx, model.find_dir(idx, self.P("D", *rel.split("/"))))

    def test_parent_rule_keeps_child_rule(self):
        self.scan("D")
        X, Y = self.P("D", "X"), self.P("D", "X", "Y")
        self.app.add_rule(Y, "archive")
        self.app.add_rule(X, "cache")
        idx = self.app.indexes["D"]
        y = self.cats(idx, "X/Y")
        self.assertEqual(y[C.ARCHIVE], 60 * MB, "부모 규칙이 자식 규칙을 덮었다(보관 → 캐시)")
        self.assertEqual(y[C.CACHE], 0)
        x = self.cats(idx, "X")
        self.assertEqual((x[C.CACHE], x[C.ARCHIVE]), (60 * MB, 60 * MB))
        self.assertEqual(model.check_invariants(idx), [])
        idx2 = self.scan("D")                                  # 다시 스캔해도 같은 답
        self.assertEqual(list(self.cats(idx2, "X/Y")), list(y))
        self.assertEqual(list(self.cats(idx2, "X")), list(x))

    def test_team_import_order_parent_last(self):
        self.scan("D")
        X, Y = self.P("D", "X"), self.P("D", "X", "Y")
        team = {"format": settings_mod.TEAM_FORMAT, "version": 1,
                "rules": [{"path": Y, "cat": "archive"}, {"path": X, "cat": "cache"}]}
        r = self.app.team_import(json.dumps(team), apply=True)
        self.assertTrue(r["applied"])
        idx = self.app.indexes["D"]
        self.assertEqual(self.cats(idx, "X/Y")[C.ARCHIVE], 60 * MB)
        self.assertEqual(self.cats(idx, "X")[C.CACHE], 60 * MB)


# ─────────────────────────────────────────────── 관문 · open_url · 잠금 · 설정 값
class TestGateAndMisc(Base):
    def test_gate_rejection_closes_connection(self):
        port = self.serve()
        inner = ("GET /api/meta HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-DB-Token: %s\r\n\r\n" % (port, TOKEN)).encode()
        outer = ("POST /api/scan HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: http://evil.example\r\n"
                 "Content-Type: text/plain\r\nContent-Length: %d\r\n\r\n" % (port, len(inner))).encode() + inner
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        s.sendall(outer)
        buf = b""
        while True:
            try:
                b = s.recv(65536)
            except socket.timeout:
                break
            if not b:
                break
            buf += b
        s.close()
        self.assertEqual(buf.count(b"HTTP/1."), 1, buf[:400])
        self.assertIn(b" 403 ", buf.split(b"\r\n", 1)[0])

    def test_chunked_rejected(self):
        port = self.serve()
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        s.sendall(("POST /api/scan HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-DB-Token: %s\r\n"
                   "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n" % (port, TOKEN)).encode())
        buf = s.recv(65536)
        s.close()
        self.assertIn(b" 400 ", buf.split(b"\r\n", 1)[0])

    def test_open_url_dot_segments(self):
        base = "https://github.com" + app_mod.OPEN_URL_PREFIX
        self.assertTrue(app_mod.allowed_open_url(base + "/releases/latest"))
        for u in (base + "/../../attacker/repo", base + "/%2e%2e/%2e%2e/attacker", base + "/./x",
                  base + "/releases/../../../x"):
            self.assertFalse(app_mod.allowed_open_url(u), u)

    def test_find_running_ignores_responder_url(self):
        class H(app_mod.BaseHTTPRequestHandler):
            def do_GET(self):
                b = json.dumps({"ok": True, "app": "DriveBoard", "window": False,
                                "url": "C:\\Users\\Public\\payload.exe"}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass
        srv = app_mod.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            port = srv.server_address[1]
            os.makedirs(os.path.dirname(app_mod.lock_path()), exist_ok=True)
            with open(app_mod.lock_path(), "w") as f:
                json.dump({"port": port, "secret": "s"}, f)
            got = app_mod.find_running(timeout=5)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(got["url"], "http://127.0.0.1:%d/" % port)

    def test_settings_overflow_numbers(self):
        v = settings_mod.validate({"big_file_mb": "1e400", "old_days": "-1e999"})
        base = settings_mod.validate({})
        self.assertEqual(v["big_file_mb"], base["big_file_mb"])
        self.assertEqual(v["old_days"], base["old_days"])
        self.serve()
        s, d = self.req("POST", "/api/settings", {"big_file_mb": "1e400"})
        self.assertEqual(s, 200, d)


if __name__ == "__main__":
    unittest.main()

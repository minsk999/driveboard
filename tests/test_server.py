"""app.py 통합 테스트: 실제 HTTP 서버(프로세스 안)를 작은 가짜 드라이브에 띄워 SPEC §7 계약을 확인한다."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

_TMP = tempfile.mkdtemp(prefix="db-server-")
os.environ["DRIVEBOARD_HOME"] = os.path.join(_TMP, "home")
os.environ["DRIVEBOARD_NO_UPDATE_CHECK"] = "1"

import app as app_mod                      # noqa: E402
import make_fake_drives                    # noqa: E402
from core import model, winapi             # noqa: E402
from core.categories import NCAT           # noqa: E402

DRIVE_KEYS = {"letter", "root", "label", "serial", "fs", "kind", "total", "free", "used", "scanned",
              "scanned_at", "scan_seconds", "cats", "scanned_bytes", "files", "dirs", "errors",
              "bin_bytes", "bin_count", "unscanned_bytes", "stale",
              # SPEC-2
              "external", "has_bin", "scan", "connected", "unc"}
ITEM_KEYS = {"key", "drive", "kind", "id", "name", "path", "size", "metric", "cat", "cat_bytes",
             "files", "mtime", "protected", "protected_reason"}
JOB_KEYS = {"id", "type", "status", "title", "created_at", "started_at", "finished_at", "bytes_total",
            "bytes_done", "files_total", "files_done", "current", "speed_bps", "eta_s", "freed", "added",
            "results", "undoable", "message"}


def setUpModule():
    global FAKE, APP, SRV, PORT, TOKEN
    # 다른 시험 모듈(test_server_v2)이 import 때 바꿨을 수 있다 → 이 모듈의 데이터 폴더로 되돌린다
    os.environ["DRIVEBOARD_HOME"] = os.path.join(_TMP, "home")
    os.environ["DRIVEBOARD_NO_UPDATE_CHECK"] = "1"
    FAKE = os.path.join(_TMP, "fake")
    make_fake_drives.make(FAKE, scale="small", clean=True)
    # 이동·휴지통 시험용 작은 실제 파일(희소 파일 아님)
    for rel, n in (("D/_시험/옮길폴더/a.txt", 3000), ("D/_시험/옮길폴더/sub/b.bin", 5000),
                   ("D/_시험/버릴폴더/c.txt", 7000), ("E/_시험/x.txt", 10)):
        p = os.path.join(FAKE, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(os.urandom(n))
    winapi.set_fake_root(FAKE)
    APP = app_mod.App()
    SRV = app_mod.make_server(APP, 18790, token="test-token-123")
    PORT = SRV.server_address[1]
    TOKEN = "test-token-123"
    threading.Thread(target=SRV.serve_forever, daemon=True).start()


def tearDownModule():
    SRV.shutdown()
    SRV.server_close()
    APP.shutdown()
    winapi.set_fake_root(None)
    shutil.rmtree(_TMP, ignore_errors=True)


def req(method, path, body=None, headers=None, token=True, host=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=30)
    h = {"Host": host or ("127.0.0.1:%d" % PORT)}
    if token:
        h["X-DB-Token"] = TOKEN
    if body is not None:
        body = json.dumps(body).encode("utf-8")
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    try:
        return r.status, json.loads(data.decode("utf-8"))
    except ValueError:
        return r.status, data


def wait_job(jid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _s, d = req("GET", "/api/jobs")
        for j in d["jobs"]:
            if j["id"] == jid and j["status"] not in ("queued", "running"):
                return j
        time.sleep(0.05)
    raise AssertionError("job timeout")


def drive_state(L):
    _s, d = req("GET", "/api/state")
    return next(x for x in d["drives"] if x["letter"] == L)


class T0Security(unittest.TestCase):
    def test_token(self):
        self.assertEqual(req("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(req("GET", "/api/state", token=False, headers={"X-DB-Token": "nope"})[0], 403)

    def test_sec_fetch_site(self):
        for v in ("none", "cross-site", "same-site"):
            self.assertEqual(req("GET", "/api/state", headers={"Sec-Fetch-Site": v})[0], 403, v)
            self.assertEqual(req("POST", "/api/scan", {"drives": []}, headers={"Sec-Fetch-Site": v})[0], 403, v)
        self.assertEqual(req("GET", "/api/state", headers={"Sec-Fetch-Site": "same-origin"})[0], 200)

    def test_host_and_origin(self):
        self.assertEqual(req("GET", "/api/state", host="evil.example:%d" % PORT)[0], 403)
        self.assertEqual(req("GET", "/", host="evil.example")[0], 403)
        self.assertEqual(req("GET", "/api/state", host="localhost:%d" % PORT)[0], 200)
        self.assertEqual(req("GET", "/api/state", headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(req("GET", "/api/state", headers={"Origin": "http://127.0.0.1:%d" % PORT})[0], 200)

    def test_static(self):
        s, body = req("GET", "/", token=False)
        self.assertEqual(s, 200)
        self.assertIn(b'content="test-token-123"', body)
        self.assertNotIn(b"__DB_TOKEN__", body)
        self.assertEqual(req("GET", "/app.js", token=False)[0], 200)
        self.assertEqual(req("GET", "/fonts/PretendardVariable.woff2", token=False)[0], 200)
        for bad in ("/../app.py", "/%2e%2e/app.py", "/..%2fapp.py", "/fonts/../../app.py",
                    "//etc/passwd", "/app.py", "/../core/ops.py", "/.hidden.js", "/C:/x.js"):
            self.assertIn(req("GET", bad, token=False)[0], (403, 404), bad)

    def test_body_cap(self):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        c.putrequest("POST", "/api/inspect", skip_host=True)
        c.putheader("Host", "127.0.0.1:%d" % PORT)
        c.putheader("X-DB-Token", TOKEN)
        c.putheader("Content-Length", str(2 << 20))
        c.endheaders()
        r = c.getresponse()
        self.assertEqual(r.status, 413)
        c.close()

    def test_bad_json(self):
        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        c.request("POST", "/api/inspect", body=b"{nope",
                  headers={"Host": "127.0.0.1:%d" % PORT, "X-DB-Token": TOKEN})
        r = c.getresponse()
        self.assertEqual(r.status, 400)
        self.assertEqual(json.loads(r.read())["error"], "bad_request")
        c.close()


class T1Flow(unittest.TestCase):
    """순서대로 돈다(스캔 → 질의 → 작업)."""

    @classmethod
    def setUpClass(cls):
        s, d = req("POST", "/api/scan", {"drives": "all"})
        assert s == 200, d
        t0 = time.time()
        while time.time() - t0 < 60:
            st = req("GET", "/api/state")[1]
            if all(x["scanned"] for x in st["drives"]) and not st["busy"]:
                break
            time.sleep(0.1)
        cls.state = req("GET", "/api/state")[1]

    def test_01_meta(self):
        s, d = req("GET", "/api/meta")
        self.assertEqual(s, 200)
        self.assertEqual(d["platform"], "fake")
        self.assertEqual(len(d["categories"]), NCAT)
        self.assertEqual(set(d["virtual"]), {"bin", "unscanned"})
        self.assertIn("big_file_mb", d["settings"])

    def test_02_state(self):
        d = self.state
        self.assertTrue(d["ok"])
        self.assertEqual([x["letter"] for x in d["drives"]], list("CDEFGHI"))
        for x in d["drives"]:
            self.assertTrue(DRIVE_KEYS <= set(x), DRIVE_KEYS - set(x))
            self.assertTrue(x["scanned"])
            self.assertTrue(x["connected"])
            self.assertTrue(x["scan"])              # 내장 고정 드라이브는 기본 스캔 대상
            self.assertFalse(x["external"])
            self.assertIsNone(x["unc"])
            self.assertEqual(len(x["cats"]), NCAT)
            self.assertEqual(sum(x["cats"]), x["scanned_bytes"])
            self.assertEqual(x["used"], x["total"] - x["free"])
            self.assertEqual(x["unscanned_bytes"], max(0, x["used"] - x["scanned_bytes"] - x["bin_bytes"]))
            self.assertFalse(x["stale"])
        for L, sc in d["scans"].items():
            self.assertEqual(sc["phase"], "done")
        self.assertIsInstance(d["jobs"], list)
        self.assertIn("busy", d)

    def test_03_scan_twice_is_noop(self):
        s, d = req("POST", "/api/scan", {"drives": ["Z"]})
        self.assertEqual((s, d["started"]), (200, []))
        self.assertEqual(req("POST", "/api/scan", {"drives": 5})[0], 400)

    def test_04_items_files(self):
        s, d = req("GET", "/api/items?drive=D&view=files&cat=&dir=0&age=0&q=&sort=size&order=desc&offset=0&limit=50")
        self.assertEqual(s, 200)
        self.assertTrue({"total", "total_bytes", "offset", "crumbs", "remainder", "items"} <= set(d))
        self.assertGreater(d["total"], 0)
        sizes = [it["metric"] for it in d["items"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        for it in d["items"]:
            self.assertTrue(ITEM_KEYS <= set(it))
            self.assertEqual(it["key"], "D:%s:%d" % (it["kind"], it["id"]))
            self.assertIn(it["kind"], ("file", "seq"))
            if it["kind"] == "seq":
                self.assertGreater(it["count"], 1)
        self.assertEqual(d["crumbs"][0]["dir"], 0)
        # 큰 항목 합 + 작은 파일 몫 = 드라이브 합계
        allf = req("GET", "/api/items?drive=D&view=files&limit=500")[1]
        dst = drive_state("D")
        self.assertEqual(allf["total_bytes"] + allf["remainder"]["bytes"], dst["scanned_bytes"])
        # 카테고리·검색·전체 드라이브
        c = req("GET", "/api/items?drive=*&view=files&cat=2&limit=20")[1]
        self.assertTrue(all(it["cat"] == 2 for it in c["items"]))
        self.assertEqual(req("GET", "/api/items?drive=Q&view=files")[0], 404)
        self.assertEqual(req("GET", "/api/items?drive=D&view=bogus")[0], 400)
        n = req("GET", "/api/items?drive=*&view=files&sort=name&order=asc&limit=30")[1]
        names = [it["name"].lower() for it in n["items"]]
        self.assertEqual(names, sorted(names))

    def test_05_items_folders(self):
        s, d = req("GET", "/api/items?drive=D&view=folders&dir=0")
        self.assertEqual(s, 200)
        kinds = [it["kind"] for it in d["items"]]
        self.assertIn("dir", kinds)
        if "loose" in kinds:
            self.assertEqual(kinds[-1], "loose")
            lo = d["items"][-1]
            self.assertTrue(lo["protected"])
        sub = next(it for it in d["items"] if it["kind"] == "dir" and it["name"] == "_시험")
        d2 = req("GET", "/api/items?drive=D&view=folders&dir=%d" % sub["id"])[1]
        self.assertEqual([c["name"] for c in d2["crumbs"]][-1], "_시험")
        self.assertEqual({it["name"] for it in d2["items"] if it["kind"] == "dir"}, {"옮길폴더", "버릴폴더"})
        roots = req("GET", "/api/items?drive=*&view=folders")[1]
        self.assertEqual(len(roots["items"]), 7)
        self.assertTrue(all(it["protected"] for it in roots["items"]))
        c = req("GET", "/api/items?drive=C&view=folders&dir=0")[1]
        win = next(it for it in c["items"] if it["name"].lower() == "windows")
        self.assertTrue(win["protected"])

    def test_06_tree(self):
        s, d = req("GET", "/api/tree?drive=D&dir=0&depth=2")
        self.assertEqual(s, 200)
        n = d["node"]
        for k in ("id", "name", "path", "size", "cat", "cat_bytes", "children"):
            self.assertIn(k, n)
        self.assertEqual(n["id"], 0)
        self.assertEqual(n["size"], drive_state("D")["scanned_bytes"])
        self.assertLessEqual(sum(c["size"] for c in n["children"]), n["size"])
        self.assertEqual(req("GET", "/api/tree?drive=Q")[0], 404)

    def test_07_inspect(self):
        p = os.path.join(FAKE, "D", "_시험", "옮길폴더")
        s, d = req("POST", "/api/inspect", {"paths": [p, os.path.join(FAKE, "D"), "/nope/x"]})
        self.assertEqual(s, 200)
        a, root, bad = d["items"]
        self.assertEqual((a["kind"], a["size"], a["files"], a["exists"], a["protected"]),
                         ("dir", 8000, 2, True, False))
        self.assertTrue(root["protected"])
        self.assertTrue(bad["protected"])
        self.assertFalse(bad["exists"])
        self.assertEqual(d["total"], a["size"] + root["size"])
        self.assertEqual(len(d["cat_bytes"]), NCAT)

    def test_08_recs_dupes_settings_jobs(self):
        s, d = req("GET", "/api/recommendations")
        self.assertEqual(s, 200)
        for r in d["items"]:
            self.assertTrue({"id", "kind", "title", "desc", "bytes", "count", "tone", "action"} <= set(r))
        s, d = req("GET", "/api/dupes?min_mb=50")
        self.assertEqual(s, 200)
        self.assertIn("wasted_total", d)
        for g in d["groups"]:
            self.assertTrue({"size", "name", "wasted", "verified", "items"} <= set(g))
        s, d = req("GET", "/api/settings")
        self.assertEqual(s, 200)
        s, d = req("POST", "/api/settings", {"old_days": 200, "rules": [{"path": "/x", "cat": 1}]})
        self.assertEqual((s, d["settings"]["old_days"]), (200, 200))
        self.assertEqual(d["settings"]["rules"], [])
        req("POST", "/api/settings", {"old_days": 180})
        s, d = req("GET", "/api/jobs")
        self.assertEqual(s, 200)
        self.assertIsInstance(d["jobs"], list)
        s, d = req("POST", "/api/dupes/verify", {})
        self.assertEqual(s, 200)
        j = wait_job(d["job_id"])
        self.assertTrue(JOB_KEYS <= set(j), JOB_KEYS - set(j))
        self.assertEqual(j["type"], "verify_dupes")
        self.assertIn(j["status"], ("done", "partial"))
        self.assertEqual(req("POST", "/api/reveal", {"path": os.path.join(FAKE, "D", "_시험")})[0], 200)
        self.assertEqual(req("POST", "/api/reveal", {"path": "/etc/passwd"})[0], 400)
        self.assertEqual(req("POST", "/api/nope", {})[0], 404)

    def test_09_protected(self):
        win = os.path.join(FAKE, "C", "Windows")
        s, d = req("POST", "/api/ops/recycle", {"paths": [win]})
        self.assertEqual((s, d["error"]), (400, "protected"))
        self.assertEqual(d["blocked"][0]["path"], win)
        self.assertTrue(os.path.isdir(win))
        for p in (os.path.join(FAKE, "D"), "/etc", os.path.join(FAKE, "D", "..", "C")):
            s, d = req("POST", "/api/ops/recycle", {"paths": [p]})
            self.assertEqual(s, 400, p)
        s, d = req("POST", "/api/ops/move", {"paths": [win], "target": "H", "verify": "quick"})
        self.assertEqual((s, d["error"]), (400, "protected"))
        s, d = req("POST", "/api/ops/move", {"paths": [os.path.join(FAKE, "D", "_시험")], "target": "D"})
        self.assertEqual((s, d["error"]), (400, "same_drive"))
        self.assertEqual(req("POST", "/api/ops/recycle", {"paths": []})[0], 400)
        self.assertEqual(req("POST", "/api/ops/empty_bin", {"drives": ["Q"]})[0], 400)
        self.assertEqual(req("POST", "/api/ops/undo", {"job_id": "nope"})[0], 404)

    def test_10_recycle(self):
        p = os.path.join(FAKE, "D", "_시험", "버릴폴더")
        before = drive_state("D")
        s, d = req("POST", "/api/ops/recycle", {"paths": [p]})
        self.assertEqual(s, 200, d)
        j = wait_job(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertFalse(j["undoable"])
        self.assertFalse(os.path.exists(p))
        binp = os.path.join(FAKE, "D", winapi.FAKE_BIN)
        self.assertTrue(any(n.endswith("버릴폴더") for n in os.listdir(binp)))
        after = drive_state("D")
        self.assertEqual(after["scanned_bytes"], before["scanned_bytes"] - 7000)
        self.assertEqual(after["bin_bytes"], before["bin_bytes"] + 7000)
        self.assertEqual(after["free"], before["free"])      # 휴지통을 비워야 공간이 생긴다
        with APP.lock:
            idx = APP.indexes["D"]
            self.assertEqual(model.check_invariants(idx), [])
            self.assertIsNone(model.find_dir(idx, p))
        # 휴지통 비우기 → 공간 확보
        s, d = req("POST", "/api/ops/empty_bin", {"drives": ["D"]})
        self.assertEqual((s, d["error"]), (400, "need_confirm"))      # 영구 삭제 — 확인 없이는 안 한다
        s, d = req("POST", "/api/ops/empty_bin", {"drives": ["D"], "confirm": True})
        self.assertEqual(s, 200)
        j = wait_job(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        emp = drive_state("D")
        self.assertEqual(emp["bin_bytes"], 0)
        self.assertEqual(emp["free"], after["free"] + after["bin_bytes"])

    def test_11_move_and_undo(self):
        src = os.path.join(FAKE, "D", "_시험", "옮길폴더")
        dst = os.path.join(FAKE, "H", "_시험", "옮길폴더")
        dbefore, hbefore = drive_state("D"), drive_state("H")
        s, pv = req("POST", "/api/ops/preview_move", {"paths": [src], "target": "H"})
        self.assertEqual(s, 200)
        self.assertEqual((pv["bytes"], pv["files"], pv["enough"]), (8000, 2, True))
        self.assertEqual(pv["dest_examples"], [dst])
        s, d = req("POST", "/api/ops/move", {"paths": [src], "target": "H", "dest_dir": None, "verify": "full"})
        self.assertEqual(s, 200, d)
        j = wait_job(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertTrue(j["undoable"])
        self.assertEqual(j["freed"], {"D": 8000})
        self.assertEqual(j["added"], {"H": 8000})
        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.isfile(os.path.join(dst, "sub", "b.bin")))
        da, ha = drive_state("D"), drive_state("H")
        self.assertEqual(da["scanned_bytes"], dbefore["scanned_bytes"] - 8000)
        self.assertEqual(ha["scanned_bytes"], hbefore["scanned_bytes"] + 8000)
        self.assertEqual(da["free"], dbefore["free"] + 8000)
        self.assertEqual(ha["free"], hbefore["free"] - 8000)
        with APP.lock:
            self.assertEqual(model.check_invariants(APP.indexes["D"]), [])
            self.assertEqual(model.check_invariants(APP.indexes["H"]), [])
            self.assertIsNone(model.find_dir(APP.indexes["D"], src))
            did = model.find_dir(APP.indexes["H"], dst)
            self.assertIsNotNone(did)
            self.assertEqual(APP.indexes["H"].d_size[did], 8000)
        # 저장된 인덱스도 갱신됐다
        from core import store
        h2 = store.load_index(APP._key(APP.drive("H")))
        self.assertIsNotNone(model.find_dir(h2, dst))
        # 되돌리기
        s, d = req("POST", "/api/ops/undo", {"job_id": j["id"]})
        self.assertEqual(s, 200, d)
        u = wait_job(d["job_id"])
        self.assertEqual(u["status"], "done", u)
        self.assertTrue(os.path.isfile(os.path.join(src, "a.txt")))
        self.assertFalse(os.path.exists(dst))
        self.assertEqual(drive_state("D")["scanned_bytes"], dbefore["scanned_bytes"])
        self.assertEqual(drive_state("H")["scanned_bytes"], hbefore["scanned_bytes"])
        self.assertEqual(req("POST", "/api/ops/undo", {"job_id": j["id"]})[0], 400)

    def test_12_sequence_move(self):
        # 스캔된 시퀀스(작은 실제 프레임)를 만들어 그룹 이동이 되는지
        sd = os.path.join(FAKE, "E", "_시험", "seq")
        os.makedirs(sd)
        # 시퀀스는 합계가 큰 파일 기준 이상이어야 행이 생긴다 → 기준을 1MB 로 낮춘다
        for i in range(1, 8):
            with open(os.path.join(sd, "f_%04d.png" % i), "wb") as f:
                f.write(b"x" * (200 << 10))
        s, d = req("POST", "/api/settings", {"big_file_mb": 1})
        self.assertEqual(s, 200)
        s, d = req("POST", "/api/rules/delete", {"path": "/none"})
        self.assertEqual(s, 404)
        s, d = req("POST", "/api/scan", {"drives": ["E"]})
        self.assertEqual(d["started"], ["E"])
        t0 = time.time()
        while time.time() - t0 < 30 and req("GET", "/api/state")[1]["busy"]:
            time.sleep(0.05)
        items = req("GET", "/api/items?drive=E&view=files&q=f_&limit=50")[1]["items"]
        seq = [it for it in items if it["kind"] == "seq" and it["name"] == "f_[####].png"]
        self.assertEqual(len(seq), 1, items)
        self.assertEqual(seq[0]["count"], 7)
        s, d = req("POST", "/api/ops/move", {"paths": [seq[0]["path"]], "target": "F", "verify": "quick"})
        self.assertEqual(s, 200, d)
        j = wait_job(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(os.listdir(sd), [])
        moved = os.path.join(FAKE, "F", "_시험", "seq")
        self.assertEqual(len(os.listdir(moved)), 7)
        with APP.lock:
            self.assertEqual(model.check_invariants(APP.indexes["E"]), [])
        req("POST", "/api/settings", {"big_file_mb": 50})

    def test_13_rules(self):
        p = os.path.join(FAKE, "H", "_시험")
        os.makedirs(os.path.join(p, "keep"), exist_ok=True)
        with open(os.path.join(p, "keep", "z.txt"), "wb") as f:
            f.write(b"z" * 4000)
        s, d = req("POST", "/api/scan", {"drives": ["H"]})
        t0 = time.time()
        while time.time() - t0 < 30 and req("GET", "/api/state")[1]["busy"]:
            time.sleep(0.05)
        s, d = req("POST", "/api/rules", {"path": p, "cat": 8})
        self.assertEqual(s, 200, d)
        self.assertTrue(any(r["cat"] == 8 for r in d["settings"]["rules"]))
        f = req("GET", "/api/items?drive=H&view=folders&dir=0")[1]
        it = next(x for x in f["items"] if x["name"] == "_시험")
        self.assertEqual(it["cat_bytes"][8], it["size"])
        s, d = req("POST", "/api/rules/delete", {"path": p})
        self.assertEqual(s, 200, d)
        self.assertEqual(d["settings"]["rules"], [])
        j = wait_job(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        f = req("GET", "/api/items?drive=H&view=folders&dir=0")[1]
        it = next(x for x in f["items"] if x["name"] == "_시험")
        self.assertEqual(it["cat_bytes"][8], 0)
        with APP.lock:
            self.assertEqual(model.check_invariants(APP.indexes["H"]), [])

    def test_14_restart_loads_cache(self):
        a2 = app_mod.App(start_queue=False)
        try:
            self.assertEqual(set(a2.indexes), set("CDEFGHI"))
            st = {x["letter"]: x for x in a2.drive_states()}
            self.assertTrue(st["D"]["scanned"])
            self.assertEqual(st["D"]["scanned_bytes"], drive_state("D")["scanned_bytes"])
        finally:
            a2.queue.stop(1.0)


if __name__ == "__main__":
    unittest.main()

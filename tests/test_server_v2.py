"""app.py v2 통합 시험 (SPEC-2): 실제 HTTP 서버를 가짜 드라이브(lead · member 프로필)에 띄워 새 API 를 확인한다.

장치·NAS(등록·공유 목록·연결 끊김·기록 삭제) · 복사·영구 삭제·no_bin · 프로젝트 · /media 관문과 Range ·
미리보기 · 팀 설정 · 업데이트 · open_url · 단일 실행 잠금.
"""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import app as app_mod                      # noqa: E402
import make_fake_drives                    # noqa: E402
from core import media, model, store, winapi   # noqa: E402

TOKEN = "test-token-v2"
NAS = make_fake_drives.NAS_UNC                 # \\ORICO\영상백업
NAS_KEY = winapi.network_key(NAS)              # N-0d2634
V2_DRIVE_KEYS = {"kind", "external", "has_bin", "scan", "connected", "unc"}
MEDIA_KEYS = {"path", "name", "size", "mtime", "cat", "ext", "playable", "poster_url", "video_url"}
PROJECT_KEYS = {"key", "drive", "did", "path", "name", "date", "date_source", "year", "month", "client",
                "client_raw", "title", "worker", "worker_name", "job_kind", "size", "cat_bytes", "files",
                "newest", "preview", "videos", "connected"}
HAVE_FFMPEG = all(media.find_ffmpeg())


class Env:
    """임시 데이터 폴더 + 가짜 드라이브 + 프로세스 안 서버 하나."""

    def __init__(self, profile, port, prep=None):
        self.tmp = tempfile.mkdtemp(prefix="db-v2-%s-" % profile)
        self._env = {k: os.environ.get(k) for k in ("DRIVEBOARD_HOME", "DRIVEBOARD_NO_UPDATE_CHECK",
                                                    "DRIVEBOARD_FAKE_ADOBE")}
        os.environ["DRIVEBOARD_HOME"] = os.path.join(self.tmp, "home")
        os.environ["DRIVEBOARD_NO_UPDATE_CHECK"] = "1"
        os.environ.pop("DRIVEBOARD_FAKE_ADOBE", None)
        self.fake = os.path.join(self.tmp, "fake")
        make_fake_drives.make(self.fake, scale="small", clean=True, profile=profile)
        if prep:
            prep(self)
        winapi.set_fake_root(self.fake)
        self.app = app_mod.App()
        self.app.open_url_fn = self._opened
        self.opened = []
        self.srv = app_mod.make_server(self.app, port, token=TOKEN)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _opened(self, u):
        self.opened.append(u)
        return True

    def p(self, *parts):
        return os.path.join(self.fake, *parts)

    def write(self, rel, n):
        p = self.p(*rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(os.urandom(n))
        return p

    def close(self):
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

    # ── HTTP ──
    def raw(self, method, path, body=None, headers=None, token=True, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": host or ("127.0.0.1:%d" % self.port)}
        if token:
            h["X-DB-Token"] = TOKEN
        if body is not None:
            body = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        return r.status, hdrs, data

    def req(self, method, path, body=None, **kw):
        s, _h, data = self.raw(method, path, body, **kw)
        try:
            return s, json.loads(data.decode("utf-8"))
        except ValueError:
            return s, data

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.req("POST", path, body if body is not None else {}, **kw)

    def state(self):
        return self.get("/api/state")[1]

    def drive(self, key):
        return next((x for x in self.state()["drives"] if x["letter"] == key), None)

    def wait_idle(self, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.state()
            if not st["busy"]:
                return st
            time.sleep(0.05)
        raise AssertionError("busy timeout")

    def scan(self, keys):
        s, d = self.post("/api/scan", {"drives": keys})
        assert s == 200, d
        self.wait_idle()
        return d

    def wait_job(self, jid, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            _s, d = self.get("/api/jobs")
            for j in d["jobs"]:
                if j["id"] == jid and j["status"] not in ("queued", "running"):
                    return j
            time.sleep(0.05)
        raise AssertionError("job timeout")

    def all_projects(self, **q):
        qs = "&".join("%s=%s" % (k, v) for k, v in q.items())
        s, d = self.get("/api/projects" + ("?" + qs if qs else ""))
        assert s == 200, d
        return [p for g in d["groups"] for p in g["projects"]], d


# ═════════════════════════════════════════════════════════════════ 팀원 PC (C + D)

class AMember(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.E = Env("member", 18920)

    @classmethod
    def tearDownClass(cls):
        cls.E.close()

    def test_member_pc(self):
        E = self.E
        st = E.state()
        self.assertEqual([d["letter"] for d in st["drives"]], ["C", "D"])
        for d in st["drives"]:
            self.assertTrue(V2_DRIVE_KEYS <= set(d))
            self.assertEqual((d["scan"], d["connected"], d["external"], d["has_bin"]), (True, True, False, True))
        self.assertEqual({d["letter"]: d["kind"] for d in st["drives"]}, {"C": "ssd", "D": "hdd"})
        self.assertIn("previews", st)
        self.assertIsNone(st["update"])
        d = E.scan("all")
        self.assertEqual(sorted(d["started"]), ["C", "D"])
        self.assertTrue(all(x["scanned"] for x in E.state()["drives"]))
        projs, body = E.all_projects()
        self.assertGreater(body["total"], 5)
        names = {p["name"] for p in projs}
        self.assertIn("260803_소담코스_녹차_진정크림_배너결합_KMS", names)
        self.assertIn("251023_한결증권_브랜드필름_편집_HYC", names)
        for p in projs:
            self.assertTrue(PROJECT_KEYS <= set(p), PROJECT_KEYS - set(p))
            self.assertIn(p["drive"], ("C", "D"))
            self.assertTrue(p["connected"])
        # 팀원 PC 에서도 같은 드라이브 안 복사는 폴더를 골라야 한다
        src = E.write("D/_시험/복사/a.txt", 1234)
        E.scan(["D"])
        s, r = E.post("/api/ops/move", {"paths": [os.path.dirname(src)], "target": "D", "mode": "copy"})
        self.assertEqual((s, r["error"]), (400, "need_dest"))
        dest = os.path.join(E.fake, "C", "작업")
        s, r = E.post("/api/ops/move", {"paths": [os.path.dirname(src)], "target": None, "dest_dir": dest,
                                        "mode": "copy"})
        self.assertEqual(s, 200, r)
        j = E.wait_job(r["job_id"])
        self.assertEqual((j["type"], j["status"]), ("copy", "done"), j)
        self.assertTrue(os.path.isfile(src))
        self.assertEqual(s, 200)
        s, r = E.get("/api/recommendations")
        self.assertEqual(s, 200)


# ═════════════════════════════════════════════════════════════════ 팀장 PC (내장 7 + 외장 J · USB K · NAS)

def _prep_lead(E):
    # J 에 시리얼을 박아 둔다 → 문자가 바뀌어도 같은 장치로 알아보는지 본다
    jm = os.path.join(E.fake, "J", "_drive.json")
    with open(jm, "r", encoding="utf-8") as f:
        m = json.load(f)
    m["serial"] = "5A5A0001"
    with open(jm, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    E.write("D/_시험/복사폴더/a.txt", 3000)
    E.write("D/_시험/복사폴더/sub/b.bin", 5000)
    E.write("D/_시험/백업/keep.txt", 10)
    E.write("D/_시험/info.txt", 77)
    E.write("K/_시험/지울폴더/c.txt", 7000)
    E.write("K/_시험/옮길.txt", 4096)
    E.write("N-0d2634/보관/readme.txt", 5)


class BLead(unittest.TestCase):
    """순서대로 돈다."""

    @classmethod
    def setUpClass(cls):
        cls.E = Env("lead", 18940, prep=_prep_lead)

    @classmethod
    def tearDownClass(cls):
        cls.E.close()

    # ── 드라이브 · NAS ──
    def test_01_drives(self):
        E = self.E
        st = E.state()
        ds = {d["letter"]: d for d in st["drives"]}
        self.assertEqual(list(ds), list("CDEFGHIJK"))       # NAS 는 등록 전엔 안 보인다
        self.assertEqual((ds["J"]["kind"], ds["J"]["external"], ds["J"]["has_bin"], ds["J"]["scan"]),
                         ("ssd", True, True, False))
        self.assertEqual((ds["K"]["kind"], ds["K"]["external"], ds["K"]["has_bin"], ds["K"]["scan"]),
                         ("usb", True, False, False))
        self.assertTrue(all(ds[L]["scan"] for L in "CDEFGHI"))
        d = E.scan("all")
        self.assertEqual(sorted(d["started"]), list("CDEFGHI"))   # 외장·USB 는 「모두 스캔」에서 빠진다
        st = E.state()
        self.assertFalse(any(x["scanned"] for x in st["drives"] if x["letter"] in "JK"))
        E.scan(["J", "k"])
        self.assertTrue(all(x["scanned"] for x in E.state()["drives"]))

    def test_02_shares_and_add(self):
        E = self.E
        s, d = E.post("/api/locations/shares", {"host": "\\\\ORICO"})
        self.assertEqual(s, 200, d)
        self.assertEqual(d["host"], "ORICO")
        self.assertEqual([(x["name"], x["unc"], x["key"], x["added"]) for x in d["shares"]],
                         [("영상백업", NAS, NAS_KEY, False)])
        s, d = E.post("/api/locations/shares", {"host": "NOPE"})
        self.assertEqual((s, d["error"]), (400, "no_shares"))
        self.assertEqual(E.post("/api/locations/shares", {"host": "a b"})[0], 400)
        # 형식 검사
        s, d = E.post("/api/locations/add", {"path": "\\\\ORICO"})
        self.assertEqual((s, d["error"], d["host"]), (400, "need_share", "ORICO"))
        for bad in ("C:\\x", "", "\\\\?\\C:\\x", "\\\\.\\pipe\\x", 5, "\\\\srv\\a\\..\\b"):
            s, d = E.post("/api/locations/add", {"path": bad})
            self.assertEqual(s, 400, bad)
        s, d = E.post("/api/locations/add", {"path": "//ORICO/영상백업/", "label": "NAS 백업"})
        self.assertEqual(s, 200, d)
        self.assertEqual((d["key"], d["unc"], d["connected"], d["label"]), (NAS_KEY, NAS, True, "NAS 백업"))
        self.assertEqual(d["settings"]["network_locations"], [{"path": NAS, "label": "NAS 백업"}])
        self.assertEqual(E.post("/api/locations/add", {"path": NAS})[1]["error"], "exists")
        n = E.drive(NAS_KEY)
        self.assertEqual((n["kind"], n["has_bin"], n["scan"], n["connected"], n["unc"], n["label"]),
                         ("network", False, False, True, NAS, "NAS 백업"))
        s, d = E.post("/api/locations/shares", {"host": "orico"})
        self.assertTrue(d["shares"][0]["added"])
        # 등록 안 된(꺼진) NAS 도 목록에 남는다
        s, d = E.post("/api/locations/add", {"path": "\\\\192.168.0.99\\없는공유"})
        self.assertEqual(s, 200, d)
        self.assertFalse(d["connected"])
        self.assertTrue(d["message"])
        off = E.drive(d["key"])
        self.assertEqual((off["connected"], off["scan"]), (False, False))
        s, r = E.post("/api/scan", {"drives": [d["key"]]})
        self.assertEqual(r["started"], [])
        self.assertEqual(r["skipped"][0]["drive"], d["key"])
        s, r = E.post("/api/locations/remove", {"key": d["key"].upper()})     # 대소문자 무관
        self.assertEqual(s, 200, r)
        self.assertIsNone(E.drive(d["key"]))
        self.assertEqual(E.post("/api/locations/remove", {"key": d["key"]})[0], 404)

    def test_03_scan_toggle_and_nas_scan(self):
        E = self.E
        s, d = E.post("/api/drives/scan_toggle", {"key": NAS_KEY, "scan": True})
        self.assertEqual(s, 200, d)
        self.assertTrue(d["drive"]["scan"])
        self.assertEqual(d["settings"]["drive_scan"], {NAS_KEY: True})
        self.assertEqual(E.post("/api/drives/scan_toggle", {"key": NAS_KEY, "scan": "yes"})[0], 400)
        self.assertEqual(E.post("/api/drives/scan_toggle", {"key": "Q", "scan": True})[0], 404)
        d = E.scan("all")
        self.assertIn(NAS_KEY, d["started"])
        n = E.drive(NAS_KEY)
        self.assertTrue(n["scanned"])
        self.assertGreater(n["files"], 10)
        self.assertEqual(n["bin_bytes"], 0)
        s, it = E.get("/api/items?drive=%s&view=folders" % NAS_KEY.upper())
        self.assertEqual(s, 200, it)
        self.assertIn("2024", [x["name"] for x in it["items"]])
        x = next(x for x in it["items"] if x["name"] == "2024")
        self.assertFalse(x["protected"])
        self.assertTrue(x["key"].startswith(NAS_KEY + ":"))
        self.assertTrue(os.path.isfile(store.index_path(E.app._key(E.app.drive(NAS_KEY)))))
        # 다시 끈다(이후 시험의 「모두 스캔」이 느려지지 않게)
        E.post("/api/drives/scan_toggle", {"key": NAS_KEY, "scan": False})

    # ── 휴지통 없음 · 영구 삭제 ──
    def test_04_no_bin_and_delete(self):
        E = self.E
        p = E.p("K", "_시험", "지울폴더")
        s, d = E.post("/api/ops/recycle", {"paths": [p]})
        self.assertEqual((s, d["error"]), (400, "no_bin"))
        self.assertIn("삭제(복구 불가)", d["message"])
        self.assertEqual(d["drives"], ["K"])
        self.assertTrue(os.path.isdir(p))
        s, d = E.post("/api/ops/delete", {"paths": [p]})
        self.assertEqual((s, d["error"]), (400, "need_confirm"))
        s, d = E.post("/api/ops/delete", {"paths": [p], "confirm": "true"})
        self.assertEqual((s, d["error"]), (400, "need_confirm"))
        for bad in (E.p("K"), "/etc/passwd", E.p("K", ".fakebin")):
            s, d = E.post("/api/ops/delete", {"paths": [bad], "confirm": True})
            self.assertEqual((s, d["error"]), (400, "protected"), bad)
        before = E.drive("K")
        s, d = E.post("/api/ops/delete", {"paths": [p], "confirm": True})
        self.assertEqual(s, 200, d)
        j = E.wait_job(d["job_id"])
        self.assertEqual((j["type"], j["status"]), ("delete", "done"), j)
        self.assertFalse(j["undoable"])
        self.assertFalse(os.path.exists(p))
        self.assertFalse(os.path.exists(E.p("K", ".fakebin")) and os.listdir(E.p("K", ".fakebin")))
        after = E.drive("K")
        self.assertEqual(after["scanned_bytes"], before["scanned_bytes"] - 7000)
        with E.app.lock:
            self.assertEqual(model.check_invariants(E.app.indexes["K"]), [])
        # 휴지통 있는 드라이브에서도 명시적으로 고르면 영구 삭제
        q = E.p("D", "_시험", "info.txt")
        s, d = E.post("/api/ops/delete", {"paths": [q], "confirm": True})
        self.assertEqual(E.wait_job(d["job_id"])["status"], "done")
        self.assertFalse(os.path.exists(q))
        self.assertFalse(any(n.endswith("info.txt") for n in os.listdir(E.p("D", ".fakebin"))))

    # ── 복사 ──
    def test_05_copy_and_undo(self):
        E = self.E
        src = E.p("D", "_시험", "복사폴더")
        dst = E.p("J", "_시험", "복사폴더")
        jb, db = E.drive("J"), E.drive("D")
        s, pv = E.post("/api/ops/preview_move", {"paths": [src], "target": "J", "mode": "copy"})
        self.assertEqual(s, 200, pv)
        self.assertEqual((pv["mode"], pv["bytes"], pv["files"], pv["freed"], pv["added"], pv["dest_examples"]),
                         ("copy", 8000, 2, {}, {"J": 8000}, [dst]))
        self.assertEqual(E.post("/api/ops/move", {"paths": [src], "target": "J", "mode": "clone"})[0], 400)
        s, d = E.post("/api/ops/move", {"paths": [src], "target": "J", "mode": "copy", "verify": "full"})
        self.assertEqual(s, 200, d)
        j = E.wait_job(d["job_id"])
        self.assertEqual((j["type"], j["status"], j["mode"]), ("copy", "done", "copy"), j)
        self.assertTrue(j["undoable"])
        self.assertEqual((j["freed"], j["added"]), ({}, {"J": 8000}))
        self.assertTrue(os.path.isfile(os.path.join(src, "sub", "b.bin")))       # 원본 그대로
        self.assertTrue(os.path.isfile(os.path.join(dst, "sub", "b.bin")))
        self.assertEqual(E.drive("D")["scanned_bytes"], db["scanned_bytes"])
        self.assertEqual(E.drive("J")["scanned_bytes"], jb["scanned_bytes"] + 8000)
        s, d = E.post("/api/ops/undo", {"job_id": j["id"]})
        self.assertEqual(s, 200, d)
        u = E.wait_job(d["job_id"])
        self.assertEqual(u["status"], "done", u)
        self.assertFalse(os.path.exists(dst))
        self.assertTrue(os.path.isfile(os.path.join(src, "a.txt")))
        self.assertEqual(E.drive("J")["scanned_bytes"], jb["scanned_bytes"])
        with E.app.lock:
            self.assertEqual(model.check_invariants(E.app.indexes["J"]), [])

    def test_06_copy_same_drive_and_folder_target(self):
        E = self.E
        src = E.p("D", "_시험", "복사폴더")
        s, d = E.post("/api/ops/move", {"paths": [src], "target": "D", "mode": "copy"})
        self.assertEqual((s, d["error"]), (400, "need_dest"))
        s, pv = E.post("/api/ops/preview_move", {"paths": [src], "target": "D", "mode": "copy"})
        self.assertTrue(pv["need_dest"])
        s, d = E.post("/api/ops/move", {"paths": [src], "target": "D"})
        self.assertEqual((s, d["error"]), (400, "same_drive"))
        self.assertEqual(E.post("/api/ops/move", {"paths": [src], "target": None})[1]["error"], "unknown_drive")
        bk = E.p("D", "_시험", "백업")
        s, d = E.post("/api/ops/move", {"paths": [src], "target": "", "dest_dir": bk, "mode": "copy"})
        self.assertEqual(s, 200, d)
        j = E.wait_job(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        self.assertTrue(os.path.isfile(os.path.join(bk, "복사폴더", "sub", "b.bin")))
        self.assertTrue(os.path.isfile(os.path.join(bk, "keep.txt")))
        # 자기 자신 안으로는 안 된다
        s, d = E.post("/api/ops/move", {"paths": [E.p("D", "_시험")], "dest_dir": bk, "mode": "copy"})
        self.assertEqual((s, d["error"]), (400, "invalid_dest"))
        # 모르는 위치
        s, d = E.post("/api/ops/move", {"paths": [src], "dest_dir": "/tmp", "mode": "copy"})
        self.assertEqual((s, d["error"]), (400, "invalid_dest"))
        # 폴더 대상 이동: K(USB) → NAS 의 고른 폴더
        f = E.p("K", "_시험", "옮길.txt")
        nas_dir = E.p("N-0d2634", "보관")
        s, d = E.post("/api/ops/move", {"paths": [f], "target": None, "dest_dir": nas_dir})
        self.assertEqual(s, 200, d)
        j = E.wait_job(d["job_id"])
        self.assertEqual((j["type"], j["status"]), ("move", "done"), j)
        self.assertFalse(os.path.exists(f))
        self.assertTrue(os.path.isfile(os.path.join(nas_dir, "옮길.txt")))
        # NAS 로 복사 → 되돌리기는 영구 삭제(휴지통 없음)
        s, d = E.post("/api/ops/move", {"paths": [src], "target": NAS_KEY, "mode": "copy"})
        self.assertEqual(s, 200, d)
        j = E.wait_job(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        ndst = E.p("N-0d2634", "_시험", "복사폴더")
        self.assertTrue(os.path.isdir(ndst))
        u = E.wait_job(E.post("/api/ops/undo", {"job_id": j["id"]})[1]["job_id"])
        self.assertEqual(u["status"], "done", u)
        self.assertTrue(u.get("permanent"))
        self.assertFalse(os.path.exists(ndst))
        self.assertTrue(os.path.isdir(src))

    # ── 프로젝트 ──
    def test_07_projects(self):
        E = self.E
        projs, body = E.all_projects()
        self.assertTrue({"ok", "total", "total_bytes", "groups", "clients", "workers", "years", "roots",
                         "scanned"} <= set(body))
        self.assertEqual(body["total"], len(projs))
        self.assertEqual(body["total_bytes"], sum(p["size"] for p in projs))
        for g in body["groups"]:
            self.assertTrue({"key", "title", "subtitle", "bytes", "count", "cat_bytes", "projects"} <= set(g))
            self.assertEqual(g["count"], len(g["projects"]))
        drives = {p["drive"] for p in projs}
        self.assertTrue({"D", "J", "K", NAS_KEY} <= drives, drives)
        by = {p["name"]: p for p in projs}
        p = by["260803_소담코스_녹차_진정크림_배너결합_KMS"]
        self.assertEqual((p["client"], p["worker"], p["date"], p["date_source"], p["year"], p["month"]),
                         ("소담코스", "KMS", "2026-08-03", "name", 2026, 8))
        self.assertEqual(p["title"], "녹차 진정크림 배너결합")
        self.assertNotIn("소담코스_진정크림", by)          # aep 없는 폴더 — 아직 프로젝트 아님
        for q in projs:
            self.assertTrue(PROJECT_KEYS <= set(q), PROJECT_KEYS - set(q))
            if q["preview"]:
                self.assertTrue(MEDIA_KEYS <= set(q["preview"]), q["preview"])
        # 광고주별: 용량 큰 순, 미분류는 맨 뒤
        titles = [g["title"] for g in body["groups"]]
        nonuns = [g["bytes"] for g in body["groups"] if g["title"] != "미분류"]
        self.assertEqual(nonuns, sorted(nonuns, reverse=True))
        if "미분류" in titles:
            self.assertEqual(titles[-1], "미분류")
        # 시간순·연월별
        _p, t = E.all_projects(group="time")
        self.assertTrue(all("년" in g["title"] and "월" in g["title"] for g in t["groups"]))
        keys = [g["key"] for g in t["groups"]]
        self.assertEqual(keys, sorted(keys, reverse=True))
        _p, m = E.all_projects(group="month", year=2026)
        self.assertEqual(m["year"], 2026)
        self.assertTrue(all(q["year"] == 2026 for q in _p))
        self.assertEqual([y["year"] for y in m["years"]], sorted({q["year"] for q in projs}, reverse=True))
        # 필터·검색·정렬
        g, r = E.all_projects(client="%EC%86%8C%EB%8B%B4%EC%BD%94%EC%8A%A4")           # 소담코스
        self.assertTrue(g and all(q["client"] == "소담코스" for q in g))
        g, r = E.all_projects(worker="kms")
        self.assertTrue(g and all(q["worker"] == "KMS" for q in g))
        g, r = E.all_projects(drive="d", sort="size")
        self.assertTrue(all(q["drive"] == "D" for q in g))
        g, r = E.all_projects(q="%EB%85%B9%EC%B0%A8")        # 녹차
        self.assertIn("260803_소담코스_녹차_진정크림_배너결합_KMS", {q["name"] for q in g})
        self.assertEqual(E.get("/api/projects?drive=%3F%3F")[0], 400)
        # 상세
        s, d = E.get("/api/project?key=" + p["key"])
        self.assertEqual(s, 200, d)
        self.assertEqual(d["project"]["key"], p["key"])
        self.assertEqual(d["cat_bytes"], p["cat_bytes"])
        self.assertTrue(d["children"])
        for c in d["children"]:
            self.assertTrue({"key", "kind", "path", "size", "protected"} <= set(c))
        self.assertTrue(d["videos"])
        for v in d["videos"]:
            self.assertTrue(MEDIA_KEYS <= set(v))
            self.assertIn(v["playable"], ("direct", "proxy", "none"))
        self.assertEqual(E.get("/api/project?key=D:999999")[0], 404)
        self.assertEqual(E.get("/api/project?key=nope")[0], 404)
        s, d2 = E.get("/api/project?path=" + _q(p["path"]))
        self.assertEqual((s, d2["project"]["key"]), (200, p["key"]))

    def test_08_project_settings(self):
        E = self.E
        projs, _b = E.all_projects()
        p = next(q for q in projs if q["name"] == "260803_소담코스_녹차_진정크림_배너결합_KMS")
        # 이 폴더는 프로젝트가 아님 → 사라지고, 되돌리면 돌아온다
        s, d = E.post("/api/projects/not_project", {"path": p["path"]})
        self.assertEqual(s, 200, d)
        self.assertIn(p["path"], d["settings"]["project_excluded"])
        self.assertNotIn(p["name"], {q["name"] for q in E.all_projects()[0]})
        s, d = E.post("/api/projects/not_project", {"path": p["path"], "remove": True})
        self.assertEqual(s, 200, d)
        self.assertIn(p["name"], {q["name"] for q in E.all_projects()[0]})
        self.assertEqual(E.post("/api/projects/not_project", {"path": "/no/such"})[0], 400)
        # 프로젝트 폴더 지정 → 직속 자식(aep 없는 소담코스_진정크림 포함)이 프로젝트가 된다
        root = os.path.dirname(p["path"])
        s, d = E.post("/api/projects/roots", {"add": root})
        self.assertEqual(s, 200, d)
        self.assertEqual(d["settings"]["project_roots"], [root])
        names = {q["name"] for q in E.all_projects()[0]}
        self.assertIn("소담코스_진정크림", names)
        s, d = E.post("/api/projects/roots", {"remove": root})
        self.assertEqual((s, d["settings"]["project_roots"]), (200, []))
        self.assertEqual(E.post("/api/projects/roots", {"remove": root})[0], 404)
        self.assertEqual(E.post("/api/projects/roots", {})[0], 400)
        self.assertEqual(E.post("/api/projects/roots", {"add": "/etc"})[0], 400)
        # 광고주 별칭은 바로 반영(인덱스를 다시 훑지 않는다)
        raw = next(q for q in E.all_projects()[0] if q["client_raw"] == "KIWOOM")
        s, d = E.post("/api/settings", {"client_aliases": {"kiwoom": "한결증권"},
                                        "workers": {"KMS": "가나다"}})
        self.assertEqual(s, 200, d)
        by = {q["key"]: q for q in E.all_projects()[0]}
        self.assertEqual(by[raw["key"]]["client"], "한결증권")
        self.assertEqual(by[p["key"]]["worker_name"], "가나다")
        _g, body = E.all_projects()
        self.assertIn({"initial": "KMS", "name": "가나다", "count": _count(_g, "KMS")}, body["workers"])
        E.post("/api/settings", {"client_aliases": {}, "workers": {}})

    # ── 미디어 ──
    def _video(self):
        projs, _b = self.E.all_projects()
        for p in projs:
            s, d = self.E.get("/api/project?key=" + p["key"])
            for v in d["videos"]:
                if v["video_url"] and v["size"] and v["size"] < (20 << 20):
                    return v
        self.skipTest("직접 재생되는 작은 영상이 가짜 드라이브에 없다")

    def test_09_media_serving(self):
        E = self.E
        v = self._video()
        url = v["video_url"]
        self.assertTrue(url.startswith("/media/"))
        with open(v["path"], "rb") as f:
            data = f.read()
        s, h, body = E.raw("GET", url, token=False)
        self.assertEqual((s, len(body)), (200, len(data)))
        self.assertEqual(body, data)
        self.assertEqual(h.get("Accept-Ranges"), "bytes")
        self.assertTrue(h.get("Content-Type", "").startswith("video/"))
        s, h, body = E.raw("GET", url, token=False, headers={"Range": "bytes=100-199",
                                                             "Sec-Fetch-Site": "same-origin"})
        self.assertEqual((s, body), (206, data[100:200]))
        self.assertEqual(h["Content-Range"], "bytes 100-199/%d" % len(data))
        s, h, body = E.raw("GET", url, token=False, headers={"Range": "bytes=-10"})
        self.assertEqual((s, body), (206, data[-10:]))
        s, h, body = E.raw("GET", url, token=False, headers={"Range": "bytes=%d-" % (len(data) + 5)})
        self.assertEqual(s, 416)
        s, h, body = E.raw("HEAD", url, token=False)
        self.assertEqual((s, body, h["Content-Length"]), (200, b"", str(len(data))))
        # 같은 경로는 같은 토큰(응답을 다시 만들어도 URL 이 그대로 → 브라우저 캐시가 산다)
        s, d = E.get("/api/project?path=" + _q(_project_of(E, v["path"])))
        self.assertIn(url, [x["video_url"] for x in d["videos"]])

    def test_10_media_gate(self):
        E = self.E
        v = self._video()
        url = v["video_url"]
        for sfs in ("cross-site", "same-site", "none"):
            self.assertEqual(E.raw("GET", url, token=False, headers={"Sec-Fetch-Site": sfs})[0], 403, sfs)
        self.assertEqual(E.raw("GET", url, token=False, host="evil.example:%d" % E.port)[0], 403)
        self.assertEqual(E.raw("GET", url, token=False, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(E.raw("GET", "/media/", token=False)[0], 404)
        self.assertEqual(E.raw("GET", "/media/AAAAAAAAAAAAAAAAAAAAAA", token=False)[0], 404)
        self.assertEqual(E.raw("GET", "/media/../app.py", token=False)[0], 404)
        self.assertEqual(E.raw("GET", "/media/a%2F..%2Fb", token=False)[0], 404)
        # 표에 있는 토큰이라도: 알려진 드라이브 밖 · 영상이 아닌 확장자 · 링크로 밖을 가리키는 파일은 거부
        outside = os.path.join(E.tmp, "outside.mp4")
        shutil.copyfile(v["path"], outside)
        t = E.app.media.issue(outside)
        self.assertEqual(E.raw("GET", "/media/" + t, token=False)[0], 403)
        txt = E.write("D/_시험/secret.txt", 50)
        self.assertEqual(E.raw("GET", "/media/" + E.app.media.issue(txt), token=False)[0], 403)
        link = E.p("D", "_시험", "link.mp4")
        os.symlink(outside, link)
        self.assertEqual(E.raw("GET", "/media/" + E.app.media.issue(link), token=False)[0], 403)
        gone = E.p("D", "_시험", "gone.mp4")
        self.assertEqual(E.raw("GET", "/media/" + E.app.media.issue(gone), token=False)[0], 403)
        self.assertEqual(E.raw("GET", url, token=False)[0], 200)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg 가 없다")
    def test_11_previews(self):
        E = self.E
        v = self._video()
        s, d = E.post("/api/media/poster", {"path": v["path"]})
        self.assertEqual(s, 200, d)
        self.assertIn(d["status"], ("queued", "ready"))
        self.assertIn(d["playable"], ("direct", "proxy"))
        self.assertTrue(E.app.previews.wait_idle(60))
        s, d = E.post("/api/media/poster", {"path": v["path"]})
        self.assertEqual(d["status"], "ready", d)
        self.assertGreater(d.get("duration") or 0, 0)
        s, h, body = E.raw("GET", d["url"], token=False)
        self.assertEqual((s, h["Content-Type"], body[:2]), (200, "image/jpeg", b"\xff\xd8"))
        st = E.state()["previews"]
        self.assertTrue(st["ffmpeg"])
        self.assertIn(v["path"], [x["path"] for x in st["done"]])
        self.assertGreater(st["version"], 0)
        # 목록·상세의 <media> 에 포스터가 실린다
        s, d2 = E.get("/api/project?path=" + _q(_project_of(E, v["path"])))
        vv = next(x for x in d2["videos"] if x["path"] == v["path"])
        self.assertTrue(vv["poster_url"])
        # 프록시
        s, d = E.post("/api/media/proxy", {"path": v["path"]})
        self.assertEqual(s, 200, d)
        self.assertTrue(E.app.previews.wait_idle(120))
        s, d = E.post("/api/media/proxy", {"path": v["path"]})
        self.assertEqual(d["status"], "ready", d)
        self.assertEqual(E.raw("GET", d["url"], token=False, headers={"Range": "bytes=0-9"})[0], 206)
        # 잘못된 요청
        self.assertEqual(E.post("/api/media/poster", {"path": "/etc/passwd"})[0], 400)
        self.assertEqual(E.post("/api/media/poster", {"path": E.p("D", "_시험", "x.mp4")})[0], 400)
        self.assertEqual(E.post("/api/media/poster", {})[0], 400)
        # 캐시 비우기
        s, d = E.post("/api/media/clear_cache")
        self.assertEqual(s, 200, d)
        self.assertGreaterEqual(d["removed"], 2)
        self.assertEqual(E.state()["previews"]["count"], 0)

    # ── 연결 안 된 장치 ──
    def test_12_disconnected_and_forget(self):
        E = self.E
        jb = E.drive("J")
        self.assertTrue(jb["scanned"])
        jproj = [p for p in E.all_projects()[0] if p["drive"] == "J"]
        self.assertTrue(jproj)
        # 다른 문자로 다시 꽂기 → 시리얼로 같은 장치
        os.rename(E.p("J"), E.p("L"))
        E.app.invalidate()
        L = E.drive("L")
        self.assertIsNone(E.drive("J"))
        self.assertTrue(L["connected"] and L["scanned"], L)
        self.assertEqual(L["scanned_bytes"], jb["scanned_bytes"])
        s, it = E.get("/api/items?drive=L&view=folders")
        self.assertEqual(s, 200)
        self.assertTrue(all(x["path"].startswith(E.p("L")) for x in it["items"]))
        self.assertTrue({p["name"] for p in E.all_projects()[0] if p["drive"] == "L"}
                        >= {p["name"] for p in jproj})
        # 빼기 → 회색 카드(마지막 숫자 + 조작 불가)
        away = os.path.join(E.tmp, "unplugged")
        os.rename(E.p("L"), away)
        E.app.invalidate()
        off = E.drive("L")
        self.assertIsNotNone(off)
        self.assertEqual((off["connected"], off["scan"], off["scanned"], off["kind"], off["external"]),
                         (False, False, True, "ssd", True))
        self.assertEqual(off["total"], jb["total"])
        self.assertGreater(off["last_seen"], 0)
        s, it = E.get("/api/items?drive=L&view=files&limit=20")
        self.assertEqual(s, 200, it)
        self.assertTrue(it["items"])
        self.assertTrue(all(x["protected"] for x in it["items"]))
        self.assertEqual(it["items"][0]["protected_reason"], app_mod.R_OFFLINE)
        self.assertEqual(E.get("/api/tree?drive=L")[1]["connected"], False)
        offp = [p for p in E.all_projects()[0] if p["drive"] == "L"]
        self.assertTrue(offp and not any(p["connected"] for p in offp))
        self.assertTrue(all(p["preview"] is None or p["preview"]["video_url"] is None for p in offp))
        s, r = E.post("/api/scan", {"drives": ["L"]})
        self.assertEqual((r["started"], r["skipped"][0]["drive"]), ([], "L"))
        self.assertNotIn("L", {x["drive"] for x in E.get("/api/items?drive=*&view=folders")[1]["items"]})
        # 같은 문자를 다른 장치가 쓰면 회색 카드는 '~시리얼' 키로 비켜 선다
        make_fake_drives.write_tree(E.p("L"), {"x/y.bin": 100})
        with open(E.p("L", "_drive.json"), "w", encoding="utf-8") as f:
            json.dump({"label": "다른 USB", "kind": "usb", "total": 1 << 34, "serial": "77770000"}, f)
        E.app.invalidate()
        ds = {d["letter"]: d for d in E.state()["drives"]}
        self.assertTrue(ds["L"]["connected"])
        self.assertFalse(ds["L"]["scanned"])
        self.assertIn("~5A5A0001", ds)
        self.assertEqual(ds["~5A5A0001"]["last_letter"], "L")
        self.assertFalse(ds["~5A5A0001"]["connected"])
        self.assertEqual(E.get("/api/items?drive=~5a5a0001&view=folders")[0], 200)
        # 기록 삭제
        self.assertEqual(E.post("/api/drives/forget", {"key": "L"})[1]["error"], "connected")
        self.assertEqual(E.post("/api/drives/forget", {"key": "Q"})[0], 404)
        s, d = E.post("/api/drives/forget", {"key": "~5a5a0001"})
        self.assertEqual(s, 200, d)
        self.assertNotIn("~5A5A0001", {d["letter"] for d in E.state()["drives"]})
        self.assertIsNone(store.load_index("5A5A0001"))
        self.assertFalse(any(p["drive"] == "~5A5A0001" for p in E.all_projects()[0]))
        shutil.rmtree(E.p("L"))
        E.app.invalidate()

    def test_13_nas_offline_and_remove(self):
        E = self.E
        n = E.drive(NAS_KEY)
        self.assertTrue(n["scanned"])
        clip = E.write("N-0d2634/보관/clip.mp4", 2048)
        murl = "/media/" + E.app.media.issue(clip)
        self.assertEqual(E.raw("GET", murl, token=False)[0], 200)
        hidden = E.p("_nas_off")
        os.rename(E.p("N-0d2634"), hidden)              # NAS 꺼짐
        E.app.invalidate()
        self.assertEqual(E.raw("GET", murl, token=False)[0], 403)      # 연결 끊긴 위치는 서빙하지 않는다
        off = E.drive(NAS_KEY)
        self.assertEqual((off["connected"], off["scanned"], off["kind"]), (False, True, "network"))
        self.assertEqual(off["total"], n["total"])      # 마지막으로 본 숫자
        s, d = E.post("/api/ops/move", {"paths": [E.p("D", "_시험", "복사폴더")], "target": NAS_KEY,
                                        "mode": "copy"})
        self.assertEqual((s, d["error"]), (400, "unknown_drive"))
        os.rename(hidden, E.p("N-0d2634"))
        E.app.invalidate()
        self.assertTrue(E.drive(NAS_KEY)["connected"])
        sk = E.app._key(E.app.drive(NAS_KEY))
        s, d = E.post("/api/locations/remove", {"key": NAS_KEY})
        self.assertEqual(s, 200, d)
        self.assertEqual(d["settings"]["network_locations"], [])
        self.assertIsNone(E.drive(NAS_KEY))
        self.assertIsNone(store.load_index(sk))          # 저장된 기록도 지운다(파일은 그대로)
        self.assertTrue(os.path.isdir(E.p("N-0d2634", "2024")))

    # ── 팀 설정 · 업데이트 · 기타 ──
    def test_14_team(self):
        E = self.E
        E.post("/api/settings", {"workers": {"KMS": "가나다"}, "client_aliases": {"한결": "한결증권"}})
        s, h, body = E.raw("GET", "/api/team/export")
        self.assertEqual(s, 200)
        self.assertIn("attachment", h.get("Content-Disposition", ""))
        exp = json.loads(body)
        self.assertEqual((exp["format"], exp["version"]), ("driveboard-team", 1))
        self.assertEqual(exp["workers"], {"KMS": "가나다"})
        self.assertNotIn("project_roots", exp)
        E.post("/api/settings", {"workers": {}, "client_aliases": {}})
        team = dict(exp)
        team["workers"] = {"KMS": "가나다", "HYC": "라마바"}
        team["rules"] = [{"path": E.p("D", "_시험"), "cat": 8}, {"path": "X:\\없는\\폴더", "cat": 1}]
        s, d = E.post("/api/team/import", {"data": team})
        self.assertEqual(s, 200, d)
        self.assertFalse(d["applied"])
        self.assertEqual(set(d["diff"]["workers"]["added"]), {"KMS", "HYC"})
        self.assertEqual(d["diff"]["rules"]["missing_paths"], 1)
        self.assertEqual(E.get("/api/settings")[1]["settings"]["workers"], {})      # 미리보기는 저장 안 함
        s, d = E.post("/api/team/import", {"data": json.dumps(team), "apply": True})
        self.assertEqual(s, 200, d)
        self.assertTrue(d["applied"])
        st = E.get("/api/settings")[1]["settings"]
        self.assertEqual(st["workers"], {"KMS": "가나다", "HYC": "라마바"})
        self.assertEqual(st["client_aliases"], {"한결": "한결증권"})
        self.assertEqual(len(st["rules"]), 2)
        f = E.get("/api/items?drive=D&view=folders&dir=0")[1]
        it = next(x for x in f["items"] if x["name"] == "_시험")
        self.assertEqual(it["cat_bytes"][8], it["size"])          # 규칙이 인덱스에 바로 반영
        # 왕복: 내보낸 것을 다시 가져오면 바뀌는 것이 없다
        exp2 = json.loads(E.raw("GET", "/api/team/export")[2])
        d = E.post("/api/team/import", {"data": exp2})[1]
        self.assertEqual((d["diff"]["workers"]["added"], d["diff"]["workers"]["changed"]), ({}, {}))
        self.assertEqual(d["diff"]["rules"]["added"], [])
        for bad in ({"format": "x"}, "{nope", {"format": "driveboard-team", "version": 99}, 5):
            s, d = E.post("/api/team/import", {"data": bad})
            self.assertEqual((s, d["error"]), (400, "bad_team_file"), bad)
        E.post("/api/rules/delete", {"path": E.p("D", "_시험")})
        E.post("/api/rules/delete", {"path": "X:\\없는\\폴더"})
        E.post("/api/settings", {"workers": {}, "client_aliases": {}})
        E.wait_idle()

    def test_15_update_and_open_url(self):
        E = self.E
        self.assertIsNone(E.state()["update"])
        api = _FakeGitHub({"tag_name": "v9.9.9", "html_url":
                           "https://github.com/minsk999/driveboard/releases/tag/v9.9.9", "body": "x"})
        try:
            with mock.patch.dict(os.environ, {"DRIVEBOARD_NO_UPDATE_CHECK": ""}):
                c = E.app.start_update_check(base_url=api.base)
                self.assertTrue(c.wait(10))
        finally:
            api.close()
        self.assertEqual(E.state()["update"], {"latest": "9.9.9", "newer": True,
                                               "url": "https://github.com/minsk999/driveboard/releases/tag/v9.9.9"})
        # 설정에서 끄면 알약이 바로 내려가고, 꺼진 채로는 조회하지 않는다
        s, d = E.post("/api/settings", {"update_check": False})
        self.assertEqual((s, d["settings"]["update_check"]), (200, False))
        self.assertIsNone(E.state()["update"])
        with mock.patch.dict(os.environ, {"DRIVEBOARD_NO_UPDATE_CHECK": ""}):
            c = E.app.start_update_check(base_url="http://127.0.0.1:1")
            self.assertTrue(c.wait(5))
            self.assertIsNone(c.result)
        self.assertIsNone(E.state()["update"])
        E.post("/api/settings", {"update_check": True})
        ok = ["https://github.com/minsk999/driveboard/releases/tag/v1.1.0",
              "https://github.com/minsk999/driveboard", "https://github.com/minsk999/driveboard/releases/latest"]
        for u in ok:
            s, d = E.post("/api/open_url", {"url": u})
            self.assertEqual((s, d["opened"]), (200, True), u)
        self.assertEqual(E.opened, ok)
        for u in ("http://github.com/minsk999/driveboard", "https://github.com/minsk999/driveboardX",
                  "https://github.com.evil/minsk999/driveboard/", "https://evil@github.com/minsk999/driveboard",
                  "https://github.com:444/minsk999/driveboard", "https://github.com/minsk999/other",
                  "file:///C:/Windows", "javascript:alert(1)", "https://github.com/minsk999/driveboard/\\..",
                  "https://github.com/minsk999/driveboard/a b", 5, None):
            s, d = E.post("/api/open_url", {"url": u})
            self.assertEqual((s, d["error"]), (400, "forbidden_url"), u)
        self.assertEqual(len(E.opened), len(ok))

    def test_16_pick_folder_and_meta(self):
        E = self.E
        s, d = E.post("/api/pick_folder")
        self.assertEqual((s, d["error"]), (409, "no_dialog"))
        seen = []

        class W:
            def create_file_dialog(self, kind, **kw):
                seen.append((kind, kw))
                return (E.p("D", "_시험"),)
        E.app.window = W()
        try:
            s, d = E.post("/api/pick_folder", {"initial": E.p("D")})
            self.assertEqual(s, 200, d)
            self.assertEqual((d["path"], d["drive"], d["known"]), (E.p("D", "_시험"), "D", True))
            self.assertEqual(seen[0][1], {"directory": E.p("D")})
            W.create_file_dialog = lambda self, kind, **kw: None
            s, d = E.post("/api/pick_folder")
            self.assertEqual((d["path"], d["canceled"]), (None, True))
            m = E.get("/api/meta")[1]
            self.assertTrue(m["dialog"])
            self.assertEqual(m["version"], app_mod.version_mod.VERSION)
            self.assertIn("ffmpeg", m)
        finally:
            E.app.window = None

    def test_17_single_instance(self):
        E = self.E
        self.assertIsNone(app_mod.find_running())
        app_mod.write_instance_lock(E.srv)
        try:
            r = app_mod.find_running()
            self.assertEqual((r["app"], r["window"], r["url"]), ("DriveBoard", False,
                                                                  "http://127.0.0.1:%d/" % E.port))
        finally:
            app_mod.remove_instance_lock(E.srv)
        self.assertFalse(os.path.exists(app_mod.lock_path()))
        # 비밀값이 틀리거나 브라우저(Sec-Fetch-Site)에서 오면 거부
        self.assertEqual(E.raw("GET", "/__driveboard/focus", token=False)[0], 403)
        self.assertEqual(E.raw("GET", "/__driveboard/focus", token=False,
                               headers={"X-DB-Instance": E.srv.instance_secret,
                                        "Sec-Fetch-Site": "same-origin"})[0], 403)
        # 죽은 포트를 가리키는 잠금 파일 → 없는 것으로
        store.atomic_write(app_mod.lock_path(), json.dumps({"port": 1, "secret": "x"}).encode())
        self.assertIsNone(app_mod.find_running(timeout=1))
        os.unlink(app_mod.lock_path())

    def test_18_restart_keeps_devices(self):
        E = self.E
        a2 = app_mod.App(start_queue=False)
        try:
            self.assertTrue(set("CDEFGHIK") <= set(a2.indexes))
            st = {x["letter"]: x for x in a2.drive_states()}
            self.assertTrue(st["K"]["scanned"])
            self.assertEqual(st["K"]["scanned_bytes"], E.drive("K")["scanned_bytes"])
        finally:
            a2.queue.stop(1.0)
            a2.previews.close()


def _count(projs, initial):
    return sum(1 for p in projs if p["worker"] == initial)


def _q(s):
    from urllib.parse import quote
    return quote(s, safe="")


def _project_of(E, path):
    for p in E.all_projects()[0]:
        if path.startswith(p["path"] + os.sep):
            return p["path"]
    raise AssertionError("project of %s" % path)


class _FakeGitHub:
    def __init__(self, payload):
        body = json.dumps(payload).encode("utf-8")

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


if __name__ == "__main__":
    unittest.main()

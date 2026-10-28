"""1.0.0 검수 후속 — 창 닫기 확인 · 자기 PC 공유 등록 거부 · 영구 삭제 확인 요구 · 창 스택 자가 진단 · 같은 공유 표시."""
import contextlib
import io
import os
import socket
import sys
import threading
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import tempfile  # noqa: E402
if os.name == "nt":      # 임시 폴더를 긴 이름으로(…\RUNNER~1\… → …\runneradmin\…): 제품이 경로를 긴 이름으로
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())     # 돌려주므로 시험의 기대값도 같은 모양이어야 한다

import app as app_mod                                   # noqa: E402
from core import ops, safety, store, winapi                # noqa: E402
from test_server_fixes import Base, MB, _sparse, _write  # noqa: E402


class FakeWindow:
    def __init__(self, js_result=True, js_raises=False):
        self.js = []
        self.destroyed = threading.Event()
        self.js_result, self.js_raises = js_result, js_raises

    def evaluate_js(self, code):
        self.js.append(code)
        if self.js_raises:
            raise RuntimeError("no page")
        return self.js_result

    def destroy(self):
        self.destroyed.set()


class TestWindowClose(Base):
    def busy_job(self):
        gate = threading.Event()
        jid = self.app.queue.submit_task("rescan", "분석", lambda ctx: gate.wait(10))
        return gate, jid

    def test_idle_closes(self):
        self.app.window = FakeWindow()
        self.assertTrue(self.app.window_closing())
        self.assertEqual(self.app.window.js, [])

    def test_analysis_task_does_not_block_close(self):
        gate, _jid = self.busy_job()
        try:
            self.app.window = FakeWindow()
            self.assertFalse(self.app.close_state()["busy"])     # 다시 스캔·중복 확인은 멈춰도 잃는 것이 없다
            self.assertTrue(self.app.window_closing())
        finally:
            gate.set()

    def setUp(self):
        super().setUp()
        real = ops._write

        def slow(f, data):                       # 이동이 시험 동안 끝나지 않게(64KiB 마다 20ms)
            time.sleep(0.02)
            return real(f, data)
        p = mock.patch.object(ops, "_write", slow)
        p.start()
        self.addCleanup(p.stop)
        self.app.queue.chunk_size = 64 * 1024

    def _move_running(self):
        """오래 걸리는 이동 하나를 돌린다."""
        src = _write(self.P("D", "big", "a.mov"), os.urandom(8 * MB))
        jid = self.app.queue.submit_move([src], "E")
        t0 = time.time()
        while time.time() - t0 < 10:
            j = self.app.queue.get(jid)
            if j["status"] == "running":
                return jid
            time.sleep(0.01)
        self.fail("이동이 시작하지 않았어요")

    def test_busy_move_asks_ui_and_keeps_window(self):
        w = self.app.window = FakeWindow(js_result=True)
        jid = self._move_running()
        st = self.app.close_state()
        self.assertTrue(st["busy"])
        self.assertEqual([j["id"] for j in st["jobs"]], [jid])
        self.assertFalse(self.app.window_closing())              # 지금은 닫지 않는다
        t0 = time.time()
        while not w.js and time.time() - t0 < 5:
            time.sleep(0.01)
        self.assertTrue(w.js and "askClose" in w.js[0], w.js)
        self.assertIn(jid, w.js[0])
        time.sleep(0.2)
        self.assertFalse(w.destroyed.is_set(), "화면이 물어봤는데 창을 닫았다")
        # 화면의 [작업을 멈추고 닫기]
        r = self.app.request_close()
        self.assertTrue(r["ok"])
        self.assertTrue(w.destroyed.wait(15))
        self.assertTrue(self.app.window_closing())                # 한 번 허락하면 그대로 닫힌다
        j = self.app.queue.get(jid)
        self.assertIn(j["status"], ("canceled", "partial", "failed", "done"), j)
        self.assertTrue(os.path.exists(self.P("D", "big", "a.mov")), "멈춘 이동이 원본을 지웠다")

    def test_cannot_ask_fails_open(self):
        w = self.app.window = FakeWindow(js_raises=True)
        self._move_running()
        self.assertFalse(self.app.window_closing())
        self.assertTrue(w.destroyed.wait(15), "물어보지 못했는데 창을 가뒀다")

    def test_close_endpoint_needs_window(self):
        self.serve()
        s, d = self.req("POST", "/api/app/close", {})
        self.assertEqual((s, d["error"]), (400, "no_window"))
        self.app.window = w = FakeWindow()
        s, d = self.req("POST", "/api/app/close", {})
        self.assertEqual(s, 200, d)
        self.assertTrue(w.destroyed.wait(10))


class TestLocalShareRefused(Base):
    def test_add_location_rejects_own_pc(self):
        self.serve()
        host = socket.gethostname()
        for unc in ("\\\\localhost\\C$", "\\\\127.0.0.1\\share", "\\\\%s\\Users" % host,
                    "//LOCALHOST/d$/proj"):
            with self.subTest(unc=unc):
                s, d = self.req("POST", "/api/locations/add", {"path": unc})
                self.assertEqual((s, d["error"]), (400, "local_share"), d)
                self.assertEqual(d["message"], safety.R_LOCAL_SHARE)
        self.assertEqual(self.app.settings.get("network_locations"), [])
        s, d = self.req("POST", "/api/settings", {"network_locations": [{"path": "\\\\localhost\\c$"}]})
        self.assertEqual((s, d["error"]), (400, "local_share"), d)
        self.assertEqual(self.app.settings.get("network_locations"), [])
        # 진짜 NAS 는 그대로 받는다(관리 공유 이름 $ 도)
        s, d = self.req("POST", "/api/locations/add", {"path": "\\\\ORICO\\video$"})
        self.assertEqual(s, 200, d)


class TestPermanentNeedsConfirm(Base):
    DRIVES = {"C": {"kind": "ssd"}, "D": {"kind": "hdd"}, "U": {"kind": "usb", "has_bin": False}}

    def test_empty_bin_and_copy_undo_need_confirm(self):
        self.serve()
        s, d = self.req("POST", "/api/ops/empty_bin", {"drives": ["D"]})
        self.assertEqual((s, d["error"]), (400, "need_confirm"))
        s, d = self.req("POST", "/api/ops/empty_bin", {"drives": ["D"], "confirm": True})
        self.assertEqual(s, 200, d)
        src = _write(self.P("D", "p", "a.mov"), b"x" * 1000)
        s, d = self.req("POST", "/api/ops/copy", {"paths": [src], "target": "U"})
        self.assertEqual(s, 200, d)
        j = self.wait(d["job_id"])
        self.assertEqual(j["status"], "done", j)
        dst = self.P("U", "p", "a.mov")
        self.assertTrue(os.path.isfile(dst))
        s, d = self.req("POST", "/api/ops/undo", {"job_id": j["id"]})
        self.assertEqual((s, d["error"], d.get("permanent")), (400, "need_confirm", True), d)
        self.assertTrue(os.path.isfile(dst))
        self.assertTrue(self.app.queue.get(j["id"])["undoable"])
        s, d = self.req("POST", "/api/ops/undo", {"job_id": j["id"], "confirm": True})
        self.assertEqual(s, 200, d)
        self.assertEqual(self.wait(d["job_id"])["status"], "done")
        self.assertFalse(os.path.exists(dst))
        self.assertTrue(os.path.isfile(src))

    def test_delete_job_carries_confirmed_amount(self):
        top = self.P("U", "Grew")
        _write(os.path.join(top, "a.mov"), b"a" * 1000)
        self.scan("U")
        caps = self.app.check_growth(self.app.expand_items([top]))
        self.assertEqual(caps, {top: (1000, 1)})
        # 확인 뒤, 작업이 시작되기 전에 파일이 늘었다 → 작업이 그 항목을 지우지 않는다
        _write(os.path.join(top, "late.mov"), b"b" * 10)
        j = self.wait(self.app.queue.submit_delete([top], limits=caps))
        self.assertNotEqual(j["status"], "done", j)
        self.assertTrue(os.path.isfile(os.path.join(top, "a.mov")))


class TestSelftestGui(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get("DRIVEBOARD_HOME")
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="db-stg-")
        os.environ["DRIVEBOARD_HOME"] = self.tmp

    def tearDown(self):
        import shutil
        if self._home is None:
            os.environ.pop("DRIVEBOARD_HOME", None)
        else:
            os.environ["DRIVEBOARD_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log(self):
        with open(os.path.join(self.tmp, "selftest-gui.log"), encoding="utf-8") as f:
            return f.read()

    def test_missing_stack_fails_without_server(self):
        with mock.patch.object(app_mod, "make_server", side_effect=AssertionError("서버를 띄웠다")), \
                mock.patch.dict(sys.modules, {"webview.platforms.winforms": None}):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = app_mod.main(["--selftest-gui"])
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", self.log())

    def test_full_stack_passes(self):
        added = []
        clr = types.SimpleNamespace(AddReference=added.append)
        wv = types.ModuleType("webview")
        plats = types.ModuleType("webview.platforms")
        mods = {"webview": wv, "webview.platforms": plats,
                "webview.platforms.winforms": types.ModuleType("webview.platforms.winforms"),
                "webview.platforms.edgechromium": types.ModuleType("webview.platforms.edgechromium"),
                "clr": clr}
        wv.platforms = plats
        plats.winforms = mods["webview.platforms.winforms"]
        plats.edgechromium = mods["webview.platforms.edgechromium"]
        with mock.patch.object(app_mod, "make_server", side_effect=AssertionError("서버를 띄웠다")), \
                mock.patch.dict(sys.modules, mods):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = app_mod.main(["--selftest-gui"])
        self.assertEqual(rc, 0, self.log())
        self.assertEqual(added, ["Microsoft.Web.WebView2.Core"])
        self.assertIn("OK", self.log())


class TestSameShareShown(Base):
    DRIVES = {"C": {"kind": "ssd"},
              "Z": {"kind": "network", "unc": "\\\\ORICO\\share"},
              "N-x": {"kind": "network", "unc": "\\\\ORICO\\share"}}
    SETTINGS = {"network_locations": [{"path": "\\\\ORICO\\share"}]}

    def test_fake_backend_marks_same_as_and_state_shows_twin(self):
        raw = winapi.list_drives(network_locations=[{"path": "\\\\ORICO\\share"}])
        net = [d for d in raw if d["letter"] != "Z" and d.get("unc")]
        self.assertEqual([d.get("same_as") for d in net], ["Z"])
        z = next(s for s in self.app.drive_states() if s["letter"] == "Z")
        self.assertEqual(z["also_as"], [winapi.network_key("\\\\ORICO\\share")])
        c = next(s for s in self.app.drive_states() if s["letter"] == "C")
        self.assertEqual(c["also_as"], [])


if __name__ == "__main__":
    unittest.main()

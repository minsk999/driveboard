"""ops.JobQueue 테스트 — 가짜 드라이브(작은 실제 파일)로 이동·휴지통·되돌리기·기록을 검사한다."""
import errno
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.name == "nt":      # 임시 폴더를 긴 이름으로(…\RUNNER~1\… → …\runneradmin\…): 제품이 경로를 긴 이름으로
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())     # 돌려주므로 시험의 기대값도 같은 모양이어야 한다

from core import ops, safety, winapi  # noqa: E402

GiB = 1 << 30
MiB = 1 << 20
OLD = 1577836800  # 2020-01-01


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def mkfile(path, size, mtime=None, seed=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = (hashlib.sha256(("%s:%d" % (os.path.basename(path), seed)).encode()).digest() * (size // 32 + 1))[:size]
    with open(path, "wb") as f:
        f.write(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _rel(p, top):
    return os.path.relpath(p, top).replace(os.sep, "/")      # 키는 OS 와 무관하게 'sub/a.bin'


def tree(top):
    """{상대경로('/' 구분): ('d',) | ('f', size, sha)}"""
    out = {}
    for dp, dns, fns in os.walk(top):
        for d in dns:
            out[_rel(os.path.join(dp, d), top)] = ("d",)
        for f in fns:
            p = os.path.join(dp, f)
            out[_rel(p, top)] = ("f", os.path.getsize(p), sha(p))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db-ops-")
        self.root = os.path.join(self.tmp, "fake")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self._env = {k: os.environ.get(k) for k in ("DRIVEBOARD_HOME", "DRIVEBOARD_FAKE_ADOBE")}
        os.environ["DRIVEBOARD_HOME"] = self.home
        os.environ.pop("DRIVEBOARD_FAKE_ADOBE", None)
        self._prev_root = winapi.get_fake_root()
        self.mk("C", 100 * GiB, "ssd")
        self.mk("D", 100 * GiB, "hdd")
        self.mk("E", 1 * GiB, "hdd")
        winapi.set_fake_root(self.root)
        self.C, self.D, self.E = (os.path.join(self.root, x) for x in "CDE")
        self.calls = []
        self.hooks = {
            "on_removed": lambda p: self.calls.append(("removed", p)),
            "on_added": lambda p: self.calls.append(("added", p)),
            "on_changed": lambda p: self.calls.append(("changed", p)),
            "on_job_done": lambda j: self.calls.append(("done", j)),
        }
        self.q = self.make_queue()
        self.q.start()

    def tearDown(self):
        self.q.stop(5)
        winapi.set_fake_root(self._prev_root)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk(self, letter, total, kind):
        d = os.path.join(self.root, letter)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_drive.json"), "w") as f:
            json.dump({"label": "%s%s" % (kind.upper(), letter), "kind": kind, "total": total}, f)

    def make_queue(self, hooks=None):
        drives = winapi.list_drives
        return ops.JobQueue(self.home, drives, lambda p: safety.check(p, drives()),
                            hooks=self.hooks if hooks is None else hooks)

    def wait(self, jid, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self.q.get(jid)
            if j and j["status"] not in ("queued", "running"):
                return j
            time.sleep(0.02)
        self.fail("작업이 끝나지 않았어요: %s" % self.q.get(jid))

    def assertNoDbpart(self):
        for dp, _dns, fns in os.walk(self.root):
            for f in fns:
                self.assertFalse(f.endswith(".dbpart"), os.path.join(dp, f))

    def assertPublic(self, job):
        for k in ("id", "type", "status", "title", "created_at", "started_at", "finished_at",
                  "bytes_total", "bytes_done", "files_total", "files_done", "current", "speed_bps",
                  "eta_s", "freed", "added", "results", "undoable", "message"):
            self.assertIn(k, job)
        self.assertFalse([k for k in job if k.startswith("_")])
        json.dumps(job)


class MoveTests(Base):
    def test_move_file(self):
        src = mkfile(os.path.join(self.D, "proj", "a.bin"), 300_000, mtime=OLD)
        h = sha(src)
        jid = self.q.submit_move([src], "C")
        j = self.wait(jid)
        self.assertPublic(j)
        self.assertEqual(j["status"], "done", j)
        dst = os.path.join(self.C, "proj", "a.bin")
        self.assertFalse(os.path.exists(src))
        self.assertEqual(sha(dst), h)
        self.assertEqual(os.stat(dst).st_mtime, OLD)
        self.assertEqual(j["freed"], {"D": 300_000})
        self.assertEqual(j["added"], {"C": 300_000})
        self.assertEqual(j["results"], [{"path": src, "ok": True, "dest": dst}])
        self.assertTrue(j["undoable"])
        self.assertEqual(j["bytes_done"], j["bytes_total"])
        self.assertTrue(os.path.isdir(os.path.join(self.D, "proj")))     # 상위 폴더는 남는다
        self.assertIn(("removed", src), self.calls)
        self.assertIn(("added", dst), self.calls)
        self.assertNoDbpart()

    def test_move_dir_tree_preserves_structure_and_mtimes(self):
        top = os.path.join(self.D, "Projects", "2024_광고")
        mkfile(os.path.join(top, "a.aep"), 50_000, mtime=OLD)
        mkfile(os.path.join(top, "footage", "C0001.MP4"), 2 * MiB + 17, mtime=OLD + 10)
        mkfile(os.path.join(top, "footage", "deep", "x.wav"), 4 * MiB + 3, mtime=OLD + 20)
        mkfile(os.path.join(top, "render", "final_v3.mov"), 1, mtime=OLD + 30)
        mkfile(os.path.join(top, "zero.txt"), 0, mtime=OLD + 40)
        os.makedirs(os.path.join(top, "empty", "nested"))
        dir_times = {}
        for rel in ("footage/deep", "footage", "render", "empty/nested", "empty", ""):
            p = os.path.join(top, rel) if rel else top
            t = OLD + 1000 + len(rel)
            os.utime(p, (t, t))
            dir_times[rel] = t
        before = tree(top)
        jid = self.q.submit_move([top], "C", verify="full")
        j = self.wait(jid)
        self.assertEqual(j["status"], "done", j)
        dst = os.path.join(self.C, "Projects", "2024_광고")
        self.assertEqual(tree(dst), before)
        self.assertFalse(os.path.exists(top))
        self.assertTrue(os.path.isdir(os.path.join(self.D, "Projects")))
        self.assertEqual(os.stat(os.path.join(dst, "footage", "deep", "x.wav")).st_mtime, OLD + 20)
        for rel, t in dir_times.items():
            p = os.path.join(dst, rel) if rel else dst
            self.assertEqual(os.stat(p).st_mtime, t, rel)
        self.assertEqual(j["files_done"], 5)
        self.assertNoDbpart()

    def test_collision_suffix(self):
        mkfile(os.path.join(self.C, "proj", "a.mp4"), 10, seed=1)
        mkfile(os.path.join(self.C, "proj", "a (2).mp4"), 10, seed=2)
        src = mkfile(os.path.join(self.D, "proj", "a.mp4"), 20, seed=3)
        h = sha(src)
        j = self.wait(self.q.submit_move([src], "C"))
        self.assertEqual(j["status"], "done")
        self.assertEqual(j["results"][0]["dest"], os.path.join(self.C, "proj", "a (3).mp4"))
        self.assertEqual(sha(os.path.join(self.C, "proj", "a (3).mp4")), h)
        self.assertEqual(os.path.getsize(os.path.join(self.C, "proj", "a.mp4")), 10)
        # 폴더 충돌
        mkfile(os.path.join(self.C, "Shoot", "keep.txt"), 5)
        mkfile(os.path.join(self.D, "Shoot", "new.txt"), 6)
        j = self.wait(self.q.submit_move([os.path.join(self.D, "Shoot")], "C"))
        self.assertEqual(j["results"][0]["dest"], os.path.join(self.C, "Shoot (2)"))
        self.assertTrue(os.path.exists(os.path.join(self.C, "Shoot (2)", "new.txt")))
        self.assertEqual(os.listdir(os.path.join(self.C, "Shoot")), ["keep.txt"])

    def test_same_name_items_in_one_job(self):
        a = mkfile(os.path.join(self.D, "x", "clip.mov"), 10, seed=1)
        b = mkfile(os.path.join(self.D, "y", "clip.mov"), 11, seed=2)
        inbox = os.path.join(self.C, "inbox")
        j = self.wait(self.q.submit_move([a, b], "C", dest_dir=inbox))
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(sorted(os.listdir(inbox)), ["clip (2).mov", "clip.mov"])

    def test_group_move_and_collision_folder(self):
        seq = os.path.join(self.D, "render", "seq")
        frames = [mkfile(os.path.join(seq, "shot_%04d.exr" % i), 1000 + i) for i in range(1, 7)]
        other = mkfile(os.path.join(seq, "notes.txt"), 5)
        label = os.path.join(seq, "shot_[####].exr")
        j = self.wait(self.q.submit_move([{"label": label, "paths": frames}], "C"))
        self.assertEqual(j["status"], "done", j)
        dseq = os.path.join(self.C, "render", "seq")
        self.assertEqual(sorted(os.listdir(dseq)), ["shot_%04d.exr" % i for i in range(1, 7)])
        self.assertEqual(j["results"][0]["path"], label)
        self.assertEqual(j["results"][0]["dest"], os.path.join(dseq, "shot_[####].exr"))
        self.assertTrue(os.path.exists(other))
        self.assertFalse(any(os.path.exists(f) for f in frames))
        self.assertIn(("removed", label), self.calls)
        # 충돌: 한 장이라도 겹치면 묶음 전체가 새 폴더로
        frames2 = [mkfile(os.path.join(self.D, "render", "seq", "shot_%04d.exr" % i), 2000 + i) for i in range(1, 7)]
        j = self.wait(self.q.submit_move([{"label": label, "paths": frames2}], "C"))
        self.assertEqual(j["status"], "done", j)
        coll = os.path.join(dseq, "shot (2)")
        self.assertEqual(sorted(os.listdir(coll)), ["shot_%04d.exr" % i for i in range(1, 7)])
        self.assertEqual(os.path.getsize(os.path.join(dseq, "shot_0003.exr")), 1003)   # 원래 것은 그대로
        self.assertEqual(os.path.getsize(os.path.join(coll, "shot_0003.exr")), 2003)
        self.assertIn(("added", coll), self.calls)

    def test_group_with_missing_member_is_partial(self):
        seq = os.path.join(self.D, "s")
        frames = [mkfile(os.path.join(seq, "f_%03d.png" % i), 100) for i in range(5)]
        missing = os.path.join(seq, "f_005.png")
        j = self.wait(self.q.submit_move([{"label": os.path.join(seq, "f_[###].png"),
                                           "paths": frames + [missing]}], "C"))
        self.assertEqual(j["status"], "partial", j)
        self.assertFalse(j["undoable"])
        self.assertEqual(len(os.listdir(os.path.join(self.C, "s"))), 5)

    def _corrupt_first_write(self):
        state = {"n": 0}
        real = ops._write

        def bad(f, data):
            state["n"] += 1
            if state["n"] == 1 and len(data):
                b = bytearray(data)
                b[len(b) // 2] ^= 0xFF
                return real(f, bytes(b))
            return real(f, data)
        return bad

    def test_verify_full_and_quick_detect_corruption(self):
        for level in ("full", "quick"):
            with self.subTest(level=level):
                src = mkfile(os.path.join(self.D, "v_" + level, "a.bin"), 500_000)
                h = sha(src)
                with mock.patch.object(ops, "_write", self._corrupt_first_write()):
                    j = self.wait(self.q.submit_move([src], "C", verify=level))
                self.assertEqual(j["status"], "failed", j)
                self.assertFalse(j["results"][0]["ok"])
                self.assertIn("원본", j["results"][0]["error"])
                self.assertEqual(sha(src), h)
                self.assertFalse(os.path.exists(os.path.join(self.C, "v_" + level)))
                self.assertEqual(j["freed"], {})
                self.assertFalse(j["undoable"])
                self.assertNoDbpart()

    def test_verify_size_detects_truncation(self):
        src = mkfile(os.path.join(self.D, "t", "a.bin"), 100_000)
        h = sha(src)
        real = ops._write
        with mock.patch.object(ops, "_write", lambda f, d: real(f, bytes(d)[:-1])):
            j = self.wait(self.q.submit_move([src], "C", verify="size"))
        self.assertEqual(j["status"], "failed", j)
        self.assertEqual(sha(src), h)
        self.assertNoDbpart()

    def test_corruption_inside_dir_rolls_back_whole_item(self):
        top = os.path.join(self.D, "proj")
        for n in ("a.bin", "b.bin", "sub/c.bin"):
            mkfile(os.path.join(top, n), 200_000)
        before = tree(top)
        state = {"n": 0}
        real = ops._write

        def bad(f, data):
            state["n"] += 1
            if state["n"] == 2:
                data = b"\x00" + bytes(data)[1:]
            return real(f, data)
        with mock.patch.object(ops, "_write", bad):
            j = self.wait(self.q.submit_move([top], "C", verify="quick"))
        self.assertEqual(j["status"], "failed", j)
        self.assertEqual(tree(top), before)
        self.assertFalse(os.path.exists(os.path.join(self.C, "proj")))
        self.assertNoDbpart()

    def test_cancel_mid_copy(self):
        big = os.path.join(self.D, "big", "huge.mov")
        os.makedirs(os.path.dirname(big))
        with open(big, "wb") as f:
            f.write(os.urandom(50 * MiB))
        h = sha(big)
        small = mkfile(os.path.join(self.D, "big2", "after.bin"), 10)
        self.q.chunk_size = 64 * 1024
        real = ops._write

        def slow(f, data):
            time.sleep(0.002)
            return real(f, data)
        with mock.patch.object(ops, "_write", slow):
            jid = self.q.submit_move([big, small], "C")
            t0 = time.time()
            while (self.q.get(jid)["bytes_done"] < 2 * MiB) and time.time() - t0 < 30:
                time.sleep(0.01)
            self.assertTrue(self.q.cancel(jid))
            j = self.wait(jid)
        self.assertEqual(j["status"], "canceled", j)
        self.assertEqual(sha(big), h)
        self.assertTrue(os.path.exists(small))
        self.assertFalse(os.path.exists(os.path.join(self.C, "big")))
        self.assertFalse(os.path.exists(os.path.join(self.C, "big2")))
        self.assertEqual([r["ok"] for r in j["results"]], [False, False])
        self.assertEqual(j["results"][0]["error"], ops.R_CANCELED)
        self.assertNoDbpart()
        self.assertFalse(self.q.cancel(jid))

    def test_never_overwrites_dest_created_during_copy(self):
        src = mkfile(os.path.join(self.D, "race", "a.bin"), 5000)
        h = sha(src)
        dst = os.path.join(self.C, "race", "a.bin")
        real = ops._open_read

        def opener(p):
            if p == src and not os.path.exists(dst):
                mkfile(dst, 7, seed=99)               # 다른 프로그램이 같은 이름을 막 만들었다
            return real(p)
        with mock.patch.object(ops, "_open_read", opener):
            j = self.wait(self.q.submit_move([src], "C"))
        self.assertEqual(j["status"], "failed", j)
        self.assertEqual(os.path.getsize(dst), 7)       # 남의 파일은 그대로
        self.assertEqual(sha(src), h)
        self.assertNoDbpart()

    def test_cancel_inside_dir_rolls_back_item(self):
        top = os.path.join(self.D, "cproj")
        first = mkfile(os.path.join(top, "a_small.bin"), 1000)
        big = os.path.join(top, "z_big.bin")
        with open(big, "wb") as f:
            f.write(os.urandom(20 * MiB))
        before = tree(top)
        self.q.chunk_size = 64 * 1024
        real = ops._write

        def slow(f, data):
            time.sleep(0.003)
            return real(f, data)
        with mock.patch.object(ops, "_write", slow):
            jid = self.q.submit_move([top], "C")
            t0 = time.time()
            while self.q.get(jid)["bytes_done"] < 2 * MiB and time.time() - t0 < 30:
                time.sleep(0.01)
            self.q.cancel(jid)
            j = self.wait(jid)
        self.assertEqual(j["status"], "canceled", j)
        self.assertEqual(tree(top), before)
        self.assertTrue(os.path.exists(first))
        self.assertFalse(os.path.exists(os.path.join(self.C, "cproj")))
        self.assertNoDbpart()

    def test_unreadable_file_partial_keeps_source(self):
        top = os.path.join(self.D, "proj")
        mkfile(os.path.join(top, "a.bin"), 1000)
        mkfile(os.path.join(top, "sub", "locked.bin"), 2000)
        mkfile(os.path.join(top, "sub", "b.bin"), 3000)
        real = ops._open_read

        def opener(p):
            if p.endswith("locked.bin"):
                raise PermissionError(13, "locked", p)
            return real(p)
        with mock.patch.object(ops, "_open_read", opener):
            j = self.wait(self.q.submit_move([top], "C"))
        self.assertEqual(j["status"], "partial", j)
        r = j["results"][0]
        self.assertFalse(r["ok"])
        self.assertEqual(r["dest"], os.path.join(self.C, "proj"))
        self.assertIn("남겨", r["error"])
        self.assertEqual(tree(top), {"sub": ("d",), "sub/locked.bin": tree(top)["sub/locked.bin"]})
        dst = tree(os.path.join(self.C, "proj"))
        self.assertEqual(sorted(dst), ["a.bin", "sub", "sub/b.bin"])
        self.assertEqual(j["freed"], {"D": 4000})
        self.assertFalse(j["undoable"])
        self.assertIn(("changed", top), self.calls)
        self.assertNoDbpart()

    def test_source_changed_after_copy_is_kept(self):
        top = os.path.join(self.D, "proj")
        mkfile(os.path.join(top, "one.aep"), 1000, mtime=OLD)
        mkfile(os.path.join(top, "two.aep"), 1000, mtime=OLD)
        opened = []
        real = ops._open_read

        def opener(p):
            if opened and opened[-1] != p and len(opened) == 1:
                with open(opened[0], "ab") as f:       # 앞서 복사한 파일을 사용자가 저장한 것처럼
                    f.write(b"new work")
            opened.append(p)
            return real(p)
        with mock.patch.object(ops, "_open_read", opener):
            j = self.wait(self.q.submit_move([top], "C", verify="size"))
        self.assertEqual(j["status"], "partial", j)
        changed = opened[0]
        name = os.path.basename(changed)
        self.assertEqual(os.path.getsize(changed), 1008)                      # 새 작업이 남아 있다
        self.assertFalse(os.path.exists(os.path.join(self.C, "proj", name)))  # 낡은 사본은 거뒀다
        self.assertIn(ops.R_CHANGED, j["results"][0]["error"])

    def test_symlinks_are_not_followed(self):
        outside = mkfile(os.path.join(self.tmp, "outside", "precious.bin"), 100)
        top = os.path.join(self.D, "proj")
        mkfile(os.path.join(top, "a.bin"), 10)
        try:
            os.symlink(os.path.dirname(outside), os.path.join(top, "link"))
        except (OSError, NotImplementedError):
            self.skipTest("symlink 불가")
        j = self.wait(self.q.submit_move([top], "C"))
        self.assertEqual(j["status"], "partial", j)
        self.assertTrue(os.path.exists(outside))
        self.assertTrue(os.path.islink(os.path.join(top, "link")))
        self.assertFalse(os.path.lexists(os.path.join(self.C, "proj", "link")))
        # 링크 자체·링크를 거친 경로는 거부
        j = self.wait(self.q.submit_move([os.path.join(top, "link")], "C"))
        self.assertEqual((j["status"], j["results"][0]["error"]), ("failed", ops.R_LINK))
        j = self.wait(self.q.submit_move([os.path.join(top, "link", "precious.bin")], "C"))
        self.assertEqual((j["status"], j["results"][0]["error"]), ("failed", ops.R_LINK_PATH))
        j = self.wait(self.q.submit_recycle([os.path.join(top, "link", "precious.bin")]))
        self.assertEqual(j["status"], "failed")
        self.assertTrue(os.path.exists(outside))

    def test_overlap_via_alias_drive_is_refused(self):
        # subst 처럼 다른 문자로 보이지만 실제로는 원본 안인 '드라이브'
        top = os.path.join(self.D, "proj")
        mkfile(os.path.join(top, "a.bin"), 100)
        alias = os.path.join(self.tmp, "alias_X")
        try:
            os.symlink(top, alias)
        except (OSError, NotImplementedError):
            self.skipTest("symlink 불가")
        drives = lambda: winapi.list_drives() + [{"letter": "X", "root": alias, "free": 100 * GiB}]
        q = ops.JobQueue(self.home, drives, lambda p: safety.check(p, drives()))
        q.start()
        try:
            # 고른 폴더가 실제로는 원본 안 → 시작 전에 거부
            with self.assertRaises(ops.OpError) as cm:
                q.submit_move([top], "X", dest_dir=os.path.join(alias, "inner"))
            self.assertEqual(cm.exception.code, "invalid_dest")
            # 같은 상대 경로(alias/proj)도 실제로는 원본 안 → 실행 중 재검사가 막는다
            jid = q.submit_move([top], "X")
            t0 = time.time()
            while q.get(jid)["status"] in ("queued", "running") and time.time() - t0 < 10:
                time.sleep(0.02)
            j = q.get(jid)
        finally:
            q.stop(5)
        self.assertEqual(j["status"], "failed", j)
        self.assertEqual(j["results"][0]["error"], ops.R_OVERLAP)
        self.assertEqual(sorted(os.listdir(top)), ["a.bin"])

    def test_dest_dir(self):
        src = mkfile(os.path.join(self.D, "deep", "er", "a.bin"), 10)
        inbox = os.path.join(self.C, "inbox", "new")
        j = self.wait(self.q.submit_move([src], "C", dest_dir=inbox))
        self.assertEqual(j["status"], "done")
        self.assertTrue(os.path.exists(os.path.join(inbox, "a.bin")))
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([mkfile(os.path.join(self.D, "b.bin"), 1)], "C",
                               dest_dir=os.path.join(self.E, "x"))
        self.assertEqual(cm.exception.code, "invalid_dest")

    def test_nested_items_deduped(self):
        top = os.path.join(self.D, "proj")
        a = mkfile(os.path.join(top, "a.bin"), 10)
        j = self.wait(self.q.submit_move([a, top, top + "/", a], "C"))
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(len(j["results"]), 1)
        self.assertEqual(j["results"][0]["path"], top)

    def test_hooks_size_of_and_job_done(self):
        sizes = []
        hooks = dict(self.hooks, size_of=lambda p: sizes.append(p) or (123, 1))
        q = self.make_queue(hooks)
        q.start()
        try:
            src = mkfile(os.path.join(self.D, "h", "a.bin"), 10)
            jid = q.submit_move([src], "C")
            self.assertEqual(q.get(jid)["bytes_total"], 123)       # 제출 시에는 인덱스 값
            t0 = time.time()
            while q.get(jid)["status"] in ("queued", "running") and time.time() - t0 < 10:
                time.sleep(0.02)
            j = q.get(jid)
            self.assertEqual(j["bytes_total"], 10)                 # 실행하며 실제 크기로 고친다
            self.assertIn(src, sizes)
            done = [c[1] for c in self.calls if c[0] == "done" and c[1]["id"] == jid]
            self.assertEqual(len(done), 1)
            self.assertPublic(done[0])
        finally:
            q.stop(5)

    def test_hook_exceptions_do_not_break_job(self):
        def boom(*_a):
            raise RuntimeError("x")
        q = self.make_queue({"on_removed": boom, "on_added": boom, "on_job_done": boom, "size_of": boom})
        q.start()
        try:
            src = mkfile(os.path.join(self.D, "hx", "a.bin"), 10)
            jid = q.submit_move([src], "C")
            t0 = time.time()
            while q.get(jid)["status"] in ("queued", "running") and time.time() - t0 < 10:
                time.sleep(0.02)
            self.assertEqual(q.get(jid)["status"], "done")
            self.assertGreater(q.hook_errors, 0)
        finally:
            q.stop(5)

    def test_without_on_changed_falls_back(self):
        calls = []
        q = self.make_queue({"on_removed": lambda p: calls.append(("r", p)),
                             "on_added": lambda p: calls.append(("a", p))})
        q.start()
        try:
            top = os.path.join(self.D, "pp")
            mkfile(os.path.join(top, "ok.bin"), 10)
            mkfile(os.path.join(top, "locked.bin"), 10)
            real = ops._open_read
            with mock.patch.object(ops, "_open_read",
                                   lambda p: (_ for _ in ()).throw(PermissionError(13, "x")) if "locked" in p else real(p)):
                jid = q.submit_move([top], "C")
                t0 = time.time()
                while q.get(jid)["status"] in ("queued", "running") and time.time() - t0 < 10:
                    time.sleep(0.02)
            self.assertEqual(q.get(jid)["status"], "partial")
            self.assertIn(("r", top), calls)
            self.assertIn(("a", top), calls)
        finally:
            q.stop(5)


class ValidationTests(Base):
    def test_protected_rejection(self):
        ok = mkfile(os.path.join(self.D, "proj", "a.bin"), 10)
        win = mkfile(os.path.join(self.D, "Windows", "System32", "x.dll"), 10)
        for items in ([self.D], [win], [ok, win], [os.path.join(self.D, "_drive.json")],
                      [os.path.join(self.D, "proj", "..", "Windows")], ["relative/x"],
                      [os.path.join(self.tmp, "elsewhere.bin")],
                      [{"label": "x", "paths": [ok, win]}]):
            with self.subTest(items=items):
                with self.assertRaises(ops.OpError) as cm:
                    self.q.submit_move(items, "C")
                self.assertEqual(cm.exception.code, "protected")
                self.assertTrue(cm.exception.extra["blocked"])
                with self.assertRaises(ops.OpError) as cm:
                    self.q.submit_recycle(items)
                self.assertEqual(cm.exception.code, "protected")
                d = cm.exception.to_dict()
                self.assertEqual((d["ok"], d["error"]), (False, "protected"))
        self.assertTrue(os.path.exists(ok))
        self.assertTrue(os.path.exists(win))
        self.assertEqual(self.q.jobs(), [])

    def test_dest_protected(self):
        src = mkfile(os.path.join(self.D, "proj", "a.bin"), 10)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "C", dest_dir=os.path.join(self.C, "Program Files"))
        self.assertEqual(cm.exception.code, "invalid_dest")
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "C", dest_dir=os.path.join(self.C, "Users"))
        self.assertEqual(cm.exception.code, "invalid_dest")
        # 원본은 괜찮지만 옮길 자리(C 최상위의 Windows)가 보호된 곳
        win_dir = os.path.join(self.D, "proj", "Windows")
        mkfile(os.path.join(win_dir, "x.bin"), 10)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([win_dir], "C", dest_dir=self.C)
        self.assertEqual(cm.exception.code, "protected")
        self.assertIn("옮길 위치", cm.exception.extra["blocked"][0]["reason"])
        self.assertTrue(os.path.exists(os.path.join(win_dir, "x.bin")))

    def test_same_drive_unknown_empty_invalid(self):
        src = mkfile(os.path.join(self.D, "proj", "a.bin"), 10)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "D")
        self.assertEqual(cm.exception.code, "same_drive")
        self.assertEqual(cm.exception.extra["paths"], [src])
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "Z")
        self.assertEqual(cm.exception.code, "unknown_drive")
        for empty in ([], ()):
            with self.assertRaises(ops.OpError) as cm:
                self.q.submit_move(empty, "C")
            self.assertEqual(cm.exception.code, "empty")
            with self.assertRaises(ops.OpError) as cm:
                self.q.submit_recycle(empty)
            self.assertEqual(cm.exception.code, "empty")
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "C", verify="none")
        self.assertEqual(cm.exception.code, "invalid")
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([42], "C")
        self.assertEqual(cm.exception.code, "invalid")

    def test_space_rejection(self):
        src = mkfile(os.path.join(self.D, "proj", "a.bin"), 1000)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "E")
        e = cm.exception
        self.assertEqual(e.code, "space")
        self.assertEqual(e.extra["need"], 1000 + ops.MARGIN)
        self.assertEqual(e.extra["free"], GiB)
        self.assertIn("공간이 부족", e.message)
        self.assertTrue(os.path.exists(src))

    def test_space_counts_queued_jobs(self):
        self.q.stop(5)
        self.mk("F", GiB + 3 * MiB, "ssd")
        winapi.invalidate_usage()
        q = self.make_queue()
        a = mkfile(os.path.join(self.D, "a", "a.bin"), 2 * MiB)
        b = mkfile(os.path.join(self.D, "b", "b.bin"), 2 * MiB)
        q.submit_move([a], "F")                      # 아직 시작 안 함 → 대기 중
        with self.assertRaises(ops.OpError) as cm:
            q.submit_move([b], "F")
        self.assertEqual(cm.exception.code, "space")
        q.stop(5)

    def test_preview_move(self):
        a = mkfile(os.path.join(self.D, "proj", "a.bin"), 1000)
        b = mkfile(os.path.join(self.D, "proj2", "b.bin"), 500)
        mkfile(os.path.join(self.C, "proj", "a.bin"), 1)
        win = mkfile(os.path.join(self.D, "Windows", "x"), 1)
        p = self.q.preview_move([a, b, win], "C")
        self.assertTrue(p["ok"])
        self.assertEqual((p["bytes"], p["files"]), (1500, 2))
        self.assertEqual(p["conflicts"], 1)
        self.assertEqual(p["dest_examples"], [os.path.join(self.C, "proj", "a (2).bin"),
                                              os.path.join(self.C, "proj2", "b.bin")])
        self.assertEqual(len(p["blocked"]), 1)
        self.assertEqual(p["target_free_after"], p["target_free"] - 1500)
        self.assertTrue(p["enough"])
        p = self.q.preview_move([a], "E")
        self.assertFalse(p["enough"])
        p = self.q.preview_move([a], "D")
        self.assertEqual(p["blocked"][0]["path"], a)
        self.assertEqual(self.q.jobs(), [])
        self.assertTrue(os.path.exists(a))
        with self.assertRaises(ops.OpError):
            self.q.preview_move([a], "Q")


class UndoTests(Base):
    def test_undo_restores(self):
        top = os.path.join(self.D, "Projects", "A")
        mkfile(os.path.join(top, "x.aep"), 1000, mtime=OLD)
        mkfile(os.path.join(top, "f", "y.mov"), 2000, mtime=OLD)
        before = tree(top)
        jid = self.q.submit_move([top], "C")
        j = self.wait(jid)
        self.assertEqual(j["status"], "done")
        uid = self.q.undo(jid)
        self.assertFalse(self.q.get(jid)["undoable"])
        u = self.wait(uid)
        self.assertPublic(u)
        self.assertEqual(u["status"], "done", u)
        self.assertEqual(u["type"], "undo")
        self.assertEqual(u["undo_of"], jid)
        self.assertEqual(tree(top), before)
        self.assertEqual(os.stat(os.path.join(top, "x.aep")).st_mtime, OLD)
        self.assertFalse(os.path.exists(os.path.join(self.C, "Projects", "A")))
        self.assertEqual(u["freed"], {"C": 3000})
        self.assertEqual(u["added"], {"D": 3000})
        self.assertFalse(u["undoable"])
        self.assertFalse(self.q.get(jid)["undoable"])
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo(jid)
        self.assertEqual(cm.exception.code, "not_undoable")
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo(uid)
        self.assertEqual(cm.exception.code, "not_undoable")
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo("nope")
        self.assertEqual(cm.exception.code, "not_found")

    def test_undo_when_original_recreated(self):
        src = mkfile(os.path.join(self.D, "p", "clip.mov"), 100, seed=1)
        jid = self.q.submit_move([src], "C")
        self.wait(jid)
        mkfile(src, 7, seed=2)                         # 같은 이름이 다시 생겼다
        u = self.wait(self.q.undo(jid))
        self.assertEqual(u["status"], "done", u)
        self.assertEqual(u["results"][0]["dest"], os.path.join(self.D, "p", "clip (2).mov"))
        self.assertEqual(os.path.getsize(src), 7)
        self.assertEqual(os.path.getsize(os.path.join(self.D, "p", "clip (2).mov")), 100)

    def test_undo_group(self):
        seq = os.path.join(self.D, "r")
        frames = [mkfile(os.path.join(seq, "a_%03d.dpx" % i), 50) for i in range(5)]
        jid = self.q.submit_move([{"label": os.path.join(seq, "a_[###].dpx"), "paths": frames}], "C")
        self.assertEqual(self.wait(jid)["status"], "done")
        u = self.wait(self.q.undo(jid))
        self.assertEqual(u["status"], "done", u)
        self.assertTrue(all(os.path.exists(f) for f in frames))
        self.assertEqual(os.listdir(os.path.join(self.C, "r")) if os.path.exists(os.path.join(self.C, "r")) else [], [])

    def test_partial_job_undoes_only_complete_items(self):
        good = mkfile(os.path.join(self.D, "g", "ok.bin"), 10)
        top = os.path.join(self.D, "bad")
        mkfile(os.path.join(top, "locked.bin"), 10)
        mkfile(os.path.join(top, "fine.bin"), 10)
        real = ops._open_read
        with mock.patch.object(ops, "_open_read",
                               lambda p: (_ for _ in ()).throw(PermissionError(13, "x")) if "locked" in p else real(p)):
            j = self.wait(self.q.submit_move([good, top], "C"))
        self.assertEqual(j["status"], "partial")
        self.assertTrue(j["undoable"])
        u = self.wait(self.q.undo(j["id"]))
        self.assertEqual(u["status"], "done")
        self.assertEqual(len(u["results"]), 1)
        self.assertTrue(os.path.exists(good))
        self.assertTrue(os.path.exists(os.path.join(self.C, "bad", "fine.bin")))

    def test_recycle_not_undoable(self):
        src = mkfile(os.path.join(self.D, "r", "a.bin"), 10)
        jid = self.q.submit_recycle([src])
        self.wait(jid)
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo(jid)
        self.assertEqual(cm.exception.code, "not_undoable")


class RecycleTests(Base):
    def test_recycle_into_fakebin_and_freed(self):
        f = mkfile(os.path.join(self.D, "old", "a.mov"), 1000)
        d = os.path.join(self.D, "old", "dir")
        mkfile(os.path.join(d, "b.bin"), 2000)
        mkfile(os.path.join(d, "sub", "c.bin"), 3000)
        seq = [mkfile(os.path.join(self.C, "seq", "s_%04d.png" % i), 10) for i in range(5)]
        jid = self.q.submit_recycle([f, d, {"label": os.path.join(self.C, "seq", "s_[####].png"), "paths": seq}])
        j = self.wait(jid)
        self.assertPublic(j)
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["type"], "recycle")
        self.assertFalse(j["undoable"])
        self.assertEqual(j["freed"], {"D": 6000, "C": 50})
        self.assertIn("휴지통을 비워야", j["message"])
        self.assertIn("휴지통에서 복원", j["message"])
        self.assertFalse(os.path.exists(f) or os.path.exists(d) or any(os.path.exists(s) for s in seq))
        self.assertEqual(winapi.recycle_bin_info(self.D), (6000, 2))
        self.assertEqual(winapi.recycle_bin_info(self.C), (50, 5))
        self.assertIn(("removed", f), self.calls)
        self.assertIn(("removed", d), self.calls)
        self.assertEqual([r["ok"] for r in j["results"]], [True, True, True])

    def test_recycle_missing_item_partial(self):
        f = mkfile(os.path.join(self.D, "x.bin"), 10)
        j = self.wait(self.q.submit_recycle([f, os.path.join(self.D, "gone.bin")]))
        self.assertEqual(j["status"], "partial", j)
        self.assertEqual(j["results"][1]["error"], ops.R_MISSING)

    def test_adobe_warning(self):
        os.environ["DRIVEBOARD_FAKE_ADOBE"] = "AfterFX.exe"
        c = mkfile(os.path.join(self.D, "Cache", "Media Cache Files", "x.cfa"), 10)
        j = self.wait(self.q.submit_recycle([c]))
        self.assertEqual(j["status"], "done")
        self.assertIn("After Effects", j["message"])
        n = mkfile(os.path.join(self.D, "plain", "y.mov"), 10)
        j = self.wait(self.q.submit_recycle([n]))
        self.assertNotIn("After Effects", j["message"])

    def test_empty_bin(self):
        f = mkfile(os.path.join(self.D, "a.bin"), 4000)
        self.wait(self.q.submit_recycle([f]))
        self.assertEqual(winapi.recycle_bin_info(self.D), (4000, 1))
        free_before = {d["letter"]: d["free"] for d in winapi.list_drives()}["D"]
        j = self.wait(self.q.submit_empty_bin(["D"]))
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["type"], "empty_bin")
        self.assertEqual(j["freed"], {"D": 4000})
        self.assertEqual(winapi.recycle_bin_info(self.D), (0, 0))
        self.assertEqual({d["letter"]: d["free"] for d in winapi.list_drives()}["D"], free_before + 4000)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_empty_bin(["Q"])
        self.assertEqual(cm.exception.code, "unknown_drive")
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_empty_bin([])
        self.assertEqual(cm.exception.code, "empty")


class PersistenceAndTaskTests(Base):
    def test_jobs_json_persistence(self):
        src = mkfile(os.path.join(self.D, "p", "a.bin"), 10)
        jid = self.q.submit_move([src], "C")
        j = self.wait(jid)
        self.q.stop(5)
        path = os.path.join(self.home, "jobs.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["jobs"].append({"id": "jcrash", "type": "move", "status": "running", "title": "x",
                             "_kind": "move", "results": [], "_items": [
                                 {"kind": "path", "src": "/x", "dst_final": "/y/x", "done": False}]})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        q2 = self.make_queue()
        self.q = q2
        j2 = q2.get(jid)
        self.assertEqual(j2, j)
        self.assertTrue(j2["undoable"])
        crash = q2.get("jcrash")
        self.assertEqual(crash["status"], "failed")
        self.assertIn("중단", crash["message"])
        self.assertIn("/y/x", crash["message"])
        self.assertEqual([x["id"] for x in q2.jobs()], ["jcrash", jid])
        self.assertEqual(q2.active(), [])
        q2.start()
        u = self.wait(q2.undo(jid))                       # 다시 켠 뒤에도 되돌릴 수 있다
        self.assertEqual(u["status"], "done")
        self.assertTrue(os.path.exists(src))

    def test_group_undo_after_restart_and_compaction(self):
        seq = os.path.join(self.D, "seqr")
        frames = [mkfile(os.path.join(seq, "f_%04d.exr" % i), 64) for i in range(8)]
        jid = self.q.submit_move([{"label": os.path.join(seq, "f_[####].exr"), "paths": frames}], "C")
        self.assertEqual(self.wait(jid)["status"], "done")
        bad = self.q.submit_move([{"label": os.path.join(self.D, "nope", "g_[##].png"),
                                   "paths": [os.path.join(self.D, "nope", "g_%02d.png" % i) for i in range(5)]}], "C")
        self.assertEqual(self.wait(bad)["status"], "failed")
        self.q.stop(5)
        with open(os.path.join(self.home, "jobs.json"), encoding="utf-8") as f:
            saved = {j["id"]: j for j in json.load(f)["jobs"]}
        self.assertEqual(saved[bad]["_items"][0]["members"], [])            # 실패한 묶음은 목록을 버린다
        self.assertEqual(saved[bad]["_items"][0]["count"], 5)
        self.assertNotIn("members_final", saved[jid]["_items"][0])
        self.assertEqual(len(saved[jid]["_items"][0]["members"]), 8)
        self.assertEqual([n for n in os.listdir(self.home) if n.endswith(".tmp")], [])
        self.q = self.make_queue()
        self.q.start()
        u = self.wait(self.q.undo(jid))
        self.assertEqual(u["status"], "done", u)
        self.assertTrue(all(os.path.exists(f) for f in frames))

    def test_corrupt_jobs_json(self):
        self.q.stop(5)
        path = os.path.join(self.home, "jobs.json")
        with open(path, "w") as f:
            f.write("{broken")
        q2 = self.make_queue()
        self.q = q2
        self.assertEqual(q2.jobs(), [])
        self.assertTrue(os.path.exists(path + ".bad"))

    def test_history_limit(self):
        self.q.stop(5)
        q = self.make_queue()
        self.q = q
        for i in range(ops.MAX_JOBS + 15):
            jid = q.submit_task("verify_dupes", "t%d" % i, lambda ctx: None)
            q.cancel(jid)
        self.assertEqual(len(q.jobs(1000)), ops.MAX_JOBS)
        with open(os.path.join(self.home, "jobs.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["jobs"]), ops.MAX_JOBS)

    def test_submit_task(self):
        def fn(ctx):
            ctx.progress(bytes_total=100, files_total=2)
            ctx.progress(bytes_done=50, files_done=1, current="/a")
            ctx.result("/a", True)
            ctx.result("/b", False, error="달라요.")
            ctx.message("검사했어요.")
            return {"groups": 3}
        jid = self.q.submit_task("verify_dupes", "중복 확인", fn)
        j = self.wait(jid)
        self.assertPublic(j)
        self.assertEqual((j["type"], j["status"], j["title"]), ("verify_dupes", "partial", "중복 확인"))
        self.assertEqual(j["message"], "검사했어요.")
        self.assertEqual(j["results"], [{"path": "/a", "ok": True}, {"path": "/b", "ok": False, "error": "달라요."}])
        self.assertEqual(self.q.result_of(jid), {"groups": 3})
        j = self.wait(self.q.submit_task("rescan", "다시 스캔", lambda ctx: None))
        self.assertEqual(j["status"], "done")

        def boom(ctx):
            raise ValueError("터졌어요")
        j = self.wait(self.q.submit_task("rescan", "x", boom))
        self.assertEqual(j["status"], "failed")
        self.assertIn("터졌어요", j["message"])

    def test_task_cancel(self):
        started = threading.Event()

        def fn(ctx):
            started.set()
            while not ctx.cancelled():
                time.sleep(0.01)
        jid = self.q.submit_task("verify_dupes", "loop", fn)
        queued = self.q.submit_task("verify_dupes", "next", lambda ctx: None)
        started.wait(5)
        self.assertEqual(self.q.get(jid)["status"], "running")
        self.assertEqual([x["id"] for x in self.q.active()], [jid, queued])
        self.assertTrue(self.q.cancel(queued))
        self.assertEqual(self.q.get(queued)["status"], "canceled")
        self.assertTrue(self.q.cancel(jid))
        self.assertEqual(self.wait(jid)["status"], "canceled")

    def test_stop_cancels_queued(self):
        self.q.stop(5)
        q = self.make_queue()
        self.q = q
        jid = q.submit_task("x", "x", lambda ctx: None)
        q.stop(5)
        self.assertEqual(q.get(jid)["status"], "canceled")

    def test_fmt_size(self):
        self.assertEqual(ops.fmt_size(0), "0KB")
        self.assertEqual(ops.fmt_size(512), "0.5KB")
        self.assertEqual(ops.fmt_size(4_000_000_000_000), "3.63TB")
        self.assertEqual(ops.fmt_size(406 * GiB + 5), "406GB")
        self.assertEqual(ops.fmt_size(int(48.25 * GiB)), "48.2GB")
        self.assertEqual(ops.fmt_size(int(9.815 * MiB)), "9.81MB")


NAS_UNC = "\\\\ORICO\\영상백업"


class DeviceBase(Base):
    """외장 HDD(X) · USB 메모리(U, 휴지통 없음) · NAS(N-orico, 휴지통 없음)를 더한 가짜 드라이브."""

    def setUp(self):
        super().setUp()
        self.mkdrive("X", {"label": "외장", "kind": "hdd", "total": 100 * GiB, "external": True})
        self.mkdrive("U", {"label": "USB", "kind": "usb", "total": 100 * GiB, "has_bin": False})
        self.mkdrive("N-orico", {"label": "영상백업", "network": True, "unc": NAS_UNC, "total": 100 * GiB})
        winapi.invalidate_usage()
        self.X, self.U = os.path.join(self.root, "X"), os.path.join(self.root, "U")
        self.NAS = os.path.join(self.root, "N-orico")
        self.NK = winapi.network_key(NAS_UNC)

    def mkdrive(self, name, meta):
        d = os.path.join(self.root, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_drive.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        return d


class CopyTests(DeviceBase):
    def test_copy_file_across_drives(self):
        src = mkfile(os.path.join(self.D, "proj", "a.bin"), 300_000, mtime=OLD)
        h = sha(src)
        jid = self.q.submit_move([src], "C", mode="copy")
        j = self.wait(jid)
        self.assertPublic(j)
        self.assertEqual((j["type"], j["status"], j["mode"]), ("copy", "done", "copy"), j)
        dst = os.path.join(self.C, "proj", "a.bin")
        self.assertEqual(sha(src), h)                           # 원본 그대로
        self.assertEqual(sha(dst), h)
        self.assertEqual(os.stat(dst).st_mtime, OLD)
        self.assertEqual(j["freed"], {})
        self.assertEqual(j["added"], {"C": 300_000})
        self.assertEqual(j["results"], [{"path": src, "ok": True, "dest": dst}])
        self.assertTrue(j["undoable"])
        self.assertIn("복사", j["title"])
        self.assertIn("복사했어요", j["message"])
        self.assertIn(("added", dst), self.calls)
        self.assertFalse([c for c in self.calls if c[0] == "removed"])
        self.assertNoDbpart()

    def test_copy_dir_tree_keeps_source_and_mtimes(self):
        top = os.path.join(self.D, "Projects", "260803_소담코스_녹차")
        mkfile(os.path.join(top, "a.aep"), 50_000, mtime=OLD)
        mkfile(os.path.join(top, "render", "final.mp4"), 2 * MiB + 5, mtime=OLD + 10)
        mkfile(os.path.join(top, "zero.txt"), 0)
        os.makedirs(os.path.join(top, "empty"))
        os.utime(os.path.join(top, "render"), (OLD + 99, OLD + 99))
        before = tree(top)
        j = self.wait(self.q.submit_move([top], "X", mode="copy", verify="full"))
        self.assertEqual(j["status"], "done", j)
        dst = os.path.join(self.X, "Projects", "260803_소담코스_녹차")
        self.assertEqual(tree(top), before)
        self.assertEqual(tree(dst), before)
        self.assertEqual(os.stat(os.path.join(dst, "render")).st_mtime, OLD + 99)
        self.assertNoDbpart()

    def test_copy_group(self):
        seq = os.path.join(self.D, "render", "seq")
        frames = [mkfile(os.path.join(seq, "shot_%04d.exr" % i), 1000 + i) for i in range(1, 6)]
        label = os.path.join(seq, "shot_[####].exr")
        j = self.wait(self.q.submit_move([{"label": label, "paths": frames}], self.NK, mode="copy"))
        self.assertEqual(j["status"], "done", j)
        dseq = os.path.join(self.NAS, "render", "seq")
        self.assertEqual(sorted(os.listdir(dseq)), ["shot_%04d.exr" % i for i in range(1, 6)])
        self.assertTrue(all(os.path.exists(f) for f in frames))
        self.assertEqual(j["added"], {self.NK: sum(1000 + i for i in range(1, 6))})
        # 되돌리기 → NAS 는 휴지통이 없어 영구 삭제, 원본은 그대로
        with self.assertRaises(ops.OpError) as cm:           # HTTP 경로(confirm=False)는 확인부터 받는다
            self.q.undo(j["id"], confirm=False)
        self.assertEqual((cm.exception.code, cm.exception.extra.get("permanent")), ("need_confirm", True))
        self.assertTrue(self.q.get(j["id"])["undoable"])       # 거절해도 되돌리기는 남는다
        self.assertTrue(os.path.isdir(dseq))
        u = self.wait(self.q.undo(j["id"]))
        self.assertEqual(u["status"], "done", u)
        self.assertTrue(u["permanent"])
        self.assertFalse(os.path.exists(os.path.join(self.NAS, "render")))
        self.assertFalse(os.path.exists(os.path.join(self.NAS, ".fakebin")))
        self.assertTrue(all(os.path.exists(f) for f in frames))
        self.assertIn("영구 삭제", u["message"])

    def test_copy_within_same_drive_needs_dest(self):
        src = mkfile(os.path.join(self.D, "proj", "a.bin"), 1000)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "D", mode="copy")
        self.assertEqual(cm.exception.code, "need_dest")
        p = self.q.preview_move([src], "D", mode="copy")
        self.assertTrue(p["need_dest"])
        self.assertEqual(len(p["blocked"]), 1)
        # 이동은 폴더를 골라도 같은 드라이브면 거부(SPEC-2 F)
        backup = os.path.join(self.D, "backup")
        os.makedirs(backup)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([src], "D", dest_dir=backup)
        self.assertEqual(cm.exception.code, "same_drive")
        j = self.wait(self.q.submit_move([src], "D", dest_dir=backup, mode="copy"))
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(os.path.getsize(os.path.join(backup, "a.bin")), 1000)
        self.assertTrue(os.path.exists(src))
        # 원본 옆(같은 폴더)으로 복사 → 이름이 겹치니 ' (2)'
        j = self.wait(self.q.submit_move([src], None, dest_dir=os.path.dirname(src), mode="copy"))
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["results"][0]["dest"], os.path.join(self.D, "proj", "a (2).bin"))
        self.assertEqual(sha(src), sha(os.path.join(self.D, "proj", "a (2).bin")))

    def test_folder_target_without_drive(self):
        src = mkfile(os.path.join(self.D, "p", "clip.mov"), 4000)
        pick = os.path.join(self.NAS, "백업", "2026")
        os.makedirs(pick)
        j = self.wait(self.q.submit_move([src], None, dest_dir=pick, mode="copy"))
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["target"], self.NK)
        self.assertEqual(j["target_label"], "영상백업")
        self.assertTrue(os.path.exists(os.path.join(pick, "clip.mov")))
        j = self.wait(self.q.submit_move([src], "", dest_dir=os.path.join(self.X, ""), mode="move"))
        self.assertEqual(j["status"], "done", j)                    # 외장 디스크 최상위 폴더로
        self.assertTrue(os.path.exists(os.path.join(self.X, "clip.mov")))
        self.assertFalse(os.path.exists(src))
        a = mkfile(os.path.join(self.D, "q", "x.bin"), 10)
        bad = [os.path.join(self.C, "없는폴더"),                     # 없는 폴더
               os.path.join(self.C, "file.bin"),                     # 파일
               os.path.join(self.tmp, "outside"),                    # 아는 드라이브 밖
               os.path.join(self.C, "Windows"),                      # 보호 경로(내장)
               os.path.join(self.NAS, "#recycle")]                   # NAS 시스템 폴더
        mkfile(bad[1], 5)
        os.makedirs(bad[2])
        os.makedirs(bad[3])
        os.makedirs(bad[4])
        for dd in bad:
            with self.subTest(dd=dd):
                with self.assertRaises(ops.OpError) as cm:
                    self.q.submit_move([a], None, dest_dir=dd, mode="copy")
                self.assertEqual(cm.exception.code, "invalid_dest", cm.exception.message)
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([a], None, mode="copy")
        self.assertEqual(cm.exception.code, "unknown_drive")
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_move([a], "C", mode="clone")
        self.assertEqual(cm.exception.code, "invalid")
        self.assertTrue(os.path.exists(a))

    def test_nas_paths_named_like_system_folders_are_allowed(self):
        src = mkfile(os.path.join(self.D, "Users", "kms", "proj", "a.mov"), 100)
        with self.assertRaises(ops.OpError):
            self.q.submit_move([os.path.join(self.D, "Users", "kms")], self.NK)    # 내장: 프로필 폴더 자체
        j = self.wait(self.q.submit_move([src], self.NK))
        self.assertEqual(j["status"], "done", j)
        dst = os.path.join(self.NAS, "Users", "kms", "proj", "a.mov")
        self.assertTrue(os.path.exists(dst))
        # NAS 안의 Users·Windows 는 사용자 폴더 — 옮기고 지울 수 있다
        win = mkfile(os.path.join(self.NAS, "Windows", "b.mov"), 50)
        with self.assertRaises(ops.OpError) as cm:                                # 대상 C:\Users 자체는 보호
            self.q.submit_move([os.path.join(self.NAS, "Users")], "C")
        self.assertEqual(cm.exception.code, "protected")
        j = self.wait(self.q.submit_move([os.path.join(self.NAS, "Users", "kms", "proj")], "C"))
        self.assertEqual(j["status"], "done", j)
        self.assertTrue(os.path.exists(os.path.join(self.C, "Users", "kms", "proj", "a.mov")))
        self.assertEqual(j["freed"], {self.NK: 100})
        j = self.wait(self.q.submit_delete([os.path.dirname(win)]))
        self.assertEqual(j["status"], "done", j)
        self.assertFalse(os.path.exists(win))

    def test_recursion_guard(self):
        top = os.path.join(self.D, "proj")
        mkfile(os.path.join(top, "a.bin"), 10)
        inner = os.path.join(top, "sub")
        os.makedirs(inner)
        for dd, target in ((inner, None), (top, None), (os.path.join(inner, "deeper"), "D")):
            with self.subTest(dd=dd):
                with self.assertRaises(ops.OpError) as cm:
                    self.q.submit_move([top], target, dest_dir=dd, mode="copy")
                self.assertEqual(cm.exception.code, "invalid_dest")
                self.assertEqual(cm.exception.message, ops.R_INTO_SELF)
        p = self.q.preview_move([top], "D", dest_dir=inner, mode="copy")
        self.assertEqual(p["blocked"][0]["reason"], ops.R_INTO_SELF)
        self.assertEqual(sorted(os.listdir(top)), ["a.bin", "sub"])
        self.assertEqual(self.q.jobs(), [])

    def test_copy_undo_removes_only_created(self):
        mkfile(os.path.join(self.C, "Projects", "keep.txt"), 7)          # 대상에 이미 있던 폴더·파일
        top = os.path.join(self.D, "Projects", "A")
        mkfile(os.path.join(top, "x.aep"), 1000, mtime=OLD)
        mkfile(os.path.join(top, "f", "y.mov"), 2000, mtime=OLD)
        mkfile(os.path.join(top, "f", "z.mov"), 3000, mtime=OLD)
        before = tree(top)
        jid = self.q.submit_move([top], "C", mode="copy")
        self.assertEqual(self.wait(jid)["status"], "done")
        dst = os.path.join(self.C, "Projects", "A")
        user_new = mkfile(os.path.join(dst, "f", "user_note.txt"), 9)       # 사용자가 사본 폴더에 새로 둔 것
        uid = self.q.undo(jid)
        self.assertFalse(self.q.get(jid)["undoable"])
        u = self.wait(uid)
        self.assertPublic(u)
        self.assertEqual((u["type"], u["status"]), ("undo", "done"), u)
        self.assertFalse(u["permanent"])
        self.assertEqual(tree(top), before)                                   # 원본 그대로
        self.assertTrue(os.path.exists(os.path.join(self.C, "Projects", "keep.txt")))
        self.assertTrue(os.path.exists(user_new))                             # 남의 파일은 남는다
        self.assertFalse(os.path.exists(os.path.join(dst, "x.aep")))
        self.assertFalse(os.path.exists(os.path.join(dst, "f", "y.mov")))
        self.assertEqual(winapi.recycle_bin_info(self.C)[1], 3)               # C 는 휴지통이 있다
        self.assertEqual(u["freed"], {"C": 6000})
        self.assertFalse(self.q.get(jid)["undoable"])
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo(jid)
        self.assertEqual(cm.exception.code, "not_undoable")
        # 다시: 만든 폴더가 비면 폴더까지 지운다(원래 있던 Projects 는 남긴다)
        os.remove(user_new)
        j2 = self.wait(self.q.submit_move([top], "C", mode="copy"))
        self.assertEqual(j2["results"][0]["dest"], os.path.join(self.C, "Projects", "A (2)"))
        u2 = self.wait(self.q.undo(j2["id"]))
        self.assertEqual(u2["status"], "done", u2)
        self.assertFalse(os.path.exists(os.path.join(self.C, "Projects", "A (2)")))
        self.assertTrue(os.path.isdir(os.path.join(self.C, "Projects")))
        self.assertNoDbpart()

    def test_copy_undo_keeps_changed_copies_and_lone_copies(self):
        top = os.path.join(self.D, "B")
        a = mkfile(os.path.join(top, "a.mov"), 1000, mtime=OLD)
        mkfile(os.path.join(top, "b.mov"), 1000, mtime=OLD)
        mkfile(os.path.join(top, "c.mov"), 1000, mtime=OLD)
        j = self.wait(self.q.submit_move([top], "U", mode="copy"))
        self.assertEqual(j["status"], "done", j)
        dst = os.path.join(self.U, "B")
        with open(os.path.join(dst, "b.mov"), "ab") as f:                     # 사본을 고쳐 썼다
            f.write(b"edit")
        os.remove(a)                                                          # 원본을 지웠다 → 사본이 유일본
        u = self.wait(self.q.undo(j["id"]))
        self.assertEqual(u["status"], "partial", u)
        self.assertTrue(u["permanent"])
        self.assertTrue(os.path.exists(os.path.join(dst, "a.mov")))
        self.assertTrue(os.path.exists(os.path.join(dst, "b.mov")))
        self.assertFalse(os.path.exists(os.path.join(dst, "c.mov")))
        self.assertIn("남겨", u["results"][0]["error"])
        self.assertTrue(self.q.get(j["id"])["undoable"])                      # 남은 것은 다시 시도할 수 있다

    def test_copy_partial_and_undo(self):
        top = os.path.join(self.D, "proj")
        mkfile(os.path.join(top, "a.bin"), 1000)
        mkfile(os.path.join(top, "sub", "locked.bin"), 2000)
        real = ops._open_read

        def opener(p):
            if p.endswith("locked.bin"):
                raise PermissionError(13, "locked", p)
            return real(p)
        with mock.patch.object(ops, "_open_read", opener):
            j = self.wait(self.q.submit_move([top], "C", mode="copy"))
        self.assertEqual(j["status"], "partial", j)
        self.assertIn("복사하지 못했어요", j["results"][0]["error"])
        self.assertTrue(j["undoable"])
        self.assertTrue(os.path.exists(os.path.join(self.C, "proj", "a.bin")))
        u = self.wait(self.q.undo(j["id"]))
        self.assertEqual(u["status"], "done", u)
        self.assertFalse(os.path.exists(os.path.join(self.C, "proj")))
        self.assertTrue(os.path.exists(os.path.join(top, "sub", "locked.bin")))

    def test_copy_nothing_copied_rolls_back_dirs(self):
        top = os.path.join(self.D, "only_locked")
        mkfile(os.path.join(top, "sub", "locked.bin"), 2000)
        with mock.patch.object(ops, "_open_read", mock.Mock(side_effect=PermissionError(13, "locked"))):
            j = self.wait(self.q.submit_move([top], "C", mode="copy"))
        self.assertEqual(j["status"], "failed", j)
        self.assertFalse(os.path.exists(os.path.join(self.C, "only_locked")))
        self.assertFalse(j["undoable"])

    def test_copy_cancel_leaves_no_copy(self):
        big = os.path.join(self.D, "big", "huge.mov")
        os.makedirs(os.path.dirname(big))
        with open(big, "wb") as f:
            f.write(os.urandom(30 * MiB))
        h = sha(big)
        self.q.chunk_size = 64 * 1024
        real = ops._write

        def slow(f, data):
            time.sleep(0.002)
            return real(f, data)
        with mock.patch.object(ops, "_write", slow):
            jid = self.q.submit_move([os.path.dirname(big)], "C", mode="copy")
            t0 = time.time()
            while self.q.get(jid)["bytes_done"] < 2 * MiB and time.time() - t0 < 30:
                time.sleep(0.01)
            self.q.cancel(jid)
            j = self.wait(jid)
        self.assertEqual(j["status"], "canceled", j)
        self.assertEqual(sha(big), h)
        self.assertFalse(os.path.exists(os.path.join(self.C, "big")))
        self.assertFalse(j["undoable"])
        self.assertNoDbpart()

    def test_preview_copy_and_move(self):
        a = mkfile(os.path.join(self.D, "proj", "a.bin"), 1000)
        p = self.q.preview_move([a], "C", mode="copy")
        self.assertEqual((p["mode"], p["freed"], p["added"]), ("copy", {}, {"C": 1000}))
        self.assertFalse(p["need_dest"])
        p = self.q.preview_move([a], "C")
        self.assertEqual((p["mode"], p["freed"], p["added"]), ("move", {"D": 1000}, {"C": 1000}))
        pick = os.path.join(self.NAS, "in")
        os.makedirs(pick)
        p = self.q.preview_move([a], None, dest_dir=pick, mode="copy")
        self.assertEqual((p["target"], p["target_kind"]), (self.NK, "network"))
        self.assertEqual(p["dest_examples"], [os.path.join(pick, "a.bin")])
        self.assertEqual(self.q.jobs(), [])

    def test_copy_undo_after_restart(self):
        top = os.path.join(self.D, "R")
        mkfile(os.path.join(top, "a.bin"), 100)
        seq = [mkfile(os.path.join(self.D, "S", "f_%02d.png" % i), 50) for i in range(5)]
        j1 = self.wait(self.q.submit_move([top, {"label": os.path.join(self.D, "S", "f_[##].png"), "paths": seq}],
                                          "C", mode="copy"))
        self.assertEqual(j1["status"], "done", j1)
        self.q.stop(5)
        self.q = self.make_queue()
        self.assertTrue(self.q.get(j1["id"])["undoable"])
        self.q.start()
        u = self.wait(self.q.undo(j1["id"]))
        self.assertEqual(u["status"], "done", u)
        self.assertFalse(os.path.exists(os.path.join(self.C, "R")))
        self.assertFalse(os.path.exists(os.path.join(self.C, "S")))
        self.assertTrue(os.path.exists(os.path.join(top, "a.bin")))
        self.assertTrue(all(os.path.exists(f) for f in seq))

    def test_copy_undo_when_original_record_lost(self):
        src = mkfile(os.path.join(self.D, "L", "a.bin"), 10)
        j = self.wait(self.q.submit_move([src], "C", mode="copy"))
        self.q.stop(5)
        self.q = self.make_queue()
        uid = self.q.undo(j["id"])
        with self.q._lock:
            self.q._jobs.pop(j["id"])                     # 기록이 사라진 경우(가장 오래된 기록이 밀려남 등)
            self.q._order.remove(j["id"])
        self.q.start()
        u = self.wait(uid)
        self.assertEqual(u["status"], "failed", u)
        self.assertIn("기록", u["message"])
        self.assertTrue(os.path.exists(os.path.join(self.C, "L", "a.bin")))   # 모르면 지우지 않는다
        self.assertTrue(os.path.exists(src))

    def test_space_uses_dest_dir(self):
        src = mkfile(os.path.join(self.D, "p", "a.bin"), 1000)
        pick = os.path.join(self.E, "in")
        os.makedirs(pick)
        with mock.patch.object(winapi, "disk_usage", wraps=winapi.disk_usage) as du:
            with self.assertRaises(ops.OpError) as cm:
                self.q.submit_move([src], None, dest_dir=pick, mode="copy")
        self.assertEqual(cm.exception.code, "space")
        self.assertIn(mock.call(pick), du.call_args_list)


class NoBinAndDeleteTests(DeviceBase):
    def test_recycle_on_no_bin_location_refused(self):
        for root in (self.U, self.NAS):
            p = mkfile(os.path.join(root, "x", "a.mov"), 10)
            with self.subTest(root=root):
                with self.assertRaises(ops.OpError) as cm:
                    self.q.submit_recycle([p])
                self.assertEqual(cm.exception.code, "no_bin")
                self.assertEqual(cm.exception.extra["paths"], [p])
                self.assertTrue(os.path.exists(p))
                ok, err = winapi.recycle(p)                       # winapi 도 이중으로 거부
                self.assertFalse(ok)
                self.assertEqual(err, winapi.NO_BIN_MSG)
        # 외장 HDD(NTFS) 는 휴지통이 있다
        x = mkfile(os.path.join(self.X, "x", "a.mov"), 10)
        j = self.wait(self.q.submit_recycle([x]))
        self.assertEqual(j["status"], "done", j)

    def test_recycle_exec_recheck_no_bin(self):
        p = mkfile(os.path.join(self.X, "y", "a.mov"), 10)
        self.q.stop(5)
        q = self.make_queue()
        self.q = q
        jid = q.submit_recycle([p])                               # 제출할 땐 휴지통이 있었는데
        self.mkdrive("X", {"label": "외장", "kind": "hdd", "total": 100 * GiB, "has_bin": False})
        q.start()
        j = self.wait(jid)
        self.assertEqual(j["status"], "failed", j)
        self.assertEqual(j["results"][0]["error"], ops.R_NO_BIN)
        self.assertTrue(os.path.exists(p))

    def test_submit_delete(self):
        top = os.path.join(self.U, "DCIM")
        mkfile(os.path.join(top, "a.mov"), 1000)
        mkfile(os.path.join(top, "sub", "b.mov"), 2000)
        os.makedirs(os.path.join(top, "empty"))
        single = mkfile(os.path.join(self.NAS, "one.mov"), 300)
        frames = [mkfile(os.path.join(self.NAS, "seq", "f_%02d.png" % i), 10) for i in range(5)]
        keep = mkfile(os.path.join(self.NAS, "seq", "notes.txt"), 3)
        jid = self.q.submit_delete([top, single, {"label": os.path.join(self.NAS, "seq", "f_[##].png"),
                                                  "paths": frames}])
        j = self.wait(jid)
        self.assertPublic(j)
        self.assertEqual((j["type"], j["status"]), ("delete", "done"), j)
        self.assertFalse(os.path.exists(top))
        self.assertFalse(os.path.exists(single))
        self.assertFalse(any(os.path.exists(f) for f in frames))
        self.assertTrue(os.path.exists(keep))
        self.assertEqual(j["freed"], {"U": 3000, self.NK: 350})
        self.assertFalse(j["undoable"])
        self.assertIn("복구할 수 없어요", j["message"])
        self.assertFalse(os.path.exists(os.path.join(self.U, ".fakebin")))
        self.assertIn(("removed", top), self.calls)
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo(jid)
        self.assertEqual(cm.exception.code, "not_undoable")

    def test_delete_stops_item_that_grew_after_confirm(self):
        # 확인한 양(limits)보다 실행 때 더 있으면 그 항목은 한 파일도 지우지 않는다 — 큐에서 기다리는 동안 늘어난 경우
        top = os.path.join(self.U, "Grew")
        a = mkfile(os.path.join(top, "a.mov"), 1000)
        other = mkfile(os.path.join(self.U, "ok.mov"), 50)
        late = mkfile(os.path.join(top, "late.mov"), 10)          # 확인 뒤 동료가 넣은 파일
        j = self.wait(self.q.submit_delete([top, other], limits={top: (1000, 1), other: (50, 1)}))
        self.assertEqual(j["status"], "partial", j)
        res = {r["path"]: r for r in j["results"]}
        self.assertFalse(res[top]["ok"])
        self.assertEqual(res[top]["error"], ops.R_GREW)
        self.assertTrue(os.path.exists(a) and os.path.exists(late))
        self.assertFalse(os.path.exists(other))
        self.assertEqual(j["freed"], {"U": 50})
        # 같거나 줄었으면 그대로 지운다
        os.remove(late)
        j = self.wait(self.q.submit_delete([top], limits={top: (1000, 1)}))
        self.assertEqual(j["status"], "done", j)
        self.assertFalse(os.path.exists(top))

    def test_delete_refusals(self):
        win = mkfile(os.path.join(self.C, "Windows", "x.dll"), 5)
        for p in (self.U, self.NAS, os.path.join(self.C, "Windows"), os.path.join(self.NAS, "@eaDir"),
                  os.path.join(self.tmp, "outside.bin"), os.path.join(self.U, "_drive.json")):
            with self.subTest(p=p):
                with self.assertRaises(ops.OpError) as cm:
                    self.q.submit_delete([p])
                self.assertEqual(cm.exception.code, "protected")
        self.assertTrue(os.path.exists(win))
        with self.assertRaises(ops.OpError) as cm:
            self.q.submit_delete([])
        self.assertEqual(cm.exception.code, "empty")
        j = self.wait(self.q.submit_delete([os.path.join(self.U, "missing.mov")]))
        self.assertEqual(j["status"], "failed", j)

    def test_delete_does_not_follow_links(self):
        outside = mkfile(os.path.join(self.tmp, "outside", "precious.bin"), 100)
        top = os.path.join(self.U, "proj")
        mkfile(os.path.join(top, "a.bin"), 10)
        try:
            os.symlink(os.path.dirname(outside), os.path.join(top, "link"))
        except (OSError, NotImplementedError):
            self.skipTest("symlink 불가")
        j = self.wait(self.q.submit_delete([top]))
        self.assertEqual(j["status"], "partial", j)
        self.assertTrue(os.path.exists(outside))
        self.assertTrue(os.path.islink(os.path.join(top, "link")))
        self.assertFalse(os.path.exists(os.path.join(top, "a.bin")))

    def test_delete_partial_on_locked_file(self):
        top = os.path.join(self.U, "p")
        mkfile(os.path.join(top, "a.bin"), 10)
        locked = mkfile(os.path.join(top, "b.bin"), 20)
        real = ops._remove_file

        def rm(p):
            if p == locked:
                raise PermissionError(13, "in use", p)
            return real(p)
        with mock.patch.object(ops, "_remove_file", rm):
            j = self.wait(self.q.submit_delete([top]))
        self.assertEqual(j["status"], "partial", j)
        self.assertTrue(os.path.exists(locked))
        self.assertEqual(j["freed"], {"U": 10})
        self.assertIn(("changed", top), self.calls)


class DeviceLossTests(DeviceBase):
    def _lose_after(self, n_writes, err):
        state = {"n": 0}
        real = ops._write

        def w(f, data):
            state["n"] += 1
            if state["n"] > n_writes:
                raise err
            return real(f, data)
        return w

    def test_device_removed_mid_file(self):
        self.q.chunk_size = 64 * 1024
        first = mkfile(os.path.join(self.D, "one", "a.bin"), 100_000)
        top = os.path.join(self.D, "two")
        mkfile(os.path.join(top, "b.bin"), 50_000)
        mkfile(os.path.join(top, "c.bin"), 400_000)
        third = mkfile(os.path.join(self.D, "three", "d.bin"), 10)
        before = tree(top)
        err = OSError(errno.ENODEV, "No such device")
        for mode in ("move", "copy"):
            with self.subTest(mode=mode):
                if mode == "copy":
                    first = mkfile(os.path.join(self.D, "one", "a.bin"), 100_000)
                    shutil.rmtree(os.path.join(self.U, "one"), ignore_errors=True)
                # a.bin 2청크 + b.bin 1청크 + c.bin 의 첫 청크까지 쓰고 장치가 빠진다
                with mock.patch.object(ops, "_write", self._lose_after(4, err)):
                    j = self.wait(self.q.submit_move([first, top, third], "U", mode=mode))
                self.assertEqual(j["status"], "partial", j)
                self.assertIn(ops.R_DEVLOST, j["message"])
                self.assertEqual([r["ok"] for r in j["results"]], [True, False, False])
                self.assertEqual(j["results"][1]["error"], ops.R_DEVLOST)
                self.assertEqual(j["results"][2]["error"], ops.R_DEVLOST)   # 멈췄다 — 다음 항목은 시도하지 않는다
                self.assertEqual(tree(top), before)                          # 원본 그대로
                self.assertTrue(os.path.exists(third))
                self.assertFalse(os.path.exists(os.path.join(self.U, "two")))
                self.assertFalse(os.path.exists(os.path.join(self.U, "three")))
                self.assertNoDbpart()

    def test_target_root_vanishes_with_io_error(self):
        top = os.path.join(self.D, "proj")
        mkfile(os.path.join(top, "a.bin"), 300_000)
        before = tree(top)
        self.q.chunk_size = 64 * 1024
        real = ops._write
        gone = os.path.join(self.tmp, "unplugged")
        unplugged = []
        real_alive = winapi.path_alive
        nas_key = os.path.normcase(self.NAS)

        def w(f, data):
            if os.name == "nt":
                # Windows 는 열린 파일(.dbpart)이 든 폴더의 이름을 바꾸지 못한다(PermissionError) — 폴더를
                # 치우는 대신, 실제로 NAS 가 끊겼을 때처럼 path_alive 가 「없다」고 답하게 한다
                unplugged.append(True)
            elif os.path.exists(self.NAS):
                os.rename(self.NAS, gone)                    # NAS 가 사라진다
            raise OSError(errno.EIO, "Input/output error")

        def alive(p, *a, **k):
            if unplugged and os.path.normcase(str(p)).startswith(nas_key):
                return False
            return real_alive(p, *a, **k)
        with mock.patch.object(ops, "_write", w), mock.patch.object(winapi, "path_alive", alive):
            j = self.wait(self.q.submit_move([top], self.NK, mode="move"))
        self.assertEqual(j["status"], "failed", j)
        self.assertEqual(j["results"][0]["error"], ops.R_DEVLOST)
        self.assertIn(ops.R_DEVLOST, j["message"])
        self.assertEqual(tree(top), before)
        self.assertNoDbpart()
        del real

    def test_source_device_removed(self):
        top = os.path.join(self.U, "card")
        mkfile(os.path.join(top, "a.mov"), 1000)
        mkfile(os.path.join(top, "b.mov"), 1000)
        before = tree(top)
        real = ops._open_read
        n = {"i": 0}

        def opener(p):
            n["i"] += 1
            if n["i"] > 1:
                raise OSError(errno.ENODEV, "gone", p)
            return real(p)
        with mock.patch.object(ops, "_open_read", opener):
            j = self.wait(self.q.submit_move([top], "C"))
        self.assertEqual(j["status"], "failed", j)
        self.assertEqual(j["results"][0]["error"], ops.R_DEVLOST)
        self.assertEqual(tree(top), before)
        self.assertFalse(os.path.exists(os.path.join(self.C, "card")))

    def test_disconnected_location_refused(self):
        self.q.stop(5)
        locs = [{"path": NAS_UNC, "label": "영상백업"}, {"path": "\\\\DEAD\\share", "label": "죽은 NAS"}]
        drives = lambda: winapi.list_drives(network_locations=locs)
        q = ops.JobQueue(self.home, drives, lambda p: safety.check(p, drives()))
        self.q = q
        q.start()
        src = mkfile(os.path.join(self.D, "p", "a.bin"), 10)
        dead = winapi.network_key("\\\\DEAD\\share")
        with self.assertRaises(ops.OpError) as cm:
            q.submit_move([src], dead, mode="copy")
        self.assertEqual((cm.exception.code, cm.exception.message), ("unknown_drive", ops.R_DEST_OFFLINE))
        j = self.wait(q.submit_move([src], self.NK.upper(), mode="copy"))   # 앱이 대문자로 보내도 같은 키
        self.assertEqual(j["status"], "done", j)

    def test_network_target_uses_small_chunks_and_reports_speed(self):
        src = mkfile(os.path.join(self.D, "p", "big.bin"), 5 * MiB + 3)
        sizes = []
        real = ops._write

        def w(f, data):
            sizes.append(len(data))
            time.sleep(0.12)
            return real(f, data)
        with mock.patch.object(ops, "_write", w):
            jid = self.q.submit_move([src], self.NK, mode="copy")
            speeds = []
            t0 = time.time()
            while time.time() - t0 < 30:
                jj = self.q.get(jid)
                if jj["status"] not in ("queued", "running"):
                    break
                if jj["speed_bps"]:
                    speeds.append((jj["speed_bps"], jj["eta_s"]))
                time.sleep(0.05)
        j = self.wait(jid)
        self.assertEqual(j["status"], "done", j)
        self.assertLessEqual(max(sizes), 1 << 20)
        self.assertTrue(speeds, "속도가 한 번도 보고되지 않았어요")
        self.assertTrue(all(e is not None and e >= 0 for _b, e in speeds))



class UndoDiskIdentityTests(Base):
    """되돌리기는 같은 문자에 '다른 디스크'가 붙어 있으면 그 디스크의 파일을 옮기거나 지우지 않는다."""

    def swap_disk(self, letter, serial="DEADBEEF"):
        d = os.path.join(self.root, letter)
        os.rename(d, os.path.join(self.tmp, "unplugged-" + letter))
        os.makedirs(d)
        with open(os.path.join(d, "_drive.json"), "w") as f:
            json.dump({"label": "OTHER", "kind": "hdd", "total": 100 * GiB, "serial": serial}, f)
        winapi.invalidate_usage()
        return d

    def test_move_undo_refused_on_swapped_disk(self):
        src = os.path.join(self.D, "proj")
        mkfile(os.path.join(src, "mine.mov"), 1000)
        jid = self.q.submit_move([src], "C")
        self.assertEqual(self.wait(jid)["status"], "done")
        C = self.swap_disk("C")
        theirs = mkfile(os.path.join(C, "proj", "colleague_master.mov"), 5000)
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo(jid)
        self.assertEqual(cm.exception.code, "drive_changed")
        self.assertIn("C:", cm.exception.message if hasattr(cm.exception, "message") else str(cm.exception))
        self.assertTrue(os.path.exists(theirs))
        self.assertFalse(os.path.exists(src))
        self.assertTrue(self.q.get(jid)["undoable"])          # 원래 디스크를 다시 꽂으면 되돌릴 수 있다

    def test_move_undo_checked_again_when_it_runs_and_after_restart(self):
        src = os.path.join(self.D, "proj")
        mkfile(os.path.join(src, "mine.mov"), 1000)
        jid = self.q.submit_move([src], "C")
        self.wait(jid)
        self.q.stop(5)
        self.q = self.make_queue()                            # 재시작: 기록된 일련번호가 남아 있어야 한다
        uid = self.q.undo(jid)                                # 아직 일꾼을 시작하지 않았다
        C = self.swap_disk("C")
        theirs = mkfile(os.path.join(C, "proj", "colleague_master.mov"), 5000)
        self.q.start()
        u = self.wait(uid)
        self.assertEqual(u["status"], "failed", u)
        self.assertIn("다른 디스크", u["message"])
        self.assertTrue(os.path.exists(theirs))
        self.assertFalse(os.path.exists(src))

    def test_move_undo_same_disk_still_works(self):
        src = os.path.join(self.D, "proj")
        mkfile(os.path.join(src, "mine.mov"), 1000)
        jid = self.q.submit_move([src], "C")
        self.wait(jid)
        u = self.wait(self.q.undo(jid))
        self.assertEqual(u["status"], "done", u)
        self.assertTrue(os.path.exists(os.path.join(src, "mine.mov")))

    def test_copy_undo_refused_on_swapped_disk(self):
        src = mkfile(os.path.join(self.D, "p", "clip.mov"), 1000, mtime=OLD)
        jid = self.q.submit_move([src], "C", mode="copy")
        self.assertEqual(self.wait(jid)["status"], "done")
        C = self.swap_disk("C")
        theirs = mkfile(os.path.join(C, "p", "clip.mov"), 1000, mtime=OLD)
        with self.assertRaises(ops.OpError) as cm:
            self.q.undo(jid)
        self.assertEqual(cm.exception.code, "drive_changed")
        self.assertTrue(os.path.exists(theirs))


class InterruptedMoveHistoryTests(Base):
    """이동 도중 앱이 죽거나 창이 닫혔을 때 — 기록이 '원본은 그대로'라고 거짓말하지 않는다."""

    def reload(self, snap):
        home2 = os.path.join(self.tmp, "home-%d" % time.monotonic_ns())
        os.makedirs(home2)
        with open(os.path.join(home2, "jobs.json"), "w", encoding="utf-8") as f:
            f.write(snap)
        drives = winapi.list_drives
        return ops.JobQueue(home2, drives, lambda p: safety.check(p, drives()), hooks={})

    def snapshot(self):
        with open(os.path.join(self.home, "jobs.json"), encoding="utf-8") as f:
            return f.read()

    def test_crash_while_copying_second_item_keeps_first_done_and_undoable(self):
        small = mkfile(os.path.join(self.D, "a", "small.mov"), 1000)
        big = mkfile(os.path.join(self.D, "b", "big.mov"), 300_000)
        self.q.chunk_size = 64 * 1024
        gate, hit = threading.Event(), threading.Event()
        real = ops._write

        def w(f, data):
            if "big.mov" in f.name:
                hit.set()
                gate.wait(10)
            return real(f, data)
        with mock.patch.object(ops, "_write", w):
            jid = self.q.submit_move([small, big], "C")
            self.assertTrue(hit.wait(10))
            time.sleep(0.05)
            snap = self.snapshot()                            # 지금 죽었다면 디스크에 남은 기록
            gate.set()
            self.wait(jid)
        self.assertFalse(os.path.exists(small))               # 첫 항목 원본은 이미 지워졌다
        persisted = json.loads(snap)["jobs"][-1]
        self.assertEqual([bool(it.get("done")) for it in persisted["_items"]], [True, False])
        j = self.reload(snap).get(jid)
        self.assertEqual(j["status"], "partial", j)
        self.assertTrue(j["undoable"])
        self.assertIn("끝난 1개는 옮겨졌어요", j["message"])
        self.assertNotIn("중단됐어요. 원본은 지워지지 않았어요", j["message"])
        self.assertIn("대상에 일부 사본이 남았을 수 있어요", j["message"])

    def test_crash_during_source_deletion_says_item_may_be_split(self):
        top = os.path.join(self.D, "proj")
        for i in range(5):
            mkfile(os.path.join(top, "f%d.mov" % i), 1000)
        gate, hit = threading.Event(), threading.Event()
        real = ops._remove_file
        n = {"i": 0}

        def rm(p):
            n["i"] += 1
            if n["i"] == 3:
                hit.set()
                gate.wait(10)
            return real(p)
        with mock.patch.object(ops, "_remove_file", rm):
            jid = self.q.submit_move([top], "C")
            self.assertTrue(hit.wait(10))
            snap = self.snapshot()
            gate.set()
            self.wait(jid)
        j = self.reload(snap).get(jid)
        self.assertEqual(j["status"], "failed", j)
        self.assertFalse(j["undoable"])
        self.assertIn("원본을 지우던 중", j["message"])
        self.assertIn(os.path.join(self.C, "proj"), j["message"])
        self.assertNotIn("원본은 지워지지 않았어요", j["message"])

    def test_interrupted_before_any_deletion_keeps_old_wording(self):
        big = mkfile(os.path.join(self.D, "b", "big.mov"), 300_000)
        self.q.chunk_size = 64 * 1024
        gate, hit = threading.Event(), threading.Event()
        real = ops._write

        def w(f, data):
            hit.set()
            gate.wait(10)
            return real(f, data)
        with mock.patch.object(ops, "_write", w):
            jid = self.q.submit_move([big], "C")
            self.assertTrue(hit.wait(10))
            snap = self.snapshot()
            gate.set()
            self.wait(jid)
        j = self.reload(snap).get(jid)
        self.assertEqual(j["status"], "failed")
        self.assertIn("원본은 지워지지 않았어요.", j["message"])
        self.assertIn("대상에 일부 사본이 남았을 수 있어요: " + os.path.join(self.C, "b", "big.mov"), j["message"])

    def test_stop_waits_for_source_deletion_phase(self):
        top = os.path.join(self.D, "proj")
        for i in range(30):
            mkfile(os.path.join(top, "f%02d.mov" % i), 1000)
        real = ops._remove_file

        def slow(p):
            time.sleep(0.03)
            return real(p)
        with mock.patch.object(ops, "_remove_file", slow):
            jid = self.q.submit_move([top], "C")
            t0 = time.time()
            while time.time() - t0 < 10 and len(os.listdir(top)) >= 30:
                time.sleep(0.005)
            self.q.stop(timeout=0.05)                         # 창 닫기 → 짧은 타임아웃
        self.assertFalse(os.path.exists(top) and os.listdir(top), "원본 삭제가 중간에 끊겼어요")
        self.assertEqual(len(os.listdir(os.path.join(self.C, "proj"))), 30)
        j = self.reload(self.snapshot()).get(jid)
        self.assertEqual(j["status"], "done", j)
        self.assertTrue(j["undoable"])


class SourceUnplugDuringDeleteTests(DeviceBase):
    def test_source_unplugged_while_deleting_is_not_counted_as_moved(self):
        top = os.path.join(self.U, "shoot")
        for i in range(5):
            mkfile(os.path.join(top, "c%d.mov" % i), 1000)
        gone = os.path.join(self.tmp, "unplugged-U")
        real = ops._remove_file
        n = {"i": 0}

        def rm(p):
            n["i"] += 1
            if n["i"] == 2:
                os.rename(self.U, gone)                       # USB 가 빠졌다 — Windows 는 '경로 없음'을 준다
            if not os.path.exists(os.path.dirname(p)):
                raise FileNotFoundError(errno.ENOENT, "The system cannot find the path specified", p)
            return real(p)
        with mock.patch.object(ops, "_remove_file", rm):
            j = self.wait(self.q.submit_move([top], "C"))
        self.assertEqual(j["status"], "partial", j)
        self.assertIn(ops.R_DEVLOST, j["message"])
        self.assertFalse(j["results"][0]["ok"])
        self.assertIn(ops.R_DEVLOST, j["results"][0]["error"])
        self.assertEqual(j["freed"], {"U": 1000})            # 실제로 지운 것은 1개뿐
        self.assertEqual(len(os.listdir(os.path.join(gone, "shoot"))), 4)
        self.assertEqual(len(os.listdir(os.path.join(self.C, "shoot"))), 5)   # 사본은 거두지 않는다
        self.assertNotIn(("removed", top), self.calls)
        self.assertFalse(j["undoable"])


if __name__ == "__main__":
    unittest.main()

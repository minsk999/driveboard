"""Scanner: skipping rules, sequences, progress, cancel, duplicate refusal, subtree scans."""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
if os.name == "nt":      # 임시 폴더를 긴 이름으로(…\RUNNER~1\… → …\runneradmin\…): 제품이 경로를 긴 이름으로
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())     # 돌려주므로 시험의 기대값도 같은 모양이어야 한다

from core import model, scanner  # noqa: E402
from core import categories as C  # noqa: E402
from core.categories import strip_long_prefix  # noqa: E402
from make_fake_drives import make, write_tree  # noqa: E402

MB = 1 << 20


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dbtest-scan-")
        self.root = os.path.join(self.tmp, "D")
        os.makedirs(self.root)
        self.drive = {"letter": "D", "root": self.root, "label": "SSD1", "serial": "SER-D",
                      "fs": "NTFS", "kind": "ssd"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def P(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def scan(self, settings=None):
        idx = scanner.scan_subtree(self.root, self.drive, settings or {})
        self.assertEqual(model.check_invariants(idx), [])
        return idx


class TestWalk(Tmp):
    def test_skips(self):
        write_tree(self.root, {
            "keep/a.mov": 60 * MB,
            "$Recycle.Bin/S-1-5/$R123.mov": 70 * MB,
            "System Volume Information/x": 5,
            ".fakebin/20250101_x.mov": 80 * MB,
            "_drive.json": 100,
            "keep/_drive.json": 7,                         # only skipped at the root
            "keep/.fakebin/z": 9,
            "excluded/big.mov": 90 * MB,
            "keep/excl2/c.mov": 91 * MB,
        })
        os.symlink(self.P("keep"), self.P("link_dir"))
        os.symlink(self.P("keep/a.mov"), self.P("keep/link_file.mov"))
        os.symlink(self.P("nowhere"), self.P("broken"))
        idx = self.scan({"excluded": [self.P("excluded"), self.P("keep/excl2")]})
        self.assertEqual(idx.d_size[0], 60 * MB + 7 + 9)
        self.assertEqual(idx.d_files[0], 3)
        self.assertEqual(idx.f_name, ["a.mov"])
        self.assertIsNone(model.find_dir(idx, self.P("link_dir")))
        self.assertIsNone(model.find_dir(idx, self.P("$Recycle.Bin")))
        self.assertIsNotNone(model.find_dir(idx, self.P("keep/.fakebin")))

    def test_system_context_only_on_internal(self):
        write_tree(self.root, {
            "Windows/백업본.mov": 60 * MB,
            "Users/kim/AppData/Local/x/a.mov": 60 * MB,
            "Media Cache Files/c.cfa": 60 * MB,
        })
        cats = lambda idx: {n: c for n, c in zip(idx.f_name, idx.f_cat)}  # noqa: E731
        got = cats(self.scan())
        self.assertEqual((got["백업본.mov"], got["a.mov"]), (C.APP, C.APP))     # 내장: 앱·시스템
        for extra in ({"external": True}, {"kind": "usb"}, {"kind": "network", "network": True}):
            self.drive = dict(self.drive, **extra)
            got = cats(self.scan())
            self.assertNotEqual(got["백업본.mov"], C.APP, extra)                 # 외장·USB·NAS: 사용자 데이터
            self.assertNotEqual(got["a.mov"], C.APP, extra)
            self.assertEqual(got["c.cfa"], C.CACHE, extra)                       # 캐시 규칙은 그대로
        self.assertFalse(scanner.is_internal({"external": True}))
        self.assertTrue(scanner.is_internal({"kind": "hdd"}))

    def test_sequences(self):
        files = {}
        for i in range(1, 11):
            files["render/exr/sh_%04d.exr" % i] = 6 * MB        # 60MB -> big sequence
        for i in range(1, 5):
            files["render/few/f_%04d.exr" % i] = 30 * MB        # only 4 frames -> files
        for i in range(1, 6):
            files["render/mixed/m_%03d.png" % i] = 1 * MB       # 3 digits group
            files["render/mixed/m_%04d.png" % i] = 1 * MB       # 4 digits group
        for i in range(1, 7):
            files["cache/c_%04d.cfa" % i] = 10 * MB             # cache ext never grouped
            files["clips/C%04d.MP4" % i] = 10 * MB              # video never grouped
            files["toolong/t_%09d.exr" % i] = 1 * MB            # 9 digits: not frames
            files["gaps/g_%04d.dpx" % (i * 10)] = 20 * MB       # gaps are fine
        write_tree(self.root, files)
        idx = self.scan()
        names = sorted(idx.f_name)
        self.assertIn("sh_[####].exr", names)
        self.assertIn("g_[####].dpx", names)
        self.assertEqual(sum(1 for n in names if n.startswith("f_")), 0)   # 30MB each < 50MB
        k, sid = model.find_item(idx, self.P("render/exr/sh_[####].exr"))
        self.assertEqual((idx.f_count[sid], idx.f_size[sid], idx.f_seq[sid]), (10, 60 * MB, (1, 10)))
        self.assertEqual(idx.f_cat[sid], C.RENDER)
        k, gid = model.find_item(idx, self.P("gaps/g_[####].dpx"))
        self.assertEqual((idx.f_count[gid], idx.f_seq[gid]), (6, (10, 60)))
        self.assertEqual(idx.f_cat[gid], C.IMAGE)
        self.assertEqual(len(model.frames_of(idx, gid)), 6)
        md = model.find_dir(idx, self.P("render/mixed"))
        self.assertEqual(idx.d_files[md], 10)
        self.assertEqual(idx.d_ofiles[md], 10)     # two small sequences, counted per frame
        self.assertEqual(model.cat_counts(idx)[C.CACHE], 6)
        self.assertEqual(idx.d_files[0], 10 + 4 + 10 + 6 + 6 + 6 + 6)

    def test_big_threshold_setting(self):
        write_tree(self.root, {"a.mov": 20 * MB, "b.mov": 9 * MB})
        idx = self.scan({"big_file_mb": 10})
        self.assertEqual(idx.f_name, ["a.mov"])
        self.assertEqual(idx.big_bytes, 10 * MB)
        idx = self.scan({"big_file_mb": 1})
        self.assertEqual(sorted(idx.f_name), ["a.mov", "b.mov"])

    def test_classification_end_to_end(self):
        write_tree(self.root, {
            "Users/kim/AppData/Local/Temp/t.mp4": 60 * MB,
            "Users/kim/AppData/Local/Adobe/x.dat": 60 * MB,
            "Program Files/Adobe/AfterFX.exe": 60 * MB,
            "pagefile.sys": 60 * MB,
            "work/footage/C0001.MP4": 60 * MB,
            "work/render/out_v02.mov": 60 * MB,
        })
        idx = self.scan({"rules": [{"path": self.P("work/render"), "cat": "archive"}]})
        cats = model.cat_totals(idx)
        self.assertEqual(cats[C.CACHE], 60 * MB)
        self.assertEqual(cats[C.APP], 180 * MB)
        self.assertEqual(cats[C.SOURCE], 60 * MB)
        self.assertEqual(cats[C.ARCHIVE], 60 * MB)

    def test_scandir_errors_are_counted(self):
        write_tree(self.root, {"ok/a.mov": 1, "locked/b.mov": 2, "locked/sub/c.mov": 3})
        real = os.scandir
        locked = self.P("locked")

        def fake(p):
            # Windows 에선 스캐너가 \\\\?\\ 접두사를 붙여 부른다(_scan_path) — 떼고 비교한다
            if os.path.normpath(strip_long_prefix(p)) == locked:
                raise PermissionError(13, "denied", p)
            return real(p)
        with mock.patch.object(scanner.os, "scandir", side_effect=fake):
            idx = self.scan()
        self.assertEqual(idx.errors, 1)
        self.assertEqual(idx.error_samples, [locked])
        self.assertEqual(idx.d_size[0], 1)
        self.assertIsNotNone(model.find_dir(idx, locked))

    def test_error_samples_capped(self):
        dirs = ["d%02d" % i for i in range(30)]
        write_tree(self.root, {}, dirs=dirs)
        real = os.scandir

        def fake(p):
            if os.path.basename(p).startswith("d"):
                raise PermissionError(13, "denied", p)
            return real(p)
        with mock.patch.object(scanner.os, "scandir", side_effect=fake):
            idx = self.scan()
        self.assertEqual(idx.errors, 30)
        self.assertEqual(len(idx.error_samples), 20)

    def test_korean_and_odd_names(self):
        write_tree(self.root, {"2025_한결증권_브랜드필름/footage/C0001.MP4": 70 * MB,
                               "공백 있는 폴더/최종 (2).mp4": 55 * MB,
                               "a [b]/x#1.mov": 51 * MB})
        idx = self.scan()
        p = self.P("2025_한결증권_브랜드필름/footage/C0001.MP4")
        k, fid = model.find_item(idx, p)
        self.assertEqual(model.path_of_file(idx, fid), p)
        self.assertEqual(idx.f_cat[fid], C.SOURCE)
        k, fid = model.find_item(idx, self.P("공백 있는 폴더/최종 (2).mp4"))
        self.assertEqual(idx.f_cat[fid], C.RENDER)
        self.assertIsNotNone(model.find_item(idx, self.P("a [b]/x#1.mov")))

    def test_long_path_prefix_only_on_windows(self):
        self.assertFalse(scanner.IS_NT and os.name != "nt")
        if os.name != "nt":
            self.assertEqual(model._scan_path("/x/y"), "/x/y")
            return
        # Windows: 확장 길이 접두사(긴 경로·끝 점/공백 이름도 열 수 있게)를 붙이고, 이미 있으면 그대로
        self.assertEqual(model._scan_path("/x/y"), "\\\\?\\" + os.path.abspath("/x/y"))
        self.assertEqual(model._scan_path("D:\\a\\b"), "\\\\?\\D:\\a\\b")
        self.assertEqual(model._scan_path("\\\\srv\\share\\a"), "\\\\?\\UNC\\srv\\share\\a")
        self.assertEqual(model._scan_path("\\\\?\\D:\\a"), "\\\\?\\D:\\a")


class TestSubtree(Tmp):
    def test_dir_file_seq(self):
        write_tree(self.root, {"p/render/a.mov": 60 * MB, "p/render/b.txt": 3,
                               **{"p/render/s/f_%03d.exr" % i: 11 * MB for i in range(5)}})
        sub = scanner.scan_subtree(self.P("p/render"), self.drive, {})
        self.assertFalse(sub.partial)
        self.assertEqual(sub.root, self.P("p/render"))
        self.assertEqual(sub.d_size[0], 60 * MB + 3 + 55 * MB)
        self.assertEqual(model.cat_totals(sub)[C.RENDER], 115 * MB)    # render context kept
        f = scanner.scan_subtree(self.P("p/render/a.mov"), self.drive, {})
        self.assertTrue(f.partial)
        self.assertEqual(f.root, self.P("p/render"))
        self.assertEqual((f.f_name, f.d_size[0], f.f_cat[0]), (["a.mov"], 60 * MB, C.RENDER))
        s = scanner.scan_subtree(self.P("p/render/s/f_[###].exr"), self.drive, {})
        self.assertEqual((s.f_name, s.f_count[0], s.f_seq[0]), (["f_[###].exr"], 5, (0, 4)))
        s = scanner.scan_subtree(self.P("p/render/s/f_[###].exr"), self.drive, {},
                                 seq_range=(1, 3))
        self.assertEqual((s.nitems, s.d_files[0], s.d_size[0]), (0, 3, 33 * MB))
        with self.assertRaises(FileNotFoundError):
            scanner.scan_subtree(self.P("p/none.mov"), self.drive, {})
        with self.assertRaises(FileNotFoundError):
            scanner.scan_subtree(self.P("p/render/s/zz_[###].exr"), self.drive, {})
        with self.assertRaises(ValueError):
            scanner.scan_subtree(os.path.join(self.tmp, "E", "x"), self.drive, {})

    def test_symlink_file_refused(self):
        write_tree(self.root, {"a.mov": 1})
        os.symlink(self.P("a.mov"), self.P("l.mov"))
        with self.assertRaises(ValueError):
            scanner.scan_subtree(self.P("l.mov"), self.drive, {})


def snapshot(idx):
    """Everything that must match a fresh scan (mtimes excluded)."""
    out = {}
    for d in range(idx.ndirs):
        if idx.d_alive[d]:
            out[("d", model.path_of_dir(idx, d))] = (
                idx.d_size[d], idx.d_files[d], tuple(model.cat_totals(idx, d)),
                tuple(model.cat_counts(idx, d)), idx.d_own[d], idx.d_ofiles[d])
    for f in range(idx.nitems):
        if idx.f_alive[f]:
            out[("f", model.path_of_file(idx, f))] = (
                idx.f_size[f], idx.f_count[f], idx.f_cat[f], idx.f_seq.get(f))
    return out


class TestRefresh(Tmp):
    def setUp(self):
        super().setUp()
        write_tree(self.root, {
            "p/footage/C0001.MP4": 300 * MB, "p/footage/C0002.MP4": 10 * MB,
            "p/footage/note.txt": 5, "p/render/final.mp4": 80 * MB,
            **{"p/render/exr/s_%04d.exr" % i: 3 * MB for i in range(1, 21)},
            "q/x.zip": 70 * MB, "top.txt": 9,
        })
        self.idx = self.scan()

    def check(self):
        self.assertEqual(model.check_invariants(self.idx), [])
        fresh = scanner.scan_subtree(self.root, self.drive, {})
        self.assertEqual(snapshot(self.idx), snapshot(fresh))

    def refresh(self, rel):
        scanner.refresh(self.idx, self.P(rel), self.drive, {})
        self.check()

    def test_small_file_removed_and_added(self):
        os.unlink(self.P("p/footage/C0002.MP4"))
        self.refresh("p/footage/C0002.MP4")
        write_tree(self.root, {"p/footage/C0003.MP4": 12 * MB})
        self.refresh("p/footage/C0003.MP4")
        self.refresh("p/footage/C0003.MP4")          # idempotent: no double counting
        write_tree(self.root, {"top2.txt": 3})
        self.refresh("top2.txt")                     # directly in the drive root

    def test_big_file_and_folder(self):
        os.unlink(self.P("p/footage/C0001.MP4"))
        self.refresh("p/footage/C0001.MP4")
        shutil.rmtree(self.P("q"))
        self.refresh("q")
        write_tree(self.root, {"new/a/b/c.mov": 90 * MB, "new/a/d.txt": 1})
        self.refresh("new/a/b/c.mov")                # unknown parents: scans "new"
        self.assertIsNotNone(model.find_dir(self.idx, self.P("new/a/b")))

    def test_sequence_changes(self):
        write_tree(self.root, {"p/render/exr/s_%04d.exr" % i: 3 * MB for i in range(21, 26)})
        self.refresh("p/render/exr/s_[####].exr")
        k, sid = model.find_item(self.idx, self.P("p/render/exr/s_[####].exr"))
        self.assertEqual((self.idx.f_count[sid], self.idx.f_seq[sid]), (25, (1, 25)))
        for i in range(1, 26):
            os.unlink(self.P("p/render/exr/s_%04d.exr" % i))
        self.refresh("p/render/exr/s_[####].exr")
        self.assertIsNone(model.find_item(self.idx, self.P("p/render/exr/s_[####].exr")))

    def test_dir_replaced_by_file_and_missing_parents(self):
        shutil.rmtree(self.P("q"))
        write_tree(self.root, {"q": 60 * MB})
        self.refresh("q")
        shutil.rmtree(self.P("p/render"))
        self.refresh("p/render/exr/s_0001.exr")       # its parents are gone too
        self.assertIsNone(model.find_dir(self.idx, self.P("p/render")))
        with self.assertRaises(ValueError):
            scanner.refresh(self.idx, os.path.join(self.tmp, "E", "x"), self.drive, {})

    def test_refresh_files_and_size_of(self):
        self.assertEqual(model.size_of(self.idx, self.P("p/footage")), (310 * MB + 5, 3))
        self.assertEqual(model.size_of(self.idx, self.P("p/render/exr/s_[####].exr")),
                         (60 * MB, 20))
        self.assertIsNone(model.size_of(self.idx, self.P("p/footage/note.txt")))
        write_tree(self.root, {"p/footage/extra.wav": 2 * MB})
        self.assertTrue(scanner.refresh_files(self.idx, self.P("p/footage"), self.drive, {}))
        self.check()
        self.assertFalse(scanner.refresh_files(self.idx, self.P("nope"), self.drive, {}))


class TestRepresentativeVideo(Tmp):
    """SPEC-2 B.3: 폴더마다 대표 영상 후보 1개 (크기 무관, render > video > source, mtime 최신)."""
    T0 = 1_700_000_000.0

    def vid(self, idx, rel):
        d = model.find_dir(idx, self.P(rel) if rel else self.root)
        return idx.d_vid.get(d)

    def test_selection_rules(self):
        t = self.T0
        write_tree(self.root, {
            "p/render/a_comp_v01.mp4": (3 * MB, t + 10),
            "p/render/a_comp_v02.mp4": (2 * MB, t + 20),        # newest render wins
            "p/render/still.png": (1 * MB, t + 99),             # image: never a candidate
            "p/mix/clip.mov": (5, t + 50),                      # tiny files count too
            "p/mix/C0001.MP4": (9 * MB, t + 60),                # camera name -> source
            "p/mix/Z_final.mp4": (1, t + 1),                    # render by name beats newer
            "p/footage/C0002.MP4": (4 * MB, t + 70),
            "p/Proxy/C0002_Proxy.mp4": (1 * MB, t + 80),        # proxy: no
            "p/Media Cache Files/x.mp4": (1 * MB, t + 90),      # cache context: no
            "p/aep/a.aep": (1, t), "p/aep/b.txt": (1, t),
            "p/doc/x.pdf": (1, t),
            "p/same/b.mov": (1, t + 5), "p/same/a.mov": (1, t + 5),   # tie -> name
            "Windows/System32/demo.mp4": (1, t),                # app context: no
            "top.mp4": (1, t),
        })
        idx = self.scan()
        self.assertEqual(self.vid(idx, "p/render"), ("a_comp_v02.mp4", 2 * MB, t + 20, C.RENDER))
        self.assertEqual(self.vid(idx, "p/mix")[0], "Z_final.mp4")
        self.assertEqual(self.vid(idx, "p/footage")[3], C.SOURCE)
        for rel in ("p/Proxy", "p/Media Cache Files", "p/aep", "p/doc", "Windows/System32", "p"):
            self.assertIsNone(self.vid(idx, rel), rel)
        self.assertEqual(self.vid(idx, "p/same")[0], "a.mov")
        self.assertEqual(self.vid(idx, "")[0], "top.mp4")
        self.assertEqual({model.path_of_dir(idx, d) for d in idx.d_aep}, {self.P("p/aep")})
        bd, best = model.best_video(idx, model.find_dir(idx, self.P("p")))
        self.assertEqual((model.path_of_dir(idx, bd), best[0]),
                         (self.P("p/render"), "a_comp_v02.mp4"))
        self.assertEqual(model.best_video(idx, 0)[1][0], "a_comp_v02.mp4")

    def test_numbered_aep_versions_still_mark_folder(self):
        write_tree(self.root, {"p/aep/BF_v%03d.aep" % i: 10 for i in range(1, 8)})
        idx = self.scan()
        self.assertIn(model.find_dir(idx, self.P("p/aep")), idx.d_aep)
        write_tree(self.root, {"q/Adobe After Effects Auto-Save/x auto-save 1.aep": 10})
        self.assertNotIn(model.find_dir(self.scan(), self.P("q/Adobe After Effects Auto-Save")),
                         self.scan().d_aep)

    def test_refresh_keeps_candidates_current(self):
        t = self.T0
        write_tree(self.root, {"p/render/a.mp4": (1 * MB, t + 1), "p/render/b.mp4": (1, t + 2),
                               "p/aep/x.aep": (1, t)})
        idx = self.scan()
        os.unlink(self.P("p/render/b.mp4"))
        scanner.refresh(idx, self.P("p/render/b.mp4"), self.drive, {})
        self.assertEqual(self.vid(idx, "p/render")[0], "a.mp4")
        write_tree(self.root, {"p/render/c.mp4": (2, t + 3)})
        scanner.refresh(idx, self.P("p/render/c.mp4"), self.drive, {})
        self.assertEqual(self.vid(idx, "p/render")[0], "c.mp4")
        os.unlink(self.P("p/render/a.mp4"))
        os.unlink(self.P("p/render/c.mp4"))
        scanner.refresh(idx, self.P("p/render/c.mp4"), self.drive, {})
        self.assertIsNone(self.vid(idx, "p/render"))
        os.unlink(self.P("p/aep/x.aep"))
        scanner.refresh(idx, self.P("p/aep/x.aep"), self.drive, {})
        self.assertEqual(idx.d_aep, set())
        write_tree(self.root, {"new/render/n.mp4": (1, t + 9)})
        scanner.refresh(idx, self.P("new"), self.drive, {})
        self.assertEqual(self.vid(idx, "new/render")[0], "n.mp4")
        shutil.rmtree(self.P("new"))
        scanner.refresh(idx, self.P("new"), self.drive, {})
        self.assertEqual(model.check_invariants(idx), [])
        self.assertEqual(idx.d_vid, {})
        # a file grafted into a folder (e.g. moved in) merges with the existing candidate
        write_tree(self.root, {"p/render/old.mp4": (1, t), "p/render/newer.mp4": (1, t + 50)})
        scanner.refresh(idx, self.P("p/render/old.mp4"), self.drive, {})
        self.assertEqual(self.vid(idx, "p/render")[0], "newer.mp4")

    def test_apply_rule_updates_candidate(self):
        write_tree(self.root, {"p/x/a.mp4": 1, "p/y/b.mp4": 1})
        idx = self.scan()
        model.apply_rule(idx, self.P("p/x"), C.RENDER)
        self.assertEqual(self.vid(idx, "p/x")[3], C.RENDER)
        model.apply_rule(idx, self.P("p/y/b.mp4"), C.CACHE)
        self.assertIsNone(self.vid(idx, "p/y"))
        self.assertEqual(model.check_invariants(idx), [])

    def test_network_key_is_kept_lowercase(self):
        write_tree(self.root, {"a.mov": 1})
        h = scanner.start_scan(dict(self.drive, letter="n-3FA9C1"), {})
        self.assertEqual(h.letter, "N-3fa9c1")
        h.join(10)
        self.assertEqual(scanner.get_scan("N-3FA9C1"), None)


class TestAsync(Tmp):
    def test_progress_and_result(self):
        write_tree(self.root, {"d%02d/f%03d.mov" % (i, j): (j + 1) * MB
                               for i in range(20) for j in range(30)})
        events = []
        with mock.patch.object(scanner, "PROGRESS_INTERVAL", 0.0):
            h = scanner.start_scan(self.drive, {}, events.append)
            self.assertTrue(h.join(30))
        self.assertIsNone(h.error)
        idx = h.result
        self.assertIsNotNone(idx)
        self.assertEqual(model.check_invariants(idx), [])
        phases = [e["phase"] for e in events]
        self.assertIn("scan", phases)
        self.assertIn("finalize", phases)
        self.assertEqual(phases[-1], "done")
        last = events[-1]
        self.assertEqual(last["files"], 600)
        self.assertEqual(last["bytes"], idx.d_size[0])
        self.assertEqual(sum(last["cats"]), idx.d_size[0])
        self.assertEqual(last["letter"], "D")
        for e in events:
            self.assertEqual(set(e) >= {"phase", "files", "dirs", "bytes", "current", "cats",
                                        "started_at"}, True)
            self.assertEqual(len(e["cats"]), C.NCAT)
        # running totals never decrease
        b = [e["bytes"] for e in events if e["phase"] == "scan"]
        self.assertEqual(b, sorted(b))
        self.assertEqual(h.progress["phase"], "done")
        self.assertGreater(idx.scanned_at, 0)
        self.assertEqual(idx.serial, "SER-D")
        self.assertFalse(scanner.is_scanning("D"))

    def test_duplicate_refused_and_cancel(self):
        write_tree(self.root, {"d%03d/f.txt" % i: 1 for i in range(50)})
        gate = threading.Event()
        started = threading.Event()

        def cb(ev):
            if ev["phase"] == "scan":
                started.set()
                gate.wait(10)
        with mock.patch.object(scanner, "PROGRESS_INTERVAL", 0.0):
            h = scanner.start_scan(self.drive, {}, cb)
            self.assertTrue(started.wait(10))
            self.assertTrue(scanner.is_scanning("d"))
            with self.assertRaises(scanner.ScanBusyError):
                scanner.start_scan(dict(self.drive), {}, None)
            other = dict(self.drive, letter="E")
            h2 = scanner.start_scan(other, {}, None)      # another letter is fine
            h.cancel()
            gate.set()
            self.assertTrue(h.join(10))
            self.assertTrue(h2.join(10))
        self.assertIsNone(h.result)
        self.assertEqual(h.progress["phase"], "canceled")
        self.assertIsNotNone(h2.result)
        # after it finished, the same letter can be scanned again
        h3 = scanner.start_scan(self.drive, {}, None)
        self.assertTrue(h3.join(10))
        self.assertIsNotNone(h3.result)

    def test_error_phase(self):
        bad = dict(self.drive, letter="Q", root=os.path.join(self.tmp, "missing"))
        events = []
        h = scanner.start_scan(bad, {}, events.append)
        self.assertTrue(h.join(10))
        self.assertIsNone(h.result)
        self.assertTrue(h.error)
        self.assertEqual(events[-1]["phase"], "error")

    def test_callback_exception_does_not_kill_scan(self):
        write_tree(self.root, {"a.mov": 1})

        def cb(ev):
            raise RuntimeError("ui broke")
        h = scanner.start_scan(self.drive, {}, cb)
        self.assertTrue(h.join(10))
        self.assertIsNotNone(h.result)


class TestFakeDrives(unittest.TestCase):
    def test_small_fake_drives_scan(self):
        tmp = tempfile.mkdtemp(prefix="dbtest-fake-")
        try:
            out = os.path.join(tmp, "fake")
            t = time.time()
            rep = make(out, "small")
            self.assertLess(time.time() - t, 30)
            self.assertEqual([r["letter"] for r in rep], list("CDEFGHI"))
            total_files = 0
            for r in rep:
                root = os.path.join(out, r["letter"])
                idx = scanner.scan_subtree(root, {"letter": r["letter"], "root": root}, {})
                self.assertEqual(model.check_invariants(idx), [])
                # the generator's byte count includes "System Volume Information" (skipped)
                self.assertLessEqual(abs(idx.d_size[0] - r["file_bytes"]), 64 << 10)
                self.assertGreaterEqual(r["system_bytes"], 0)
                total_files += idx.d_files[0]
            self.assertLess(total_files, 6000)
            with self.assertRaises(SystemExit):
                make(out, "small")                          # refuses a non-empty folder
            os.makedirs(os.path.join(out, "not_a_drive"))
            with self.assertRaises(SystemExit):
                make(out, "small", clean=True)              # never deletes foreign folders
            self.assertTrue(os.path.isdir(os.path.join(out, "C")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestFakeProfiles(unittest.TestCase):
    """SPEC-2: lead (내장 7 + J·K·NAS) / member (C+D) 프로필, 팀 규칙 프로젝트와 실제 영상."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dbtest-prof-")
        cls.media = os.path.join(cls.tmp, "media")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self, profile, **kw):
        import json
        out = os.path.join(self.tmp, profile + str(len(os.listdir(self.tmp))))
        t = time.time()
        rep = make(out, "small", profile=profile, media_cache=self.media, **kw)
        self.assertLess(time.time() - t, 60)
        metas = {}
        for r in rep:
            with open(os.path.join(out, r["letter"], "_drive.json"), encoding="utf-8") as f:
                metas[r["letter"]] = json.load(f)
        return out, rep, metas

    def scan_projects(self, out, rep):
        from core import projects
        allp = []
        for r in rep:
            root = os.path.join(out, r["letter"])
            idx = scanner.scan_subtree(root, {"letter": r["letter"], "root": root}, {})
            self.assertEqual(model.check_invariants(idx), [])
            self.assertLessEqual(abs(idx.d_size[0] - r["file_bytes"]), 64 << 10)
            allp += projects.detect_projects(idx, {})
        return allp

    def test_lead(self):
        import make_fake_drives as M
        from core import settings as S_
        from core import winapi
        out, rep, metas = self.build("lead")
        keys = [r["letter"] for r in rep]
        nk = winapi.network_key("\\\\ORICO\\영상백업")
        self.assertEqual(nk, S_.network_key("//ORICO/영상백업"))
        self.assertEqual(keys, list("CDEFGHIJK") + [nk])
        self.assertEqual(M.network_key(M.NAS_UNC), nk)
        self.assertEqual({k: (m["kind"], m["external"], m["has_bin"], m["network"])
                          for k, m in metas.items() if k in ("C", "H", "J", "K", nk)},
                         {"C": ("ssd", False, True, False), "H": ("hdd", False, True, False),
                          "J": ("ssd", True, True, False), "K": ("usb", True, False, False),
                          nk: ("network", False, False, True)})
        self.assertEqual(metas["J"]["label"], "Samsung T7")
        self.assertEqual(metas[nk]["unc"], "\\\\ORICO\\영상백업")
        self.assertEqual(metas[nk]["label"], "ORICO NAS · 영상백업")
        for k in ("K", nk):
            self.assertFalse(os.path.exists(os.path.join(out, k, ".fakebin")))
        self.assertTrue(os.path.isdir(os.path.join(out, "J", ".fakebin")))
        self.assertLess(sum(r["files"] for r in rep), 9000)
        ps = self.scan_projects(out, rep)
        names = {p["name"] for p in ps}
        for want in ("260803_소담코스_녹차_진정크림_배너결합_KMS", "251023_한결증권_브랜드필름_편집_HYC",
                     "260115_바른기술_인터뷰_JJH", "2025-11_한빛은행_런칭"):
            self.assertIn(want, names)
        self.assertNotIn("소담코스_진정크림", names)          # aep 없음: project_roots 를 지정해야 보인다
        self.assertGreaterEqual(len({p["client"] for p in ps if p["date_source"] == "name"}), 10)
        self.assertGreaterEqual(len({p["worker"] for p in ps if p["worker"]}), 5)
        self.assertTrue({2024, 2025, 2026} <= {p["year"] for p in ps})
        self.assertTrue(any(p["drive"] == nk for p in ps))
        self.assertTrue(any(p["drive"] == "J" for p in ps))
        prev = [p["preview"] for p in ps if p["preview"]]
        self.assertTrue(any(p is None for p in (x["preview"] for x in ps)))   # 영상 없는 것도
        self.assertTrue(any(v["name"].endswith("_ProRes.mov") for v in prev))
        p = next(x for x in ps if x["name"] == "260803_소담코스_녹차_진정크림_배너결합_KMS")
        self.assertEqual(p["preview"]["name"], "GD_15s_최종.mp4")
        if M.demo_clips(self.media)["h264"]:                  # ffmpeg 가 있으면 진짜 영상
            self.assertGreater(os.path.getsize(p["preview"]["path"]), 1000)
            with open(p["preview"]["path"], "rb") as f:
                self.assertEqual(f.read(12)[4:8], b"ftyp")
            self.assertGreater(sum(r["real_files"] for r in rep), 30)
            self.assertLess(sum(r["real_bytes"] for r in rep), 120 << 20)

    def test_member_and_no_video(self):
        out, rep, metas = self.build("member", videos=False)
        self.assertEqual([r["letter"] for r in rep], ["C", "D"])
        self.assertEqual((metas["C"]["kind"], metas["C"]["total"]), ("ssd", 1000202273280))
        self.assertEqual((metas["D"]["kind"], metas["D"]["total"]), ("hdd", 2000398934016))
        self.assertEqual(sum(r["real_files"] for r in rep), 0)
        ps = self.scan_projects(out, rep)
        self.assertGreaterEqual(len(ps), 8)
        p = next(x for x in ps if x["name"] == "260803_소담코스_녹차_진정크림_배너결합_KMS")
        self.assertEqual(p["preview"]["name"], "GD_15s_최종.mp4")      # sparse placeholder
        with self.assertRaises(ValueError):
            make(os.path.join(self.tmp, "bad"), "small", profile="nope")


if __name__ == "__main__":
    unittest.main()

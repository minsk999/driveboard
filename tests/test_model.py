"""DriveIndex queries and mutations; invariants are checked after every change."""

import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
if os.name == "nt":      # 임시 폴더를 긴 이름으로(…\RUNNER~1\… → …\runneradmin\…): 제품이 경로를 긴 이름으로
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())     # 돌려주므로 시험의 기대값도 같은 모양이어야 한다

from core import model  # noqa: E402
from core import categories as C  # noqa: E402
from core.scanner import scan_subtree  # noqa: E402
from make_fake_drives import write_tree  # noqa: E402

MB = 1 << 20
KB = 1 << 10
DAY = 86400.0
NOW = time.time()


def T(days):
    return NOW - days * DAY


def std_tree(root):
    """Big threshold 50MB. Expectations in the tests are computed by hand from this table."""
    write_tree(root, {
        "proj/footage/C0001.MP4": (900 * MB, T(400)),      # big, source
        "proj/footage/C0002.MP4": (10 * MB, T(400)),       # small, source
        "proj/footage/clip.xml": (1 * KB, T(400)),         # small, other
        "proj/render/final_v03.mp4": (200 * MB, T(10)),    # big, render
        "proj/aep/a.aep": (1 * MB, T(5)),                  # small, project
        "Media Cache Files/x.cfa": (70 * MB, T(30)),       # big, cache
        "Media Cache Files/y.pek": (100 * KB, T(30)),      # small, cache
        "top.zip": (60 * MB, T(200)),                      # big, archive
        "readme.txt": (1 * KB, T(1)),                      # small, other
        **{"proj/render/exr/shot_%04d.exr" % i: (5 * MB, T(20) + i) for i in range(1, 21)},
        **{"proj/render/small_seq/s_%03d.png" % i: (1 * MB, T(15)) for i in range(1, 7)},
    }, dirs=["proj/empty"])
    return root


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dbtest-model-")
        self.root = os.path.join(self.tmp, "D")
        std_tree(self.root)
        self.drive = {"letter": "D", "root": self.root, "label": "SSD1", "serial": "S-D"}
        self.idx = scan_subtree(self.root, self.drive, {})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def P(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def inv(self, idx=None):
        errs = model.check_invariants(idx or self.idx)
        self.assertEqual(errs, [])
        i = idx or self.idx
        self.assertEqual(sum(model.cat_totals(i, 0)), i.d_size[0])


class TestQueries(Base):
    def test_totals(self):
        idx = self.idx
        self.inv()
        total = (900 + 10 + 200 + 1 + 70 + 60 + 100 + 6) * MB + (1 + 100 + 1) * KB
        self.assertEqual(idx.d_size[0], total)
        self.assertEqual(idx.d_files[0], 9 + 20 + 6)
        cats = model.cat_totals(idx)
        self.assertEqual(cats[C.SOURCE], 910 * MB)
        self.assertEqual(cats[C.RENDER], 200 * MB + 100 * MB + 6 * MB)
        self.assertEqual(cats[C.CACHE], 70 * MB + 100 * KB)
        self.assertEqual(cats[C.ARCHIVE], 60 * MB)
        self.assertEqual(cats[C.PROJECT], 1 * MB)
        self.assertEqual(cats[C.OTHER], 2 * KB)
        counts = model.cat_counts(idx)
        self.assertEqual(counts[C.RENDER], 1 + 20 + 6)
        self.assertEqual(sorted(idx.f_name), sorted(
            ["C0001.MP4", "final_v03.mp4", "shot_[####].exr", "x.cfa", "top.zip"]))
        s = model.summary(idx)
        self.assertEqual(s["scanned_bytes"], total)
        self.assertEqual(s["files"], 35)
        self.assertEqual(s["dirs"], 8)   # proj, footage, render, exr, small_seq, aep, empty, MCF
        self.assertEqual(s["items"], 5)

    def test_paths_and_find(self):
        idx = self.idx
        self.assertEqual(model.find_dir(idx, self.root), 0)
        self.assertEqual(model.path_of_dir(idx, 0), self.root)
        did = model.find_dir(idx, self.P("proj/render"))
        self.assertIsNotNone(did)
        self.assertEqual(model.path_of_dir(idx, did), self.P("proj/render"))
        self.assertEqual(model.find_dir(idx, self.P("proj/render") + os.sep), did)
        self.assertEqual(model.find_item(idx, self.P("proj/render")), ("dir", did))
        k, fid = model.find_item(idx, self.P("proj/footage/C0001.MP4"))
        self.assertEqual(k, "file")
        self.assertEqual(model.path_of_file(idx, fid), self.P("proj/footage/C0001.MP4"))
        k, sid = model.find_item(idx, self.P("proj/render/exr/shot_[####].exr"))
        self.assertEqual(k, "file")
        self.assertEqual(idx.f_count[sid], 20)
        self.assertEqual(idx.f_seq[sid], (1, 20))
        self.assertIsNone(model.find_item(idx, self.P("proj/footage/C0002.MP4")))   # small
        self.assertIsNone(model.find_item(idx, self.P("nope/x")))
        self.assertIsNone(model.find_item(idx, os.path.join(self.tmp, "E", "x")))
        self.assertIsNone(model.find_dir(idx, "relative/path"))

    def test_frames_of(self):
        idx = self.idx
        _k, sid = model.find_item(idx, self.P("proj/render/exr/shot_[####].exr"))
        frames = model.frames_of(idx, sid)
        self.assertEqual(frames, [self.P("proj/render/exr/shot_%04d.exr" % i)
                                  for i in range(1, 21)])
        # files outside first..last or with another digit count are not frames
        write_tree(self.root, {"proj/render/exr/shot_0021.exr": 1,
                               "proj/render/exr/shot_00005.exr": 1,
                               "proj/render/exr/shot_0003.png": 1})
        self.assertEqual(len(model.frames_of(idx, sid)), 20)
        _k, fid = model.find_item(idx, self.P("top.zip"))
        self.assertEqual(model.frames_of(idx, fid), [self.P("top.zip")])

    def test_list_children(self):
        idx = self.idx
        rows = model.list_children(idx, 0)
        kinds = [(r["kind"], r["name"]) for r in rows]
        self.assertEqual(kinds, [("dir", "proj"), ("dir", "Media Cache Files"),
                                 ("file", "top.zip"), ("loose", model.LOOSE_NAME)])
        loose = rows[-1]
        self.assertEqual(loose["size"], 1 * KB)
        self.assertEqual(loose["files"], 1)
        proj = rows[0]
        self.assertEqual(proj["size"], (900 + 10 + 200 + 1 + 100 + 6) * MB + 1 * KB)
        self.assertEqual(proj["cat"], C.SOURCE)
        self.assertEqual(len(proj["cat_bytes"]), C.NCAT)
        # category filter: metric is that category's bytes; zero rows dropped
        rows = model.list_children(idx, 0, cat=C.RENDER)
        self.assertEqual([r["name"] for r in rows], ["proj"])
        self.assertEqual(rows[0]["metric"], 306 * MB)
        rows = model.list_children(idx, 0, cat=C.OTHER)
        self.assertEqual([(r["kind"], r["metric"]) for r in rows], [("dir", 1 * KB), ("loose", 1 * KB)])
        # age filter (newest <= now - age)
        rows = model.list_children(idx, 0, age_days=100, now=NOW)
        self.assertEqual([r["name"] for r in rows], ["top.zip"])
        rd = model.find_dir(idx, self.P("proj/render"))
        rows = model.list_children(idx, rd)
        self.assertEqual([(r["kind"], r["name"]) for r in rows],
                         [("file", "final_v03.mp4"), ("dir", "exr"), ("dir", "small_seq")])
        sq = rows[1]
        self.assertEqual(sq["files"], 20)
        ed = model.find_dir(idx, self.P("proj/empty"))
        self.assertEqual(model.list_children(idx, ed), [])
        self.assertEqual(idx.d_newest[ed], idx.d_mtime[ed])   # empty folder: its own mtime

    def test_newest(self):
        idx = self.idx
        fd = model.find_dir(idx, self.P("proj/footage"))
        self.assertAlmostEqual(idx.d_newest[fd], T(400), delta=1)
        pd = model.find_dir(idx, self.P("proj"))
        self.assertAlmostEqual(idx.d_newest[pd], T(5), delta=1)       # a.aep

    def test_list_files(self):
        idx = self.idx
        items, n, b = model.list_files(idx)
        self.assertEqual(n, 5)
        self.assertEqual(b, (900 + 200 + 100 + 70 + 60) * MB)
        self.assertEqual([i["name"] for i in items],
                         ["C0001.MP4", "final_v03.mp4", "shot_[####].exr", "x.cfa", "top.zip"])
        seq = items[2]
        self.assertTrue(seq["seq"])
        self.assertEqual((seq["count"], seq["first"], seq["last"]), (20, 1, 20))
        items, n, b = model.list_files(idx, order="asc")
        self.assertEqual(items[0]["name"], "top.zip")
        items, n, b = model.list_files(idx, sort="name", order="asc")
        self.assertEqual([i["name"] for i in items],
                         ["C0001.MP4", "final_v03.mp4", "shot_[####].exr", "top.zip", "x.cfa"])
        items, n, b = model.list_files(idx, sort="mtime", order="desc")
        self.assertEqual(items[0]["name"], "final_v03.mp4")
        self.assertEqual(items[-1]["name"], "C0001.MP4")
        items, n, b = model.list_files(idx, offset=1, limit=2)
        self.assertEqual([i["name"] for i in items], ["final_v03.mp4", "shot_[####].exr"])
        self.assertEqual(n, 5)
        items, n, b = model.list_files(idx, offset=4, limit=10, order="asc")
        self.assertEqual([i["name"] for i in items], ["C0001.MP4"])
        items, n, b = model.list_files(idx, offset=9)
        self.assertEqual((items, n), ([], 5))
        items, n, b = model.list_files(idx, cat=C.RENDER)
        self.assertEqual((n, b), (2, 300 * MB))
        items, n, b = model.list_files(idx, age_days=100, now=NOW)
        self.assertEqual(sorted(i["name"] for i in items), ["C0001.MP4", "top.zip"])
        items, n, b = model.list_files(idx, q="SHOT")
        self.assertEqual([i["name"] for i in items], ["shot_[####].exr"])
        items, n, b = model.list_files(idx, q="footage")       # matches the folder path
        self.assertEqual([i["name"] for i in items], ["C0001.MP4"])
        pd = model.find_dir(idx, self.P("proj"))
        items, n, b = model.list_files(idx, did=pd)
        self.assertEqual((n, b), (3, 1200 * MB))
        items, n, b = model.list_files(idx, did=pd, cat=C.SOURCE, limit=0)
        self.assertEqual((items, n, b), ([], 1, 900 * MB))

    def test_small_remainder(self):
        idx = self.idx
        self.assertEqual(model.small_remainder(idx, 0, C.RENDER), (6 * MB, 6))
        self.assertEqual(model.small_remainder(idx, 0, C.SOURCE), (10 * MB, 1))
        b, f = model.small_remainder(idx, 0)
        self.assertEqual(f, 35 - (1 + 1 + 20 + 1 + 1))
        self.assertEqual(b, idx.d_size[0] - (900 + 200 + 100 + 70 + 60) * MB)


class TestMutations(Base):
    def test_detach_file(self):
        idx = self.idx
        before = idx.d_size[0]
        n = model.detach(idx, self.P("proj/footage/C0001.MP4"))
        self.assertEqual(n, 900 * MB)
        self.inv()
        self.assertEqual(idx.d_size[0], before - 900 * MB)
        self.assertEqual(model.cat_totals(idx)[C.SOURCE], 10 * MB)
        self.assertIsNone(model.find_item(idx, self.P("proj/footage/C0001.MP4")))
        self.assertEqual(model.list_files(idx)[1], 4)
        fd = model.find_dir(idx, self.P("proj/footage"))
        self.assertAlmostEqual(idx.d_newest[fd], T(400), delta=1)
        self.assertEqual(model.detach(idx, self.P("proj/footage/C0001.MP4")), 0)

    def test_detach_sequence(self):
        idx = self.idx
        n = model.detach(idx, self.P("proj/render/exr/shot_[####].exr"))
        self.assertEqual(n, 100 * MB)
        self.inv()
        self.assertEqual(model.cat_counts(idx)[C.RENDER], 1 + 6)
        ed = model.find_dir(idx, self.P("proj/render/exr"))
        self.assertEqual((idx.d_size[ed], idx.d_files[ed]), (0, 0))
        self.assertEqual(idx.d_newest[ed], idx.d_mtime[ed])

    def test_detach_dir(self):
        idx = self.idx
        rd = model.find_dir(idx, self.P("proj/render"))
        before = idx.d_size[0]
        n = model.detach(idx, self.P("proj/render"))
        self.assertEqual(n, 306 * MB)
        self.inv()
        self.assertEqual(idx.d_size[0], before - 306 * MB)
        self.assertEqual(model.cat_totals(idx)[C.RENDER], 0)
        self.assertIsNone(model.find_dir(idx, self.P("proj/render")))
        self.assertIsNone(model.find_item(idx, self.P("proj/render/exr/shot_[####].exr")))
        self.assertFalse(idx.d_alive[rd])
        self.assertEqual(model.list_files(idx, cat=C.RENDER)[1], 0)
        pd = model.find_dir(idx, self.P("proj"))
        self.assertAlmostEqual(idx.d_newest[pd], T(5), delta=1)
        # detaching everything leaves a consistent empty root
        for rel in ("proj", "Media Cache Files", "top.zip"):
            model.detach(idx, self.P(rel))
            self.inv()
        self.assertEqual(idx.d_size[0], 1 * KB)
        with self.assertRaises(ValueError):
            model.detach(idx, self.root)

    def test_sequence_detach_series(self):
        idx = self.idx
        for rel in ("proj/render/exr/shot_[####].exr", "proj/render/final_v03.mp4",
                    "proj/render/small_seq", "proj/render/exr", "proj/render"):
            model.detach(idx, self.P(rel))
            self.inv()
        self.assertEqual(model.cat_totals(idx)[C.RENDER], 0)

    def test_apply_rule_dir_and_file(self):
        idx = self.idx
        pd = model.find_dir(idx, self.P("proj"))
        psize = idx.d_size[pd]
        root_before = model.cat_totals(idx)
        model.apply_rule(idx, self.P("proj"), C.ARCHIVE)
        self.inv()
        cats = model.cat_totals(idx)
        self.assertEqual(cats[C.ARCHIVE], 60 * MB + psize)
        self.assertEqual(cats[C.SOURCE], 0)
        self.assertEqual(cats[C.RENDER], 0)
        self.assertEqual(cats[C.CACHE], root_before[C.CACHE])
        self.assertEqual(model.list_files(idx, cat=C.ARCHIVE)[1], 4)
        self.assertEqual(model.cat_counts(idx, pd)[C.ARCHIVE], idx.d_files[pd])
        model.apply_rule(idx, self.P("top.zip"), C.OTHER)
        self.inv()
        self.assertEqual(model.cat_totals(idx)[C.ARCHIVE], psize)
        model.apply_rule(idx, self.P("does/not/exist"), C.OTHER)    # no-op
        self.inv()

    def _drive(self, letter):
        root = os.path.join(self.tmp, letter)
        os.makedirs(root, exist_ok=True)
        return {"letter": letter, "root": root, "label": letter, "serial": "S-" + letter}

    def test_graft_new_dir_with_intermediates(self):
        h = self._drive("H")
        write_tree(h["root"], {"keep/old.mov": (80 * MB, T(50))})
        hidx = scan_subtree(h["root"], h, {})
        dest = os.path.join(h["root"], "Projects", "A", "render")
        write_tree(dest, {"final_v03.mp4": (200 * MB, T(10)), "notes.txt": 5,
                          **{"exr/f_%04d.exr" % i: 11 * MB for i in range(1, 6)}})
        sub = scan_subtree(dest, h, {})
        self.assertFalse(sub.partial)
        model.graft(hidx, sub, dest)
        self.inv(hidx)
        self.assertEqual(hidx.d_size[0], 80 * MB + 200 * MB + 5 + 55 * MB)
        self.assertIsNotNone(model.find_dir(hidx, os.path.join(h["root"], "Projects")))
        k, fid = model.find_item(hidx, os.path.join(dest, "exr", "f_[####].exr"))
        self.assertEqual(hidx.f_count[fid], 5)
        self.assertEqual(model.cat_totals(hidx)[C.RENDER], 255 * MB)
        self.assertEqual(len(model.frames_of(hidx, fid)), 5)
        items, n, b = model.list_files(hidx, cat=C.RENDER)
        self.assertEqual(n, 2)

    def test_graft_replaces_existing_dir(self):
        idx = self.idx
        before = idx.d_size[0]
        write_tree(self.root, {"proj/render/extra.mov": (75 * MB, T(1))})
        sub = scan_subtree(self.P("proj/render"), self.drive, {})
        model.graft(idx, sub, self.P("proj/render"))
        self.inv()
        self.assertEqual(idx.d_size[0], before + 75 * MB)     # no double counting
        self.assertEqual(model.list_files(idx, cat=C.RENDER)[1], 3)
        self.assertEqual(model.list_files(idx, q="extra")[1], 1)

    def test_graft_partial_file_and_sequence(self):
        h = self._drive("H")
        write_tree(h["root"], {"a/b.wav": (2 * MB, T(3))})
        hidx = scan_subtree(h["root"], h, {})
        write_tree(h["root"], {"a/new/big.mov": (120 * MB, T(2)), "a/small.txt": 300,
                               **{"a/seq/p_%03d.png" % i: 15 * MB for i in range(10, 15)}})
        for rel in ("a/new/big.mov", "a/small.txt"):
            p = os.path.join(h["root"], *rel.split("/"))
            sub = scan_subtree(p, h, {})
            self.assertTrue(sub.partial)
            model.graft(hidx, sub, p)
            self.inv(hidx)
        seqp = os.path.join(h["root"], "a", "seq", "p_[###].png")
        sub = scan_subtree(seqp, h, {})
        self.assertEqual(sub.nitems, 1)
        model.graft(hidx, sub, seqp)
        self.inv(hidx)
        self.assertEqual(hidx.d_size[0], 2 * MB + 120 * MB + 300 + 75 * MB)
        self.assertEqual(hidx.d_files[0], 1 + 1 + 1 + 5)
        k, fid = model.find_item(hidx, seqp)
        self.assertEqual((hidx.f_count[fid], hidx.f_seq[fid]), (5, (10, 14)))
        # grafting the same big file again replaces the row instead of doubling it
        p = os.path.join(h["root"], "a", "new", "big.mov")
        model.graft(hidx, scan_subtree(p, h, {}), p)
        self.inv(hidx)
        self.assertEqual(hidx.d_size[0], 2 * MB + 120 * MB + 300 + 75 * MB)

    def test_graft_root_replaces_everything(self):
        idx = self.idx
        write_tree(self.root, {"more/z.mov": (51 * MB, T(1))})
        before = idx.d_size[0]
        sub = scan_subtree(self.root, self.drive, {})
        model.graft(idx, sub, self.root)
        self.inv()
        self.assertEqual(idx.d_size[0], before + 51 * MB)
        self.assertEqual(idx.letter, "D")
        self.assertIsNotNone(model.find_item(idx, self.P("more/z.mov")))

    def test_graft_outside_root_rejected(self):
        other = self._drive("E")
        write_tree(other["root"], {"x.mov": 1})
        sub = scan_subtree(other["root"], other, {})
        with self.assertRaises(ValueError):
            model.graft(self.idx, sub, other["root"])

    def test_mixed_sequence_of_changes(self):
        idx = self.idx
        model.detach(idx, self.P("proj/footage/C0001.MP4"))
        model.apply_rule(idx, self.P("proj/render"), C.OTHER)
        write_tree(self.root, {"proj/render/z.mov": (64 * MB, T(1))})
        p = self.P("proj/render/z.mov")
        model.graft(idx, scan_subtree(p, self.drive, {}), p)
        model.detach(idx, self.P("proj/render/exr"))
        self.inv()
        fresh = scan_subtree(self.root, self.drive, {})
        # z.mov was classified by the scanner (render folder -> render)
        self.assertEqual(idx.d_size[0], fresh.d_size[0] - 900 * MB - 100 * MB)


class TestPerformance(unittest.TestCase):
    """1M big rows: warm list_files queries must stay under 0.5s."""

    @classmethod
    def setUpClass(cls):
        t = time.time()
        idx = model.new_index("Z", os.path.join(os.sep, "perf", "Z"))
        top = [idx._new_dir("p%03d" % i, 0) for i in range(200)]
        leaves = []
        for t_ in top:
            for j in range(50):
                leaves.append(idx._new_dir("d%02d" % j, t_))
        n = 1000000
        nl = len(leaves)
        sizes = idx.f_size
        for i in range(n):
            idx._new_item(leaves[i % nl], "file_%07d.mov" % i, (i * 7919) % 1000003 + 50 * 2 ** 20,
                          NOW - (i % 1000) * DAY, i % 11)
        cls.idx = idx
        cls.top = top
        cls.build_s = time.time() - t
        del sizes

    def timed(self, **kw):
        t = time.time()
        res = model.list_files(self.idx, **kw)
        return time.time() - t, res

    def test_queries(self):
        idx = self.idx
        cold, (items, n, b) = self.timed()
        self.assertEqual(n, 1000000)
        self.assertEqual(len(items), 200)
        self.assertGreaterEqual(items[0]["size"], items[-1]["size"])
        t = time.time()
        model.warm(idx, ("mtime", "name"))
        report = {"build": self.build_s, "cold_size_sort": cold,
                  "warm_mtime_name": time.time() - t}
        variants = (("all", {}), ("page2", {"offset": 200}),
                    ("cat", {"cat": 2}), ("subtree", {"did": self.top[3], "cat": 1}),
                    ("age", {"age_days": 365, "now": NOW}),
                    ("query", {"q": "file_00012"}), ("mtime_cat", {"sort": "mtime", "cat": 5}),
                    ("name_asc", {"sort": "name", "order": "asc"}),
                    ("all_filters", {"did": self.top[7], "cat": 3, "age_days": 90, "q": "file_",
                                     "now": NOW}))
        for name, kw in variants:
            idx._qcache.clear()              # first time this filter is used
            dt1, (items, n, b) = self.timed(**kw)
            dt2, _r = self.timed(**kw)       # next page / repeat: cached
            report[name] = dt1
            report[name + "_again"] = dt2
            self.assertLess(dt1, 0.5, name)
            self.assertLess(dt2, 0.5, name)
        dt, (items, n, b) = self.timed(q="zzzz-none")
        self.assertEqual(n, 0)
        report["query_miss"] = dt
        print("\n[perf] list_files 1M rows: " + ", ".join(
            "%s=%.3fs" % (k, v) for k, v in report.items()))

if __name__ == "__main__":
    unittest.main()

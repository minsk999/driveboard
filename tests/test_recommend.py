"""Duplicate detection, quick hash and home-screen recommendations."""

import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from core import dupes, model, recommend  # noqa: E402
from core import categories as C  # noqa: E402
from core.scanner import scan_subtree  # noqa: E402
from make_fake_drives import make, write_tree  # noqa: E402

MB = 1 << 20
GB = 1 << 30
DAY = 86400.0
NOW = time.time()
REC_KEYS = {"id", "kind", "title", "desc", "bytes", "count", "tone", "action"}


def T(days):
    return NOW - days * DAY


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dbtest-rec-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drive(self, letter, files, dirs=()):
        root = os.path.join(self.tmp, letter)
        write_tree(root, files, dirs)
        d = {"letter": letter, "root": root, "label": letter}
        idx = scan_subtree(root, d, {})
        self.assertEqual(model.check_invariants(idx), [])
        return idx, root


class TestDupes(Tmp):
    def build(self):
        d, droot = self.drive("D", {
            "proj/footage/C0001.MP4": (900 * MB, T(30)),
            "proj/footage/C0002.MP4": 100 * MB,
            "proj/copy/C0001.MP4": (900 * MB, T(10)),
            **{"seq/s_%04d.exr" % i: 20 * MB for i in range(1, 6)},
            "Program Files/app/big.dll": 60 * MB,
            "small.mov": 10 * MB,
        })
        h, hroot = self.drive("H", {
            "백업/proj/footage/C0001.MP4": (900 * MB, T(5)),
            "백업/proj/footage/c0002.mp4": 100 * MB,
            "other/C0001.MP4": 901 * MB,
            **{"seq/s_%04d.exr" % i: 20 * MB for i in range(1, 6)},
            "Program Files/app/big.dll": 60 * MB,
            "small.mov": 10 * MB,
        })
        return {"D": d, "H": h}, droot, hroot

    def test_find_dupes(self):
        idxs, droot, hroot = self.build()
        groups = dupes.find_dupes(idxs, 50 * MB)
        self.assertEqual([(g["name"].lower(), g["size"], len(g["items"])) for g in groups],
                         [("c0001.mp4", 900 * MB, 3), ("c0002.mp4", 100 * MB, 2)])
        g = groups[0]
        self.assertEqual(g["wasted"], 1800 * MB)
        self.assertIsNone(g["verified"])
        self.assertEqual(set(g), {"id", "size", "name", "wasted", "verified", "items"})
        paths = [it["path"] for it in g["items"]]
        self.assertEqual(paths, [os.path.join(droot, "proj", "footage", "C0001.MP4"),
                                 os.path.join(droot, "proj", "copy", "C0001.MP4"),
                                 os.path.join(hroot, "백업", "proj", "footage", "C0001.MP4")])
        # (oldest first)
        for it in g["items"]:
            self.assertEqual(set(it), {"drive", "path", "mtime", "key"})
            L, kind, fid = it["key"].split(":")
            self.assertEqual(kind, "file")
            self.assertEqual(model.path_of_file(idxs[L], int(fid)), it["path"])
        again = dupes.find_dupes(idxs, 50 * MB)
        self.assertEqual([x["id"] for x in again], [x["id"] for x in groups])
        self.assertEqual(dupes.wasted_total(groups), 1800 * MB + 100 * MB)
        # threshold
        self.assertEqual([x["size"] for x in dupes.find_dupes(idxs, 500 * MB)], [900 * MB])
        # a detached copy is no longer a duplicate
        model.detach(idxs["D"], os.path.join(droot, "proj", "copy"))
        groups = dupes.find_dupes(idxs, 50 * MB)
        self.assertEqual(groups[0]["wasted"], 900 * MB)

    def test_same_drive_only_and_empty(self):
        idx, _r = self.drive("E", {"a/x.mov": 60 * MB, "b/x.mov": 60 * MB})
        self.assertEqual(len(dupes.find_dupes({"E": idx}, 50 * MB)), 1)
        self.assertEqual(dupes.find_dupes({}, 1), [])
        self.assertEqual(dupes.find_dupes({"E": None}, 1), [])

    def test_quick_hash_and_verify(self):
        rnd = os.urandom(5 * MB)

        def w(name, data):
            p = os.path.join(self.tmp, name)
            with open(p, "wb") as f:
                f.write(data)
            return p
        a = w("a.bin", rnd)
        b = w("b.bin", rnd)
        mid = bytearray(rnd)
        mid[5 * MB // 2] ^= 0xFF
        c = w("c.bin", bytes(mid))                 # middle MB differs
        gap = bytearray(rnd)
        gap[int(1.5 * MB)] ^= 0xFF
        d = w("d.bin", bytes(gap))                 # outside the sampled ranges
        tail = bytearray(rnd)
        tail[-1] ^= 0xFF
        e = w("e.bin", bytes(tail))
        small1 = w("s1.bin", rnd[: 2 * MB])
        small2 = bytearray(rnd[: 2 * MB])
        small2[int(1.5 * MB)] ^= 1
        small2 = w("s2.bin", bytes(small2))       # small files are hashed whole
        ha = dupes.quick_hash(a)
        self.assertTrue(ha.startswith("%d-" % (5 * MB)))
        self.assertEqual(ha, dupes.quick_hash(b))
        self.assertNotEqual(ha, dupes.quick_hash(c))
        self.assertEqual(ha, dupes.quick_hash(d))   # documented quick-hash limitation
        self.assertNotEqual(ha, dupes.quick_hash(e))
        self.assertNotEqual(dupes.quick_hash(small1), dupes.quick_hash(small2))
        with self.assertRaises(OSError):
            dupes.quick_hash(os.path.join(self.tmp, "missing.bin"))

        def grp(*paths):
            return {"items": [{"path": p} for p in paths], "verified": None}
        g = grp(a, b)
        self.assertTrue(dupes.verify_group(g))
        self.assertTrue(g["verified"])
        self.assertFalse(dupes.verify_group(grp(a, c)))
        g = grp(a, os.path.join(self.tmp, "missing.bin"))
        self.assertIsNone(dupes.verify_group(g))
        self.assertIn("error", g["items"][1])
        g = grp(a, b, os.path.join(self.tmp, "missing.bin"))
        self.assertIsNone(dupes.verify_group(g))   # unreadable copy: not confirmed
        self.assertIsNone(dupes.verify_group(grp(a, b), cancel=lambda: True))


class TestRecommend(Tmp):
    def setUp(self):
        super().setUp()
        self.d, self.droot = self.drive("D", {
            "Media Cache Files/x.cfa": (2 * GB, T(3)),
            "proj/render/old_final.mp4": (3 * GB, T(400)),
            "proj/render/new_final.mp4": (2 * GB, T(1)),
            "proj/footage/A001C001_x.mxf": (12 * GB, T(10)),
            "2023_old_project/footage/C0001.MP4": (5 * GB, T(500)),
            "2023_old_project/aep/a.aep": (10 * MB, T(520)),
            "Windows/old.dll": (3 * GB, T(900)),
            "Users/kim/AppData/Local/old/x.dat": (3 * GB, T(900)),
            "Users/kim/Desktop/2022_옛작업/a.mov": (2 * GB, T(600)),
            "pagefile.sys": (16 * GB, T(1)),
        })
        self.h, self.hroot = self.drive("H", {
            "백업/2023_old_project/footage/C0001.MP4": (5 * GB, T(500)),
            "아카이브/2021/a.mov": (4 * GB, T(1000)),
        })
        self.idxs = {"D": self.d, "H": self.h}
        TB = 4000787030016
        self.state = [
            {"letter": "D", "label": "SSD1", "kind": "ssd", "total": TB,
             "free": int(TB * 0.075), "bin_bytes": 200 * MB, "bin_count": 3},
            {"letter": "H", "label": "HDD1", "kind": "hdd", "total": 2 * TB,
             "free": int(TB * 0.8), "bin_bytes": 50 * MB, "bin_count": 1},
        ]
        self.groups = dupes.find_dupes(self.idxs, 50 * MB)

    def test_build(self):
        recs = recommend.build(self.idxs, self.state, {"old_days": 180}, self.groups, NOW)
        for r in recs:
            self.assertEqual(set(r), REC_KEYS)
            self.assertIn(r["tone"], ("safe", "review", "info"))
            self.assertIsInstance(r["bytes"], int)
            self.assertTrue(r["title"] and r["desc"])
        by = {r["id"]: r for r in recs}
        self.assertEqual(recs[0]["id"], "cache")             # safe first
        self.assertEqual([r["id"] for r in recs[1:]],
                         ["balance-D", "big", "old_project", "dupes", "old_render", "bin"])
        c = by["cache"]
        self.assertEqual((c["bytes"], c["count"], c["tone"]), (2 * GB, 1, "safe"))
        self.assertEqual(c["desc"], "AE·프리미어 캐시는 지워도 다시 만들어져요.")
        self.assertEqual(c["action"], {"type": "view", "drive": "D", "view": "files",
                                       "cat": C.CACHE, "age": 0})
        b = by["bin"]
        self.assertEqual((b["bytes"], b["count"]), (200 * MB, 3))
        self.assertEqual(b["action"], {"type": "empty_bin", "drives": ["D"]})
        self.assertIn("휴지통을 비워야", b["desc"])
        o = by["old_render"]
        self.assertEqual((o["bytes"], o["count"]), (3 * GB, 1))
        self.assertEqual(o["action"]["age"], 180)
        self.assertEqual(o["action"]["cat"], C.RENDER)
        self.assertIn("6개월", o["desc"])
        dp = by["dupes"]
        self.assertEqual((dp["bytes"], dp["count"], dp["action"]), (5 * GB, 1, {"type": "dupes"}))
        bg = by["big"]
        self.assertEqual((bg["bytes"], bg["count"]), (12 * GB, 1))   # pagefile.sys is app
        op = by["old_project"]
        # D only (H is the roomy hdd): 2023_old_project and Desktop\2022_옛작업
        self.assertEqual((op["bytes"], op["count"]), (5 * GB + 10 * MB + 2 * GB, 2))
        self.assertIn("2023_old_project", op["desc"])
        self.assertIn("HDD1 (H:)", op["desc"])
        self.assertEqual(op["action"], {"type": "view", "drive": "D", "view": "folders",
                                        "cat": None, "age": 365})
        bal = by["balance-D"]
        self.assertEqual(bal["tone"], "info")
        self.assertIn("7%", bal["title"])
        self.assertIn("HDD1 (H:)", bal["desc"])
        self.assertEqual(bal["bytes"], int(4000787030016 * 0.1) - int(4000787030016 * 0.075))

    def test_thresholds_and_empty(self):
        self.assertEqual(recommend.build({}, [], {}, [], NOW), [])
        small, _r = self.drive("E", {"Media Cache Files/x.cfa": 10 * MB})
        recs = recommend.build({"E": small}, {"E": {"total": 100, "free": 50}}, {}, None, NOW)
        self.assertEqual(recs, [])
        recs = recommend.build({"E": small}, {"E": {"total": 100, "free": 5}}, {}, None, NOW)
        self.assertEqual([r["id"] for r in recs], ["balance-E"])
        self.assertIn("다른 드라이브에도", recs[0]["desc"])

    def test_old_folders_on_external_or_nas_are_not_suggested(self):
        # H 를 외장(또는 NAS)으로 바꾸면 거기 있는 오래된 폴더는 「옮겨 보세요」 대상이 아니다
        for extra in ({"external": True}, {"kind": "network", "letter": "N-aaaaaa"}):
            st = [dict(self.state[0]), dict(self.state[1], **extra)]
            L = st[1]["letter"]
            idxs = {"D": self.d, L: self.h}
            op = {r["id"]: r for r in recommend.build(idxs, st, {}, self.groups, NOW)}["old_project"]
            self.assertEqual(op["count"], 2, extra)            # D 의 둘만
            self.assertNotIn("백업", op["desc"])
            self.assertNotIn("N-aaaaaa", op["desc"])

    def test_old_folders_target_must_have_room(self):
        # H(HDD)가 꽉 찼으면 HDD 라도 권하지 않는다 — 같은 화면의 「공간 부족」 카드와 반대말이 된다
        TB = 4000787030016
        full = [dict(self.state[0], free=3 * TB // 4), dict(self.state[1], free=5 * GB)]
        op = {r["id"]: r for r in recommend.build(self.idxs, full, {}, self.groups, NOW)}["old_project"]
        self.assertNotIn("HDD1 (H:)", op["desc"])
        self.assertNotIn("남음", op["desc"])                  # D 는 원본이라 대상이 아니고, 다른 곳도 없다
        # 넉넉한 다른 드라이브가 있으면 그쪽(HDD 가 아니어도)
        st = full + [{"letter": "E", "label": "SSD2", "kind": "ssd", "total": 2 * TB, "free": TB}]
        op = {r["id"]: r for r in recommend.build(self.idxs, st, {}, self.groups, NOW)}["old_project"]
        self.assertIn("SSD2 (E:)", op["desc"])

    def test_old_days_setting(self):
        recs = recommend.build(self.idxs, self.state, {"old_days": 730}, self.groups, NOW)
        self.assertNotIn("old_render", [r["id"] for r in recs])

    def test_fmt(self):
        f = recommend.fmt_bytes
        self.assertEqual(f(0), "0KB")
        self.assertEqual(f(512), "0.5KB")
        self.assertEqual(f(4000787030016), "3.63TB")
        self.assertEqual(f(2000398934016), "1.81TB")
        self.assertEqual(f(1000202273280), "931GB")
        self.assertEqual(f(406 * GB + 1), "406GB")
        self.assertEqual(f(int(48.25 * GB)), "48.2GB")
        self.assertEqual(f(int(9.815 * MB)), "9.81MB")
        self.assertEqual(f(1000 * MB), "0.97GB")
        self.assertEqual(recommend.fmt_days(180), "6개월")
        self.assertEqual(recommend.fmt_days(365), "1년")
        self.assertEqual(recommend.fmt_days(100), "100일")


class TestOnFakeDrives(unittest.TestCase):
    def test_demo_like_small_drives(self):
        tmp = tempfile.mkdtemp(prefix="dbtest-recfake-")
        try:
            out = os.path.join(tmp, "fake")
            rep = make(out, "small")
            idxs = {}
            state = []
            from make_fake_drives import DRIVES
            for L, label, kind, total, free in DRIVES:
                root = os.path.join(out, L)
                idxs[L] = scan_subtree(root, {"letter": L, "root": root, "label": label}, {})
                state.append({"letter": L, "label": label, "kind": kind, "total": total,
                              "free": free, "bin_bytes": next(r["bin_bytes"] for r in rep
                                                              if r["letter"] == L),
                              "bin_count": 1})
            groups = dupes.find_dupes(idxs, 50 * MB)
            self.assertTrue(any({it["drive"] for it in g["items"]} == {"D", "H"} for g in groups))
            self.assertTrue(any({it["drive"] for it in g["items"]} == {"E", "I"} for g in groups))
            recs = recommend.build(idxs, state, {"old_days": 180}, groups, time.time())
            ids = [r["id"] for r in recs]
            for want in ("cache", "bin", "old_render", "dupes", "big", "old_project"):
                self.assertIn(want, ids)
            self.assertEqual(recs[0]["tone"], "safe")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class TestDriveNames(unittest.TestCase):
    def test_network_key_never_shown(self):
        nas = {"letter": "N-0d2634", "label": "ORICO · 영상백업", "kind": "network"}
        self.assertEqual(recommend._dname(nas), "ORICO · 영상백업")
        self.assertEqual(recommend._dname({"letter": "D", "label": "SSD1"}), "SSD1 (D:)")
        self.assertEqual(recommend._where("N-0d2634", nas), "ORICO · 영상백업")
        self.assertEqual(recommend._where("J", {"label": "Samsung T7"}), "J:")
        self.assertEqual(recommend._where("~5A5A0001", None), "연결 안 된 장치")
        self.assertEqual(recommend._dname({"letter": "N-aaaaaa"}), "네트워크 위치")

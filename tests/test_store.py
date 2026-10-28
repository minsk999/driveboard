"""Index persistence (binary, no pickle) and settings."""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
if os.name == "nt":      # 임시 폴더를 긴 이름으로(…\RUNNER~1\… → …\runneradmin\…): 제품이 경로를 긴 이름으로
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())     # 돌려주므로 시험의 기대값도 같은 모양이어야 한다

from core import model, settings, store  # noqa: E402
from core import categories as C  # noqa: E402
from core.scanner import scan_subtree  # noqa: E402
from make_fake_drives import write_tree  # noqa: E402

MB = 1 << 20


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dbtest-store-")
        self.home = os.path.join(self.tmp, "home")
        self._env = mock.patch.dict(os.environ, {"DRIVEBOARD_HOME": self.home})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


def same_index(tc, a, b):
    for k in ("letter", "root", "label", "serial", "fs", "errors", "error_samples",
              "scanned_at", "scan_seconds", "big_bytes", "d_name", "f_name", "f_seq"):
        tc.assertEqual(getattr(a, k), getattr(b, k), k)
    for k in ("d_parent", "d_size", "d_files", "d_newest", "d_cat", "d_catn", "d_own",
              "d_ofiles", "d_onew", "d_mtime", "f_dir", "f_size", "f_mtime", "f_cat", "f_count"):
        tc.assertEqual(list(getattr(a, k)), list(getattr(b, k)), k)
    tc.assertEqual(a.d_vid, b.d_vid, "d_vid")
    tc.assertEqual(a.d_aep, b.d_aep, "d_aep")


class TestStore(HomeCase):
    def make_index(self):
        root = os.path.join(self.tmp, "D")
        write_tree(root, {
            "2025_한결증권_브랜드필름/footage/C0001.MP4": 900 * MB,
            "2025_한결증권_브랜드필름/render/최종_v03.mp4": 70 * MB,
            "2025_한결증권_브랜드필름/render/exr/샷_0001.exr": 11 * MB,
            **{"2025_한결증권_브랜드필름/render/exr/샷_%04d.exr" % i: 11 * MB for i in range(2, 9)},
            "작은 파일.txt": 12,
            "Media Cache Files/a.cfa": 3 * MB,
        }, dirs=["빈 폴더"])
        idx = scan_subtree(root, {"letter": "D", "root": root, "label": "SSD1", "serial": "AB-12",
                                  "fs": "NTFS"}, {})
        idx.errors = 2
        idx.error_samples = [os.path.join(root, "잠긴 폴더"), os.path.join(root, "x")]
        return idx, root

    def test_data_dir(self):
        self.assertEqual(store.data_dir(), self.home)
        self.assertTrue(os.path.isdir(self.home))
        self.assertEqual(os.path.dirname(store.index_path("D")), self.home)
        self.assertTrue(store.index_path("1A2B-3C4D").endswith("index-1A2B-3C4D.dbi"))
        self.assertTrue(store.index_path("a/../b").endswith("index-a_.._b.dbi"))
        with self.assertRaises(ValueError):
            store.index_key("")

    def test_roundtrip_korean(self):
        idx, root = self.make_index()
        path = store.save_index(idx)
        self.assertEqual(os.path.basename(path), "index-AB-12.dbi")
        back = store.load_index("AB-12")
        self.assertIsNotNone(back)
        same_index(self, idx, back)
        self.assertEqual(model.check_invariants(back), [])
        p = os.path.join(root, "2025_한결증권_브랜드필름", "render", "exr", "샷_[####].exr")
        k, fid = model.find_item(back, p)
        self.assertEqual((back.f_count[fid], back.f_seq[fid]), (8, (1, 8)))
        self.assertEqual(len(model.frames_of(back, fid)), 8)
        self.assertEqual(model.list_files(back)[1:], model.list_files(idx)[1:])
        self.assertEqual(model.list_children(back, 0), model.list_children(idx, 0))
        # data is not pickle and not plain text
        with open(path, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(store.MAGIC))
        self.assertNotIn(b"pickle", raw)

    def test_roundtrip_after_mutations_compacts(self):
        idx, root = self.make_index()
        model.detach(idx, os.path.join(root, "2025_한결증권_브랜드필름", "render"))
        model.apply_rule(idx, os.path.join(root, "2025_한결증권_브랜드필름"), C.ARCHIVE)
        store.save_index(idx)
        back = store.load_index("AB-12")
        self.assertEqual(model.check_invariants(back), [])
        self.assertEqual(back.d_size[0], idx.d_size[0])
        self.assertEqual(model.cat_totals(back), model.cat_totals(idx))
        self.assertEqual(back.ndirs, sum(idx.d_alive))
        self.assertEqual(back.nitems, sum(idx.f_alive))
        self.assertIsNone(model.find_dir(back, os.path.join(root, "2025_한결증권_브랜드필름",
                                                            "render")))

    def test_d_vid_and_d_aep_roundtrip_with_compaction(self):
        idx, root = self.make_index()
        proj = os.path.join(root, "2025_한결증권_브랜드필름")
        write_tree(proj, {"aep/BF_v01.aep": 2 * MB, "late/z.mov": 3 * MB})
        model.graft(idx, scan_subtree(proj, {"letter": "D", "root": root}, {}), proj)
        rdid = model.find_dir(idx, os.path.join(proj, "render"))
        self.assertEqual(idx.d_vid[rdid][0], "최종_v03.mp4")
        self.assertIn(model.find_dir(idx, os.path.join(proj, "aep")), idx.d_aep)
        model.detach(idx, os.path.join(proj, "footage"))           # tombstones -> renumbering
        store.save_index(idx)
        back = store.load_index("AB-12")
        self.assertEqual(model.check_invariants(back), [])
        names = {model.path_of_dir(back, d): v for d, v in back.d_vid.items()}
        self.assertEqual(names, {model.path_of_dir(idx, d): v for d, v in idx.d_vid.items()})
        self.assertEqual({model.path_of_dir(back, d) for d in back.d_aep},
                         {os.path.join(proj, "aep")})
        self.assertNotIn(os.path.join(proj, "footage"), names)    # source dir gone with detach
        b = model.best_video(back, model.find_dir(back, proj))
        self.assertEqual(b[1][0], "최종_v03.mp4")                  # render beats newer 'late'

    def test_letter_key_and_list(self):
        idx, _root = self.make_index()
        idx.serial = ""
        store.save_index(idx)
        self.assertIsNotNone(store.load_index("D"))
        lst = store.list_indexes()
        self.assertEqual([x["key"] for x in lst], ["D"])
        self.assertEqual(lst[0]["label"], "SSD1")
        self.assertEqual(lst[0]["nitems"], idx.nitems)
        self.assertTrue(lst[0]["valid_version"])
        self.assertTrue(store.delete_index("D"))
        self.assertIsNone(store.load_index("D"))
        self.assertEqual(store.list_indexes(), [])

    def test_missing_and_corrupt(self):
        self.assertIsNone(store.load_index("nope"))
        idx, _root = self.make_index()
        path = store.save_index(idx)
        with open(path, "rb") as f:
            good = f.read()
        bad_cases = {
            "truncated": good[: len(good) // 2],
            "flipped": good[:40] + bytes([good[40] ^ 0xFF]) + good[41:],
            "garbage": b"\x00" * 100,
            "empty": b"",
            "magic": b"XXXXXXXX" + good[8:],
        }
        payload = zlib.decompress(good[len(store.MAGIC):])
        hl = int.from_bytes(payload[:4], "little")
        header = json.loads(payload[4:4 + hl])
        header["version"] = store.VERSION - 1
        hj = json.dumps(header).encode()
        old = store.MAGIC + zlib.compress(len(hj).to_bytes(4, "little") + hj + payload[4 + hl:])
        bad_cases["old_version"] = old
        header["version"] = store.VERSION
        header["blocks"][0][3] += 1          # block table no longer matches the data
        hj = json.dumps(header).encode()
        bad_cases["bad_table"] = store.MAGIC + zlib.compress(
            len(hj).to_bytes(4, "little") + hj + payload[4 + hl:])
        for name, data in bad_cases.items():
            with self.subTest(name=name):
                with open(path, "wb") as f:
                    f.write(data)
                self.assertIsNone(store.load_index("AB-12"))
                store.list_indexes()           # never raises

    def test_atomic_write(self):
        target = os.path.join(self.home, "x.bin")
        store.atomic_write(target, b"one")
        store.atomic_write(target, b"two")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"two")
        self.assertEqual(os.listdir(self.home), ["x.bin"])
        # a failing write leaves the old file and no temp file behind
        with mock.patch.object(store.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                store.atomic_write(target, b"three")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"two")
        self.assertEqual(os.listdir(self.home), ["x.bin"])

    def test_large_index_roundtrip(self):
        idx = model.new_index("H", os.path.join(os.sep, "big", "H"), "HDD1", "BIG-1", "NTFS")
        dirs = [idx._new_dir("프로젝트_%04d" % i, 0) for i in range(2000)]
        n = 200000
        for i in range(n):
            d = dirs[i % 2000]
            sz = 50 * MB + i
            fid = idx._new_item(d, "클립_%06d.mov" % i, sz, 1.7e9 + i, i % 11)
            b = d * C.NCAT + i % 11
            idx.d_cat[b] += sz
            idx.d_catn[b] += 1
            idx.d_size[d] += sz
            idx.d_files[d] += 1
        for d in dirs:
            idx.d_size[0] += idx.d_size[d]
            idx.d_files[0] += idx.d_files[d]
            for k in range(C.NCAT):
                idx.d_cat[k] += idx.d_cat[d * C.NCAT + k]
                idx.d_catn[k] += idx.d_catn[d * C.NCAT + k]
        t = time.time()
        path = store.save_index(idx)
        t_save = time.time() - t
        t = time.time()
        back = store.load_index("BIG-1")
        t_load = time.time() - t
        self.assertIsNotNone(back)
        same_index(self, idx, back)
        self.assertEqual(model.check_invariants(back), [])
        size = os.path.getsize(path)
        print("\n[perf] 200k-row index: save=%.2fs load=%.2fs file=%.1fMB" % (
            t_save, t_load, size / MB))
        self.assertLess(t_save, 10)
        self.assertLess(t_load, 10)


class TestSettings(HomeCase):
    def test_defaults(self):
        s = settings.load()
        self.assertEqual(s, {"big_file_mb": 50, "verify": "quick", "old_days": 180,
                             "excluded": [], "cache_paths": [], "rules": [],
                             "workers": {}, "client_aliases": {}, "project_roots": [],
                             "project_excluded": [], "preview_cache_gb": 2, "drive_scan": {},
                             "network_locations": [], "update_check": True})
        self.assertFalse(os.path.exists(settings.settings_path()))

    def test_save_partial_and_reload(self):
        full = settings.save({"big_file_mb": 100, "rules": [{"path": "D:\\Proj", "cat": "render"}]})
        self.assertEqual(full["big_file_mb"], 100)
        self.assertEqual(full["verify"], "quick")
        self.assertEqual(full["rules"], [{"path": os.path.normpath("D:\\Proj"), "cat": 1}])
        full2 = settings.save({"verify": "full"})
        self.assertEqual(full2["big_file_mb"], 100)
        self.assertEqual(full2["verify"], "full")
        self.assertEqual(settings.load(), full2)
        self.assertEqual(settings.big_file_bytes(full2), 100 * MB)

    def test_clamp_and_types(self):
        s = settings.save({"big_file_mb": 0, "old_days": 99999, "verify": "nope",
                           "excluded": "notalist", "cache_paths": ["  ", 5, "/a/b", "/a/b/"],
                           "unknown": 1})
        self.assertEqual(s["big_file_mb"], settings.BIG_FILE_MB_RANGE[0])
        self.assertEqual(s["old_days"], settings.OLD_DAYS_RANGE[1])
        self.assertEqual(s["verify"], "quick")
        self.assertEqual(s["excluded"], [])
        self.assertEqual(s["cache_paths"], [os.path.normpath("/a/b")])
        self.assertNotIn("unknown", s)
        s = settings.save({"big_file_mb": "75", "old_days": 30.7, "verify": " SIZE "})
        self.assertEqual((s["big_file_mb"], s["old_days"], s["verify"]), (75, 30, "size"))
        s = settings.save({"big_file_mb": True, "old_days": None})
        self.assertEqual((s["big_file_mb"], s["old_days"]), (75, 30))

    def test_rules_validation(self):
        s = settings.save({"rules": [
            {"path": "/x", "cat": 2}, {"path": "/y", "cat": 42}, {"path": "", "cat": 1},
            {"cat": 1}, "junk", {"path": "/x/", "cat": "proxy"}, {"path": "/z", "cat": "app"}]})
        self.assertEqual(s["rules"], [{"path": os.path.normpath("/x"), "cat": C.PROXY},       # Windows: '\\x'
                                      {"path": os.path.normpath("/z"), "cat": C.APP}])

    def test_corrupt_file_gives_defaults(self):
        os.makedirs(self.home, exist_ok=True)
        with open(settings.settings_path(), "wb") as f:
            f.write(b"{not json")
        self.assertEqual(settings.load()["big_file_mb"], 50)
        with open(settings.settings_path(), "wb") as f:
            f.write(b"[1,2,3]")
        self.assertEqual(settings.load()["verify"], "quick")
        s = settings.save({"old_days": 90})
        self.assertEqual(s["old_days"], 90)
        with open(settings.settings_path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["old_days"], 90)

    def test_korean_paths(self):
        s = settings.save({"excluded": ["/드라이브/개인 자료"]})
        self.assertEqual(settings.load()["excluded"], [os.path.normpath("/드라이브/개인 자료")])
        self.assertEqual(s["excluded"], settings.load()["excluded"])

    def test_v2_keys(self):
        s = settings.save({
            "workers": {" kms ": " 홍길동 ", "hyc": "", 5: "x", "PNJ": 3, "toolongINITIALS123": "y"},
            "client_aliases": {"한결": "한결증권", "Kiwoom": " 한결증권 ", "같음": " 같음", "": "x",
                               "소담코스 ": "소담코스"},
            "project_roots": ["/p/Projects", "/p/Projects/"], "project_excluded": [3, "/x"],
            "preview_cache_gb": "0.1", "drive_scan": {"d": True, "N-3FA9C1": False, "e": "yes"},
            "network_locations": ["\\\\ORICO\\영상백업\\", {"path": "//orico/영상백업"},
                                  {"path": "\\\\NAS\\v", "label": " 회사 NAS "},
                                  "C:\\local", "\\\\only-server", "\\\\s\\..\\x"],
            "update_check": False})
        self.assertEqual(s["workers"], {"KMS": "홍길동"})
        self.assertEqual(s["client_aliases"], {"한결": "한결증권", "Kiwoom": "한결증권"})
        self.assertEqual(s["project_roots"], [os.path.normpath("/p/Projects")])
        self.assertEqual(s["project_excluded"], [os.path.normpath("/x")])
        self.assertEqual(s["preview_cache_gb"], 0.5)
        self.assertEqual(s["drive_scan"], {"D": True, "N-3fa9c1": False})
        self.assertEqual(s["network_locations"], [
            {"path": "\\\\ORICO\\영상백업", "label": "영상백업"},
            {"path": "\\\\NAS\\v", "label": "회사 NAS"}])
        self.assertFalse(s["update_check"])
        self.assertEqual(settings.save({"preview_cache_gb": 999})["preview_cache_gb"], 50)
        self.assertEqual(settings.save({"preview_cache_gb": "3.25"})["preview_cache_gb"], 3.25)
        self.assertEqual(settings.save({"update_check": "no"})["update_check"], False)
        self.assertEqual(settings.preview_cache_bytes({"preview_cache_gb": 2}), 2 << 30)

    def test_network_key(self):
        k = settings.network_key("\\\\ORICO\\영상백업")
        self.assertRegex(k, r"^N-[0-9a-f]{6}$")
        self.assertEqual(settings.network_key("//orico/영상백업"), k)
        self.assertNotEqual(settings.network_key("//orico/영상백업/2024"), k)   # 등록 경로마다
        self.assertEqual(settings.network_key("\\\\ORICO\\영상백업\\"), k)
        import hashlib
        self.assertEqual(k, "N-" + hashlib.sha1("\\\\orico\\영상백업".encode()).hexdigest()[:6])
        self.assertIsNone(settings.network_key("D:\\x"))
        self.assertEqual(settings.client_key(" Ｋｉｗｏｏｍ  증권 "), "kiwoom증권")

    def test_team_export_import(self):
        mine = settings.validate({"workers": {"KMS": "홍길동", "HYC": "옛이름"},
                                  "client_aliases": {"한결": "한결증권"},
                                  "rules": [{"path": self.tmp, "cat": "render"}],
                                  "project_roots": ["/개인/경로"]})
        team = settings.export_team(mine)
        self.assertEqual(set(team), {"format", "version", "workers", "client_aliases", "rules"})
        self.assertEqual(team["workers"], {"KMS": "홍길동", "HYC": "옛이름"})
        json.dumps(team, ensure_ascii=False)
        incoming = {"format": "driveboard-team", "version": 1,
                    "workers": {"HYC": "김하늘", "JJH": "이서연", "KMS": "홍길동"},
                    "client_aliases": {"다우": "바른기술", "한결": "한결증권"},
                    "rules": [{"path": self.tmp, "cat": "source"},
                              {"path": "/없는/경로/렌더", "cat": "render"}, {"path": 3}]}
        new, diff = settings.import_team(mine, incoming)
        self.assertEqual(new["workers"], {"KMS": "홍길동", "HYC": "김하늘", "JJH": "이서연"})
        self.assertEqual(new["client_aliases"], {"한결": "한결증권", "다우": "바른기술"})
        self.assertEqual(new["project_roots"], mine["project_roots"])
        self.assertEqual(diff["workers"], {"added": {"JJH": "이서연"},
                                           "changed": {"HYC": ["옛이름", "김하늘"]}, "same": 1})
        self.assertEqual(diff["client_aliases"]["added"], {"다우": "바른기술"})
        self.assertEqual(diff["client_aliases"]["same"], 1)
        r = diff["rules"]
        self.assertEqual([(e["path"], e["exists"]) for e in r["added"]],
                         [(os.path.normpath("/없는/경로/렌더"), False)])
        self.assertEqual([(e["cat"], e["old_cat"], e["exists"]) for e in r["changed"]],
                         [(C.SOURCE, C.RENDER, True)])
        self.assertEqual(r["missing_paths"], 1)
        cats = {x["path"]: x["cat"] for x in new["rules"]}
        self.assertEqual(cats[os.path.normpath(self.tmp)], C.SOURCE)
        self.assertFalse(os.path.exists(settings.settings_path()))     # import never saves
        for bad in (None, [], {"format": "x"}, {"format": "driveboard-team", "version": 99}):
            with self.assertRaises(ValueError):
                settings.import_team(mine, bad)


if __name__ == "__main__":
    unittest.main()

"""winapi 가짜 백엔드 + 플랫폼 무관 부분(구조체 레이아웃·경로 검사) 테스트."""
import ctypes
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.name == "nt":      # 임시 폴더를 긴 이름으로(…\RUNNER~1\… → …\runneradmin\…): 제품이 경로를 긴 이름으로
    tempfile.tempdir = os.path.realpath(tempfile.gettempdir())     # 돌려주므로 시험의 기대값도 같은 모양이어야 한다

from core import winapi  # noqa: E402

GiB = 1 << 30


def _write(path, n):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x5a" * n)


class FakeBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db-winapi-")
        self.root = os.path.join(self.tmp, "fake")
        os.makedirs(self.root)
        self._prev_root = winapi.get_fake_root()
        self._prev_adobe = os.environ.pop("DRIVEBOARD_FAKE_ADOBE", None)
        self.mk("C", {"label": "SSD1", "kind": "ssd", "total": 10 * GiB, "system_bytes": 1 << 20})
        self.mk("D", {"label": "HDD1", "kind": "hdd", "total": 20 * GiB})
        os.makedirs(os.path.join(self.root, "AB"))          # 두 글자 → 드라이브 아님
        os.makedirs(os.path.join(self.root, "x"))           # 소문자 → 드라이브 아님
        with open(os.path.join(self.root, "E"), "w") as f:  # 파일 → 드라이브 아님
            f.write("x")
        try:
            os.symlink(os.path.join(self.root, "D"), os.path.join(self.root, "F"))
        except (OSError, NotImplementedError):
            pass
        winapi.set_fake_root(self.root)

    def tearDown(self):
        winapi.set_fake_root(self._prev_root)
        if self._prev_adobe is not None:
            os.environ["DRIVEBOARD_FAKE_ADOBE"] = self._prev_adobe
        else:
            os.environ.pop("DRIVEBOARD_FAKE_ADOBE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk(self, letter, meta):
        d = os.path.join(self.root, letter)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_drive.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f)
        return d

    def drive(self, letter):
        return {d["letter"]: d for d in winapi.list_drives()}[letter]

    def test_list_drives_shape(self):
        ds = winapi.list_drives()
        self.assertEqual([d["letter"] for d in ds], ["C", "D"])
        c = ds[0]
        for k in ("letter", "root", "label", "serial", "fs", "kind", "total", "free"):
            self.assertIn(k, c)
        self.assertEqual(c["root"], os.path.join(self.root, "C"))
        self.assertEqual((c["label"], c["kind"], c["total"]), ("SSD1", "ssd", 10 * GiB))
        self.assertEqual(c["free"], 10 * GiB - (1 << 20))
        self.assertEqual(ds[1]["kind"], "hdd")
        self.assertRegex(c["serial"], r"^[0-9A-F]{8}$")
        self.assertNotEqual(c["serial"], ds[1]["serial"])
        self.assertTrue(winapi.is_fake())

    def test_free_counts_files_and_fakebin_not_meta(self):
        d = os.path.join(self.root, "D")
        _write(os.path.join(d, "proj", "a.bin"), 1000)
        _write(os.path.join(d, ".fakebin", "1_old.bin"), 500)
        winapi.invalidate_usage("D")
        self.assertEqual(self.drive("D")["free"], 20 * GiB - 1500)
        self.assertEqual(winapi.disk_usage(d), (20 * GiB, 20 * GiB - 1500))
        self.assertEqual(winapi.disk_usage(os.path.join(d, "proj")), (20 * GiB, 20 * GiB - 1500))

    def test_usage_cache_and_invalidate(self):
        d = os.path.join(self.root, "D")
        before = self.drive("D")["free"]
        _write(os.path.join(d, "new.bin"), 4096)
        self.assertEqual(self.drive("D")["free"], before)          # 캐시
        winapi.invalidate_usage(os.path.join(d, "new.bin"))
        self.assertEqual(self.drive("D")["free"], before - 4096)
        _write(os.path.join(d, "new2.bin"), 10)
        winapi.invalidate_usage()
        self.assertEqual(self.drive("D")["free"], before - 4106)

    def test_bad_meta_defaults(self):
        with open(os.path.join(self.root, "D", "_drive.json"), "w") as f:
            f.write("{not json")
        winapi.invalidate_usage()
        d = self.drive("D")
        self.assertEqual(d["label"], "FAKE D")
        self.assertEqual(d["kind"], "unknown")
        self.assertEqual(d["total"], 1 << 40)

    def test_unknown_kind_normalized(self):
        self.mk("G", {"label": "X", "kind": "tape", "total": GiB})
        self.assertEqual(self.drive("G")["kind"], "unknown")

    def test_disk_usage_outside_raises(self):
        with self.assertRaises(OSError):
            winapi.disk_usage(self.tmp)

    def test_recycle_file_into_fakebin(self):
        d = os.path.join(self.root, "D")
        p = os.path.join(d, "proj", "a.mov")
        _write(p, 2000)
        winapi.invalidate_usage()
        free0 = self.drive("D")["free"]
        self.assertEqual(winapi.recycle_bin_info(d), (0, 0))
        ok, err = winapi.recycle(p)
        self.assertTrue(ok, err)
        self.assertIsNone(err)
        self.assertFalse(os.path.exists(p))
        names = os.listdir(os.path.join(d, ".fakebin"))
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^\d+_a\.mov$")
        self.assertEqual(winapi.recycle_bin_info(d), (2000, 1))
        self.assertEqual(self.drive("D")["free"], free0)   # 휴지통도 공간을 차지한다

    def test_recycle_dir_and_many_order(self):
        d = os.path.join(self.root, "C")
        _write(os.path.join(d, "a", "x.bin"), 10)
        _write(os.path.join(d, "a", "sub", "y.bin"), 20)
        _write(os.path.join(d, "b.bin"), 30)
        paths = [os.path.join(d, "a"), os.path.join(d, "missing.bin"), os.path.join(d, "b.bin")]
        res = winapi.recycle_many(paths)
        self.assertEqual([r[0] for r in res], paths)
        self.assertEqual([r[1] for r in res], [True, False, True])
        self.assertTrue(res[1][2])
        self.assertEqual(winapi.recycle_bin_info(d), (60, 2))

    def test_recycle_refusals(self):
        d = os.path.join(self.root, "D")
        _write(os.path.join(d, ".fakebin", "1_x"), 1)
        for p in (d, d + os.sep, os.path.join(d, ".fakebin"), os.path.join(d, ".fakebin", "1_x"),
                  os.path.join(d, "_drive.json"), os.path.join(self.tmp, "outside.bin"), "", "relative/x"):
            with self.subTest(p=p):
                ok, err = winapi.recycle(p)
                self.assertFalse(ok)
                self.assertTrue(err.endswith("요."), err)
        self.assertTrue(os.path.isdir(d))
        self.assertTrue(os.path.exists(os.path.join(d, "_drive.json")))

    def test_empty_recycle_bin(self):
        d = os.path.join(self.root, "D")
        _write(os.path.join(d, "a.bin"), 3000)
        _write(os.path.join(d, "dir", "b.bin"), 1000)
        self.assertTrue(winapi.recycle(os.path.join(d, "a.bin"))[0])
        self.assertTrue(winapi.recycle(os.path.join(d, "dir"))[0])
        winapi.invalidate_usage()
        free0 = self.drive("D")["free"]
        self.assertEqual(winapi.recycle_bin_info(d), (4000, 2))
        self.assertEqual(winapi.empty_recycle_bin(d), (True, None))
        self.assertEqual(winapi.recycle_bin_info(d), (0, 0))
        self.assertEqual(os.listdir(os.path.join(d, ".fakebin")), [])
        self.assertEqual(self.drive("D")["free"], free0 + 4000)
        self.assertEqual(winapi.empty_recycle_bin(os.path.join(self.root, "C")), (True, None))
        self.assertFalse(winapi.empty_recycle_bin(self.tmp)[0])

    def test_empty_does_not_follow_symlinked_bin(self):
        d = os.path.join(self.root, "C")
        victim = os.path.join(self.tmp, "victim")
        _write(os.path.join(victim, "keep.bin"), 5)
        try:
            os.symlink(victim, os.path.join(d, ".fakebin"))
        except (OSError, NotImplementedError):
            self.skipTest("symlink 불가")
        self.assertFalse(winapi.empty_recycle_bin(d)[0])
        self.assertTrue(os.path.exists(os.path.join(victim, "keep.bin")))

    def test_reveal_noop(self):
        self.assertTrue(winapi.reveal(os.path.join(self.root, "D")))

    def test_running_adobe_env(self):
        self.assertEqual(winapi.running_adobe(), [])
        os.environ["DRIVEBOARD_FAKE_ADOBE"] = "AfterFX.exe, Adobe Premiere Pro.exe"
        self.assertEqual(winapi.running_adobe(), ["AfterFX.exe", "Adobe Premiere Pro.exe"])
        self.assertEqual(winapi.adobe_display_names(winapi.running_adobe()), ["After Effects", "Premiere Pro"])
        os.environ["DRIVEBOARD_FAKE_ADOBE"] = "1"
        self.assertEqual(winapi.running_adobe(), ["AfterFX.exe"])

    def test_no_fake_root_on_linux(self):
        if winapi.IS_WIN:
            self.skipTest("Windows 에선 실제 백엔드가 된다")
        winapi.set_fake_root(None)
        self.assertTrue(winapi.is_fake())
        self.assertEqual(winapi.list_drives(), [])
        ok, err = winapi.recycle(os.path.join(self.root, "D", "x"))
        self.assertFalse(ok)


class PurePieces(unittest.TestCase):
    def test_long_path_noop_off_windows(self):
        p = "/x/" + "a" * 300
        if not winapi.IS_WIN:
            self.assertEqual(winapi.long_path(p), p)

    def test_long_path_nt(self):
        short = "D:\\proj\\a.mp4"
        self.assertEqual(winapi._long_path_nt(short), short)
        long_ = "D:\\" + "\\".join(["folder%03d" % i for i in range(30)]) + "\\a.mp4"
        self.assertGreater(len(long_), 240)
        self.assertEqual(winapi._long_path_nt(long_), "\\\\?\\" + long_)
        unc = "\\\\server\\share\\" + "b" * 250
        self.assertEqual(winapi._long_path_nt(unc), "\\\\?\\UNC\\server\\share\\" + "b" * 250)
        pre = "\\\\?\\D:\\" + "c" * 300
        self.assertEqual(winapi._long_path_nt(pre), pre)
        rel = "folder\\" + "d" * 300
        self.assertEqual(winapi._long_path_nt(rel), rel)
        dotty = "D:\\x\\..\\" + "e" * 300
        self.assertEqual(winapi._long_path_nt(dotty), "\\\\?\\D:\\" + "e" * 300)
        self.assertEqual(winapi._long_path_nt("D:/" + "f" * 300), "\\\\?\\D:\\" + "f" * 300)

    def test_struct_layout_64(self):
        shf, rb = winapi._make_structs(True)
        self.assertEqual(ctypes.sizeof(shf), 56)
        self.assertEqual(shf.wFunc.offset, 8)
        self.assertEqual(shf.pFrom.offset, 16)
        self.assertEqual(shf.pTo.offset, 24)
        self.assertEqual(shf.fFlags.offset, 32)
        self.assertEqual(shf.fAnyOperationsAborted.offset, 36)
        self.assertEqual(shf.hNameMappings.offset, 40)
        self.assertEqual(shf.lpszProgressTitle.offset, 48)
        self.assertEqual(ctypes.sizeof(rb), 24)
        self.assertEqual(rb.i64Size.offset, 8)
        self.assertEqual(rb.i64NumItems.offset, 16)

    def test_struct_layout_32_packed(self):
        shf, rb = winapi._make_structs(False)
        self.assertEqual(ctypes.sizeof(shf), 30)
        self.assertEqual(shf.wFunc.offset, 4)
        self.assertEqual(shf.pFrom.offset, 8)
        self.assertEqual(shf.fFlags.offset, 16)
        self.assertEqual(shf.fAnyOperationsAborted.offset, 18)
        self.assertEqual(shf.hNameMappings.offset, 22)
        self.assertEqual(shf.lpszProgressTitle.offset, 26)
        self.assertEqual(ctypes.sizeof(rb), 20)
        self.assertEqual(rb.i64Size.offset, 4)

    def test_native_struct_matches(self):
        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        self.assertEqual(ctypes.sizeof(winapi.SHFILEOPSTRUCTW), 56 if is64 else 30)
        self.assertEqual(ctypes.sizeof(winapi.SHQUERYRBINFO), 24 if is64 else 20)

    def test_ioctl_structs(self):
        self.assertEqual(ctypes.sizeof(winapi.STORAGE_PROPERTY_QUERY), 12)
        self.assertEqual(ctypes.sizeof(winapi.DEVICE_SEEK_PENALTY_DESCRIPTOR), 12)
        self.assertEqual(winapi.DEVICE_SEEK_PENALTY_DESCRIPTOR.IncursSeekPenalty.offset, 8)
        self.assertEqual(winapi.IOCTL_STORAGE_QUERY_PROPERTY, 0x2D1400)

    def test_flags(self):
        f = winapi.RECYCLE_FLAGS
        for flag in (winapi.FOF_ALLOWUNDO, winapi.FOF_NOCONFIRMATION, winapi.FOF_WANTNUKEWARNING,
                     winapi.FOF_SILENT, winapi.FOF_NOERRORUI):
            self.assertTrue(f & flag)
        self.assertLess(f, 1 << 16)       # FILEOP_FLAGS 는 WORD
        self.assertEqual(winapi.FO_DELETE, 3)
        self.assertEqual(winapi.EMPTY_FLAGS, 7)

    def test_win_root(self):
        self.assertEqual(winapi._win_root("d"), "D:\\")
        self.assertEqual(winapi._win_root("D:"), "D:\\")
        self.assertEqual(winapi._win_root("d:\\"), "D:\\")
        self.assertEqual(winapi._win_root("D:/"), "D:\\")
        for bad in (None, "", "D:\\x", "DD", "\\\\server\\s", 5):
            self.assertIsNone(winapi._win_root(bad), bad)

    def test_win_recycle_precheck(self):
        bad = ["", "D:\\", "D:", "relative\\x", "D:\\a*.mp4", "D:\\a?.mp4", "D:\\x\0y",
               "\\\\?\\D:\\x", "\\\\server\\share\\x", "D:\\" + "a" * 300, "D:\\a:stream"]
        for p in bad:
            with self.subTest(p=p):
                q, err = winapi._win_recycle_check(p)
                self.assertIsNone(q)
                self.assertTrue(err)
        if not winapi.IS_WIN:
            q, err = winapi._win_recycle_check("D:\\proj\\a.mp4")
            self.assertEqual(err, "파일을 찾을 수 없어요.")

    def test_sh_err(self):
        self.assertEqual(winapi._sh_err_ko(32), "다른 프로그램이 사용 중이에요.")
        self.assertIn("0x1234", winapi._sh_err_ko(0x1234))

    def test_kind_from_label(self):
        self.assertEqual(winapi._kind_from_label("SSD2"), "ssd")
        self.assertEqual(winapi._kind_from_label("hdd_8tb"), "hdd")
        self.assertEqual(winapi._kind_from_label("Data"), "unknown")


NAS = "\\\\ORICO\\영상백업"


class FakeDevices(unittest.TestCase):
    """SPEC-2 A.1: 외장·USB·NAS 가짜 드라이브(_drive.json 확장)와 네트워크 위치."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="db-winapi-dev-")
        self.root = os.path.join(self.tmp, "fake")
        os.makedirs(self.root)
        self._prev_root = winapi.get_fake_root()
        self.mk("C", {"label": "SSD1", "kind": "ssd", "total": 10 * GiB})
        self.mk("E", {"label": "외장", "kind": "hdd", "total": 4 * GiB, "external": True})
        self.mk("F", {"label": "USB", "kind": "removable", "total": GiB})               # 옛 이름 → usb
        self.mk("G", {"label": "카드", "kind": "usb", "total": GiB, "has_bin": True})
        self.mk("Z", {"label": "video", "network": True, "unc": "\\\\192.168.0.10\\video", "total": 8 * GiB})
        self.mk("N-orico", {"label": "영상백업", "network": True, "unc": NAS, "total": 16 * GiB})
        self.mk("N-orico2", {"label": "자료", "network": True, "unc": "//orico/자료/", "total": GiB})
        self.mk("N-other", {"label": "남의", "network": True, "unc": "\\\\OTHER\\x", "total": GiB})
        self.mk("N-nounc", {"label": "UNC 없음", "network": True, "total": GiB})
        winapi.set_fake_root(self.root)

    def tearDown(self):
        winapi.set_fake_root(self._prev_root)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk(self, name, meta):
        d = os.path.join(self.root, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "_drive.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        return d

    def by_key(self, **kw):
        return {d["letter"]: d for d in winapi.list_drives(**kw)}

    def test_fields_and_kinds(self):
        ds = self.by_key()
        nk = winapi.network_key(NAS)
        self.assertEqual(sorted(ds), sorted(["C", "E", "F", "G", "Z", nk, winapi.network_key("\\\\orico\\자료"),
                                             winapi.network_key("\\\\OTHER\\x"), "N-nounc"]))
        for d in ds.values():
            for k in ("letter", "root", "label", "serial", "fs", "kind", "total", "free",
                      "external", "has_bin", "scan", "connected", "unc"):
                self.assertIn(k, d)
            self.assertIn(d["kind"], winapi.KINDS)
            self.assertTrue(d["connected"])
        exp = {  # kind, external, has_bin, scan, unc
            "C": ("ssd", False, True, True, None),
            "E": ("hdd", True, True, False, None),
            "F": ("usb", True, False, False, None),
            "G": ("usb", True, True, False, None),
            "Z": ("network", False, False, False, "\\\\192.168.0.10\\video"),
            nk: ("network", False, False, False, NAS),
        }
        for k, v in exp.items():
            d = ds[k]
            self.assertEqual((d["kind"], d["external"], d["has_bin"], d["scan"], d["unc"]), v, k)
        self.assertEqual(ds[nk]["root"], os.path.join(self.root, "N-orico"))
        self.assertEqual(ds[nk]["total"], 16 * GiB)
        self.assertRegex(ds[nk]["serial"], r"^[0-9A-F]{8}$")
        self.assertEqual(ds["N-nounc"]["unc"], None)

    def test_network_locations_filter_and_offline(self):
        nk = winapi.network_key(NAS)
        dead = "\\\\DEAD\\share"
        ds = self.by_key(network_locations=[{"path": "//orico/영상백업/", "label": "팀 백업"},
                                            {"path": dead, "label": ""}, "\\\\orico\\영상백업",   # 중복
                                            {"path": "not unc"}, 42])
        self.assertEqual(sorted(ds), sorted(["C", "E", "F", "G", "Z", nk, winapi.network_key(dead)]))
        self.assertEqual(ds[nk]["label"], "팀 백업")
        self.assertTrue(ds[nk]["connected"])
        off = ds[winapi.network_key(dead)]
        self.assertEqual((off["connected"], off["kind"], off["total"], off["free"], off["has_bin"]),
                         (False, "network", 0, 0, False))
        self.assertEqual((off["label"], off["unc"], off["root"]), ("share", dead, dead + "\\"))
        self.assertEqual(self.by_key(network_locations=[]).keys(), {"C", "E", "F", "G", "Z"})

    def test_drive_scan_override(self):
        nk = winapi.network_key(NAS)
        ds = self.by_key(drive_scan={"c": False, nk.upper(): True, "E:": True, "Q": True, "Z": "yes"})
        self.assertFalse(ds["C"]["scan"])
        self.assertTrue(ds[nk]["scan"])
        self.assertTrue(ds["E"]["scan"])
        self.assertFalse(ds["Z"]["scan"])                        # bool 이 아니면 무시

    def test_slow_nas_does_not_block(self):
        self.mk("N-slow", {"label": "느림", "network": True, "unc": "\\\\SLOW\\s", "total": GiB, "delay": 3})
        old = winapi.NET_TIMEOUT
        winapi.NET_TIMEOUT = 0.2
        try:
            for _ in range(3):
                t0 = time.monotonic()
                ds = self.by_key(network_locations=[{"path": "\\\\SLOW\\s"}])
                self.assertLess(time.monotonic() - t0, 1.5)
                self.assertFalse(ds[winapi.network_key("\\\\SLOW\\s")]["connected"])
            busy = [t for t in threading.enumerate() if t.name == "driveboard-probe"]
            self.assertLessEqual(len(busy), 1)                     # 같은 NAS 확인은 쌓이지 않는다
            info = winapi.location_info("\\\\slow\\s")
            self.assertFalse(info["connected"])
        finally:
            winapi.NET_TIMEOUT = old

    def test_list_network_shares_fake(self):
        shares, err = winapi.list_network_shares("\\\\ORICO")
        self.assertIsNone(err)
        self.assertEqual(shares, [{"name": "영상백업", "unc": "\\\\ORICO\\영상백업"},
                                  {"name": "자료", "unc": "\\\\orico\\자료"}])
        for host in ("orico", "//ORICO/", "\\\\orico\\영상백업"):
            self.assertEqual(len(winapi.list_network_shares(host)[0]), 2, host)
        shares, err = winapi.list_network_shares("NOPE")
        self.assertEqual(shares, [])
        self.assertTrue(err.endswith("요."), err)
        for bad in ("", "\\\\", "a b", None, "a*b", "?"):
            shares, err = winapi.list_network_shares(bad)
            self.assertEqual(shares, [])
            self.assertTrue(err)

    def test_location_info(self):
        info = winapi.location_info("//ORICO/영상백업")
        self.assertEqual((info["key"], info["unc"], info["share"], info["connected"]),
                         (winapi.network_key(NAS), "\\\\ORICO\\영상백업", "영상백업", True))
        self.assertEqual(info["total"], 16 * GiB)
        off = winapi.location_info("\\\\ORICO\\없음")
        self.assertFalse(off["connected"])
        self.assertEqual(off["label"], "없음")
        self.assertIsNone(winapi.location_info("C:\\x"))

    def test_no_bin_locations(self):
        for name in ("F", "N-orico"):
            d = os.path.join(self.root, name)
            p = os.path.join(d, "x", "a.mov")
            _write(p, 10)
            ok, err = winapi.recycle(p)
            self.assertFalse(ok)
            self.assertEqual(err, winapi.NO_BIN_MSG)
            self.assertTrue(os.path.exists(p))
            self.assertFalse(os.path.exists(os.path.join(d, ".fakebin")))
            self.assertEqual(winapi.recycle_bin_info(d), (0, 0))
            self.assertEqual(winapi.empty_recycle_bin(d), (True, None))
        g = os.path.join(self.root, "G", "y.mov")
        _write(g, 5)
        self.assertEqual(winapi.recycle(g), (True, None))

    def test_disk_usage_and_invalidate_network(self):
        d = os.path.join(self.root, "N-orico")
        nk = winapi.network_key(NAS)
        self.assertEqual(winapi.disk_usage(d), (16 * GiB, 16 * GiB))
        _write(os.path.join(d, "a.bin"), 1000)
        winapi.invalidate_usage(nk.upper())
        self.assertEqual(winapi.disk_usage(os.path.join(d, "a.bin")), (16 * GiB, 16 * GiB - 1000))
        with self.assertRaises(OSError):
            winapi.disk_usage("\\\\DEAD\\share\\")

    def test_path_alive(self):
        self.assertTrue(winapi.path_alive(os.path.join(self.root, "C")))
        self.assertFalse(winapi.path_alive(os.path.join(self.root, "nope")))
        self.assertFalse(winapi.path_alive(""))


class NetworkPieces(unittest.TestCase):
    def test_normalize_unc(self):
        ok = {
            "\\\\ORICO\\영상백업": "\\\\ORICO\\영상백업",
            "\\\\ORICO\\영상백업\\": "\\\\ORICO\\영상백업",
            "//ORICO/영상백업/": "\\\\ORICO\\영상백업",
            "\\\\?\\UNC\\ORICO\\s\\a": "\\\\ORICO\\s\\a",
            "\\\\192.168.0.10\\video\\\\2026\\.\\x": "\\\\192.168.0.10\\video\\2026\\x",
            "  \\\\h\\s  ": "\\\\h\\s",
        }
        for a, b in ok.items():
            self.assertEqual(winapi.normalize_unc(a), b, a)
        for bad in ("", "C:\\x", "\\\\", "\\\\h", "\\\\h\\", "\\\\.\\C:", "\\\\?\\C:\\x", "\\\\\\h\\s",
                    "\\\\h\\s\\..\\x", "\\\\h\\a*b", "\\\\h:1\\s", None, 5, "relative\\x"):
            self.assertIsNone(winapi.normalize_unc(bad), bad)
        self.assertEqual(winapi.unc_root("//h/s"), "\\\\h\\s\\")
        self.assertEqual(winapi.unc_host_share("\\\\h\\s\\x"), ("h", "s"))

    def test_network_key(self):
        import hashlib
        k = winapi.network_key("\\\\ORICO\\영상백업")
        self.assertEqual(k, "N-" + hashlib.sha1("\\\\orico\\영상백업".encode()).hexdigest()[:6])
        self.assertRegex(k, r"^N-[0-9a-f]{6}$")
        for same in ("//orico/영상백업/", "\\\\?\\UNC\\Orico\\영상백업", "\\\\ORICO\\영상백업\\"):
            self.assertEqual(winapi.network_key(same), k)
        self.assertNotEqual(winapi.network_key("\\\\ORICO\\자료"), k)
        self.assertIsNone(winapi.network_key("D:\\x"))

    def test_norm_key_and_host(self):
        for a, b in (("d", "D"), ("D:", "D"), ("D:\\", "D"), ("d:/", "D"), ("N-3FA9C1", "N-3fa9c1"),
                     ("n-3fa9c1", "N-3fa9c1"), ("", ""), ("DD", ""), ("N-", ""), ("N-a/b", ""),
                     (None, ""), ("C:\\x", "")):
            self.assertEqual(winapi.norm_key(a), b, a)
        for a, b in (("\\\\ORICO", "ORICO"), ("ORICO", "ORICO"), ("192.168.0.10", "192.168.0.10"),
                     ("//nas/share", "nas"), ("\\\\nas\\share\\x", "nas")):
            self.assertEqual(winapi.normalize_host(a), b, a)
        for bad in ("", "\\\\", "a b", "?", ".", "a|b", None):
            self.assertIsNone(winapi.normalize_host(bad), bad)

    def test_default_scan(self):
        self.assertTrue(winapi.default_scan("ssd", False))
        self.assertTrue(winapi.default_scan("unknown", False))
        self.assertFalse(winapi.default_scan("hdd", True))
        self.assertFalse(winapi.default_scan("usb", True))
        self.assertFalse(winapi.default_scan("network", False))

    def test_run_with_timeout(self):
        ev = threading.Event()
        done, val, exc = winapi._run_with_timeout(("t", 1), lambda: (ev.wait(5), 7)[1], 0.05)
        self.assertEqual((done, val, exc), (False, None, None))
        done2, _v, _e = winapi._run_with_timeout(("t", 1), lambda: 99, 0.05)   # 아직 도는 것을 기다린다
        self.assertFalse(done2)
        ev.set()
        done, val, exc = winapi._run_with_timeout(("t", 1), lambda: 99, 2)
        self.assertTrue(done)
        self.assertIn(val, (7, 99))
        done, val, exc = winapi._run_with_timeout(("t", 2), lambda: 1 / 0, 2)
        self.assertTrue(done)
        self.assertIsInstance(exc, ZeroDivisionError)

    def test_device_structs(self):
        S = winapi.STORAGE_DEVICE_DESCRIPTOR
        self.assertEqual(S.RemovableMedia.offset, 10)
        self.assertEqual(S.VendorIdOffset.offset, 12)
        self.assertEqual(S.BusType.offset, 28)
        self.assertEqual(S.RawPropertiesLength.offset, 32)
        self.assertEqual(ctypes.sizeof(S), 40)
        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        self.assertEqual(ctypes.sizeof(winapi.SHARE_INFO_1), 24 if is64 else 12)
        self.assertEqual(winapi.SHARE_INFO_1.shi1_type.offset, 8 if is64 else 4)
        self.assertEqual(winapi.StorageDeviceProperty, 0)
        self.assertEqual(winapi.EXTERNAL_BUS, {4, 7, 12, 13})
        self.assertTrue(winapi.is_external_bus(7))
        self.assertFalse(winapi.is_external_bus(11))             # SATA
        self.assertFalse(winapi.is_external_bus(17))             # NVMe
        self.assertFalse(winapi.is_external_bus(None))
        self.assertEqual((winapi.DRIVE_REMOVABLE, winapi.DRIVE_FIXED, winapi.DRIVE_REMOTE), (2, 3, 4))
        self.assertEqual(winapi.MAX_PREFERRED_LENGTH, 0xFFFFFFFF)

    def test_local_has_bin_rule(self):
        """이동식(SD·USB 메모리)은 $RECYCLE.BIN 폴더가 남아 있어도 휴지통 없음 — 셸이 거기선 영구 삭제한다."""
        tmp = tempfile.mkdtemp(prefix="db-hasbin-")
        try:
            with_bin = os.path.join(tmp, "with") + os.sep
            no_bin = os.path.join(tmp, "without") + os.sep
            os.makedirs(with_bin + winapi.BIN_DIR)
            os.makedirs(no_bin)
            f = winapi._win_local_has_bin
            R, F, N = winapi.DRIVE_REMOVABLE, winapi.DRIVE_FIXED, winapi.DRIVE_REMOTE
            for fs in ("FAT32", "exFAT", "NTFS", ""):
                self.assertFalse(f(R, fs, with_bin), "removable %s + 폴더" % fs)
                self.assertFalse(f(R, fs, no_bin), "removable %s" % fs)
            self.assertTrue(f(F, "NTFS", no_bin))
            self.assertTrue(f(F, "ReFS", no_bin))
            self.assertTrue(f(F, "exFAT", with_bin))
            self.assertFalse(f(F, "exFAT", no_bin))
            self.assertFalse(f(N, "NTFS", with_bin))
            self.assertFalse(f(5, "CDFS", with_bin))                  # CD-ROM
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

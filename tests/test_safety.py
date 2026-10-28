"""safety.check 손으로 쓴 벡터 (Windows 경로는 flavor='nt' 로 Linux 에서도 검사)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import safety  # noqa: E402

NT_DRIVES = [{"letter": "C", "root": "C:\\"}, {"letter": "D", "root": "D:\\"},
             {"letter": "H", "root": "H:\\"}]
FAKE_DRIVES = [{"letter": "C", "root": "/fake/C"}, {"letter": "D", "root": "/fake/D"}]

# (path, 허용?, 이유 상수 또는 None)
NT_VECTORS = [
    # 상대·형식
    ("", False, safety.R_EMPTY),
    ("   ", False, safety.R_EMPTY),
    ("C:", False, safety.R_RELATIVE),
    ("C:Windows", False, safety.R_RELATIVE),
    ("Windows\\System32", False, safety.R_RELATIVE),
    ("\\Windows", False, safety.R_RELATIVE),
    ("Projects\\A", False, safety.R_RELATIVE),
    ("D:\\proj\\a\0b", False, safety.R_BADCHAR),
    ("D:\\proj\\a*b.mp4", False, safety.R_BADCHAR),
    ("D:\\proj\\a?.mp4", False, safety.R_BADCHAR),
    ("D:\\proj\\a|b", False, safety.R_BADCHAR),
    # 상위 이동
    ("D:\\Projects\\..\\Windows", False, safety.R_DOTDOT),
    ("D:\\Projects\\..\\x", False, safety.R_DOTDOT),
    ("D:\\..", False, safety.R_DOTDOT),
    ("D:/Projects/../../x", False, safety.R_DOTDOT),
    ("D:\\Projects\\.\\A", True, None),
    # 루트
    ("C:\\", False, safety.R_ROOT),
    ("D:\\", False, safety.R_ROOT),
    ("d:/", False, safety.R_ROOT),
    ("H:\\\\", False, safety.R_ROOT),
    # 시스템 폴더
    ("C:\\Windows", False, safety.R_SYSDIR),
    ("c:\\windows\\system32\\drivers\\etc", False, safety.R_SYSDIR),
    ("C:\\Program Files", False, safety.R_SYSDIR),
    ("C:\\Program Files\\Adobe\\Adobe After Effects 2025", False, safety.R_SYSDIR),
    ("C:\\Program Files (x86)\\Steam", False, safety.R_SYSDIR),
    ("C:\\ProgramData\\Adobe", False, safety.R_SYSDIR),
    ("D:\\$Recycle.Bin\\S-1-5-21", False, safety.R_SYSDIR),
    ("D:\\System Volume Information", False, safety.R_SYSDIR),
    ("C:\\Recovery", False, safety.R_SYSDIR),
    ("C:\\Boot\\BCD", False, safety.R_SYSDIR),
    ("C:\\EFI\\Microsoft", False, safety.R_SYSDIR),
    ("C:\\$WinREAgent", False, safety.R_SYSDIR),
    ("C:\\$WINDOWS.~BT\\Sources", False, safety.R_SYSDIR),
    ("C:\\Documents and Settings", False, safety.R_SYSDIR),
    ("H:\\Windows", False, safety.R_SYSDIR),            # 다른 드라이브에도 같은 규칙
    ("D:\\Program Files Backup", True, None),            # 이름이 같지 않으면 허용
    ("D:\\Projects\\Windows", True, None),               # 루트 바로 아래가 아니면 허용
    # 루트 시스템 파일
    ("C:\\pagefile.sys", False, safety.R_SYSFILE),
    ("C:\\hiberfil.sys", False, safety.R_SYSFILE),
    ("C:\\swapfile.sys", False, safety.R_SYSFILE),
    ("C:\\bootmgr", False, safety.R_SYSFILE),
    ("C:\\BOOTNXT", False, safety.R_SYSFILE),
    ("C:\\DumpStack.log.tmp", False, safety.R_SYSFILE),
    ("D:\\Projects\\pagefile.sys", True, None),
    # Users
    ("C:\\Users", False, safety.R_USERS),
    ("C:\\Users\\kms", False, safety.R_PROFILE),
    ("C:\\Users\\Public", False, safety.R_PROFILE),
    ("c:\\USERS\\Kms", False, safety.R_PROFILE),
    ("C:\\Users\\kms\\NTUSER.DAT", False, safety.R_USERFILE),
    ("C:\\Users\\kms\\ntuser.dat.LOG1", False, safety.R_USERFILE),
    ("C:\\Users\\kms\\AppData", False, safety.R_APPDATA),
    ("C:\\Users\\kms\\AppData\\Local", False, safety.R_APPDATA),
    ("C:\\Users\\kms\\AppData\\Roaming", False, safety.R_APPDATA),
    ("C:\\Users\\kms\\AppData\\LocalLow", False, safety.R_APPDATA),
    ("C:\\Users\\kms\\AppData\\Local\\Temp", False, safety.R_TEMP),
    ("C:\\Users\\kms\\AppData\\Local\\Temp\\x", True, None),
    ("C:\\Users\\kms\\AppData\\Local\\Temp\\AdobeTemp\\y.tmp", True, None),
    ("C:\\Users\\kms\\AppData\\Roaming\\Adobe\\Common\\Media Cache Files", True, None),
    ("C:\\Users\\kms\\AppData\\LocalLow\\x", True, None),
    ("C:\\Users\\kms\\Desktop", False, safety.R_STD),
    ("c:\\users\\KMS\\desktop", False, safety.R_STD),
    ("C:\\Users\\kms\\Desktop\\render.mp4", True, None),
    ("C:\\Users\\kms\\Documents", False, safety.R_STD),
    ("C:\\Users\\kms\\Downloads", False, safety.R_STD),
    ("C:\\Users\\kms\\Downloads\\setup.zip", True, None),
    ("C:\\Users\\kms\\Pictures", False, safety.R_STD),
    ("C:\\Users\\kms\\Videos", False, safety.R_STD),
    ("C:\\Users\\kms\\Videos\\proj", True, None),
    ("C:\\Users\\kms\\Music", False, safety.R_STD),
    ("C:\\Users\\kms\\OneDrive", False, safety.R_STD),
    ("C:\\Users\\kms\\OneDrive - Studio", False, safety.R_STD),
    ("C:\\Users\\kms\\projects", True, None),
    ("H:\\Users\\kms", False, safety.R_PROFILE),
    ("H:\\Users\\kms\\Desktop\\x", True, None),
    # 알 수 없는 드라이브·네트워크·장치
    ("Z:\\Projects", False, safety.R_UNKNOWN),
    ("E:\\x", False, safety.R_UNKNOWN),
    ("\\\\server\\share\\x", False, safety.R_UNKNOWN),       # 등록 안 한 공유
    ("//server/share/x", False, safety.R_UNKNOWN),
    ("\\\\?\\UNC\\server\\share\\x", False, safety.R_UNKNOWN),
    ("\\\\.\\D:", False, safety.R_NETWORK),
    ("\\\\.\\PhysicalDrive0\\x", False, safety.R_NETWORK),
    ("\\\\?\\GLOBALROOT\\Device\\x", False, safety.R_NETWORK),
    ("\\\\server", False, safety.R_NETWORK),
    ("\\\\server\\", False, safety.R_NETWORK),
    ("\\\\?\\D:\\Projects\\A", True, None),
    ("\\\\?\\C:\\Windows", False, safety.R_SYSDIR),
    # Windows 가 다르게 해석하는 이름
    ("D:\\Windows.", False, safety.R_NAME),
    ("C:\\Windows \\x", False, safety.R_NAME),
    ("C:\\Program Files.\\x", False, safety.R_NAME),
    ("C:\\PROGRA~1\\Adobe", False, safety.R_NAME),
    ("C:\\Users\\KOOMIN~1\\Desktop\\x", False, safety.R_NAME),
    ("D:\\proj\\file.mp4:stream", False, safety.R_NAME),
    ("D:\\proj\\CON", False, safety.R_NAME),
    ("D:\\proj\\nul.txt", False, safety.R_NAME),
    ("D:\\proj\\COM1.log", False, safety.R_NAME),
    # 평범한 작업 경로
    ("D:/Projects/A", True, None),
    ("D:\\Projects\\A\\", True, None),
    ("D:\\Projects\\\\A", True, None),
    ("D:\\Projects\\Media Cache Files", True, None),
    ("H:\\납품\\2025\\최종_v3.mov", True, None),
    ("D:\\console\\a.mp4", True, None),
    ("D:\\proj\\shot~a.exr", True, None),
]

FAKE_VECTORS = [
    ("/fake/D", False, safety.R_ROOT),
    ("/fake/D/", False, safety.R_ROOT),
    ("/fake/d", False, safety.R_ROOT),                    # 가짜 루트도 대소문자 무시
    ("/fake/D/Windows/x", False, safety.R_SYSDIR),
    ("/fake/D/windows", False, safety.R_SYSDIR),
    ("/fake/D/Program Files (x86)", False, safety.R_SYSDIR),
    ("/fake/D/pagefile.sys", False, safety.R_SYSFILE),
    ("/fake/D/proj/a.mp4", True, None),
    ("/fake/D/.fakebin", False, safety.R_FAKEMETA),
    ("/fake/D/.fakebin/123_a.mp4", False, safety.R_FAKEMETA),
    ("/fake/D/_drive.json", False, safety.R_FAKEMETA),
    ("/fake/D/proj/_drive.json", True, None),
    ("/fake/E/x", False, safety.R_UNKNOWN),
    ("/fake", False, safety.R_UNKNOWN),
    ("/fakeD/x", False, safety.R_UNKNOWN),
    ("/fake/DD/x", False, safety.R_UNKNOWN),
    ("fake/D/x", False, safety.R_RELATIVE),
    ("/fake/D/../C/x", False, safety.R_DOTDOT),
    ("/fake/D/proj/sub/../x", False, safety.R_DOTDOT),
    ("/fake/D/Users", False, safety.R_USERS),
    ("/fake/D/Users/kms", False, safety.R_PROFILE),
    ("/fake/D/Users/kms/AppData/Local", False, safety.R_APPDATA),
    ("/fake/D/Users/kms/AppData/Local/Temp/x", True, None),
    ("/fake/D/Users/kms/Desktop", False, safety.R_STD),
    ("/fake/D/Users/kms/Desktop/x", True, None),
    ("/fake/C/proj//a.mp4", True, None),
]


# SPEC-2 A.1: 외장·USB·NAS 에는 시스템 폴더 규칙을 적용하지 않는다.
EXT_DRIVES = NT_DRIVES + [
    {"letter": "N-3fa9c1", "root": "\\\\ORICO\\영상백업\\", "kind": "network", "external": False},
    {"letter": "N-77aa00", "root": "\\\\192.168.0.10\\video\\2026\\", "kind": "network"},
    {"letter": "Z", "root": "Z:\\", "kind": "network"},
    {"letter": "E", "root": "E:\\", "kind": "hdd", "external": True},
    {"letter": "F", "root": "F:\\", "kind": "usb", "external": True},
]
EXT_VECTORS = [
    # NAS 공유(UNC)
    ("\\\\ORICO\\영상백업", False, safety.R_ROOT),
    ("\\\\ORICO\\영상백업\\", False, safety.R_ROOT),
    ("\\\\orico\\영상백업\\", False, safety.R_ROOT),                 # 대소문자 무시
    ("//ORICO/영상백업/", False, safety.R_ROOT),
    ("\\\\?\\UNC\\ORICO\\영상백업", False, safety.R_ROOT),           # 확장 길이 UNC
    ("\\\\ORICO\\영상백업\\Projects\\A", True, None),
    ("\\\\?\\UNC\\ORICO\\영상백업\\Projects\\A", True, None),
    ("//orico/영상백업/Projects/A/x.mov", True, None),
    ("\\\\ORICO\\영상백업\\Users", True, None),                       # NAS 안의 Users 는 사용자 폴더
    ("\\\\ORICO\\영상백업\\Users\\kms", True, None),
    ("\\\\ORICO\\영상백업\\Users\\kms\\Desktop", True, None),
    ("\\\\ORICO\\영상백업\\EFI", True, None),
    ("\\\\ORICO\\영상백업\\Windows", True, None),
    ("\\\\ORICO\\영상백업\\Program Files\\x", True, None),
    ("\\\\ORICO\\영상백업\\pagefile.sys", True, None),
    ("\\\\ORICO\\영상백업\\#recycle", False, safety.R_DEVSYS),
    ("\\\\ORICO\\영상백업\\#recycle\\old.mov", False, safety.R_DEVSYS),
    ("\\\\ORICO\\영상백업\\#snapshot", False, safety.R_DEVSYS),
    ("\\\\ORICO\\영상백업\\Projects\\@eaDir", False, safety.R_DEVSYS),
    ("\\\\ORICO\\영상백업\\Projects\\.DS_Store", False, safety.R_DEVSYS),
    ("\\\\ORICO\\영상백업\\$RECYCLE.BIN", False, safety.R_DEVSYS),
    ("\\\\ORICO\\영상백업\\System Volume Information", False, safety.R_DEVSYS),
    ("\\\\ORICO\\영상백업\\a\\..\\b", False, safety.R_DOTDOT),
    ("\\\\ORICO\\영상백업\\a*b", False, safety.R_BADCHAR),
    ("\\\\ORICO\\영상백업\\CON", False, safety.R_NAME),
    ("\\\\ORICO\\다른공유\\x", False, safety.R_UNKNOWN),
    ("\\\\ORICO2\\영상백업\\x", False, safety.R_UNKNOWN),
    # 하위 폴더를 루트로 등록한 위치
    ("\\\\192.168.0.10\\video\\2026", False, safety.R_ROOT),
    ("\\\\192.168.0.10\\video\\2026\\Windows", True, None),
    ("\\\\192.168.0.10\\video\\2025\\x", False, safety.R_UNKNOWN),
    ("\\\\192.168.0.10\\video\\2026\\_drive.json", True, None),
    # 매핑된 네트워크 드라이브
    ("Z:\\", False, safety.R_ROOT),
    ("Z:\\Users\\kms", True, None),
    ("Z:\\Windows\\x", True, None),
    ("Z:\\@eaDir", False, safety.R_DEVSYS),
    # 외장 HDD(고정으로 보임) · USB 메모리
    ("E:\\", False, safety.R_ROOT),
    ("E:\\Users", True, None),
    ("E:\\Users\\kms\\AppData", True, None),
    ("E:\\EFI\\Boot", True, None),
    ("E:\\Windows", True, None),
    ("E:\\$RECYCLE.BIN\\S-1-5", False, safety.R_DEVSYS),
    ("E:\\System Volume Information", False, safety.R_DEVSYS),
    ("E:\\.fakebin", False, safety.R_DEVSYS),
    ("F:\\Program Files", True, None),
    ("F:\\DCIM\\.DS_Store", False, safety.R_DEVSYS),
    ("F:\\Windows.", False, safety.R_NAME),                # 이름 해석 규칙은 어디서나
    ("F:\\PROGRA~1", False, safety.R_NAME),
    # 내장 C·D 는 그대로 막힌다
    ("C:\\Users", False, safety.R_USERS),
    ("C:\\Windows", False, safety.R_SYSDIR),
    ("C:\\EFI", False, safety.R_SYSDIR),
    ("D:\\Users\\kms", False, safety.R_PROFILE),
]
FAKE_EXT_DRIVES = FAKE_DRIVES + [
    {"letter": "N-aaaaaa", "root": "/fake/N-orico", "kind": "network", "unc": "\\\\ORICO\\영상백업"},
    {"letter": "U", "root": "/fake/U", "kind": "usb", "external": True},
]
FAKE_EXT_VECTORS = [
    ("/fake/N-orico", False, safety.R_ROOT),
    ("/fake/N-orico/Users/kms", True, None),
    ("/fake/N-orico/Windows", True, None),
    ("/fake/N-orico/_drive.json", False, safety.R_FAKEMETA),
    ("/fake/N-orico/.fakebin", False, safety.R_FAKEMETA),
    ("/fake/N-orico/@eaDir/x", False, safety.R_DEVSYS),
    ("/fake/U/EFI", True, None),
    ("/fake/U/.fakebin/1_x", False, safety.R_FAKEMETA),
    ("/fake/C/Users", False, safety.R_USERS),
]


class SafetyVectors(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get("DRIVEBOARD_HOME")
        os.environ["DRIVEBOARD_HOME"] = "/nonexistent-driveboard-home-for-tests"
        safety.set_extra_protected([])
        # 손으로 쓴 벡터는 순수 문자열 판정을 본다 — Windows 러너에선 C:\\PROGRA~1 이 실제로 있어서
        # 짧은 이름 펼치기가 켜져 있으면 결과가 그 PC 디스크에 따라 달라진다(펼치기는 ShortNames 에서 본다).
        self._long = safety._long_name
        safety._long_name = None

    def tearDown(self):
        safety._long_name = self._long
        if self._home is None:
            os.environ.pop("DRIVEBOARD_HOME", None)
        else:
            os.environ["DRIVEBOARD_HOME"] = self._home
        safety.set_extra_protected([])

    def test_vector_count(self):
        self.assertGreaterEqual(len(NT_VECTORS) + len(FAKE_VECTORS), 50)
        self.assertGreaterEqual(len(EXT_VECTORS) + len(FAKE_EXT_VECTORS), 50)

    def test_nt_vectors(self):
        for path, ok, reason in NT_VECTORS:
            with self.subTest(path=path):
                got_ok, got_reason = safety.check(path, NT_DRIVES, flavor="nt")
                self.assertEqual(got_ok, ok, "%r → %r" % (path, got_reason))
                if reason is not None:
                    self.assertEqual(got_reason, reason, path)
                else:
                    self.assertEqual(got_reason, "")

    def test_fake_vectors(self):
        for path, ok, reason in FAKE_VECTORS:
            with self.subTest(path=path):
                got_ok, got_reason = safety.check(path, FAKE_DRIVES, flavor="posix")
                self.assertEqual(got_ok, ok, "%r → %r" % (path, got_reason))
                if reason is not None:
                    self.assertEqual(got_reason, reason, path)

    def test_external_vectors(self):
        for path, ok, reason in EXT_VECTORS:
            with self.subTest(path=path):
                got_ok, got_reason = safety.check(path, EXT_DRIVES, flavor="nt")
                self.assertEqual(got_ok, ok, "%r → %r" % (path, got_reason))
                self.assertEqual(got_reason, reason or "", path)
        for path, ok, reason in FAKE_EXT_VECTORS:
            with self.subTest(path=path):
                got_ok, got_reason = safety.check(path, FAKE_EXT_DRIVES, flavor="posix")
                self.assertEqual(got_ok, ok, "%r → %r" % (path, got_reason))
                self.assertEqual(got_reason, reason or "", path)

    def test_unc_drive_of_and_normalize(self):
        d, rel = safety.drive_of("\\\\?\\UNC\\orico\\영상백업\\A\\b.mov", EXT_DRIVES, flavor="nt")
        self.assertEqual((d["letter"], rel), ("N-3fa9c1", ["A", "b.mov"]))
        d, rel = safety.drive_of("//192.168.0.10/video/2026/x", EXT_DRIVES, flavor="nt")
        self.assertEqual((d["letter"], rel), ("N-77aa00", ["x"]))
        self.assertEqual(safety.normalize("//ORICO/영상백업//a/./b", flavor="nt"), "\\\\ORICO\\영상백업\\a\\b")
        self.assertEqual(safety.normalize("\\\\?\\UNC\\ORICO\\s", flavor="nt"), "\\\\ORICO\\s\\")
        self.assertIsNone(safety.normalize("\\\\.\\C:\\x", flavor="nt"))
        self.assertTrue(safety.mod_for("\\\\srv\\s") is __import__("ntpath") or safety.IS_WIN)

    def test_is_external(self):
        self.assertTrue(safety.is_external({"kind": "network"}))
        self.assertTrue(safety.is_external({"kind": "usb"}))
        self.assertTrue(safety.is_external({"kind": "ssd", "external": True}))
        self.assertFalse(safety.is_external({"kind": "ssd", "external": False}))
        self.assertFalse(safety.is_external({"letter": "C"}))
        self.assertFalse(safety.is_external("E:\\"))
        # 키만 있고 루트가 없는 네트워크 dict 는 'N:' 로 추측하지 않는다
        self.assertEqual(safety.check("N:\\x", [{"letter": "N-3fa9c1"}], flavor="nt"), (False, safety.R_UNKNOWN))

    def test_loopback_and_own_pc_shares_are_refused(self):
        """\\\\localhost\\C$ 같은 자기 PC 공유는 외장 규칙(루트만 보호)이 적용돼 Windows 폴더가 열린다 → 전부 거부."""
        me = (safety._safe_hostname() or "ThisPC").split(".", 1)[0]
        locs = [{"letter": "N-1", "root": "\\\\localhost\\C$\\", "kind": "network", "unc": "\\\\localhost\\C$"},
                {"letter": "N-2", "root": "\\\\127.0.0.1\\c$\\", "kind": "network", "unc": "\\\\127.0.0.1\\c$"},
                {"letter": "N-3", "root": "\\\\%s\\Users\\" % me, "kind": "network", "unc": "\\\\%s\\Users" % me},
                {"letter": "Z", "root": "Z:\\", "kind": "network", "unc": "\\\\LOCALHOST\\D$"}]
        for path in ("\\\\localhost\\C$\\Windows\\System32", "\\\\localhost\\C$\\proj\\a.mov",
                     "//127.0.0.1/c$/Users/me/AppData", "\\\\%s\\Users\\me\\AppData" % me, "Z:\\proj\\a.mov",
                     "\\\\?\\UNC\\localhost\\C$\\Windows"):
            with self.subTest(path=path):
                self.assertEqual(safety.check(path, locs, flavor="nt"), (False, safety.R_LOCAL_SHARE))
        self.assertTrue(safety.is_local_unc("\\\\[::1]\\c$"))
        self.assertFalse(safety.is_local_unc("\\\\ORICO\\영상백업"))
        self.assertFalse(safety.is_local_unc("C:\\Windows"))
        self.assertFalse(safety.is_local_unc(None))
        # 진짜 NAS 는 그대로(숨김 공유 이름 '$' 도 NAS 에선 정상)
        self.assertEqual(safety.check("\\\\ORICO\\영상백업\\A\\b.mov", EXT_DRIVES, flavor="nt"), (True, ""))

    def test_extra_protected_applies_on_external(self):
        safety.set_extra_protected(["E:\\Tools\\DriveBoard"])
        self.assertEqual(safety.check("E:\\Tools\\DriveBoard\\x", EXT_DRIVES, flavor="nt"), (False, safety.R_SELF))
        self.assertEqual(safety.check("E:\\Tools", EXT_DRIVES, flavor="nt"), (False, safety.R_SELF_PARENT))
        self.assertEqual(safety.check("\\\\ORICO\\영상백업\\a.exe", EXT_DRIVES, flavor="nt", cat=9),
                         (False, safety.R_APPCAT))

    def test_none_and_non_string(self):
        self.assertEqual(safety.check(None, NT_DRIVES, flavor="nt"), (False, safety.R_EMPTY))
        self.assertEqual(safety.check(123, NT_DRIVES, flavor="nt"), (False, safety.R_EMPTY))

    def test_reasons_are_polite_korean(self):
        for name in dir(safety):
            if name.startswith("R_"):
                text = getattr(safety, name)
                self.assertTrue(text.endswith("요."), "%s: %s" % (name, text))

    def test_no_drives(self):
        self.assertEqual(safety.check("D:\\Projects", [], flavor="nt"), (False, safety.R_UNKNOWN))

    def test_drives_as_strings_and_letters(self):
        self.assertTrue(safety.check("D:\\proj", ["D:\\"], flavor="nt")[0])
        self.assertTrue(safety.check("D:\\proj", [{"letter": "D"}], flavor="nt")[0])
        self.assertFalse(safety.check("D:\\", [{"letter": "D"}], flavor="nt")[0])

    def test_app_category(self):
        self.assertEqual(safety.check("D:\\tools\\a.exe", NT_DRIVES, flavor="nt", cat=9),
                         (False, safety.R_APPCAT))
        self.assertTrue(safety.check("D:\\tools\\a.mp4", NT_DRIVES, flavor="nt", cat=1)[0])

    def test_extra_protected(self):
        safety.set_extra_protected(["D:\\Tools\\DriveBoard"])
        self.assertEqual(safety.check("D:\\Tools\\DriveBoard", NT_DRIVES, flavor="nt"),
                         (False, safety.R_SELF))
        self.assertEqual(safety.check("d:\\tools\\driveboard\\core\\ops.py", NT_DRIVES, flavor="nt"),
                         (False, safety.R_SELF))
        self.assertEqual(safety.check("D:\\Tools", NT_DRIVES, flavor="nt"),
                         (False, safety.R_SELF_PARENT))
        self.assertTrue(safety.check("D:\\Tools2", NT_DRIVES, flavor="nt")[0])
        self.assertTrue(safety.check("D:\\Tools\\Other", NT_DRIVES, flavor="nt")[0])
        self.assertTrue(safety.check("H:\\Tools\\DriveBoard", NT_DRIVES, flavor="nt")[0])

    def test_builtin_data_home(self):
        if safety.IS_WIN:
            # Windows: os.path.abspath 가 드라이브 문자를 붙인다 → 같은 규칙을 NT 경로로 본다
            os.environ["DRIVEBOARD_HOME"] = "D:\\dbhome"
            self.assertEqual(safety.check("D:\\dbhome\\jobs.json", NT_DRIVES, flavor="nt"), (False, safety.R_SELF))
            self.assertEqual(safety.check("D:\\dbhome", NT_DRIVES, flavor="nt"), (False, safety.R_SELF))
            self.assertTrue(safety.check("D:\\dbhome2", NT_DRIVES, flavor="nt")[0])
            return
        os.environ["DRIVEBOARD_HOME"] = "/fake/D/dbhome"
        self.assertEqual(safety.check("/fake/D/dbhome/jobs.json", FAKE_DRIVES, flavor="posix"), (False, safety.R_SELF))
        self.assertEqual(safety.check("/fake/D/dbhome", FAKE_DRIVES, flavor="posix"), (False, safety.R_SELF))
        self.assertTrue(safety.check("/fake/D/dbhome2", FAKE_DRIVES, flavor="posix")[0])

    def test_builtin_app_dir(self):
        app = os.path.dirname(os.path.dirname(os.path.abspath(safety.__file__)))
        drives = [{"letter": "D", "root": os.path.dirname(app)}]
        self.assertEqual(safety.check(app, drives), (False, safety.R_SELF))
        self.assertEqual(safety.check(os.path.join(app, "core"), drives), (False, safety.R_SELF))
        # 가짜 드라이브를 앱 폴더 안에 둔 경우엔 앱 폴더 규칙이 드라이브 전체를 막지 않는다
        inside = [{"letter": "D", "root": os.path.join(app, "fake", "D")}]
        self.assertTrue(safety.check(os.path.join(app, "fake", "D", "proj"), inside)[0])

    def test_drive_of_and_normalize(self):
        d, rel = safety.drive_of("d:/Projects\\A/b.mp4", NT_DRIVES, flavor="nt")
        self.assertEqual(d["letter"], "D")
        self.assertEqual(rel, ["Projects", "A", "b.mp4"])
        self.assertIsNone(safety.drive_of("Z:\\x", NT_DRIVES, flavor="nt"))
        d, rel = safety.drive_of("/fake/d/Proj/x", FAKE_DRIVES, flavor="posix")
        self.assertEqual((d["letter"], rel), ("D", ["Proj", "x"]))
        self.assertEqual(safety.normalize("d:/a//b/./c", flavor="nt"), "D:\\a\\b\\c")
        self.assertEqual(safety.normalize("/a//b/./c/", flavor="posix"), "/a/b/c")
        self.assertIsNone(safety.normalize("a/../b", flavor="posix"))
        self.assertIsNone(safety.normalize("relative", flavor="posix"))

    def test_longest_root_wins(self):
        drives = [{"letter": "C", "root": "/x"}, {"letter": "D", "root": "/x/D"}]
        d, rel = safety.drive_of("/x/D/a", drives, flavor="posix")
        self.assertEqual(d["letter"], "D")
        self.assertEqual(rel, ["a"])

    def test_mod_for(self):
        import ntpath
        import posixpath
        self.assertIs(safety.mod_for("C:\\x", "nt"), ntpath)
        self.assertIs(safety.mod_for("/x", "posix"), posixpath)
        if not safety.IS_WIN:
            self.assertIs(safety.mod_for("D:\\x"), ntpath)
            self.assertIs(safety.mod_for("/x"), posixpath)


class ShortNames(unittest.TestCase):
    """8.3 짧은 이름: 디스크에 있는 부분은 긴 이름으로 펼쳐 판정하고, 펼칠 수 없으면 거부한다."""

    # 가짜 디스크: 짧은 이름 → 긴 이름 (대소문자 무시). 여기 없는 경로는 「없음」.
    DISK = {
        "c:\\progra~1": "C:\\Program Files",
        "c:\\progra~1\\adobe": "C:\\Program Files\\Adobe",
        "c:\\users": "C:\\Users",
        "c:\\users\\runner~1": "C:\\Users\\runneradmin",
        "c:\\users\\runner~1\\appdata": "C:\\Users\\runneradmin\\AppData",
        "c:\\users\\runner~1\\appdata\\local": "C:\\Users\\runneradmin\\AppData\\Local",
        "c:\\users\\runner~1\\appdata\\local\\temp":
            "C:\\Users\\runneradmin\\AppData\\Local\\Temp",
        "c:\\users\\runner~1\\appdata\\local\\temp\\db-x":
            "C:\\Users\\runneradmin\\AppData\\Local\\Temp\\db-x",
        "d:\\abc~1": "D:\\ABC~1",                     # 긴 이름이 정말로 'ABC~1' 인 폴더
        "\\\\nas\\share\\projec~1": "\\\\nas\\share\\Project Archive",
    }

    def setUp(self):
        self._long = safety._long_name
        self.calls = []

        def fake(p):
            self.calls.append(p)
            return self.DISK.get(p.casefold())
        safety._long_name = fake
        self.drives = [{"letter": "C", "root": "C:\\"}, {"letter": "D", "root": "D:\\"},
                       {"letter": "N-1", "root": "\\\\nas\\share", "kind": "network", "network": True}]

    def tearDown(self):
        safety._long_name = self._long

    def test_existing_short_path_is_expanded(self):
        tmp = "C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\db-x"
        long_ = "C:\\Users\\runneradmin\\AppData\\Local\\Temp\\db-x"
        self.assertEqual(safety.expand_short_names(tmp), long_)
        self.assertEqual(safety.normalize(tmp + "\\a.mov", flavor="nt"), long_ + "\\a.mov")
        self.assertEqual(safety.check(tmp + "\\a.mov", self.drives, flavor="nt"), (True, ""))
        d, rel = safety.drive_of(tmp + "\\new\\b.mov", self.drives, flavor="nt")
        self.assertEqual(rel, ["Users", "runneradmin", "AppData", "Local", "Temp", "db-x", "new", "b.mov"])

    def test_protection_applies_to_expanded_form(self):
        # 짧은 이름이 Program Files 를 가리키면 시스템 폴더로 막는다(이름 거부가 아니라)
        self.assertEqual(safety.check("C:\\PROGRA~1\\Adobe", self.drives, flavor="nt"),
                         (False, safety.R_SYSDIR))
        self.assertEqual(safety.check("C:\\Users\\RUNNER~1\\AppData\\Local\\Temp", self.drives,
                                      flavor="nt"), (False, safety.R_TEMP))
        self.assertEqual(safety.check("C:\\Users\\RUNNER~1", self.drives, flavor="nt"),
                         (False, safety.R_PROFILE))

    def test_unexpandable_short_name_still_refused(self):
        self.assertEqual(safety.check("C:\\Users\\KOOMIN~1\\Desktop\\x", self.drives, flavor="nt"),
                         (False, safety.R_NAME))
        # 앞부분은 펼쳤지만 짧은 이름처럼 생긴 조각이 디스크에 없다 → 거부
        self.assertEqual(safety.check("C:\\Users\\RUNNER~1\\NEWDIR~1\\x", self.drives, flavor="nt"),
                         (False, safety.R_NAME))
        self.assertIsNone(safety.normalize("C:\\Users\\RUNNER~1\\NEWDIR~1", flavor="nt"))

    def test_real_long_name_that_looks_short_is_allowed(self):
        self.assertEqual(safety.check("D:\\ABC~1\\x.mov", self.drives, flavor="nt"), (True, ""))

    def test_other_ambiguous_forms_not_expanded(self):
        self.calls.clear()
        for p, why in (("C:\\PROGRA~1.\\x", safety.R_NAME), ("C:\\PROGRA~1 \\x", safety.R_NAME),
                       ("C:\\PROGRA~1\\a.txt:s", safety.R_NAME),
                       ("C:\\PROGRA~1\\..\\Windows", safety.R_NAME)):
            self.assertEqual(safety.check(p, self.drives, flavor="nt"), (False, why), p)
        self.assertEqual(self.calls, [])               # 디스크에 묻지도 않는다

    def test_no_disk_access_without_tilde(self):
        self.calls.clear()
        self.assertEqual(safety.check("D:\\proj\\a.mov", self.drives, flavor="nt"), (True, ""))
        self.assertEqual(safety.expand_short_names("D:\\proj~x\\a"), "D:\\proj~x\\a")
        self.assertEqual(self.calls, [])

    def test_unc_and_prefixed(self):
        self.assertEqual(safety.expand_short_names("\\\\nas\\share\\PROJEC~1\\a"),
                         "\\\\nas\\share\\Project Archive\\a")
        self.assertEqual(safety.expand_short_names("\\\\?\\UNC\\nas\\share\\PROJEC~1"),
                         "\\\\?\\UNC\\nas\\share\\Project Archive")
        self.assertEqual(safety.expand_short_names("\\\\?\\C:\\PROGRA~1\\x"),
                         "\\\\?\\C:\\Program Files\\x")
        self.assertEqual(safety.check("\\\\nas\\share\\PROJEC~1\\a", self.drives, flavor="nt"),
                         (True, ""))

    def test_roots_and_extra_protected_with_short_names(self):
        # 가짜 드라이브 루트·보호 폴더에 짧은 이름이 있어도 알아본다(예전엔 조용히 건너뛰었다)
        tmp = "C:\\Users\\RUNNER~1\\AppData\\Local\\Temp\\db-x"
        drives = [{"letter": "D", "root": tmp}]
        self.assertEqual(safety.check(tmp + "\\a", drives, flavor="nt"), (True, ""))
        long_ = safety.expand_short_names(tmp)
        self.assertEqual(safety.check(long_ + "\\a", drives, flavor="nt"), (True, ""))
        safety.set_extra_protected([tmp + "\\a"])
        try:
            self.assertEqual(safety.check(long_ + "\\a\\b", drives, flavor="nt"), (False, safety.R_SELF))
        finally:
            safety.set_extra_protected([])

    def test_off_windows_is_noop(self):
        safety._long_name = None
        self.assertEqual(safety.expand_short_names("C:\\PROGRA~1\\x"), "C:\\PROGRA~1\\x")
        self.assertEqual(safety.check("C:\\PROGRA~1\\x", self.drives, flavor="nt"), (False, safety.R_NAME))


@unittest.skipUnless(os.name == "nt", "Windows 전용 — 실제 GetLongPathNameW")
class ShortNamesOnWindows(unittest.TestCase):
    def test_real_temp_dir(self):
        import ctypes
        import tempfile
        from ctypes import wintypes
        d = tempfile.mkdtemp(prefix="db short name ")
        try:
            gs = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
            gs.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            gs.restype = wintypes.DWORD
            buf = ctypes.create_unicode_buffer(1024)
            n = gs(d, buf, 1024)
            short = buf.value if 0 < n < 1024 else d
            long_ = os.path.realpath(d)
            if "~" not in short:
                self.skipTest("이 볼륨은 8.3 짧은 이름을 만들지 않아요")
            self.assertEqual(os.path.normcase(safety.expand_short_names(short)), os.path.normcase(long_))
            drives = [{"letter": "T", "root": os.path.dirname(long_)}]
            self.assertEqual(safety.check(os.path.join(short, "x.mov"), drives), (True, ""))
            self.assertEqual(os.path.normcase(safety.normalize(os.path.join(short, "x.mov"))),
                             os.path.normcase(os.path.join(long_, "x.mov")))
        finally:
            os.rmdir(d)


if __name__ == "__main__":
    unittest.main()

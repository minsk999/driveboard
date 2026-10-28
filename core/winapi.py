"""Windows 전용 호출(ctypes)과 가짜 드라이브(fake) 백엔드 — 같은 함수 이름을 둘 다 제공한다.

모든 Windows API 는 이 파일에만 둔다. `set_fake_root()` 로 가짜 루트가 잡히면 Windows 에서도
가짜 백엔드를 쓰고, Windows 가 아니면 가짜 루트가 없어도 가짜 백엔드다(드라이브 0개).

가짜 드라이브(SPEC §12, SPEC-2 A.1): 가짜 루트 아래 대문자 한 글자 이름의 폴더 하나 = 드라이브 하나.
`_drive.json` = {"label", "kind", "total", "system_bytes", "external", "has_bin", "network", "unc"}
(새 필드는 모두 선택). free = total - (실제 파일 크기 합, `_drive.json` 제외·`.fakebin` 포함) - system_bytes.
가짜 네트워크 위치 = 이름이 "N-" 로 시작하는 폴더(`network:true`, `unc:"\\\\ORICO\\영상백업"`).
등록 위치 목록(network_locations)이 주어지면 그 UNC 와 맞는 N-* 폴더만 나오고, 없으면(None) 전부 나온다(개발 편의).

드라이브 키(필드 이름은 "letter"): 문자 드라이브 "C"… / 등록한 네트워크 위치 "N-" + sha1(소문자 UNC 루트) 앞 6자.
드라이브 dict: letter, root, label, serial, fs, kind('ssd'|'hdd'|'usb'|'network'|'unknown'), total, free,
external, has_bin, scan, connected, unc.
"""
from __future__ import annotations

import csv
import ctypes
import errno
import hashlib
import io
import json
import ntpath
import os
import re
import shutil
import subprocess
import threading
import time
import zlib

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000

# SHFileOperationW
FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_NO_CONNECTED_ELEMENTS = 0x2000   # report.htm 을 지울 때 report_files 폴더까지 같이 지우지 않게
FOF_WANTNUKEWARNING = 0x4000         # 필수: 없으면 휴지통보다 큰 파일이 경고 없이 영구 삭제된다
RECYCLE_FLAGS = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING
                 | FOF_SILENT | FOF_NOERRORUI | FOF_NO_CONNECTED_ELEMENTS)

# SHEmptyRecycleBinW
SHERB_NOCONFIRMATION = 0x1
SHERB_NOPROGRESSUI = 0x2
SHERB_NOSOUND = 0x4
EMPTY_FLAGS = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
StorageDeviceProperty = 0
StorageDeviceSeekPenaltyProperty = 7
PropertyStandardQuery = 0
BusType1394 = 4
BusTypeUsb = 7
BusTypeSd = 12
BusTypeMmc = 13
EXTERNAL_BUS = frozenset({BusType1394, BusTypeUsb, BusTypeSd, BusTypeMmc})
# NetShareEnum / WNetGetConnectionW
STYPE_DISKTREE = 0
STYPE_MASK = 0xFF
STYPE_SPECIAL = 0x80000000
MAX_PREFERRED_LENGTH = 0xFFFFFFFF
ERROR_MORE_DATA = 234
ERROR_CONNECTION_UNAVAIL = 1201
NET_TIMEOUT = 3.0                # 죽은 NAS 가 앱을 막지 않게: 위치 확인은 이 시간 안에 끝나야 한다
SHARE_TIMEOUT = 8.0              # 공유 목록 조회 상한
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
SEM_FAILCRITICALERRORS = 0x0001
SEM_NOOPENFILEERRORBOX = 0x8000
MAX_PATH = 260
BATCH = 500                      # SHFileOperationW 한 번에 넘기는 경로 수 상한
LONG_PATH_AT = 240               # 이 길이를 넘으면 \\?\ 접두사를 붙인다

NO_BIN_MSG = "이 위치에는 휴지통이 없어요. 「삭제(복구 불가)」로 지워야 해요."

ADOBE_PROCS = {
    "afterfx.exe": "After Effects",
    "adobe premiere pro.exe": "Premiere Pro",
    "adobe media encoder.exe": "Media Encoder",
}

FAKE_META = "_drive.json"
FAKE_BIN = ".fakebin"
FAKE_NET_PREFIX = "N-"
KINDS = ("ssd", "hdd", "usb", "network", "unknown")
_KIND_ALIAS = {"removable": "usb", "nas": "network", "remote": "network"}
BIN_DIR = "$RECYCLE.BIN"

_lock = threading.RLock()
_fake_root: str | None = None
_usage_cache: dict = {}          # letter -> (monotonic, used_bytes)
_USAGE_TTL = 10.0                # 외부 변경 대비 안전망. 작업 뒤엔 invalidate_usage 로 즉시 무효화


# ─────────────────────────────── 구조체 (레이아웃은 Linux 에서도 검사 가능) ──────────────────

class STORAGE_PROPERTY_QUERY(ctypes.Structure):
    _fields_ = [("PropertyId", ctypes.c_uint32),          # STORAGE_PROPERTY_ID (enum)
                ("QueryType", ctypes.c_uint32),           # STORAGE_QUERY_TYPE (enum)
                ("AdditionalParameters", ctypes.c_ubyte * 1)]


class DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32),
                ("Size", ctypes.c_uint32),
                ("IncursSeekPenalty", ctypes.c_ubyte)]    # BOOLEAN


class STORAGE_DEVICE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [("Version", ctypes.c_uint32),
                ("Size", ctypes.c_uint32),
                ("DeviceType", ctypes.c_ubyte),
                ("DeviceTypeModifier", ctypes.c_ubyte),
                ("RemovableMedia", ctypes.c_ubyte),       # BOOLEAN
                ("CommandQueueing", ctypes.c_ubyte),      # BOOLEAN
                ("VendorIdOffset", ctypes.c_uint32),
                ("ProductIdOffset", ctypes.c_uint32),
                ("ProductRevisionOffset", ctypes.c_uint32),
                ("SerialNumberOffset", ctypes.c_uint32),
                ("BusType", ctypes.c_uint32),             # STORAGE_BUS_TYPE (enum)
                ("RawPropertiesLength", ctypes.c_uint32),
                ("RawDeviceProperties", ctypes.c_ubyte * 1)]


class SHARE_INFO_1(ctypes.Structure):
    _fields_ = [("shi1_netname", ctypes.c_wchar_p),       # LMSTR
                ("shi1_type", ctypes.c_uint32),           # DWORD
                ("shi1_remark", ctypes.c_wchar_p)]        # LMSTR


def _make_structs(is64: bool | None = None):
    """(SHFILEOPSTRUCTW, SHQUERYRBINFO) 클래스를 만든다.

    shellapi.h 는 32비트에서만 1바이트 패킹(pshpack1), 64비트는 자연 정렬이다.
    is64 가 현재 프로세스와 다르면 포인터 대신 같은 크기 정수를 넣어 레이아웃만 흉내 낸다(테스트용).
    """
    native64 = ctypes.sizeof(ctypes.c_void_p) == 8
    if is64 is None:
        is64 = native64
    if is64 == native64:
        ptr = ctypes.c_void_p
    else:
        ptr = ctypes.c_uint64 if is64 else ctypes.c_uint32
    extra = {} if is64 else {"_pack_": 1, "_layout_": "ms"}
    shf = type("SHFILEOPSTRUCTW", (ctypes.Structure,), dict(extra, _fields_=[
        ("hwnd", ptr),                       # HWND
        ("wFunc", ctypes.c_uint32),          # UINT
        ("pFrom", ptr),                      # PCZZWSTR (이중 NUL 목록 버퍼 주소)
        ("pTo", ptr),                        # PCZZWSTR
        ("fFlags", ctypes.c_uint16),         # FILEOP_FLAGS = WORD
        ("fAnyOperationsAborted", ctypes.c_int32),   # BOOL
        ("hNameMappings", ptr),              # LPVOID
        ("lpszProgressTitle", ptr),          # PCWSTR
    ]))
    rb = type("SHQUERYRBINFO", (ctypes.Structure,), dict(extra, _fields_=[
        ("cbSize", ctypes.c_uint32),         # DWORD
        ("i64Size", ctypes.c_int64),
        ("i64NumItems", ctypes.c_int64),
    ]))
    return shf, rb


SHFILEOPSTRUCTW, SHQUERYRBINFO = _make_structs()


# ─────────────────────────────── Windows 바인딩 (지연 초기화) ──────────────────────────────

class _Win:
    pass


_W = None


def _win():
    global _W
    if _W is not None:
        return _W
    from ctypes import wintypes as wt
    w = _Win()
    w.wt = wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sh32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)

    k32.GetLogicalDriveStringsW.argtypes = [wt.DWORD, wt.LPWSTR]
    k32.GetLogicalDriveStringsW.restype = wt.DWORD
    k32.GetDriveTypeW.argtypes = [wt.LPCWSTR]
    k32.GetDriveTypeW.restype = wt.UINT
    k32.GetVolumeInformationW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD, wt.LPDWORD,
                                          wt.LPDWORD, wt.LPDWORD, wt.LPWSTR, wt.DWORD]
    k32.GetVolumeInformationW.restype = wt.BOOL
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID,
                                wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.CreateFileW.restype = wt.HANDLE
    k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, wt.LPVOID, wt.DWORD,
                                    wt.LPVOID, wt.DWORD, wt.LPDWORD, wt.LPVOID]
    k32.DeviceIoControl.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.CloseHandle.restype = wt.BOOL
    k32.QueryDosDeviceW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
    k32.QueryDosDeviceW.restype = wt.DWORD
    k32.GetVolumeNameForVolumeMountPointW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
    k32.GetVolumeNameForVolumeMountPointW.restype = wt.BOOL
    pull = ctypes.POINTER(ctypes.c_ulonglong)                 # PULARGE_INTEGER
    k32.GetDiskFreeSpaceExW.argtypes = [wt.LPCWSTR, pull, pull, pull]
    k32.GetDiskFreeSpaceExW.restype = wt.BOOL
    try:
        k32.SetThreadErrorMode.argtypes = [wt.DWORD, wt.LPDWORD]
        k32.SetThreadErrorMode.restype = wt.BOOL
        w.has_thread_error_mode = True
    except AttributeError:
        w.has_thread_error_mode = False

    sh32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    sh32.SHFileOperationW.restype = ctypes.c_int
    sh32.SHQueryRecycleBinW.argtypes = [wt.LPCWSTR, ctypes.POINTER(SHQUERYRBINFO)]
    sh32.SHQueryRecycleBinW.restype = ctypes.c_long          # HRESULT
    sh32.SHEmptyRecycleBinW.argtypes = [wt.HWND, wt.LPCWSTR, wt.DWORD]
    sh32.SHEmptyRecycleBinW.restype = ctypes.c_long          # HRESULT

    ole32.CoInitializeEx.argtypes = [wt.LPVOID, wt.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None

    # 네트워크(없어도 나머지는 동작해야 한다)
    try:
        mpr = ctypes.WinDLL("mpr", use_last_error=True)
        mpr.WNetGetConnectionW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.LPDWORD]
        mpr.WNetGetConnectionW.restype = wt.DWORD
    except (OSError, AttributeError):
        mpr = None
    try:
        net = ctypes.WinDLL("netapi32", use_last_error=True)
        net.NetShareEnum.argtypes = [wt.LPWSTR, wt.DWORD, ctypes.POINTER(ctypes.c_void_p), wt.DWORD,
                                     wt.LPDWORD, wt.LPDWORD, wt.LPDWORD]
        net.NetShareEnum.restype = wt.DWORD                   # NET_API_STATUS
        net.NetApiBufferFree.argtypes = [ctypes.c_void_p]
        net.NetApiBufferFree.restype = wt.DWORD
    except (OSError, AttributeError):
        net = None

    w.k32, w.sh32, w.ole32, w.mpr, w.net = k32, sh32, ole32, mpr, net
    w.INVALID_HANDLE = ctypes.c_void_p(-1).value
    _W = w
    return w


class _QuietErrors:
    """「디스크를 넣으세요」 같은 시스템 오류 창을 막는다(스레드 단위)."""

    def __enter__(self):
        self.old = None
        try:
            w = _win()
            if w.has_thread_error_mode:
                old = w.wt.DWORD(0)
                if w.k32.SetThreadErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX,
                                            ctypes.byref(old)):
                    self.old = old.value
        except Exception:
            self.old = None
        return self

    def __exit__(self, *exc):
        if self.old is not None:
            try:
                w = _win()
                dummy = w.wt.DWORD(0)
                w.k32.SetThreadErrorMode(self.old, ctypes.byref(dummy))
            except Exception:
                pass
        return False


# ─────────────────────────────── 공통 ───────────────────────────────────────────

def _safety():
    from . import safety          # safety 는 core 의 다른 모듈을 import 하지 않는다(순환 없음)
    return safety


def set_fake_root(path):
    """가짜 드라이브 루트를 정한다. None 이면 해제(Windows 에선 실제 API 로 돌아간다)."""
    global _fake_root
    with _lock:
        # 8.3 짧은 이름(…\\RUNNER~1\\…)은 긴 이름으로 — 안 그러면 safety 가 루트를 못 알아본다
        _fake_root = _safety().expand_short_names(os.path.abspath(path)) if path else None
        _usage_cache.clear()


def get_fake_root():
    return _fake_root


def is_fake() -> bool:
    return _fake_root is not None or not IS_WIN


def invalidate_usage(path_or_letter=None):
    """가짜 드라이브 사용량 캐시를 비운다. 인자 없으면 전부."""
    with _lock:
        if not path_or_letter:
            _usage_cache.clear()
            return
        s = str(path_or_letter)
        k = norm_key(s)
        if k:
            _usage_cache.pop(k, None)
            return
        hit = _fake_drive_of(s)
        if hit:
            _usage_cache.pop(hit[0], None)
        else:
            _usage_cache.clear()


def _long_path_nt(p: str) -> str:
    """Windows 확장 길이 접두사(\\\\?\\)를 붙인다 — 순수 문자열 함수(테스트 가능)."""
    if not isinstance(p, str) or len(p) <= LONG_PATH_AT:
        return p
    if p.startswith("\\\\?\\") or p.startswith("\\\\.\\"):
        return p
    q = ntpath.normpath(p)          # \\?\ 아래에선 . 과 .. 를 풀어 주지 않으므로 먼저 정규화
    if q.startswith("\\\\"):
        return "\\\\?\\UNC\\" + q[2:]
    if re.match(r"^[A-Za-z]:\\", q):
        return "\\\\?\\" + q
    return p                        # 상대 경로엔 붙일 수 없다


def long_path(p: str) -> str:
    """Windows 에서 경로가 240자를 넘으면 확장 길이 접두사를 붙인다. 그 밖엔 그대로."""
    if not IS_WIN:
        return p
    return _long_path_nt(p)


_HOST_BAD = re.compile(r'[\\/<>"|?*\s\x00-\x1f]')


def normalize_unc(p):
    """'\\\\srv\\share\\a\\' · '//srv/share' · '\\\\?\\UNC\\srv\\share' → '\\\\srv\\share\\a'(끝 \\ 없음). 틀리면 None."""
    if not isinstance(p, str):
        return None
    s = p.strip().replace("/", "\\")
    if s[:8].upper() == "\\\\?\\UNC\\":
        s = "\\\\" + s[8:]
    if not s.startswith("\\\\") or s.startswith("\\\\\\"):
        return None
    if s[2:].split("\\", 1)[0] in (".", "?"):      # \\.\pipe · \\?\C: 같은 장치 경로 (. 을 거르기 전에 본다)
        return None
    parts = [c for c in s[2:].split("\\") if c not in ("", ".")]
    if len(parts) < 2:
        return None
    server, share = parts[0], parts[1]
    if server in ("?", ".") or _HOST_BAD.search(server) or ":" in server:
        return None
    for c in parts[1:]:
        if c == ".." or any(ch in '<>"|?*:' or ord(ch) < 32 for ch in c):
            return None
    return "\\\\" + "\\".join(parts)


def unc_root(p):
    """드라이브 루트처럼 쓸 UNC('\\\\srv\\share\\'). 틀리면 None."""
    u = normalize_unc(p)
    return u + "\\" if u else None


def unc_host_share(p):
    """('srv', 'share') 또는 None."""
    u = normalize_unc(p)
    if not u:
        return None
    parts = u[2:].split("\\")
    return parts[0], parts[1]


def network_key(p):
    """등록한 네트워크 위치의 드라이브 키: 'N-' + sha1(소문자 UNC 루트) 앞 6자. 틀리면 None."""
    u = normalize_unc(p)
    if not u:
        return None
    return FAKE_NET_PREFIX + hashlib.sha1(u.lower().encode("utf-8")).hexdigest()[:6]


def _unc_serial(u):
    return hashlib.sha1(("unc:" + u.lower()).encode("utf-8")).hexdigest()[:8].upper()


def normalize_host(host):
    """'\\\\ORICO' · 'ORICO' · '192.168.0.10' · '\\\\ORICO\\share' → 'ORICO' 꼴의 이름. 틀리면 None."""
    if not isinstance(host, str):
        return None
    s = host.strip().replace("/", "\\").lstrip("\\")
    s = s.split("\\", 1)[0]
    if not s or len(s) > 255 or s in (".", "?") or _HOST_BAD.search(s):
        return None
    return s


def norm_key(k):
    """드라이브 키 비교용 정규형: 'd'·'D:'·'D:\\' → 'D', 'n-3FA9C1' → 'N-3fa9c1'. 그 밖은 ''."""
    if not isinstance(k, str):
        return ""
    s = k.strip()
    if len(s) > 2 and s[:2].upper() == FAKE_NET_PREFIX:
        if any(c in s for c in "\\/:\0"):
            return ""
        return FAKE_NET_PREFIX + s[2:].lower()
    m = re.match(r"^([A-Za-z])(?::[\\/]?)?$", s)
    return m.group(1).upper() if m else ""


def default_scan(kind, external):
    """스캔 대상 기본값: 내장 고정 드라이브만."""
    return kind not in ("usb", "network") and not external


_probes: dict = {}               # key -> {ev, val, exc}


def _run_with_timeout(key, fn, timeout):
    """fn() 을 데몬 스레드에서 돌려 timeout 초까지만 기다린다 → (끝났나, 값, 예외).

    같은 key 의 확인이 아직 돌고 있으면(죽은 NAS) 새로 띄우지 않고 그것을 기다린다 — 스레드가 쌓이지 않는다."""
    with _lock:
        box = _probes.get(key)
        if box is None:
            box = {"ev": threading.Event(), "val": None, "exc": None}
            _probes[key] = box

            def runner(box=box):
                try:
                    box["val"] = fn()
                except BaseException as e:      # noqa: BLE001 — 결과로 돌려준다
                    box["exc"] = e
                finally:
                    with _lock:
                        if _probes.get(key) is box:
                            _probes.pop(key, None)
                    box["ev"].set()
            threading.Thread(target=runner, name="driveboard-probe", daemon=True).start()
    if not box["ev"].wait(max(0.0, float(timeout))):
        return False, None, None
    return True, box["val"], box["exc"]


def _loc_entries(network_locations):
    """설정 network_locations → [(key, unc, label)] — 틀린 것·중복은 뺀다."""
    out, seen = [], set()
    for loc in network_locations or ():
        if isinstance(loc, str):
            path, label = loc, None
        elif isinstance(loc, dict):
            path, label = loc.get("path"), loc.get("label")
        else:
            continue
        u = normalize_unc(path)
        if not u:
            continue
        k = network_key(u)
        if k in seen:
            continue
        seen.add(k)
        out.append((k, u, label.strip() if isinstance(label, str) and label.strip() else None))
    return out


def _drive_dict(letter, root, label, serial, fs, kind, total, free, external, has_bin, connected, unc):
    return {"letter": letter, "root": root, "label": label, "serial": serial, "fs": fs, "kind": kind,
            "total": int(total), "free": int(free), "external": bool(external), "has_bin": bool(has_bin),
            "scan": default_scan(kind, external), "connected": bool(connected), "unc": unc}


def _offline_location(key, u, label):
    share = u[2:].split("\\")[1]
    return _drive_dict(key, u + "\\", label or share, _unc_serial(u), "", "network", 0, 0,
                       False, False, False, u)


# ─────────────────────────────── 가짜 백엔드 ───────────────────────────────────────

def _fake_drive_dirs():
    """[(letter, path)] — 대문자 한 글자 폴더(문자 드라이브)."""
    root = _fake_root
    out = []
    if not root:
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if len(name) == 1 and "A" <= name <= "Z":
            p = os.path.join(root, name)
            if os.path.isdir(p) and not os.path.islink(p):
                out.append((name, p))
    return out


def _fake_net_dirs():
    """[(key, path, meta)] — 'N-' 로 시작하는 폴더(가짜 네트워크 위치). key 는 unc 로 계산(없으면 폴더 이름)."""
    root = _fake_root
    out = []
    if not root:
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    seen = set()
    for name in names:
        if len(name) <= 2 or name[:2].upper() != FAKE_NET_PREFIX:
            continue
        p = os.path.join(root, name)
        if not os.path.isdir(p) or os.path.islink(p):
            continue
        m = _fake_meta(name, p)
        key = network_key(m["unc"]) if m["unc"] else norm_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((key, p, m))
    return out


def _fake_all_dirs():
    return [(k, p) for k, p in _fake_drive_dirs()] + [(k, p) for k, p, _m in _fake_net_dirs()]


def _fake_meta(letter, droot):
    meta = {}
    try:
        with open(os.path.join(droot, FAKE_META), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            meta = data
    except (OSError, ValueError):
        meta = {}

    def _int(v, default):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return default

    kind = str(meta.get("kind", "unknown")).lower()
    kind = _KIND_ALIAS.get(kind, kind)
    if kind not in KINDS:
        kind = "unknown"
    is_net_dir = len(letter) > 2 and letter[:2].upper() == FAKE_NET_PREFIX
    network = meta.get("network") is True or kind == "network" or is_net_dir
    if network:
        kind = "network"
    unc = normalize_unc(meta.get("unc")) if network else None
    ext = meta.get("external")
    external = ext if isinstance(ext, bool) else (kind == "usb")
    hb = meta.get("has_bin")
    if network:
        has_bin = False                       # SMB 공유에는 Windows 휴지통이 없다
    elif isinstance(hb, bool):
        has_bin = hb
    else:
        has_bin = kind != "usb"               # 모르면 없다고 본다(플래시 드라이브)
    try:
        delay = max(0.0, float(meta.get("delay") or 0))   # 가짜 전용: 응답이 느린(죽은) NAS 흉내
    except (TypeError, ValueError):
        delay = 0.0
    serial = meta.get("serial")
    if not serial:
        serial = _unc_serial(unc) if unc else "%08X" % zlib.crc32(("fake:" + letter).encode())
    return {
        "label": str(meta.get("label") or ("FAKE " + letter)),
        "kind": kind,
        "total": _int(meta.get("total"), 1 << 40),
        "system_bytes": _int(meta.get("system_bytes"), 0),
        "fs": str(meta.get("fs") or ("SMB" if network else "NTFS")),
        "serial": str(serial),
        "external": external,
        "has_bin": has_bin,
        "network": network,
        "unc": unc,
        "delay": delay,
    }


def _tree_size(top, skip_top=()):
    """(바이트, 파일 수) — 링크를 따라가지 않는 반복 DFS."""
    total = count = 0
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                if d == top and e.name in skip_top:
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        total += e.stat(follow_symlinks=False).st_size
                        count += 1
                except OSError:
                    pass
    return total, count


def _fake_used(letter, droot):
    now = time.monotonic()
    with _lock:
        hit = _usage_cache.get(letter)
        if hit and now - hit[0] < _USAGE_TTL:
            return hit[1]
    used, _ = _tree_size(droot, skip_top=(FAKE_META,))
    with _lock:
        _usage_cache[letter] = (time.monotonic(), used)
    return used


def _same_or_inside(path, root):
    a = os.path.normcase(os.path.abspath(path))
    r = os.path.normcase(os.path.abspath(root))
    return a == r or a.startswith(r.rstrip(os.sep) + os.sep)


def _fake_drive_of(path):
    """(key, droot) — 문자 드라이브와 N-* 위치 모두. 없으면 None."""
    if not _fake_root or not isinstance(path, str) or not path:
        return None
    best = None
    for key, droot in _fake_all_dirs():
        if _same_or_inside(path, droot):
            if best is None or len(droot) > len(best[1]):
                best = (key, droot)
    return best


def _fake_probe(key, droot, m):
    """(연결됨, total, free) — 느린 NAS 흉내(delay)도 실제와 같은 시간 제한 경로를 지난다."""
    def fn():
        if m["delay"]:
            time.sleep(m["delay"])
        if not os.path.isdir(droot):
            raise FileNotFoundError(droot)
        used = _fake_used(key, droot)
        return m["total"], max(0, m["total"] - used - m["system_bytes"])
    if not m["network"]:
        t, f = fn()
        return True, t, f
    done, val, exc = _run_with_timeout(("fake", key), fn, NET_TIMEOUT)
    if not done or exc is not None or val is None:
        return False, 0, 0
    return True, val[0], val[1]


def _fake_entry(key, droot, m, label=None):
    ok, total, free = _fake_probe(key, droot, m)
    return _drive_dict(key, droot, label or m["label"], m["serial"], m["fs"], m["kind"],
                       total, free, m["external"], m["has_bin"], ok, m["unc"])


def _fake_list_drives(network_locations=None):
    out = []
    for letter, droot in _fake_drive_dirs():
        out.append(_fake_entry(letter, droot, _fake_meta(letter, droot)))
    nets = _fake_net_dirs()
    if network_locations is None:
        for key, droot, m in nets:
            out.append(_fake_entry(key, droot, m))
        return out
    by_unc = {m["unc"].lower(): (key, droot, m) for key, droot, m in nets if m["unc"]}
    mapped = {d["unc"].lower(): d["letter"] for d in out if d.get("unc")}     # _win_list_drives 와 같은 규칙
    for key, u, label in _loc_entries(network_locations):
        hit = by_unc.get(u.lower())
        if hit is None:
            e = _offline_location(key, u, label)
        else:
            e = _fake_entry(key, hit[1], hit[2], label=label)
        if u.lower() in mapped:
            e["same_as"] = mapped[u.lower()]    # 같은 공유가 문자로도 매핑돼 있다(합계에서 두 번 세지 말 것)
        out.append(e)
    return out


def _fake_disk_usage(root):
    hit = _fake_drive_of(root)
    if not hit:
        raise FileNotFoundError("가짜 드라이브가 아니에요: %s" % root)
    letter, droot = hit
    m = _fake_meta(os.path.basename(droot), droot)
    used = _fake_used(letter, droot)
    return m["total"], max(0, m["total"] - used - m["system_bytes"])


def _fake_has_bin(droot):
    return _fake_meta(os.path.basename(droot), droot)["has_bin"]


def _fake_list_shares(host):
    h = normalize_host(host)
    if not h:
        return [], "장치 이름이나 IP 주소가 올바르지 않아요."
    out, seen = [], set()
    for _key, _p, m in _fake_net_dirs():
        hs = unc_host_share(m["unc"]) if m["unc"] else None
        if not hs or hs[0].lower() != h.lower() or hs[1].lower() in seen:
            continue
        seen.add(hs[1].lower())
        out.append({"name": hs[1], "unc": "\\\\%s\\%s" % hs})
    if not out:
        return [], "\\\\%s 에서 공유 폴더를 찾지 못했어요." % h
    out.sort(key=lambda x: x["name"].lower())
    return out, None


def _fake_location_info(u):
    for key, droot, m in _fake_net_dirs():
        if m["unc"] and m["unc"].lower() == u.lower():
            ok, total, free = _fake_probe(key, droot, m)
            return {"connected": ok, "total": total, "free": free, "label": m["label"], "fs": m["fs"],
                    "serial": m["serial"], "root": droot}
    return {"connected": False, "total": 0, "free": 0, "label": None, "fs": "", "serial": _unc_serial(u),
            "root": u + "\\"}


def _fake_recycle(path):
    if not isinstance(path, str) or not path or "\0" in path:
        return False, "경로가 올바르지 않아요."
    hit = _fake_drive_of(path)
    if not hit:
        return False, "가짜 드라이브 안의 경로가 아니에요."
    letter, droot = hit
    ap = os.path.abspath(path)
    if os.path.normcase(ap) == os.path.normcase(os.path.abspath(droot)):
        return False, "드라이브 최상위는 휴지통으로 보낼 수 없어요."
    binp = os.path.join(droot, FAKE_BIN)
    if _same_or_inside(ap, binp):
        return False, "이미 휴지통 안에 있어요."
    if os.path.normcase(ap) == os.path.normcase(os.path.join(droot, FAKE_META)):
        return False, "가짜 드라이브 설정 파일이라 보낼 수 없어요."
    if not _fake_has_bin(droot):
        return False, NO_BIN_MSG
    if not os.path.lexists(ap):
        return False, "파일을 찾을 수 없어요."
    if os.path.islink(binp) or (os.path.lexists(binp) and not os.path.isdir(binp)):
        return False, "가짜 휴지통 폴더가 올바르지 않아요."
    try:
        os.makedirs(binp, exist_ok=True)
        base = os.path.basename(ap)[:180]
        for _ in range(1000):
            dest = os.path.join(binp, "%d_%s" % (time.time_ns(), base))
            if not os.path.lexists(dest):
                break
        else:
            return False, "휴지통 이름을 만들지 못했어요."
        os.rename(ap, dest)
    except OSError as e:
        return False, _os_err_ko(e)
    finally:
        invalidate_usage(letter)
    return True, None


def _fake_bin_dir(root):
    hit = _fake_drive_of(root)
    if not hit:
        return None, None
    return hit[0], os.path.join(hit[1], FAKE_BIN)


def _fake_bin_info(root):
    letter, binp = _fake_bin_dir(root)
    if not binp or not os.path.isdir(binp) or os.path.islink(binp):
        return 0, 0
    if not _fake_has_bin(os.path.dirname(binp)):
        return 0, 0
    try:
        count = len(os.listdir(binp))
    except OSError:
        count = 0
    size, _ = _tree_size(binp)
    return size, count


def _fake_empty(root):
    letter, binp = _fake_bin_dir(root)
    if not binp:
        return False, "가짜 드라이브가 아니에요."
    if not os.path.lexists(binp) or not _fake_has_bin(os.path.dirname(binp)):
        return True, None                 # 휴지통이 없는 위치 — 비울 것도 없다(.fakebin 은 건드리지 않는다)
    if os.path.islink(binp) or not os.path.isdir(binp):
        return False, "가짜 휴지통 폴더가 올바르지 않아요."
    failed = 0
    try:
        with os.scandir(binp) as it:
            entries = list(it)
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    shutil.rmtree(e.path)
                else:
                    os.remove(e.path)
            except OSError:
                failed += 1
    except OSError as e:
        return False, _os_err_ko(e)
    finally:
        invalidate_usage(letter)
    if failed:
        return False, "휴지통의 항목 %d개를 지우지 못했어요." % failed
    return True, None


def _fake_running_adobe():
    v = os.environ.get("DRIVEBOARD_FAKE_ADOBE", "").strip()
    if not v:
        return []
    if v in ("1", "true", "yes"):
        return ["AfterFX.exe"]
    return [s.strip() for s in v.split(",") if s.strip()]


# ─────────────────────────────── Windows 백엔드 ─────────────────────────────────────

_WIN_ROOT = re.compile(r"^([A-Za-z]):[\\/]?$")
_WIN_ABS_ITEM = re.compile(r"^[A-Za-z]:\\[^\\]")


def _win_root(root):
    """'D', 'D:', 'D:\\', 'D:/' → 'D:\\'. 그 밖은 None(★None 을 API 에 넘기면 '모든 드라이브' 뜻이다)."""
    if not isinstance(root, str):
        return None
    s = root.strip()
    if len(s) == 1 and s.isalpha():
        return s.upper() + ":\\"
    m = _WIN_ROOT.match(s)
    return (m.group(1).upper() + ":\\") if m else None


def _win_open_volume(letter):
    w = _win()
    h = w.k32.CreateFileW("\\\\.\\%s:" % letter, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                          None, OPEN_EXISTING, 0, None)
    if not h or h == w.INVALID_HANDLE:
        return None
    return h


def _win_query_seek(h):
    w = _win()
    q = STORAGE_PROPERTY_QUERY()
    q.PropertyId = StorageDeviceSeekPenaltyProperty
    q.QueryType = PropertyStandardQuery
    out = DEVICE_SEEK_PENALTY_DESCRIPTOR()
    got = w.wt.DWORD(0)
    ok = w.k32.DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY,
                               ctypes.byref(q), ctypes.sizeof(q),
                               ctypes.byref(out), ctypes.sizeof(out),
                               ctypes.byref(got), None)
    if not ok or got.value < DEVICE_SEEK_PENALTY_DESCRIPTOR.IncursSeekPenalty.offset + 1:
        return None
    return bool(out.IncursSeekPenalty)


def _win_query_bus(h):
    """(BusType, RemovableMedia) 또는 (None, None). 출력은 가변 길이라 넉넉한 버퍼에 받는다."""
    w = _win()
    q = STORAGE_PROPERTY_QUERY()
    q.PropertyId = StorageDeviceProperty
    q.QueryType = PropertyStandardQuery
    buf = ctypes.create_string_buffer(1024)
    got = w.wt.DWORD(0)
    ok = w.k32.DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY,
                               ctypes.byref(q), ctypes.sizeof(q),
                               ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(got), None)
    need = STORAGE_DEVICE_DESCRIPTOR.BusType.offset + 4
    if not ok or got.value < need:
        return None, None
    desc = STORAGE_DEVICE_DESCRIPTOR.from_buffer_copy(buf.raw[:ctypes.sizeof(STORAGE_DEVICE_DESCRIPTOR)])
    return int(desc.BusType), bool(desc.RemovableMedia)


def _win_device_info(letter):
    """(seek_penalty True/False/None, bus_type|None) — 관리자 권한 없이(접근 0 으로 연다)."""
    w = _win()
    h = _win_open_volume(letter)
    if h is None:
        return None, None
    try:
        try:
            sp = _win_query_seek(h)
        except Exception:
            sp = None
        try:
            bus, _rm = _win_query_bus(h)
        except Exception:
            bus = None
        return sp, bus
    finally:
        w.k32.CloseHandle(h)


def _win_seek_penalty(letter):
    """True=HDD(탐색 지연 있음), False=SSD, None=알 수 없음."""
    return _win_device_info(letter)[0]


def is_external_bus(bus):
    return bus in EXTERNAL_BUS


def _kind_from_label(label):
    u = (label or "").upper()
    if "SSD" in u or "NVME" in u:
        return "ssd"
    if "HDD" in u:
        return "hdd"
    return "unknown"


def _win_volume_info(root):
    w = _win()
    name = ctypes.create_unicode_buffer(261)
    fsn = ctypes.create_unicode_buffer(261)
    serial, maxc, flags = w.wt.DWORD(0), w.wt.DWORD(0), w.wt.DWORD(0)
    ok = w.k32.GetVolumeInformationW(root, name, 261, ctypes.byref(serial), ctypes.byref(maxc),
                                     ctypes.byref(flags), fsn, 261)
    if not ok:
        return None
    return name.value, "%08X" % (serial.value & 0xFFFFFFFF), fsn.value


def _win_dos_target(letter):
    """QueryDosDeviceW('D:') 의 첫 대상. subst 드라이브는 '\\??\\D:\\폴더' 꼴이다."""
    w = _win()
    buf = ctypes.create_unicode_buffer(1024)
    n = w.k32.QueryDosDeviceW("%s:" % letter, buf, 1024)
    return buf.value if n else None


def _win_volume_guid(root):
    w = _win()
    buf = ctypes.create_unicode_buffer(64)
    if w.k32.GetVolumeNameForVolumeMountPointW(root, buf, 64):
        return buf.value
    return None


def _win_free_space(root):
    """(total, free) — GetDiskFreeSpaceExW(호출자 기준 남은 공간 — NAS 할당량 반영). 실패하면 OSError."""
    w = _win()
    avail, total, tfree = ctypes.c_ulonglong(0), ctypes.c_ulonglong(0), ctypes.c_ulonglong(0)
    if not w.k32.GetDiskFreeSpaceExW(root, ctypes.byref(avail), ctypes.byref(total), ctypes.byref(tfree)):
        err = ctypes.get_last_error()
        raise OSError(errno.EIO, "GetDiskFreeSpaceExW 실패(%d)" % err, root)
    return int(total.value), int(avail.value)


def _win_wnet(letter):
    """매핑된 네트워크 드라이브의 UNC(\\\\srv\\share) 또는 None."""
    w = _win()
    if w.mpr is None:
        return None
    n = w.wt.DWORD(1024)
    for _ in range(2):
        buf = ctypes.create_unicode_buffer(n.value + 1)
        rc = w.mpr.WNetGetConnectionW("%s:" % letter, buf, ctypes.byref(n))
        if rc == ERROR_MORE_DATA:
            continue
        if rc in (0, ERROR_CONNECTION_UNAVAIL) and buf.value:
            return normalize_unc(buf.value)
        return None
    return None


def _win_has_bin_dir(root):
    try:
        return os.path.isdir(root + BIN_DIR)
    except OSError:
        return False


def _win_local_has_bin(dtype, fs, root):
    """고정(내장·외장) NTFS/ReFS = 있음. 고정 FAT·exFAT 는 $RECYCLE.BIN 폴더가 이미 있을 때만. 모르면 없음.

    이동식(DRIVE_REMOVABLE — SD 카드·USB 메모리)은 폴더가 있어도 **없음**이다. 셸은 드라이브 종류로
    휴지통 대상을 정하고(고정 드라이브만) 이동식 매체의 $RECYCLE.BIN 은 보지 않는다 — 다른 리더에서
    고정 디스크로 잡혔을 때 생긴 폴더가 남아 있어도 FOF_ALLOWUNDO 는 그 매체에서 영구 삭제가 된다.
    """
    if dtype != DRIVE_FIXED:
        return False
    if (fs or "").upper() in ("NTFS", "REFS"):
        return True
    return _win_has_bin_dir(root)


def _win_net_probe(root):
    """네트워크 루트(문자 또는 UNC)의 (label, serial, fs, total, free) — 시간 제한 안에서."""
    def fn():
        with _QuietErrors():
            total, free = _win_free_space(root)       # 실패하면 OSError → 연결 안 됨
            vi = _win_volume_info(root)               # 라벨·형식은 못 얻어도 연결은 된 것
        return (vi or ("", "", "")), total, free
    done, val, exc = _run_with_timeout(("net", root.lower()), fn, NET_TIMEOUT)
    if not done or exc is not None or val is None:
        return None
    return val


def _win_list_drives(network_locations=None):
    w = _win()
    out = []
    seen_vol = set()
    with _QuietErrors():
        buf = ctypes.create_unicode_buffer(512)
        n = w.k32.GetLogicalDriveStringsW(511, buf)
        roots = [r for r in buf[:n].split("\0") if r] if 0 < n <= 511 else []
        for root in roots:
            r = _win_root(root)
            if not r:
                continue
            try:
                dtype = w.k32.GetDriveTypeW(r)
                if dtype not in (DRIVE_FIXED, DRIVE_REMOVABLE, DRIVE_REMOTE):
                    continue              # CD-ROM·RAM 디스크·알 수 없음
                if dtype == DRIVE_REMOTE:
                    out.append(_win_remote_entry(r))
                    continue
                tgt = _win_dos_target(r[0])
                if tgt and tgt.startswith("\\??\\"):
                    continue              # subst 드라이브 — 다른 드라이브의 폴더라 같은 곳을 두 번 세게 된다
                guid = _win_volume_guid(r)
                if guid:
                    if guid.lower() in seen_vol:
                        continue          # 한 볼륨에 문자가 둘 — 첫 문자만
                    seen_vol.add(guid.lower())
                vi = _win_volume_info(r)
                if vi is None:
                    continue              # 준비되지 않은 드라이브(카드 리더에 카드 없음 등)
                label, serial, fs = vi
                total, _used, free = shutil.disk_usage(r)
            except OSError:
                continue
            try:
                sp, bus = _win_device_info(r[0])
            except Exception:
                sp, bus = None, None
            if dtype == DRIVE_REMOVABLE:
                kind, external = "usb", True
            else:
                kind = "hdd" if sp else ("ssd" if sp is False else _kind_from_label(label))
                external = is_external_bus(bus)
            out.append(_drive_dict(r[0], r, label, serial, fs, kind, total, free, external,
                                   _win_local_has_bin(dtype, fs, r), True, None))
    out.sort(key=lambda d: d["letter"])
    mapped = {d["unc"].lower(): d["letter"] for d in out if d.get("unc")}
    for key, u, label in _loc_entries(network_locations):
        e = _win_location_entry(key, u, label)
        if u.lower() in mapped:
            e["same_as"] = mapped[u.lower()]    # 같은 공유가 문자로도 매핑돼 있다(합계에서 두 번 세지 말 것)
        out.append(e)
    return out


def _win_remote_entry(r):
    """매핑된 네트워크 드라이브(Z:) — 끊겨 있어도 목록에 남긴다(connected:false)."""
    try:
        unc = _win_wnet(r[0])
    except Exception:
        unc = None
    share = unc[2:].split("\\")[1] if unc else None
    val = _win_net_probe(r)
    if val is None or val[0] is None:
        serial = _unc_serial(unc) if unc else "%08X" % zlib.crc32(("net:" + r).encode())
        return _drive_dict(r[0], r, share or (r[0] + ":"), serial, "", "network", 0, 0,
                           False, False, False, unc)
    (vlabel, _vserial, fs), total, free = val
    serial = _unc_serial(unc) if unc else _vserial
    return _drive_dict(r[0], r, share or vlabel or (r[0] + ":"), serial, fs, "network", total, free,
                       False, False, True, unc)


def _win_location_entry(key, u, label):
    root = u + "\\"
    val = _win_net_probe(root)
    if val is None or val[0] is None:
        return _offline_location(key, u, label)
    (_vlabel, _vserial, fs), total, free = val
    share = u[2:].split("\\")[1]
    return _drive_dict(key, root, label or share, _unc_serial(u), fs, "network", total, free,
                       False, False, True, u)


_NET_ERR = {
    5: "접근이 거부됐어요. 탐색기에서 그 장치에 한 번 로그인한 뒤 다시 시도해 주세요.",
    53: "장치를 찾을 수 없어요. 이름이나 IP 주소를 확인해 주세요.",
    67: "그런 이름의 장치나 공유를 찾을 수 없어요.",
    1219: "같은 장치에 다른 계정으로 이미 연결돼 있어요.",
    1222: "네트워크에 연결돼 있지 않아요.",
    1326: "사용자 이름이나 암호가 맞지 않아요.",
    1231: "네트워크에서 장치에 닿을 수 없어요.",
    1232: "네트워크에서 장치에 닿을 수 없어요.",
    2114: "장치의 공유 서비스가 꺼져 있어요.",
}


def _net_err_ko(rc):
    return _NET_ERR.get(rc, "공유 목록을 가져오지 못했어요 (코드 %d)." % rc)


def _win_list_shares(server):
    """NetShareEnum(레벨 1) → ([{name, unc}], err). 디스크 공유(STYPE_DISKTREE)만, 관리 공유($)는 뺀다."""
    w = _win()
    if w.net is None:
        return [], "이 PC 에서 공유 목록 기능을 쓸 수 없어요."
    srv = "\\\\" + server
    out, seen = [], set()
    resume = w.wt.DWORD(0)
    for _ in range(64):
        bufp = ctypes.c_void_p()
        read, total = w.wt.DWORD(0), w.wt.DWORD(0)
        rc = w.net.NetShareEnum(srv, 1, ctypes.byref(bufp), MAX_PREFERRED_LENGTH,
                                ctypes.byref(read), ctypes.byref(total), ctypes.byref(resume))
        try:
            if rc not in (0, ERROR_MORE_DATA):
                return [], _net_err_ko(rc)
            if bufp.value and read.value:
                arr = ctypes.cast(bufp, ctypes.POINTER(SHARE_INFO_1 * read.value)).contents
                for si in arr:
                    name = si.shi1_netname or ""
                    t = int(si.shi1_type) & 0xFFFFFFFF
                    if (t & STYPE_MASK) != STYPE_DISKTREE or (t & STYPE_SPECIAL) or not name or name.endswith("$"):
                        continue
                    u = normalize_unc("%s\\%s" % (srv, name))
                    if u and name.lower() not in seen:
                        seen.add(name.lower())
                        out.append({"name": name, "unc": u})
        finally:
            if bufp.value:
                w.net.NetApiBufferFree(bufp)
        if rc == 0:
            break
    out.sort(key=lambda x: x["name"].lower())
    return out, None


_SH_ERR = {
    2: "파일을 찾을 수 없어요.",
    3: "경로를 찾을 수 없어요.",
    5: "접근이 거부됐어요.",
    32: "다른 프로그램이 사용 중이에요.",
    33: "다른 프로그램이 파일 일부를 잠갔어요.",
    0x71: "원본과 대상이 같아요.",
    0x74: "드라이브 최상위는 다룰 수 없어요.",
    0x75: "취소됐어요.",
    0x78: "접근이 거부됐어요.",
    0x79: "경로가 너무 깊어요.",
    0x7C: "경로가 올바르지 않아요.",
    0x81: "이름이 너무 길어요.",
    0x402: "알 수 없는 오류가 났어요.",
    0x10000: "대상 쪽에서 오류가 났어요.",
}


def _sh_err_ko(code):
    if code in _SH_ERR:
        return _SH_ERR[code]
    return "휴지통으로 보내지 못했어요 (코드 0x%X)." % (code & 0xFFFFFFFF)


def _win_recycle_check(p):
    """(정규화 경로, None) 또는 (None, 이유). SHFileOperationW 에 넘겨도 안전한지 미리 본다."""
    if not isinstance(p, str) or not p:
        return None, "경로가 비어 있어요."
    if any(c in p for c in '\0*?"<>|') or any(ord(c) < 32 for c in p):
        return None, "경로에 쓸 수 없는 문자가 있어요."   # * ? 는 셸이 와일드카드로 펼친다
    q = ntpath.normpath(p)
    if not _WIN_ABS_ITEM.match(q) or ":" in q[2:]:
        return None, "드라이브 문자로 시작하는 전체 경로가 아니에요."
    if len(q) >= MAX_PATH:
        return None, "경로가 너무 길어(260자 이상) 휴지통으로 보낼 수 없어요."
    if not os.path.lexists(q):
        return None, "파일을 찾을 수 없어요."
    return q, None


def _shfileop_delete(paths):
    """FO_DELETE 한 번. (반환 코드, 사용자가 취소했는가)."""
    w = _win()
    for p in paths:
        if "\0" in p:
            raise ValueError("NUL in path")
    buf = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")   # 이중 NUL 종료(+여유 NUL)
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.addressof(buf)
    op.pTo = None
    op.fFlags = RECYCLE_FLAGS
    op.fAnyOperationsAborted = 0
    op.hNameMappings = None
    op.lpszProgressTitle = None
    hr = w.ole32.CoInitializeEx(None, 0x2 | 0x4)    # APARTMENTTHREADED | DISABLE_OLE1DDE
    try:
        code = w.sh32.SHFileOperationW(ctypes.byref(op))
    finally:
        if hr in (0, 1):                             # S_OK, S_FALSE
            w.ole32.CoUninitialize()
    del buf
    return int(code), bool(op.fAnyOperationsAborted)


def _win_root_has_bin(r):
    """그 드라이브에 휴지통이 있는가 — 네트워크는 없음, 모르면 없음(영구 삭제가 되지 않게)."""
    w = _win()
    try:
        dtype = w.k32.GetDriveTypeW(r)
        if dtype == DRIVE_REMOTE:
            return False
        vi = _win_volume_info(r)
        return _win_local_has_bin(dtype, vi[2] if vi else "", r)
    except Exception:
        return False


def _win_recycle_many(paths):
    order = list(paths)
    key_of = {}          # 입력 경로 -> 결과 키
    res = {}             # 결과 키 -> (ok, err)
    valid = []           # (key, 정규화 경로) — 대소문자만 다른 중복은 한 번만 보낸다
    vkeys = set()
    bins = {}            # 'D:\\' -> 휴지통 있음?
    for p in order:
        if p in key_of:
            continue
        q, err = _win_recycle_check(p)
        if not err:
            r = q[:3].upper()
            if r not in bins:
                with _QuietErrors():
                    bins[r] = _win_root_has_bin(r)
            if not bins[r]:
                err = NO_BIN_MSG          # 이중 안전: 휴지통 없는 곳에 FOF_ALLOWUNDO 는 영구 삭제가 된다
        if err:
            key_of[p] = ("err", p)
            res[("err", p)] = (False, err)
            continue
        k = q.casefold()
        key_of[p] = k
        if k not in vkeys:
            vkeys.add(k)
            valid.append((k, q))
    stop = False
    for i in range(0, len(valid), BATCH):
        batch = valid[i:i + BATCH]
        if stop:
            for k, _q in batch:
                res[k] = (False, "취소됐어요.")
            continue
        code, aborted = _shfileop_delete([q for _k, q in batch])
        left = []
        for k, q in batch:
            if os.path.lexists(q):
                left.append((k, q))
            else:
                res[k] = (True, None)
        if not left:
            continue
        if aborted:
            stop = True
            for k, _q in left:
                res[k] = (False, "취소됐어요.")
            continue
        if len(batch) == 1:
            res[left[0][0]] = (False, _sh_err_ko(code) if code else "휴지통으로 보내지 못했어요.")
            continue
        # 묶음 하나가 실패하면 나머지가 처리되지 않았을 수 있다 → 남은 것만 하나씩 다시
        for k, q in left:
            if stop:
                res[k] = (False, "취소됐어요.")
                continue
            c2, ab2 = _shfileop_delete([q])
            if not os.path.lexists(q):
                res[k] = (True, None)
            elif ab2:
                stop = True
                res[k] = (False, "취소됐어요.")
            else:
                res[k] = (False, _sh_err_ko(c2) if c2 else "휴지통으로 보내지 못했어요.")
    return [(p,) + tuple(res[key_of[p]]) for p in order]


def _win_bin_info(root):
    r = _win_root(root)
    if not r:
        return 0, 0
    w = _win()
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    with _QuietErrors():
        hr = w.sh32.SHQueryRecycleBinW(r, ctypes.byref(info))
    if hr != 0:
        return 0, 0
    return max(0, int(info.i64Size)), max(0, int(info.i64NumItems))


def _win_empty(root):
    r = _win_root(root)
    if not r:
        return False, "드라이브 경로가 올바르지 않아요."
    size, count = _win_bin_info(r)
    if size == 0 and count == 0:
        return True, None
    w = _win()
    hr = w.ole32.CoInitializeEx(None, 0x2 | 0x4)
    try:
        res = w.sh32.SHEmptyRecycleBinW(None, r, EMPTY_FLAGS)
    finally:
        if hr in (0, 1):
            w.ole32.CoUninitialize()
    if res == 0:
        return True, None
    s2, c2 = _win_bin_info(r)
    if s2 == 0 and c2 == 0:             # 이미 비어 있으면 E_UNEXPECTED 를 돌려주는 버전이 있다
        return True, None
    return False, "휴지통을 비우지 못했어요 (코드 0x%08X)." % (res & 0xFFFFFFFF)


def _decode_console(b):
    for enc in ("oem", "mbcs", "utf-8"):
        try:
            return b.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return b.decode("utf-8", "replace")


def _win_running_adobe():
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for row in csv.reader(io.StringIO(_decode_console(r.stdout or b""))):
        if not row:
            continue
        name = row[0].strip()
        if name.lower() in ADOBE_PROCS and name not in found:
            found.append(name)
    return found


def _win_reveal(path):
    if not isinstance(path, str) or not path or '"' in path or "\0" in path:
        return False
    p = ntpath.normpath(path)
    try:
        if os.path.lexists(long_path(p)):
            subprocess.Popen('explorer /select,"%s"' % p, creationflags=CREATE_NO_WINDOW)
        else:
            parent = ntpath.dirname(p)
            if not os.path.isdir(long_path(parent)):
                return False
            subprocess.Popen('explorer "%s"' % parent, creationflags=CREATE_NO_WINDOW)
    except OSError:
        return False
    return True


# ─────────────────────────────── 공개 함수 ─────────────────────────────────────────

def _os_err_ko(e):
    if isinstance(e, FileNotFoundError):
        return "파일을 찾을 수 없어요."
    if isinstance(e, PermissionError):
        return "접근이 거부됐거나 다른 프로그램이 사용 중이에요."
    return "오류가 났어요: %s" % (getattr(e, "strerror", None) or e)


def list_drives(network_locations=None, drive_scan=None):
    """문자 드라이브(내장·외장 고정, 이동식, 매핑된 네트워크) + 등록한 네트워크 위치.

    network_locations: settings 의 [{path, label}] (None = Windows 는 없음, 가짜는 N-* 전부).
    drive_scan: {key: bool} — 사용자가 고른 스캔 대상(없으면 내장 고정만 true).
    """
    if is_fake():
        out = _fake_list_drives(network_locations)
    else:
        out = _win_list_drives(network_locations)
    if isinstance(drive_scan, dict):
        pref = {norm_key(k): v for k, v in drive_scan.items() if isinstance(v, bool) and norm_key(k)}
        for d in out:
            v = pref.get(norm_key(d["letter"]))
            if v is not None:
                d["scan"] = v
    return out


def _is_remote_path(p):
    if not isinstance(p, str):
        return False
    if p.startswith("\\\\") or p.startswith("//"):
        return True
    r = _win_root(p[:2]) if len(p) >= 2 else None
    if not r:
        return False
    try:
        return _win().k32.GetDriveTypeW(r) == DRIVE_REMOTE
    except Exception:
        return False


def disk_usage(root):
    """(total, free). 경로는 드라이브 루트 또는 그 안의 폴더. 네트워크는 시간 제한(넘으면 OSError)."""
    if is_fake():
        return _fake_disk_usage(root)
    if _is_remote_path(root):
        p = root if root.endswith("\\") else root + "\\"
        done, val, exc = _run_with_timeout(("du", p.lower()), lambda: _win_free_space(p), NET_TIMEOUT)
        if not done:
            raise OSError(errno.ETIMEDOUT, "장치가 응답하지 않아요.", root)
        if exc is not None:
            raise exc if isinstance(exc, OSError) else OSError(errno.EIO, str(exc), root)
        return val
    du = shutil.disk_usage(root)
    return int(du.total), int(du.free)


def path_alive(path, timeout=NET_TIMEOUT):
    """폴더가 지금 닿는가 — 죽은 NAS 에서도 timeout 초 안에 False 로 돌아온다."""
    if not isinstance(path, str) or not path:
        return False
    lp = long_path(path)
    done, val, exc = _run_with_timeout(("alive", path), lambda: os.path.isdir(lp), timeout)
    return bool(done and exc is None and val)


def list_network_shares(host):
    """(shares[{name, unc}], err|None) — 장치 이름만으로 디스크 공유 목록(NetShareEnum). 실패면 ([], 이유)."""
    h = normalize_host(host)
    if not h:
        return [], "장치 이름이나 IP 주소가 올바르지 않아요."
    if is_fake():
        return _fake_list_shares(h)
    done, val, exc = _run_with_timeout(("shares", h.lower()), lambda: _win_list_shares(h), SHARE_TIMEOUT)
    if not done:
        return [], "\\\\%s 장치가 응답하지 않아요." % h
    if exc is not None or not isinstance(val, tuple):
        return [], "공유 목록을 가져오지 못했어요: %s" % (exc or "알 수 없는 오류")
    return val


def location_info(unc):
    """등록(할) 네트워크 위치 확인 → {key, unc, root, connected, total, free, label, fs, serial}.
    죽은 NAS 라도 약 NET_TIMEOUT 초 안에 connected:false 로 돌아온다. UNC 가 틀리면 None."""
    u = normalize_unc(unc)
    if not u:
        return None
    key = network_key(u)
    if is_fake():
        info = _fake_location_info(u)
    else:
        val = _win_net_probe(u + "\\")
        if val is None or val[0] is None:
            info = {"connected": False, "total": 0, "free": 0, "label": None, "fs": "", "serial": _unc_serial(u)}
        else:
            (vlabel, _vs, fs), total, free = val
            info = {"connected": True, "total": total, "free": free, "label": vlabel or None, "fs": fs,
                    "serial": _unc_serial(u)}
        info["root"] = u + "\\"
    share = u[2:].split("\\")[1]
    info.update(key=key, unc=u, share=share)
    if not info.get("label"):
        info["label"] = share
    return info


def recycle_many(paths):
    """[(path, ok, err)] — 입력 순서 그대로. 휴지통으로만 보낸다(영구 삭제는 경고가 뜬다)."""
    paths = list(paths)
    if not paths:
        return []
    if is_fake():
        if _fake_root is None:
            return [(p, False, "가짜 드라이브가 설정되지 않았어요.") for p in paths]
        return [(p,) + _fake_recycle(p) for p in paths]
    return _win_recycle_many(paths)


def recycle(path):
    """(ok, err)."""
    _p, ok, err = recycle_many([path])[0]
    return ok, err


def recycle_bin_info(root):
    """(bytes, count) — 그 드라이브 휴지통."""
    if is_fake():
        return _fake_bin_info(root)
    return _win_bin_info(root)


def empty_recycle_bin(root):
    """(ok, err) — 그 드라이브 휴지통만 영구히 비운다."""
    if is_fake():
        return _fake_empty(root)
    return _win_empty(root)


def reveal(path):
    """탐색기에서 선택해 보여 준다. 가짜 백엔드는 아무것도 하지 않는다."""
    if is_fake():
        return True
    return _win_reveal(path)


def running_adobe():
    """실행 중인 Adobe 앱 프로세스 이름 목록(예: ['AfterFX.exe'])."""
    if is_fake():
        return _fake_running_adobe()
    return _win_running_adobe()


def adobe_display_names(procs):
    return [ADOBE_PROCS.get(p.lower(), p) for p in procs]


_env_fake = os.environ.get("DRIVEBOARD_FAKE")
if _env_fake:
    set_fake_root(_env_fake)

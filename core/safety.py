"""보호 경로 판정 (SPEC §9). 디스크를 읽지 않는 순수 문자열 판정이다.

check(path, drives) -> (ok, reason_ko)
  drives: [{letter, root, kind, external, ...}] 또는 루트 문자열 목록.
  Windows 루트(`D:\\`), UNC 루트(`\\\\srv\\share\\`)와 가짜 루트(실제 디렉터리)를 받는다.
  가짜 루트는 그 디렉터리를 드라이브 최상위로 보고 같은 상대 규칙을 대소문자 무시로 적용한다.
  flavor: 'nt' | 'posix' | None(자동: Windows 면 nt, 아니면 경로 모양으로 판단).

SPEC-2 A.1: 시스템 폴더 규칙(Windows·Users·EFI …)은 **내장 Windows 드라이브에만** 적용한다.
외장·USB·네트워크 위치(dict 의 external:true 또는 kind 'usb'|'network')에서는 루트 자체와
장치·NAS 시스템 폴더($RECYCLE.BIN, System Volume Information, .DS_Store, @eaDir, #recycle,
#snapshot, .fakebin)만 막는다 — 공유 안의 `Users`·`Windows` 같은 이름의 사용자 폴더를 막지 않도록.
"""
from __future__ import annotations

import ntpath
import os
import posixpath
import re
import socket

IS_WIN = os.name == "nt"
APP_CAT = 9

SYSTEM_DIRS = frozenset({
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
    "system volume information", "recovery", "boot", "efi", "$winreagent", "$windows.~bt",
    # SPEC 목록 밖이지만 지우면 위험한 것들
    "$windows.~ws", "windows.old", "$sysreset", "config.msi", "documents and settings",
    "$getcurrent", "msocache",
})
ROOT_FILES = frozenset({
    "pagefile.sys", "hiberfil.sys", "swapfile.sys", "bootmgr", "bootnxt", "bootnt",
    "dumpstack.log.tmp", "dumpstack.log", "bootsect.bak",
})
USER_STD = frozenset({
    "desktop", "documents", "downloads", "pictures", "videos", "music", "onedrive",
    "favorites", "contacts", "links", "saved games", "searches", "3d objects",
})
APPDATA_SUBS = frozenset({"local", "roaming", "locallow"})
FAKE_META = frozenset({".fakebin", "_drive.json"})
# 외장·NAS 에서 막는 장치·NAS 시스템 이름(경로의 어느 단계에 있어도)
DEVICE_SYS = frozenset({"$recycle.bin", "system volume information", ".ds_store", "@eadir",
                        "#recycle", "#snapshot", ".fakebin"})
EXTERNAL_KINDS = frozenset({"usb", "network"})

_RESERVED = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"}
                      | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)})
_BAD_CHARS = set('<>"|?*')
_SHORT_NAME = re.compile(r"^[^.~\s]{1,6}~\d{1,6}(\.[^.\s]{1,3})?$")
_NT_LIKE = re.compile(r"^(?:[A-Za-z]:|[\\/]{2})")
_DRIVE = re.compile(r"^([A-Za-z]):(?:([\\/].*))?$", re.S)

R_EMPTY = "경로가 비어 있어요."
R_BADCHAR = "경로에 쓸 수 없는 문자가 있어요."
R_RELATIVE = "전체 경로가 아니라서 다룰 수 없어요."
R_DOTDOT = "경로에 '..'(상위 폴더 이동)이 있어 다룰 수 없어요."
R_NETWORK = "장치 경로나 올바르지 않은 네트워크 경로는 다룰 수 없어요."
R_NAME = "Windows 가 이름을 다르게 해석할 수 있는 경로라 다룰 수 없어요."
R_UNKNOWN = "DriveBoard 가 관리하는 드라이브의 경로가 아니에요."
R_ROOT = "드라이브 최상위 폴더는 다룰 수 없어요."
R_SYSDIR = "Windows 시스템 폴더라 보호돼요."
R_DEVSYS = "장치·NAS 가 쓰는 시스템 폴더라 보호돼요."
R_SYSFILE = "Windows 시스템 파일이라 보호돼요."
R_USERS = "사용자 폴더(Users) 자체는 보호돼요."
R_PROFILE = "사용자 프로필 폴더 자체는 보호돼요. 안의 항목은 고를 수 있어요."
R_USERFILE = "사용자 설정 파일이라 보호돼요."
R_APPDATA = "AppData 폴더 자체는 보호돼요. 안의 캐시 폴더는 고를 수 있어요."
R_STD = "바탕 화면·문서 같은 기본 폴더 자체는 보호돼요. 안의 항목은 고를 수 있어요."
R_TEMP = "Temp 폴더 자체는 보호돼요. 안의 항목은 고를 수 있어요."
R_FAKEMETA = "가짜 드라이브 관리 파일이라 보호돼요."
R_APPCAT = "앱·시스템 항목이라 보호돼요."
R_SELF = "DriveBoard 가 쓰는 폴더라 보호돼요."
R_SELF_PARENT = "DriveBoard 가 쓰는 폴더가 안에 있어 보호돼요."
R_LOCAL_SHARE = ("이 PC 자신의 공유 폴더(\\\\localhost·\\\\PC이름 등)라 다루지 않아요 — 시스템 폴더 보호가 적용되지 않는 "
                 "경로예요. 드라이브 문자(C: 등)로 다뤄 주세요.")

_extra_raw: list = []


class _Bad(Exception):
    pass


def mod_for(s, flavor=None):
    """경로 문자열에 맞는 path 모듈(ntpath/posixpath)."""
    if flavor == "nt":
        return ntpath
    if flavor == "posix":
        return posixpath
    if IS_WIN:
        return ntpath
    if isinstance(s, str) and _NT_LIKE.match(s):
        return ntpath
    return posixpath


def _check_nt_component(c):
    if c.endswith(".") or c.endswith(" "):
        raise _Bad(R_NAME)                      # 'Windows.' 는 'Windows' 로 해석된다
    if ":" in c:
        raise _Bad(R_NAME)                      # 대체 데이터 스트림
    if any(ch in _BAD_CHARS or ord(ch) < 32 for ch in c):
        raise _Bad(R_BADCHAR)
    stem = c.split(".", 1)[0].rstrip(" ").casefold()
    if stem in _RESERVED:
        raise _Bad(R_NAME)                      # CON, NUL … 장치 이름
    if _SHORT_NAME.match(c) and len(c.split(".", 1)[0]) <= 8:
        raise _Bad(R_NAME)                      # PROGRA~1 같은 8.3 짧은 이름


def _split(path, flavor=None):
    """(mod, drive, comps). drive 는 'D:'(nt) 또는 ''(posix). 문제가 있으면 _Bad."""
    if not isinstance(path, str) or not path.strip():
        raise _Bad(R_EMPTY)
    if "\0" in path:
        raise _Bad(R_BADCHAR)
    mod = mod_for(path, flavor)
    if mod is ntpath:
        p = path
        if p.startswith("\\\\?\\") or p.startswith("\\??\\"):
            rest = p[4:]
            if re.match(r"^[A-Za-z]:\\", rest) and "/" not in rest:
                p = rest
            elif rest[:4].upper() == "UNC\\" and "/" not in rest:
                p = "\\\\" + rest[4:]                # 확장 길이 UNC → 일반 UNC
            else:
                raise _Bad(R_NETWORK)
        if p[:2] in ("\\\\", "//", "\\/", "/\\"):
            return _split_unc(mod, p)
        m = _DRIVE.match(p)
        if not m or m.group(2) is None:
            raise _Bad(R_RELATIVE)              # 'Windows', '\\Windows', 'C:Windows', 'C:'
        drive = m.group(1).upper() + ":"
        comps = []
        for c in re.split(r"[\\/]", m.group(2)):
            if c == "" or c == ".":
                continue
            if c == "..":
                raise _Bad(R_DOTDOT)
            _check_nt_component(c)
            comps.append(c)
        return mod, drive, comps
    if not path.startswith("/"):
        raise _Bad(R_RELATIVE)
    comps = []
    for c in path.split("/"):
        if c == "" or c == ".":
            continue
        if c == "..":
            raise _Bad(R_DOTDOT)
        comps.append(c)
    return mod, "", comps


def _split_unc(mod, p):
    """'\\\\srv\\share\\a' → (ntpath, '\\\\srv\\share', ['a']). 서버·공유 이름이 이상하면 _Bad."""
    parts = re.split(r"[\\/]", p[2:])
    if len(parts) < 2:
        raise _Bad(R_NETWORK)
    server, share = parts[0], parts[1]
    if not server or not share or server in (".", "?") or ":" in server:
        raise _Bad(R_NETWORK)                   # \\\\.\\ · \\\\?\\ 장치 경로, 빈 이름
    for c in (server, share):
        if c in (".", "..") or any(ch in _BAD_CHARS or ord(ch) < 32 for ch in c):
            raise _Bad(R_NETWORK)
    _check_nt_component(share)
    comps = []
    for c in parts[2:]:
        if c == "" or c == ".":
            continue
        if c == "..":
            raise _Bad(R_DOTDOT)
        _check_nt_component(c)
        comps.append(c)
    return mod, "\\\\" + server + "\\" + share, comps


def normalize(path, flavor=None):
    """정규화한 경로 문자열(실패 시 None). nt: 'D:\\a\\b', posix: '/a/b'."""
    try:
        mod, drive, comps = _split(path, flavor)
    except _Bad:
        return None
    if mod is ntpath:
        return drive + "\\" + "\\".join(comps)
    return "/" + "/".join(comps)


def _drive_root(d):
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        r = d.get("root")
        if r:
            return r
        letter = d.get("letter")
        if isinstance(letter, str) and len(letter) == 1 and letter.isalpha():
            return letter.upper() + ":\\"          # 'N-…' 키는 루트 없이 추측하지 않는다
    return None


def _fold(comps):
    return [c.casefold() for c in comps]


def _match(mod, drive, comps, drives, flavor):
    """가장 긴(깊은) 루트를 가진 드라이브 → (drive_obj, root_comps) 또는 None."""
    fc = _fold(comps)
    best = None
    for d in drives or ():
        root = _drive_root(d)
        if not root:
            continue
        try:
            rmod, rdrive, rcomps = _split(root, flavor)
        except _Bad:
            continue
        if rmod is not mod or rdrive.casefold() != drive.casefold():
            continue
        if len(rcomps) > len(comps) or _fold(rcomps) != fc[:len(rcomps)]:
            continue
        if best is None or len(rcomps) > len(best[1]):
            best = (d, rcomps)
    return best


_LOOPBACK_HOSTS = frozenset({"localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1", "0--1.ipv6-literal.net",
                             "0.0.0.0", "."})


_local_names_cache: list = []


def _local_hostnames():
    if _local_names_cache:
        return _local_names_cache[0]
    names = set()
    for n in (os.environ.get("COMPUTERNAME"), _safe_hostname()):
        if n:
            n = n.casefold().rstrip(".")
            names.add(n)
            names.add(n.split(".", 1)[0])
    names.discard("")
    _local_names_cache.append(frozenset(names))
    return _local_names_cache[0]


def _safe_hostname():
    try:
        return socket.gethostname()
    except Exception:
        return ""


def unc_host(p):
    """'\\\\host\\share…' 의 host(소문자), UNC 가 아니면 ''."""
    if not isinstance(p, str):
        return ""
    q = p.replace("/", "\\")
    if q[:4] in ("\\\\?\\", "\\\\.\\"):
        q = q[4:]
        if q[:4].upper() == "UNC\\":
            q = "\\\\" + q[4:]
        else:
            return ""
    if not q.startswith("\\\\"):
        return ""
    return q[2:].split("\\", 1)[0].casefold().rstrip(".")


def is_local_unc(p):
    """UNC 가 이 PC 자신을 가리키는가(루프백·127.x·이 PC 이름). 그 아래는 C:\\Windows 와 같은 곳일 수 있다."""
    h = unc_host(p)
    if not h:
        return False
    if h in _LOOPBACK_HOSTS or h.startswith("127.") or h.endswith(".localhost"):
        return True
    return h in _local_hostnames() or h.split(".", 1)[0] in _local_hostnames()


def is_external(d):
    """외장·USB·네트워크 위치인가(시스템 폴더 규칙을 적용하지 않는 곳). 문자열 루트는 내장으로 본다."""
    if not isinstance(d, dict):
        return False
    return bool(d.get("external")) or d.get("kind") in EXTERNAL_KINDS or d.get("network") is True


def drive_of(path, drives, flavor=None):
    """(drive, rel_comps) — path 가 속한 드라이브와 그 루트 기준 상대 경로 조각(대소문자 보존). 없으면 None."""
    try:
        mod, drive, comps = _split(path, flavor)
    except _Bad:
        return None
    hit = _match(mod, drive, comps, drives, flavor)
    if hit is None:
        return None
    return hit[0], comps[len(hit[1]):]


def set_extra_protected(paths):
    """앱 폴더·데이터 폴더처럼 추가로 지킬 경로(그 안과 그 조상 폴더 모두 거부)."""
    global _extra_raw
    _extra_raw = [p for p in (paths or []) if isinstance(p, str) and p]


def get_extra_protected():
    return list(_extra_raw)


def _builtin_protected():
    out = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]   # 앱 폴더
    home = os.environ.get("DRIVEBOARD_HOME")
    if home:
        out.append(os.path.abspath(home))
    elif IS_WIN and os.environ.get("LOCALAPPDATA"):
        out.append(os.path.join(os.environ["LOCALAPPDATA"], "DriveBoard"))
    else:
        out.append(os.path.join(os.path.expanduser("~"), ".driveboard"))
    return out


def _extra_reason(mod, drive, comps, root_comps):
    fc = _fold(comps)
    frc = _fold(root_comps)
    for raw in list(_extra_raw) + _builtin_protected():
        try:
            emod, edrive, ecomps = _split(raw)
        except _Bad:
            continue
        if emod is not mod or edrive.casefold() != drive.casefold():
            continue
        fe = _fold(ecomps)
        if len(fe) <= len(frc) and frc[:len(fe)] == fe:
            continue        # 드라이브 전체가 그 폴더 안(가짜 드라이브를 앱 폴더 안에 둔 경우) → 적용 안 함
        if len(fc) >= len(fe) and fc[:len(fe)] == fe:
            return R_SELF
        if len(fe) > len(fc) and fe[:len(fc)] == fc:
            return R_SELF_PARENT
    return None


def _rule_reason(rl, is_fake, external=False):
    """루트 기준 상대 조각(소문자) → 이유 또는 None."""
    if not rl:
        return R_ROOT
    top = rl[0]
    if is_fake and top in FAKE_META:
        return R_FAKEMETA
    if external:
        if any(c in DEVICE_SYS for c in rl):
            return R_DEVSYS
        return None
    if top in SYSTEM_DIRS:
        return R_SYSDIR
    if len(rl) == 1 and top in ROOT_FILES:
        return R_SYSFILE
    if top == "users":
        if len(rl) == 1:
            return R_USERS
        if len(rl) == 2:
            return R_PROFILE
        sub = rl[2]
        if len(rl) == 3:
            if sub == "appdata":
                return R_APPDATA
            if sub in USER_STD or sub.startswith("onedrive - "):
                return R_STD
            if sub.startswith("ntuser") or sub.startswith("usrclass"):
                return R_USERFILE
        if sub == "appdata":
            if len(rl) == 4 and rl[3] in APPDATA_SUBS:
                return R_APPDATA
            if len(rl) == 5 and rl[3] == "local" and rl[4] == "temp":
                return R_TEMP
    return None


def check(path, drives, flavor=None, cat=None):
    """(ok, reason_ko). ok 이면 reason 은 ''. cat 이 APP_CAT(9) 이면 거부."""
    try:
        mod, drive, comps = _split(path, flavor)
    except _Bad as e:
        return False, str(e)
    hit = _match(mod, drive, comps, drives, flavor)
    if hit is None:
        return False, R_UNKNOWN
    d, rcomps = hit
    if isinstance(d, dict) and (is_local_unc(d.get("unc")) or is_local_unc(d.get("root"))) or is_local_unc(path):
        return False, R_LOCAL_SHARE         # 루프백·자기 PC 공유: 외장 규칙(루트만 보호)이 적용되면 안 된다
    rel = comps[len(rcomps):]
    is_fake = mod is posixpath or (len(rcomps) > 0 and not drive.startswith("\\\\"))
    reason = _rule_reason(_fold(rel), is_fake, is_external(d))
    if reason:
        return False, reason
    if cat == APP_CAT:
        return False, R_APPCAT
    reason = _extra_reason(mod, drive, comps, rcomps)
    if reason:
        return False, reason
    return True, ""


def is_protected(path, drives, flavor=None, cat=None):
    return not check(path, drives, flavor=flavor, cat=cat)[0]

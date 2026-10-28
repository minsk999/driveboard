"""Category definitions and classification rules (SPEC §3).

Everything here is pure: no I/O. The id of a category is its list index and is
written into saved indexes, so the order of CATEGORIES must never change.
"""

import os
import re

# ---------------------------------------------------------------- categories

CATEGORIES = [
    {"id": 0, "key": "cache", "name": "캐시·임시", "color": "#FF9F0A", "deletable": True,
     "desc": "AE 디스크 캐시·미디어 캐시·Temp. 지워도 다시 만들어져요"},
    {"id": 1, "key": "render", "name": "렌더·출력", "color": "#2FD27F", "deletable": True,
     "desc": "렌더·익스포트·납품본"},
    {"id": 2, "key": "source", "name": "촬영·소스", "color": "#0A84FF", "deletable": True,
     "desc": "카메라 원본·소스 푸티지"},
    {"id": 3, "key": "proxy", "name": "프록시", "color": "#30D5C8", "deletable": True,
     "desc": "프록시·저해상도 사본"},
    {"id": 4, "key": "video", "name": "기타 영상", "color": "#6E6BFF", "deletable": True,
     "desc": "위로 분류되지 않은 영상"},
    {"id": 5, "key": "project", "name": "프로젝트", "color": "#BF5AF2", "deletable": True,
     "desc": "aep·prproj·psd·ai·c4d·blend …"},
    {"id": 6, "key": "image", "name": "이미지·시퀀스", "color": "#FF4F8B", "deletable": True,
     "desc": "사진·이미지·이미지 시퀀스"},
    {"id": 7, "key": "audio", "name": "오디오", "color": "#FFD60A", "deletable": True,
     "desc": "wav·mp3·aiff …"},
    {"id": 8, "key": "archive", "name": "압축·백업", "color": "#C0A37A", "deletable": True,
     "desc": "zip·rar·7z·iso·Auto-Save"},
    {"id": 9, "key": "app", "name": "앱·시스템", "color": "#8E8E93", "deletable": False,
     "desc": "Windows·Program Files·실행 파일"},
    {"id": 10, "key": "other", "name": "기타", "color": "#5C5C63", "deletable": True,
     "desc": "나머지"},
]

NCAT = 11
(CACHE, RENDER, SOURCE, PROXY, VIDEO, PROJECT, IMAGE, AUDIO,
 ARCHIVE, APP, OTHER) = range(NCAT)

KEY_TO_ID = {c["key"]: c["id"] for c in CATEGORIES}
ID_TO_KEY = [c["key"] for c in CATEGORIES]

# Virtual segments (UI bars only, not categories).
VIRTUAL = {
    "bin": {"name": "휴지통", "color": "#FF453A"},
    "unscanned": {"name": "시스템·확인 불가", "color": "#3A3A40"},
}


def cat_id(value):
    """Accepts an int id or a key string; returns the int id or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value < NCAT else None
    if isinstance(value, str):
        v = value.strip().lower()
        if v.isdigit():
            n = int(v)
            return n if 0 <= n < NCAT else None
        return KEY_TO_ID.get(v)
    return None


# ---------------------------------------------------------------- data tables (SPEC §3.2)

CACHE_EXT = frozenset({"cfa", "pek", "ims", "mcdb", "prmdc", "aecache", "mcache"})
VIDEO_EXT = frozenset({
    "mp4", "mov", "mxf", "avi", "mkv", "m4v", "wmv", "mts", "m2ts", "r3d", "braw", "ari",
    "mpg", "mpeg", "webm", "3gp", "insv", "dv", "vob", "flv", "f4v", "hevc", "h264", "h265",
    "prores"})
PROXY_VIDEO_EXT = frozenset({"lrv"})                      # GoPro low-res video
RAW_VIDEO_EXT = frozenset({"r3d", "braw", "ari", "mxf"})
IMAGE_EXT = frozenset({
    "jpg", "jpeg", "png", "tif", "tiff", "exr", "dpx", "tga", "bmp", "gif", "webp", "heic",
    "heif", "cr2", "cr3", "arw", "nef", "dng", "raf", "orf", "rw2", "hdr"})
PROJECT_EXT = frozenset({
    "aep", "aepx", "aet", "prproj", "psd", "psb", "ai", "indd", "c4d", "blend", "ma", "mb",
    "max", "hip", "nk", "drp", "fcpxml", "veg", "sesx", "mogrt", "ffx"})
AUDIO_EXT = frozenset({"wav", "mp3", "aac", "m4a", "aif", "aiff", "flac", "ogg", "opus",
                       "wma", "bwf"})
ARCHIVE_EXT = frozenset({"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "iso", "dmg",
                         "img", "bak"})
APP_EXT = frozenset({"exe", "dll", "sys", "msi", "msp", "cab", "appx", "msix", "aex",
                     "plugin", "ocx", "drv", "cpl"})

ROOT_SYS_FILES = frozenset({"pagefile.sys", "hiberfil.sys", "swapfile.sys",
                            "dumpstack.log.tmp"})
# $windows.~ws is Windows setup media; treated as system (not cache) for safety.
ROOT_SYS_DIRS = frozenset({
    "windows", "program files", "program files (x86)", "programdata", "$windows.~bt",
    "$windows.~ws", "windows.old", "recovery", "$winreagent", "boot", "efi"})

CACHE_DIR_NAMES = frozenset({"media cache files", "media cache", "peak files", "disk cache",
                             "aecache", "conformed audio files"})
CACHE_DIR_PREFIXES = ("disk cache - ",)
ARCHIVE_DIR_NAMES = frozenset({"adobe after effects auto-save", "adobe premiere pro auto-save",
                               "auto-save", "autosave", "backup", "backups", "백업"})
PROXY_DIR_NAMES = frozenset({"proxy", "proxies", "프록시"})
RENDER_DIR_RE = re.compile(
    r"^(render|renders|rendered|output|outputs|export|exports|deliver|delivery|deliverables"
    r"|final|finals|out|출력|렌더|납품|완성본|최종)$")
RENDER_DIR_SUBSTR = ("render", "렌더", "출력", "납품")
SOURCE_DIR_RE = re.compile(
    r"^(footage|footages|source|sources|src|raw|rush|rushes|원본|소스|촬영|촬영본|camera|cam"
    r"|dcim|private|m4root|xdroot|avchd|contents|clip|clips)$")
SOURCE_DIR_SUBSTR = ("촬영", "원본", "footage")

CAMERA_RE = re.compile(
    r"(?:C\d{4}|[A-Z]\d{3}_?C\d{3}.*|G[HX]\d{6}|GOPR\d{4}|DJI_\d{4,}.*|MVI_\d{4}|IMG_\d{4}"
    r"|PXL_\d{8}_\d+.*|VID_\d{8}_\d+.*|\d{8}_\d{6})", re.IGNORECASE)
PROXY_NAME_SUFFIXES = ("_proxy", "-proxy", " proxy")
RENDER_NAME_SUBSTR = ("comp", "render", "final", "export", "렌더", "최종", "완성")
VERSION_TAIL_RE = re.compile(r"[_ ]v\d+$", re.IGNORECASE)

# Image sequences (SPEC §3.3): never grouped for these extensions.
# Deviation: video extensions are excluded too (camera clips C0001.MP4… stay separate files
# so that duplicate detection and per-clip actions keep working).
SEQ_EXCLUDE_EXT = CACHE_EXT | VIDEO_EXT | PROXY_VIDEO_EXT
SEQ_MIN_FRAMES = 5
SEQ_MIN_DIGITS = 3
SEQ_MAX_DIGITS = 8
# name = prefix + digits + "." + ext ; prefix is lazy so digits is the maximal trailing run.
FRAME_RE = re.compile(r"^(.*?)([0-9]+)\.([^.]+)$", re.DOTALL)
# display name of a collapsed sequence: prefix + "[####]" + "." + ext
SEQ_DISPLAY_RE = re.compile(r"^(.*)\[(#{3,8})\]\.([^.]+)$", re.DOTALL)

# ---------------------------------------------------------------- file classification

_T_VIDEO, _T_LRV, _T_IMAGE, _T_PROJECT, _T_AUDIO, _T_ARCHIVE, _T_APP, _T_CACHE = range(8)
_EXT_TYPE = {}
for _set, _t in ((APP_EXT, _T_APP), (ARCHIVE_EXT, _T_ARCHIVE), (AUDIO_EXT, _T_AUDIO),
                 (PROJECT_EXT, _T_PROJECT), (IMAGE_EXT, _T_IMAGE), (VIDEO_EXT, _T_VIDEO),
                 (PROXY_VIDEO_EXT, _T_LRV), (CACHE_EXT, _T_CACHE)):
    for _e in _set:
        _EXT_TYPE[_e] = _t
del _set, _t, _e

# ctx = (cat|None, mode, loc). mode: how the category was obtained.
M_NORMAL, M_RULE, M_CPATH, M_SYS, M_APPDATA = range(5)
# loc bits describe the directory itself (used by its children).
L_USERS = 1      # <root>\Users
L_USER = 2       # <root>\Users\<name>
L_APPDATA = 4    # any dir named AppData
L_LOCAL = 8      # AppData\Local

ROOT_CTX = (None, M_NORMAL, 0)


def split_ext(name):
    """(stem, ext_lower). ext is text after the last '.', '' if none."""
    dot = name.rfind(".")
    if dot < 0:
        return name, ""
    return name[:dot], name[dot + 1:].lower()


def classify(name, ctx, depth=1):
    """Category id for a file `name` inside a directory with context `ctx`.

    depth = depth of the containing directory (drive root = 0).
    """
    cat, mode, _loc = ctx
    if mode == M_RULE or mode == M_CPATH:
        return cat
    dot = name.rfind(".")
    if dot < 0:
        stem, ext = name, ""
    else:
        stem, ext = name[:dot], name[dot + 1:].lower()
    t = _EXT_TYPE.get(ext)
    if t == _T_CACHE:
        return CACHE
    if cat == CACHE:
        return CACHE
    if cat == APP:
        return APP
    if depth == 0 and name.lower() in ROOT_SYS_FILES:
        return APP
    if t is None:
        if cat == ARCHIVE:
            return ARCHIVE
        return OTHER
    if t == _T_VIDEO:
        sl = stem.lower()
        if cat == PROXY or sl.endswith(PROXY_NAME_SUFFIXES) or "_proxy" in sl:
            return PROXY
        if cat == RENDER:
            return RENDER
        if cat == SOURCE or ext in RAW_VIDEO_EXT or CAMERA_RE.fullmatch(stem):
            return SOURCE
        if cat == ARCHIVE:
            return ARCHIVE
        for s in RENDER_NAME_SUBSTR:
            if s in sl:
                return RENDER
        if VERSION_TAIL_RE.search(sl):
            return RENDER
        return VIDEO
    if t == _T_LRV:
        return PROXY
    if t == _T_IMAGE:
        if cat == RENDER or cat == PROXY or cat == SOURCE:
            return cat
        return IMAGE
    if t == _T_PROJECT:
        # Auto-Save folders hold .aep/.prproj; the archive context wins for them.
        return ARCHIVE if cat == ARCHIVE else PROJECT
    if t == _T_AUDIO:
        return AUDIO
    if t == _T_ARCHIVE:
        return ARCHIVE
    if t == _T_APP:
        return ARCHIVE if cat == ARCHIVE else APP
    return OTHER


def _normkey(path):
    return os.path.normcase(os.path.normpath(path))


class Classifier:
    """Directory-context + file classification bound to one settings snapshot."""

    def __init__(self, settings=None, internal=True):
        settings = settings or {}
        # 외장·USB·NAS(internal=False)에는 Windows 시스템 문맥(최상위 Windows·Program Files…, AppData)을
        # 적용하지 않는다(SPEC-2 A.1) — 공유·외장 디스크의 같은 이름 폴더가 앱·시스템(보호)으로 오인되지 않게
        self.internal = bool(internal)
        rules = {}
        for r in settings.get("rules") or ():
            try:
                p = r.get("path") if isinstance(r, dict) else None
                c = cat_id(r.get("cat")) if isinstance(r, dict) else None
            except Exception:
                continue
            if isinstance(p, str) and p.strip() and c is not None:
                rules[_normkey(p.strip())] = c
        cpaths = set()
        for p in settings.get("cache_paths") or ():
            if isinstance(p, str) and p.strip():
                cpaths.add(_normkey(p.strip()))
        self.rules = rules
        self.cache_paths = cpaths
        self.needs_paths = bool(rules or cpaths)

    # -- contexts
    def root_ctx(self, root_path=None):
        if root_path is not None and self.needs_paths:
            k = _normkey(root_path)
            c = self.rules.get(k)
            if c is not None:
                return (c, M_RULE, 0)
            if k in self.cache_paths:
                return (CACHE, M_CPATH, 0)
        return ROOT_CTX

    def dir_ctx(self, parent_ctx, dir_name, dir_path, depth):
        """Context of directory `dir_name` (full path `dir_path`, depth >= 1)."""
        pcat, pmode, ploc = parent_ctx
        n = dir_name.strip().lower()
        loc = 0
        if depth == 1 and n == "users":
            loc = L_USERS
        elif ploc & L_USERS:
            loc = L_USER
        if n == "appdata":
            loc |= L_APPDATA
        elif n == "local" and (ploc & L_APPDATA):
            loc |= L_LOCAL

        if self.needs_paths and dir_path is not None:
            k = _normkey(dir_path)
            c = self.rules.get(k)
            if c is not None:
                return (c, M_RULE, loc)
            if pmode == M_RULE:
                return (pcat, M_RULE, loc)
            if k in self.cache_paths:
                return (CACHE, M_CPATH, loc)
        if pmode == M_RULE or pmode == M_CPATH or pmode == M_SYS:
            return (pcat, pmode, loc)

        # 3. name contexts — cache first (also wins inside AppData)
        if (n in CACHE_DIR_NAMES or n.startswith(CACHE_DIR_PREFIXES)
                or (n == "temp" and (ploc & L_LOCAL))):
            return (CACHE, M_NORMAL, loc)
        if pmode == M_APPDATA:
            return (APP, M_APPDATA, loc)
        if self.internal:
            if depth == 1 and n in ROOT_SYS_DIRS:
                return (APP, M_SYS, loc)
            if n == "appdata" and (ploc & L_USER):
                return (APP, M_APPDATA, loc)
        if n in ARCHIVE_DIR_NAMES:
            return (ARCHIVE, M_NORMAL, loc)
        if n in PROXY_DIR_NAMES:
            return (PROXY, M_NORMAL, loc)
        if RENDER_DIR_RE.match(n):
            return (RENDER, M_NORMAL, loc)
        for s in RENDER_DIR_SUBSTR:
            if s in n:
                return (RENDER, M_NORMAL, loc)
        if SOURCE_DIR_RE.match(n):
            return (SOURCE, M_NORMAL, loc)
        for s in SOURCE_DIR_SUBSTR:
            if s in n:
                return (SOURCE, M_NORMAL, loc)
        return (pcat, M_NORMAL, loc)

    @staticmethod
    def file_cat(file_name, ctx, depth):
        return classify(file_name, ctx, depth)

    def ctx_for(self, path, root):
        """(ctx, depth) of directory `path` by walking from drive `root`.

        Raises ValueError if `path` is not `root` or below it.
        """
        parts = rel_parts(root, path)
        if parts is None:
            raise ValueError("path is outside the drive root")
        ctx = self.root_ctx(root)
        cur = root
        for i, comp in enumerate(parts):
            cur = os.path.join(cur, comp)
            ctx = self.dir_ctx(ctx, comp, cur, i + 1)
        return ctx, len(parts)


def strip_long_prefix(p):
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    if p.startswith("\\\\?\\"):
        return p[4:]
    return p


def rel_parts(root, path):
    """Path components of `path` relative to `root` (original spelling), or None."""
    path = strip_long_prefix(path)
    root = strip_long_prefix(root)
    np_ = os.path.normpath(path)
    nr = os.path.normpath(root)
    kp = os.path.normcase(np_)
    kr = os.path.normcase(nr)
    if kp == kr:
        return []
    pre = kr if kr.endswith(os.sep) else kr + os.sep
    if not kp.startswith(pre):
        return None
    rest = np_[len(pre):]
    if not rest:
        return []
    parts = rest.split(os.sep)
    if any(p in ("", ".", "..") for p in parts):
        return None
    return parts


def dominant(cat_bytes):
    """Category id with the most bytes (OTHER when all zero)."""
    best, bv = OTHER, 0
    for i, v in enumerate(cat_bytes):
        if v > bv:
            best, bv = i, v
    return best

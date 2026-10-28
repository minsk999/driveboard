"""DriveBoard — 진입점: 127.0.0.1 HTTP 서버 + (선택) pywebview 창 + API 라우팅 (SPEC §7 · SPEC-2).

python app.py [--no-open] [--port N] [--fake DIR] [--multi]
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import stat as stat_mod
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import __version__                      # noqa: E402
from core import categories as cats_mod           # noqa: E402
from core import dupes as dupes_mod               # noqa: E402
from core import media, paths, projects, update   # noqa: E402
from core import model, ops, recommend, safety, scanner, store, winapi  # noqa: E402
from core import settings as settings_mod         # noqa: E402
from core import version as version_mod           # noqa: E402
from core.categories import APP, NCAT, dominant   # noqa: E402

UI_DIR = paths.ui_dir()
DEFAULT_PORT = 8790
BODY_MAX = 1 << 20
GB = 1 << 30
STATIC_TYPES = {
    "html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8",
    "js": "application/javascript; charset=utf-8", "woff2": "font/woff2",
    "txt": "text/plain; charset=utf-8", "svg": "image/svg+xml", "png": "image/png",
    "ico": "image/x-icon",
}
TOKEN_PLACEHOLDER = b"__DB_TOKEN__"
R_LOOSE = "작은 파일 묶음은 폴더째로 고르세요."
R_OFFLINE = "연결되지 않은 장치라 다룰 수 없어요."
DRIVES_TTL = 2.0
BIN_TTL = 5.0
SCAN_KEEP = 30.0
STATS_TTL = 3.0
STALE_ABS = 20 * GB
STALE_REL = 0.05
LOCK_NAME = "instance.json"
OPEN_URL_PREFIX = "/" + version_mod.REPO          # https://github.com/minsk999/driveboard/...
VIDEO_PROBE_BUDGET = 2.0                          # 프로젝트 상세: ffprobe 를 기다리는 총 시간(초)
VIDEO_PROBE_MAX = 8
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_OFF_KEY_RE = re.compile(r"^~[A-Za-z0-9_.-]{1,64}$")
_CLOUD_MASK = 0x400 | 0x1000 | 0x40000 | 0x400000   # REPARSE | OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA
_DEV_FIELDS = ("letter", "label", "kind", "external", "has_bin", "unc", "fs", "root")
_PROJECT_KEYS = ("workers", "client_aliases", "project_roots", "project_excluded", "rules", "cache_paths")
_DRIVE_KEYS = ("network_locations", "drive_scan")


class ApiError(Exception):
    def __init__(self, status, code, message, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self):
        d = {"ok": False, "error": self.code, "message": self.message}
        d.update(self.extra)
        return d


def _op_error(e):
    status = 404 if e.code == "not_found" else 400
    return ApiError(status, e.code, e.message, **(e.extra or {}))


def _int(v, default=0, lo=None, hi=None):
    try:
        n = int(float(v))
    except (TypeError, ValueError, OverflowError):
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


# 요청 본문·쿼리에서 경로를 담는 키. 8.3 짧은 이름(…\\KIMMIN~1\\…)은 여기서 한 번 긴 이름으로 바꿔
# 인덱스·보호 규칙·작업이 모두 같은 모양의 경로를 보게 한다(safety 도 스스로 펼치지만, 인덱스 조회·
# 확인한 양 대조처럼 문자열을 그대로 비교하는 곳이 있다). 'label' 같은 표시용 값은 건드리지 않는다.
_PATH_KEYS = frozenset({"path", "paths", "dest_dir", "initial", "expect", "add", "remove"})


def _canon_paths(v, depth=0):
    if isinstance(v, str):
        return safety.expand_short_names(v)
    if depth > 3:
        return v
    if isinstance(v, list):
        return [_canon_paths(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {k: (_canon_paths(x, depth + 1) if k in _PATH_KEYS else x) for k, x in v.items()}
    return v


def canon_request(b):
    """API 요청(dict)의 경로 값들을 긴 이름으로(Windows 만 — 그 밖엔 그대로)."""
    if not isinstance(b, dict):
        return b
    return {k: (_canon_paths(v) if k in _PATH_KEYS else v) for k, v in b.items()}


def _dkey(x):
    """화면·API 의 드라이브 키 정규형: 'd'→'D', 'n-3FA9C1'→'N-3fa9c1', 연결 안 된 장치 '~XXXX'. 틀리면 ''."""
    if not isinstance(x, str):
        return ""
    k = winapi.norm_key(x)
    if k:
        return k
    s = x.strip()
    if _OFF_KEY_RE.match(s):
        return "~" + s[1:].upper()
    return ""


def _letter(d):
    return _dkey(str((d or {}).get("letter") or ""))


def _drive_name(d):
    lab = d.get("label") or ""
    L = _letter(d)
    if len(L) != 1:
        return lab or L
    return ("%s (%s:)" % (lab, L)) if lab else "%s:" % L


def _same_path(a, b):
    try:
        return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))
    except (TypeError, ValueError):
        return False


def _path_arg(p, what="경로"):
    if not isinstance(p, str) or not p.strip() or "\0" in p or len(p) > 4096:
        raise ApiError(400, "invalid", "%s가 올바르지 않아요." % what)
    return p


def _connected(d):
    return isinstance(d, dict) and d.get("connected") is not False


def _one_per_share(drives):
    """같은 네트워크 공유(UNC)가 문자(Z:)로도 매핑되고 「NAS 추가」로도 등록돼 있으면 하나만 남긴다.

    둘은 시리얼(_unc_serial)이 같아 저장 인덱스 하나를 같이 쓰게 된다 → 같은 파일이 두 드라이브에 보이고
    (자기 자신이 「중복」이 된다) 합계가 두 번 더해진다. 연결된 쪽 → 문자 드라이브 순으로 하나를 고른다.
    (winapi 가 다는 same_as 도 같은 경우다.) 지역 드라이브는 건드리지 않는다.
    """
    by = {}
    for i, d in enumerate(drives):
        u = d.get("unc") if d.get("kind") == "network" else None
        if isinstance(u, str) and u:
            by.setdefault(os.path.normcase(u.rstrip("\\/")).lower(), []).append(i)
    drop = set()
    for idxs in by.values():
        if len(idxs) < 2:
            continue
        keep = min(idxs, key=lambda i: (not _connected(drives[i]), len(_letter(drives[i])) != 1, i))
        for i in idxs:
            if i != keep:
                drop.add(i)
        also = [_letter(drives[i]) for i in idxs if i != keep]
        drives[keep] = dict(drives[keep], also_as=also)
    return [d for i, d in enumerate(drives) if i not in drop]


def _unsafe_file(path):
    """링크·클라우드 자리표시자·일반 파일 아님 → True (서빙·미리보기 생성 안 함 — 읽기만 해도 다운로드가 시작된다)."""
    try:
        st = os.lstat(winapi.long_path(path))
    except (OSError, ValueError):
        return True
    if stat_mod.S_ISLNK(st.st_mode) or not stat_mod.S_ISREG(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _CLOUD_MASK)


def _inside(path, root):
    try:
        a = os.path.normcase(os.path.realpath(path))
        r = os.path.normcase(os.path.realpath(root))
    except (OSError, ValueError):
        return False
    r = r.rstrip("\\/")
    return a == r or a.startswith(r + os.sep) or (os.altsep is not None and a.startswith(r + os.altsep))


def allowed_open_url(u):
    """https://github.com/minsk999/driveboard(/...) 만 연다(기본 브라우저)."""
    if not isinstance(u, str) or len(u) > 2048 or any(ord(c) < 33 or c in "\\\"'<>`" for c in u):
        return False
    try:
        p = urlparse(u)
    except ValueError:
        return False
    if p.scheme != "https" or p.netloc != "github.com" or p.username or p.password:
        return False
    path = p.path
    if "%" in path or any(seg in (".", "..") for seg in path.split("/")):
        return False                      # /minsk999/driveboard/../../다른/저장소 — 브라우저가 정규화하면 범위를 벗어난다
    return path == OPEN_URL_PREFIX or path.startswith(OPEN_URL_PREFIX + "/")


# ═════════════════════════════════════════════════════════════════ 앱 상태

class App:
    """인덱스·드라이브·작업 큐·미리보기를 묶는다.

    잠금 순서: self.lock(인덱스) 을 쥔 채로 queue 메서드를 부르지 않는다.
    _dlock(드라이브 캐시)·_mlock(메타)·_block(휴지통 캐시)은 말단 잠금 — 쥔 채로 다른 잠금을 잡지 않는다.
    """

    def __init__(self, start_queue=True):
        self.lock = threading.RLock()           # 인덱스 표(읽기·쓰기 모두)
        self._dlock = threading.Lock()          # 드라이브 목록 캐시
        self._block = threading.Lock()          # 휴지통 정보 캐시
        self._mlock = threading.Lock()          # drivemeta
        self._synclock = threading.Lock()       # 인덱스 ↔ 드라이브 맞추기(한 번에 하나)
        self._dialog_lock = threading.Lock()
        self.indexes = {}                       # 화면 키 -> DriveIndex (연결 안 된 장치 포함)
        self._loaded = {}                       # 저장 키(시리얼) -> DriveIndex | False(없음)
        self._offline = {}                      # 화면 키 -> 연결 안 된 장치의 가짜 드라이브 dict
        self._disp_sk = {}                      # 화면 키 -> 저장 키
        self.scans = {}                         # key -> progress dict (+ _ended)
        self._scan_dirty = {}                   # 화면 키 -> {경로 키: 경로} 전체 스캔 동안 작업이 바꾼 것(_hook_refresh → _scan_watch)
        self.verified = {}                      # dupe group id -> {res: True/False/None, fp: 그때 읽은 파일들의 지문}
        self.settings = settings_mod.load()
        self._sgen = 0                          # 프로젝트에 영향을 주는 설정이 바뀔 때마다 +1
        self._pcache = {}                       # 화면 키 -> (sig, records)
        self._drives = None
        self._drives_at = 0.0
        self._drives_sig = None
        self._refreshing = False
        self._need_sync = True
        self._bins = {}                         # root -> (t, bytes, count)
        self._stats = (0.0, None)
        self._meta_path = os.path.join(store.data_dir(), "drivemeta.json")
        self._meta = self._load_meta()
        self._meta_dirty = False
        self.window = None                      # pywebview 창(있으면)
        self._close_ok = False                  # 창 닫기를 이미 허락했다(파일 작업 중 확인을 거쳤거나 강제)
        self.open_url_fn = webbrowser.open
        self.checker = None
        ff, fp = media.find_ffmpeg(app_dir=paths.ffmpeg_app_dir())
        self.ffprobe = fp
        self.media = media.MediaRegistry(10000)
        self.previews = media.PreviewQueue(store.data_dir(), ff, workers=2,
                                           cache_gb=self.settings.get("preview_cache_gb", 2), ffprobe=fp)
        self.queue = ops.JobQueue(store.data_dir(), self.drives, self._safety_check, hooks={
            "size_of": self._hook_size_of,
            "on_removed": self._hook_refresh,
            "on_added": self._hook_refresh,
            "on_changed": self._hook_refresh,
            "on_job_done": self._hook_job_done,
        })
        self.drives(force=True)
        for idx in list(self.indexes.values()):
            threading.Thread(target=self._warm, args=(idx,), daemon=True).start()
        if start_queue:
            self.queue.start()

    # ── 업데이트 확인 ──
    def start_update_check(self, base_url=None, current=None):
        self.checker = update.Checker(enabled=bool(self.settings.get("update_check", True)),
                                      base_url=base_url, current=current)
        self.checker.start()
        return self.checker

    def update_state(self):
        c = self.checker
        st = c.state() if c is not None else None
        if not st:
            return None
        return {"latest": st["latest"], "url": st["url"], "newer": True}

    # ── 드라이브 ──
    def _list_raw(self):
        s = self.settings
        return _one_per_share(winapi.list_drives(network_locations=s.get("network_locations") or [],
                                                 drive_scan=s.get("drive_scan") or {}))

    def _set_drives(self, lst):
        """_dlock 을 쥔 채로 부른다."""
        self._drives = [dict(d) for d in lst]
        self._drives_at = time.monotonic()
        sig = tuple((_letter(d), d.get("serial"), d.get("root"), bool(d.get("connected", True)))
                    for d in self._drives)
        if sig != self._drives_sig:
            self._drives_sig = sig
            self._need_sync = True
        self._note_devices(self._drives)

    def drives(self, force=False):
        """연결된(+등록한) 드라이브 목록. TTL 이 지난 정도면 옛 목록을 주고 뒤에서 새로 고친다(죽은 NAS 대비)."""
        with self._dlock:
            now = time.monotonic()
            if force or self._drives is None or self._drives_at == 0.0:
                try:
                    self._set_drives(self._list_raw())
                except Exception:
                    if self._drives is None:
                        self._drives = []
                    self._drives_at = now
            elif now - self._drives_at > DRIVES_TTL and not self._refreshing:
                self._refreshing = True
                threading.Thread(target=self._bg_refresh, name="drives", daemon=True).start()
            out = [dict(d) for d in self._drives]
        self._maybe_sync(out)
        return out

    def _bg_refresh(self):
        try:
            lst = self._list_raw()
        except Exception:
            lst = None
        with self._dlock:
            if lst is not None:
                self._set_drives(lst)
            else:
                self._drives_at = time.monotonic()
            self._refreshing = False
            out = [dict(d) for d in self._drives]
        self._maybe_sync(out)

    def drive(self, letter):
        """연결 목록(등록한 NAS 포함)에서 찾는다."""
        L = _dkey(letter)
        if not L:
            return None
        for d in self.drives():
            if _letter(d) == L:
                return d
        return None

    def any_drive(self, letter):
        """연결 목록 또는 연결 안 된 장치(저장된 인덱스)."""
        d = self.drive(letter)
        if d is not None:
            return d
        with self.lock:
            off = self._offline.get(_dkey(letter))
            return dict(off) if off else None

    def all_drives(self):
        live = self.drives()
        with self.lock:
            off = [dict(x) for x in self._offline.values()]
        off.sort(key=lambda d: -(d.get("last_seen") or 0))
        return live + off

    def invalidate(self):
        winapi.invalidate_usage()
        with self._dlock:
            self._drives_at = 0.0
        with self._block:
            self._bins.clear()

    def bin_info(self, d):
        if not _connected(d) or d.get("has_bin") is False:
            return 0, 0
        root = d.get("root")
        now = time.monotonic()
        with self._block:
            hit = self._bins.get(root)
            if hit and now - hit[0] < BIN_TTL:
                return hit[1], hit[2]
        try:
            b, c = winapi.recycle_bin_info(root)
        except Exception:
            b, c = 0, 0
        with self._block:
            self._bins[root] = (time.monotonic(), int(b), int(c))
        return int(b), int(c)

    def drive_for_path(self, path):
        hit = safety.drive_of(path, self.drives()) if isinstance(path, str) and path else None
        return hit[0] if hit else None

    def index_for_path(self, path):
        d = self.drive_for_path(path)
        if d is None:
            return None, None
        return d, self.indexes.get(_letter(d))

    # ── 메타(스캔 시점 기준값 + 장치의 마지막 모습) ──
    def _load_meta(self):
        try:
            with open(self._meta_path, "rb") as f:
                m = json.loads(f.read().decode("utf-8"))
            return {k: v for k, v in m.items() if isinstance(v, dict)} if isinstance(m, dict) else {}
        except (OSError, ValueError, UnicodeDecodeError, AttributeError):
            return {}

    def _save_meta(self):
        with self._mlock:
            data = json.dumps(self._meta, ensure_ascii=False).encode("utf-8")
            self._meta_dirty = False
        try:
            store.atomic_write(self._meta_path, data)
        except OSError:
            pass

    def _meta_get(self, sk):
        with self._mlock:
            m = self._meta.get(sk)
            return json.loads(json.dumps(m)) if isinstance(m, dict) else {}

    def _meta_set(self, sk, **kv):
        with self._mlock:
            m = self._meta.setdefault(sk, {})
            m.update(kv)
            self._meta_dirty = True

    def _note_devices(self, drives):
        """연결된 장치의 모습을 기억한다(빠진 뒤 「연결 안 됨」 카드에 쓴다). 구조가 바뀔 때만 바로 저장."""
        changed = False
        now = time.time()
        with self._mlock:
            for d in drives:
                if not _connected(d):
                    continue
                sk = self._key(d)
                if not sk:
                    continue
                m = self._meta.setdefault(sk, {})
                old = m.get("dev") if isinstance(m.get("dev"), dict) else {}
                new = {k: d.get(k) for k in _DEV_FIELDS}
                new["letter"] = _letter(d)
                if any(old.get(k) != new[k] for k in _DEV_FIELDS):
                    changed = True
                new.update(total=int(d.get("total") or 0), free=int(d.get("free") or 0), last_seen=now)
                m["dev"] = new
                self._meta_dirty = True
        if changed:
            threading.Thread(target=self._save_meta, daemon=True).start()

    @staticmethod
    def _key(d_or_idx):
        try:
            return store.index_key(d_or_idx if not isinstance(d_or_idx, dict)
                                   else (d_or_idx.get("serial") or d_or_idx.get("letter")))
        except ValueError:
            return None

    # ── 인덱스 ↔ 드라이브 맞추기 (시리얼로 같은 장치를 알아본다) ──
    def _get_idx(self, sk):
        with self.lock:
            hit = self._loaded.get(sk)
        if hit is not None:
            return hit or None
        idx = store.load_index(sk)
        if idx is not None and not idx.d_name:
            idx = None
        with self.lock:
            cur = self._loaded.get(sk)
            if cur:                               # 그 사이 스캔이 끝났다
                return cur
            self._loaded[sk] = idx if idx is not None else False
        return idx

    def _maybe_sync(self, drives):
        if not self._need_sync:
            return
        owns = getattr(self.lock, "_is_owned", lambda: True)()
        # 인덱스 잠금을 쥔 스레드는 기다리지 않는다(맞추는 쪽이 그 잠금을 기다릴 수 있다 → 교착)
        if not self._synclock.acquire(blocking=not owns):
            return
        try:
            while self._need_sync:
                self._need_sync = False
                self._sync_indexes(drives)
                with self._dlock:                 # 그 사이 목록이 또 바뀌었으면 최신 것으로 한 번 더
                    drives = [dict(d) for d in (self._drives or [])]
        finally:
            self._synclock.release()

    def _resync(self):
        self._need_sync = True
        self._maybe_sync(self.drives())

    def _sync_indexes(self, drives):
        assign, disp_sk, used = {}, {}, set()
        taken = {_letter(d) for d in drives}
        for d in drives:
            L, sk = _letter(d), self._key(d)
            if not L or not sk:
                continue
            if sk in used:
                continue                          # 한 인덱스를 두 화면 키에 주지 않는다(시리얼이 같은 두 장치)
            idx = self._get_idx(sk)
            if idx is None:
                continue
            if _connected(d):
                root = model.norm_root(d.get("root") or "")
                with self.lock:
                    if not _same_path(idx.root, root):
                        # 같은 볼륨(시리얼)인데 문자가 바뀐 경우만 경로를 옮겨 쓴다
                        if idx.serial and d.get("serial") and idx.serial == d.get("serial"):
                            idx.root = root
                            idx._reset_caches()
                        else:
                            continue
            assign[L] = idx
            disp_sk[L] = sk
            used.add(sk)
        offline = {}
        for ent in store.list_indexes():
            sk = ent.get("key")
            if not sk or sk in used or not ent.get("valid_version"):
                continue
            idx = self._get_idx(sk)
            if idx is None:
                continue
            dev = self._meta_get(sk).get("dev") or {}
            want = _dkey(dev.get("letter") or ent.get("letter") or "")
            L = want if want and want not in taken and want not in assign and not want.startswith("~") \
                else "~" + sk.upper()
            if L in assign or L in taken:
                continue
            total = int(dev.get("total") or 0)
            offline[L] = {
                "letter": L, "root": idx.root, "label": dev.get("label") or idx.label or "",
                "serial": idx.serial or "", "fs": dev.get("fs") or idx.fs or "",
                "kind": dev.get("kind") or "unknown", "total": total,
                "free": int(dev.get("free") or 0), "external": bool(dev.get("external")),
                "has_bin": bool(dev.get("has_bin", True)), "scan": False, "connected": False,
                "unc": dev.get("unc"), "last_seen": dev.get("last_seen"),
                "last_letter": want or None}
            assign[L] = idx
            disp_sk[L] = sk
        with self.lock:
            for L, idx in assign.items():
                idx.letter = L
            self.indexes = assign
            self._disp_sk = disp_sk
            self._offline = offline
            for L in [k for k in self._pcache if k not in assign]:
                del self._pcache[L]

    def _warm(self, idx):
        try:
            with self.lock:
                model.warm(idx)
        except Exception:
            pass

    def save_index(self, letter):
        with self.lock:
            idx = self.indexes.get(_dkey(letter))
            if idx is None or _dkey(letter) in self._offline:
                return
            try:
                store.save_index(idx)
            except (OSError, ValueError):
                pass

    # ── ops 연동 ──
    def cat_of(self, path):
        with self.lock:
            d, idx = self.index_for_path(path)
            if idx is None:
                return None
            r = model.find_item(idx, path)
            if r is None:
                return None
            if r[0] == "file":
                return idx.f_cat[r[1]]
            b = r[1] * NCAT
            return APP if dominant(idx.d_cat[b:b + NCAT]) == APP else None

    def _safety_check(self, path):
        return safety.check(path, self.drives(), cat=self.cat_of(path))

    def protected(self, path, cat):
        ok, reason = safety.check(path, self.drives(), cat=cat)
        return (not ok), ("" if ok else reason)

    def _hook_size_of(self, path):
        with self.lock:
            _d, idx = self.index_for_path(path)
            if idx is None:
                return None
            return model.size_of(idx, path)

    def _hook_refresh(self, path):
        d = self.drive_for_path(path)
        if d is None:
            return
        L = _letter(d)
        with self.lock:
            self._note_dirty(L, path)
            idx = self.indexes.get(L)
            if idx is None:
                return
            scanner.refresh(idx, path, d, self.settings)

    def _hook_job_done(self, job):
        self.invalidate()
        letters = set((job.get("freed") or {}).keys()) | set((job.get("added") or {}).keys())
        for L in letters:
            self.save_index(L)
        if self._meta_dirty:
            self._save_meta()

    def expand_items(self, paths_):
        """UI 경로 → ops 항목. 시퀀스 표시 경로는 {'label','paths'} 묶음으로."""
        if not isinstance(paths_, list) or not paths_:
            raise ApiError(400, "empty", "고른 항목이 없어요.")
        out = []
        with self.lock:
            for p in paths_:
                if not isinstance(p, str) or not p or "\0" in p:
                    raise ApiError(400, "invalid", "경로가 올바르지 않아요.")
                _d, idx = self.index_for_path(p)
                r = model.find_item(idx, p) if idx is not None else None
                if r is not None and r[0] == "file" and model.is_seq(idx, r[1]):
                    fid = r[1]
                    frames = model.frames_of(idx, fid)
                    if not frames:
                        raise ApiError(400, "not_found", "시퀀스 파일을 찾을 수 없어요. 다시 스캔해 주세요.",
                                       path=p)
                    if len(frames) > idx.f_count[fid]:
                        # 스캔 뒤 프레임이 늘었다: 사용자가 본 것보다 많이 다루지 않는다
                        raise ApiError(400, "changed", "스캔한 뒤 시퀀스 프레임이 늘었어요. 다시 스캔한 뒤 해 주세요.",
                                       path=p)
                    out.append({"label": model.path_of_file(idx, fid), "paths": frames})
                else:
                    out.append(p)
        return out

    @staticmethod
    def _disk_totals(path):
        """(바이트, 파일 수) — 영구 삭제가 실제로 지울 것(ops 와 같은 걷기: 링크는 따라가지 않는다). 못 읽으면 None."""
        try:
            st = os.lstat(winapi.long_path(path))
        except OSError:
            return None
        if stat_mod.S_ISDIR(st.st_mode):
            files, _dirs, _skipped = ops._scan_tree(path, safety.mod_for(path))
            return sum(z for _p, z in files), len(files)
        if stat_mod.S_ISREG(st.st_mode):
            return st.st_size, 1
        return None

    def check_growth(self, items, expect=None):
        """영구 삭제 직전 — 확인창이 보여 준 양보다 디스크에 더 있으면 시작하지 않는다(시퀀스의 「프레임이 늘었어요」와 같은 규칙).

        확인창의 숫자는 inspect() = 스캔 인덱스다. 스캔 뒤 동료가 넣은 파일까지 「N개 항목(작은 크기)」 동의로
        지우면 안 된다. expect: UI 가 확인창에 보여 준 [{path, bytes, files}] (없으면 인덱스 값으로 본다).
        """
        exp = {}
        if isinstance(expect, list):
            for e in expect[:5000]:
                if not isinstance(e, dict) or not isinstance(e.get("path"), str):
                    continue
                b, n = e.get("bytes"), e.get("files")
                if (isinstance(b, int) and not isinstance(b, bool) and b >= 0
                        and isinstance(n, int) and not isinstance(n, bool) and n >= 0):
                    exp[os.path.normcase(os.path.normpath(e["path"]))] = (b, n)
        grown, caps = [], {}
        for it in items:
            if not isinstance(it, str):
                continue                          # 시퀀스 묶음 — expand_items 가 이미 프레임 수를 봤다
            if not self._safety_check(it)[0]:
                continue                          # 보호된 항목 — submit_delete 가 통째로 거부한다(걸어 볼 필요도 없다)
            with self.lock:
                _d, idx = self.index_for_path(it)
                r = model.find_item(idx, it) if idx is not None else None
                if r is None:
                    base = None
                elif r[0] == "dir":
                    base = (idx.d_size[r[1]], idx.d_files[r[1]])
                else:
                    base = (idx.f_size[r[1]], idx.f_count[r[1]])
            limits = [x for x in (base, exp.get(os.path.normcase(os.path.normpath(it)))) if x is not None]
            if not limits:
                continue                          # 인덱스에 없는 작은 파일 — 확인창도 크기를 말하지 않았다
            lb, ln = min(x[0] for x in limits), min(x[1] for x in limits)
            caps[it] = (lb, ln)
            disk = self._disk_totals(it)
            if disk is None:
                continue                          # 없거나 못 읽는다 — 작업이 항목별로 알린다
            if disk[0] > lb or disk[1] > ln:
                grown.append({"path": it, "bytes": disk[0], "files": disk[1],
                              "confirmed_bytes": lb, "confirmed_files": ln})
        if grown:
            raise ApiError(409, "changed",
                           "스캔한 뒤 내용이 늘었어요(또는 스캔에서 뺀 폴더가 들어 있어요). "
                           "다시 스캔해서 확인한 뒤 지워 주세요.",
                           paths=[g["path"] for g in grown], grown=grown)
        return caps

    # ── 스캔 ──
    def start_scans(self, letters):
        started, skipped = [], []
        for L in letters:
            d = self.drive(L)
            if d is None:
                if self.any_drive(L) is not None:
                    skipped.append({"drive": _dkey(L), "reason": "연결되지 않은 장치예요."})
                continue
            if not _connected(d):
                skipped.append({"drive": _letter(d), "reason": "연결되지 않은 장치예요."})
                continue
            if scanner.is_scanning(_letter(d)):
                continue
            with self.lock:
                self._scan_dirty[_letter(d)] = {}          # 스캔 동안 작업이 바꾼 경로(스캔 시작 전부터 모은다)
            try:
                h = scanner.start_scan(d, self.settings, self._scan_cb)
            except scanner.ScanBusyError:
                with self.lock:
                    self._scan_dirty.pop(_letter(d), None)
                continue
            with self.lock:
                p = dict(h.progress)
                if p.get("phase") in ("done", "error", "canceled"):
                    p["phase"] = "finalize"       # 작은 드라이브는 여기 오기 전에 끝난다 — 끝 표시는 _scan_watch 만 한다
                if (self.scans.get(_letter(d)) or {}).get("phase") != "finalize":
                    self.scans[_letter(d)] = p
            threading.Thread(target=self._scan_watch, args=(h, d), daemon=True).start()
            started.append(_letter(d))
        return started, skipped

    def _scan_cb(self, p):
        L = _dkey(str(p.get("letter") or ""))
        with self.lock:
            prev = self.scans.get(L)
            if prev and prev.get("phase") in ("done", "error", "canceled") and p.get("phase") in ("scan", "finalize"):
                return
            p = dict(p)
            if p.get("phase") in ("done", "error", "canceled"):
                # 인덱스를 표에 넣고 저장할 때까지는 「정리 중」 — 끝 표시는 _scan_watch 가 한다(busy 가 일찍 풀리지 않게)
                p["phase"] = "finalize"
            self.scans[L] = p

    def _scan_watch(self, h, d):
        h.join()
        L = _letter(d)
        idx = h.result
        if idx is not None:
            idx.letter = L
            self.invalidate()
            try:
                total, free = winapi.disk_usage(d["root"])
            except Exception:
                total, free = d.get("total") or 0, d.get("free") or 0
            bb, _bc = self.bin_info(d)
            key = self._key(d)
            with self.lock:
                self._replay_dirty(L, idx, d)
                self.indexes[L] = idx
                self._offline.pop(L, None)
                if key:
                    self._loaded[key] = idx
                    self._disp_sk[L] = key
                try:
                    store.save_index(idx)
                except (OSError, ValueError):
                    pass
            if key:
                used = max(0, int(total) - int(free))
                self._meta_set(key, unscanned=used - int(idx.d_size[0]) - bb)
                self._save_meta()
            self._warm(idx)
        with self.lock:
            self._scan_dirty.pop(L, None)
            cur = self.scans.get(L)
            if cur is not None:
                cur = dict(cur)
                if cur.get("phase") in ("scan", "finalize"):
                    cur["phase"] = "error" if h.error else ("canceled" if h.canceled else "done")
                    if h.error:
                        cur["error"] = h.error
                cur["_ended"] = time.time()
                self.scans[L] = cur

    def _note_dirty(self, L, path):
        """self.lock 을 쥔 채로. 그 드라이브를 통째로 스캔하는 중이면 — 스캔 결과는 이 변경 전에 걸은 부분(또는
        옛 분류 규칙)을 담고 있을 수 있다 — 결과를 표에 넣기 직전에 이 경로를 다시 맞춘다(_scan_watch).
        안 그러면 지운·옮긴 것이 되살아나고 복사한 것이 사라진다."""
        pend = self._scan_dirty.get(L)
        if pend is not None:
            pend.setdefault(os.path.normcase(os.path.normpath(path)), path)

    def _replay_dirty(self, L, idx, d):
        """self.lock 을 쥔 채로: 스캔 동안 작업이 바꾼 경로를 새 인덱스에 다시 맞춘다(디스크를 다시 본다)."""
        pend = self._scan_dirty.pop(L, None) or {}
        for p in pend.values():
            try:
                scanner.refresh(idx, p, d, self.settings)
            except Exception:
                pass

    def cancel_scan(self, L):
        h = scanner.get_scan(_dkey(str(L)) or str(L))
        if h is not None and not h.done:
            h.cancel()
            return True
        return False

    # ── 상태 ──
    def drive_state(self, d):
        L = _letter(d)
        conn = _connected(d)
        sk = self._key(d)
        dev = (self._meta_get(sk).get("dev") or {}) if sk else {}
        total = int(d.get("total") or 0)
        free = int(d.get("free") or 0)
        if not conn and not total:
            total, free = int(dev.get("total") or 0), int(dev.get("free") or 0)
        used = max(0, total - free)
        bb, bc = self.bin_info(d)
        with self.lock:
            idx = self.indexes.get(L)
            s = self._summary(idx) if idx is not None and idx.d_name else None
        out = {"letter": L, "root": d.get("root"), "label": d.get("label") or "",
               "serial": d.get("serial") or "", "fs": d.get("fs") or "", "kind": d.get("kind") or "unknown",
               "external": bool(d.get("external")), "has_bin": d.get("has_bin") is not False,
               "scan": bool(d.get("scan")), "connected": conn, "unc": d.get("unc"),
               "total": total, "free": free, "used": used,
               "scanned": s is not None, "scanned_at": s["scanned_at"] if s else None,
               "scan_seconds": s["scan_seconds"] if s else None,
               "cats": s["cats"] if s else [0] * NCAT,
               "cat_files": s["cat_files"] if s else [0] * NCAT,
               "scanned_bytes": s["scanned_bytes"] if s else 0,
               "files": s["files"] if s else 0, "dirs": s["dirs"] if s else 0,
               "errors": s["errors"] if s else 0,
               "bin_bytes": bb, "bin_count": bc, "stale": False,
               "also_as": list(d.get("also_as") or [])}
        if not conn:
            out["last_seen"] = d.get("last_seen") or dev.get("last_seen")
            if d.get("last_letter"):
                out["last_letter"] = d["last_letter"]
        sb = out["scanned_bytes"]
        out["unscanned_bytes"] = max(0, used - sb - bb)
        if s is not None and conn and sk:
            base = self._meta_get(sk)
            un = used - sb - bb
            if isinstance(base.get("unscanned"), (int, float)):
                delta = abs(un - base["unscanned"])
                out["stale"] = delta > min(STALE_ABS, STALE_REL * max(used, 1))
            else:
                self._meta_set(sk, unscanned=un)
        return out

    def _summary(self, idx):
        """model.summary 는 표 전체를 훑는다 → 인덱스가 바뀔 때만 다시 계산(폴링이 싸야 한다)."""
        sig = (getattr(idx, "_ver", 0), len(idx.d_name), len(idx.f_name), idx.scanned_at)
        hit = getattr(idx, "_db_summary", None)
        if hit is None or hit[0] != sig:
            hit = (sig, model.summary(idx))
            idx._db_summary = hit
        return hit[1]

    def drive_states(self, include_offline=True):
        ds = self.all_drives() if include_offline else self.drives()
        return [self.drive_state(d) for d in ds]

    def preview_state(self):
        now = time.monotonic()
        t, st = self._stats
        if st is None or now - t > STATS_TTL:
            st = self.previews.stats()
            self._stats = (now, st)
        done = []
        for e in st.get("done") or []:
            done.append({"kind": e.get("kind"), "path": e.get("path"), "t": e.get("t")})
        return {"ffmpeg": st.get("ffmpeg"), "version": self.previews.version, "count": st.get("count"),
                "bytes": st.get("bytes"), "limit_bytes": st.get("limit_bytes"),
                "queued": st.get("queued"), "running": st.get("running"), "done": done}

    def state(self):
        drives = self.drive_states()
        now = time.time()
        with self.lock:
            for L in [k for k, v in self.scans.items() if v.get("_ended") and now - v["_ended"] > SCAN_KEEP]:
                del self.scans[L]
            scans = {L: {k: v for k, v in s.items() if not k.startswith("_")} for L, s in self.scans.items()}
        active = self.queue.active()
        done = [j for j in self.queue.jobs(len(active) + 20) if j["status"] not in ("queued", "running")]
        done.sort(key=lambda j: j.get("finished_at") or 0)
        busy = bool(active) or any(s.get("phase") in ("scan", "finalize") for s in scans.values())
        return {"ok": True, "now": now, "drives": drives, "scans": scans,
                "jobs": list(reversed(active)) + done[-10:], "busy": busy,
                "update": self.update_state(), "previews": self.preview_state()}

    # ── 행 변환 ──
    def _item(self, d, row, metric=None):
        L = _letter(d)
        kind = row["kind"]
        if kind == "file" and row.get("seq"):
            kind = "seq"
        cat = row.get("cat", cats_mod.OTHER)
        if not _connected(d) or L in self._offline:
            prot, reason = True, R_OFFLINE
        elif kind == "loose":
            prot, reason = True, R_LOOSE
        elif kind == "dir":
            prot, reason = self.protected(row["path"], APP if cat == APP else None)
        else:
            prot, reason = self.protected(row["path"], cat)
        it = {"key": "%s:%s:%d" % (L, kind, row["id"]), "drive": L, "kind": kind, "id": row["id"],
              "name": row["name"], "path": row["path"], "size": row["size"],
              "metric": row.get("metric", row["size"]) if metric is None else metric,
              "cat": cat, "cat_bytes": list(row.get("cat_bytes") or [0] * NCAT),
              "files": row.get("files", 0), "mtime": row.get("mtime"),
              "protected": prot, "protected_reason": reason}
        if kind == "seq":
            it["count"] = row.get("count", row.get("files", 0))
            if "first" in row:
                it["first"], it["last"] = row["first"], row["last"]
        return it

    def _root_row(self, d, idx, cat):
        cb = model.cat_totals(idx, 0)
        size = idx.d_size[0]
        return {"kind": "dir", "id": 0, "name": _drive_name(d), "path": idx.root, "size": size,
                "metric": size if cat is None else cb[cat], "cat_bytes": cb, "files": idx.d_files[0],
                "mtime": idx.d_newest[0], "cat": dominant(cb)}

    def _crumbs(self, d, idx, did):
        chain = []
        x = did
        while x >= 0:
            chain.append(x)
            x = idx.d_parent[x]
        out = []
        for x in reversed(chain):
            out.append({"dir": x, "name": _drive_name(d) if x == 0 else idx.d_name[x],
                        "path": model.path_of_dir(idx, x)})
        return out

    @staticmethod
    def _valid_dir(idx, did):
        return 0 <= did < len(idx.d_name) and idx.d_alive[did]

    def _live_indexes(self):
        """연결된 드라이브의 인덱스만 (추천·중복·전체 목록용)."""
        live = {_letter(d) for d in self.drives() if _connected(d)}
        with self.lock:
            return {L: idx for L, idx in self.indexes.items() if L in live and L not in self._offline}

    # ── 질의 ──
    def items(self, qs):
        drive = (qs.get("drive") or "*").strip()
        view = qs.get("view") or "files"
        if view not in ("files", "folders"):
            raise ApiError(400, "invalid", "보기 종류가 올바르지 않아요.")
        cat = cats_mod.cat_id(qs.get("cat")) if (qs.get("cat") or "") != "" else None
        did = _int(qs.get("dir"), 0, 0)
        age = _int(qs.get("age"), 0, 0, 36500)
        q = (qs.get("q") or "").strip().lower()
        sort = qs.get("sort") if qs.get("sort") in ("size", "mtime", "name") else "size"
        order = "asc" if qs.get("order") == "asc" else "desc"
        offset = _int(qs.get("offset"), 0, 0)
        limit = _int(qs.get("limit"), 200, 0, 500)
        now = time.time()
        crumbs, remainder = [], {"bytes": 0, "files": 0}
        if drive == "*":
            live_drives = [d for d in self.drives() if _connected(d)]
            live_idx = self._live_indexes()
        else:
            d0 = self.any_drive(drive)
            if d0 is None:
                raise ApiError(404, "unknown_drive", "드라이브를 찾을 수 없어요.")
        with self.lock:
            if drive == "*":
                targets = [(d, live_idx[_letter(d)]) for d in live_drives if _letter(d) in live_idx]
            else:
                d = d0
                idx = self.indexes.get(_letter(d))
                if idx is None:
                    raise ApiError(409, "not_scanned", "아직 스캔하지 않은 드라이브예요.")
                if not self._valid_dir(idx, did):
                    did = 0
                targets = [(d, idx)]
            rows = []
            if view == "folders" and drive == "*":
                for d, idx in targets:
                    r = self._root_row(d, idx, cat)
                    if cat is not None and r["metric"] <= 0:
                        continue
                    if q and q not in r["name"].lower():
                        continue
                    rows.append((d, r))
                total = len(rows)
            elif view == "folders":
                d, idx = targets[0]
                crumbs = self._crumbs(d, idx, did)
                kids = model.list_children(idx, did, cat, age, now)
                if q:
                    kids = [r for r in kids if r["kind"] != "loose" and q in r["name"].lower()]
                loose = [r for r in kids if r["kind"] == "loose"]
                rest = [r for r in kids if r["kind"] != "loose"]
                rev = order != "asc"
                if sort == "mtime":
                    rest.sort(key=lambda r: r.get("mtime") or 0, reverse=rev)
                elif sort == "name":
                    rest.sort(key=lambda r: r["name"].lower(), reverse=rev)
                else:
                    rest.sort(key=lambda r: r["metric"], reverse=rev)
                rows = [(d, r) for r in rest + loose]
                total = len(rows)
            else:
                merged, total, tb = [], 0, 0
                for d, idx in targets:
                    base = did if drive != "*" else 0
                    lst, n, b = model.list_files(idx, base, cat, age, q, sort, order,
                                                 0 if drive == "*" else offset,
                                                 offset + limit if drive == "*" else limit, now)
                    total += n
                    tb += b
                    merged.extend((d, r) for r in lst)
                    if not q and age == 0:
                        rb, rf = model.small_remainder(idx, base, cat)
                        remainder["bytes"] += rb
                        remainder["files"] += rf
                    if drive != "*":
                        crumbs = self._crumbs(d, idx, base)
                if drive == "*":
                    rev = order != "asc"
                    if sort == "mtime":
                        merged.sort(key=lambda t: t[1].get("mtime") or 0, reverse=rev)
                    elif sort == "name":
                        merged.sort(key=lambda t: t[1]["name"].lower(), reverse=rev)
                    else:
                        merged.sort(key=lambda t: t[1]["metric"], reverse=rev)
                    merged = merged[offset:offset + limit]
                items = [self._item(d, r) for d, r in merged]
                return {"ok": True, "total": total, "total_bytes": tb, "offset": offset,
                        "crumbs": crumbs, "remainder": remainder, "items": items}
            tb = sum(r["metric"] for _d, r in rows)
            page = rows[offset:offset + limit]
            items = [self._item(d, r) for d, r in page]
        return {"ok": True, "total": total, "total_bytes": tb, "offset": offset,
                "crumbs": crumbs, "remainder": remainder, "items": items}

    def tree(self, qs):
        L = _dkey(qs.get("drive") or "")
        did = _int(qs.get("dir"), 0, 0)
        depth = _int(qs.get("depth"), 2, 1, 3)
        d = self.any_drive(L) if L else None
        if d is None:
            raise ApiError(404, "unknown_drive", "드라이브를 찾을 수 없어요.")
        with self.lock:
            idx = self.indexes.get(L)
            if idx is None:
                raise ApiError(409, "not_scanned", "아직 스캔하지 않은 드라이브예요.")
            if not self._valid_dir(idx, did):
                did = 0

            def node(x, lv):
                cb = model.cat_totals(idx, x)
                n = {"id": x, "kind": "dir", "name": _drive_name(d) if x == 0 else idx.d_name[x],
                     "path": model.path_of_dir(idx, x), "size": idx.d_size[x], "cat": dominant(cb),
                     "cat_bytes": cb, "files": idx.d_files[x], "mtime": idx.d_newest[x]}
                if lv >= depth:
                    return n
                rows = model.list_children(idx, x)
                loose = [r for r in rows if r["kind"] == "loose"]
                rest = [r for r in rows if r["kind"] != "loose" and r["size"] > 0]
                rest.sort(key=lambda r: -r["size"])
                out = []
                for r in rest[:200]:
                    if r["kind"] == "dir":
                        out.append(node(r["id"], lv + 1))
                    else:
                        k = "seq" if r.get("seq") else "file"
                        c = {"id": r["id"], "kind": k, "name": r["name"], "path": r["path"],
                             "size": r["size"], "cat": r["cat"], "cat_bytes": r["cat_bytes"],
                             "files": r["files"], "mtime": r["mtime"]}
                        if k == "seq":
                            c["count"] = r["count"]
                        out.append(c)
                tail = rest[200:]
                if tail:
                    out.append({"name": "기타 %d개" % len(tail), "size": sum(r["size"] for r in tail),
                                "cat": cats_mod.OTHER, "other": True})
                for r in loose:
                    if r["size"] > 0:
                        out.append({"name": "작은 파일 %d개" % r["files"], "size": r["size"],
                                    "cat": r["cat"], "cat_bytes": r["cat_bytes"], "other": True})
                n["children"] = out
                return n

            return {"ok": True, "node": node(did, 0), "connected": _connected(d) and L not in self._offline}

    def inspect(self, paths_):
        if not isinstance(paths_, list):
            raise ApiError(400, "invalid", "경로 목록이 올바르지 않아요.")
        out, tcb, total, tf = [], [0] * NCAT, 0, 0
        for p in paths_[:5000]:
            if not isinstance(p, str):
                continue
            with self.lock:
                _d, idx = self.index_for_path(p)
                r = model.find_item(idx, p) if idx is not None else None
                if r is not None and r[0] == "dir":
                    i = r[1]
                    cb = model.cat_totals(idx, i)
                    kind, size, files = "dir", idx.d_size[i], idx.d_files[i]
                    cat = APP if dominant(cb) == APP else None
                elif r is not None:
                    i = r[1]
                    cb = [0] * NCAT
                    cb[idx.f_cat[i]] = idx.f_size[i]
                    kind = "seq" if model.is_seq(idx, i) else "file"
                    size, files, cat = idx.f_size[i], idx.f_count[i], idx.f_cat[i]
                else:
                    cb, kind, size, files, cat = [0] * NCAT, "file", 0, 0, None
            exists = r is not None or os.path.lexists(p)
            prot, reason = self.protected(p, cat)
            if not prot and _d is not None and not _connected(_d):
                prot, reason = True, R_OFFLINE
            out.append({"path": p, "kind": kind, "size": size, "files": files, "cat_bytes": cb,
                        "protected": prot, "protected_reason": reason, "exists": bool(exists)})
            total += size
            tf += files
            for k in range(NCAT):
                tcb[k] += cb[k]
        return {"ok": True, "items": out, "total": total, "total_files": tf, "cat_bytes": tcb}

    def dupe_groups(self, min_bytes=50 << 20):
        live = self._live_indexes()
        with self.lock:
            groups = dupes_mod.find_dupes(live, min_bytes)
        with self.lock:
            for g in groups:
                ent = self.verified.get(g["id"])
                if ent is None:
                    continue
                if dupes_mod.fingerprint_matches(ent.get("fp"), g):
                    g["verified"] = ent.get("res")
                else:
                    # 확인한 뒤 구성원·크기·수정 시각이 바뀌었다(다시 내보낸 파일, 새로 나타난 같은 이름의 파일…)
                    # → 옛 「내용 같음」을 이어 붙이지 않는다(미확인)
                    del self.verified[g["id"]]
        return groups

    def recommendations(self):
        states = [s for s in self.drive_states(include_offline=False) if s["connected"]]
        groups = self.dupe_groups()
        live = self._live_indexes()
        with self.lock:
            return recommend.build(live, states, self.settings, groups)

    # ── 장치 · NAS ──
    def add_location(self, path, label=None):
        if not isinstance(path, str) or not path.strip() or len(path) > 4096 or "\0" in path:
            raise ApiError(400, "invalid_unc", "네트워크 경로를 입력해 주세요. 예: \\\\ORICO\\공유이름")
        u = winapi.normalize_unc(path)
        if not u:
            s = path.strip()
            host = winapi.normalize_host(s) if s.startswith(("\\\\", "//")) else None
            if host and "\\" not in s.replace("/", "\\").strip("\\"):
                raise ApiError(400, "need_share",
                               "공유 폴더 이름까지 넣어 주세요(예: \\\\%s\\공유이름). [공유 찾기]로 고를 수도 있어요." % host,
                               host=host)
            raise ApiError(400, "invalid_unc", "네트워크 경로 형식이 아니에요. 예: \\\\ORICO\\공유이름")
        if safety.is_local_unc(u):
            # 이 PC 자신의 공유(\\localhost\C$·\\내PC이름\…)는 시스템 폴더 보호가 안 걸린다 — 등록부터 받지 않는다
            raise ApiError(400, "local_share", safety.R_LOCAL_SHARE, unc=u)
        locs = list(self.settings.get("network_locations") or [])
        key = winapi.network_key(u)
        if any(winapi.network_key(x.get("path")) == key for x in locs):
            raise ApiError(409, "exists", "이미 추가한 위치예요.", key=key)
        if len(locs) >= settings_mod.MAX_NETWORK:
            raise ApiError(400, "too_many", "네트워크 위치는 %d개까지 추가할 수 있어요." % settings_mod.MAX_NETWORK)
        if label is not None and not isinstance(label, str):
            raise ApiError(400, "invalid", "이름이 올바르지 않아요.")
        info = winapi.location_info(u) or {}
        lab = (label or "").strip()[:64] or info.get("label") or u[2:].split("\\")[-1]
        self.settings = settings_mod.save({"network_locations": locs + [{"path": u, "label": lab}]})
        self.invalidate()
        self._resync()
        d = self.drive(key)
        msg = None if info.get("connected") else "지금은 연결할 수 없어요. 장치가 켜져 있는지, 경로가 맞는지 확인해 주세요."
        if d is None:
            twin = next((x for x in self.drives() if key in (x.get("also_as") or ())), None)
            if twin is not None:
                d = twin
                msg = "이 공유는 이미 %s 로 연결돼 있어요. 한 번만 세도록 그 카드에 함께 보여요." % _drive_name(twin)
        return {"ok": True, "key": key, "unc": u, "label": lab, "connected": bool(info.get("connected")),
                "message": msg, "drive": self.drive_state(d) if d else None, "settings": self.settings}

    def remove_location(self, key):
        k = winapi.norm_key(key) if isinstance(key, str) else ""
        locs = list(self.settings.get("network_locations") or [])
        hit = [x for x in locs if k and winapi.network_key(x.get("path")) == k]
        if not hit:
            raise ApiError(404, "not_found", "그 네트워크 위치를 찾을 수 없어요.")
        if scanner.is_scanning(k):
            raise ApiError(409, "busy", "스캔 중이에요. 스캔을 취소한 뒤 해 주세요.")
        if self.queue.active():
            raise ApiError(409, "busy", "진행 중인 작업이 끝난 뒤에 해 주세요.")
        d = self.drive(k)
        sk = self._key(d) if d else None
        ds = {x: v for x, v in (self.settings.get("drive_scan") or {}).items() if _dkey(x) != k}
        self.settings = settings_mod.save({"network_locations": [x for x in locs if x not in hit],
                                           "drive_scan": ds})
        if sk:
            self._drop_saved(sk, k)
        self.invalidate()
        self._resync()
        return {"ok": True, "removed": k, "settings": self.settings}

    def _drop_saved(self, sk, disp):
        """저장된 인덱스와 장치 기록만 지운다(사용자 파일은 건드리지 않는다)."""
        store.delete_index(sk)
        with self._mlock:
            self._meta.pop(sk, None)
            self._meta_dirty = True
        self._save_meta()
        with self.lock:
            self._loaded.pop(sk, None)
            self._offline.pop(disp, None)
            if self._disp_sk.get(disp) == sk:
                self.indexes.pop(disp, None)
                self._disp_sk.pop(disp, None)
            self._pcache.pop(disp, None)
            self.scans.pop(disp, None)

    def list_shares(self, host):
        h = winapi.normalize_host(host) if isinstance(host, str) else None
        if not h:
            raise ApiError(400, "invalid_host", "장치 이름이나 IP 주소를 입력해 주세요. 예: ORICO 또는 192.168.0.10")
        shares, err = winapi.list_network_shares(h)
        if err and not shares:
            raise ApiError(400, "no_shares", err + " 공유 이름까지 직접 입력해 주세요.", host=h, shares=[])
        reg = {winapi.network_key(x.get("path")) for x in self.settings.get("network_locations") or []}
        out = []
        for s in shares:
            k = winapi.network_key(s.get("unc"))
            out.append({"name": s.get("name"), "unc": s.get("unc"), "key": k, "added": k in reg})
        return {"ok": True, "host": h, "shares": out}

    def scan_toggle(self, key, on):
        if not isinstance(on, bool):
            raise ApiError(400, "invalid", "scan 값은 true/false 여야 해요.")
        d = self.drive(key)
        if d is None:
            raise ApiError(404, "unknown_drive", "드라이브를 찾을 수 없어요.")
        L = _letter(d)
        ds = {x: v for x, v in (self.settings.get("drive_scan") or {}).items() if _dkey(x) != L}
        ds[L] = on
        self.settings = settings_mod.save({"drive_scan": ds})
        self.invalidate()
        d = self.drive(L)
        return {"ok": True, "drive": self.drive_state(d) if d else None, "settings": self.settings}

    def forget(self, key):
        L = _dkey(key)
        d = self.any_drive(L) if L else None
        if d is None:
            raise ApiError(404, "unknown_drive", "드라이브를 찾을 수 없어요.")
        if _connected(d) and L not in self._offline:
            raise ApiError(400, "connected", "연결된 장치의 기록은 지울 수 없어요. 다시 스캔해 주세요.")
        if scanner.is_scanning(L):
            raise ApiError(409, "busy", "스캔 중이에요.")
        with self.lock:
            sk = self._disp_sk.get(L)
        if not sk:
            sk = self._key(d)
        if not sk:
            raise ApiError(404, "not_found", "저장된 기록이 없어요.")
        self._drop_saved(sk, L)
        self._resync()
        return {"ok": True, "forgotten": L}

    def pick_folder(self, initial=None):
        w = self.window
        if w is None:
            raise ApiError(409, "no_dialog", "폴더 선택 창을 열 수 없어요. 경로를 직접 입력해 주세요.")
        if not self._dialog_lock.acquire(blocking=False):
            raise ApiError(409, "busy", "폴더 선택 창이 이미 열려 있어요.")
        try:
            try:
                import webview  # type: ignore
                kind = getattr(webview, "FOLDER_DIALOG", None)
                if kind is None:
                    kind = webview.FileDialog.FOLDER
            except Exception:
                kind = 20                       # pywebview FOLDER_DIALOG
            kw = {}
            if isinstance(initial, str) and initial and winapi.path_alive(initial):   # 죽은 NAS 에서 SMB 시간 제한만큼 멈추지 않게
                kw["directory"] = initial
            try:
                # pywebview 가 창의 UI 스레드로 넘겨 띄우고, 닫힐 때까지 기다린다
                res = w.create_file_dialog(kind, **kw)
            except Exception as e:  # noqa: BLE001
                raise ApiError(500, "dialog_failed", "폴더 선택 창을 띄우지 못했어요: %s" % e)
        finally:
            self._dialog_lock.release()
        if not res:
            return {"ok": True, "path": None, "canceled": True}
        p = res[0] if isinstance(res, (list, tuple)) else res
        if not isinstance(p, str) or not p:
            return {"ok": True, "path": None, "canceled": True}
        p = safety.expand_short_names(p)
        d = self.drive_for_path(p)
        return {"ok": True, "path": p, "canceled": False, "drive": _letter(d) if d else None,
                "known": d is not None}

    # ── 작업 ──
    def submit_verify(self, group_ids):
        ids = set(str(x) for x in group_ids) if isinstance(group_ids, list) and group_ids else None

        def fn(ctx):
            groups = [g for g in self.dupe_groups() if ids is None or g["id"] in ids]
            ctx.progress(files_total=len(groups), files_done=0, bytes_total=sum(g["size"] * len(g["items"]) for g in groups))
            same = diff = 0
            done_b = 0
            for i, g in enumerate(groups):
                if ctx.cancelled():
                    break
                ctx.progress(current=g["name"])
                res = dupes_mod.verify_group(g, cancel=ctx.cancelled)
                if ctx.cancelled():
                    break
                with self.lock:
                    self.verified[g["id"]] = {"res": res, "fp": dupes_mod.fingerprint(g)}
                if res is True:
                    same += 1
                elif res is False:
                    diff += 1
                ctx.result(g["name"], res is not None,
                           error=None if res is not None else "일부 파일을 읽지 못했어요.")
                done_b += g["size"] * len(g["items"])
                ctx.progress(files_done=i + 1, bytes_done=done_b)
            ctx.message("중복 확인을 마쳤어요. 내용이 같은 묶음 %d개, 다른 묶음 %d개예요." % (same, diff))
            return {"same": same, "diff": diff}

        return self.queue.submit_task("verify_dupes", "중복 파일 내용 확인", fn)

    def submit_rescan(self, path, title):
        d = self.drive_for_path(path)
        if d is None:
            raise ApiError(400, "unknown_drive", "드라이브를 찾을 수 없어요.")
        L = _letter(d)

        def fn(ctx):
            ctx.progress(current=path)
            sub = scanner.scan_subtree(path, d, self.settings) if os.path.isdir(path) else None
            with self.lock:
                self._note_dirty(L, path)
                idx = self.indexes.get(L)
                if idx is not None and sub is not None:
                    model.graft(idx, sub, path)
                    try:
                        store.save_index(idx)
                    except (OSError, ValueError):
                        pass
            ctx.result(path, True)
            ctx.message("분류 규칙을 지우고 그 폴더를 다시 스캔했어요.")

        return self.queue.submit_task("rescan", title, fn)

    def _apply_rules(self, rules):
        """규칙을 인덱스에 바로 반영한다(다시 스캔 없이). ★가장 깊은 규칙이 이긴다(SPEC §4, Classifier.dir_ctx):
        model.apply_rule 은 하위 트리 전체를 덮으므로 얕은 규칙부터 적용하고, 적용한 폴더 안에 저장된 다른 규칙은
        그 뒤에 다시 적용해 되살린다 — 안 그러면 부모 규칙이 자식 규칙을 지우고, 다음 스캔에서 다시 뒤집힌다."""
        todo = []
        for r in rules:
            p, c = r.get("path"), cats_mod.cat_id(r.get("cat"))
            if isinstance(p, str) and p and c is not None:
                todo.append((p, c))
        if not todo:
            return
        saved = []
        for r in self.settings.get("rules") or []:
            p, c = r.get("path"), cats_mod.cat_id(r.get("cat"))
            if isinstance(p, str) and p and c is not None:
                saved.append((p, c))

        def depth(p):
            return os.path.normpath(p).rstrip(os.sep).count(os.sep)

        plan, seen = [], set()
        for p, c in todo:
            plan.append((p, c))
            for q, qc in saved:
                rel = cats_mod.rel_parts(p, q)
                if rel:                                       # q 는 p 안의 더 깊은 규칙
                    plan.append((q, qc))
        out = []
        for p, c in sorted(plan, key=lambda pc: depth(pc[0])):     # 얕은 것부터(정렬은 안정적 — 같은 깊이는 순서대로)
            k = os.path.normcase(os.path.normpath(p))
            if k in seen:
                out = [x for x in out if os.path.normcase(os.path.normpath(x[0])) != k]
            seen.add(k)
            out.append((p, c))
        touched = {}
        for p, c in out:
            with self.lock:
                _d, idx = self.index_for_path(p)
                if _d is not None:
                    self._note_dirty(_letter(_d), p)      # 스캔 중이면 그 결과는 옛 규칙으로 분류돼 있다
                if idx is not None and model.find_dir(idx, p) is not None:
                    model.apply_rule(idx, p, c)
                    touched[id(idx)] = idx
        for idx in touched.values():
            with self.lock:
                try:
                    store.save_index(idx)
                except (OSError, ValueError):
                    pass

    def add_rule(self, path, cat):
        c = cats_mod.cat_id(cat)
        if c is None or not isinstance(path, str) or not path:
            raise ApiError(400, "invalid", "카테고리나 경로가 올바르지 않아요.")
        with self.lock:
            _d, idx = self.index_for_path(path)
            r = model.find_item(idx, path) if idx is not None else None
            if r is None or r[0] != "dir":
                raise ApiError(400, "invalid", "폴더를 찾을 수 없어요.")
            path = model.path_of_dir(idx, r[1])
        rules = [x for x in self.settings.get("rules", []) if not _same_path(x.get("path", ""), path)]
        rules.append({"path": path, "cat": c})
        self._save_settings({"rules": rules})
        self._apply_rules([{"path": path, "cat": c}])
        return self.settings

    def delete_rule(self, path):
        if not isinstance(path, str) or not path:
            raise ApiError(400, "invalid", "경로가 올바르지 않아요.")
        old = self.settings.get("rules", [])
        rules = [x for x in old if not _same_path(x.get("path", ""), path)]
        if len(rules) == len(old):
            raise ApiError(404, "not_found", "그 규칙을 찾을 수 없어요.")
        self._save_settings({"rules": rules})
        jid = None
        if self.drive_for_path(path) is not None and os.path.isdir(path):
            jid = self.submit_rescan(path, "분류 규칙 삭제 · 다시 스캔")
        return jid, self.settings

    def _save_settings(self, partial):
        old = self.settings
        self.settings = settings_mod.save(partial)
        if any(old.get(k) != self.settings.get(k) for k in _PROJECT_KEYS):
            self._sgen += 1
        if any(old.get(k) != self.settings.get(k) for k in _DRIVE_KEYS):
            self.invalidate()
            self._need_sync = True
        if old.get("preview_cache_gb") != self.settings.get("preview_cache_gb"):
            self.previews.set_cache_gb(self.settings.get("preview_cache_gb"))
        if self.settings.get("update_check") is False and self.checker is not None:
            self.checker = None                   # 껐으면 알약도 바로 내린다
        elif old.get("update_check") is False and self.settings.get("update_check") is True:
            self.start_update_check()
        return self.settings

    def save_settings(self, partial):
        if not isinstance(partial, dict):
            raise ApiError(400, "invalid", "설정 형식이 올바르지 않아요.")
        partial = {k: v for k, v in partial.items() if k != "rules"}
        nl = partial.get("network_locations")
        if isinstance(nl, list) and any(isinstance(x, dict) and safety.is_local_unc(x.get("path")) for x in nl):
            raise ApiError(400, "local_share", safety.R_LOCAL_SHARE)
        return self._save_settings(partial)

    def reveal(self, path):
        if not isinstance(path, str) or self.drive_for_path(path) is None:
            raise ApiError(400, "invalid", "알려진 드라이브의 경로가 아니에요.")
        target = path
        if not os.path.lexists(path):
            with self.lock:
                _d, idx = self.index_for_path(path)
                r = model.find_item(idx, path) if idx is not None else None
                frames = model.frames_of(idx, r[1]) if r is not None and r[0] == "file" else []
            target = frames[0] if frames else os.path.dirname(path)
        return bool(winapi.reveal(target))

    def open_url(self, u):
        if not allowed_open_url(u):
            raise ApiError(400, "forbidden_url", "열 수 없는 주소예요.")
        try:
            ok = self.open_url_fn(u)
        except Exception:
            ok = False
        return {"ok": True, "opened": bool(ok)}

    # ── 팀 설정 ──
    def team_export(self):
        return settings_mod.export_team(self.settings)

    def _known_exists(self, path):
        """남이 준 경로가 이 PC 에 있는가 — 이미 아는(연결된) 드라이브 안일 때만 디스크를 본다.

        모르는 UNC 호스트를 stat 하면 Windows 가 그 호스트에 인증을 시도한다(NTLM 응답 유출 · SMB 시간 제한만큼 멈춤).
        아는 네트워크 드라이브도 죽어 있을 수 있어 시간 제한(path_alive)으로만 본다.
        """
        d = self.drive_for_path(path)
        if d is None or not _connected(d):
            return False
        if d.get("kind") == "network" or d.get("unc"):
            return winapi.path_alive(path)
        try:
            return os.path.exists(path)
        except (OSError, ValueError):
            return False

    def team_import(self, data, apply=False):
        if isinstance(data, str):
            if len(data) > BODY_MAX:
                raise ApiError(413, "too_large", "파일이 너무 커요.")
            try:
                data = json.loads(data)
            except ValueError:
                raise ApiError(400, "bad_team_file", "팀 설정 파일을 읽지 못했어요.")
        try:
            new, diff = settings_mod.import_team(self.settings, data, exists=self._known_exists)
        except ValueError as e:
            raise ApiError(400, "bad_team_file", "%s." % e)
        if not apply:
            return {"ok": True, "applied": False, "diff": diff}
        changed = [dict(x) for x in diff["rules"]["added"] + diff["rules"]["changed"] if x.get("exists")]
        self._save_settings({"workers": new["workers"], "client_aliases": new["client_aliases"],
                             "rules": new["rules"]})
        self._apply_rules(changed)
        return {"ok": True, "applied": True, "diff": diff, "settings": self.settings}

    # ── 프로젝트 ──
    def projects_all(self):
        with self.lock:
            snap = [(L, idx) for L, idx in self.indexes.items() if idx.d_name]
            offline = set(self._offline)
        live = {_letter(d): d for d in self.drives()}
        out = []
        for L, idx in snap:
            g = self._sgen
            sig = (id(idx), idx._ver, L, g)
            hit = self._pcache.get(L)
            if hit is None or hit[0] != sig:
                s = self.settings
                with self.lock:
                    if self.indexes.get(L) is not idx:
                        continue
                    d0 = live.get(L) or self._offline.get(L) or {}
                    recs = projects.detect_projects(
                        idx, s, cats_mod.Classifier(s, internal=scanner.is_internal(d0)))
                    sig = (id(idx), idx._ver, L, g)
                hit = (sig, recs)
                self._pcache[L] = hit
            d = live.get(L)
            conn = d is not None and _connected(d) and L not in offline
            if d is None:
                d = self._offline.get(L) or {"letter": L}
            lab = _drive_name(d)
            for r in hit[1]:
                out.append(dict(r, connected=conn, drive_label=lab, drive_kind=d.get("kind") or "unknown"))
        projects.unify_clients(out)
        return out

    def media_for(self, pv, live=True, probe=False, network=False):
        """{path,name,size,mtime,cat} → <media>. 연결 안 된 장치는 URL 없이."""
        if not pv:
            return None
        path = pv.get("path")
        cat = pv.get("cat")
        if not live or not isinstance(path, str):
            ext = cats_mod.split_ext(pv.get("name") or "")[1]
            return {"path": path, "name": pv.get("name"), "size": pv.get("size"), "mtime": pv.get("mtime"),
                    "cat": cat, "ext": ext, "playable": "none", "poster_url": None, "video_url": None,
                    "offline": True}
        if _unsafe_file(path):
            obj = media.media_object(path, self.media, None, info=None, cat=cat, ffprobe="")
            obj.update(playable="none", poster_url=None, video_url=None)
            return obj
        info = None
        if probe and self.ffprobe and not network:
            info = media.probe(path, self.ffprobe)
        if info is None:
            info = media.probe_cached(path)
        return media.media_object(path, self.media, self.previews, info=info, cat=cat, ffprobe="")

    def projects_view(self, qs):
        q = (qs.get("q") or "")[:200]
        drive = qs.get("drive") or "*"
        if drive != "*":
            dk = _dkey(drive)
            if not dk:
                raise ApiError(400, "invalid", "드라이브가 올바르지 않아요.")
            drive = dk
        res = projects.group_projects(self.projects_all(), group=qs.get("group") or "client", q=q,
                                      worker=(qs.get("worker") or "")[:50],
                                      client=(qs.get("client") or "")[:200],
                                      year=qs.get("year") or None, sort=qs.get("sort") or "recent",
                                      drive=None if drive == "*" else drive)
        nets = {_letter(d) for d in self.drives() if d.get("kind") == "network"}
        for g in res["groups"]:
            for p in g["projects"]:
                p["preview"] = self.media_for(p.get("preview"), live=p.get("connected"),
                                              network=p["drive"] in nets)
        with self.lock:
            res["scanned"] = sum(1 for idx in self.indexes.values() if idx.d_name)
        res["roots"] = list(self.settings.get("project_roots") or [])
        res["excluded"] = len(self.settings.get("project_excluded") or [])
        return res

    def project_detail(self, key, path=None):
        recs = self.projects_all()
        p = None
        if isinstance(key, str) and ":" in key:
            dk, _, num = key.rpartition(":")
            k = "%s:%s" % (_dkey(dk), num)
            p = next((r for r in recs if r["key"] == k), None)
        if p is None and isinstance(path, str) and path:
            p = next((r for r in recs if _same_path(r["path"], path)), None)
        if p is None:
            raise ApiError(404, "not_found", "프로젝트를 찾을 수 없어요. 목록을 새로 고쳐 주세요.")
        L = p["drive"]
        d = self.any_drive(L) or {"letter": L, "connected": False}
        live = p["connected"]
        network = d.get("kind") == "network"
        with self.lock:
            idx = self.indexes.get(L)
            if idx is None or not self._valid_dir(idx, p["did"]):
                raise ApiError(404, "not_found", "프로젝트를 찾을 수 없어요. 목록을 새로 고쳐 주세요.")
            root = idx.root
            rows = model.list_children(idx, p["did"])
            loose = [r for r in rows if r["kind"] == "loose"]
            rest = sorted([r for r in rows if r["kind"] != "loose"], key=lambda r: -r["size"])
            children = [self._item(d, r) for r in rest + loose]
        vids = []
        if live:
            try:
                vids = projects.project_videos(
                    p["path"], 200, self.settings, root=root,
                    classifier=cats_mod.Classifier(self.settings, internal=scanner.is_internal(d)))
            except Exception:
                vids = []
        if not vids and p.get("preview"):
            vids = [p["preview"]]
        t0 = time.monotonic()
        videos = []
        for i, v in enumerate(vids):
            probe = i < VIDEO_PROBE_MAX and time.monotonic() - t0 < VIDEO_PROBE_BUDGET
            videos.append(self.media_for(v, live=live, probe=probe, network=network))
        proj = dict(p)
        proj["preview"] = self.media_for(p.get("preview"), live=live, probe=True, network=network)
        return {"ok": True, "project": proj, "videos": videos, "cat_bytes": list(p["cat_bytes"]),
                "children": children}

    def _known_folder(self, path):
        """프로젝트 설정에 넣을 폴더: 스캔한 인덱스에 있거나, 알려진 드라이브 안의 실제 폴더."""
        with self.lock:
            for idx in self.indexes.values():
                if idx.d_name:
                    try:
                        did = model.find_dir(idx, path)
                    except Exception:
                        did = None
                    if did is not None:
                        return model.path_of_dir(idx, did)
        if self.drive_for_path(path) is not None and os.path.isdir(path):
            return os.path.normpath(path)
        return None

    def not_project(self, path, remove=False):
        _path_arg(path)
        cur = list(self.settings.get("project_excluded") or [])
        if remove:
            new = [x for x in cur if not _same_path(x, path)]
            if len(new) == len(cur):
                raise ApiError(404, "not_found", "그 폴더는 제외 목록에 없어요.")
        else:
            fp = self._known_folder(path)
            if fp is None:
                raise ApiError(400, "invalid", "폴더를 찾을 수 없어요.")
            new = [x for x in cur if not _same_path(x, fp)] + [fp]
        self._save_settings({"project_excluded": new})
        return {"ok": True, "settings": self.settings}

    def project_roots(self, b):
        cur = list(self.settings.get("project_roots") or [])
        if b.get("add") is not None:
            p = _path_arg(b.get("add"), "폴더")
            fp = self._known_folder(p)
            if fp is None:
                raise ApiError(400, "invalid", "폴더를 찾을 수 없어요. 스캔한 드라이브 안의 폴더를 골라 주세요.")
            new = [x for x in cur if not _same_path(x, fp)] + [fp]
        elif b.get("remove") is not None:
            p = _path_arg(b.get("remove"), "폴더")
            new = [x for x in cur if not _same_path(x, p)]
            if len(new) == len(cur):
                raise ApiError(404, "not_found", "그 폴더는 목록에 없어요.")
        else:
            raise ApiError(400, "invalid", "add 또는 remove 가 필요해요.")
        self._save_settings({"project_roots": new})
        return {"ok": True, "settings": self.settings}

    # ── 미디어 ──
    def media_path_ok(self, path):
        """/media 관문: 영상·이미지 확장자 + (미리보기 캐시 안 또는 연결된 알려진 드라이브 안) + 일반 파일."""
        if not isinstance(path, str) or not path:
            return False
        if not media.is_media_ext(cats_mod.split_ext(os.path.basename(path))[1]):
            return False
        if self.previews.is_cache_file(path):
            return _inside(path, self.previews.dir) and not _unsafe_file(path)
        d = self.drive_for_path(path)
        if d is None or not _connected(d) or not d.get("root"):
            return False
        return _inside(path, d["root"]) and not _unsafe_file(path)

    def media_request(self, kind, path):
        _path_arg(path)
        ext = cats_mod.split_ext(os.path.basename(path))[1]
        if ext not in cats_mod.VIDEO_EXT and ext not in cats_mod.PROXY_VIDEO_EXT:
            if kind == "poster" and ext in media.WEB_IMAGE_EXT and self.media_path_ok(path):
                return {"ok": True, "status": "ready", "url": self.media.url(path)}
            raise ApiError(400, "not_video", "영상 파일이 아니에요.")
        d = self.drive_for_path(path)
        if d is None or not _connected(d) or not self.media_path_ok(path):
            raise ApiError(400, "invalid", "미리보기를 만들 수 없는 경로예요.")
        res = self.previews.request(kind, path)
        out = {"ok": True, "status": res.get("status")}
        if res.get("file"):
            out["url"] = self.media.url(res["file"])
        if res.get("reason"):
            out["reason"] = res["reason"]
        if res.get("status") != "queued":
            self._stats = (0.0, None)
        info = media.probe_cached(path)
        if info is None and self.ffprobe and d.get("kind") != "network":
            info = media.probe(path, self.ffprobe)
        out["playable"] = media.playable(path, info)
        if info:
            for k in ("duration", "width", "height"):
                if info.get(k):
                    out[k] = info[k]
        return out

    def clear_preview_cache(self):
        n, b = self.previews.clear()
        self._stats = (0.0, None)
        return {"ok": True, "removed": n, "bytes": b}

    # ── 창 닫기 ──
    _CLOSE_GUARD = ("move", "copy", "recycle", "delete", "empty_bin", "undo")

    def close_state(self):
        """창을 닫으면 멈추는 파일 작업(분석 작업은 빼고). deleting = 원본을 지우는 중 — 닫으면 그 항목을 마저 지운 뒤 닫힌다."""
        act = [j for j in self.queue.active() if j.get("type") in self._CLOSE_GUARD]
        return {"busy": bool(act), "deleting": self.queue.deleting(),
                "grace_s": int(self.queue.delete_grace),
                "jobs": [{"id": j.get("id"), "type": j.get("type"), "status": j.get("status"),
                          "title": j.get("title") or ""} for j in act[:20]]}

    def window_closing(self):
        """pywebview closing — False 면 창을 닫지 않는다. 파일 작업 중이면 화면에 물어본다.

        물어보지 못하면(화면이 아직 없거나 오류) 닫는다 — 사용자를 창에 가두지 않는다(fail-open)."""
        if self._close_ok:
            return True
        try:
            st = self.close_state()
        except Exception:
            return True
        w = self.window
        if not st["busy"] or w is None:
            return True

        def ask():
            ok = False
            try:
                ok = w.evaluate_js("!!(window.DriveBoard && window.DriveBoard.askClose && "
                                   "window.DriveBoard.askClose(%s))" % json.dumps(st, ensure_ascii=True)) is True
            except Exception:
                ok = False
            if not ok:
                self.request_close()
        # closing 은 UI 스레드에서 불린다 — evaluate_js 가 같은 스레드를 기다리면 멈추므로 따로 돌린다
        threading.Thread(target=ask, name="ask-close", daemon=True).start()
        return False

    def request_close(self):
        """화면의 [멈추고 닫기] — 진행 중인 작업을 멈추고(원본 삭제 단계는 마저 끝낸 뒤) 창을 닫는다."""
        w = self.window
        if w is None:
            raise ApiError(400, "no_window", "창 없이 실행 중이라 여기서 닫을 수 없어요.")
        self._close_ok = True
        deleting = self.queue.deleting()

        def run():
            try:
                self.queue.stop(timeout=5.0)
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass
        threading.Thread(target=run, name="close", daemon=True).start()
        return {"ok": True, "deleting": deleting}

    def shutdown(self):
        try:
            self.queue.stop(timeout=5.0)
        except Exception:
            pass
        try:
            self.previews.close()
        except Exception:
            pass
        with self.lock:
            keys = [L for L in self.indexes if L not in self._offline]
        for L in keys:
            self.save_index(L)
        if self._meta_dirty:
            self._save_meta()


# ═════════════════════════════════════════════════════════════════ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "DriveBoard/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                             "style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; "
                             "frame-ancestors 'none'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, code, err, msg, **extra):
        d = {"ok": False, "error": err, "message": msg}
        d.update(extra)
        self._send(code, d)

    def _gate(self, is_api):
        """보안 관문 — 모든 요청이 여기 한 곳을 지난다 (SPEC §7).

        ★거절할 때는 연결을 닫는다: 본문을 읽지 않은 채 keep-alive 로 두면 그 본문이 「두 번째 요청」 으로
        읽혀(요청 밀수) 브라우저가 붙인 Origin·Sec-Fetch-* 없이 관문을 다시 지난다.
        """
        te = self.headers.get("Transfer-Encoding")
        try:
            clen = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            clen = -1
        if te is not None or clen < 0:
            self.close_connection = True          # chunked 는 받지 않는다(본문 경계를 맞출 수 없다)
            self._err(400, "bad_request", "요청을 읽지 못했어요.")
            return False
        if self.command in ("GET", "HEAD") and clen > 0:
            self.close_connection = True          # GET 본문은 읽지 않는다 — 다음 요청으로 읽히지 않게 닫는다
        ok = self._gate_check(is_api)
        if not ok:
            self.close_connection = True
        return ok

    def _gate_check(self, is_api):
        port = self.server.server_address[1]
        if self.headers.get("Host", "") not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
            self._err(403, "forbidden", "허용되지 않은 접근이에요.")
            return False
        org = self.headers.get("Origin")
        if org is not None and org not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
            self._err(403, "forbidden", "허용되지 않은 출처예요.")
            return False
        if is_api:
            tok = self.headers.get("X-DB-Token", "")
            if not hmac.compare_digest(tok.encode("utf-8", "replace"), self.server.token.encode("utf-8")):
                self._err(403, "forbidden", "토큰이 맞지 않아요.")
                return False
            sfs = self.headers.get("Sec-Fetch-Site")
            if sfs is not None and sfs != "same-origin":
                self._err(403, "forbidden", "허용되지 않은 출처예요.")
                return False
        return True

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_request", "요청을 읽지 못했어요.")
        if n < 0 or n > BODY_MAX:
            self.close_connection = True
            raise ApiError(413, "too_large", "요청이 너무 커요.")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad_request", "요청을 읽지 못했어요.")
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request", "요청 형식이 올바르지 않아요.")
        return data

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        is_api = u.path.startswith("/api/")
        if not self._gate(is_api):
            return
        if u.path.startswith("/media/"):
            return self._media(u.path[len("/media/"):])
        if u.path == "/__driveboard/focus":
            return self._focus()
        if not is_api:
            return self._static(u.path)
        qs = canon_request({k: v[-1] for k, v in parse_qs(u.query, keep_blank_values=True).items()})
        self._dispatch(lambda: self._get(u.path, qs))

    def do_POST(self):
        u = urlparse(self.path)
        if not self._gate(True):
            return
        if not u.path.startswith("/api/"):
            self.close_connection = True          # 본문을 읽지 않았다
            return self._err(404, "not_found", "없는 주소예요.")

        def run():
            return self._post(u.path, canon_request(self._body()))
        self._dispatch(run)

    def _dispatch(self, fn):
        try:
            res = fn()
        except ApiError as e:
            return self._err(e.status, e.code, e.message, **e.extra)
        except ops.OpError as e:
            ae = _op_error(e)
            return self._err(ae.status, ae.code, ae.message, **ae.extra)
        except Exception as e:  # noqa: BLE001 — 앱을 죽이지 않는다
            return self._err(500, "internal", "서버 오류가 났어요: %s" % e)
        if res is None:
            return self._err(404, "not_found", "없는 주소예요.")
        headers = None
        if isinstance(res, tuple):
            res, headers = res
        self._send(200, res, headers=headers)

    def _media(self, token):
        """/media/<token> — 토큰은 서버가 응답을 만들 때 발급한다. 경로는 URL 에 없다 (SPEC-2 C.1)."""
        A = self.server.app
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs is not None and sfs != "same-origin":
            return self._err(403, "forbidden", "허용되지 않은 출처예요.")
        if not _TOKEN_RE.match(token or ""):
            return self._err(404, "not_found", "없는 미디어예요.")
        path = A.media.resolve(token)
        if not path:
            return self._err(404, "not_found", "없는 미디어예요.")
        if not A.media_path_ok(path):
            return self._err(403, "forbidden", "보여 줄 수 없는 파일이에요.")
        ctype = media.content_type_for(cats_mod.split_ext(os.path.basename(path))[1])
        media.serve_file(self, winapi.long_path(path), content_type=ctype)

    def _focus(self):
        """두 번째 실행이 보내는 「앞으로 나와」 — 데이터 폴더의 잠금 파일에 적힌 비밀값을 알아야 한다."""
        sec = getattr(self.server, "instance_secret", None)
        got = self.headers.get("X-DB-Instance", "")
        if (not sec or self.headers.get("Sec-Fetch-Site") is not None
                or not hmac.compare_digest(got.encode("utf-8", "replace"), sec.encode("utf-8"))):
            return self._err(403, "forbidden", "허용되지 않은 접근이에요.")
        A = self.server.app
        shown = False
        w = A.window
        if w is not None:
            # restore() 는 WinForms 에서 WindowState=Normal 이라 최대화한 창을 작게 만든다 — 최소화돼 있을 때만,
            # 그리고 최소화 전에 최대화였으면 다시 최대화한다(창 상태는 main() 이 events 로 따라간다)
            st = getattr(A, "win_state", None) or {}
            names = ["show"]
            if st.get("now") == "min":
                names = ["restore", "maximize", "show"] if st.get("before") == "max" else ["restore", "show"]
            for name in names:
                try:
                    getattr(w, name)()
                    shown = True
                except Exception:
                    pass
            try:
                w.on_top = True
                w.on_top = False
            except Exception:
                pass
        port = self.server.server_address[1]
        self._send(200, {"ok": True, "app": "DriveBoard", "window": w is not None, "shown": shown,
                         "url": "http://127.0.0.1:%d/" % port})

    def _get(self, p, qs):
        A = self.server.app
        if p == "/api/meta":
            return {"ok": True, "version": __version__, "platform": "fake" if winapi.is_fake() else "win",
                    "categories": cats_mod.CATEGORIES, "virtual": cats_mod.VIRTUAL, "settings": A.settings,
                    "ffmpeg": bool(A.previews.ffmpeg), "dialog": A.window is not None,
                    "releases_url": version_mod.RELEASES_URL}
        if p == "/api/state":
            return A.state()
        if p == "/api/items":
            return A.items(qs)
        if p == "/api/tree":
            return A.tree(qs)
        if p == "/api/jobs":
            return {"ok": True, "jobs": A.queue.jobs(200)}
        if p == "/api/recommendations":
            return {"ok": True, "items": A.recommendations()}
        if p == "/api/dupes":
            mm = _int(qs.get("min_mb"), 50, 1, 1 << 20)
            g = A.dupe_groups(mm << 20)
            return {"ok": True, "groups": g, "wasted_total": dupes_mod.wasted_total(g)}
        if p == "/api/settings":
            return {"ok": True, "settings": A.settings}
        if p == "/api/projects":
            return A.projects_view(qs)
        if p == "/api/project":
            return A.project_detail(qs.get("key"), qs.get("path"))
        if p == "/api/team/export":
            return A.team_export(), {"Content-Disposition": 'attachment; filename="driveboard-team.json"'}
        return None

    def _post(self, p, b):
        A = self.server.app
        Q = A.queue
        if p == "/api/scan":
            ds = b.get("drives")
            if ds == "all":
                letters = [_letter(d) for d in A.drives() if d.get("scan") and _connected(d)]
            elif isinstance(ds, list):
                letters = [_dkey(str(x)) for x in ds]
                letters = [x for x in letters if x]
            else:
                raise ApiError(400, "invalid", "드라이브 목록이 올바르지 않아요.")
            started, skipped = A.start_scans(letters)
            return {"ok": True, "started": started, "skipped": skipped}
        if p == "/api/scan/cancel":
            return {"ok": True, "canceled": A.cancel_scan(str(b.get("drive", "")))}
        if p == "/api/inspect":
            return A.inspect(b.get("paths"))
        if p == "/api/ops/recycle":
            return {"ok": True, "job_id": Q.submit_recycle(A.expand_items(b.get("paths")))}
        if p in ("/api/ops/move", "/api/ops/preview_move", "/api/ops/copy"):
            mode = b.get("mode") or ("copy" if p == "/api/ops/copy" else "move")
            if mode not in ("move", "copy"):
                raise ApiError(400, "invalid", "작업 방식은 move 또는 copy 여야 해요.")
            dd = b.get("dest_dir") if isinstance(b.get("dest_dir"), str) and b.get("dest_dir") else None
            tg = b.get("target")
            target = _dkey(tg) if isinstance(tg, str) and tg.strip() else None
            if isinstance(tg, str) and tg.strip() and not target:
                raise ApiError(400, "unknown_drive", "대상 드라이브를 찾을 수 없어요.")
            if target is None and dd is None:
                raise ApiError(400, "unknown_drive", "옮길 드라이브나 폴더를 골라 주세요.")
            items = A.expand_items(b.get("paths"))
            if p == "/api/ops/preview_move":
                return Q.preview_move(items, target, dest_dir=dd, mode=mode)
            verify = b.get("verify") or A.settings.get("verify", "quick")
            jid = Q.submit_move(items, target, dest_dir=dd, verify=verify, mode=mode)
            return {"ok": True, "job_id": jid}
        if p == "/api/ops/delete":
            if b.get("confirm") is not True:
                raise ApiError(400, "need_confirm", "영구 삭제는 복구할 수 없어요. 확인한 뒤 다시 요청해 주세요.")
            items = A.expand_items(b.get("paths"))
            caps = A.check_growth(items, b.get("expect"))
            return {"ok": True, "job_id": Q.submit_delete(items, limits=caps)}
        if p == "/api/ops/empty_bin":
            ds = b.get("drives")
            if not isinstance(ds, list):
                raise ApiError(400, "invalid", "드라이브 목록이 올바르지 않아요.")
            if b.get("confirm") is not True:
                raise ApiError(400, "need_confirm", "휴지통을 비우면 복구할 수 없어요. 확인한 뒤 다시 요청해 주세요.")
            return {"ok": True, "job_id": Q.submit_empty_bin([_dkey(str(x)) or str(x) for x in ds])}
        if p == "/api/ops/cancel":
            if not Q.cancel(str(b.get("job_id", ""))):
                raise ApiError(404, "not_found", "취소할 작업을 찾을 수 없어요.")
            return {"ok": True}
        if p == "/api/app/close":
            return A.request_close()
        if p == "/api/ops/undo":
            return {"ok": True, "job_id": Q.undo(str(b.get("job_id", "")), confirm=b.get("confirm") is True)}
        if p == "/api/reveal":
            return {"ok": True, "shown": A.reveal(b.get("path"))}
        if p == "/api/dupes/verify":
            return {"ok": True, "job_id": A.submit_verify(b.get("group_ids"))}
        if p == "/api/settings":
            return {"ok": True, "settings": A.save_settings(b)}
        if p == "/api/rules":
            return {"ok": True, "settings": A.add_rule(b.get("path"), b.get("cat"))}
        if p == "/api/rules/delete":
            jid, s = A.delete_rule(b.get("path"))
            return {"ok": True, "job_id": jid, "settings": s}
        if p == "/api/locations/add":
            return A.add_location(b.get("path"), b.get("label"))
        if p == "/api/locations/remove":
            return A.remove_location(b.get("key"))
        if p == "/api/locations/shares":
            return A.list_shares(b.get("host"))
        if p == "/api/drives/scan_toggle":
            return A.scan_toggle(b.get("key"), b.get("scan"))
        if p == "/api/drives/forget":
            return A.forget(b.get("key"))
        if p == "/api/pick_folder":
            return A.pick_folder(b.get("initial"))
        if p == "/api/projects/not_project":
            return A.not_project(b.get("path"), remove=b.get("remove") is True)
        if p == "/api/projects/roots":
            return A.project_roots(b)
        if p == "/api/media/poster":
            return A.media_request("poster", b.get("path"))
        if p == "/api/media/proxy":
            return A.media_request("proxy", b.get("path"))
        if p == "/api/media/clear_cache":
            return A.clear_preview_cache()
        if p == "/api/team/import":
            return A.team_import(b.get("data"), apply=b.get("apply") is True)
        if p == "/api/open_url":
            return A.open_url(b.get("url"))
        return None

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        parts = rel.split("/")
        if (not rel or "\\" in rel or ":" in rel or "\0" in rel or path.startswith("//")
                or any(x in ("", ".", "..") for x in parts) or any(x.startswith(".") for x in parts)):
            return self._err(403, "forbidden", "허용되지 않은 경로예요.")
        ext = rel.rsplit(".", 1)[-1].lower() if "." in parts[-1] else ""
        if ext not in STATIC_TYPES:
            return self._err(404, "not_found", "없는 파일이에요.")
        full = os.path.realpath(os.path.join(UI_DIR, *parts))
        ui_real = os.path.realpath(UI_DIR)
        if not full.startswith(ui_real + os.sep) or not os.path.isfile(full):
            return self._err(404, "not_found", "없는 파일이에요.")
        with open(full, "rb") as f:
            data = f.read()
        if rel == "index.html":
            data = data.replace(TOKEN_PLACEHOLDER, self.server.token.encode("ascii"), 1)
        self._send(200, data, STATIC_TYPES[ext])


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False      # 다른 프로세스와 같은 포트를 겹쳐 잡지 않는다

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_server(app, port=DEFAULT_PORT, tries=50, token=None):
    last = None
    for p in range(port, port + max(1, tries)):
        try:
            srv = Server(("127.0.0.1", p), Handler)
        except OSError as e:
            last = e
            continue
        srv.app = app
        srv.token = token or secrets.token_urlsafe(32)
        srv.instance_secret = secrets.token_urlsafe(24)
        srv.verbose = False
        return srv
    raise OSError("빈 포트를 찾지 못했어요: %s" % last)


# ═════════════════════════════════════════════════════════════════ 한 번만 실행

def lock_path():
    return os.path.join(store.data_dir(), LOCK_NAME)


def write_instance_lock(srv):
    data = {"pid": os.getpid(), "port": srv.server_address[1], "secret": srv.instance_secret,
            "started_at": time.time()}
    try:
        store.atomic_write(lock_path(), json.dumps(data).encode("utf-8"))
    except OSError:
        pass


def remove_instance_lock(srv):
    try:
        with open(lock_path(), "rb") as f:
            cur = json.loads(f.read().decode("utf-8"))
        if isinstance(cur, dict) and cur.get("secret") == srv.instance_secret:
            os.unlink(lock_path())
    except (OSError, ValueError, UnicodeDecodeError):
        pass


def find_running(timeout=2.0):
    """이미 떠 있는 DriveBoard 가 응답하면 {window, url, shown} (그쪽 창은 앞으로 나온다), 아니면 None."""
    try:
        with open(lock_path(), "rb") as f:
            cur = json.loads(f.read().decode("utf-8"))
        port = int(cur["port"])
        sec = str(cur["secret"])
    except (OSError, ValueError, UnicodeDecodeError, KeyError, TypeError):
        return None
    if not (0 < port < 65536):
        return None
    req = urllib.request.Request("http://127.0.0.1:%d/__driveboard/focus" % port,
                                 headers={"X-DB-Instance": sec, "Host": "127.0.0.1:%d" % port})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            data = json.loads(r.read(65536).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("app") != "DriveBoard" or not data.get("ok"):
        return None
    # 응답한 쪽이 준 url 은 쓰지 않는다 — 옛 잠금 파일의 포트를 다른 프로그램이 잡고 있을 수 있고,
    # 그 url 을 webbrowser.open(Windows 는 os.startfile)에 넘기면 아무 파일·프로토콜이나 열린다
    data = dict(data)
    data["url"] = "http://127.0.0.1:%d/" % port
    return data


# ═════════════════════════════════════════════════════════════════ 창

def _apply_dark_titlebar(title):
    """Windows: 표준 프레임의 타이틀바만 다크로 (DwmSetWindowAttribute 20/35/36)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi")
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))
        cap = ctypes.c_uint(0x00100C0B)      # #0B0C10 (COLORREF 0x00BBGGRR)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap), ctypes.sizeof(cap))
        txt = ctypes.c_uint(0x00F4EEED)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(txt), ctypes.sizeof(txt))
    except Exception:
        pass


def selftest_gui():
    """배포본 자가 진단(--selftest-gui): 창을 띄우지 않고 네이티브 창 스택(pywebview·WinForms·EdgeChromium·pythonnet·
    WebView2 어셈블리)이 실제로 올라오는지만 본다. 0 = 정상, 1 = 실패(무엇이 빠졌는지 기록). 서버는 띄우지 않는다."""
    step = "webview"
    try:
        import webview  # type: ignore  # noqa: F401
        step = "webview.platforms.winforms"
        import webview.platforms.winforms  # type: ignore  # noqa: F401
        step = "webview.platforms.edgechromium"
        import webview.platforms.edgechromium  # type: ignore  # noqa: F401
        step = "clr"
        import clr  # type: ignore
        step = "Microsoft.Web.WebView2.Core"
        clr.AddReference("Microsoft.Web.WebView2.Core")
    except BaseException as e:  # noqa: BLE001 — SystemExit·DLL 로드 실패까지 전부 「실패」로 알린다
        msg = "selftest-gui FAIL at %s: %s: %s" % (step, type(e).__name__, e)
        rc = 1
    else:
        msg = "selftest-gui OK"
        rc = 0
    try:
        with open(os.path.join(store.data_dir(), "selftest-gui.log"), "w", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass
    try:
        print(msg, file=sys.stderr if rc else sys.stdout, flush=True)
    except Exception:
        pass
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="DriveBoard")
    ap.add_argument("--no-open", action="store_true", help="창 없이 서버만")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--fake", default=None, help="가짜 드라이브 폴더(개발·테스트)")
    ap.add_argument("--multi", action="store_true", help="이미 떠 있어도 하나 더 띄운다(개발용)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--selftest-gui", action="store_true", help="창 스택이 올라오는지만 확인하고 끝낸다(배포 검사용)")
    a = ap.parse_args(argv)
    if sys.stdout is None or sys.stderr is None:      # pythonw: 콘솔이 없다 → 로그 파일로
        try:
            logf = open(os.path.join(store.data_dir(), "driveboard.log"), "a", encoding="utf-8", buffering=1)
        except OSError:
            logf = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or logf
        sys.stderr = sys.stderr or logf
    if a.selftest_gui:
        return selftest_gui()
    single = not a.no_open and not a.multi
    if single:
        other = find_running()
        if other is not None:
            print("DriveBoard 가 이미 실행 중이에요: %s" % other.get("url"), flush=True)
            if not other.get("window") and other.get("url"):
                webbrowser.open(other["url"])
            return 0
    fake = a.fake or os.environ.get("DRIVEBOARD_FAKE")
    if fake:
        winapi.set_fake_root(fake)
    app = App()
    app.start_update_check()
    srv = make_server(app, a.port)
    srv.verbose = a.verbose
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("DriveBoard: %s" % url, flush=True)
    if single:
        write_instance_lock(srv)

    def stop():
        if single:
            remove_instance_lock(srv)
        app.shutdown()

    if a.no_open:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop()
        return 0
    try:
        import webview  # type: ignore
    except Exception:
        webview = None
    if webview is None:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop()
        return 0
    try:
        # 팀 설정 내보내기(Blob 다운로드)가 창 안에서 막히지 않게 — pywebview 5+ 기본값은 막힘
        webview.settings["ALLOW_DOWNLOADS"] = True
    except Exception:
        pass
    t = threading.Thread(target=srv.serve_forever, name="http", daemon=True)
    t.start()
    win = webview.create_window("DriveBoard", url, width=1360, height=860, min_size=(1100, 720),
                                background_color="#0B0C10")
    app.window = win
    try:
        win.events.shown += lambda: _apply_dark_titlebar("DriveBoard")
    except Exception:
        pass
    try:
        # 이동·삭제 중에 닫으면 화면에서 먼저 묻는다(False = 닫지 않음). 원본 삭제 단계는 stop() 이 마저 끝낸다.
        win.events.closing += app.window_closing
    except Exception:
        pass
    app.win_state = {"now": "normal", "before": "normal"}

    def _ws(now):
        def fn(*_a):
            st = app.win_state
            if now == "min":
                st["before"] = st.get("now") if st.get("now") != "min" else st.get("before")
            st["now"] = now
        return fn
    for ev, now in (("minimized", "min"), ("maximized", "max"), ("restored", "normal")):
        try:
            getattr(win.events, ev).__iadd__(_ws(now))
        except Exception:
            pass
    try:
        # private_mode 기본값(True)은 매 실행 버리는 프로필이라 프로젝트 보기의 localStorage(묶기·정렬·접기·음량)가 사라진다
        kw = {}
        try:
            import inspect
            ps = inspect.signature(webview.start).parameters
            if "private_mode" in ps:
                kw["private_mode"] = False
            if "storage_path" in ps:
                kw["storage_path"] = os.path.join(store.data_dir(), "webview")
        except (TypeError, ValueError):
            pass
        webview.start(**kw)
    finally:
        app.window = None
        srv.shutdown()
        stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

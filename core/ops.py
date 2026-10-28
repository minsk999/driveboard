"""작업 큐 (SPEC §8, SPEC-2 A·F) — 휴지통 / 이동(복사→검증→원본 삭제) / 복사 / 영구 삭제 /
휴지통 비우기 / 되돌리기 / 기록.

데이터 안전이 최우선이다.
- 원본은 그 항목의 모든 파일이 복사·검증된 뒤에만 지운다. 지우기 직전에 원본이 바뀌었으면 남긴다.
- 대상 파일은 덮어쓰지 않는다(커밋은 '없을 때만' 연산).
- 실패·취소된 항목은 우리가 만든 사본·.dbpart·폴더만 되돌려 지운다. 원본은 그대로다.
- 링크·정션·클라우드 자리표시자는 따라가지도 지우지도 않는다.
- 복사(mode='copy')는 이동과 같은 경로에서 원본 삭제 단계만 빠진다. 되돌리기는 그 작업이 만든 파일·폴더만,
  복사본이 그대로이고 원본도 그대로일 때만 지운다(원본이 사라졌으면 복사본이 유일본이라 남긴다).
- 휴지통이 없는 위치(NAS·대부분의 USB 메모리, has_bin:false)에 휴지통 보내기는 거부(no_bin) — 영구 삭제는
  submit_delete 로만, 사용자가 명시적으로 고른 경우에만.
- 장치가 빠지거나 NAS 가 끊기면(입출력 오류) 그 항목은 되돌리고 작업을 멈춘다 — 원본은 그대로다.
  원본을 지우는 단계에서 원본 장치가 빠지면 그 항목은 '일부만 옮김'으로 남기고 사본은 거두지 않는다.
- 원본 삭제 단계에 들어가기 직전·항목이 끝난 직후 기록을 바로 디스크에 남긴다 — 앱이 죽어도 다음 실행이
  저장된 항목 상태로 안내한다(원본이 그대로라고 단정하지 않는다). stop() 은 그 단계가 끝나길 기다린다.
- 되돌리기는 작업 때 기록한 볼륨 일련번호와 지금 그 문자의 디스크가 다르면 하지 않는다(drive_changed).

hooks(모두 선택, dict 또는 속성 객체):
  size_of(path) -> (bytes, files) | None   인덱스로 빠르게 크기 조회(없으면 디스크를 걷는다)
  on_removed(path)    path(파일·폴더·시퀀스 표시 경로)가 원본 쪽에서 사라졌다 → detach
  on_added(path)      path 가 새로 생겼다 → scan_subtree + graft (중간 폴더는 graft 가 만든다)
  on_changed(path)    path 아래 내용이 바뀌었다 → 다시 스캔(없으면 on_removed + on_added 로 대신)
  on_job_done(job)    작업이 끝났다(공개 job dict)
"""
from __future__ import annotations

import collections
import errno
import hashlib
import json
import math
import os
import posixpath
import queue
import shutil
import stat
import threading
import time

from . import safety, winapi

IS_WIN = os.name == "nt"
CHUNK = 8 << 20              # 8MiB
MARGIN = 1 << 30             # 공간 검사 여유 1GiB
SPAN = 1 << 20               # quick 검증: 처음·가운데·끝 1MiB
MAX_JOBS = 200
RECYCLE_CHUNK = 100          # 휴지통: 이만큼씩 보내며 사이사이 취소 확인
VERIFY_LEVELS = ("size", "quick", "full")
MODES = ("move", "copy")
SLOW_CHUNK = {"network": 1 << 20, "usb": 4 << 20}   # 느린 대상은 작은 청크 → 진행률·속도가 자주 갱신된다
CACHE_HINTS = ("media cache", "disk cache", "peak files", "aecache", "conformed audio")
_REPARSE_MASK = 0x400 | 0x1000 | 0x40000 | 0x400000   # REPARSE_POINT | OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS

R_LINK = "링크·정션 또는 클라우드 자리표시자라 다룰 수 없어요."
R_LINK_PATH = "링크(정션)를 거친 경로라 다룰 수 없어요."
R_MISSING = "파일을 찾을 수 없어요."
R_NOTREG = "일반 파일이 아니라 옮길 수 없어요."
R_CANCELED = "취소됐어요."
R_CHANGED = "옮기는 동안 원본이 바뀌어 원본을 남겨 뒀어요."
R_OVERLAP = "옮길 위치가 실제로는 원본과 같은 곳(또는 그 안)이라 옮기지 않았어요."
R_INTO_SELF = "폴더를 자기 자신 안으로 옮기거나 복사할 수 없어요."
R_DEVLOST = "장치 연결이 끊겼어요."
R_NO_BIN = winapi.NO_BIN_MSG
R_COPY_CHANGED = "복사한 뒤 바뀐 파일이라 남겨 뒀어요."
R_ORIG_GONE = "원본이 없어져 복사본이 유일본이라 남겨 뒀어요."
R_ORIG_CHANGED = "복사한 뒤 원본이 바뀌어 복사본을 남겨 뒀어요."
R_DEST_OFFLINE = "대상 장치가 연결되어 있지 않아요."
R_GREW = "확인한 뒤 내용이 늘어 지우지 않았어요. 다시 스캔해서 확인한 뒤 지워 주세요."
R_DRIVE_CHANGED = ("%s 에 그때와 다른 디스크가 연결돼 있어요(볼륨 일련번호가 달라요). "
                   "다른 디스크의 파일을 건드리지 않도록 되돌리지 않았어요.")

# 장치 분리·네트워크 끊김으로 보는 오류(Windows winerror / POSIX errno)
_DEVLOST_WINERR = frozenset({21, 51, 53, 55, 59, 64, 67, 121, 1167, 1231, 1232, 1236, 433})
_DEVLOST_ERRNO = frozenset(e for e in (getattr(errno, n, None) for n in (
    "ENODEV", "ENXIO", "ENOTCONN", "ESHUTDOWN", "EHOSTDOWN", "EHOSTUNREACH", "ENETDOWN",
    "ENETUNREACH", "ENETRESET", "ECONNRESET", "ECONNABORTED", "ETIMEDOUT", "ESTALE", "ENOMEDIUM")) if e)


class OpError(Exception):
    """작업을 시작하지 않는 검증 오류.
    code: protected|same_drive|space|empty|unknown_drive|invalid|invalid_dest|not_found|not_undoable|no_bin|need_dest|
          drive_changed"""

    def __init__(self, code, message, extra=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = dict(extra or {})

    def to_dict(self):
        d = {"ok": False, "error": self.code, "message": self.message}
        d.update(self.extra)
        return d


class _Cancelled(Exception):
    pass


class _SrcError(Exception):
    """원본을 읽지 못함 → 그 파일은 원본에 남기고 계속(partial). devlost 면 항목을 되돌리고 멈춘다."""

    def __init__(self, reason, devlost=False):
        super().__init__(reason)
        self.reason = reason
        self.devlost = devlost


class _DstError(Exception):
    """대상 쪽 실패·검증 불일치 → 그 항목 전체를 되돌린다."""

    def __init__(self, reason, nospace=False, devlost=False):
        super().__init__(reason)
        self.reason = reason
        self.nospace = nospace
        self.devlost = devlost


def _src_err(e):
    return _SrcError(_err_ko(e), devlost=_is_devlost(e))


def _dst_err(e):
    return _DstError(_err_ko(e), nospace=_is_nospace(e), devlost=_is_devlost(e))


# ─────────────────────────────── 테스트가 바꿔 끼울 수 있는 저수준 함수 ────────────────────

def _open_read(path):
    return open(path, "rb")


def _write(f, data):
    f.write(data)


def _lp(p):
    return winapi.long_path(p)


# ─────────────────────────────── 보조 ───────────────────────────────────────────

def fmt_size(n):
    """탐색기식 1024 진법, 유효숫자 3자리(버림)."""
    n = int(n or 0)
    if n <= 0:
        return "0KB"
    units = ("KB", "MB", "GB", "TB", "PB")
    v, i = n / 1024.0, 0
    while v >= 1000 and i < len(units) - 1:
        v /= 1024.0
        i += 1
    if v < 1:
        return "%.1fKB" % max(0.1, math.floor(v * 10) / 10)
    if v >= 100:
        s = "%d" % int(v)
    elif v >= 10:
        s = "%.1f" % (math.floor(v * 10) / 10)
    else:
        s = "%.2f" % (math.floor(v * 100) / 100)
    return s + units[i]


def _is_nospace(e):
    return getattr(e, "errno", None) == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112)


def _is_devlost(e):
    """장치가 빠졌거나 네트워크가 끊긴 오류인가."""
    if not isinstance(e, OSError):
        return False
    w = getattr(e, "winerror", None)
    if w is not None:
        return w in _DEVLOST_WINERR
    return getattr(e, "errno", None) in _DEVLOST_ERRNO


def _err_ko(e):
    if isinstance(e, FileNotFoundError):
        return R_MISSING
    if isinstance(e, FileExistsError):
        return "같은 이름의 항목이 이미 있어요."
    if isinstance(e, PermissionError):
        return "접근이 거부됐거나 다른 프로그램이 사용 중이에요."
    if _is_nospace(e):
        return "대상 드라이브 공간이 부족해요."
    if getattr(e, "errno", None) == errno.ENAMETOOLONG or getattr(e, "winerror", None) == 206:
        return "경로나 이름이 너무 길어요."
    if _is_devlost(e):
        return R_DEVLOST
    if getattr(e, "errno", None) == errno.EIO or getattr(e, "winerror", None) in (23, 1117):
        return "디스크에서 읽거나 쓰지 못했어요(입출력 오류)."
    return "오류가 났어요: %s" % (getattr(e, "strerror", None) or e)


def _is_reparse_st(st):
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_MASK)


def _is_reparse_path(p):
    try:
        st = os.lstat(_lp(p))
    except OSError:
        return False
    return _is_reparse_st(st)


def _entry_is_reparse(e):
    try:
        if e.is_symlink():
            return True
        j = getattr(e, "is_junction", None)      # 3.12+
        if j is not None and j():
            return True
        return _is_reparse_st(e.stat(follow_symlinks=False))
    except OSError:
        return True                               # 모르면 다루지 않는다


def _retry(fn, tries=4, delay=0.15):
    """Windows 백신·색인기가 막 쓴 파일을 잠깐 잡는 경우만 다시 시도한다."""
    n = tries if IS_WIN else 1
    for i in range(n):
        try:
            return fn()
        except PermissionError:
            if i == n - 1:
                raise
            time.sleep(delay)


def _remove_file(p):
    lp = _lp(p)
    try:
        _retry(lambda: os.remove(lp))
    except PermissionError:
        if not IS_WIN:
            raise
        st = os.lstat(lp)
        if st.st_mode & stat.S_IWRITE:
            raise
        os.chmod(lp, stat.S_IWRITE | stat.S_IREAD)          # 읽기 전용 해제
        try:
            _retry(lambda: os.remove(lp))
        except OSError:
            try:
                os.chmod(lp, st.st_mode)
            except OSError:
                pass
            raise


def _discard_file(p):
    """우리가 만든 파일만 지운다(실패는 무시)."""
    try:
        _remove_file(p)
    except OSError:
        pass


def _is_empty_dir(p):
    try:
        with os.scandir(_lp(p)) as it:
            for _e in it:
                return False
        return True
    except OSError:
        return False


def _commit(tmp, dst):
    """tmp → dst. dst 가 이미 있으면 FileExistsError(절대 덮어쓰지 않는다)."""
    lt, ld = _lp(tmp), _lp(dst)
    if IS_WIN:
        _retry(lambda: os.rename(lt, ld))     # Windows rename 은 대상이 있으면 실패한다
        return
    try:
        os.link(lt, ld)                        # link 도 대상이 있으면 실패 → 원자적 '없을 때만'
    except FileExistsError:
        raise
    except OSError:
        if os.path.lexists(ld):
            raise FileExistsError(errno.EEXIST, "exists", dst)
        os.rename(lt, ld)
        return
    try:
        os.unlink(lt)
    except OSError:
        pass


def _tmp_for(dst):
    cand = dst + ".dbpart"
    n = 1
    while os.path.lexists(_lp(cand)):         # 남이 만든 같은 이름은 건드리지 않는다
        cand = "%s.%d.dbpart" % (dst, n)
        n += 1
    return cand


def _read_exact(f, n):
    out = bytearray()
    while len(out) < n:
        b = f.read(min(n - len(out), CHUNK))
        if not b:
            break
        out += b
    return bytes(out)


def _with_suffix(mod, path, n, is_dir):
    head, name = mod.split(path)
    if is_dir:
        return mod.join(head, "%s (%d)" % (name, n))
    stem, ext = mod.splitext(name)
    return mod.join(head, "%s (%d)%s" % (stem, n, ext))


def _group_folder_name(label, mod):
    import re
    stem = mod.splitext(mod.basename(label))[0]
    stem = re.sub(r"\[#+\]|#+|%0?\d*d", "", stem)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" ._-")
    return stem or "시퀀스"


def _scan_tree(top, mod):
    """(files[(path,size)], dirs[path] 부모 먼저, skipped[(path,reason)]) — 링크는 따라가지 않는다."""
    dirs, files, skipped = [top], [], []
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(_lp(d))
        except OSError as e:
            skipped.append((d, _err_ko(e)))
            continue
        with it:
            for e in it:
                p = mod.join(d, e.name)
                try:
                    if _entry_is_reparse(e):
                        skipped.append((p, R_LINK))
                        continue
                    if e.is_dir(follow_symlinks=False):
                        dirs.append(p)
                        stack.append(p)
                        continue
                    st = e.stat(follow_symlinks=False)
                    if not stat.S_ISREG(st.st_mode):
                        skipped.append((p, R_NOTREG))
                        continue
                    files.append((p, st.st_size))
                except OSError as ex:
                    skipped.append((p, _err_ko(ex)))
    return files, dirs, skipped


def _real(p):
    r = os.path.realpath(_lp(p))
    if r.startswith("\\\\?\\"):
        r = r[4:]
    return os.path.normcase(r)


def _overlaps(src, dst, mod):
    """dst(또는 그 가장 가까운 존재하는 조상)가 실제로 src 와 같거나 그 안이면 True.

    subst·볼륨 겹침·링크로 드라이브 문자가 달라 보여도 같은 곳일 수 있다 → 자기 안으로 복사하면 원본을 잃는다."""
    p = dst
    while not os.path.lexists(_lp(p)):
        np_ = mod.dirname(p)
        if np_ == p:
            return False
        p = np_
    try:
        rs, rd = _real(src), _real(p)
    except (OSError, ValueError):
        return True                               # 모르면 막는다
    sep = os.sep
    return rd == rs or rd.startswith(rs.rstrip(sep) + sep)


def _tree_bytes(top):
    total = count = 0
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(_lp(d))
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if _entry_is_reparse(e):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(os.path.join(d, e.name))
                    else:
                        total += e.stat(follow_symlinks=False).st_size
                        count += 1
                except OSError:
                    pass
    return total, count


def _atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        _retry(lambda: os.replace(tmp, path))
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _letter_of(d):
    """드라이브 키('D' 또는 'N-3fa9c1')."""
    if isinstance(d, dict):
        return winapi.norm_key(str(d.get("letter") or ""))
    return ""


def _find_drive(drives, letter):
    L = winapi.norm_key(letter) if isinstance(letter, str) else ""
    if not L:
        return None
    for d in drives or ():
        if _letter_of(d) == L:
            return d
    return None


def _disp(d, key=None):
    """화면용 이름: 문자 드라이브 'H:', 네트워크 위치는 라벨."""
    k = _letter_of(d) if d else winapi.norm_key(key or "")
    if len(k) == 1:
        return k + ":"
    if isinstance(d, dict) and d.get("label"):
        return str(d["label"])
    return k or "?"


def _serial_of(d):
    return str((d or {}).get("serial") or "")


def _serial_mismatch(drives, expect):
    """expect {문자: 볼륨 일련번호}(작업 때 기록) 중 지금 그 문자에 다른 디스크가 붙은 첫 문자, 없으면 None.
    기록이 없거나(옛 기록) 지금 그 문자가 없거나 일련번호를 모르면 판정하지 않는다(다른 검사가 맡는다)."""
    for L, want in (expect or {}).items():
        if not want:
            continue
        d = _find_drive(drives, L)
        if d is None:
            continue
        now = _serial_of(d)
        if now and now != want:
            return L
    return None


def _no_bin(d):
    """휴지통이 없다고 명시된 위치인가(필드가 없는 옛 dict 는 있다고 본다)."""
    return isinstance(d, dict) and d.get("has_bin") is False


def _inside_key(child, parent):
    """child 가 parent 와 같거나 그 안인가(정규화된 경로 문자열 비교)."""
    c, p = _key(child), _key(parent)
    if c == p:
        return True
    p = p.rstrip("\\/")
    return c.startswith(p + "\\") or c.startswith(p + "/")


def _key(p):
    """비교 키 — Windows 경로는 대소문자 무시, POSIX(가짜 드라이브) 경로는 그대로."""
    return p.casefold() if safety.mod_for(p) is not posixpath else p


def _ancestor_keys(k):
    for i, ch in enumerate(k):
        if ch in "\\/" and i > 0:
            yield k[:i]


# ─────────────────────────────── 작업 문맥 ─────────────────────────────────────────

class JobContext:
    """submit_task 의 fn(ctx) 이 받는 객체. 이동·휴지통 내부에서도 쓴다."""

    def __init__(self, q, job, ev):
        self._q = q
        self._job = job
        self._ev = ev
        self._samples = collections.deque()
        self.job_id = job["id"]
        self.warnings = 0

    def cancelled(self):
        return self._ev.is_set()

    def progress(self, bytes_done=None, bytes_total=None, files_done=None, files_total=None, current=None):
        with self._q._lock:
            j = self._job
            if bytes_total is not None:
                j["bytes_total"] = int(bytes_total)
            if files_total is not None:
                j["files_total"] = int(files_total)
            if files_done is not None:
                j["files_done"] = int(files_done)
            if current is not None:
                j["current"] = str(current)
            if bytes_done is not None:
                j["bytes_done"] = int(bytes_done)
                self._speed()

    def result(self, path, ok, error=None, dest=None):
        r = {"path": path, "ok": bool(ok)}
        if dest is not None:
            r["dest"] = dest
        if error is not None:
            r["error"] = str(error)
        with self._q._lock:
            self._job["results"].append(r)

    def message(self, text):
        with self._q._lock:
            self._job["message"] = str(text)

    # 내부용
    def _add_bytes(self, n):
        with self._q._lock:
            self._job["bytes_done"] += n
            self._speed()

    def _set_bytes(self, n):
        with self._q._lock:
            self._job["bytes_done"] = n
            self._speed()

    def _file_done(self):
        with self._q._lock:
            self._job["files_done"] += 1

    def _current(self, p):
        with self._q._lock:
            self._job["current"] = p

    def _speed(self):
        now = time.monotonic()
        s = self._samples
        j = self._job
        if s and now - s[-1][0] < 0.25:
            return
        s.append((now, j["bytes_done"]))
        while len(s) > 2 and now - s[0][0] > 5.0:
            s.popleft()
        if len(s) >= 2 and s[-1][0] > s[0][0]:
            bps = max(0.0, (s[-1][1] - s[0][1]) / (s[-1][0] - s[0][0]))
            j["speed_bps"] = int(bps)
            rem = max(0, j["bytes_total"] - j["bytes_done"])
            j["eta_s"] = int(rem / bps) if bps > 0 else None


# ─────────────────────────────── 작업 큐 ──────────────────────────────────────────

class JobQueue:
    def __init__(self, data_dir, drives_provider, safety_check, hooks=None):
        self.data_dir = data_dir
        self.drives_provider = drives_provider
        self.safety_check = safety_check
        self._hooks = hooks or {}
        self.chunk_size = CHUNK
        self.margin = MARGIN
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._jobs = {}
        self._order = []
        self._events = {}
        self._fns = {}
        self._returns = {}
        self._q = queue.Queue()
        self._thread = None
        self._stopping = threading.Event()
        self._seq = 0
        self._last_save = 0.0
        self._dirty = False
        self._deleting = threading.Event()     # 원본 삭제 단계(취소를 받지 않는다) 진행 중
        self.delete_grace = 120.0              # stop() 이 그 단계가 끝나길 더 기다리는 최대 초
        self.hook_errors = 0
        self.save_error = None
        self._path = os.path.join(data_dir, "jobs.json")
        self._load()

    # ── 수명 ──
    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stopping.clear()
            self._thread = threading.Thread(target=self._worker, name="driveboard-ops", daemon=True)
            self._thread.start()
            for jid in self._order:
                if self._jobs[jid]["status"] == "queued":
                    self._q.put(jid)

    def stop(self, timeout=10.0):
        self._stopping.set()
        with self._lock:
            for jid in self._order:
                j = self._jobs[jid]
                if j["status"] == "queued":
                    self._finish_unrun(j, "앱을 종료해 취소했어요.")
                elif j["status"] == "running":
                    self._events[jid].set()
        self._q.put(None)
        t = self._thread
        if t is not None:
            t.join(timeout)
            if t.is_alive() and self._deleting.is_set():
                # 원본을 지우는 중에 끊으면 한 항목이 원본·대상에 나뉜다 — 그 단계는 끝까지 기다린다(상한 있음)
                deadline = time.monotonic() + max(0.0, float(self.delete_grace))
                while t.is_alive() and self._deleting.is_set() and time.monotonic() < deadline:
                    t.join(0.2)
                t.join(1.0)                    # 그 항목의 기록(done 등)까지 남길 짧은 여유
        self._save(force=True)
        return not (t is not None and t.is_alive())

    # ── 조회 ──
    def get(self, job_id):
        with self._lock:
            j = self._jobs.get(job_id)
            return self._public(j) if j else None

    def jobs(self, limit=200):
        with self._lock:
            ids = list(reversed(self._order))[:max(0, int(limit))]
            return [self._public(self._jobs[i]) for i in ids]

    def deleting(self):
        """원본 삭제 단계(취소를 받지 않는다)가 진행 중인가 — stop() 이 그 단계가 끝나길 기다린다."""
        return self._deleting.is_set()

    def active(self):
        with self._lock:
            return [self._public(self._jobs[i]) for i in self._order
                    if self._jobs[i]["status"] in ("queued", "running")]

    def result_of(self, job_id):
        """submit_task 의 fn 이 돌려준 값(메모리에만, 재시작하면 없다)."""
        return self._returns.get(job_id)

    def cancel(self, job_id):
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return False
            if j["status"] == "queued":
                self._finish_unrun(j, "취소했어요.")
                done = self._public(j)
            elif j["status"] == "running":
                self._events[job_id].set()
                return True
            else:
                return False
        self._save(force=True)
        self._hook("on_job_done", done)
        return True

    # ── 제출 ──
    def submit_recycle(self, items):
        specs = self._parse_items(items)
        drives = self._drives()
        blocked = []
        for sp in specs:
            for p in self._spec_paths(sp):
                ok, reason = self._check(p)
                if not ok:
                    blocked.append({"path": p, "reason": reason})
                    break
        if blocked:
            raise OpError("protected", "보호된 항목이 있어 시작하지 않았어요.", {"blocked": blocked})
        nobin, nobin_drives = [], []
        for sp in specs:
            hit = safety.drive_of(self._spec_paths(sp)[0], drives)
            if hit and _no_bin(hit[0]):
                nobin.append(self._spec_label(sp))
                if _letter_of(hit[0]) not in nobin_drives:
                    nobin_drives.append(_letter_of(hit[0]))
        if nobin:
            raise OpError("no_bin", "휴지통이 없는 위치(NAS·USB 메모리 등)의 항목이 있어요. "
                          "「삭제(복구 불가)」로만 지울 수 있어요.", {"paths": nobin, "drives": nobin_drives})
        plan = []
        tb = 0
        for sp in specs:
            hit = safety.drive_of(self._spec_paths(sp)[0], drives)
            letter = _letter_of(hit[0]) if hit else ""
            est = self._hook_size(sp["src"] if sp["kind"] == "path" else sp["label"])
            b, f = est if est else (0, 0)
            plan.append(dict(sp, bytes=b, files=f, src_letter=letter, done=False))
            tb += b
        title = "휴지통으로 보내기 · " + self._items_title(plan)
        return self._enqueue("recycle", "recycle", title, _items=plan, bytes_total=tb, files_total=len(plan))

    def submit_delete(self, items, limits=None):
        """영구 삭제(복구 불가). 휴지통이 없는 위치용 — 또는 사용자가 명시적으로 고른 경우. 보호 경로는 거부.

        limits: {경로: (바이트, 파일 수)} — 사용자가 확인한 양. 실행 때 디스크에 그보다 많으면 그 항목은 지우지 않는다
        (큐에서 기다리는 동안 동료가 파일을 넣은 경우 — 동의한 것보다 더 지우지 않는다)."""
        specs = self._parse_items(items)
        lim = {}
        if isinstance(limits, dict):
            for k, v in limits.items():
                if isinstance(k, str) and k and isinstance(v, (list, tuple)) and len(v) == 2:
                    lim[_key(self._norm(k))] = (int(v[0]), int(v[1]))
        drives = self._drives()
        blocked = []
        for sp in specs:
            for p in self._spec_paths(sp):
                ok, reason = self._check(p)
                if not ok:
                    blocked.append({"path": p, "reason": reason})
                    break
        if blocked:
            raise OpError("protected", "보호된 항목이 있어 시작하지 않았어요.", {"blocked": blocked})
        plan, tb = [], 0
        for sp in specs:
            hit = safety.drive_of(self._spec_paths(sp)[0], drives)
            if hit is None:
                raise OpError("protected", "보호된 항목이 있어 시작하지 않았어요.",
                              {"blocked": [{"path": self._spec_label(sp), "reason": safety.R_UNKNOWN}]})
            est = self._hook_size(self._spec_label(sp))
            b, f = est if est else (0, 0)
            ent = dict(sp, bytes=b, files=f, src_letter=_letter_of(hit[0]), done=False)
            cap = lim.get(_key(sp["src"])) if sp["kind"] == "path" else None
            if cap is not None:
                ent["limit_b"], ent["limit_f"] = cap
            plan.append(ent)
            tb += b
        title = "영구 삭제 · " + self._items_title(plan)
        return self._enqueue("delete", "delete", title, _items=plan, bytes_total=tb, files_total=len(plan))

    def submit_move(self, items, target=None, dest_dir=None, verify="quick", mode="move"):
        """이동·복사. target 은 드라이브 키('H'·'N-3fa9c1'), 또는 None/'' + dest_dir(사용자가 고른 아무 폴더)."""
        if mode not in MODES:
            raise OpError("invalid", "작업 방식이 올바르지 않아요.", {"mode": mode})
        if verify not in VERIFY_LEVELS:
            raise OpError("invalid", "검증 수준이 올바르지 않아요.", {"verify": verify})
        specs = self._parse_items(items)
        drives = self._drives()
        tdrive = self._resolve_target(drives, target, dest_dir)
        plan, blocked, same, tb, tf, dd, into = self._plan_move(specs, drives, tdrive, dest_dir, mode)
        if into:
            raise OpError("invalid_dest", R_INTO_SELF, {"paths": into})
        if blocked:
            raise OpError("protected", "보호된 항목이 있어 시작하지 않았어요.", {"blocked": blocked})
        if same:
            if mode == "copy":
                raise OpError("need_dest", "같은 드라이브 안에 복사하려면 복사할 폴더를 골라 주세요.", {"paths": same})
            raise OpError("same_drive", "같은 드라이브로는 옮길 수 없어요.", {"paths": same})
        if not plan:
            raise OpError("empty", "옮길 항목이 없어요." if mode == "move" else "복사할 항목이 없어요.")
        T = _letter_of(tdrive)
        free = self._effective_free(tdrive, dd)
        if free is not None and free < tb + self.margin:
            raise OpError("space", self._space_msg(_disp(tdrive), tb + self.margin, free),
                          {"need": tb + self.margin, "free": free, "bytes": tb, "target": T})
        name = "%s → %s" % (self._items_title(plan), _disp(tdrive))
        title = ("복사 · " + name) if mode == "copy" else name
        return self._enqueue(mode, "move", title, _items=plan, _target=T, _verify=verify, _mode=mode,
                             _dest_dir=dd, _tkind=tdrive.get("kind"), mode=mode, target=T,
                             target_label=_disp(tdrive), bytes_total=tb, files_total=tf)

    def preview_move(self, items, target=None, dest_dir=None, mode="move"):
        if mode not in MODES:
            raise OpError("invalid", "작업 방식이 올바르지 않아요.", {"mode": mode})
        specs = self._parse_items(items)
        drives = self._drives()
        tdrive = self._resolve_target(drives, target, dest_dir)
        plan, blocked, same, tb, tf, dd, into = self._plan_move(specs, drives, tdrive, dest_dir, mode)
        for p in into:
            blocked.append({"path": p, "reason": R_INTO_SELF})
        for p in same:
            blocked.append({"path": p, "reason": "같은 드라이브 안에 복사하려면 폴더를 골라 주세요." if mode == "copy"
                            else "같은 드라이브로는 옮길 수 없어요."})
        free = self._effective_free(tdrive, dd)
        free = int(free) if free is not None else 0
        examples, conflicts = [], 0
        reserved = set()
        for it in plan:
            dest, coll = self._preview_dest(it, reserved)
            if coll:
                conflicts += 1
            if len(examples) < 3:
                examples.append(dest)
        T = _letter_of(tdrive)
        freed = {}
        if mode == "move":
            for it in plan:
                freed[it["src_letter"]] = freed.get(it["src_letter"], 0) + it["bytes"]
        return {"ok": True, "mode": mode, "target": T, "target_label": _disp(tdrive),
                "target_kind": tdrive.get("kind"), "bytes": tb, "files": tf, "target_free": free,
                "target_free_after": free - tb, "enough": free >= tb + self.margin,
                "blocked": blocked, "dest_examples": examples, "conflicts": conflicts,
                "freed": freed, "added": {T: tb} if tb else {},
                "need_dest": bool(same) and mode == "copy"}

    def _resolve_target(self, drives, target, dest_dir):
        """대상 드라이브 dict. target 이 없으면 dest_dir 이 속한 드라이브(그 폴더는 이미 있어야 한다)."""
        tkey = winapi.norm_key(target) if isinstance(target, str) else ""
        if isinstance(target, str) and target.strip() and not tkey:
            raise OpError("unknown_drive", "대상 드라이브를 찾을 수 없어요.", {"target": target})
        if tkey:
            tdrive = _find_drive(drives, tkey)
            if tdrive is None:
                raise OpError("unknown_drive", "대상 드라이브를 찾을 수 없어요.", {"target": target})
        else:
            if not (isinstance(dest_dir, str) and dest_dir.strip()):
                raise OpError("unknown_drive", "옮길 드라이브나 폴더를 골라 주세요.", {"target": target})
            nd = safety.normalize(dest_dir)
            hit = safety.drive_of(nd, drives) if nd else None
            if hit is None:
                raise OpError("invalid_dest", "고른 폴더가 DriveBoard 가 아는 드라이브·NAS 안에 있지 않아요.",
                              {"dest_dir": dest_dir})
            if not os.path.isdir(_lp(nd)) or _is_reparse_path(nd):
                raise OpError("invalid_dest", "고른 폴더를 찾을 수 없거나 일반 폴더가 아니에요.", {"dest_dir": dest_dir})
            tdrive = hit[0]
        if isinstance(tdrive, dict) and tdrive.get("connected") is False:
            raise OpError("unknown_drive", R_DEST_OFFLINE, {"target": _letter_of(tdrive)})
        return tdrive

    def submit_empty_bin(self, letters):
        if isinstance(letters, str):
            letters = [letters]
        if not isinstance(letters, (list, tuple)):
            raise OpError("invalid", "드라이브 목록이 올바르지 않아요.")
        drives = self._drives()
        out = []
        for L in letters:
            d = _find_drive(drives, L)
            if d is None:
                raise OpError("unknown_drive", "드라이브를 찾을 수 없어요: %s" % L, {"target": L})
            if _letter_of(d) not in out:
                out.append(_letter_of(d))
        if not out:
            raise OpError("empty", "비울 휴지통을 고르지 않았어요.")
        title = "휴지통 비우기 · " + ", ".join(x + ":" for x in out)
        return self._enqueue("empty_bin", "empty_bin", title, _letters=out, files_total=len(out))

    def submit_task(self, type, title, fn):
        if not callable(fn):
            raise OpError("invalid", "작업 함수가 올바르지 않아요.")
        return self._enqueue(str(type), "task", str(title), _fn=fn)

    def undo(self, job_id, confirm=True):
        """confirm=False: 휴지통 없는 곳의 복사본을 지우는(영구 삭제) 되돌리기는 need_confirm 으로 거절한다(HTTP 경로)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise OpError("not_found", "작업을 찾을 수 없어요.")
            if (job.get("type") not in ("move", "copy") or job.get("status") in ("queued", "running")
                    or not job.get("undoable") or job.get("_undo_pending")):
                raise OpError("not_undoable", "되돌릴 수 없는 작업이에요.")
        if job.get("type") == "copy":
            return self._undo_copy(job_id, job, confirm=confirm)
        with self._lock:
            todo = [(i, it) for i, it in enumerate(job.get("_items") or [])
                    if it.get("done") and not it.get("undone")]
            verify = job.get("_verify", "quick")
            title = "되돌리기 · " + job.get("title", "")
            T = job.get("_target")
        if not todo:
            raise OpError("not_undoable", "되돌릴 항목이 없어요.")
        drives = self._drives()
        expect = self._undo_expect(job, [T] + [it.get("src_letter") for _i, it in todo])
        bad = _serial_mismatch(drives, expect)
        if bad:
            raise OpError("drive_changed", R_DRIVE_CHANGED % _disp(_find_drive(drives, bad) or {"letter": bad}),
                          {"target": bad})
        plan, blocked = [], []
        need = collections.Counter()
        for idx, it in todo:
            if it["kind"] == "path":
                cur, orig = it.get("dst_final"), it["src"]
                for p, label in ((cur, "옮긴 항목"), (orig, "원래 위치")):
                    ok, r = self._check(p)
                    if not ok:
                        blocked.append({"path": p, "reason": "%s: %s" % (label, r)})
                plan.append({"kind": "path", "src": cur, "dst_base": orig, "bytes": it.get("bytes", 0),
                             "files": it.get("files", 0), "src_letter": T,
                             "dst_letter": it.get("src_letter"), "orig": idx, "done": False})
            else:
                fin = it.get("members_final") or it["members"]
                if not fin or not it.get("rels"):
                    continue
                moved = it.get("moved") or []
                pairs = [[fin[i][1], fin[i][0]] for i in moved]
                rels = [it["rels"][i] for i in moved]
                for cur, orig in pairs[:1] + pairs[-1:]:
                    for p, label in ((cur, "옮긴 항목"), (orig, "원래 위치")):
                        ok, r = self._check(p)
                        if not ok:
                            blocked.append({"path": p, "reason": "%s: %s" % (label, r)})
                mod = safety.mod_for(it["label"])
                plan.append({"kind": "group",
                             "label": mod.join(it.get("final_dir") or it["coll_dir"], mod.basename(it["label"])),
                             "orig_label": it["label"], "members": pairs, "rels": rels,
                             "coll_dir": it["base_dir"], "base_dir": it.get("final_dir") or it["coll_dir"],
                             "bytes": it.get("bytes", 0), "files": len(pairs), "src_letter": T,
                             "dst_letter": it.get("src_letter"), "orig": idx, "done": False})
            need[it.get("src_letter")] += it.get("bytes", 0)
        if not plan:
            raise OpError("not_undoable", "되돌릴 항목이 없어요.")
        if blocked:
            raise OpError("protected", "보호된 경로가 있어 되돌릴 수 없어요.", {"blocked": blocked})
        for L, b in need.items():
            d = _find_drive(drives, L)
            if d is None:
                raise OpError("unknown_drive", "원래 드라이브를 찾을 수 없어요: %s" % L, {"target": L})
            free = self._effective_free(d)
            if free is not None and free < b + self.margin:
                raise OpError("space", self._space_msg(L, b + self.margin, free),
                              {"need": b + self.margin, "free": free, "bytes": b, "target": L})
        with self._lock:
            if job.get("_undo_pending") or not job.get("undoable"):
                raise OpError("not_undoable", "이미 되돌리는 중이에요.")
            nid = self._enqueue("undo", "move", title, _items=plan, _target=None, _verify=verify,
                                undo_of=job_id, _expect=expect, bytes_total=sum(x["bytes"] for x in plan),
                                files_total=sum(x["files"] for x in plan))
            job["_undo_pending"] = nid
            job["undoable"] = False
        self._save(force=True)
        return nid

    @staticmethod
    def _undo_expect(job, letters):
        """되돌리기가 기대하는 {문자: 일련번호} — 원 작업이 실행될 때 기록한 것(_serials) 중 letters 만."""
        rec = job.get("_serials") or {}
        return {L: rec[L] for L in letters if L and rec.get(L)}

    @staticmethod
    def _copy_undo_items(job):
        """복사 작업에서 되돌릴(복사본을 지울) 항목 [(i, it)] — 끝났거나 일부 복사된 것."""
        return [(i, it) for i, it in enumerate(job.get("_items") or [])
                if not it.get("undone") and (it.get("c_files") or it.get("c_sig") or it.get("c_dirs"))]

    @classmethod
    def _is_undoable(cls, job):
        if job.get("_undo_pending"):
            return False
        if job.get("type") == "copy":
            return bool(cls._copy_undo_items(job))
        if job.get("type") == "move":
            return any(it.get("done") and not it.get("undone") for it in job.get("_items") or [])
        return False

    def _undo_copy(self, job_id, job, confirm=True):
        """복사 되돌리기 = 그 작업이 만든 파일·폴더만 지운다(휴지통이 있으면 휴지통, 없으면 영구 삭제).
        지우기 직전에 복사본이 그대로인지, 원본이 아직 있는지 다시 본다 — 아니면 그 복사본은 남긴다."""
        drives = self._drives()
        with self._lock:
            todo = self._copy_undo_items(job)
            T = job.get("_target")
            title = "복사 되돌리기 · " + job.get("title", "").replace("복사 · ", "", 1)
        if not todo:
            raise OpError("not_undoable", "되돌릴 항목이 없어요.")
        tdrive = _find_drive(drives, T)
        if tdrive is None or tdrive.get("connected") is False:
            raise OpError("unknown_drive", "복사한 위치를 찾을 수 없거나 연결되어 있지 않아요.", {"target": T})
        expect = self._undo_expect(job, [T])
        if _serial_mismatch(drives, expect):
            raise OpError("drive_changed", R_DRIVE_CHANGED % _disp(tdrive), {"target": T})
        blocked = []
        for _i, it in todo:
            top = it.get("dst_final") or it.get("final_dir")
            if top:
                ok, r = self._check(top)
                if not ok:
                    blocked.append({"path": top, "reason": r})
        if blocked:
            raise OpError("protected", "보호된 경로가 있어 되돌릴 수 없어요.", {"blocked": blocked})
        permanent = _no_bin(tdrive)
        if permanent and not confirm:
            raise OpError("need_confirm", "%s 에는 휴지통이 없어서 복사본이 영구 삭제돼요. 확인한 뒤 다시 요청해 주세요."
                          % _disp(tdrive), {"permanent": True, "target": T})
        plan = [{"kind": "uncopy", "orig": i, "bytes": int(it.get("bytes") or 0), "done": False,
                 "label": it.get("dst_final") or it.get("final_dir") or self._item_path(it)} for i, it in todo]
        with self._lock:
            if job.get("_undo_pending") or not job.get("undoable"):
                raise OpError("not_undoable", "이미 되돌리는 중이에요.")
            nid = self._enqueue("undo", "uncopy", title, _items=plan, _target=T, undo_of=job_id,
                                _expect=expect, permanent=permanent, bytes_total=sum(x["bytes"] for x in plan),
                                files_total=len(plan))
            job["_undo_pending"] = nid
            job["undoable"] = False
        self._save(force=True)
        return nid

    # ── 내부: 항목 해석 ──
    def _parse_items(self, items):
        if isinstance(items, (str, dict)):
            items = [items]
        if not isinstance(items, (list, tuple)):
            raise OpError("invalid", "항목 목록이 올바르지 않아요.")
        specs = []
        for x in items:
            if isinstance(x, str) and x:
                specs.append({"kind": "path", "src": self._norm(x)})
            elif isinstance(x, dict):
                ps = x.get("paths")
                if (not isinstance(ps, (list, tuple)) or not ps
                        or not all(isinstance(p, str) and p for p in ps)):
                    raise OpError("invalid", "묶음 항목의 경로 목록이 올바르지 않아요.")
                members, seen = [], set()
                for p in ps:
                    n = self._norm(p)
                    if _key(n) not in seen:
                        seen.add(_key(n))
                        members.append(n)
                label = x.get("label") if isinstance(x.get("label"), str) and x.get("label") else members[0]
                specs.append({"kind": "group", "label": self._norm(label), "members": members})
            else:
                raise OpError("invalid", "항목 목록이 올바르지 않아요.")
        specs = self._dedupe(specs)
        if not specs:
            raise OpError("empty", "고른 항목이 없어요.")
        return specs

    @staticmethod
    def _norm(p):
        n = safety.normalize(p)
        return n if n else p

    @staticmethod
    def _dedupe(specs):
        """같은 항목·다른 항목 안에 든 항목을 뺀다(폴더와 그 안의 파일을 같이 고른 경우)."""
        paths = sorted({_key(sp["src"]): sp for sp in specs if sp["kind"] == "path"}.items(),
                       key=lambda kv: len(kv[0]))
        kept = set()
        for k, _sp in paths:
            if any(a in kept for a in _ancestor_keys(k)):
                continue
            kept.add(k)
        out, labels, emitted = [], set(), set()
        for sp in specs:
            if sp["kind"] == "path":
                k = _key(sp["src"])
                if k in kept and k not in emitted:
                    emitted.add(k)
                    out.append(sp)
            else:
                mem = [m for m in sp["members"]
                       if _key(m) not in kept and not any(a in kept for a in _ancestor_keys(_key(m)))]
                lk = _key(sp["label"])
                if mem and lk not in labels:
                    labels.add(lk)
                    out.append(dict(sp, members=mem))
        return out

    @staticmethod
    def _spec_paths(sp):
        return [sp["src"]] if sp["kind"] == "path" else list(sp["members"])

    @staticmethod
    def _spec_label(sp):
        return sp["src"] if sp["kind"] == "path" else sp["label"]

    def _plan_move(self, specs, drives, tdrive, dest_dir, mode="move"):
        """(plan, blocked, same, bytes, files, dest_dir, into_self)."""
        T = _letter_of(tdrive)
        troot = safety._drive_root(tdrive)
        tmod = safety.mod_for(troot)
        if isinstance(dest_dir, str) and dest_dir.strip():
            nd = safety.normalize(dest_dir)
            hit = safety.drive_of(nd, drives) if nd else None
            if not nd or not hit or _letter_of(hit[0]) != T:
                raise OpError("invalid_dest", "옮길 폴더가 대상 드라이브 안에 있지 않아요.", {"dest_dir": dest_dir})
            if os.path.lexists(_lp(nd)) and (not os.path.isdir(_lp(nd)) or _is_reparse_path(nd)):
                raise OpError("invalid_dest", "옮길 위치가 일반 폴더가 아니에요.", {"dest_dir": dest_dir})
            if hit[1]:
                ok, r = self._check(nd)
                if not ok:
                    raise OpError("invalid_dest", "옮길 위치가 보호된 곳이에요: " + r, {"dest_dir": dest_dir})
            dest_dir = nd
        else:
            dest_dir = None
        plan, blocked, same, into = [], [], [], []
        tb = tf = 0
        for sp in specs:
            paths = self._spec_paths(sp)
            if dest_dir and sp["kind"] == "path" and (
                    _inside_key(dest_dir, paths[0]) or self._overlaps_safe(paths[0], dest_dir)):
                into.append(paths[0])                 # 자기 자신 안으로 — 끝없는 복사·원본 손실
                continue
            bad = False
            for p in paths:
                ok, r = self._check(p)
                if not ok:
                    blocked.append({"path": p, "reason": r})
                    bad = True
                    break
            if bad:
                continue
            hit = safety.drive_of(paths[0], drives)
            if hit is None:
                blocked.append({"path": paths[0], "reason": safety.R_UNKNOWN})
                continue
            sdrive, rel = hit
            SL = _letter_of(sdrive)
            if SL == T and not (mode == "copy" and dest_dir):
                same.append(sp["src"] if sp["kind"] == "path" else sp["label"])
                continue
            if sp["kind"] == "path":
                dst = tmod.join(dest_dir, rel[-1]) if dest_dir else tmod.join(troot, *rel)
                ok, r = self._check(dst)
                if not ok:
                    blocked.append({"path": paths[0], "reason": "옮길 위치가 보호된 곳이에요: " + r})
                    continue
                b, f = self._estimate(paths[0])
                plan.append({"kind": "path", "src": paths[0], "dst_base": dst, "bytes": b, "files": f,
                             "src_letter": SL, "done": False})
            else:
                rels_all = []
                for m in paths:
                    h = safety.drive_of(m, drives)
                    if h is None or _letter_of(h[0]) != SL or not h[1]:
                        rels_all = None
                        break
                    rels_all.append(h[1])
                if rels_all is None:
                    blocked.append({"path": sp["label"], "reason": "한 묶음의 파일이 여러 드라이브에 있어요."})
                    continue
                base_rel = list(rels_all[0][:-1])
                for r_ in rels_all[1:]:
                    n = 0
                    while (n < len(base_rel) and n < len(r_) - 1
                           and base_rel[n].casefold() == r_[n].casefold()):
                        n += 1
                    base_rel = base_rel[:n]
                sroot = safety._drive_root(sdrive)
                smod = safety.mod_for(sroot)
                base_dir = smod.join(sroot, *base_rel) if base_rel else sroot
                coll_dir = dest_dir if dest_dir else (tmod.join(troot, *base_rel) if base_rel else troot)
                rels = [list(r_[len(base_rel):]) for r_ in rels_all]
                pairs = [[m, tmod.join(coll_dir, *r_)] for m, r_ in zip(paths, rels)]
                ok, r = self._check(pairs[0][1])
                if not ok:
                    blocked.append({"path": sp["label"], "reason": "옮길 위치가 보호된 곳이에요: " + r})
                    continue
                b, f = self._estimate_group(sp["label"], paths)
                plan.append({"kind": "group", "label": sp["label"], "members": pairs, "rels": rels,
                             "coll_dir": coll_dir, "base_dir": base_dir, "bytes": b, "files": f,
                             "src_letter": SL, "done": False})
            tb += plan[-1]["bytes"]
            tf += plan[-1]["files"]
        return plan, blocked, same, tb, tf, dest_dir, into

    @staticmethod
    def _overlaps_safe(src, dest_dir):
        """실제 경로(링크·subst 풀어서)로 dest_dir 이 src 안인가. 폴더가 아니면 False."""
        try:
            if not os.path.isdir(_lp(src)) or _is_reparse_path(src):
                return False
            return _overlaps(src, dest_dir, safety.mod_for(dest_dir))
        except OSError:
            return True

    def _preview_dest(self, it, reserved):
        if it["kind"] == "path":
            try:
                is_dir = os.path.isdir(_lp(it["src"]))
            except OSError:
                is_dir = False
            mod = safety.mod_for(it["dst_base"])
            dst = self._free_dest(mod, it["dst_base"], is_dir, reserved)
            return dst, dst != it["dst_base"]
        mod = safety.mod_for(it["coll_dir"])
        coll = any(os.path.lexists(_lp(d)) or d.casefold() in reserved for _s, d in it["members"])
        final = it["coll_dir"]
        if coll:
            final = self._free_folder(mod, it["coll_dir"], _group_folder_name(it["label"], mod), reserved)
        for r_ in it["rels"]:
            reserved.add(mod.join(final, *r_).casefold())
        return mod.join(final, mod.basename(it["label"])), coll

    # ── 내부: 크기·공간 ──
    def _hook_size(self, path):
        v = self._hook("size_of", path)
        if isinstance(v, (tuple, list)) and len(v) >= 2:
            try:
                return int(v[0]), int(v[1])
            except (TypeError, ValueError):
                return None
        return None

    def _estimate(self, path):
        v = self._hook_size(path)
        if v is not None:
            return v
        try:
            st = os.lstat(_lp(path))
        except OSError:
            return 0, 0
        if stat.S_ISDIR(st.st_mode) and not _is_reparse_st(st):
            return _tree_bytes(path)
        return st.st_size, 1

    def _estimate_group(self, label, members):
        v = self._hook_size(label)
        if v is not None:
            return v
        b = 0
        for m in members:
            try:
                b += os.lstat(_lp(m)).st_size
            except OSError:
                pass
        return b, len(members)

    def _fresh_free(self, drive, path=None):
        """대상의 실제 남은 공간 — 고른 폴더가 있으면 그 폴더 기준(마운트·NAS 할당량이 다를 수 있다)."""
        where = path if isinstance(path, str) and path else safety._drive_root(drive)
        try:
            return int(winapi.disk_usage(where)[1])
        except Exception:
            if where != safety._drive_root(drive):
                try:
                    return int(winapi.disk_usage(safety._drive_root(drive))[1])
                except Exception:
                    pass
            f = drive.get("free") if isinstance(drive, dict) else None
            return int(f) if isinstance(f, (int, float)) else None

    def _reserved_bytes(self, letter):
        total = 0
        with self._lock:
            for jid in self._order:
                j = self._jobs[jid]
                if j["status"] not in ("queued", "running") or j.get("_kind") != "move":
                    continue
                for it in j.get("_items") or []:
                    if it.get("_finished"):
                        continue
                    if (it.get("dst_letter") or j.get("_target")) == letter:
                        total += int(it.get("bytes") or 0)
        return total

    def _effective_free(self, drive, path=None):
        f = self._fresh_free(drive, path)
        if f is None:
            return None
        return f - self._reserved_bytes(_letter_of(drive))

    @staticmethod
    def _space_msg(name, need, free):
        k = winapi.norm_key(name)
        name = (k + ": 드라이브") if len(k) == 1 else str(name)
        return ("%s 공간이 부족해요. 필요 %s(여유 1GB 포함) · 남은 공간 %s"
                % (name, fmt_size(need), fmt_size(max(0, free))))

    # ── 내부: 공통 ──
    def _drives(self):
        try:
            d = self.drives_provider()
            return list(d or [])
        except Exception:
            return []

    def _check(self, path):
        try:
            res = self.safety_check(path)
            ok, reason = res[0], res[1]
        except Exception as e:
            return False, "보호 경로를 판정하지 못했어요: %s" % e
        return bool(ok), (reason or "")

    def _hook(self, name, *args):
        h = self._hooks
        fn = h.get(name) if isinstance(h, dict) else getattr(h, name, None)
        if fn is None:
            return None
        try:
            return fn(*args)
        except Exception:
            self.hook_errors += 1
            return None

    def _has_hook(self, name):
        h = self._hooks
        return (h.get(name) if isinstance(h, dict) else getattr(h, name, None)) is not None

    def _changed(self, path):
        if self._has_hook("on_changed"):
            self._hook("on_changed", path)
        else:
            self._hook("on_removed", path)
            self._hook("on_added", path)

    def _items_title(self, plan):
        if len(plan) == 1:
            it = plan[0]
            p = it.get("src") if it["kind"] == "path" else it.get("label")
            return safety.mod_for(p).basename(p) or p
        return "%d개 항목" % len(plan)

    def _enqueue(self, jtype, kind, title, **fields):
        with self._lock:
            self._seq += 1
            jid = "j%d-%d" % (int(time.time() * 1000), self._seq)
            job = {"id": jid, "type": jtype, "status": "queued", "title": title,
                   "created_at": time.time(), "started_at": None, "finished_at": None,
                   "bytes_total": 0, "bytes_done": 0, "files_total": 0, "files_done": 0,
                   "current": "", "speed_bps": 0, "eta_s": None, "freed": {}, "added": {},
                   "results": [], "undoable": False, "message": "", "_kind": kind}
            fn = fields.pop("_fn", None)
            job.update(fields)
            if fn is not None:
                self._fns[jid] = fn
            self._jobs[jid] = job
            self._order.append(jid)
            self._events[jid] = threading.Event()
            self._trim()
        self._save(force=True)
        self._q.put(jid)
        return jid

    def _trim(self):
        while len(self._order) > MAX_JOBS:
            for jid in self._order:
                if self._jobs[jid]["status"] not in ("queued", "running"):
                    self._order.remove(jid)
                    self._jobs.pop(jid, None)
                    self._events.pop(jid, None)
                    self._returns.pop(jid, None)
                    break
            else:
                return

    def _public(self, j):
        out = {}
        for k, v in j.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                v = dict(v)
            elif isinstance(v, list):
                v = list(v)
            out[k] = v
        return out

    def _finish_unrun(self, j, msg):
        j["status"] = "canceled"
        j["finished_at"] = time.time()
        j["message"] = msg
        if j.get("type") == "undo":
            self._finish_undo(j)

    # ── 저장 ──
    def _save(self, force=False):
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_save < 1.0:
                self._dirty = True
                return
            self._last_save = now
            self._dirty = False
            try:
                text = json.dumps({"version": 1, "jobs": [self._jobs[j] for j in self._order]},
                                  ensure_ascii=False)
            except (TypeError, ValueError) as e:
                self.save_error = str(e)
                return
        with self._save_lock:
            try:
                _atomic_write(self._path, text)
                self.save_error = None
            except OSError as e:
                self.save_error = str(e)

    def _load(self):
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            try:
                os.replace(self._path, self._path + ".bad")
            except OSError:
                pass
            return
        jobs = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            return
        now = time.time()
        for j in jobs:
            if not (isinstance(j, dict) and isinstance(j.get("id"), str) and isinstance(j.get("status"), str)):
                continue
            j.setdefault("results", [])
            j.setdefault("freed", {})
            j.setdefault("added", {})
            j.setdefault("_kind", j.get("type"))
            if j["status"] in ("queued", "running"):
                msg = "앱이 종료되어 작업이 중단됐어요."
                if j.get("_kind") == "move" and j.get("_mode") == "copy":
                    for it in j.get("_items") or []:
                        if it.get("dst_final") and not it.get("_finished"):
                            msg += " 대상에 일부 사본이 남았을 수 있어요: %s" % it["dst_final"]
                            break
                elif j.get("_kind") == "move":
                    msg += self._interrupted_move_msg(j)
                j["status"] = "failed"
                if j.get("_kind") == "move" and any(it.get("done") or it.get("partial")
                                                    for it in j.get("_items") or []):
                    j["status"] = "partial"         # 끝난 항목은 옮겨졌다 — '실패'가 아니다(되돌리기도 남는다)
                j["finished_at"] = now
                j["message"] = msg
            self._jobs[j["id"]] = j
            self._order.append(j["id"])
            self._events[j["id"]] = threading.Event()
        for jid in list(self._order):
            j = self._jobs[jid]
            if j.get("type") == "undo" and j.get("undo_of") in self._jobs:
                orig = self._jobs[j["undo_of"]]
                if orig.get("_undo_pending") == jid:
                    self._finish_undo(j)
        for jid in self._order:
            j = self._jobs[jid]
            if j.get("type") in ("move", "copy"):
                j["undoable"] = self._is_undoable(j)
        self._trim()

    @staticmethod
    def _interrupted_move_msg(j):
        """앱이 이동 도중 종료됐을 때의 안내 — 저장된 항목 상태로 지어낸다(원본이 그대로라고 단정하지 않는다)."""
        items = j.get("_items") or []
        undo = j.get("type") == "undo"
        done = [it for it in items if it.get("done")]
        part = [it for it in items if it.get("partial") and not it.get("done")]
        live = [it for it in items if not it.get("_finished")]
        split = [it for it in live if it.get("deleting")]
        copying = [it for it in live if not it.get("deleting") and (it.get("dst_final") or it.get("final_dir"))]

        def where(it):
            return it.get("dst_final") or it.get("final_dir") or JobQueue._item_path(it) or "?"

        parts = []
        if done:
            parts.append("끝난 %d개는 %s(원래 위치에서는 지워졌어요)." % (len(done), "원래 위치로 되돌렸어요" if undo
                                                                     else "옮겨졌어요"))
        if part:
            parts.append("%d개는 일부만 옮겨졌어요." % len(part))
        for it in split:
            parts.append("%s 은 원본을 지우던 중이었어요 — 원래 위치와 %s 에 나뉘어 있을 수 있으니 "
                         "어느 쪽도 지우지 말고 확인해 주세요." % (JobQueue._item_path(it), where(it)))
        if not (done or part or split):
            parts.append("원본은 지워지지 않았어요.")
        elif any(not it.get("deleting") for it in live):
            parts.append("나머지 항목의 원본은 지워지지 않았어요.")
        for it in copying[:1]:
            parts.append("대상에 일부 사본이 남았을 수 있어요: %s" % where(it))
        return " " + " ".join(parts)

    # ── 일꾼 ──
    def _worker(self):
        while True:
            try:
                jid = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._dirty:
                    self._save(force=True)
                if self._stopping.is_set():
                    return
                continue
            if jid is None:
                if self._stopping.is_set():
                    return
                continue
            if self._stopping.is_set():
                continue
            try:
                self._run(jid)
            except Exception:
                pass

    def _run(self, jid):
        with self._lock:
            job = self._jobs.get(jid)
            if job is None or job["status"] != "queued":
                return
            job["status"] = "running"
            job["started_at"] = time.time()
            ev = self._events[jid]
        ctx = JobContext(self, job, ev)
        self._save(force=True)
        try:
            kind = job.get("_kind")
            if kind == "recycle":
                self._exec_recycle(ctx, job)
            elif kind == "move":
                self._exec_move(ctx, job)
            elif kind == "empty_bin":
                self._exec_empty(ctx, job)
            elif kind == "delete":
                self._exec_delete(ctx, job)
            elif kind == "uncopy":
                self._exec_uncopy(ctx, job)
            elif kind == "task":
                self._exec_task(ctx, job)
            else:
                with self._lock:
                    job["status"] = "failed"
                    job["message"] = "알 수 없는 작업 종류예요."
        except Exception as e:
            with self._lock:
                job["status"] = "failed"
                job["message"] = "작업 중 오류가 났어요: %s" % e
        finally:
            with self._lock:
                job["finished_at"] = time.time()
                job["speed_bps"] = 0
                job["eta_s"] = 0
                job["current"] = ""
                if job.get("status") == "running":
                    job["status"] = "failed"
                if job.get("type") == "undo":
                    self._finish_undo(job)
                pub = self._public(job)
            self._save(force=True)
            self._hook("on_job_done", pub)

    def _finish_undo(self, ujob):
        orig = self._jobs.get(ujob.get("undo_of"))
        if not orig:
            return
        for it in ujob.get("_items") or []:
            if it.get("done") and isinstance(it.get("orig"), int):
                items = orig.get("_items") or []
                if 0 <= it["orig"] < len(items):
                    items[it["orig"]]["undone"] = True
        if orig.get("_undo_pending") == ujob["id"]:
            orig.pop("_undo_pending", None)
        orig["undoable"] = self._is_undoable(orig)

    # ── 결과 기록 ──
    def _result(self, job, path, ok, error=None, dest=None):
        r = {"path": path, "ok": bool(ok)}
        if dest is not None:
            r["dest"] = dest
        if error:
            r["error"] = error
        with self._lock:
            job["results"].append(r)

    @staticmethod
    def _final_status(items, cancelled):
        n_ok = sum(1 for it in items if it.get("done"))
        n_part = sum(1 for it in items if it.get("partial"))
        if cancelled:
            return "canceled"
        if items and n_ok == len(items):
            return "done"
        if n_ok or n_part:
            return "partial"
        return "failed" if items else "done"

    def _adobe_warning(self, items):
        hit = False
        for it in items:
            for p in ([it.get("src")] if it["kind"] == "path" else [it.get("label")] + list(it.get("members") or [])[:1]):
                if isinstance(p, (list, tuple)):
                    p = p[0]
                if isinstance(p, str) and any(h in p.lower() for h in CACHE_HINTS):
                    hit = True
                    break
            if hit:
                break
        if not hit:
            return ""
        try:
            procs = winapi.running_adobe()
        except Exception:
            procs = []
        if not procs:
            return ""
        names = ", ".join(winapi.adobe_display_names(procs))
        return "실행 중인 앱(%s)이 있어요. 캐시를 정리할 땐 앱을 먼저 닫는 것이 안전해요." % names

    # ── 재검사 ──
    def _reparse_ancestor(self, path, drives, stop_at_missing=False):
        hit = safety.drive_of(path, drives)
        if hit is None:
            return None
        root = safety._drive_root(hit[0])
        mod = safety.mod_for(root)
        cur = root
        for c in hit[1][:-1]:
            cur = mod.join(cur, c)
            try:
                st = os.lstat(_lp(cur))
            except OSError:
                if stop_at_missing:
                    return None
                continue
            if _is_reparse_st(st):
                return cur
            if not stat.S_ISDIR(st.st_mode):
                return cur
        return None

    def _recheck_source(self, path, drives):
        ok, r = self._check(path)
        if not ok:
            return r
        if not os.path.lexists(_lp(path)):
            return R_MISSING
        if _is_reparse_path(path):
            return R_LINK
        if self._reparse_ancestor(path, drives):
            return R_LINK_PATH
        return None

    def _recheck_dest(self, path, drives):
        ok, r = self._check(path)
        if not ok:
            return r
        if self._reparse_ancestor(path, drives, stop_at_missing=True):
            return R_LINK_PATH
        return None

    def _free_dest(self, mod, base, is_dir, reserved):
        cand, n = base, 2
        while os.path.lexists(_lp(cand)) or cand.casefold() in reserved:
            cand = _with_suffix(mod, base, n, is_dir)
            n += 1
            if n > 100000:
                raise _DstError("빈 이름을 찾지 못했어요.")
        reserved.add(cand.casefold())
        return cand

    def _free_folder(self, mod, parent, stem, reserved):
        n = 2
        while True:
            cand = mod.join(parent, "%s (%d)" % (stem, n))
            if not os.path.lexists(_lp(cand)) and cand.casefold() not in reserved:
                reserved.add(cand.casefold())
                return cand
            n += 1
            if n > 100000:
                raise _DstError("빈 폴더 이름을 찾지 못했어요.")

    # ── 이동 ──
    def _exec_move(self, ctx, job):
        items = job["_items"]
        verify = job.get("_verify", "quick")
        copy = job.get("_mode") == "copy"
        drives = self._drives()
        if job.get("type") == "undo":
            bad = _serial_mismatch(drives, job.get("_expect"))
            if bad:
                return self._fail_all(job, items, R_DRIVE_CHANGED % _disp(_find_drive(drives, bad) or {"letter": bad}))
        else:
            letters = {job.get("_target")} | {it.get("src_letter") for it in items}
            with self._lock:                    # 되돌리기가 '같은 디스크인가'를 확인할 수 있게 지금 일련번호를 남긴다
                job["_serials"] = {L: _serial_of(_find_drive(drives, L)) for L in letters
                                   if L and _serial_of(_find_drive(drives, L))}
        need = collections.Counter()
        for it in items:
            need[it.get("dst_letter") or job.get("_target")] += int(it.get("bytes") or 0)
        ctx.troots = {}
        kinds = set()
        for L, b in need.items():
            d = _find_drive(drives, L)
            if d is None:
                return self._fail_all(job, items, "대상 드라이브를 찾을 수 없어요: %s" % L)
            if d.get("connected") is False:
                return self._fail_all(job, items, R_DEST_OFFLINE)
            ctx.troots[L] = safety._drive_root(d)
            kinds.add(d.get("kind"))
            free = self._fresh_free(d, job.get("_dest_dir") if L == job.get("_target") else None)
            if free is not None and free < b + self.margin:
                return self._fail_all(job, items, self._space_msg(_disp(d), b + self.margin, free))
        ctx.chunk = self.chunk_size
        for k in kinds:
            if k in SLOW_CHUNK:
                ctx.chunk = min(ctx.chunk, SLOW_CHUNK[k])
        warn = self._adobe_warning(items)
        reserved = set()
        stop_reason = None
        cancelled = False
        copystat_warn = 0
        for it in items:
            if stop_reason or ctx.cancelled():
                cancelled = cancelled or (not stop_reason)
                self._result(job, self._item_path(it), False, error=stop_reason or R_CANCELED)
                it["_finished"] = True
                self._compact(it)
                continue
            try:
                if it["kind"] == "group":
                    res = self._move_group(ctx, job, it, drives, verify, reserved, copy)
                else:
                    res = self._move_path(ctx, job, it, drives, verify, reserved, copy)
            except _Cancelled:
                cancelled = True
                it.pop("dst_final", None)
                it.pop("final_dir", None)
                self._result(job, self._item_path(it), False, error=R_CANCELED)
                it["_finished"] = True
                self._compact(it)
                continue
            it["_finished"] = True
            deleted = it.pop("deleting", False)
            copystat_warn += res.get("warn", 0)
            if res.get("stop"):
                stop_reason = res["stop"]
            try:
                winapi.invalidate_usage(self._item_path(it))
                if it.get("dst_final") or it.get("final_dir"):
                    winapi.invalidate_usage(it.get("dst_final") or it.get("final_dir"))
            except Exception:
                pass
            self._compact(it)
            # 원본을 지운 항목은 바로 디스크에 남긴다 — 다음 항목을 복사하는 동안 앱이 죽어도 기록이
            # '원본은 그대로'라고 거짓말하지 않고, 끝난 항목은 되돌릴 수 있게.
            self._save(force=deleted)
        devlost = stop_reason == R_DEVLOST
        status = self._final_status(items, cancelled)
        n_ok = sum(1 for it in items if it.get("done"))
        if devlost and status == "done":
            status = "partial"
        moved = sum(job["added" if copy else "freed"].values())
        is_undo = job.get("type") == "undo"
        if status == "done":
            if is_undo:
                msg = "%d개 항목(%s)을 원래 위치로 되돌렸어요." % (n_ok, fmt_size(moved))
            elif copy:
                msg = "%d개 항목(%s)을 복사했어요." % (n_ok, fmt_size(moved))
            else:
                msg = "%d개 항목(%s)을 옮겼어요." % (n_ok, fmt_size(moved))
        elif status == "partial":
            msg = ("%d개는 복사했고 %d개는 다 복사하지 못했어요." if copy
                   else "%d개는 옮겼고 %d개는 다 옮기지 못했어요.") % (n_ok, len(items) - n_ok)
        elif status == "canceled":
            msg = "취소했어요. 이미 끝난 %d개는 %s 채로 두었어요." % (n_ok, "복사된" if copy else "옮겨진")
        else:
            errs = [r.get("error") for r in job["results"] if r.get("error")]
            msg = ("복사하지 못했어요." if copy else "옮기지 못했어요.") + (" " + errs[0] if errs else "")
        if devlost and R_DEVLOST not in msg:
            msg = R_DEVLOST + " " + msg
        if devlost and not copy and any(it.get("partial") for it in items):
            msg += " 다 옮기지 못한 항목은 원래 위치와 대상에 나뉘어 있고, 일부 파일은 양쪽에 다 있을 수 있어요."
        elif devlost:
            msg += " 원본은 그대로예요."
        if copystat_warn:
            msg += " (수정 시각을 %s 못한 파일 %d개)" % ("복사하지" if copy else "옮기지", copystat_warn)
        with self._lock:
            job["status"] = status
            job["message"] = (warn + " " + msg).strip() if warn else msg
            if job.get("type") == "copy":
                job["undoable"] = self._is_undoable(job)
            else:
                job["undoable"] = job.get("type") == "move" and n_ok > 0

    @staticmethod
    def _compact(it):
        """끝난 묶음 항목의 큰 목록을 줄인다 — 되돌리기에 필요한 것(완료 항목의 최종 경로·rels)만 남긴다."""
        if it.get("kind") != "group":
            return
        n = len(it.get("members") or [])
        it.setdefault("count", n)
        if (it.get("done") or it.get("c_sig")) and it.get("members_final"):
            it["members"] = it.pop("members_final")       # 복사는 일부만 끝나도 되돌리기에 필요하다
        else:
            it["members"] = []
            it["rels"] = []
            it.pop("members_final", None)
            it.pop("moved", None)

    def _fail_all(self, job, items, reason):
        for it in items:
            self._result(job, self._item_path(it), False, error=reason)
            it["_finished"] = True
            self._compact(it)
        with self._lock:
            job["status"] = "failed"
            job["message"] = reason

    @staticmethod
    def _item_path(it):
        return it.get("src") if it["kind"] == "path" else it.get("label")

    def _move_path(self, ctx, job, it, drives, verify, reserved, copy=False):
        src = it["src"]
        r = self._recheck_source(src, drives)
        if r:
            return self._item_fail(job, it, r)
        try:
            st = os.lstat(_lp(src))
        except OSError as e:
            return self._item_fail(job, it, _err_ko(e))
        is_dir = stat.S_ISDIR(st.st_mode)
        if not is_dir and not stat.S_ISREG(st.st_mode):
            return self._item_fail(job, it, R_NOTREG)
        mod = safety.mod_for(it["dst_base"])
        try:
            dst = self._free_dest(mod, it["dst_base"], is_dir, reserved)
        except _DstError as e:
            return self._item_fail(job, it, e.reason)
        r = self._recheck_dest(dst, drives)
        if r:
            return self._item_fail(job, it, "옮길 위치를 쓸 수 없어요: " + r)
        if _overlaps(src, dst, mod):
            return self._item_fail(job, it, R_OVERLAP)
        with self._lock:
            it["dst_final"] = dst
        self._save(force=True)                # 중단되면 다음 실행이 '대상에 사본이 남았을 수 있는 곳'을 알려 준다
        smod = safety.mod_for(src)
        if is_dir:
            files, dirs, skipped = _scan_tree(src, smod)
        else:
            files, dirs, skipped = [(src, st.st_size)], [], []
        n = len(src)
        pairs = [(s, dst + s[n:], size) for s, size in files]
        dir_pairs = [(d, dst + d[n:]) for d in dirs]
        res = self._transfer(ctx, job, it, pairs, dir_pairs, skipped, verify, copy=copy)
        if res["fail"]:
            it.pop("dst_final", None)
            return self._item_fail(job, it, res["fail"], stop=res.get("stop"), warn=res["warn"])
        if copy:
            return self._copy_done_path(job, it, src, dst, n, is_dir, res)
        complete = not res.get("stop") and not os.path.lexists(_lp(src))   # 장치가 빠져도 '원본 없음'으로 보인다
        moved_bytes = res["moved_bytes"]
        self._account(job, it, moved_bytes)
        if complete:
            it["done"] = True
            self._result(job, src, True, dest=dst)
            self._hook("on_removed", src)
            self._hook("on_added", dst)
        elif res["moved"]:
            it["partial"] = True
            self._result(job, src, False, error=self._partial_reason(res["kept"]), dest=dst)
            self._changed(src)
            self._hook("on_added", dst)
        else:
            it.pop("dst_final", None)
            self._result(job, src, False, error=self._first_reason(res["kept"]))
        return {"warn": res["warn"], "stop": res.get("stop")}

    def _copy_done_path(self, job, it, src, dst, n, is_dir, res):
        """복사 결과 기록 — 되돌리기용으로 만든 파일(서명 포함)·폴더를 남긴다."""
        made = res["moved"] or (is_dir and not res["kept"] and res["created"])
        if not made:
            it.pop("dst_final", None)
            self._result(job, src, False, error=self._first_reason(res["kept"]))
            return {"warn": res["warn"]}
        with self._lock:
            it["c_files"] = [[s[n:], z, sig[0], sig[1]] for s, _d, z, sig in res["sigs"]]
            it["c_dirs"] = list(res["created"])
        self._account(job, it, res["moved_bytes"], copy=True)
        if not res["kept"]:
            it["done"] = True
            self._result(job, src, True, dest=dst)
        else:
            it["partial"] = True
            self._result(job, src, False, error=self._partial_reason(res["kept"], copy=True), dest=dst)
        self._hook("on_added", dst)
        return {"warn": res["warn"]}

    def _move_group(self, ctx, job, it, drives, verify, reserved, copy=False):
        label = it["label"]
        kept, good = [], []
        for i, (s, d) in enumerate(it["members"]):
            r = self._recheck_source(s, drives)
            size = 0
            if r is None:
                try:
                    st = os.lstat(_lp(s))
                    size = st.st_size
                    if not stat.S_ISREG(st.st_mode):
                        r = R_NOTREG
                except OSError as e:
                    r = _err_ko(e)
            if r:
                kept.append((s, r))
            else:
                good.append((i, s, size))
        if not good:
            return self._item_fail(job, it, self._first_reason(kept))
        coll_dir = it["coll_dir"]
        mod = safety.mod_for(coll_dir)
        rels = it["rels"]
        dests = {i: mod.join(coll_dir, *rels[i]) for i, _s, _z in good}
        final_dir = coll_dir
        if any(os.path.lexists(_lp(d)) or d.casefold() in reserved for d in dests.values()):
            try:
                final_dir = self._free_folder(mod, coll_dir, _group_folder_name(label, mod), reserved)
            except _DstError as e:
                return self._item_fail(job, it, e.reason)
            dests = {i: mod.join(final_dir, *rels[i]) for i, _s, _z in good}
        for d in dests.values():
            reserved.add(d.casefold())
        first = dests[good[0][0]]
        r = self._recheck_dest(first, drives)
        if r is None:
            for d in dests.values():
                ok, rr = self._check(d)
                if not ok:
                    r = rr
                    break
        if r:
            return self._item_fail(job, it, "옮길 위치를 쓸 수 없어요: " + r)
        existed = os.path.isdir(_lp(final_dir))
        with self._lock:
            it["final_dir"] = final_dir
            fin = [list(p) for p in it["members"]]
            for i, d in dests.items():
                fin[i][1] = d
            it["members_final"] = fin
        self._save(force=True)
        pairs = [(s, dests[i], size) for i, s, size in good]
        res = self._transfer(ctx, job, it, pairs, [], kept, verify, group_base=it.get("base_dir"), copy=copy)
        dest_label = mod.join(final_dir, mod.basename(label))
        if res["fail"]:
            it.pop("final_dir", None)
            return self._item_fail(job, it, res["fail"], stop=res.get("stop"), warn=res["warn"])
        moved_set = {s for s, _d, _z in res["moved"]}
        it["moved"] = [i for i, s, _z in good if s in moved_set]
        self._account(job, it, res["moved_bytes"], copy=copy)
        complete = not res["kept"]
        if copy and res["moved"]:
            sig_of = {s: (z, sig) for s, _d, z, sig in res["sigs"]}
            fin = it["members_final"]
            with self._lock:
                it["c_sig"] = [[sig_of[fin[i][0]][0], sig_of[fin[i][0]][1][0], sig_of[fin[i][0]][1][1]]
                               for i in it["moved"]]               # moved 와 같은 순서: [크기, 원본 mtime, 사본 mtime]
                it["c_dirs"] = list(res["created"])
        if res["moved"]:
            if complete:
                it["done"] = True
                self._result(job, label, True, dest=dest_label)
                if not copy:
                    self._hook("on_removed", label)
            else:
                it["partial"] = True
                self._result(job, label, False, error=self._partial_reason(res["kept"], copy=copy), dest=dest_label)
                if not copy:
                    self._changed(it.get("base_dir") or safety.mod_for(label).dirname(label))
            if existed:
                self._changed(final_dir)
            else:
                self._hook("on_added", final_dir)
        else:
            self._result(job, label, False, error=self._first_reason(res["kept"]))
        return {"warn": res["warn"], "stop": res.get("stop")}

    def _item_fail(self, job, it, reason, stop=None, warn=0):
        self._result(job, self._item_path(it), False, error=reason)
        return {"stop": stop, "warn": warn}

    def _account(self, job, it, moved_bytes, copy=False):
        if moved_bytes <= 0:
            return
        S = it.get("src_letter")
        T = it.get("dst_letter") or job.get("_target")
        with self._lock:
            if S and not copy:
                job["freed"][S] = job["freed"].get(S, 0) + moved_bytes
            if T:
                job["added"][T] = job["added"].get(T, 0) + moved_bytes

    @staticmethod
    def _first_reason(kept):
        return kept[0][1] if kept else "옮기지 못했어요."

    @staticmethod
    def _partial_reason(kept, copy=False):
        if copy:
            if not kept:
                return "일부만 복사했어요."
            return "일부만 복사했어요. %d개는 복사하지 못했어요: %s" % (len(kept), kept[0][1])
        if not kept:
            return "원본 폴더에 남은 항목이 있어 일부만 옮겼어요."
        return "일부만 옮겼어요. %d개는 원본에 남겨 뒀어요: %s" % (len(kept), kept[0][1])

    def _make_parents(self, path, created, cache):
        mod = safety.mod_for(path)
        parent = mod.dirname(path)
        if parent in cache:
            return
        missing = []
        p = parent
        while True:
            lp = _lp(p)
            if os.path.isdir(lp) and not _is_reparse_path(p):
                break
            if os.path.lexists(lp):
                raise _DstError("같은 이름의 파일이 있어 폴더를 만들 수 없어요: %s" % p)
            missing.append(p)
            np_ = mod.dirname(p)
            if np_ == p:
                raise _DstError("대상 드라이브에 접근할 수 없어요.")
            p = np_
        for d in reversed(missing):
            try:
                os.mkdir(_lp(d))
            except OSError as e:
                raise _dst_err(e)
            created.append(d)
        cache.add(parent)

    def _target_gone(self, ctx, job, it):
        """대상 장치가 사라졌는가(빠진 USB·끊긴 NAS) — 시간 제한 안에서 확인한다."""
        L = it.get("dst_letter") or job.get("_target")
        root = (getattr(ctx, "troots", None) or {}).get(L)
        if not root:
            return False
        try:
            return not winapi.path_alive(root)
        except Exception:
            return False

    def _transfer(self, ctx, job, it, pairs, dir_pairs, kept, verify, group_base=None, copy=False):
        """pairs [(src,dst,size)] 를 복사·검증한 뒤 원본을 지운다(copy 면 지우지 않는다).

        반환 {fail, stop, moved[(s,d,size)], moved_bytes, kept[(p,reason)], warn, created[폴더], sigs}.
        fail 이면 이 항목에서 우리가 만든 것은 전부 되돌렸고 원본은 그대로다.
        취소면 되돌린 뒤 _Cancelled 를 다시 던진다. 장치가 끊기면 fail=stop=R_DEVLOST.
        """
        item_bytes = sum(z for _s, _d, z in pairs)
        with self._lock:
            job["bytes_total"] += item_bytes - int(it.get("bytes") or 0)
            job["files_total"] += len(pairs) - int(it.get("files") or 0)
            it["bytes"], it["files"] = item_bytes, len(pairs)
            base_done = job["bytes_done"]
        kept = list(kept)
        created, copied, cache = [], [], set()
        dstat = {}
        warn = 0
        out = {"fail": None, "stop": None, "moved": [], "moved_bytes": 0, "kept": kept, "warn": 0,
               "created": [], "sigs": []}
        try:
            if dir_pairs:
                self._make_parents(dir_pairs[0][1], created, cache)
                for _sd, dd in dir_pairs:
                    try:
                        os.mkdir(_lp(dd))
                    except OSError as e:
                        raise _dst_err(e)
                    created.append(dd)
                    cache.add(dd)
            for s, d, size in pairs:
                if ctx.cancelled():
                    raise _Cancelled()
                ctx._current(s)
                self._make_parents(d, created, cache)
                try:
                    sig, w = self._copy_file(ctx, s, d, verify)
                except _SrcError as e:
                    if e.devlost:
                        raise
                    kept.append((s, e.reason))
                    ctx._file_done()
                    continue
                warn += w
                dmt = sig[1]
                if copy:
                    try:
                        dmt = os.stat(_lp(d)).st_mtime_ns     # copystat 실패 시엔 사본 시각이 다르다
                    except OSError:
                        pass
                copied.append((s, d, size, sig, dmt))
                ctx._file_done()
            for sd, dd in dir_pairs:
                try:
                    st = os.stat(_lp(sd))
                    dstat[dd] = (st.st_atime_ns, st.st_mtime_ns)
                except OSError:
                    pass
        except BaseException as e:
            self._rollback(copied, created)
            ctx._set_bytes(base_done + item_bytes)
            if isinstance(e, _Cancelled):
                raise
            if isinstance(e, _SrcError):              # 원본 장치가 끊겼다
                out["fail"] = out["stop"] = R_DEVLOST
                return out
            if isinstance(e, (_DstError, OSError)):
                nospace = e.nospace if isinstance(e, _DstError) else _is_nospace(e)
                lost = (e.devlost if isinstance(e, _DstError) else _is_devlost(e))
                out["fail"] = e.reason if isinstance(e, _DstError) else _err_ko(e)
                out["warn"] = warn
                if nospace:
                    out["stop"] = "대상 드라이브 공간이 부족해 멈췄어요."
                elif lost or self._target_gone(ctx, job, it):
                    out["fail"] = out["stop"] = R_DEVLOST
                return out
            raise
        if copy:
            return self._finish_copy(ctx, out, copied, created, dir_pairs, dstat, kept, warn,
                                     base_done + item_bytes)
        # 여기부터 원본 삭제 — 이 항목의 복사본은 전부 검증됐다. 취소는 받지 않는다(끝까지 정리).
        # 시작 전에 기록을 디스크에 남긴다: 이 뒤에 앱이 죽으면 다음 실행이 '원본 일부가 이미 지워졌을 수
        # 있다'를 알아야 한다(앞서 끝난 항목의 done 도 함께 남는다).
        with self._lock:
            it["deleting"] = True
        self._deleting.set()
        try:
            self._save(force=True)
            moved, src_lost = self._delete_sources(it, copied, kept)
        finally:
            self._deleting.clear()
        if src_lost:
            out["stop"] = R_DEVLOST
        for sd, _dd in reversed(dir_pairs):
            try:
                os.rmdir(_lp(sd))
            except OSError:
                pass
        if group_base and not src_lost:
            gmod = safety.mod_for(group_base)
            gk = group_base.casefold()
            parents = sorted({gmod.dirname(s) for s, _d, _z in moved}, key=len, reverse=True)
            for p in parents:
                while len(p) > len(group_base) and p.casefold().startswith(gk):
                    try:
                        os.rmdir(_lp(p))
                    except OSError:
                        break
                    p = gmod.dirname(p)
        back = {dd: sd for sd, dd in dir_pairs}
        for dd in reversed(created):
            sd = back.get(dd)
            if _is_empty_dir(dd) and (sd is None or os.path.lexists(_lp(sd))):
                try:
                    os.rmdir(_lp(dd))
                except OSError:
                    pass
        warn += self._apply_dir_times(dir_pairs, dstat)
        ctx._set_bytes(base_done + item_bytes)
        out.update(moved=moved, moved_bytes=sum(z for _s, _d, z in moved), kept=kept, warn=warn)
        return out

    def _source_gone(self, path):
        """원본 장치가 사라졌는가(빠진 USB·끊긴 NAS) — 목록에서 없어졌거나 루트에 닿지 않으면 True."""
        hit = safety.drive_of(path, self._drives())
        if hit is None:
            return True
        try:
            return not winapi.path_alive(safety._drive_root(hit[0]))
        except Exception:
            return False

    def _delete_sources(self, it, copied, kept):
        """검증된 사본의 원본을 지운다 → (moved[(s,d,size)], 원본 장치가 끊겼는가).

        원본이 '없다'는 결과가 장치가 빠져서 나온 것일 수 있다(Windows 는 빠진 장치에도 '경로 없음'을 준다).
        그래서 이상이 보이면 원본 장치가 살아 있는지 한 번 본다. 끊겼으면 그 파일과 남은 파일은 모두
        '옮기지 못함(R_DEVLOST)'으로 남기고 사본도 거두지 않는다 — 원본이 장치에 그대로 있을 수 있어서
        양쪽에 둘 다 있는 쪽이 안전하다(지워졌다고 세면 해제 용량·기록이 거짓이 된다)."""
        moved = []
        lost = False

        def gone():                                  # 이상이 보일 때만 묻는다(정상 경로에선 0회)
            nonlocal lost
            if not lost:
                lost = self._source_gone(it.get("src") or it.get("label") or copied[0][0])
            return lost

        for s, d, size, sig, _dmt in copied:
            if lost:
                kept.append((s, R_DEVLOST))
                continue
            why = None
            try:
                st = os.stat(_lp(s))
            except FileNotFoundError:
                if gone():
                    kept.append((s, R_DEVLOST))
                    continue
                moved.append((s, d, size))          # 원본이 이미 없다 → 사본이 유일본, 남긴다
                continue
            except OSError as e:
                if _is_devlost(e) or gone():
                    lost = True
                    kept.append((s, R_DEVLOST))
                    continue
                why = _err_ko(e)
            else:
                if (st.st_size, st.st_mtime_ns) != sig:
                    why = R_CHANGED
                else:
                    try:
                        _remove_file(s)
                        moved.append((s, d, size))
                        continue
                    except FileNotFoundError:
                        if gone():
                            kept.append((s, R_DEVLOST))
                            continue
                        moved.append((s, d, size))
                        continue
                    except OSError as e:
                        if _is_devlost(e) or gone():
                            lost = True
                            kept.append((s, R_DEVLOST))
                            continue
                        why = _err_ko(e)
            kept.append((s, why))
            if os.path.lexists(_lp(s)):
                _discard_file(d)                    # 원본이 남았으니 사본은 거둔다(한 곳에만 있게)
            elif gone():
                kept[-1] = (s, R_DEVLOST)           # 원본이 안 보이는 건 장치가 빠져서다 — 사본은 남긴다
            else:
                moved.append((s, d, size))
        return moved, bool(lost)

    @staticmethod
    def _apply_dir_times(dir_pairs, dstat):
        warn = 0
        for _sd, dd in reversed(dir_pairs):
            ts = dstat.get(dd)
            if ts and os.path.isdir(_lp(dd)):
                try:
                    os.utime(_lp(dd), ns=ts)
                except OSError:
                    warn += 1
        return warn

    def _finish_copy(self, ctx, out, copied, created, dir_pairs, dstat, kept, warn, bytes_end):
        """복사 모드 마무리: 원본은 건드리지 않는다. 아무것도 못 복사했으면 만든 폴더를 되돌린다."""
        if not copied and kept:
            self._rollback([], created)
            ctx._set_bytes(bytes_end)
            out.update(kept=kept, warn=warn)
            return out
        warn += self._apply_dir_times(dir_pairs, dstat)
        ctx._set_bytes(bytes_end)
        out.update(moved=[(s, d, z) for s, d, z, _sig, _dm in copied],
                   moved_bytes=sum(z for _s, _d, z, _sig, _dm in copied), kept=kept, warn=warn,
                   created=list(created), sigs=[(s, d, z, (sig[1], dm)) for s, d, z, sig, dm in copied])
        return out

    def _rollback(self, copied, created):
        for c in reversed(copied):
            _discard_file(c[1])
        for dd in reversed(created):
            try:
                os.rmdir(_lp(dd))
            except OSError:
                pass

    def _copy_file(self, ctx, src, dst, verify):
        """src → dst 복사·검증·커밋. ((size, mtime_ns), copystat 경고 수)."""
        ls = _lp(src)
        try:
            st0 = os.stat(ls)
        except OSError as e:
            raise _src_err(e)
        tmp = _tmp_for(dst)
        h = hashlib.blake2b(digest_size=32) if verify == "full" else None
        created = False
        copied = 0
        try:
            try:
                fs = _open_read(ls)
            except OSError as e:
                raise _src_err(e)
            with fs:
                try:
                    fd = open(_lp(tmp), "xb")
                except OSError as e:
                    raise _dst_err(e)
                created = True
                with fd:
                    mv = memoryview(bytearray(getattr(ctx, "chunk", None) or self.chunk_size))
                    while True:
                        if ctx.cancelled():
                            raise _Cancelled()
                        try:
                            n = fs.readinto(mv)
                        except OSError as e:
                            raise _src_err(e)
                        if not n:
                            break
                        chunk = mv[:n]
                        if h is not None:
                            h.update(chunk)
                        try:
                            _write(fd, chunk)
                        except OSError as e:
                            raise _dst_err(e)
                        copied += n
                        ctx._add_bytes(n)
                    try:
                        fd.flush()
                        os.fsync(fd.fileno())
                    except OSError as e:
                        raise _dst_err(e)
            try:
                st1 = os.stat(ls)
            except OSError as e:
                raise _src_err(e)
            if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns) or copied != st0.st_size:
                raise _SrcError("복사하는 동안 원본이 바뀌었어요.")
            self._verify(src, tmp, verify, st0.st_size, h)
            if ctx.cancelled():
                raise _Cancelled()
            try:
                _commit(tmp, dst)
            except FileExistsError:
                raise _DstError("대상에 같은 이름의 파일이 새로 생겨 멈췄어요.")
            except OSError as e:
                raise _dst_err(e)
        finally:
            if created and os.path.lexists(_lp(tmp)):
                _discard_file(tmp)           # .dbpart 는 반드시 정리(우리가 만든 것만)
        warn = 0
        try:
            shutil.copystat(ls, _lp(dst))
        except OSError:
            warn = 1
        return (st0.st_size, st0.st_mtime_ns), warn

    def _verify(self, src, tmp, level, size, hsrc):
        lt = _lp(tmp)
        try:
            st = os.stat(lt)
        except OSError as e:
            raise _dst_err(e)
        if st.st_size != size:
            raise _DstError("복사본 크기가 원본과 달라 원본을 남겨 뒀어요.")
        if level == "size":
            return
        if level == "full":
            h = hashlib.blake2b(digest_size=32)
            try:
                with open(lt, "rb") as fd:
                    while True:
                        b = fd.read(CHUNK)
                        if not b:
                            break
                        h.update(b)
            except OSError as e:
                raise _dst_err(e)
            if hsrc is None or h.digest() != hsrc.digest():
                raise _DstError("복사본 내용이 원본과 달라 원본을 남겨 뒀어요.")
            return
        if size <= 3 * SPAN:
            regions = [(0, size)]
        else:
            regions = [(0, SPAN), (size // 2 - SPAN // 2, SPAN), (size - SPAN, SPAN)]
        try:
            fs = _open_read(_lp(src))
        except OSError as e:
            raise _src_err(e)
        with fs:
            try:
                fd = open(lt, "rb")
            except OSError as e:
                raise _dst_err(e)
            with fd:
                for off, n in regions:
                    try:
                        fs.seek(off)
                        a = _read_exact(fs, n)
                    except OSError as e:
                        raise _src_err(e)
                    try:
                        fd.seek(off)
                        b = _read_exact(fd, n)
                    except OSError as e:
                        raise _dst_err(e)
                    if a != b or len(a) != n:
                        raise _DstError("복사본 내용이 원본과 달라 원본을 남겨 뒀어요.")

    # ── 휴지통 ──
    def _exec_recycle(self, ctx, job):
        items = job["_items"]
        drives = self._drives()
        warn = self._adobe_warning(items)
        prepared = []
        total = 0
        for it in items:
            paths = self._spec_paths(it)
            bad, good, sizes = {}, [], {}
            for p in paths:
                r = self._recheck_source(p, drives)
                if r is None:
                    hit = safety.drive_of(p, drives)
                    if hit and _no_bin(hit[0]):
                        r = R_NO_BIN                   # 실행 직전 재검사(영구 삭제가 되지 않게)
                if r:
                    bad[p] = r
                else:
                    good.append(p)
            if it["kind"] == "path":
                for p in good:
                    sizes[p] = self._estimate(p)[0]
            else:
                for p in good:
                    try:
                        sizes[p] = os.lstat(_lp(p)).st_size
                    except OSError:
                        sizes[p] = 0
            total += sum(sizes.values())
            prepared.append((it, good, bad, sizes))
        ctx.progress(bytes_total=total, files_total=len(items), bytes_done=0, files_done=0)
        flat = [(p, sizes[p]) for _it, good, _bad, sizes in prepared for p in good]
        status = {}
        cancelled = False
        done_bytes = 0
        for i in range(0, len(flat), RECYCLE_CHUNK):
            if ctx.cancelled():
                cancelled = True
                break
            chunk = flat[i:i + RECYCLE_CHUNK]
            ctx._current(chunk[0][0])
            try:
                res = winapi.recycle_many([p for p, _z in chunk])
            except Exception as e:
                res = [(p, False, "휴지통으로 보내지 못했어요: %s" % e) for p, _z in chunk]
            for (p, z), (_p, ok, err) in zip(chunk, res):
                status[p] = (bool(ok), err)
                if ok:
                    done_bytes += z
            ctx._set_bytes(done_bytes)
        letters = []
        n_ok = 0
        for it, good, bad, sizes in prepared:
            label = self._item_path(it)
            gone = [p for p in good if status.get(p, (False,))[0]]
            errs = [bad[p] for p in self._spec_paths(it) if p in bad]
            errs += [status[p][1] or "휴지통으로 보내지 못했어요." for p in good if p in status and not status[p][0]]
            if any(p not in status for p in good):
                errs.append(R_CANCELED)
            b = sum(sizes[p] for p in gone)
            L = it.get("src_letter")
            if b and L:
                with self._lock:
                    job["freed"][L] = job["freed"].get(L, 0) + b
                if L not in letters:
                    letters.append(L)
            if gone and len(gone) == len(self._spec_paths(it)):
                it["done"] = True
                n_ok += 1
                self._result(job, label, True)
                self._hook("on_removed", label)
            elif gone:
                it["partial"] = True
                self._result(job, label, False, error="일부만 보냈어요. " + (errs[0] if errs else ""))
                self._changed(it.get("base_dir") or safety.mod_for(label).dirname(label))
            else:
                self._result(job, label, False, error=errs[0] if errs else "휴지통으로 보내지 못했어요.")
            if it["kind"] == "group":
                it["count"] = len(it.get("members") or [])
                it["members"] = []
            ctx._file_done()
        status_s = self._final_status(items, cancelled)
        if n_ok or any(it.get("partial") for it in items):
            msg = ("휴지통으로 보냈어요. 휴지통으로 보낸 용량은 그 드라이브의 휴지통을 비워야 확보돼요 (%s). "
                   "되돌리려면 휴지통에서 복원하세요." % ", ".join(L + ":" for L in letters))
            if status_s == "partial":
                msg = "%d개는 휴지통으로 보냈고 %d개는 보내지 못했어요. " % (n_ok, len(items) - n_ok) + msg
            if status_s == "canceled":
                msg = "취소했어요. 이미 보낸 %d개는 휴지통에 있어요. " % n_ok + msg
        elif status_s == "canceled":
            msg = "취소했어요."
        else:
            errs = [r.get("error") for r in job["results"] if r.get("error")]
            msg = "휴지통으로 보내지 못했어요." + (" " + errs[0] if errs else "")
        with self._lock:
            job["status"] = status_s
            job["message"] = (warn + " " + msg).strip() if warn else msg
            job["undoable"] = False

    # ── 영구 삭제 ──
    def _delete_files(self, ctx, files, on_bytes):
        """파일을 하나씩 영구 삭제 → (지운 바이트, kept[(p, reason)]). 취소는 파일 사이에서."""
        done, kept = 0, []
        for p, size in files:
            if ctx.cancelled():
                raise _Cancelled()
            ctx._current(p)
            try:
                _remove_file(p)
            except FileNotFoundError:
                pass                                   # 이미 없다
            except OSError as e:
                kept.append((p, _err_ko(e)))
                continue
            done += size
            on_bytes(size)
            ctx._file_done()
        return done, kept

    def _exec_delete(self, ctx, job):
        """영구 삭제(복구 불가). shutil.rmtree 대신 직접 걷는다 — 링크·정션·클라우드 자리표시자는
        따라가지도 지우지도 않고(그 폴더는 남는다), 파일마다 취소·진행률을 받는다."""
        items = job["_items"]
        drives = self._drives()
        warn = self._adobe_warning(items)
        cancelled = False
        n_ok = 0
        prog = {"b": 0}

        def on_bytes(n):
            prog["b"] += n
            ctx._set_bytes(prog["b"])
        for it in items:
            label = self._item_path(it)
            if cancelled or ctx.cancelled():
                cancelled = True
                self._result(job, label, False, error=R_CANCELED)
                continue
            kept, files, dirs = [], [], []
            for p in self._spec_paths(it):
                r = self._recheck_source(p, drives)
                if r:
                    kept.append((p, r))
                    continue
                try:
                    st = os.lstat(_lp(p))
                except OSError as e:
                    kept.append((p, _err_ko(e)))
                    continue
                if stat.S_ISDIR(st.st_mode):
                    fs, ds, skipped = _scan_tree(p, safety.mod_for(p))
                    files += fs
                    dirs += ds
                    kept += skipped
                elif stat.S_ISREG(st.st_mode):
                    files.append((p, st.st_size))
                else:
                    kept.append((p, R_NOTREG))
            if "limit_b" in it and (sum(z for _p, z in files) > it["limit_b"] or len(files) > it["limit_f"]):
                self._result(job, label, False, error=R_GREW)      # 확인한 뒤 늘었다 — 한 파일도 지우지 않는다
                continue
            with self._lock:
                job["files_total"] += len(files) - 1
                job["bytes_total"] += sum(z for _p, z in files) - int(it.get("bytes") or 0)
                it["bytes"] = sum(z for _p, z in files)
            try:
                gone, k2 = self._delete_files(ctx, files, on_bytes)
            except _Cancelled:
                cancelled = True
                gone, k2 = None, []
            kept += k2
            for d in reversed(dirs):                   # 자식 폴더부터(_scan_tree 는 부모 먼저)
                try:
                    os.rmdir(_lp(d))
                except FileNotFoundError:
                    pass
                except OSError:
                    pass                               # 남은 것이 있다(링크·실패한 파일) — 남긴다
            if gone is None:
                gone = sum(z for p, z in files if not os.path.lexists(_lp(p)))
            L = it.get("src_letter")
            if gone and L:
                with self._lock:
                    job["freed"][L] = job["freed"].get(L, 0) + gone
            remains = any(os.path.lexists(_lp(p)) for p in self._spec_paths(it))
            if not remains and not kept:
                it["done"] = True
                n_ok += 1
                self._result(job, label, True)
                self._hook("on_removed", label)
            elif any(not os.path.lexists(_lp(p)) for p, _z in files):
                it["partial"] = True
                why = R_CANCELED if cancelled else (kept[0][1] if kept else "일부가 남았어요.")
                self._result(job, label, False, error="일부만 지웠어요. " + why)
                self._changed(label if os.path.lexists(_lp(label)) else
                              (it.get("base_dir") or safety.mod_for(label).dirname(label)))
            else:
                self._result(job, label, False,
                             error=R_CANCELED if cancelled else (kept[0][1] if kept else "지우지 못했어요."))
            if it["kind"] == "group":
                it["count"] = len(it.get("members") or [])
                it["members"] = []
            try:
                winapi.invalidate_usage(label)
            except Exception:
                pass
        status = self._final_status(items, cancelled)
        freed = sum(job["freed"].values())
        if status == "done":
            msg = "%d개 항목(%s)을 영구 삭제했어요. 복구할 수 없어요." % (n_ok, fmt_size(freed))
        elif status == "partial":
            msg = "%d개는 영구 삭제했고 %d개는 다 지우지 못했어요." % (n_ok, len(items) - n_ok)
        elif status == "canceled":
            msg = "취소했어요. 이미 지운 것(%s)은 되돌릴 수 없어요." % fmt_size(freed)
        else:
            errs = [r.get("error") for r in job["results"] if r.get("error")]
            msg = "지우지 못했어요." + (" " + errs[0] if errs else "")
        with self._lock:
            job["status"] = status
            job["message"] = (warn + " " + msg).strip() if warn else msg
            job["undoable"] = False

    # ── 복사 되돌리기(복사본 지우기) ──
    def _uncopy_targets(self, oit):
        """원래 복사 항목 → [(원본, 복사본, 크기, 원본 mtime, 사본 mtime)], 만든 폴더, 맨 위 경로."""
        out = []
        if oit["kind"] == "path":
            src, dst = oit["src"], oit.get("dst_final")
            if dst:
                for rel, z, smt, dmt in oit.get("c_files") or []:
                    out.append((src + rel, dst + rel, int(z), int(smt), int(dmt)))
            return out, list(oit.get("c_dirs") or []), dst
        fin = oit.get("members") or []
        for i, sig in zip(oit.get("moved") or [], oit.get("c_sig") or []):
            if 0 <= i < len(fin):
                out.append((fin[i][0], fin[i][1], int(sig[0]), int(sig[1]), int(sig[2])))
        return out, list(oit.get("c_dirs") or []), oit.get("final_dir")

    def _exec_uncopy(self, ctx, job):
        drives = self._drives()
        T = job.get("_target")
        tdrive = _find_drive(drives, T)
        with self._lock:
            orig = self._jobs.get(job.get("undo_of"))
        items = job["_items"]
        if orig is None:
            return self._fail_all(job, items, "원래 복사 작업의 기록을 찾을 수 없어요.")
        if tdrive is None or tdrive.get("connected") is False:
            return self._fail_all(job, items, "복사한 위치를 찾을 수 없거나 연결되어 있지 않아요.")
        if _serial_mismatch(drives, job.get("_expect")):
            return self._fail_all(job, items, R_DRIVE_CHANGED % _disp(tdrive))
        use_bin = not _no_bin(tdrive)
        oitems = orig.get("_items") or []
        prepared, total = [], 0
        for it in items:
            oi = it.get("orig")
            oit = oitems[oi] if isinstance(oi, int) and 0 <= oi < len(oitems) else None
            targets, cdirs, top = self._uncopy_targets(oit) if oit else ([], [], None)
            prepared.append((it, targets, cdirs, top))
            total += sum(t[2] for t in targets)
        ctx.progress(bytes_total=total, files_total=sum(len(p[1]) for p in prepared), bytes_done=0, files_done=0)
        prog = {"b": 0}

        def on_bytes(n):
            prog["b"] += n
            ctx._set_bytes(prog["b"])
        cancelled = False
        n_ok = 0
        for it, targets, cdirs, top in prepared:
            label = it.get("label") or top or ""
            if cancelled or ctx.cancelled():
                cancelled = True
                self._result(job, label, False, error=R_CANCELED)
                continue
            dele, kept, gone_b = [], [], 0
            for s, d, z, smt, dmt in targets:
                r = self._recheck_source(d, drives)
                if r == R_MISSING:
                    continue                           # 이미 지워졌다(다시 시도한 경우 등)
                if r:
                    kept.append((d, r))
                    continue
                try:
                    st = os.lstat(_lp(d))
                except OSError as e:
                    kept.append((d, _err_ko(e)))
                    continue
                if not stat.S_ISREG(st.st_mode) or (st.st_size, st.st_mtime_ns) != (z, dmt):
                    kept.append((d, R_COPY_CHANGED))
                    continue
                try:
                    sst = os.stat(_lp(s))
                except OSError:
                    kept.append((d, R_ORIG_GONE))      # 원본이 없으면 이 복사본이 유일본이다
                    continue
                if (sst.st_size, sst.st_mtime_ns) != (z, smt):
                    kept.append((d, R_ORIG_CHANGED))
                    continue
                dele.append((d, z))
            try:
                if use_bin:
                    gone_b, k2 = self._recycle_files(ctx, dele, on_bytes)
                else:
                    gone_b, k2 = self._delete_files(ctx, dele, on_bytes)
            except _Cancelled:
                cancelled = True
                k2 = []
                gone_b = sum(z for d, z in dele if not os.path.lexists(_lp(d)))
            kept += k2
            for dd in sorted(set(cdirs), key=len, reverse=True):   # 우리가 만든 폴더만, 비었을 때만
                if _is_empty_dir(dd) and not _is_reparse_path(dd):
                    try:
                        os.rmdir(_lp(dd))
                    except OSError:
                        pass
            if gone_b:
                with self._lock:
                    job["freed"][T] = job["freed"].get(T, 0) + gone_b
            if not kept and not cancelled:
                it["done"] = True
                n_ok += 1
                self._result(job, label, True)
            elif gone_b or any(not os.path.lexists(_lp(d)) for d, _z in dele):
                it["partial"] = True
                why = R_CANCELED if cancelled else kept[0][1]
                self._result(job, label, False, error="복사본 일부만 지웠어요. %d개는 남겨 뒀어요: %s" % (len(kept), why))
            else:
                self._result(job, label, False, error=R_CANCELED if cancelled else kept[0][1])
            if top:
                if os.path.lexists(_lp(top)):
                    self._changed(top)
                else:
                    self._hook("on_removed", top)
                try:
                    winapi.invalidate_usage(top)
                except Exception:
                    pass
        status = self._final_status(items, cancelled)
        freed = sum(job["freed"].values())
        where = "휴지통으로 보냈어요" if use_bin else "영구 삭제했어요"
        if status == "done":
            msg = "복사본 %d개 항목(%s)을 %s. 원본은 그대로예요." % (n_ok, fmt_size(freed), where)
        elif status == "partial":
            msg = "복사본 일부를 %s. 바뀌었거나 원본이 없는 복사본은 남겨 뒀어요." % where
        elif status == "canceled":
            msg = "취소했어요. 이미 지운 복사본(%s)은 %s." % (fmt_size(freed), "휴지통에 있어요" if use_bin else "되돌릴 수 없어요")
        else:
            errs = [r.get("error") for r in job["results"] if r.get("error")]
            msg = "복사본을 지우지 못했어요." + (" " + errs[0] if errs else "")
        with self._lock:
            job["status"] = status
            job["message"] = msg
            job["undoable"] = False

    def _recycle_files(self, ctx, files, on_bytes):
        """휴지통으로(RECYCLE_CHUNK 씩, 사이사이 취소) → (보낸 바이트, kept)."""
        done, kept = 0, []
        for i in range(0, len(files), RECYCLE_CHUNK):
            if ctx.cancelled():
                raise _Cancelled()
            chunk = files[i:i + RECYCLE_CHUNK]
            ctx._current(chunk[0][0])
            try:
                res = winapi.recycle_many([p for p, _z in chunk])
            except Exception as e:
                res = [(p, False, "휴지통으로 보내지 못했어요: %s" % e) for p, _z in chunk]
            for (p, z), (_p, ok, err) in zip(chunk, res):
                if ok:
                    done += z
                    on_bytes(z)
                    ctx._file_done()
                else:
                    kept.append((p, err or "휴지통으로 보내지 못했어요."))
        return done, kept

    # ── 휴지통 비우기 ──
    def _exec_empty(self, ctx, job):
        drives = self._drives()
        letters = job.get("_letters") or []
        n_ok = 0
        cancelled = False
        freed_total = 0
        for L in letters:
            if ctx.cancelled():
                cancelled = True
                self._result(job, L + ":", False, error=R_CANCELED)
                continue
            d = _find_drive(drives, L)
            if d is None:
                self._result(job, L + ":", False, error="드라이브를 찾을 수 없어요.")
                continue
            root = safety._drive_root(d)
            ctx._current(root)
            try:
                before, _cnt = winapi.recycle_bin_info(root)
            except Exception:
                before = 0
            try:
                ok, err = winapi.empty_recycle_bin(root)
            except Exception as e:
                ok, err = False, "휴지통을 비우지 못했어요: %s" % e
            if ok:
                n_ok += 1
                with self._lock:
                    job["freed"][L] = job["freed"].get(L, 0) + int(before)
                freed_total += int(before)
                self._result(job, root, True)
            else:
                self._result(job, root, False, error=err or "휴지통을 비우지 못했어요.")
            try:
                winapi.invalidate_usage(L)
            except Exception:
                pass
            ctx._file_done()
        if cancelled:
            st = "canceled"
        elif n_ok == len(letters):
            st = "done"
        elif n_ok:
            st = "partial"
        else:
            st = "failed"
        if n_ok:
            msg = "휴지통을 비워 %s를 확보했어요." % fmt_size(freed_total)
        else:
            errs = [r.get("error") for r in job["results"] if r.get("error")]
            msg = "휴지통을 비우지 못했어요." + (" " + errs[0] if errs else "")
        with self._lock:
            job["status"] = st
            job["message"] = msg
            job["undoable"] = False

    # ── 일반 작업 ──
    def _exec_task(self, ctx, job):
        fn = self._fns.pop(job["id"], None)
        if fn is None:
            with self._lock:
                job["status"] = "failed"
                job["message"] = "작업 함수를 찾을 수 없어요(앱을 다시 켠 경우)."
            return
        try:
            ret = fn(ctx)
        except _Cancelled:
            ret = None
        except Exception as e:
            with self._lock:
                job["status"] = "failed"
                if not job.get("message"):
                    job["message"] = "작업 중 오류가 났어요: %s" % e
            return
        self._returns[job["id"]] = ret
        with self._lock:
            res = job["results"]
            n_ok = sum(1 for r in res if r.get("ok"))
            if ctx.cancelled():
                job["status"] = "canceled"
            elif not res or n_ok == len(res):
                job["status"] = "done"
            elif n_ok:
                job["status"] = "partial"
            else:
                job["status"] = "failed"
            if not job.get("message"):
                job["message"] = {"done": "끝났어요.", "partial": "일부만 끝났어요.",
                                  "failed": "실패했어요.", "canceled": "취소했어요."}[job["status"]]

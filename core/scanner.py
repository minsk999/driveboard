"""Drive scanning (SPEC §5): one thread per drive, iterative DFS with os.scandir.

start_scan(drive, settings, progress_cb) -> ScanHandle   (asynchronous, one per letter)
scan_subtree(path, drive, settings)       -> DriveIndex  (synchronous, for after a move)

drive = {letter, root, label, serial, fs, kind}
"""

import os
import stat as _stat
import threading
import time
from array import array

from . import settings as settings_mod
from .categories import (FRAME_RE, NCAT, SEQ_DISPLAY_RE, SEQ_EXCLUDE_EXT, SEQ_MAX_DIGITS,
                         SEQ_MIN_DIGITS, SEQ_MIN_FRAMES, VIDEO_EXT, Classifier, classify,
                         rel_parts, strip_long_prefix)
from .model import (EDIT_PROJECT_CAT, EDIT_PROJECT_EXT, VID_PRIO, DriveIndex, _scan_path,
                    match_frames, norm_drive_key, norm_root, seq_display_name, vid_better)

IS_NT = os.name == "nt"
ROOT_SKIP = frozenset({"$recycle.bin", "system volume information", ".fakebin", "_drive.json"})
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
PROGRESS_INTERVAL = 0.25
MAX_ERROR_SAMPLES = 20
_TICK_EVERY = 4096


class ScanBusyError(RuntimeError):
    """A scan of the same drive letter is already running."""


class _Canceled(Exception):
    pass


def is_internal(drive):
    """내장 드라이브인가 — 외장·USB·네트워크는 시스템 문맥 규칙을 쓰지 않는다(safety.is_external 과 같은 기준)."""
    d = drive or {}
    return not (bool(d.get("external")) or d.get("kind") in ("usb", "network") or d.get("network") is True)


def _normkey(p):
    return os.path.normcase(os.path.normpath(p))


def _excluded_set(settings):
    out = set()
    for p in (settings or {}).get("excluded") or ():
        if isinstance(p, str) and p.strip():
            out.add(_normkey(strip_long_prefix(p.strip())))
    return out


def _is_reparse(st):
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


class _Walker:
    """Fills a DriveIndex from disk. Counters double as live progress."""

    def __init__(self, idx, classifier, big, excluded, cancel_ev=None, emit=None):
        self.idx = idx
        self.cls = classifier
        self.big = big
        self.excluded = excluded
        self.cancel_ev = cancel_ev
        self.emit = emit
        self.files = 0
        self.dirs = 0
        self.bytes = 0
        self.cats = [0] * NCAT
        self.current = ""
        self._last = time.monotonic()

    # -- bookkeeping
    def _err(self, path):
        idx = self.idx
        idx.errors += 1
        if len(idx.error_samples) < MAX_ERROR_SAMPLES:
            idx.error_samples.append(strip_long_prefix(path))

    def _tick(self, force=False, extra=None):
        if self.emit is None:
            return
        now = time.monotonic()
        if not force and now - self._last < PROGRESS_INTERVAL:
            return
        self._last = now
        cats = list(self.cats)
        files, nbytes = self.files, self.bytes
        if extra is not None:
            tot, nf = extra
            for k in range(NCAT):
                cats[k] += tot[k]
            files += nf
            nbytes += sum(tot)
        self.emit({"phase": "scan", "files": files, "dirs": max(0, self.dirs - 1), "bytes": nbytes,
                   "current": self.current, "cats": cats})

    # -- one directory's files (shared by walk and single-file scans)
    def _flush_dir(self, did, tot, totn, own_b, own_n, onew, fnew, vb=None):
        idx = self.idx
        if vb is not None:
            idx.d_vid[did] = vb
        b = did * NCAT
        idx.d_cat[b:b + NCAT] = array("q", tot)
        idx.d_catn[b:b + NCAT] = array("q", totn)
        idx.d_own[did] = own_b
        idx.d_ofiles[did] = own_n
        idx.d_onew[did] = onew
        idx.d_size[did] = sum(tot)
        nf = sum(totn)
        idx.d_files[did] = nf
        idx.d_newest[did] = fnew if fnew > onew else onew
        self.files += nf
        s = 0
        cats = self.cats
        for k in range(NCAT):
            v = tot[k]
            if v:
                cats[k] += v
                s += v
        self.bytes += s

    def _add_single(self, did, nm, sz, mt, ctx, depth, tot, totn, state):
        c = classify(nm, ctx, depth)
        if c == EDIT_PROJECT_CAT:
            dot = nm.rfind(".")
            if dot > 0 and nm[dot + 1:].lower() in EDIT_PROJECT_EXT:
                self.idx.d_aep.add(did)
        elif c in VID_PRIO:
            dot = nm.rfind(".")
            if dot > 0 and nm[dot + 1:].lower() in VIDEO_EXT:
                v = (nm, sz, mt, c)
                if len(state) < 5:
                    state.append(None)
                if vid_better(v, state[4]):
                    state[4] = v
        tot[c] += sz
        totn[c] += 1
        if sz >= self.big:
            self.idx._new_item(did, nm, sz, mt, c)
            if mt > state[3]:
                state[3] = mt
        else:
            state[0] += sz
            state[1] += 1
            if mt > state[2]:
                state[2] = mt

    def _group(self, did, ctx, depth, cands, tot, totn, state):
        """Collapse frame candidates; state = [own_b, own_n, onew, fnew]."""
        idx = self.idx
        for (prefix, nd, ext), lst in cands.items():
            if len(lst) < SEQ_MIN_FRAMES:
                for _n, nm, sz, mt in lst:
                    self._add_single(did, nm, sz, mt, ctx, depth, tot, totn, state)
                continue
            lst.sort()
            c = classify(lst[0][1], ctx, depth)
            if c == EDIT_PROJECT_CAT and ext.lower() in EDIT_PROJECT_EXT:
                idx.d_aep.add(did)              # 번호가 붙은 aep 여러 벌도 프로젝트 표시
            total = 0
            mx = 0.0
            for _n, _nm, sz, mt in lst:
                total += sz
                if mt > mx:
                    mx = mt
            cnt = len(lst)
            tot[c] += total
            totn[c] += cnt
            if total >= self.big:
                idx._new_item(did, seq_display_name(prefix, nd, ext), total, mx, c, cnt,
                              (lst[0][0], lst[-1][0]))
                if mx > state[3]:
                    state[3] = mx
            else:
                state[0] += total
                state[1] += cnt
                if mx > state[2]:
                    state[2] = mx

    # -- the DFS
    def walk(self, top_disp, top_ctx, top_depth, top_mtime):
        idx = self.idx
        cls = self.cls
        dir_ctx = cls.dir_ctx
        big = self.big
        excluded = self.excluded
        cancel_ev = self.cancel_ev
        join = os.path.join
        new_dir = idx._new_dir
        new_item = idx._new_item
        frame_match = FRAME_RE.match
        seq_excl = SEQ_EXCLUDE_EXT
        root_skip = ROOT_SKIP
        is_nt = IS_NT
        vprio = VID_PRIO
        vid_ext = VIDEO_EXT
        pcat = EDIT_PROJECT_CAT
        pext = EDIT_PROJECT_EXT
        d_aep = idx.d_aep
        top_os = _scan_path(top_disp) if is_nt else top_disp
        stack = [(top_os, top_disp, -1, top_ctx, top_depth, "", top_mtime)]
        while stack:
            os_path, disp, parent, ctx, depth, name, dmt = stack.pop()
            if cancel_ev is not None and cancel_ev.is_set():
                raise _Canceled()
            did = new_dir(name, parent, dmt)
            self.dirs += 1
            self.current = disp
            tot = [0] * NCAT
            totn = [0] * NCAT
            own_b = 0
            own_n = 0
            onew = 0.0
            fnew = 0.0
            cands = None
            vb = None
            vbp = 9
            nseen = 0
            try:
                it = os.scandir(os_path)
            except OSError:
                self._err(disp)
                self._flush_dir(did, tot, totn, 0, 0, 0.0, 0.0)
                continue
            try:
                with it:
                    for e in it:
                        nm = e.name
                        nseen += 1
                        if nseen % _TICK_EVERY == 0:
                            if cancel_ev is not None and cancel_ev.is_set():
                                raise _Canceled()
                            self._tick(extra=(tot, sum(totn)))
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if depth == 0 and nm.lower() in root_skip:
                                    continue
                                st = e.stat(follow_symlinks=False)
                                if is_nt and _is_reparse(st):
                                    continue
                                cdisp = join(disp, nm)
                                if excluded and _normkey(cdisp) in excluded:
                                    continue
                                stack.append((join(os_path, nm) if is_nt else cdisp, cdisp, did,
                                              dir_ctx(ctx, nm, cdisp, depth + 1), depth + 1, nm,
                                              st.st_mtime))
                                continue
                            if e.is_symlink():
                                continue
                            if depth == 0 and nm.lower() in root_skip:
                                continue
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            self._err(join(disp, nm))
                            continue
                        if is_nt and _is_reparse(st):
                            continue
                        if excluded and _normkey(join(disp, nm)) in excluded:
                            continue
                        size = st.st_size
                        mt = st.st_mtime
                        dot = nm.rfind(".")
                        if dot > 0 and "0" <= nm[dot - 1] <= "9":
                            m = frame_match(nm)
                            if m is not None:
                                digits = m.group(2)
                                nd = len(digits)
                                ext = m.group(3)
                                if (SEQ_MIN_DIGITS <= nd <= SEQ_MAX_DIGITS
                                        and ext.lower() not in seq_excl):
                                    if cands is None:
                                        cands = {}
                                    key = (m.group(1), nd, ext)
                                    rec = (int(digits), nm, size, mt)
                                    lst = cands.get(key)
                                    if lst is None:
                                        cands[key] = [rec]
                                    else:
                                        lst.append(rec)
                                    continue
                        c = classify(nm, ctx, depth)
                        tot[c] += size
                        totn[c] += 1
                        # 대표 영상 후보 (SPEC-2 B.3): 크기 무관, 우선순위 → mtime 최신 → 이름
                        if c == pcat:
                            if dot > 0 and nm[dot + 1:].lower() in pext:
                                d_aep.add(did)
                        elif c in vprio and dot > 0 and nm[dot + 1:].lower() in vid_ext:
                            pr = vprio[c]
                            if (vb is None or pr < vbp
                                    or (pr == vbp and (mt > vb[2]
                                                       or (mt == vb[2] and nm < vb[0])))):
                                vb = (nm, size, mt, c)
                                vbp = pr
                        if size >= big:
                            new_item(did, nm, size, mt, c)
                            if mt > fnew:
                                fnew = mt
                        else:
                            own_b += size
                            own_n += 1
                            if mt > onew:
                                onew = mt
            except _Canceled:
                raise
            except OSError:
                self._err(disp)
            if cands:
                state = [own_b, own_n, onew, fnew, vb]
                self._group(did, ctx, depth, cands, tot, totn, state)
                own_b, own_n, onew, fnew, vb = state
            self._flush_dir(did, tot, totn, own_b, own_n, onew, fnew, vb)
            self._tick()

    def scan_files(self, did, ctx, depth, entries):
        """Add explicit files [(name, size, mtime)] to directory `did` (sequence-aware)."""
        tot = [0] * NCAT
        totn = [0] * NCAT
        state = [0, 0, 0.0, 0.0]
        cands = {}
        for nm, size, mt in entries:
            m = FRAME_RE.match(nm)
            if m is not None:
                digits = m.group(2)
                nd = len(digits)
                ext = m.group(3)
                if SEQ_MIN_DIGITS <= nd <= SEQ_MAX_DIGITS and ext.lower() not in SEQ_EXCLUDE_EXT:
                    cands.setdefault((m.group(1), nd, ext), []).append((int(digits), nm, size, mt))
                    continue
            self._add_single(did, nm, size, mt, ctx, depth, tot, totn, state)
        if cands:
            self._group(did, ctx, depth, cands, tot, totn, state)
        self._flush_dir(did, tot, totn, state[0], state[1], state[2], state[3],
                        state[4] if len(state) > 4 else None)


def _finalize(idx):
    """Children -> parent recursive totals (ids are DFS preorder: child id > parent id)."""
    n = len(idx.d_name)
    par = idx.d_parent
    size = idx.d_size
    files = idx.d_files
    dc = idx.d_cat
    dn = idx.d_catn
    fnew = idx.d_newest            # holds "own files newest" until finalized
    enew = array("d", idx.d_mtime)  # newest for file-less subtrees
    rng = range(NCAT)
    for i in range(n - 1, 0, -1):
        p = par[i]
        s = size[i]
        nf = files[i]
        if nf > 0:
            v = fnew[i]
            if v > fnew[p]:
                fnew[p] = v
            size[p] += s
            files[p] += nf
            bi = i * NCAT
            bp = p * NCAT
            for k in rng:
                v = dn[bi + k]
                if v:
                    dn[bp + k] += v
                    dc[bp + k] += dc[bi + k]
        else:
            v = enew[i]
            fnew[i] = v
            if v > enew[p]:
                enew[p] = v
    if n and files[0] == 0:
        fnew[0] = enew[0]


def _new_root_index(drive, root_path, mtime):
    idx = DriveIndex(str(drive.get("letter") or ""), norm_root(root_path),
                     str(drive.get("label") or ""),
                     str(drive.get("serial") or ""), str(drive.get("fs") or ""))
    return idx


def _lstat(p):
    return os.lstat(_scan_path(p) if IS_NT else p)


def _scan_dir_into(idx, path, drive, settings, cancel_ev=None, emit=None):
    cls = Classifier(settings, internal=is_internal(drive))
    root = norm_root(drive.get("root") or path)
    ctx, depth = cls.ctx_for(path, root)
    st = _lstat(path)
    w = _Walker(idx, cls, settings_mod.big_file_bytes(settings), _excluded_set(settings),
                cancel_ev, emit)
    w.walk(path, ctx, depth, st.st_mtime)
    if emit is not None:
        emit({"phase": "finalize", "files": w.files, "dirs": max(0, w.dirs - 1), "bytes": w.bytes,
              "current": "", "cats": list(w.cats)})
    _finalize(idx)
    return w


def scan_subtree(path, drive, settings, seq_range=None):
    """Synchronously index `path` (a directory, a file, or a sequence display path).

    Directory -> DriveIndex rooted at `path` (partial=False; graft replaces).
    File / sequence -> DriveIndex rooted at the parent directory holding only that item
    (partial=True; graft merges). seq_range=(first,last) limits sequence rediscovery.
    """
    settings = settings_mod.validate(settings or {})
    root = norm_root(drive.get("root") or "")
    p = norm_root(os.path.normpath(strip_long_prefix(path)))
    parts = rel_parts(root, p) if root else []
    if parts is None:
        raise ValueError("path is outside the drive root")
    t0 = time.time()
    excluded = _excluded_set(settings)
    try:
        st = _lstat(p)
    except FileNotFoundError:
        st = None
    if st is not None and _stat.S_ISDIR(st.st_mode) and not (IS_NT and _is_reparse(st)):
        idx = _new_root_index(drive, p, st.st_mtime)
        if _normkey(p) in excluded:
            idx._new_dir("", -1, st.st_mtime)
        else:
            _scan_dir_into(idx, p, drive, settings)
    else:
        if not parts:
            raise ValueError("not a directory: drive root")
        parent, name = os.path.split(p)
        cls = Classifier(settings, internal=is_internal(drive))
        pctx, pdepth = cls.ctx_for(parent, root)
        try:
            pmt = _lstat(parent).st_mtime
        except OSError:
            pmt = 0.0
        idx = _new_root_index(drive, parent, pmt)
        idx.partial = True
        did = idx._new_dir("", -1, pmt)
        w = _Walker(idx, cls, settings_mod.big_file_bytes(settings), excluded)
        entries = []
        if st is not None:
            if _stat.S_ISLNK(st.st_mode) or (IS_NT and _is_reparse(st)):
                raise ValueError("reparse point / symlink is not indexed")
            if not _stat.S_ISREG(st.st_mode):
                raise ValueError("not a regular file")
            skip = (pdepth == 0 and name.lower() in ROOT_SKIP) or _normkey(p) in excluded
            if not skip:
                entries.append((name, st.st_size, st.st_mtime))
        else:
            m = SEQ_DISPLAY_RE.match(name)
            if not m:
                raise FileNotFoundError(p)
            prefix, nd, ext = m.group(1), len(m.group(2)), m.group(3)
            first, last = seq_range if seq_range else (None, None)
            for _num, nm in match_frames(parent, prefix, nd, ext, first, last):
                fp = os.path.join(parent, nm)
                if _normkey(fp) in excluded:
                    continue
                try:
                    fst = _lstat(fp)
                except OSError:
                    w._err(fp)
                    continue
                entries.append((nm, fst.st_size, fst.st_mtime))
            if not entries:
                raise FileNotFoundError(p)
        w.scan_files(did, pctx, pdepth, entries)
    idx.big_bytes = settings_mod.big_file_bytes(settings)
    idx.scanned_at = time.time()
    idx.scan_seconds = idx.scanned_at - t0
    return idx


def _direct_files(dir_path, depth, excluded):
    """[(name, size, mtime)] of the regular files directly in dir_path (no links)."""
    out = []
    root_skip = ROOT_SKIP if depth == 0 else ()
    with os.scandir(_scan_path(dir_path) if IS_NT else dir_path) as it:
        for e in it:
            nm = e.name
            try:
                if e.is_dir(follow_symlinks=False) or e.is_symlink():
                    continue
                if nm.lower() in root_skip:
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if IS_NT and _is_reparse(st):
                continue
            if excluded and _normkey(os.path.join(dir_path, nm)) in excluded:
                continue
            out.append((nm, st.st_size, st.st_mtime))
    return out


def refresh_files(idx, dir_path, drive, settings):
    """Re-read only the direct files of an indexed directory (its sub-folders are untouched).

    Exact for small files, which have no row of their own. Returns False when `dir_path`
    is not indexed (use graft(scan_subtree(...)) for it instead).
    """
    from . import model
    settings = settings_mod.validate(settings or {})
    p = norm_root(os.path.normpath(strip_long_prefix(dir_path)))
    did = model.find_dir(idx, p)
    if did is None:
        return False
    root = norm_root(drive.get("root") or idx.root)
    cls = Classifier(settings, internal=is_internal(drive))
    ctx, depth = cls.ctx_for(p, root)
    excluded = _excluded_set(settings)
    try:
        entries = _direct_files(p, depth, excluded)
    except OSError:
        entries = None
    model.clear_direct_files(idx, did)
    if entries:
        sub = _new_root_index(drive, p, idx.d_mtime[did])
        sub.partial = True
        sd = sub._new_dir("", -1, idx.d_mtime[did])
        _Walker(sub, cls, settings_mod.big_file_bytes(settings), excluded).scan_files(
            sd, ctx, depth, entries)
        model.graft(idx, sub, p)
    return True


def _isdir_st(st):
    return (st is not None and _stat.S_ISDIR(st.st_mode)
            and not (IS_NT and _is_reparse(st)))


def _lstat_or_none(p):
    try:
        return _lstat(p)
    except OSError:
        return None


def refresh(idx, path, drive, settings):
    """Bring `idx` in line with the disk at `path` after a file operation.

    path may be a directory, a file or a sequence display path, existing or gone.
    Directories are rescanned (replacing their subtree); for files / sequences the parent
    folder's direct files are re-read, so small files (no row of their own) are counted
    exactly once. Missing parents are handled by walking up to an existing folder.
    """
    from . import model
    p = norm_root(os.path.normpath(strip_long_prefix(path)))
    parts = rel_parts(idx.root, p)
    if parts is None:
        raise ValueError("path is outside this index")
    st = _lstat_or_none(p)
    if _isdir_st(st):
        top = p                             # climb to the first folder below an indexed one
        while top != idx.root and model.find_dir(idx, top) is None:
            par = os.path.dirname(top)
            if par == top or rel_parts(idx.root, par) is None or model.find_dir(idx, par) is not None:
                break
            top = par
        model.graft(idx, scan_subtree(top, drive, settings), top)
        return
    if not parts:
        return                              # drive root missing: nothing sensible to do
    r = model.find_item(idx, p)
    if r is not None and r[0] == "dir":
        model.detach(idx, p)                # folder gone (or replaced by a file)
    parent = os.path.dirname(p)
    if not _isdir_st(_lstat_or_none(parent)) or model.find_dir(idx, parent) is None:
        refresh(idx, parent, drive, settings)
        return
    refresh_files(idx, parent, drive, settings)


# ---------------------------------------------------------------- async scans

_active = {}
_active_lock = threading.Lock()


class ScanHandle:
    def __init__(self, drive, settings, progress_cb):
        self.drive = dict(drive)
        self.letter = norm_drive_key(drive.get("letter") or drive.get("root") or "")
        self.settings = settings_mod.validate(settings or {})
        self.progress_cb = progress_cb
        self.started_at = time.time()
        self.result = None
        self.error = None
        self._cancel = threading.Event()
        self.progress = {"letter": self.letter, "phase": "scan", "files": 0, "dirs": 0,
                         "bytes": 0, "current": "", "cats": [0] * NCAT,
                         "started_at": self.started_at}
        self._thread = threading.Thread(target=self._run, name="scan-%s" % self.letter,
                                        daemon=True)

    def cancel(self):
        self._cancel.set()

    @property
    def canceled(self):
        return self._cancel.is_set()

    def join(self, timeout=None):
        self._thread.join(timeout)
        return self.done

    @property
    def done(self):
        return self._thread.ident is not None and not self._thread.is_alive()

    def _emit(self, d):
        d = dict(d)
        d["letter"] = self.letter
        d["started_at"] = self.started_at
        self.progress = d
        cb = self.progress_cb
        if cb is not None:
            try:
                cb(d)
            except Exception:
                pass

    def _run(self):
        last = dict(self.progress)
        try:
            root = norm_root(self.drive.get("root") or "")
            if not root:
                raise ValueError("drive has no root")
            idx = _new_root_index(self.drive, root, 0.0)
            t0 = time.time()

            def emit(d):
                last.update(d)
                self._emit(d)
            w = _scan_dir_into(idx, root, self.drive, self.settings, self._cancel, emit)
            if self._cancel.is_set():
                raise _Canceled()
            idx.big_bytes = settings_mod.big_file_bytes(self.settings)
            idx.scanned_at = time.time()
            idx.scan_seconds = idx.scanned_at - t0
            self.result = idx
            self._emit({"phase": "done", "files": idx.d_files[0], "dirs": w.dirs - 1,
                        "bytes": idx.d_size[0], "current": "",
                        "cats": list(idx.d_cat[0:NCAT]), "errors": idx.errors})
        except _Canceled:
            self.result = None
            out = dict(last)
            out["phase"] = "canceled"
            self._emit(out)
        except Exception as e:  # noqa: BLE001 — reported to the UI
            self.result = None
            self.error = "%s: %s" % (type(e).__name__, e)
            out = dict(last)
            out["phase"] = "error"
            out["error"] = self.error
            self._emit(out)
        finally:
            with _active_lock:
                if _active.get(self.letter) is self:
                    del _active[self.letter]


def start_scan(drive, settings, progress_cb=None):
    """Start scanning drive['root'] in a new thread. Raises ScanBusyError for a duplicate."""
    h = ScanHandle(drive, settings, progress_cb)
    with _active_lock:
        cur = _active.get(h.letter)
        if cur is not None and not cur.done:
            raise ScanBusyError(h.letter)
        _active[h.letter] = h
    h._thread.start()
    return h


def is_scanning(letter):
    with _active_lock:
        h = _active.get(norm_drive_key(letter))
        return h is not None and not h.done


def get_scan(letter):
    with _active_lock:
        return _active.get(norm_drive_key(letter))

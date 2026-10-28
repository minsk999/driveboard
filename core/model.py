"""DriveIndex: directory tree + big-item table for one drive (SPEC §4).

Directories live in parallel arrays (id = index, 0 = root). Files smaller than the big-file
threshold only contribute to their directory's totals; big files and big image sequences are
rows in the item table.

Mutations (detach / graft / apply_rule) keep these invariants (see check_invariants):
  d_size[d]  == d_own[d]   + sum(alive child d_size)  + sum(alive direct item f_size)
  d_files[d] == d_ofiles[d] + sum(alive child d_files) + sum(alive direct item f_count)
  sum(d_cat[d]) == d_size[d] ; sum(d_catn[d]) == d_files[d] ; per-cat values >= 0
Callers that use one index from several threads should hold `idx.lock`.
"""

import os
import threading
import time
from array import array
from bisect import bisect_right

from .categories import (ARCHIVE, NCAT, PROJECT, RENDER, SEQ_DISPLAY_RE, SOURCE, VIDEO,
                         dominant, rel_parts, strip_long_prefix)

LOOSE_NAME = "(이 폴더의 작은 파일들)"
# 대표 영상 후보(SPEC-2 B.3)가 될 수 있는 카테고리와 우선순위(작을수록 앞). 캐시·프록시·앱은 후보가 아니다.
VID_PRIO = {RENDER: 0, VIDEO: 1, SOURCE: 2, ARCHIVE: 3}
# 편집 프로젝트 파일(SPEC-2 B.2 ③). 이 확장자 파일(카테고리 '프로젝트')이 직속으로 있는 폴더를 d_aep 에 둔다.
EDIT_PROJECT_EXT = frozenset({"aep", "aepx", "prproj", "drp"})
EDIT_PROJECT_CAT = PROJECT
_ZERO = (0,) * NCAT
_IS_NT = os.name == "nt"


class DriveIndex:
    def __init__(self, letter="", root="", label="", serial="", fs=""):
        self.letter = letter
        self.root = root
        self.label = label
        self.serial = serial
        self.fs = fs
        self.scanned_at = 0.0
        self.scan_seconds = 0.0
        self.errors = 0
        self.error_samples = []
        self.big_bytes = 50 << 20      # threshold this index was built with
        self.partial = False           # True: scan_subtree() of a file / sequence
        # directory table
        self.d_name = []
        self.d_parent = array("i")
        self.d_children = []
        self.d_size = array("q")
        self.d_files = array("q")
        self.d_newest = array("d")
        self.d_cat = array("q")        # ndirs*NCAT recursive bytes per category
        self.d_catn = array("q")       # ndirs*NCAT recursive file counts per category
        self.d_own = array("q")        # direct small-file bytes
        self.d_ofiles = array("q")     # direct small-file count
        self.d_onew = array("d")       # newest direct small-file mtime (0 = none)
        self.d_mtime = array("d")      # the directory's own mtime
        self.d_alive = bytearray()
        self.d_fids = []               # direct big item ids per dir (list or None)
        # big item table
        self.f_name = []
        self.f_dir = array("i")
        self.f_size = array("q")
        self.f_mtime = array("d")
        self.f_cat = array("b")
        self.f_count = array("i")
        self.f_alive = bytearray()
        self.f_seq = {}                # fid -> (first, last) for sequences
        self.d_vid = {}                # did -> (name, size, mtime, cat): 그 폴더 직속 대표 영상
        self.d_aep = set()             # 직속에 aep/aepx/prproj/drp 가 있는 폴더 id
        self.lock = threading.RLock()
        self._reset_caches()

    # ------------------------------------------------------------ internal helpers
    def _reset_caches(self):
        self._ver = 0 if not hasattr(self, "_ver") else self._ver + 1
        self._order = {}
        self._qcache = {}
        self._masks = {}
        self._kids = {}
        self._dlower = None
        self._flower = None
        self._blob = None
        self._paths = {}

    def _touch(self, names_changed=False):
        self._ver += 1
        self._order.clear()
        self._qcache.clear()
        self._masks.clear()
        self._kids.clear()
        if names_changed:
            self._dlower = None
            self._flower = None
            self._blob = None

    def _new_dir(self, name, parent, mtime=0.0):
        did = len(self.d_name)
        self.d_name.append(name)
        self.d_parent.append(parent)
        self.d_children.append(None)
        self.d_size.append(0)
        self.d_files.append(0)
        self.d_newest.append(mtime)
        self.d_cat.extend(_ZERO)
        self.d_catn.extend(_ZERO)
        self.d_own.append(0)
        self.d_ofiles.append(0)
        self.d_onew.append(0.0)
        self.d_mtime.append(mtime)
        self.d_alive.append(1)
        self.d_fids.append(None)
        if parent >= 0:
            ch = self.d_children[parent]
            if ch is None:
                self.d_children[parent] = [did]
            else:
                ch.append(did)
        return did

    def _new_item(self, did, name, size, mtime, cat, count=1, seq=None):
        fid = len(self.f_name)
        self.f_name.append(name)
        self.f_dir.append(did)
        self.f_size.append(size)
        self.f_mtime.append(mtime)
        self.f_cat.append(cat)
        self.f_count.append(count)
        self.f_alive.append(1)
        if seq is not None:
            self.f_seq[fid] = (int(seq[0]), int(seq[1]))
        lst = self.d_fids[did]
        if lst is None:
            self.d_fids[did] = [fid]
        else:
            lst.append(fid)
        return fid

    def rebuild_links(self):
        """Rebuild d_children / d_fids from d_parent / f_dir (after load)."""
        n = len(self.d_name)
        ch = [None] * n
        alive = self.d_alive
        par = self.d_parent
        for i in range(1, n):
            if alive[i]:
                p = par[i]
                if ch[p] is None:
                    ch[p] = [i]
                else:
                    ch[p].append(i)
        self.d_children = ch
        fids = [None] * n
        fa = self.f_alive
        fd = self.f_dir
        for i in range(len(self.f_name)):
            if fa[i]:
                d = fd[i]
                if fids[d] is None:
                    fids[d] = [i]
                else:
                    fids[d].append(i)
        self.d_fids = fids
        self._reset_caches()

    @property
    def ndirs(self):
        return len(self.d_name)

    @property
    def nitems(self):
        return len(self.f_name)


def norm_drive_key(key):
    """드라이브 키 정규화: 문자 드라이브는 대문자('d' -> 'D'), 네트워크 위치는 'N-' + 소문자 16진."""
    k = str(key or "").strip()
    if len(k) > 2 and k[:2].upper() == "N-":
        return "N-" + k[2:].lower()
    return k.upper()


def vid_better(a, b):
    """대표 영상 후보 a 가 b 보다 나은가 (우선순위 render>video>source>archive, 다음 mtime 최신, 이름)."""
    if b is None:
        return True
    pa, pb = VID_PRIO.get(a[3], 9), VID_PRIO.get(b[3], 9)
    if pa != pb:
        return pa < pb
    if a[2] != b[2]:
        return a[2] > b[2]
    return a[0] < b[0]


def best_video(idx, did=0):
    """서브트리 대표 영상 (did_of_video, (name, size, mtime, cat)) 또는 None."""
    dv = idx.d_vid
    if not dv:
        return None
    if did == 0:
        best = None
        bd = -1
        al = idx.d_alive
        for d, v in dv.items():
            if al[d] and vid_better(v, best):
                best, bd = v, d
        return (bd, best) if best is not None else None
    best = None
    bd = -1
    stack = [did]
    ch = idx.d_children
    while stack:
        d = stack.pop()
        v = dv.get(d)
        if v is not None and vid_better(v, best):
            best, bd = v, d
        k = ch[d]
        if k:
            stack.extend(k)
    return (bd, best) if best is not None else None


def norm_root(root):
    """'D:' -> 'D:\\' (a bare drive would make os.path.join drive-relative)."""
    if root:
        drv, rest = os.path.splitdrive(root)
        if drv and not rest:
            return drv + os.sep
    return root


def _norm_cat(cat):
    if cat is None or cat == "":
        return None
    try:
        c = int(cat)
    except (TypeError, ValueError):
        return None
    return c if 0 <= c < NCAT else None


def _norm_age(age_days):
    try:
        a = int(float(age_days or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, a)


def new_index(letter="", root="", label="", serial="", fs="", root_mtime=0.0):
    """An index containing only its root directory."""
    idx = DriveIndex(letter, norm_root(root), label, serial, fs)
    idx._new_dir("", -1, root_mtime)
    return idx


# ---------------------------------------------------------------- paths

def path_of_dir(idx, did):
    p = idx._paths.get(did)
    if p is not None:
        return p
    if did == 0:
        p = idx.root
    else:
        names = []
        d = did
        par = idx.d_parent
        dn = idx.d_name
        while d > 0:
            names.append(dn[d])
            d = par[d]
        names.reverse()
        p = os.path.join(idx.root, *names)
    idx._paths[did] = p
    return p


def path_of_file(idx, fid):
    return os.path.join(path_of_dir(idx, idx.f_dir[fid]), idx.f_name[fid])


def is_seq(idx, fid):
    return idx.f_count[fid] > 1 or fid in idx.f_seq


def seq_pattern(name):
    """(prefix, ndigits, ext) from a sequence display name, or None."""
    m = SEQ_DISPLAY_RE.match(name)
    if not m:
        return None
    return m.group(1), len(m.group(2)), m.group(3)


def seq_display_name(prefix, ndigits, ext):
    return "%s[%s].%s" % (prefix, "#" * ndigits, ext)


def _scan_path(p):
    if _IS_NT:
        p = os.path.abspath(p)
        if not p.startswith("\\\\?\\"):
            if p.startswith("\\\\"):
                return "\\\\?\\UNC\\" + p[2:]
            return "\\\\?\\" + p
    return p


def match_frames(dir_path, prefix, ndigits, ext, first=None, last=None):
    """[(num, name)] of real files in dir_path matching prefix+digits(ndigits)+'.'+ext."""
    out = []
    tail = "." + ext
    lp = len(prefix)
    lt = len(tail)
    want = lp + ndigits + lt
    try:
        it = os.scandir(_scan_path(dir_path))
    except OSError:
        return out
    with it:
        for e in it:
            nm = e.name
            if len(nm) != want or not nm.startswith(prefix) or not nm.endswith(tail):
                continue
            mid = nm[lp:lp + ndigits]
            if not mid.isdigit() or not mid.isascii():
                continue
            try:
                if e.is_symlink() or not e.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            num = int(mid)
            if first is not None and num < first:
                continue
            if last is not None and num > last:
                continue
            out.append((num, nm))
    out.sort()
    return out


def frames_of(idx, fid):
    """Real file paths of item `fid` (re-discovered on disk for sequences).

    For a sequence only files matching the stored pattern within first..last are returned.
    Files created after the scan inside that range are included — callers that delete or
    move should compare len() with idx.f_count[fid] if they need an exact match.
    """
    if not is_seq(idx, fid):
        return [path_of_file(idx, fid)]
    pat = seq_pattern(idx.f_name[fid])
    if pat is None:
        return []
    prefix, nd, ext = pat
    first, last = idx.f_seq.get(fid, (None, None))
    dpath = path_of_dir(idx, idx.f_dir[fid])
    return [os.path.join(dpath, nm) for _n, nm in match_frames(dpath, prefix, nd, ext, first, last)]


def _child_by_name(idx, did, key):
    kids = idx.d_children[did]
    if not kids:
        return None
    if len(kids) > 24:
        m = idx._kids.get(did)
        if m is None:
            dn = idx.d_name
            nc = os.path.normcase
            m = {nc(dn[c]): c for c in kids}
            idx._kids[did] = m
        return m.get(key)
    dn = idx.d_name
    nc = os.path.normcase
    for c in kids:
        if nc(dn[c]) == key:
            return c
    return None


def find_dir(idx, path):
    parts = rel_parts(idx.root, path)
    if parts is None or not idx.d_name:
        return None
    did = 0
    nc = os.path.normcase
    for comp in parts:
        did = _child_by_name(idx, did, nc(comp))
        if did is None:
            return None
    return did


def _find_file_in(idx, did, name):
    lst = idx.d_fids[did]
    if not lst:
        return None
    nc = os.path.normcase
    key = nc(name)
    fa = idx.f_alive
    fn = idx.f_name
    for fid in lst:
        if fa[fid] and nc(fn[fid]) == key:
            return fid
    return None


def find_item(idx, path):
    """('dir', did) | ('file', fid) | None. Sequences resolve by display path."""
    did = find_dir(idx, path)
    if did is not None:
        return ("dir", did)
    p = os.path.normpath(strip_long_prefix(path))
    parent, name = os.path.split(p)
    if not name:
        return None
    pdid = find_dir(idx, parent)
    if pdid is None:
        return None
    fid = _find_file_in(idx, pdid, name)
    if fid is None:
        return None
    return ("file", fid)


# ---------------------------------------------------------------- totals

def cat_totals(idx, did=0):
    b = did * NCAT
    return list(idx.d_cat[b:b + NCAT])


def cat_counts(idx, did=0):
    b = did * NCAT
    return list(idx.d_catn[b:b + NCAT])


def _own_cat(idx, did):
    """(bytes per cat, files per cat) of the direct small files of `did`."""
    b = did * NCAT
    vb = list(idx.d_cat[b:b + NCAT])
    vn = list(idx.d_catn[b:b + NCAT])
    dc = idx.d_cat
    dn = idx.d_catn
    for c in idx.d_children[did] or ():
        cb = c * NCAT
        for k in range(NCAT):
            vb[k] -= dc[cb + k]
            vn[k] -= dn[cb + k]
    fa = idx.f_alive
    for fid in idx.d_fids[did] or ():
        if fa[fid]:
            k = idx.f_cat[fid]
            vb[k] -= idx.f_size[fid]
            vn[k] -= idx.f_count[fid]
    return vb, vn


def summary(idx):
    alive_dirs = sum(idx.d_alive) - 1 if idx.d_alive else 0
    alive_items = sum(idx.f_alive)
    return {
        "letter": idx.letter,
        "root": idx.root,
        "scanned": True,
        "scanned_at": idx.scanned_at,
        "scan_seconds": idx.scan_seconds,
        "cats": cat_totals(idx, 0) if idx.d_name else [0] * NCAT,
        "cat_files": cat_counts(idx, 0) if idx.d_name else [0] * NCAT,
        "scanned_bytes": idx.d_size[0] if idx.d_name else 0,
        "files": idx.d_files[0] if idx.d_name else 0,
        "dirs": max(0, alive_dirs),
        "items": alive_items,
        "errors": idx.errors,
        "error_samples": list(idx.error_samples),
        "big_file_bytes": idx.big_bytes,
    }


# ---------------------------------------------------------------- listing

def _dir_entry(idx, d, cat, path=None):
    b = d * NCAT
    cb = list(idx.d_cat[b:b + NCAT])
    size = idx.d_size[d]
    nw = idx.d_newest[d]
    e = {"kind": "dir", "id": d, "name": idx.d_name[d],
         "path": path if path is not None else path_of_dir(idx, d),
         "size": size, "metric": size if cat is None else cb[cat],
         "cat_bytes": cb, "files": idx.d_files[d], "newest": nw, "mtime": nw,
         "cat": dominant(cb)}
    if cat is not None:
        e["cat_files"] = idx.d_catn[b + cat]
    return e


def _file_entry(idx, fid, path=None):
    size = idx.f_size[fid]
    c = idx.f_cat[fid]
    cb = [0] * NCAT
    cb[c] = size
    mt = idx.f_mtime[fid]
    cnt = idx.f_count[fid]
    e = {"kind": "file", "id": fid, "name": idx.f_name[fid],
         "path": path if path is not None else path_of_file(idx, fid),
         "size": size, "metric": size, "cat": c, "cat_bytes": cb,
         "files": cnt, "count": cnt, "newest": mt, "mtime": mt, "dir": idx.f_dir[fid],
         "seq": False}
    rng = idx.f_seq.get(fid)
    if rng is not None or cnt > 1:
        e["seq"] = True
        if rng is not None:
            e["first"], e["last"] = rng
    return e


def list_children(idx, did, cat=None, age_days=0, now=None):
    """Folder view rows: sub-directories, direct big items and one 'loose' row."""
    did = int(did)
    if did < 0 or did >= len(idx.d_name) or not idx.d_alive[did]:
        return []
    cat = _norm_cat(cat)
    age_days = _norm_age(age_days)
    if now is None:
        now = time.time()
    cutoff = now - age_days * 86400.0 if age_days > 0 else None
    out = []
    base = path_of_dir(idx, did)
    dc = idx.d_cat
    for c in idx.d_children[did] or ():
        if not idx.d_alive[c]:
            continue
        if cat is not None and dc[c * NCAT + cat] == 0:
            continue
        if cutoff is not None and idx.d_newest[c] > cutoff:
            continue
        out.append(_dir_entry(idx, c, cat, os.path.join(base, idx.d_name[c])))
    fa = idx.f_alive
    for fid in idx.d_fids[did] or ():
        if not fa[fid]:
            continue
        if cat is not None and idx.f_cat[fid] != cat:
            continue
        if cutoff is not None and idx.f_mtime[fid] > cutoff:
            continue
        out.append(_file_entry(idx, fid, os.path.join(base, idx.f_name[fid])))
    out.sort(key=lambda e: (-e["metric"], e["name"].lower()))
    if idx.d_ofiles[did] > 0 or idx.d_own[did] > 0:
        vb, vn = _own_cat(idx, did)
        own = idx.d_own[did]
        metric = own if cat is None else vb[cat]
        ok = metric > 0 or (cat is None and idx.d_ofiles[did] > 0)
        if ok and cutoff is not None and idx.d_onew[did] > cutoff:
            ok = False
        if ok:
            loose = {"kind": "loose", "id": did, "name": LOOSE_NAME, "path": base,
                     "size": own, "metric": metric, "cat_bytes": vb,
                     "files": idx.d_ofiles[did], "newest": idx.d_onew[did],
                     "mtime": idx.d_onew[did], "cat": dominant(vb)}
            if cat is not None:
                loose["cat_files"] = vn[cat]
            out.append(loose)
    return out


def _subtree_mask(idx, did):
    m = idx._masks.get(did)
    if m is not None:
        return m
    m = bytearray(len(idx.d_name))
    stack = [did]
    ch = idx.d_children
    while stack:
        d = stack.pop()
        m[d] = 1
        k = ch[d]
        if k:
            stack.extend(k)
    if len(idx._masks) > 16:
        idx._masks.clear()
    idx._masks[did] = m
    return m


def _lower_names(idx):
    if idx._flower is None or len(idx._flower) != len(idx.f_name):
        idx._flower = [n.lower() for n in idx.f_name]
    return idx._flower


def _lower_dirpaths(idx):
    dl = idx._dlower
    n = len(idx.d_name)
    if dl is None or len(dl) != n:
        dl = [""] * n
        par = idx.d_parent
        dn = idx.d_name
        for i in range(1, n):
            p = par[i]
            dl[i] = dl[p] + "/" + dn[i].lower()
        idx._dlower = dl
    return dl


_BLOB_SCAN_MAX = 100000


def _name_blob(idx):
    b = getattr(idx, "_blob", None)
    if b is None or b[2] != len(idx.f_name):
        ln = _lower_names(idx)
        starts = array("q")
        pos = 0
        for n in ln:
            starts.append(pos)
            pos += len(n) + 1
        b = ("\n".join(ln) + "\n", starts, len(ln))
        idx._blob = b
    return b


def _query_mask(idx, qk):
    """bytearray over item ids: 1 where the lowercase name or the folder path contains qk."""
    nf = len(idx.f_name)
    m = None
    if "\n" not in qk:
        blob, starts, _n = _name_blob(idx)
        if blob.count(qk) <= _BLOB_SCAN_MAX:
            m = bytearray(nf)
            find = blob.find
            pos = find(qk)
            while pos >= 0:
                fid = bisect_right(starts, pos) - 1
                m[fid] = 1
                nxt = starts[fid + 1] if fid + 1 < nf else len(blob)
                pos = find(qk, nxt)
    if m is None:
        m = bytearray(1 if qk in n else 0 for n in _lower_names(idx))
    dl = _lower_dirpaths(idx)
    fids = idx.d_fids
    for d, sp in enumerate(dl):
        if qk in sp:
            lst = fids[d]
            if lst:
                for f in lst:
                    m[f] = 1
    return m


def _order(idx, cat, sort):
    key = (cat, sort)
    o = idx._order.get(key)
    if o is not None:
        return o
    if cat is not None:
        base = _order(idx, None, sort)
        fc = idx.f_cat
        o = [i for i in base if fc[i] == cat]
    else:
        fa = idx.f_alive
        o = [i for i in range(len(fa)) if fa[i]]
        if sort == "mtime":
            o.sort(key=idx.f_mtime.__getitem__)
        elif sort == "name":
            ln = _lower_names(idx)
            o.sort(key=ln.__getitem__)
        else:
            o.sort(key=idx.f_size.__getitem__)
    idx._order[key] = o
    return o


def warm(idx, sorts=("size",)):
    """Pre-build the lazy sort orders (call after scan/load, e.g. in a background thread)."""
    for s in sorts:
        _order(idx, None, s)


def _filtered(idx, did, cat, age_days, q, sort, now):
    cutoff = now - age_days * 86400.0 if age_days and age_days > 0 else None
    # bucket cutoff to the minute so repeated pages hit the cache
    ck = None if cutoff is None else int(cutoff // 60)
    qk = (q or "").strip().lower()
    key = (did, cat, ck, qk, sort)
    hit = idx._qcache.get(key)
    if hit is not None:
        return hit
    o = _order(idx, cat, sort)
    if did != 0:
        m = _subtree_mask(idx, did)
        fd = idx.f_dir
        o = [i for i in o if m[fd[i]]]
    if cutoff is not None:
        fm = idx.f_mtime
        o = [i for i in o if fm[i] <= cutoff]
    if qk:
        m = _query_mask(idx, qk)
        o = [i for i in o if m[i]]
    total_bytes = sum(map(idx.f_size.__getitem__, o))
    res = (o, total_bytes)
    if len(idx._qcache) > 8:
        idx._qcache.clear()
    idx._qcache[key] = res
    return res


def list_files(idx, did=0, cat=None, age_days=0, q="", sort="size", order="desc",
               offset=0, limit=200, now=None):
    """(items, total_count, total_bytes) over the big-item table below `did`."""
    did = int(did or 0)
    if did < 0 or did >= len(idx.d_name) or not idx.d_alive[did]:
        return [], 0, 0
    cat = _norm_cat(cat)
    age_days = _norm_age(age_days)
    if sort not in ("size", "mtime", "name"):
        sort = "size"
    if now is None:
        now = time.time()
    o, total_bytes = _filtered(idx, did, cat, age_days, q, sort, now)
    total = len(o)
    offset = max(0, int(offset or 0))
    limit = max(0, int(limit or 0))
    if order == "asc":
        page = o[offset:offset + limit]
    else:
        hi = total - offset
        lo = max(0, hi - limit)
        page = o[lo:hi][::-1] if hi > 0 else []
    return [_file_entry(idx, fid) for fid in page], total, total_bytes


def small_remainder(idx, did, cat=None):
    """(bytes, files) under `did` that are not in the big-item table (for `cat`)."""
    did = int(did or 0)
    cat = _norm_cat(cat)
    if did < 0 or did >= len(idx.d_name) or not idx.d_alive[did]:
        return 0, 0
    o, big_bytes = _filtered(idx, did, cat, 0, "", "size", 0.0)
    fc = idx.f_count
    big_files = sum(map(fc.__getitem__, o))
    if cat is None:
        tb, tf = idx.d_size[did], idx.d_files[did]
    else:
        tb, tf = idx.d_cat[did * NCAT + cat], idx.d_catn[did * NCAT + cat]
    return max(0, tb - big_bytes), max(0, tf - big_files)


# ---------------------------------------------------------------- mutation

def _ancestors(idx, d):
    par = idx.d_parent
    while d >= 0:
        yield d
        d = par[d]


def _add_up(idx, d, size, files, vb, vn, sign):
    """Add (sign=+1) or subtract (sign=-1) totals on d and all its ancestors."""
    dc = idx.d_cat
    dn = idx.d_catn
    nz = [k for k in range(NCAT) if vb[k] or vn[k]]
    for a in _ancestors(idx, d):
        idx.d_size[a] += sign * size
        idx.d_files[a] += sign * files
        b = a * NCAT
        for k in nz:
            dc[b + k] += sign * vb[k]
            dn[b + k] += sign * vn[k]


def _recompute_newest_one(idx, d):
    fa = idx.f_alive
    if idx.d_files[d] > 0:
        nw = idx.d_onew[d]
        for c in idx.d_children[d] or ():
            if idx.d_files[c] > 0 and idx.d_newest[c] > nw:
                nw = idx.d_newest[c]
        for fid in idx.d_fids[d] or ():
            if fa[fid] and idx.f_mtime[fid] > nw:
                nw = idx.f_mtime[fid]
    else:
        nw = idx.d_mtime[d]
        for c in idx.d_children[d] or ():
            if idx.d_newest[c] > nw:
                nw = idx.d_newest[c]
    idx.d_newest[d] = nw


def _recompute_newest_up(idx, d):
    for a in _ancestors(idx, d):
        _recompute_newest_one(idx, a)


def _detach_dir(idx, did):
    """Tombstone a directory subtree; returns removed bytes."""
    if did == 0:
        raise ValueError("cannot detach the drive root")
    size = idx.d_size[did]
    files = idx.d_files[did]
    b = did * NCAT
    vb = list(idx.d_cat[b:b + NCAT])
    vn = list(idx.d_catn[b:b + NCAT])
    stack = [did]
    ch = idx.d_children
    fa = idx.f_alive
    dv = idx.d_vid
    while stack:
        d = stack.pop()
        idx.d_alive[d] = 0
        if dv:
            dv.pop(d, None)
        idx.d_aep.discard(d)
        for fid in idx.d_fids[d] or ():
            fa[fid] = 0
        k = ch[d]
        if k:
            stack.extend(k)
    p = idx.d_parent[did]
    kids = ch[p]
    if kids is not None:
        try:
            kids.remove(did)
        except ValueError:
            pass
        if not kids:
            ch[p] = None
    _add_up(idx, p, size, files, vb, vn, -1)
    _recompute_newest_up(idx, p)
    return size


def _detach_file(idx, fid):
    if not idx.f_alive[fid]:
        return 0
    idx.f_alive[fid] = 0
    d = idx.f_dir[fid]
    v = idx.d_vid.get(d)
    if v is not None and os.path.normcase(v[0]) == os.path.normcase(idx.f_name[fid]):
        del idx.d_vid[d]
    lst = idx.d_fids[d]
    if lst is not None:
        try:
            lst.remove(fid)
        except ValueError:
            pass
        if not lst:
            idx.d_fids[d] = None
    size = idx.f_size[fid]
    cnt = idx.f_count[fid]
    vb = [0] * NCAT
    vn = [0] * NCAT
    c = idx.f_cat[fid]
    vb[c] = size
    vn[c] = cnt
    _add_up(idx, d, size, cnt, vb, vn, -1)
    _recompute_newest_up(idx, d)
    return size


def clear_direct_files(idx, did):
    """Remove the direct files (small + big rows) of `did` from the totals; subdirs stay."""
    vb, vn = _own_cat(idx, did)
    own = idx.d_own[did]
    nfiles = idx.d_ofiles[did]
    for fid in list(idx.d_fids[did] or ()):
        _detach_file(idx, fid)
    if own or nfiles:
        _add_up(idx, did, own, nfiles, vb, vn, -1)
    idx.d_own[did] = 0
    idx.d_ofiles[did] = 0
    idx.d_onew[did] = 0.0
    idx.d_vid.pop(did, None)
    idx.d_aep.discard(did)
    _recompute_newest_up(idx, did)
    idx._touch()


def size_of(idx, path):
    """(bytes, files) of an indexed dir / big file / sequence, else None."""
    r = find_item(idx, path)
    if r is None:
        return None
    kind, i = r
    if kind == "dir":
        return idx.d_size[i], idx.d_files[i]
    return idx.f_size[i], idx.f_count[i]


def detach(idx, path):
    """Remove a directory / big file / sequence from the index. Returns removed bytes.

    Unknown paths (including small files that have no row) return 0 and change nothing.
    """
    r = find_item(idx, path)
    if r is None:
        return 0
    kind, i = r
    if kind == "dir":
        n = _detach_dir(idx, i)
    else:
        n = _detach_file(idx, i)
    idx._touch()
    return n


def _ensure_dirs(idx, dir_path, mtime=None):
    parts = rel_parts(idx.root, dir_path)
    if parts is None:
        raise ValueError("path is outside this index: %r" % (dir_path,))
    if mtime is None:
        mtime = time.time()
    did = 0
    nc = os.path.normcase
    created = False
    for comp in parts:
        c = _child_by_name(idx, did, nc(comp))
        if c is None:
            c = idx._new_dir(comp, did, mtime)
            idx._kids.pop(did, None)
            created = True
        did = c
    if created:
        _recompute_newest_up(idx, did)
    return did


def _copy_into(idx, sub, root_target, root_is_new):
    """Append all alive dirs/items of `sub` below `root_target` (an idx dir id).

    sub's root (dir 0) maps onto root_target. If root_is_new the target already carries no
    totals of its own and receives sub root's own fields; otherwise sub root's own small
    files are merged into the target's own fields.
    """
    n = len(sub.d_name)
    m = [-1] * n
    m[0] = root_target
    sa = sub.d_alive
    for j in range(1, n):
        if not sa[j]:
            continue
        p = m[sub.d_parent[j]]
        if p < 0:
            continue
        d = idx._new_dir(sub.d_name[j], p, sub.d_mtime[j])
        m[j] = d
        idx.d_size[d] = sub.d_size[j]
        idx.d_files[d] = sub.d_files[j]
        idx.d_newest[d] = sub.d_newest[j]
        idx.d_own[d] = sub.d_own[j]
        idx.d_ofiles[d] = sub.d_ofiles[j]
        idx.d_onew[d] = sub.d_onew[j]
        bj = j * NCAT
        bd = d * NCAT
        idx.d_cat[bd:bd + NCAT] = sub.d_cat[bj:bj + NCAT]
        idx.d_catn[bd:bd + NCAT] = sub.d_catn[bj:bj + NCAT]
    dv = idx.d_vid
    for j, v in sub.d_vid.items():
        if j and sa[j] and m[j] >= 0:
            dv[m[j]] = v
    for j in sub.d_aep:
        if j and sa[j] and m[j] >= 0:
            idx.d_aep.add(m[j])
    if 0 in sub.d_aep:
        idx.d_aep.add(root_target)
    elif root_is_new:
        idx.d_aep.discard(root_target)
    v0 = sub.d_vid.get(0)
    t = root_target
    if v0 is not None:
        cur = dv.get(t)
        if (root_is_new or cur is None or os.path.normcase(cur[0]) == os.path.normcase(v0[0])
                or vid_better(v0, cur)):
            dv[t] = v0
    elif root_is_new:
        dv.pop(t, None)
    idx.d_own[t] += sub.d_own[0]
    idx.d_ofiles[t] += sub.d_ofiles[0]
    if sub.d_onew[0] > idx.d_onew[t]:
        idx.d_onew[t] = sub.d_onew[0]
    if root_is_new:
        idx.d_mtime[t] = sub.d_mtime[0]
    fa = sub.f_alive
    for f in range(len(sub.f_name)):
        if not fa[f]:
            continue
        d = m[sub.f_dir[f]]
        if d < 0:
            continue
        idx._new_item(d, sub.f_name[f], sub.f_size[f], sub.f_mtime[f], sub.f_cat[f],
                      sub.f_count[f], sub.f_seq.get(f))
    return m


def graft(idx, sub, at_path):
    """Attach scan result `sub` at `at_path` (creating intermediate dirs).

    * sub of a directory (sub.partial False): replaces whatever the index had at at_path.
    * sub of a file / sequence (sub.partial True, sub.root = its parent directory): the items
      are added to that directory; an existing row with the same name is replaced.
      Small files are added to the directory's loose totals — the caller must not graft a
      small file that the index already counts.
    """
    if not sub.d_name:
        return
    if sub.partial:
        parent_path = sub.root
        parent = _ensure_dirs(idx, parent_path, sub.d_mtime[0] or None)
        for f in range(len(sub.f_name)):
            if sub.f_alive[f]:
                old = _find_file_in(idx, parent, sub.f_name[f])
                if old is not None:
                    _detach_file(idx, old)
        b0 = 0
        _copy_into(idx, sub, parent, False)
        _add_up(idx, parent, sub.d_size[0], sub.d_files[0],
                list(sub.d_cat[b0:b0 + NCAT]), list(sub.d_catn[b0:b0 + NCAT]), +1)
        _recompute_newest_up(idx, parent)
        idx._touch(names_changed=True)
        return
    parts = rel_parts(idx.root, at_path)
    if parts is None:
        raise ValueError("path is outside this index: %r" % (at_path,))
    if not parts:
        _replace_all(idx, sub)
        return
    existing = find_dir(idx, at_path)
    if existing is not None:
        _detach_dir(idx, existing)
        idx._touch()
    parent = _ensure_dirs(idx, os.path.dirname(os.path.normpath(strip_long_prefix(at_path))),
                          sub.d_mtime[0] or None)
    old = _find_file_in(idx, parent, parts[-1])
    if old is not None:
        _detach_file(idx, old)
    t = idx._new_dir(parts[-1], parent, sub.d_mtime[0])
    idx._kids.pop(parent, None)
    idx.d_size[t] = sub.d_size[0]
    idx.d_files[t] = sub.d_files[0]
    idx.d_newest[t] = sub.d_newest[0]
    bt = t * NCAT
    idx.d_cat[bt:bt + NCAT] = sub.d_cat[0:NCAT]
    idx.d_catn[bt:bt + NCAT] = sub.d_catn[0:NCAT]
    _copy_into(idx, sub, t, True)
    _add_up(idx, parent, sub.d_size[0], sub.d_files[0],
            list(sub.d_cat[0:NCAT]), list(sub.d_catn[0:NCAT]), +1)
    _recompute_newest_up(idx, parent)
    idx._touch(names_changed=True)


def _replace_all(idx, sub):
    """Replace the whole content of idx by sub (same drive root)."""
    keep = (idx.letter, idx.root, idx.label, idx.serial, idx.fs)
    for k in ("d_name", "d_parent", "d_children", "d_size", "d_files", "d_newest", "d_cat",
              "d_catn", "d_own", "d_ofiles", "d_onew", "d_mtime", "d_alive", "d_fids",
              "f_name", "f_dir", "f_size", "f_mtime", "f_cat", "f_count", "f_alive", "f_seq",
              "d_vid", "d_aep", "errors", "error_samples", "scanned_at", "scan_seconds", "big_bytes"):
        setattr(idx, k, getattr(sub, k))
    idx.letter, idx.root, idx.label, idx.serial, idx.fs = keep
    idx.d_name[0] = ""
    idx.d_parent[0] = -1
    idx.partial = False
    idx._reset_caches()


def _rule_vid(idx, d, cat, name=None):
    """규칙으로 바뀐 카테고리를 대표 영상 후보에 반영(후보가 될 수 없는 카테고리면 뺀다).

    한 폴더에 후보 하나만 기록하므로 순위가 뒤집힌 경우는 다음 스캔까지 근사다.
    """
    v = idx.d_vid.get(d)
    if v is None or (name is not None and os.path.normcase(v[0]) != os.path.normcase(name)):
        return
    if cat in VID_PRIO:
        idx.d_vid[d] = (v[0], v[1], v[2], cat)
    else:
        del idx.d_vid[d]


def apply_rule(idx, path, cat):
    """Reclassify everything at `path` (dir subtree or one item) as `cat`. No rescan."""
    cat = int(cat)
    if not 0 <= cat < NCAT:
        raise ValueError("bad category")
    r = find_item(idx, path)
    if r is None:
        # 행이 없는 작은 파일: 그 파일이 폴더의 대표 영상 후보라면 후보만 고친다
        parent, name = os.path.split(os.path.normpath(strip_long_prefix(path)))
        pd = find_dir(idx, parent) if name else None
        if pd is not None:
            _rule_vid(idx, pd, cat, name)
        return
    kind, i = r
    dc = idx.d_cat
    dn = idx.d_catn
    if kind == "file":
        old = idx.f_cat[i]
        if old == cat:
            return
        size = idx.f_size[i]
        cnt = idx.f_count[i]
        idx.f_cat[i] = cat
        _rule_vid(idx, idx.f_dir[i], cat, idx.f_name[i])
        for a in _ancestors(idx, idx.f_dir[i]):
            b = a * NCAT
            dc[b + old] -= size
            dc[b + cat] += size
            dn[b + old] -= cnt
            dn[b + cat] += cnt
        idx._touch()
        return
    did = i
    b = did * NCAT
    oldb = list(dc[b:b + NCAT])
    oldn = list(dn[b:b + NCAT])
    stack = [did]
    ch = idx.d_children
    fa = idx.f_alive
    while stack:
        d = stack.pop()
        bd = d * NCAT
        dc[bd:bd + NCAT] = array("q", _ZERO)
        dn[bd:bd + NCAT] = array("q", _ZERO)
        dc[bd + cat] = idx.d_size[d]
        dn[bd + cat] = idx.d_files[d]
        _rule_vid(idx, d, cat)
        for fid in idx.d_fids[d] or ():
            if fa[fid]:
                idx.f_cat[fid] = cat
        k = ch[d]
        if k:
            stack.extend(k)
    newb = [0] * NCAT
    newn = [0] * NCAT
    newb[cat] = idx.d_size[did]
    newn[cat] = idx.d_files[did]
    p = idx.d_parent[did]
    if p >= 0:
        for a in _ancestors(idx, p):
            ba = a * NCAT
            for k in range(NCAT):
                dc[ba + k] += newb[k] - oldb[k]
                dn[ba + k] += newn[k] - oldn[k]
    idx._touch()


# ---------------------------------------------------------------- checking

def check_invariants(idx):
    """List of human-readable violations (empty = consistent). O(n)."""
    errs = []
    n = len(idx.d_name)
    if n == 0:
        return ["no root"]
    size = [0] * n
    files = [0] * n
    cb = [[0] * NCAT for _ in range(n)]
    cn = [[0] * NCAT for _ in range(n)]
    for f in range(len(idx.f_name)):
        if not idx.f_alive[f]:
            continue
        d = idx.f_dir[f]
        if not idx.d_alive[d]:
            errs.append("alive item %d in dead dir %d" % (f, d))
            continue
        size[d] += idx.f_size[f]
        files[d] += idx.f_count[f]
    seen_children = set()
    for d in range(n):
        if not idx.d_alive[d]:
            continue
        for c in idx.d_children[d] or ():
            if not idx.d_alive[c]:
                errs.append("dead child %d listed under %d" % (c, d))
            if idx.d_parent[c] != d:
                errs.append("child %d of %d has parent %d" % (c, d, idx.d_parent[c]))
            seen_children.add(c)
            size[d] += idx.d_size[c]
            files[d] += idx.d_files[c]
        size[d] += idx.d_own[d]
        files[d] += idx.d_ofiles[d]
        if size[d] != idx.d_size[d]:
            errs.append("dir %d size %d != parts %d" % (d, idx.d_size[d], size[d]))
        if files[d] != idx.d_files[d]:
            errs.append("dir %d files %d != parts %d" % (d, idx.d_files[d], files[d]))
        b = d * NCAT
        vb = idx.d_cat[b:b + NCAT]
        vn = idx.d_catn[b:b + NCAT]
        if sum(vb) != idx.d_size[d]:
            errs.append("dir %d cat sum %d != size %d" % (d, sum(vb), idx.d_size[d]))
        if sum(vn) != idx.d_files[d]:
            errs.append("dir %d catn sum %d != files %d" % (d, sum(vn), idx.d_files[d]))
        if min(vb) < 0 or min(vn) < 0:
            errs.append("dir %d negative category value" % d)
        own_b, own_n = _own_cat(idx, d)
        if min(own_b) < 0 or min(own_n) < 0:
            errs.append("dir %d own per-cat negative %r" % (d, own_b))
        if sum(own_b) != idx.d_own[d]:
            errs.append("dir %d own per-cat sum %d != own %d" % (d, sum(own_b), idx.d_own[d]))
        if d and d not in seen_children:
            p = idx.d_parent[d]
            if p < 0 or not idx.d_alive[p] or d not in (idx.d_children[p] or ()):
                errs.append("alive dir %d not linked from parent" % d)
    for d in idx.d_aep:
        if not (0 <= d < n) or not idx.d_alive[d]:
            errs.append("d_aep on dead/unknown dir %r" % (d,))
    for d, v in idx.d_vid.items():
        if not (0 <= d < n) or not idx.d_alive[d]:
            errs.append("d_vid on dead/unknown dir %r" % (d,))
        elif not (isinstance(v, tuple) and len(v) == 4 and v[3] in VID_PRIO):
            errs.append("bad d_vid entry %r" % (v,))
    if sum(cat_totals(idx, 0)) != idx.d_size[0]:
        errs.append("root cat totals mismatch")
    return errs

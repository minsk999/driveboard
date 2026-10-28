"""Index persistence (SPEC §6). No pickle.

File layout:  MAGIC(8) + zlib( u32 header_len + header JSON + blocks... )
Header JSON holds version, byte order, metadata and the block table
[[name, typecode, count, nbytes], ...]. Typecode "s" = '\\0'-joined UTF-8 strings.
Any mismatch / corruption -> load returns None (caller rescans).
"""

import json
import os
import re
import struct
import sys
import tempfile
import time
import zlib
from array import array

from .categories import NCAT
from .model import DriveIndex
from .safety import expand_short_names

MAGIC = b"DBIDX\x00\x01\x00"
VERSION = 3          # 3: d_vid (대표 영상 후보, SPEC-2 B.3) + d_aep (편집 프로젝트 파일 폴더)
_STR_ERRORS = "surrogatepass"

_DIR_ARRAYS = (("d_parent", "i"), ("d_size", "q"), ("d_files", "q"), ("d_newest", "d"),
               ("d_cat", "q"), ("d_catn", "q"), ("d_own", "q"), ("d_ofiles", "q"),
               ("d_onew", "d"), ("d_mtime", "d"))
_FILE_ARRAYS = (("f_dir", "i"), ("f_size", "q"), ("f_mtime", "d"), ("f_cat", "b"),
                ("f_count", "i"))
_META_KEYS = ("letter", "root", "label", "serial", "fs", "scanned_at", "scan_seconds",
              "errors", "error_samples", "big_bytes")


# ---------------------------------------------------------------- locations

def data_dir(create=True):
    """DRIVEBOARD_HOME > %LOCALAPPDATA%\\DriveBoard (Windows) > ~/.driveboard."""
    p = os.environ.get("DRIVEBOARD_HOME")
    if not p:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.join(
                os.path.expanduser("~"), "AppData", "Local")
            p = os.path.join(base, "DriveBoard")
        else:
            p = os.path.join(os.path.expanduser("~"), ".driveboard")
    if create:
        os.makedirs(p, exist_ok=True)
    return expand_short_names(p)        # 8.3 짧은 이름 → 긴 이름(캐시 경로 비교가 문자열 비교라서)


def atomic_write(path, data):
    """Write bytes via temp file in the same directory + flush + fsync + os.replace."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _replace(tmp, path)
        tmp = None
        if os.name != "nt":
            try:
                dfd = os.open(d, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _replace(src, dst):
    # Windows: antivirus / indexer can hold the target open for a moment.
    tries = 20 if os.name == "nt" else 1
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]")


def index_key(idx_or_key):
    if isinstance(idx_or_key, DriveIndex):
        k = idx_or_key.serial or idx_or_key.letter
    else:
        k = idx_or_key
    k = _KEY_RE.sub("_", str(k or "").strip())
    if not k or k in (".", ".."):
        raise ValueError("index needs a serial or a drive letter")
    return k


def index_path(key):
    return os.path.join(data_dir(), "index-%s.dbi" % index_key(key))


# ---------------------------------------------------------------- encode

def _alive_view(idx):
    """Arrays to write, compacting tombstones (ids are renumbered when needed)."""
    nd = len(idx.d_name)
    nf = len(idx.f_name)
    if all(idx.d_alive) and all(idx.f_alive):
        out = {k: getattr(idx, k) for k, _t in _DIR_ARRAYS + _FILE_ARRAYS}
        out["d_name"] = idx.d_name
        out["f_name"] = idx.f_name
        seq = sorted(idx.f_seq.items())
        out["vid"] = sorted(idx.d_vid.items())
        out["aep"] = sorted(idx.d_aep)
        return out, seq
    m = [-1] * nd
    keep = []
    for i in range(nd):
        if idx.d_alive[i] and (i == 0 or m[idx.d_parent[i]] >= 0):
            m[i] = len(keep)
            keep.append(i)
    out = {"d_name": [idx.d_name[i] for i in keep]}
    for k, t in _DIR_ARRAYS:
        src = getattr(idx, k)
        if k in ("d_cat", "d_catn"):
            a = array(t)
            for i in keep:
                a.extend(src[i * NCAT:(i + 1) * NCAT])
        elif k == "d_parent":
            a = array(t, (m[src[i]] if src[i] >= 0 else -1 for i in keep))
        else:
            a = array(t, (src[i] for i in keep))
        out[k] = a
    fkeep = [f for f in range(nf) if idx.f_alive[f] and m[idx.f_dir[f]] >= 0]
    fm = {f: j for j, f in enumerate(fkeep)}
    out["f_name"] = [idx.f_name[f] for f in fkeep]
    for k, t in _FILE_ARRAYS:
        src = getattr(idx, k)
        if k == "f_dir":
            out[k] = array(t, (m[src[f]] for f in fkeep))
        else:
            out[k] = array(t, (src[f] for f in fkeep))
    seq = sorted((fm[f], r) for f, r in idx.f_seq.items() if f in fm)
    out["vid"] = sorted((m[d], v) for d, v in idx.d_vid.items() if 0 <= d < nd and m[d] >= 0)
    out["aep"] = sorted(m[d] for d in idx.d_aep if 0 <= d < nd and m[d] >= 0)
    return out, seq


def encode_index(idx):
    view, seq = _alive_view(idx)
    blocks = []
    table = []

    def add(name, tc, count, data):
        table.append([name, tc, count, len(data)])
        blocks.append(data)

    for k in ("d_name", "f_name"):
        lst = view[k]
        data = "\0".join(lst).encode("utf-8", _STR_ERRORS)
        add(k, "s", len(lst), data)
    for k, t in _DIR_ARRAYS + _FILE_ARRAYS:
        a = view[k]
        add(k, t, len(a), a.tobytes())
    sa = array("i", (f for f, _r in seq))
    s1 = array("q", (r[0] for _f, r in seq))
    s2 = array("q", (r[1] for _f, r in seq))
    add("seq_fid", "i", len(sa), sa.tobytes())
    add("seq_first", "q", len(s1), s1.tobytes())
    add("seq_last", "q", len(s2), s2.tobytes())
    vid = view["vid"]
    add("vid_name", "s", len(vid), "\0".join(v[0] for _d, v in vid).encode("utf-8", _STR_ERRORS))
    for name, tc, k in (("vid_dir", "i", None), ("vid_size", "q", 1), ("vid_mtime", "d", 2),
                        ("vid_cat", "b", 3)):
        a = array(tc, (d if k is None else v[k] for d, v in vid))
        add(name, tc, len(a), a.tobytes())
    a = array("i", view["aep"])
    add("aep_dir", "i", len(a), a.tobytes())
    meta = {k: getattr(idx, k) for k in _META_KEYS}
    header = {"version": VERSION, "byteorder": sys.byteorder, "ncat": NCAT,
              "itemsize": {t: array(t).itemsize for t in "bidq"},
              "meta": meta, "ndirs": len(view["d_name"]), "nitems": len(view["f_name"]),
              "blocks": table}
    hj = json.dumps(header, ensure_ascii=True).encode("ascii")
    payload = b"".join([struct.pack("<I", len(hj)), hj] + blocks)
    return MAGIC + zlib.compress(payload, 6)


# ---------------------------------------------------------------- decode

def _read_header(payload):
    (hl,) = struct.unpack_from("<I", payload, 0)
    header = json.loads(payload[4:4 + hl].decode("utf-8"))
    return header, 4 + hl


def decode_index(data):
    """DriveIndex or None (bad magic / version / corruption)."""
    try:
        if not data.startswith(MAGIC):
            return None
        payload = zlib.decompress(data[len(MAGIC):])
        header, pos = _read_header(payload)
        if header.get("version") != VERSION or header.get("ncat") != NCAT:
            return None
        sizes = header.get("itemsize") or {}
        for t in "bidq":
            if sizes.get(t) != array(t).itemsize:
                return None
        swap = header.get("byteorder") != sys.byteorder
        got = {}
        for name, tc, count, nbytes in header["blocks"]:
            chunk = payload[pos:pos + nbytes]
            if len(chunk) != nbytes:
                return None
            pos += nbytes
            if tc == "s":
                lst = chunk.decode("utf-8", _STR_ERRORS).split("\0") if count else []
                if len(lst) != count:
                    return None
                got[name] = lst
            else:
                a = array(tc)
                a.frombytes(chunk)
                if swap:
                    a.byteswap()
                if len(a) != count:
                    return None
                got[name] = a
        if pos != len(payload):
            return None
        nd = header["ndirs"]
        nf = header["nitems"]
        if nd < 1 or len(got["d_name"]) != nd or len(got["f_name"]) != nf:
            return None
        for k, _t in _DIR_ARRAYS:
            want = nd * NCAT if k in ("d_cat", "d_catn") else nd
            if len(got[k]) != want:
                return None
        for k, _t in _FILE_ARRAYS:
            if len(got[k]) != nf:
                return None
        par = got["d_parent"]
        if par[0] != -1:
            return None
        for i in range(1, nd):
            if not 0 <= par[i] < i:
                return None
        fd = got["f_dir"]
        if nf and (min(fd) < 0 or max(fd) >= nd):
            return None
        fc = got["f_cat"]
        if nf and (min(fc) < 0 or max(fc) >= NCAT):
            return None
        idx = DriveIndex()
        meta = header.get("meta") or {}
        for k in _META_KEYS:
            if k in meta:
                setattr(idx, k, meta[k])
        idx.error_samples = list(idx.error_samples or [])
        idx.d_name = got["d_name"]
        idx.f_name = got["f_name"]
        for k, _t in _DIR_ARRAYS + _FILE_ARRAYS:
            setattr(idx, k, got[k])
        idx.d_alive = bytearray(b"\x01") * nd
        idx.f_alive = bytearray(b"\x01") * nf
        sf, s1, s2 = got["seq_fid"], got["seq_first"], got["seq_last"]
        if not (len(sf) == len(s1) == len(s2)):
            return None
        idx.f_seq = {}
        for j in range(len(sf)):
            if not 0 <= sf[j] < nf:
                return None
            idx.f_seq[sf[j]] = (s1[j], s2[j])
        vn = got["vid_name"]
        vd, vs, vm, vc = got["vid_dir"], got["vid_size"], got["vid_mtime"], got["vid_cat"]
        if not (len(vn) == len(vd) == len(vs) == len(vm) == len(vc)):
            return None
        idx.d_vid = {}
        for j in range(len(vd)):
            if not 0 <= vd[j] < nd or not 0 <= vc[j] < NCAT:
                return None
            idx.d_vid[vd[j]] = (vn[j], vs[j], vm[j], vc[j])
        ad = got["aep_dir"]
        if len(ad) and (min(ad) < 0 or max(ad) >= nd):
            return None
        idx.d_aep = set(ad)
        idx.rebuild_links()
        return idx
    except Exception:
        return None


# ---------------------------------------------------------------- public API

def save_index(idx):
    """Atomically write idx to index-<serial or letter>.dbi; returns the path."""
    path = index_path(idx)
    atomic_write(path, encode_index(idx))
    return path


def load_index(serial_or_letter):
    """DriveIndex or None (missing, wrong version, corrupted)."""
    try:
        path = index_path(serial_or_letter)
        with open(path, "rb") as f:
            data = f.read()
    except (OSError, ValueError):
        return None
    return decode_index(data)


def delete_index(serial_or_letter):
    try:
        os.unlink(index_path(serial_or_letter))
        return True
    except (OSError, ValueError):
        return False


def _peek_header(path):
    with open(path, "rb") as f:
        head = f.read(len(MAGIC))
        if head != MAGIC:
            return None
        d = zlib.decompressobj()
        buf = b""
        need = None
        while True:
            chunk = f.read(65536)
            if chunk:
                buf += d.decompress(chunk)
            if need is None and len(buf) >= 4:
                (hl,) = struct.unpack_from("<I", buf, 0)
                need = 4 + hl
            if need is not None and len(buf) >= need:
                return json.loads(buf[4:need].decode("utf-8"))
            if not chunk:
                return None


def list_indexes():
    """[{key, path, version, letter, serial, label, root, scanned_at, ndirs, nitems, bytes}]."""
    out = []
    try:
        names = os.listdir(data_dir())
    except OSError:
        return out
    for nm in sorted(names):
        if not (nm.startswith("index-") and nm.endswith(".dbi")):
            continue
        p = os.path.join(data_dir(), nm)
        try:
            h = _peek_header(p)
        except Exception:
            h = None
        if not h:
            continue
        meta = h.get("meta") or {}
        try:
            fsize = os.path.getsize(p)
        except OSError:
            fsize = 0
        out.append({"key": nm[6:-4], "path": p, "version": h.get("version"),
                    "valid_version": h.get("version") == VERSION,
                    "letter": meta.get("letter"), "serial": meta.get("serial"),
                    "label": meta.get("label"), "root": meta.get("root"),
                    "scanned_at": meta.get("scanned_at"), "ndirs": h.get("ndirs"),
                    "nitems": h.get("nitems"), "bytes": fsize})
    return out

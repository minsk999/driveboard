"""Duplicate big files across drives: same size + same name (case-insensitive).

find_dupes() is index-only (no disk I/O). quick_hash()/verify_group() read the files.
"""

import hashlib
import os

from .categories import APP
from .model import _scan_path, path_of_file

QUICK_CHUNK = 1 << 20


def _group_id(size, lname):
    h = hashlib.blake2b(("%d:%s" % (size, lname)).encode("utf-8", "surrogatepass"),
                        digest_size=8)
    return "%d-%s" % (size, h.hexdigest())


def find_dupes(indexes, min_bytes=50 << 20):
    """Groups of >=2 distinct alive, non-sequence, non-app big files with equal (size, name).

    indexes: {letter: DriveIndex}. Returns groups sorted by wasted bytes (desc):
    {id, size, name, wasted, verified: None, items: [{drive, path, mtime, key}]}
    """
    by_size = {}
    for letter, idx in indexes.items():
        if idx is None:
            continue
        fa, fs, fc, fk = idx.f_alive, idx.f_size, idx.f_count, idx.f_cat
        for i in range(len(fa)):
            if fa[i] and fc[i] == 1 and fk[i] != APP:
                s = fs[i]
                if s >= min_bytes:
                    lst = by_size.get(s)
                    if lst is None:
                        by_size[s] = [(letter, i)]
                    else:
                        lst.append((letter, i))
    groups = []
    for size, lst in by_size.items():
        if len(lst) < 2:
            continue
        by_name = {}
        for letter, i in lst:
            nm = indexes[letter].f_name[i]
            by_name.setdefault(nm.lower(), []).append((letter, i))
        for lname, members in by_name.items():
            if len(members) < 2:
                continue
            seen = set()
            items = []
            for letter, i in members:
                idx = indexes[letter]
                p = path_of_file(idx, i)
                # 경로만으로 가린다 — 같은 공유가 두 이름(Z: 와 등록한 NAS)으로 보이면 같은 파일이 두 번 나온다
                k = os.path.normcase(os.path.normpath(p))
                if k in seen:
                    continue
                seen.add(k)
                items.append({"drive": letter, "path": p, "mtime": idx.f_mtime[i],
                              "key": "%s:file:%d" % (letter, i)})
            if len(items) < 2:
                continue
            items.sort(key=lambda it: (it["mtime"], it["drive"], it["path"]))
            groups.append({"id": _group_id(size, lname), "size": size,
                           "name": indexes[members[0][0]].f_name[members[0][1]],
                           "wasted": size * (len(items) - 1), "verified": None,
                           "items": items})
    groups.sort(key=lambda g: (-g["wasted"], g["name"].lower(), g["id"]))
    return groups


def wasted_total(groups):
    return sum(g["wasted"] for g in groups)


def quick_hash(path):
    """'<size>-<blake2b hex>' over the size and the first / middle / last 1MB.

    Files of 3MB or less are hashed whole. Raises OSError when unreadable.
    """
    return quick_hash_stat(path)[0]


def quick_hash_stat(path):
    """(quick_hash, size, mtime) — 해시를 읽은 그 파일의 크기·수정 시각(지문용)."""
    with open(_scan_path(path), "rb") as f:
        st = os.fstat(f.fileno())
        size = st.st_size
        h = hashlib.blake2b(digest_size=20)
        h.update(size.to_bytes(8, "little"))
        if size <= 3 * QUICK_CHUNK:
            while True:
                b = f.read(QUICK_CHUNK)
                if not b:
                    break
                h.update(b)
        else:
            for off in (0, size // 2 - QUICK_CHUNK // 2, size - QUICK_CHUNK):
                f.seek(off)
                h.update(f.read(QUICK_CHUNK))
    return "%d-%s" % (size, h.hexdigest()), size, st.st_mtime


def verify_group(group, cancel=None):
    """Hash every item; sets and returns group['verified'].

    True  = every item has the same quick hash (and at least two were read)
    False = at least two readable items differ
    None  = fewer than two items could be read (or canceled)
    Item dicts get 'hash' (or 'error') and 'disk_size'/'disk_mtime' (what was read — see fingerprint()).
    """
    hashes = []
    for it in group["items"]:
        if cancel is not None and cancel():
            group["verified"] = None
            return None
        try:
            hv, sz, mt = quick_hash_stat(it["path"])
            it["hash"] = hv
            it["disk_size"] = sz
            it["disk_mtime"] = mt
            it.pop("error", None)
            hashes.append(hv)
        except OSError as e:
            it["hash"] = None
            it["disk_size"] = it["disk_mtime"] = None
            it["error"] = str(e)
    if len(hashes) < 2:
        res = None
    else:
        res = len(set(hashes)) == 1 and len(hashes) == len(group["items"])
        if not res and len(set(hashes)) == 1:
            res = None      # the readable ones match but some could not be read
    group["verified"] = res
    return res


# ── 확인 결과의 지문 ──
# 묶음 id 는 「크기:이름」뿐이라 같은 이름·크기로 다시 내보낸 파일, 새로 들어온 같은 이름의 파일도 같은 id 다.
# 그래서 확인 결과는 「그때 읽은 파일들」의 (경로, 크기, 수정 시각) 과 함께 두고, 지금 인덱스의 묶음이
# 그 지문과 그대로 맞을 때만 다시 보여 준다. 하나라도 다르면 미확인(None)이다.
_MTIME_TOL = 1e-3


def _pkey(p):
    return os.path.normcase(os.path.normpath(p))


def fingerprint(group):
    """verify_group 을 마친 묶음 → {경로 키: (크기, 수정 시각)} — 디스크에서 읽은 값(인덱스 값이 아니다)."""
    return {_pkey(it["path"]): (it.get("disk_size"), it.get("disk_mtime")) for it in group["items"]}


def fingerprint_matches(fp, group):
    """지금(인덱스) 묶음의 구성원·크기·수정 시각이 지문과 모두 같은가."""
    if not isinstance(fp, dict) or len(fp) != len(group["items"]):
        return False
    size = group["size"]
    for it in group["items"]:
        v = fp.get(_pkey(it["path"]))
        if v is None:
            return False
        sz, mt = v
        if sz is None or mt is None or sz != size:
            return False
        try:
            if abs(float(it["mtime"]) - float(mt)) > _MTIME_TOL:
                return False
        except (TypeError, ValueError):
            return False
    return True

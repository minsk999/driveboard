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
                k = (letter, os.path.normcase(os.path.normpath(p)))
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
    with open(_scan_path(path), "rb") as f:
        size = os.fstat(f.fileno()).st_size
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
    return "%d-%s" % (size, h.hexdigest())


def verify_group(group, cancel=None):
    """Hash every item; sets and returns group['verified'].

    True  = every item has the same quick hash (and at least two were read)
    False = at least two readable items differ
    None  = fewer than two items could be read (or canceled)
    Item dicts get 'hash' (or 'error').
    """
    hashes = []
    for it in group["items"]:
        if cancel is not None and cancel():
            group["verified"] = None
            return None
        try:
            hv = quick_hash(it["path"])
            it["hash"] = hv
            it.pop("error", None)
            hashes.append(hv)
        except OSError as e:
            it["hash"] = None
            it["error"] = str(e)
    if len(hashes) < 2:
        res = None
    else:
        res = len(set(hashes)) == 1 and len(hashes) == len(group["items"])
        if not res and len(set(hashes)) == 1:
            res = None      # the readable ones match but some could not be read
    group["verified"] = res
    return res

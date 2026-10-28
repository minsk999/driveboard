"""Recommendations for the home screen (SPEC §10).

build(indexes, drives_state, settings, dupe_groups, now) -> [rec]
rec = {id, kind, title, desc, bytes, count, tone: 'safe'|'review'|'info', action}
Sorted: tone 'safe' first, then bytes descending.
"""

import math
import time

from .categories import APP, CACHE, NCAT, RENDER, ROOT_SYS_DIRS
from .model import _order, list_files

KiB = 1 << 10
MiB = 1 << 20
GiB = 1 << 30

CACHE_MIN = GiB
BIN_MIN = 100 * MiB
OLD_RENDER_MIN = GiB
DUPES_MIN = GiB
BIG_FILE = 10 * GiB
OLD_PROJECT_DAYS = 365
OLD_PROJECT_MIN = GiB
BALANCE_FREE_RATIO = 0.10

_USER_STD = frozenset({"desktop", "documents", "downloads", "pictures", "videos", "music",
                       "onedrive"})


def fmt_bytes(n):
    """Explorer-style size: 1024-based, 3 significant digits (truncated), KB/MB/GB/TB."""
    n = int(n or 0)
    if n <= 0:
        return "0KB"
    units = ("KB", "MB", "GB", "TB", "PB")
    v = n / 1024.0
    u = 0
    while v >= 1000 and u < len(units) - 1:
        v /= 1024.0
        u += 1
    if u == 0 and v < 1:
        return "%.1fKB" % max(0.1, math.floor(v * 10 + 1e-9) / 10)
    if v < 10:
        s = "%.2f" % (math.floor(v * 100 + 1e-9) / 100)
    elif v < 100:
        s = "%.1f" % (math.floor(v * 10 + 1e-9) / 10)
    else:
        s = "%d" % math.floor(v + 1e-9)
    return s + units[u]


def fmt_days(days):
    days = int(days)
    if days >= 365 and days % 365 == 0:
        return "%d년" % (days // 365)
    if days >= 30 and days % 30 == 0:
        return "%d개월" % (days // 30)
    return "%d일" % days


def _drive_list(drives_state):
    if not drives_state:
        return []
    if isinstance(drives_state, dict):
        out = []
        for k, v in drives_state.items():
            d = dict(v or {})
            d.setdefault("letter", k)
            out.append(d)
        return out
    return [dict(d) for d in drives_state if d]


def _is_letter(L):
    return isinstance(L, str) and len(L) == 1 and L.isalpha()


def _dname(d):
    lab = d.get("label") or ""
    L = d.get("letter") or "?"
    if not _is_letter(L):            # NAS(N-xxxxxx)·연결 안 된 장치(~시리얼): 내부 키 대신 이름만
        return lab or _unnamed(L)
    return ("%s (%s:)" % (lab, L)) if lab else ("%s:" % L)


def _where(L, d):
    """문장 속 위치 표기: 드라이브 문자는 'D:', 네트워크 위치는 라벨."""
    if _is_letter(L):
        return "%s:" % L
    return (d or {}).get("label") or _unnamed(L)


def _unnamed(L):
    return "연결 안 된 장치" if str(L).startswith("~") else "네트워크 위치"


def _rec(rid, kind, title, desc, nbytes, count, tone, action):
    return {"id": rid, "kind": kind, "title": title, "desc": desc, "bytes": int(nbytes),
            "count": int(count), "tone": tone, "action": action}


def _view(drive, view, cat=None, age=0, dir_id=None):
    a = {"type": "view", "drive": drive, "view": view, "cat": cat, "age": age}
    if dir_id is not None:
        a["dir"] = dir_id
    return a


def _one_or_all(letters):
    letters = sorted(set(letters))
    return letters[0] if len(letters) == 1 else "*"


def _old_folders(idx, cutoff):
    """Maximal folders (depth 1-3; under Users\\<name>\\<std> depth 4-5) untouched since cutoff."""
    out = []
    dn = idx.d_name
    stack = [(c, 1, 0) for c in (idx.d_children[0] or ())]
    # zone: 0 normal, 1 = Users, 2 = Users\<name>, 3 = Users\<name>\<std folder>
    while stack:
        d, depth, zone = stack.pop()
        if not idx.d_alive[d]:
            continue
        nl = dn[d].strip().lower()
        size = idx.d_size[d]
        if size < OLD_PROJECT_MIN:
            continue
        if depth == 1 and nl in ROOT_SYS_DIRS:
            continue
        if depth == 1 and nl == "users":
            stack.extend((c, 2, 1) for c in (idx.d_children[d] or ()))
            continue
        if zone == 1:
            stack.extend((c, 3, 2) for c in (idx.d_children[d] or ()))
            continue
        if zone == 2:
            if nl in _USER_STD:
                stack.extend((c, 4, 3) for c in (idx.d_children[d] or ()))
            continue        # AppData and other profile folders are never suggested
        b = d * NCAT
        app_share = idx.d_cat[b + APP] / size
        cache_share = idx.d_cat[b + CACHE] / size
        max_depth = 5 if zone == 3 else 3
        if idx.d_newest[d] <= cutoff and app_share < 0.10 and cache_share < 0.5:
            out.append(d)
            continue
        if depth < max_depth:
            stack.extend((c, depth + 1, zone) for c in (idx.d_children[d] or ()))
    return out


def build(indexes, drives_state, settings, dupe_groups, now=None):
    if now is None:
        now = time.time()
    settings = settings or {}
    indexes = {k: v for k, v in (indexes or {}).items() if v is not None and v.d_name}
    drives = _drive_list(drives_state)
    by_letter = {d.get("letter"): d for d in drives}
    recs = []

    # 1. cache
    cb, cn, cl = 0, 0, []
    for L, idx in indexes.items():
        b = idx.d_cat[CACHE]
        if b > 0:
            cb += b
            cn += idx.d_catn[CACHE]
            cl.append(L)
    if cb >= CACHE_MIN:
        recs.append(_rec("cache", "cache", "캐시·임시 파일 정리",
                         "AE·프리미어 캐시는 지워도 다시 만들어져요.",
                         cb, cn, "safe", _view(_one_or_all(cl), "files", CACHE, 0)))

    # 2. recycle bins
    bins = [d for d in drives if int(d.get("bin_bytes") or 0) >= BIN_MIN]
    if bins:
        bins.sort(key=lambda d: -int(d.get("bin_bytes") or 0))
        parts = " · ".join("%s: %s" % (d.get("letter"), fmt_bytes(d.get("bin_bytes")))
                           for d in bins)
        recs.append(_rec("bin", "bin", "휴지통 비우기",
                         "휴지통으로 보낸 용량은 휴지통을 비워야 그 드라이브에서 확보됩니다. (%s)"
                         % parts,
                         sum(int(d.get("bin_bytes") or 0) for d in bins),
                         sum(int(d.get("bin_count") or 0) for d in bins), "review",
                         {"type": "empty_bin", "drives": [d.get("letter") for d in bins]}))

    # 3. old renders
    old_days = int(settings.get("old_days") or 180)
    rb, rn, rl = 0, 0, []
    for L, idx in indexes.items():
        _items, n, b = list_files(idx, 0, RENDER, old_days, limit=0, now=now)
        if n:
            rb += b
            rn += n
            rl.append(L)
    if rb >= OLD_RENDER_MIN:
        recs.append(_rec("old_render", "old_render", "오래된 렌더·출력",
                         "%s 넘게 수정하지 않은 렌더·출력 파일이에요. 납품이 끝났다면 정리해 보세요."
                         % fmt_days(old_days),
                         rb, rn, "review", _view(_one_or_all(rl), "files", RENDER, old_days)))

    # 4. duplicates
    if dupe_groups:
        wasted = sum(int(g.get("wasted") or 0) for g in dupe_groups)
        if wasted >= DUPES_MIN:
            recs.append(_rec("dupes", "dupes", "중복 파일",
                             "이름과 크기가 같은 파일이 %d묶음 있어요. 내용이 같은지 확인한 뒤 "
                             "사본을 정리해 보세요." % len(dupe_groups),
                             wasted, len(dupe_groups), "review", {"type": "dupes"}))

    # 5. big files
    bb, bn, bl = 0, 0, []
    for L, idx in indexes.items():
        o = _order(idx, None, "size")
        fs, fk, fc = idx.f_size, idx.f_cat, idx.f_count
        for j in range(len(o) - 1, -1, -1):
            i = o[j]
            if fs[i] < BIG_FILE:
                break
            if fc[i] == 1 and fk[i] != APP and fk[i] != CACHE:
                bb += fs[i]
                bn += 1
                if not bl or bl[-1] != L:
                    bl.append(L)
    if bn:
        recs.append(_rec("big", "big", "10GB 이상 큰 파일",
                         "10GB가 넘는 파일 %d개가 %s를 차지하고 있어요." % (bn, fmt_bytes(bb)),
                         bb, bn, "review", _view(_one_or_all(bl), "files", None, 0)))

    # 6. old project folders -> move to a roomy drive
    cutoff = now - OLD_PROJECT_DAYS * 86400.0
    hdd = [d for d in drives if d.get("kind") == "hdd"]
    if hdd:
        target = max(hdd, key=lambda d: int(d.get("free") or 0))
        sources = {L for L in indexes if (by_letter.get(L) or {}).get("kind") != "hdd"}
    elif drives:
        target = max(drives, key=lambda d: int(d.get("free") or 0))
        sources = {L for L in indexes if L != target.get("letter")}
    else:
        target = None
        sources = set(indexes)
    # 외장·NAS 는 이미 보관 장소다 — 거기 있는 오래된 폴더를 「옮겨 보세요」 하지 않는다
    sources = {L for L in sources
               if not ((by_letter.get(L) or {}).get("kind") == "network"
                       or (by_letter.get(L) or {}).get("external"))}
    found = []
    for L in sorted(sources):
        idx = indexes[L]
        for d in _old_folders(idx, cutoff):
            found.append((idx.d_size[d], L, idx, d))
    if found:
        found.sort(key=lambda t: (-t[0], t[1]))
        total = sum(t[0] for t in found)
        size0, L0, idx0, d0 = found[0]
        desc = "1년 넘게 수정하지 않은 폴더 %d개예요. 가장 큰 폴더는 %s의 「%s」(%s)예요." % (
            len(found), _where(L0, by_letter.get(L0)), idx0.d_name[d0], fmt_bytes(size0))
        if target is not None:
            desc += " 여유가 많은 드라이브로 옮겨 보세요 — %s %s 남음." % (
                _dname(target), fmt_bytes(target.get("free")))
        recs.append(_rec("old_project", "old_project", "오래 손대지 않은 폴더", desc,
                         total, len(found), "review",
                         _view(_one_or_all(t[1] for t in found), "folders", None,
                               OLD_PROJECT_DAYS)))

    # 7. drives running out of space
    for d in sorted(drives, key=lambda d: d.get("letter") or ""):
        total = int(d.get("total") or 0)
        free = int(d.get("free") or 0)
        if total <= 0 or free >= total * BALANCE_FREE_RATIO:
            continue
        L = d.get("letter")
        others = [o for o in drives if o.get("letter") != L and int(o.get("free") or 0) > free]
        pct = int(math.floor(free * 100.0 / total))
        need = int(total * BALANCE_FREE_RATIO) - free
        if others:
            best = max(others, key=lambda o: int(o.get("free") or 0))
            desc = "가장 여유가 많은 %s에 %s 남아 있어요. 큰 폴더를 옮겨 보세요." % (
                _dname(best), fmt_bytes(best.get("free")))
        else:
            desc = "다른 드라이브에도 여유가 많지 않아요. 지워도 되는 항목부터 정리해 보세요."
        recs.append(_rec("balance-%s" % L, "balance", "%s 여유 공간이 %d%%예요" % (_dname(d), pct),
                         desc, need, 0, "info", _view(L, "folders", None, 0)))

    recs.sort(key=lambda r: (0 if r["tone"] == "safe" else 1, -r["bytes"], r["id"]))
    return recs


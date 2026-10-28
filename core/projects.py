"""프로젝트 보기 (SPEC-2 B.2·B.3·B.5): 인덱스에서 프로젝트 폴더를 찾고 묶는다.

detect_projects(idx, settings, classifier=None) -> [project]   인덱스만 본다(디스크 재방문 없음)
group_projects(projects, group=..., ...)          -> /api/projects 응답 본문
project_videos(path, limit=200, ...)              -> 그 폴더 하나만 디스크에서 다시 훑은 영상 목록
"""

import os
import re
import time
import unicodedata
from collections import Counter

from . import brc
from . import settings as settings_mod
from .categories import APP, CACHE, NCAT, VIDEO_EXT, Classifier, strip_long_prefix
from .model import (VID_PRIO, best_video, norm_drive_key, path_of_dir, vid_better)

UNSORTED = "미분류"
MIN_PROJECT_BYTES = 1 << 20
VIDEO_LIST_MAX_ENTRIES = 20000
_SKIP_CTX = (APP, CACHE)               # 시스템·캐시 문맥 아래는 프로젝트가 아니다
_LEAD_YEAR = re.compile(r"(?:19|20)[0-9]{2}(?=[_\s])[_\s]+")


# 프로젝트 안의 「도구 폴더」 이름(aep·AE·PR·프로젝트 …) — 한 단계 아래 aep 판정에서 쓴다
_TOOL_DIR = re.compile(r"(?i)^(?:aepx?|ae|after[ _]?effects?|pr|premiere(?:[ _]?pro)?|prproj|"
                       r"projects?|project[ _]?files?|프로젝트(?:[ _]?파일)?|davinci|resolve|drp|"
                       r"edit(?:ing)?|편집|작업[ _]?파일|src|sources?|소스|원본)$")


def _aep_below(kids, aep, names):
    """규칙 3 의 「한 단계 아래 aep」. ★모음 폴더(D:\\Projects 처럼 프로젝트 폴더를 모아 둔 곳)는 아니다:
    aep 가 있는 자식이 날짜 이름(그 자체로 프로젝트)이거나, 도구 폴더가 아닌 자식 둘 이상에 흩어져 있으면
    부모를 프로젝트로 삼지 않고 안으로 내려간다 — 그러지 않으면 모음 폴더 하나가 프로젝트 전부를 삼킨다."""
    hits = [g for g in kids or () if g in aep]
    if not hits:
        return False
    if any(brc.parse_date(names[g]) is not None for g in hits):
        return False
    return sum(1 for g in hits if not _TOOL_DIR.match(names[g])) < 2


def _dids_of(idx, paths):
    from .model import find_dir
    out = set()
    for p in paths or ():
        try:
            d = find_dir(idx, p)
        except Exception:      # noqa: BLE001 — a malformed path just does not match
            d = None
        if d is not None and d > 0 and idx.d_alive[d]:
            out.add(d)
    return out


class _Aliases:
    """client_aliases 적용: 대소문자·공백·전각 무시, 별칭 사슬은 몇 단계까지 따라간다."""

    def __init__(self, aliases):
        self.map = {}
        self.canon = {}
        for a, c in (aliases or {}).items():
            self.map[settings_mod.client_key(a)] = c
        for c in (aliases or {}).values():
            self.canon.setdefault(settings_mod.client_key(c), c)

    def resolve(self, raw):
        k = settings_mod.client_key(raw)
        if not k:
            return None
        name = None
        for _ in range(8):
            c = self.map.get(k)
            if c is None:
                break
            name = c
            k2 = settings_mod.client_key(c)
            if k2 == k:
                break
            k = k2
        if name is None:
            name = self.canon.get(k)
        return name


def _brc_name(name):
    """BRC 파서에 넘길 이름: 앞 날짜·연도 토큰을 떼고, '_' 가 없으면 공백을 구분자로 본다."""
    ymd, rest = brc.split_date(name)
    if ymd is None:
        m = _LEAD_YEAR.match(rest)
        if m and m.end() < len(rest):
            rest = rest[m.end():]
    rest = rest.strip()
    if "_" not in rest and " " in rest:
        rest = "_".join(rest.split())
    return ymd, rest


def _date_fields(ymd, ts):
    if ymd is not None:
        y, m, d = ymd
        ds = "%04d-%02d-%02d" % (y, m, d) if d else "%04d-%02d" % (y, m)
        return ds, "name", y, m
    lt = time.localtime(ts if ts and ts > 0 else 0)
    return "%04d-%02d-%02d" % (lt.tm_year, lt.tm_mon, lt.tm_mday), "mtime", lt.tm_year, lt.tm_mon


def _subtree_media(idx, did):
    """(대표 영상 (dir, (name,size,mtime,cat)) | None, 알려진 영상 수) — 한 번의 서브트리 순회."""
    dv = idx.d_vid
    ch = idx.d_children
    fids = idx.d_fids
    fa = idx.f_alive
    fn = idx.f_name
    fc = idx.f_cat
    nc = os.path.normcase
    best = None
    bd = -1
    n = 0
    stack = [did]
    while stack:
        d = stack.pop()
        v = dv.get(d)
        vn = None
        if v is not None:
            n += 1
            vn = nc(v[0])
            if vid_better(v, best):
                best, bd = v, d
        for f in fids[d] or ():
            if fa[f] and fc[f] in VID_PRIO:
                nm = fn[f]
                dot = nm.rfind(".")
                if dot > 0 and nm[dot + 1:].lower() in VIDEO_EXT and nc(nm) != vn:
                    n += 1
        k = ch[d]
        if k:
            stack.extend(k)
    return ((bd, best) if best is not None else None), n


def _record(idx, did, letter, parser, aliases, workers):
    name = idx.d_name[did]
    ymd, bname = _brc_name(name)
    p = parser.parse(bname)
    client_raw = p["client"]
    client = aliases.resolve(client_raw) if client_raw else None
    if not client:
        client = client_raw or UNSORTED
    title = " ".join(x for x in (p["campaign"], p["task"].replace("_", " ")) if x).strip()
    if not title:
        title = bname.replace("_", " ").strip() or name
    worker = p["worker"]
    wkey = worker.upper()
    newest = idx.d_newest[did] or idx.d_mtime[did]
    date, src, year, month = _date_fields(ymd, newest)
    bv, nvid = _subtree_media(idx, did)
    preview = None
    path = path_of_dir(idx, did)
    if bv is not None:
        vd, (vn, vs, vm, vc) = bv
        preview = {"path": os.path.join(path_of_dir(idx, vd), vn), "name": vn, "size": vs,
                   "mtime": vm, "cat": vc}
    b = did * NCAT
    return {"key": "%s:%d" % (letter, did), "drive": letter, "did": did, "path": path,
            "name": name, "date": date, "date_source": src, "year": year, "month": month,
            "client": client, "client_raw": client_raw, "title": title,
            "worker": wkey if wkey in workers else worker, "worker_name": workers.get(wkey, ""),
            "job_kind": p["jobKind"], "size": idx.d_size[did],
            "cat_bytes": list(idx.d_cat[b:b + NCAT]), "files": idx.d_files[did],
            "newest": newest, "preview": preview, "videos": nvid}


def detect_projects(idx, settings=None, classifier=None):
    """인덱스 하나의 프로젝트 레코드 목록 (SPEC-2 B.2). 맨 위 프로젝트가 이긴다."""
    s = settings_mod.validate(settings or {})
    cls = classifier if classifier is not None else Classifier(s)
    if not idx.d_name or not idx.d_alive[0]:
        return []
    letter = norm_drive_key(idx.letter)
    workers = s["workers"]
    parser = brc.parser(sorted(workers))
    aliases = _Aliases(s["client_aliases"])
    roots = _dids_of(idx, s["project_roots"])
    excluded = _dids_of(idx, s["project_excluded"])
    aep = idx.d_aep
    ch = idx.d_children
    names = idx.d_name
    size = idx.d_size
    dir_ctx = cls.dir_ctx
    needs_paths = getattr(cls, "needs_paths", True)
    out = []
    root_ctx = cls.root_ctx(idx.root) if hasattr(cls, "root_ctx") else (None, 0, 0)
    if root_ctx[0] in _SKIP_CTX:
        return []
    # (did, ctx, depth)
    stack = [(0, root_ctx, 0)]
    while stack:
        d, ctx, depth = stack.pop()
        kids = ch[d]
        if not kids:
            continue
        in_root = d in roots
        for c in kids:
            if size[c] < MIN_PROJECT_BYTES:
                continue
            nm = names[c]
            cctx = dir_ctx(ctx, nm, path_of_dir(idx, c) if needs_paths else None, depth + 1)
            if cctx[0] in _SKIP_CTX:
                continue
            is_proj = False
            if c not in excluded and c not in roots:
                if in_root or brc.parse_date(nm) is not None or c in aep:
                    is_proj = True
                elif aep and _aep_below(ch[c], aep, names):
                    is_proj = True
            if is_proj:
                out.append(_record(idx, c, letter, parser, aliases, workers))
            else:
                stack.append((c, cctx, depth + 1))
    _unify_spelling(out)
    return out


def _unify_spelling(projects):
    """같은 광고주(대소문자·공백·전각만 다른 것)는 가장 많이 쓴 표기 하나로."""
    by = {}
    for p in projects:
        by.setdefault(settings_mod.client_key(p["client"]), Counter())[p["client"]] += 1
    pick = {k: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for k, c in by.items()}
    for p in projects:
        p["client"] = pick[settings_mod.client_key(p["client"])]


def unify_clients(projects):
    """여러 드라이브의 detect_projects 결과를 합친 뒤 광고주 표기를 다시 맞출 때."""
    _unify_spelling(projects)
    return projects


# ---------------------------------------------------------------- 묶기 (B.5)

def _fold(s):
    return unicodedata.normalize("NFKC", s or "").casefold()


def _sort_key(sort):
    if sort == "size":
        return lambda p: (-p["size"], _fold(p["name"]))
    if sort == "name":
        return lambda p: (_fold(p["title"] or p["name"]), _fold(p["name"]), p["key"])
    return None   # recent: handled by _sorted


def _sorted(lst, sort):
    if sort in ("size", "name"):
        return sorted(lst, key=_sort_key(sort))
    return sorted(lst, key=lambda p: (p["date"], p["newest"], p["key"]), reverse=True)


def _sum_cats(lst):
    tot = [0] * NCAT
    for p in lst:
        for k, v in enumerate(p["cat_bytes"]):
            tot[k] += v
    return tot


def _group(key, title, lst, sort):
    return {"key": key, "title": title, "subtitle": "프로젝트 %d개" % len(lst),
            "bytes": sum(p["size"] for p in lst), "count": len(lst), "cat_bytes": _sum_cats(lst),
            "projects": _sorted(lst, sort)}


def _matches(p, q):
    if not q:
        return True
    hay = _fold(" ".join((p["name"], p["title"], p["client"], p["client_raw"], p["worker"],
                          p["worker_name"], p["date"])))
    return all(t in hay for t in q.split())


def group_projects(projects, group="client", q="", worker="", client="", year=None,
                   sort="recent", drive=None):
    """/api/projects 응답 본문 {ok,total,total_bytes,groups,clients,workers,years}.

    clients·workers·years(필터 칩용)는 검색어·드라이브만 적용한 전체에서 센다.
    """
    if group not in ("client", "time", "month"):
        group = "client"
    if sort not in ("recent", "size", "name"):
        sort = "recent"
    qf = _fold(q).strip()
    base = [p for p in projects
            if (not drive or drive == "*" or norm_drive_key(drive) == p["drive"])
            and _matches(p, qf)]
    # 필터 칩
    cl = {}
    for p in base:
        e = cl.setdefault(settings_mod.client_key(p["client"]),
                          {"name": p["client"], "count": 0, "bytes": 0})
        e["count"] += 1
        e["bytes"] += p["size"]
    clients = sorted(cl.values(), key=lambda e: (e["name"] == UNSORTED, -e["bytes"],
                                                 _fold(e["name"])))
    wk = {}
    for p in base:
        if p["worker"]:
            e = wk.setdefault(p["worker"].upper(), {"initial": p["worker"].upper(),
                                                    "name": p["worker_name"], "count": 0})
            e["count"] += 1
            if p["worker_name"] and not e["name"]:
                e["name"] = p["worker_name"]
    workers = sorted(wk.values(), key=lambda e: (-e["count"], e["initial"]))
    yr = {}
    for p in base:
        months = yr.setdefault(p["year"], [{"month": m, "bytes": 0, "count": 0}
                                           for m in range(1, 13)])
        months[p["month"] - 1]["bytes"] += p["size"]
        months[p["month"] - 1]["count"] += 1
    years = [{"year": y, "months": yr[y]} for y in sorted(yr, reverse=True)]
    # 필터
    sel = base
    if worker:
        w = str(worker).strip().upper()
        sel = [p for p in sel if p["worker"].upper() == w]
    if client:
        ck = settings_mod.client_key(str(client))
        sel = [p for p in sel if settings_mod.client_key(p["client"]) == ck]
    try:
        yv = int(year) if year not in (None, "", "*") else None
    except (TypeError, ValueError):
        yv = None
    if group == "month" and yv is None and years:
        yv = years[0]["year"]
    if yv is not None:
        sel = [p for p in sel if p["year"] == yv]
    groups = []
    if group == "client":
        by = {}
        for p in sel:
            by.setdefault(settings_mod.client_key(p["client"]), []).append(p)
        for k, lst in by.items():
            groups.append(_group(k or UNSORTED, lst[0]["client"], lst, sort))
        groups.sort(key=lambda g: (g["title"] == UNSORTED, -g["bytes"], _fold(g["title"])))
    else:
        by = {}
        for p in sel:
            by.setdefault((p["year"], p["month"]), []).append(p)
        for (y, m) in sorted(by, reverse=True):
            groups.append(_group("%04d-%02d" % (y, m), "%d년 %d월" % (y, m), by[(y, m)], sort))
    out = {"ok": True, "total": len(sel), "total_bytes": sum(p["size"] for p in sel),
           "groups": groups, "clients": clients, "workers": workers, "years": years}
    if group == "month":
        out["year"] = yv
    return out


# ---------------------------------------------------------------- 상세: 영상 목록

def project_videos(path, limit=200, settings=None, root=None, classifier=None):
    """프로젝트 폴더 하나를 디스크에서 다시 훑어 영상 목록 [{path,name,size,mtime,cat}].

    캐시·프록시·시스템 문맥은 뺀다. 순서 = render > video > source > archive, 다음 mtime 최신.
    항목 20000개를 넘게 보면 거기서 멈춘다(부분 목록). 링크·재분석 지점은 따라가지 않는다.
    """
    cls = classifier if classifier is not None else Classifier(settings_mod.validate(settings or {}))
    top = os.path.normpath(strip_long_prefix(path))
    try:
        if root:
            ctx, depth = cls.ctx_for(top, root)
        else:
            ctx, depth = cls.root_ctx(top), 1
    except ValueError:
        ctx, depth = cls.root_ctx(top), 1
    depth = max(1, depth)
    from .categories import classify
    from .scanner import _is_reparse, IS_NT
    from .model import _scan_path
    found = []
    seen = 0
    stack = [(top, ctx, depth)]
    while stack and seen < VIDEO_LIST_MAX_ENTRIES:
        d, dctx, dep = stack.pop()
        if dctx[0] in _SKIP_CTX:
            continue
        try:
            it = os.scandir(_scan_path(d) if IS_NT else d)
        except OSError:
            continue
        with it:
            for e in it:
                seen += 1
                if seen > VIDEO_LIST_MAX_ENTRIES:
                    break
                nm = e.name
                try:
                    if e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        if IS_NT and _is_reparse(st):
                            continue
                        cp = os.path.join(d, nm)
                        stack.append((cp, cls.dir_ctx(dctx, nm, cp, dep + 1), dep + 1))
                        continue
                    dot = nm.rfind(".")
                    if dot <= 0 or nm[dot + 1:].lower() not in VIDEO_EXT:
                        continue
                    c = classify(nm, dctx, dep)
                    if c not in VID_PRIO:
                        continue
                    st = e.stat(follow_symlinks=False)
                    if IS_NT and _is_reparse(st):
                        continue
                except OSError:
                    continue
                found.append({"path": os.path.join(d, nm), "name": nm, "size": st.st_size,
                              "mtime": st.st_mtime, "cat": c})
    found.sort(key=lambda v: (VID_PRIO[v["cat"]], -v["mtime"], v["name"]))
    return found[:max(0, int(limit))]


def preview_of(idx, did):
    """프로젝트(또는 아무 폴더)의 대표 영상 {path,name,size,mtime,cat} | None."""
    bv = best_video(idx, did)
    if bv is None:
        return None
    vd, (vn, vs, vm, vc) = bv
    return {"path": os.path.join(path_of_dir(idx, vd), vn), "name": vn, "size": vs,
            "mtime": vm, "cat": vc}

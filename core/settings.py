"""User settings (settings.json in the data dir). Validation + clamping + atomic write."""

import copy
import json
import os
import threading
import unicodedata

from .categories import cat_id
from .store import atomic_write, data_dir

DEFAULTS = {
    "big_file_mb": 50,
    "verify": "quick",
    "old_days": 180,
    "excluded": [],
    "cache_paths": [],
    "rules": [],
    # SPEC-2 (v2)
    "workers": {},              # {"KMS": "이름"} — 저장소 기본값에 실명을 넣지 않는다
    "client_aliases": {},       # {"한결": "한결증권"}
    "project_roots": [],
    "project_excluded": [],
    "preview_cache_gb": 2,
    "drive_scan": {},           # {"D": true, "N-3fa9c1": false}
    "network_locations": [],    # [{"path": "\\\\ORICO\\영상백업", "label": "..."}]
    "update_check": True,
}
VERIFY_LEVELS = ("size", "quick", "full")
BIG_FILE_MB_RANGE = (1, 102400)
OLD_DAYS_RANGE = (1, 3650)
MAX_PATHS = 500
MAX_RULES = 1000
MAX_PATH_LEN = 4096
PREVIEW_CACHE_GB_RANGE = (0.5, 50.0)
MAX_WORKERS = 300
MAX_ALIASES = 3000
MAX_NAME_LEN = 100
MAX_DRIVE_KEYS = 200
MAX_NETWORK = 64
TEAM_FORMAT = "driveboard-team"
TEAM_VERSION = 1

_lock = threading.RLock()


def defaults():
    return copy.deepcopy(DEFAULTS)


def settings_path():
    return os.path.join(data_dir(), "settings.json")


def _int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v == v and abs(v) < 1e15:
        return int(v)
    if isinstance(v, str):
        try:
            return int(float(v.strip()))
        except ValueError:
            return None
    return None


def _clamp(v, lo_hi):
    lo, hi = lo_hi
    return max(lo, min(hi, v))


def _path_key(p):
    return os.path.normcase(os.path.normpath(p))


def _paths(v):
    if not isinstance(v, (list, tuple)):
        return None
    out, seen = [], set()
    for p in v:
        if not isinstance(p, str):
            continue
        p = p.strip()
        if not p or len(p) > MAX_PATH_LEN or "\0" in p:
            continue
        p = os.path.normpath(p)
        k = _path_key(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
        if len(out) >= MAX_PATHS:
            break
    return out


def _rules(v):
    if not isinstance(v, (list, tuple)):
        return None
    by_key = {}
    order = []
    for r in v:
        if not isinstance(r, dict):
            continue
        p = r.get("path")
        c = cat_id(r.get("cat"))
        if not isinstance(p, str) or c is None:
            continue
        p = p.strip()
        if not p or len(p) > MAX_PATH_LEN or "\0" in p:
            continue
        p = os.path.normpath(p)
        k = _path_key(p)
        if k in by_key:
            order.remove(k)
        by_key[k] = {"path": p, "cat": c}
        order.append(k)
    out = [by_key[k] for k in order]
    return out[-MAX_RULES:]


def _float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v.strip())
        except ValueError:
            return None
    else:
        return None
    return f if f == f and abs(f) < 1e15 else None


def _name(v, maxlen=MAX_NAME_LEN):
    """앞뒤 공백을 걷은 표시용 이름 (제어문자 제거). 빈 값·너무 긴 값은 None."""
    if not isinstance(v, str):
        return None
    v = "".join(ch for ch in v if ch >= " " and ch != "\x7f").strip()
    if not v or len(v) > maxlen:
        return None
    return v


def norm_initial(v):
    n = _name(v, 16)
    return n.upper() if n else None


def _workers(v):
    if not isinstance(v, dict):
        return None
    out = {}
    for k, name in v.items():
        k = norm_initial(k)
        name = _name(name)
        if k and name and (k in out or len(out) < MAX_WORKERS):
            out[k] = name
    return out


def client_key(name):
    """광고주 비교 키: 전각→반각(NFKC), 대소문자·공백 무시."""
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFKC", name)
    return "".join(n.split()).casefold()


def _aliases(v):
    if not isinstance(v, dict):
        return None
    out = {}
    for a, c in v.items():
        a = _name(a)
        c = _name(c)
        if not a or not c or client_key(a) == client_key(c):
            continue
        if a in out or len(out) < MAX_ALIASES:
            out[a] = c
    return out


def _drive_scan(v):
    if not isinstance(v, dict):
        return None
    from .model import norm_drive_key
    out = {}
    for k, on in v.items():
        if not isinstance(k, str) or not isinstance(on, bool):
            continue
        k = norm_drive_key(k)
        if not k or len(k) > 16 or (len(out) >= MAX_DRIVE_KEYS and k not in out):
            continue
        out[k] = on
    return out


def norm_unc(p):
    """UNC 경로 정규화 ('\\\\srv\\share\\a', 끝 '\\' 없음) 또는 None — winapi.normalize_unc 와 같다."""
    from .winapi import normalize_unc
    if isinstance(p, str) and (len(p) > MAX_PATH_LEN or "\0" in p):
        return None
    return normalize_unc(p)


def unc_root(p):
    """UNC 경로의 공유 루트 '\\\\server\\share' (정규화 실패면 None)."""
    n = norm_unc(p)
    if n is None:
        return None
    parts = n[2:].split("\\")
    return "\\\\" + parts[0] + "\\" + parts[1]


def network_key(p):
    """등록 네트워크 위치의 드라이브 키 — winapi.network_key 와 같다 (sha1(소문자 정규화 경로)[:6])."""
    from .winapi import network_key as _nk
    return _nk(p) if norm_unc(p) else None


def _network(v):
    if not isinstance(v, (list, tuple)):
        return None
    out, seen = [], set()
    for e in v:
        if isinstance(e, str):
            e = {"path": e}
        if not isinstance(e, dict):
            continue
        p = norm_unc(e.get("path"))
        if p is None:
            continue
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        label = _name(e.get("label"), 64) or p[2:].split("\\")[-1]
        out.append({"path": p, "label": label})
        if len(out) >= MAX_NETWORK:
            break
    return out


def validate(data, base=None):
    """Full settings dict: `data` merged over `base` (defaults when None).

    Unknown keys are dropped; values of the wrong type keep the base value; numbers are
    clamped to their allowed range.
    """
    out = copy.deepcopy(base) if base is not None else defaults()
    for k, v in DEFAULTS.items():
        if k not in out:
            out[k] = copy.deepcopy(v)
    if not isinstance(data, dict):
        return out
    if "big_file_mb" in data:
        n = _int(data["big_file_mb"])
        if n is not None:
            out["big_file_mb"] = _clamp(n, BIG_FILE_MB_RANGE)
    if "old_days" in data:
        n = _int(data["old_days"])
        if n is not None:
            out["old_days"] = _clamp(n, OLD_DAYS_RANGE)
    if "verify" in data:
        v = data["verify"]
        if isinstance(v, str) and v.strip().lower() in VERIFY_LEVELS:
            out["verify"] = v.strip().lower()
    for k in ("excluded", "cache_paths"):
        if k in data:
            p = _paths(data[k])
            if p is not None:
                out[k] = p
    if "rules" in data:
        r = _rules(data["rules"])
        if r is not None:
            out["rules"] = r
    for k in ("project_roots", "project_excluded"):
        if k in data:
            p = _paths(data[k])
            if p is not None:
                out[k] = p
    if "preview_cache_gb" in data:
        f = _float(data["preview_cache_gb"])
        if f is not None:
            f = max(PREVIEW_CACHE_GB_RANGE[0], min(PREVIEW_CACHE_GB_RANGE[1], f))
            out["preview_cache_gb"] = int(f) if f == int(f) else round(f, 2)
    for k, fn in (("workers", _workers), ("client_aliases", _aliases),
                  ("drive_scan", _drive_scan), ("network_locations", _network)):
        if k in data:
            r = fn(data[k])
            if r is not None:
                out[k] = r
    if "update_check" in data and isinstance(data["update_check"], bool):
        out["update_check"] = data["update_check"]
    return out


def load():
    """Current settings (defaults when missing or unreadable)."""
    with _lock:
        try:
            with open(settings_path(), "rb") as f:
                raw = json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            raw = {}
        return validate(raw)


def save(partial):
    """Merge `partial` into the stored settings, write atomically, return the full dict."""
    with _lock:
        full = validate(partial, load())
        try:
            data = json.dumps(full, ensure_ascii=False, indent=1).encode("utf-8")
        except UnicodeEncodeError:   # lone surrogates in a path: keep it escaped
            data = json.dumps(full, ensure_ascii=True, indent=1).encode("ascii")
        atomic_write(settings_path(), data)
        return full


def big_file_bytes(settings):
    n = _int((settings or {}).get("big_file_mb"))
    if n is None:
        n = DEFAULTS["big_file_mb"]
    return _clamp(n, BIG_FILE_MB_RANGE) << 20


def preview_cache_bytes(settings):
    f = _float((settings or {}).get("preview_cache_gb"))
    if f is None:
        f = DEFAULTS["preview_cache_gb"]
    f = max(PREVIEW_CACHE_GB_RANGE[0], min(PREVIEW_CACHE_GB_RANGE[1], f))
    return int(f * (1 << 30))


# ---------------------------------------------------------------- 팀 설정 (SPEC-2 D)

def export_team(settings):
    """광고주 별칭·작업자·분류 규칙을 JSON 하나로 (개인 경로 목록·드라이브 설정은 넣지 않는다)."""
    s = validate(settings or {})
    return {"format": TEAM_FORMAT, "version": TEAM_VERSION,
            "workers": dict(s["workers"]), "client_aliases": dict(s["client_aliases"]),
            "rules": [dict(r) for r in s["rules"]]}


def _dict_diff(old, new_items):
    added, changed, same = {}, {}, 0
    for k, v in new_items.items():
        if k not in old:
            added[k] = v
        elif old[k] != v:
            changed[k] = [old[k], v]
        else:
            same += 1
    return {"added": added, "changed": changed, "same": same}


def import_team(settings, data):
    """(new_settings, preview_diff). 저장은 하지 않는다 — 미리보기를 보여 준 뒤 호출자가 save().

    가져온 값은 추가·덮어쓰기만 하고 기존 항목을 지우지 않는다. 분류 규칙은 경로라서 이 PC 에
    그 경로가 없을 수 있다(드라이브 문자가 다른 PC) → diff 의 exists 로 알린다.
    형식이 틀리면 ValueError.
    """
    if not isinstance(data, dict) or data.get("format") != TEAM_FORMAT:
        raise ValueError("팀 설정 파일 형식이 아니에요")
    try:
        ver = int(data.get("version") or 0)
    except (TypeError, ValueError):
        ver = 0
    if ver < 1 or ver > TEAM_VERSION:
        raise ValueError("지원하지 않는 팀 설정 버전이에요")
    cur = validate(settings or {})
    w = _workers(data.get("workers") or {}) or {}
    a = _aliases(data.get("client_aliases") or {}) or {}
    r = _rules(data.get("rules") or []) or []
    diff = {"workers": _dict_diff(cur["workers"], w),
            "client_aliases": _dict_diff(cur["client_aliases"], a)}
    old_rules = {_path_key(x["path"]): x for x in cur["rules"]}
    radd, rchg, rsame = [], [], 0
    for x in r:
        e = {"path": x["path"], "cat": x["cat"], "exists": os.path.exists(x["path"])}
        o = old_rules.get(_path_key(x["path"]))
        if o is None:
            radd.append(e)
        elif o["cat"] != x["cat"]:
            e["old_cat"] = o["cat"]
            rchg.append(e)
        else:
            rsame += 1
    diff["rules"] = {"added": radd, "changed": rchg, "same": rsame,
                     "missing_paths": sum(1 for e in radd + rchg if not e["exists"])}
    merged = {"workers": dict(cur["workers"], **w),
              "client_aliases": dict(cur["client_aliases"], **a),
              "rules": cur["rules"] + r}
    return validate(merged, cur), diff

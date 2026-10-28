"""영상팀 파일명 규칙(BRC) 파서 — tests/brc_reference.js 의 충실한 파이썬 이식 (SPEC-2 B.1).

규칙 순서: 날짜_광고주_캠페인_페이지p_작업내용_해상도_작업자_@작업종류_작업구분
tests/test_brc.py 가 node 로 참조 구현을 돌려 같은 답을 내는지 대조한다.

이식 메모(JS 정규식과 같게 맞춘 것):
  * JS `\\d` = [0-9] 만, JS `$`(m 플래그 없음) = 문자열 끝 → 파이썬은 `\\Z`(`$` 는 끝 개행 앞도 맞는다).
  * JS `\\s` 는 파이썬 `\\s` 와 범위가 달라(\\x1c-\\x1f, \\ufeff) 문자 집합을 직접 적었다.
날짜 헬퍼 parse_date() 는 이식이 아니라 SPEC-2 B.1 의 확장 규칙이다.
"""

import datetime as _dt
import re

RES_LIST = ("1920x1080", "1080x1080", "1080x1920", "1080x1350",
            "1200x628", "1200x1200", "1200x1500",
            "1440x1440", "1440x1800", "3840x2160", "2160x3840")
JOBKIND_CODES = ("@MB", "@BC", "@SZ", "@ED", "@AV", "@AB", "@MV", "@SH", "@CP")
WORKER_LIST = ("KMS", "HYC", "JJH", "PNJ", "KBH", "GJH")

_JS_WS = "\t\n\x0b\x0c\r    -     　﻿"
_STRIP_NUM = re.compile("[" + _JS_WS + "]+[0-9]+\\Z")
_DATE6 = re.compile(r"[0-9]{6}\Z")
_RES = re.compile(r"[0-9]{3,4}[xX][0-9]{3,4}\Z")
_RETUCH = re.compile(r"retuch([0-9]+)\Z")
_PAGE = re.compile(r"[0-9]+p\Z")
_DIGITS = re.compile(r"[0-9]+\Z")
_UPPER = re.compile(r"[A-Z]{2,5}\Z")


def _s(v):
    return "" if v is None else str(v)


def strip_num(name):
    return _STRIP_NUM.sub("", _s(name), count=1)


def is_date(p):
    return _DATE6.match(_s(p)) is not None


def is_res(p):
    p = _s(p)
    return _RES.match(p) is not None or p in RES_LIST


def is_work_type(p):
    p = _s(p)
    return p in ("xian", "vari", "sf") or _RETUCH.match(p) is not None


def is_job_kind(p):
    return _s(p) in JOBKIND_CODES


def is_page(p):
    return _PAGE.match(_s(p)) is not None


def is_special(p):
    return is_date(p) or is_res(p) or is_work_type(p) or is_job_kind(p) or is_page(p)


class Parser:
    """작업자 이니셜 목록 = 참조 구현 기본값 + extra_workers (설정의 작업자)."""

    def __init__(self, extra_workers=()):
        w = list(WORKER_LIST)
        for x in extra_workers or ():
            x = _s(x)
            if x and x not in w:
                w.append(x)
        self.workers = tuple(w)
        self._wset = frozenset(w)

    def is_worker_initial(self, p):
        return _s(p).upper() in self._wset

    @staticmethod
    def smart_split(name):
        raw = strip_num(name).split("_")
        merged = []
        i = 0
        n = len(raw)
        while i < n:
            p = raw[i]
            if i + 1 < n and _DIGITS.match(raw[i + 1]) and not is_special(p):
                merged.append(p + "_" + raw[i + 1])
                i += 2
            else:
                merged.append(p)
                i += 1
        return merged

    @staticmethod
    def tail_idx(parts):
        for t, p in enumerate(parts):
            if is_job_kind(p) or is_work_type(p):
                return t
        return len(parts)

    def find_worker_idx(self, parts):
        # 1순위: @작업종류 바로 앞의 대문자
        for i in range(1, len(parts)):
            if is_job_kind(parts[i]) and _UPPER.match(parts[i - 1]):
                return i - 1
        g_idx = [g for g, p in enumerate(parts) if p and not is_special(p)]
        # 2순위: 팀원 이니셜 목록. 0번은 광고주 자리라 제외.
        for q in range(len(g_idx) - 1, 0, -1):
            if self.is_worker_initial(parts[g_idx[q]]):
                return g_idx[q]
        # 3순위: 일반 토큰이 3개 이상일 때만 마지막 대문자 토큰
        if len(g_idx) >= 3:
            last_g = g_idx[-1]
            if _UPPER.match(parts[last_g]):
                return last_g
        return -1

    # -- apply* (이름 고치기) — 참조 구현과 같은 결과
    @staticmethod
    def apply_page(name, v):
        parts = strip_num(name).split("_")
        token = _s(v) + "p"
        for i, p in enumerate(parts):
            if is_page(p):
                parts[i] = token
                return "_".join(parts)
        generic = 0
        for j, p in enumerate(parts):
            if not is_special(p):
                generic += 1
                if generic == 2:
                    parts.insert(j + 1, token)
                    return "_".join(parts)
        parts.append(token)
        return "_".join(parts)

    def apply_worker(self, name, v):
        parts = strip_num(name).split("_")
        at = self.find_worker_idx(parts)
        if at >= 0:
            parts[at] = _s(v)
            return "_".join(parts)
        parts.insert(self.tail_idx(parts), _s(v))
        return "_".join(parts)

    @staticmethod
    def apply_job_kind(name, v):
        parts = strip_num(name).split("_")
        for i, p in enumerate(parts):
            if is_job_kind(p):
                parts[i] = _s(v)
                return "_".join(parts)
        idx = len(parts) - 1 if (parts and is_work_type(parts[-1])) else len(parts)
        parts.insert(idx, _s(v))
        return "_".join(parts)

    @staticmethod
    def apply_work_type(name, v):
        parts = strip_num(name).split("_")
        v = _s(v)
        is_retuch = _RETUCH.match(v) is not None
        for i, p in enumerate(parts):
            if is_work_type(p):
                m = _RETUCH.match(p)
                if is_retuch and m:
                    parts[i] = "retuch" + str(int(m.group(1)) + 1)
                else:
                    parts[i] = v
                return "_".join(parts)
        parts.append(v)
        return "_".join(parts)

    def parse(self, name):
        result = {"client": "", "campaign": "", "page": "", "task": "", "resolution": "",
                  "worker": "", "jobKind": "", "workType": ""}
        if not name:
            return result
        parts = self.smart_split(name)
        generic = []
        worker_idx = self.find_worker_idx(parts)
        for j, p in enumerate(parts):
            if not p:
                continue
            if is_date(p):
                continue
            if is_res(p):
                result["resolution"] = p.replace("X", "x")
                continue
            if is_work_type(p):
                result["workType"] = "retuch1" if _RETUCH.match(p) else p
                continue
            if is_job_kind(p):
                result["jobKind"] = p
                continue
            if is_page(p):
                result["page"] = p[:-1]
                continue
            if j == worker_idx:
                result["worker"] = p
                continue
            generic.append(p)
        if generic:
            result["client"] = generic[0]
        if len(generic) > 1:
            result["campaign"] = generic[1]
        if len(generic) > 2:
            result["task"] = "_".join(generic[2:])
        return result


_DEFAULT = Parser()
_cache = {}


def parser(extra_workers=()):
    key = tuple(_s(x) for x in (extra_workers or ()))
    if not key:
        return _DEFAULT
    p = _cache.get(key)
    if p is None:
        if len(_cache) > 32:
            _cache.clear()
        p = _cache[key] = Parser(key)
    return p


def parse(name, extra_workers=()):
    """-> {client, campaign, page, task, resolution, worker, jobKind, workType} (참조 구현과 같다)."""
    return parser(extra_workers).parse(name)


# ---------------------------------------------------------------- 날짜 (SPEC-2 B.1)

_SEP_END = r"(?![0-9A-Za-z])"
_D_YMD = re.compile(r"((?:19|20)[0-9]{2})([-._])([0-9]{1,2})\2([0-9]{1,2})" + _SEP_END)
_D_8 = re.compile(r"((?:19|20)[0-9]{2})([0-9]{2})([0-9]{2})" + _SEP_END)
_D_6 = re.compile(r"([0-9]{2})([0-9]{2})([0-9]{2})" + _SEP_END)
_D_YM = re.compile(r"((?:19|20)[0-9]{2})[-_]([0-9]{1,2})" + _SEP_END)


def _valid(y, m, d):
    try:
        _dt.date(y, m, d)
        return True
    except ValueError:
        return False


def _match_date(s):
    """(year, month, day|None, 끝 위치) 또는 None. s 는 왼쪽 공백을 걷은 문자열."""
    if not s or not ("0" <= s[0] <= "9"):
        return None
    m = _D_YMD.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(3)), int(m.group(4))
        return (y, mo, d, m.end()) if _valid(y, mo, d) else None
    m = _D_8.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _valid(y, mo, d):
            return (y, mo, d, m.end())
    m = _D_6.match(s)
    if m:
        y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (y, mo, d, m.end()) if _valid(y, mo, d) else None
    m = _D_YM.match(s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return (y, mo, None, m.end()) if 1 <= mo <= 12 else None
    return None


def parse_date(name):
    """이름 맨 앞의 날짜 -> (year, month, day|None) 또는 None.

    YYYY-MM-DD · YYYY.MM.DD · YYYY_MM_DD · YYYYMMDD · YYMMDD(=20YY) · YYYY-MM · YYYY_MM.
    뒤에 숫자·영문이 바로 붙으면 날짜가 아니다. 없는 날짜(13월, 2월 30일)는 무시한다.
    """
    r = _match_date(_s(name).lstrip())
    return None if r is None else r[:3]


_LEAD_SEP = re.compile(r"[\s_\-.]+")


def split_date(name):
    """(parse_date 결과|None, 날짜 뒤 나머지 — 앞의 구분자 제거)."""
    s = _s(name).lstrip()
    r = _match_date(s)
    if r is None:
        return None, s
    rest = s[r[3]:]
    m = _LEAD_SEP.match(rest)
    if m:
        rest = rest[m.end():]
    return r[:3], rest


def starts_with_date(name):
    return parse_date(name) is not None

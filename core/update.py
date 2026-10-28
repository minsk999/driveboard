"""업데이트 알림 (SPEC-2 §E) — 시작 시 GitHub 최신 릴리스를 한 번 조회한다.

- 자동 설치는 하지 않는다. 결과는 헤더 알약 「새 버전 vX.Y.Z」 용 정보뿐이다.
- 어떤 실패도 예외로 올리지 않는다(네트워크·JSON·버전 형식 → None 또는 newer:False).
- /releases/latest 는 GitHub 가 prerelease·draft 를 건너뛴다. 응답에 그 표시가 있으면 한 번 더 거른다.
- 환경변수 DRIVEBOARD_NO_UPDATE_CHECK=1 → 조회하지 않는다(시험·CI 용).
  DRIVEBOARD_UPDATE_BASE=<url> → API 기준 주소 바꾸기(로컬 시험 서버용, 루프백만 http 허용).
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.parse
import urllib.request

from .version import REPO, RELEASES_URL, LATEST_RELEASE_URL, VERSION

API_BASE = "https://api.github.com"
MAX_BODY = 1 << 20          # 응답 상한 1MB
NOTES_MAX = 4000            # 릴리스 본문은 앞부분만
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

_VER_RE = re.compile(r"^[vV]?(\d{1,6})(?:\.(\d{1,6}))?(?:\.(\d{1,6}))?(?:-([0-9A-Za-z.\-]+))?(?:\+[0-9A-Za-z.\-]+)?$")


def parse_version(s):
    """'v1.2.3' / '1.2' / '1.2.3-rc.1' → 비교 키 튜플. 형식이 틀리면 None.

    키 = (major, minor, patch, is_release, prerelease_parts) — 정식 > 같은 번호의 prerelease.
    """
    if not isinstance(s, str):
        return None
    m = _VER_RE.match(s.strip())
    if not m:
        return None
    nums = tuple(int(g) if g is not None else 0 for g in m.group(1, 2, 3))
    pre = m.group(4)
    if pre is None:
        return nums + (1, ())
    parts = []
    for p in pre.split("."):
        if p == "":
            return None
        # semver: 숫자 식별자 < 문자 식별자
        parts.append((0, int(p), "") if p.isdigit() else (1, 0, p))
    return nums + (0, tuple(parts))


def is_newer(latest, current=VERSION):
    """latest 가 current 보다 새 버전인가. 어느 쪽이든 해석 불가면 False."""
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def _base_url(base_url):
    base = base_url or os.environ.get("DRIVEBOARD_UPDATE_BASE") or API_BASE
    u = urllib.parse.urlparse(base)
    host = (u.hostname or "").lower()
    if u.scheme == "https" and host:
        return base.rstrip("/"), host
    if u.scheme == "http" and host in _LOOPBACK:
        return base.rstrip("/"), host
    return None, None


def _safe_release_url(url):
    """브라우저로 열 주소 — 우리 저장소의 릴리스 페이지만 허용, 아니면 latest 페이지."""
    if isinstance(url, str):
        u = urllib.parse.urlparse(url)
        if (u.scheme == "https" and (u.hostname or "").lower() == "github.com"
                and u.path.startswith("/%s/releases/" % REPO)):
            return url
    return LATEST_RELEASE_URL


def check_latest(timeout=5, base_url=None, current=None):
    """{latest, url, notes, newer} 또는 None(조회 실패·꺼짐). 절대 예외를 올리지 않는다."""
    try:
        return _check(timeout, base_url, current or VERSION)
    except Exception:
        return None


def _check(timeout, base_url, current):
    if os.environ.get("DRIVEBOARD_NO_UPDATE_CHECK", "").strip() not in ("", "0"):
        return None
    base, host = _base_url(base_url)
    if base is None:
        return None
    url = "%s/repos/%s/releases/latest" % (base, REPO)
    req = urllib.request.Request(url, headers={
        "User-Agent": "DriveBoard/%s (+%s)" % (VERSION, RELEASES_URL),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    handlers = [urllib.request.ProxyHandler({})] if host in _LOOPBACK else []
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=timeout) as r:
        if getattr(r, "status", 200) != 200:
            return None
        raw = r.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return None
    latest = tag.strip()
    if latest[:1] in ("v", "V"):
        latest = latest[1:]
    notes = data.get("body")
    notes = notes[:NOTES_MAX] if isinstance(notes, str) else ""
    newer = is_newer(latest, current)
    if data.get("prerelease") is True or data.get("draft") is True:
        newer = False
    return {"latest": latest, "url": _safe_release_url(data.get("html_url")),
            "notes": notes, "newer": bool(newer)}


class Checker:
    """시작 시 한 번 백그라운드로 조회하고 결과를 들고 있는다(/api/state 용).

    c = Checker(enabled=settings['update_check']); c.start(); ... c.result → dict|None
    """

    def __init__(self, enabled=True, timeout=5, base_url=None, current=None):
        self.enabled = bool(enabled)
        self.timeout = timeout
        self.base_url = base_url
        self.current = current
        self._lock = threading.Lock()
        self._result = None
        self._done = threading.Event()
        self._started = False

    def start(self):
        with self._lock:
            if self._started:
                return self
            self._started = True
        if not self.enabled:
            self._done.set()
            return self
        t = threading.Thread(target=self._run, name="update-check", daemon=True)
        t.start()
        return self

    def _run(self):
        try:
            res = check_latest(self.timeout, self.base_url, self.current)
            with self._lock:
                self._result = res
        finally:
            self._done.set()

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    @property
    def done(self):
        return self._done.is_set()

    @property
    def result(self):
        with self._lock:
            return dict(self._result) if self._result else None

    def state(self):
        """/api/state.update 에 그대로 실을 값: 새 버전일 때만 dict, 아니면 None."""
        r = self.result
        if r and r.get("newer"):
            return {"latest": r["latest"], "url": r["url"]}
        return None

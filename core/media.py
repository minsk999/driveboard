"""미디어 서빙과 미리보기 생성 (SPEC-2 §C).

app.py 가 부르는 독립 모듈이다. 경로 검사(알려진 드라이브·캐시 안인지)는 app.py 관문 몫이고,
여기서는 토큰 표 · Range 서빙 · ffprobe 판정 · ffmpeg 포스터/프록시 큐만 다룬다.
"""

import collections
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time

from . import categories as cats

CHUNK = 1 << 20
GiB = 1 << 30

POSTER_TIMEOUT = 30
PROXY_TIMEOUT = 600
PROBE_TIMEOUT = 10
PROXY_MAX_SEC = 90
PROXY_MAX_W = 960
POSTER_W = 640

# Windows: 콘솔 창 없이, 낮은 우선순위로 (이동·화면이 미리보기에 밀리지 않게)
CREATE_NO_WINDOW = 0x08000000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000

_CTYPES = {
    "mp4": "video/mp4", "m4v": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mkv": "video/x-matroska", "avi": "video/x-msvideo", "wmv": "video/x-ms-wmv",
    "mpg": "video/mpeg", "mpeg": "video/mpeg", "mts": "video/mp2t", "m2ts": "video/mp2t",
    "3gp": "video/3gpp", "flv": "video/x-flv", "mxf": "application/mxf",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff",
    "heic": "image/heic", "heif": "image/heif",
}
# 브라우저 <img> 가 그대로 보여 주는 이미지
WEB_IMAGE_EXT = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp"})
# ffmpeg 가 못 여는 카메라 RAW 영상 → 프록시도 불가
UNDECODABLE_EXT = frozenset({"r3d", "braw", "ari"})
DIRECT_EXT_GUESS = frozenset({"mp4", "m4v", "webm", "mov"})   # ffprobe 없을 때의 추정
MP4_LIKE = frozenset({"mp4", "m4v", "mov"})
DIRECT_AUDIO = frozenset({None, "aac", "mp3"})
WEBM_VCODECS = frozenset({"vp8", "vp9", "av1"})
WEBM_ACODECS = frozenset({None, "opus", "vorbis"})
# Chromium 의 H.264 디코더는 8비트 4:2:0 만 확실하다
H264_OK_PIX = frozenset({None, "yuv420p", "yuvj420p"})
_ALPHA_PIX_RE = re.compile(r"^(yuva|gbrap|rgba|argb|bgra|abgr|ya)|^pal8$")


def _ext(path):
    return cats.split_ext(os.path.basename(path))[1]


def content_type_for(ext):
    """확장자(점 없이/있어도 됨) → Content-Type."""
    e = (ext or "").lower().lstrip(".")
    return _CTYPES.get(e, "application/octet-stream")


def is_media_ext(ext):
    e = (ext or "").lower().lstrip(".")
    return e in cats.VIDEO_EXT or e in cats.PROXY_VIDEO_EXT or e in cats.IMAGE_EXT


def _popen_kw():
    kw = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
    return kw


# ------------------------------------------------------------------ ffmpeg 찾기

def _exe(name):
    return name + ".exe" if os.name == "nt" else name


def _sibling(path, name):
    """ffmpeg 경로 옆의 ffprobe."""
    p = os.path.join(os.path.dirname(path), _exe(name))
    return p if os.path.isfile(p) else None


def find_ffmpeg(app_dir=None):
    """(ffmpeg, ffprobe) — 없으면 None. 순서: DRIVEBOARD_FFMPEG → <app_dir>/bin → PATH.

    DRIVEBOARD_FFMPEG 는 ffmpeg 실행 파일 또는 그 폴더. ffprobe 는 같은 곳을 먼저 본다.
    """
    if app_dir is None:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ff = fp = None
    env = os.environ.get("DRIVEBOARD_FFMPEG", "").strip().strip('"')
    if env:
        if os.path.isdir(env):
            c = os.path.join(env, _exe("ffmpeg"))
            if os.path.isfile(c):
                ff = c
        elif os.path.isfile(env):
            ff = env
    if ff is None:
        c = os.path.join(app_dir, "bin", _exe("ffmpeg"))
        if os.path.isfile(c):
            ff = c
    if ff is None:
        ff = shutil.which("ffmpeg")
    if ff:
        fp = _sibling(ff, "ffprobe")
    if fp is None:
        c = os.path.join(app_dir, "bin", _exe("ffprobe"))
        fp = c if os.path.isfile(c) else shutil.which("ffprobe")
    return ff, fp


# ------------------------------------------------------------------ 토큰 표

class MediaRegistry:
    """토큰 → 절대경로 LRU. 같은 경로는 같은 토큰을 다시 쓴다(밀려나면 새 토큰)."""

    def __init__(self, max_items=10000):
        self.max_items = max(1, int(max_items))
        self._tok = collections.OrderedDict()   # token -> path
        self._rev = {}                          # normcase(path) -> token
        self._lock = threading.Lock()

    @staticmethod
    def _norm(path):
        return os.path.normcase(os.path.abspath(path))

    def issue(self, path):
        path = os.path.abspath(path)
        k = self._norm(path)
        with self._lock:
            t = self._rev.get(k)
            if t is not None:
                self._tok.move_to_end(t)
                return t
            t = secrets.token_urlsafe(16)
            self._tok[t] = path
            self._rev[k] = t
            while len(self._tok) > self.max_items:
                old, op = self._tok.popitem(last=False)
                self._rev.pop(self._norm(op), None)
            return t

    def url(self, path):
        return "/media/" + self.issue(path)

    def resolve(self, token):
        if not isinstance(token, str):
            return None
        with self._lock:
            p = self._tok.get(token)
            if p is not None:
                self._tok.move_to_end(token)
            return p

    def __len__(self):
        with self._lock:
            return len(self._tok)


# ------------------------------------------------------------------ Range 서빙

_RANGE_RE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.I)


def parse_range(header, size):
    """단일 Range → (start, end) 포함 구간 / None(무시하고 200) / 'bad'(416)."""
    if not header:
        return None
    if "," in header:
        return None                  # 다중 구간은 지원하지 않음 → 전체 200 (RFC 허용)
    m = _RANGE_RE.match(header)
    if not m:
        return None                  # 모르는 형식은 무시
    a, b = m.group(1), m.group(2)
    if a == "" and b == "":
        return None
    if a == "":                      # bytes=-n : 끝에서 n 바이트
        n = int(b)
        if n == 0 or size == 0:
            return "bad"
        return max(0, size - n), size - 1
    start = int(a)
    if start >= size:
        return "bad"
    end = size - 1 if b == "" else min(int(b), size - 1)
    if end < start:
        return "bad"
    return start, end


def serve_file(handler, path, content_type=None, cache="private, max-age=3600"):
    """http.server 핸들러로 파일을 흘려보낸다(Range 지원). 보낸 상태 코드를 돌려준다.

    브라우저는 영상 요청을 수시로 끊으므로 BrokenPipe/ConnectionReset 은 조용히 넘긴다.
    """
    if content_type is None:
        content_type = content_type_for(_ext(path))
    try:
        f = open(path, "rb")
    except OSError:
        _plain(handler, 404, "not found")
        return 404
    try:
        try:
            st = os.fstat(f.fileno())
        except OSError:
            _plain(handler, 404, "not found")
            return 404
        size = st.st_size
        rng = parse_range(handler.headers.get("Range"), size)
        try:
            if rng == "bad":
                handler.send_response(416)
                handler.send_header("Content-Range", "bytes */%d" % size)
                handler.send_header("Accept-Ranges", "bytes")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return 416
            if rng is None:
                code, start, end = 200, 0, size - 1
            else:
                code, (start, end) = 206, rng
            length = max(0, end - start + 1)
            handler.send_response(code)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Accept-Ranges", "bytes")
            handler.send_header("Content-Length", str(length))
            if code == 206:
                handler.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            handler.send_header("Cache-Control", cache)
            handler.send_header("Last-Modified", _httpdate(st.st_mtime))
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()
            if handler.command == "HEAD" or length == 0:
                return code
            f.seek(start)
            left = length
            while left > 0:
                buf = f.read(min(CHUNK, left))
                if not buf:
                    break                # 파일이 줄어들었다 — 그만 보낸다
                handler.wfile.write(buf)
                left -= len(buf)
            return code
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            handler.close_connection = True
            return code if rng != "bad" else 416
        except OSError:
            handler.close_connection = True
            return 500
    finally:
        f.close()


def _plain(handler, code, msg):
    body = msg.encode("utf-8")
    try:
        handler.send_response(code)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        if handler.command != "HEAD":
            handler.wfile.write(body)
    except OSError:
        handler.close_connection = True


def _httpdate(ts):
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(ts))


# ------------------------------------------------------------------ ffprobe

_probe_cache = collections.OrderedDict()
_probe_lock = threading.Lock()
_PROBE_MAX = 5000


def _sig(path):
    """(abs path, size, mtime_ns) — 파일 신원. 없으면 None."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return os.path.abspath(path), st.st_size, st.st_mtime_ns


def probe(path, ffprobe=None, timeout=PROBE_TIMEOUT):
    """ffprobe → {duration,width,height,vcodec,acodec,pix_fmt,has_alpha} / None.

    (경로, 크기, 수정시각)으로 캐시한다. 실패도 캐시한다(같은 파일을 계속 두드리지 않게).
    """
    if ffprobe is None:
        ffprobe = find_ffmpeg()[1]
    if not ffprobe:
        return None
    sig = _sig(path)
    if sig is None:
        return None
    with _probe_lock:
        if sig in _probe_cache:
            _probe_cache.move_to_end(sig)
            return _probe_cache[sig]
    info = _run_probe(ffprobe, sig[0], timeout)
    with _probe_lock:
        _probe_cache[sig] = info
        while len(_probe_cache) > _PROBE_MAX:
            _probe_cache.popitem(last=False)
    return info


def probe_cached(path):
    """이미 캐시에 있는 ffprobe 결과만(없으면 None) — 목록 응답이 ffprobe 를 기다리지 않게."""
    sig = _sig(path)
    if sig is None:
        return None
    with _probe_lock:
        return _probe_cache.get(sig)


def _run_probe(ffprobe, path, timeout):
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
           path]
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             **_popen_kw())
    except OSError:
        return None
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        return None
    if p.returncode != 0:
        return None
    try:
        data = json.loads(out.decode("utf-8", "replace") or "{}")
    except ValueError:
        return None
    return parse_probe(data)


def parse_probe(data):
    """ffprobe JSON → info dict (순수 함수)."""
    v = a = None
    for s in data.get("streams") or []:
        ct = s.get("codec_type")
        if ct == "video" and v is None:
            if (s.get("disposition") or {}).get("attached_pic"):
                continue                 # 앨범 아트는 영상이 아니다
            v = s
        elif ct == "audio" and a is None:
            a = s
    fmt = data.get("format") or {}

    def fnum(x):
        try:
            f = float(x)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    dur = fnum(fmt.get("duration")) or (fnum(v.get("duration")) if v else None)
    pix = v.get("pix_fmt") if v else None
    return {
        "duration": dur,
        "width": int(v["width"]) if v and v.get("width") else None,
        "height": int(v["height"]) if v and v.get("height") else None,
        "vcodec": ((v.get("codec_name") or "").lower() or None) if v else None,
        "acodec": ((a.get("codec_name") or "").lower() or None) if a else None,
        "pix_fmt": pix,
        "has_alpha": bool(pix and _ALPHA_PIX_RE.search(pix)),
        "format": fmt.get("format_name"),
    }


def playable(path, info=None):
    """'direct'(브라우저가 바로 재생) | 'proxy'(프록시 필요) | 'none'(영상 아님·불가)."""
    ext = _ext(path)
    if ext not in cats.VIDEO_EXT and ext not in cats.PROXY_VIDEO_EXT:
        return "none"
    if ext in UNDECODABLE_EXT:
        return "none"
    if info is None:                     # ffprobe 없음/실패 → 확장자로 추정
        return "direct" if ext in DIRECT_EXT_GUESS else "proxy"
    vc = info.get("vcodec")
    if not vc:
        return "none"                    # 영상 스트림이 없다
    ac = info.get("acodec")
    if ext in MP4_LIKE:
        if (vc == "h264" and ac in DIRECT_AUDIO and info.get("pix_fmt") in H264_OK_PIX
                and not info.get("has_alpha")):
            return "direct"
        return "proxy"                   # ProRes·HEVC·DNx·PNG·Animation·10비트 H.264 …
    if ext == "webm":
        return "direct" if vc in WEBM_VCODECS and ac in WEBM_ACODECS else "proxy"
    return "proxy"


def _kill(p):
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        pass


# ------------------------------------------------------------------ 미리보기 큐

def cache_key(path, size, mtime_ns):
    s = "%s|%d|%d" % (os.path.normcase(os.path.abspath(path)), size, mtime_ns)
    return hashlib.sha1(s.encode("utf-8", "surrogatepass")).hexdigest()


def _scale(maxw):
    # 축소만, 짝수 폭·높이(yuv420p 요건)
    return "scale=w='min(%d,trunc(iw/2)*2)':h=-2,setsar=1" % maxw


def _flatten_graph(maxw):
    """알파를 검정 위에 합성 → 축소 → yuv420p."""
    return ("[0:v]split[a][b];[a]drawbox=c=black:t=fill[bg];[bg][b]overlay=format=auto,"
            + _scale(maxw) + ",format=yuv420p[v]")


def poster_cmd(ffmpeg, src, out, info=None):
    dur = (info or {}).get("duration")
    t = min(dur * 0.1, 3.0) if dur else 0.0
    alpha = info is None or info.get("has_alpha")
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y"]
    if t > 0:
        cmd += ["-ss", "%.3f" % t]
    cmd += ["-i", src, "-map_metadata", "-1", "-an", "-sn", "-dn"]
    if alpha:
        cmd += ["-filter_complex", _flatten_graph(POSTER_W).replace("format=yuv420p", "format=yuvj420p"),
                "-map", "[v]"]
    else:
        cmd += ["-map", "0:v:0", "-vf", _scale(POSTER_W) + ",format=yuvj420p"]
    cmd += ["-frames:v", "1", "-q:v", "4", "-f", "image2", "-update", "1", out]
    return cmd


def proxy_cmd(ffmpeg, src, out, info=None):
    alpha = info is None or info.get("has_alpha")
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", src,
           "-t", str(PROXY_MAX_SEC), "-map_metadata", "-1", "-sn", "-dn"]
    if alpha:
        cmd += ["-filter_complex", _flatten_graph(PROXY_MAX_W), "-map", "[v]"]
    else:
        cmd += ["-map", "0:v:0", "-vf", _scale(PROXY_MAX_W) + ",format=yuv420p"]
    cmd += ["-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart", "-f", "mp4", out]
    return cmd


class PreviewQueue:
    """포스터·프록시 생성 큐. ops 큐와 분리된 스레드 workers 개. 요청 순서 LIFO.

    poster(path)/proxy(path) → {'status': 'ready'|'queued'|'failed'|'no_ffmpeg', 'file'?}
    """

    KINDS = {"poster": ("jpg", POSTER_TIMEOUT), "proxy": ("mp4", PROXY_TIMEOUT)}

    def __init__(self, data_dir, ffmpeg, workers=2, cache_gb=2, ffprobe=None):
        self.dir = os.path.join(data_dir, "previews")
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.nworkers = max(1, int(workers))
        self.limit = self._gb(cache_gb)
        self._lock = threading.Condition()
        self._stack = []                 # 대기 작업 key (끝 = 다음 차례)
        self._jobs = {}                  # key -> {kind, path, key, out, state}
        self._failed = {}                # key -> 이유
        self._procs = {}                 # key -> Popen
        self._done = collections.deque(maxlen=100)
        self._threads = []
        self._closed = False
        self._paused = False             # 시험용: 일꾼이 작업을 집지 않는다
        self.version = 0                 # 상태가 바뀔 때마다 증가(UI 폴링용)
        if ffmpeg:
            os.makedirs(self.dir, exist_ok=True)
            self._cleanup_tmp()

    @staticmethod
    def _gb(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 2.0
        return int(min(50.0, max(0.5, v)) * GiB)

    def set_cache_gb(self, gb):
        self.limit = self._gb(gb)
        self.evict()

    # -- 요청
    def poster(self, path):
        return self.request("poster", path)

    def proxy(self, path):
        return self.request("proxy", path)

    def request(self, kind, path):
        if kind not in self.KINDS:
            raise ValueError(kind)
        if not self.ffmpeg:
            return {"status": "no_ffmpeg"}
        sig = _sig(path)
        if sig is None or not os.path.isfile(sig[0]):
            return {"status": "failed", "reason": "missing"}
        key = kind[:2] + "-" + cache_key(*sig)
        out = os.path.join(self.dir, key + "." + self.KINDS[kind][0])
        with self._lock:
            if key in self._jobs:
                if self._jobs[key]["state"] == "queued":
                    self._stack.remove(key)          # 다시 원하면 맨 위로
                    self._stack.append(key)
                return {"status": "queued"}
            if key in self._failed:
                return {"status": "failed", "reason": self._failed[key]}
        if os.path.isfile(out):
            self._touch(out)
            return {"status": "ready", "file": out}
        with self._lock:
            if self._closed:
                return {"status": "failed", "reason": "closed"}
            if key in self._jobs:
                return {"status": "queued"}
            self._jobs[key] = {"kind": kind, "path": sig[0], "key": key, "out": out,
                               "state": "queued"}
            self._stack.append(key)
            self.version += 1
            self._start_workers()
            self._lock.notify()
        return {"status": "queued"}

    def status(self, kind, path):
        """만들지 않고 상태만 본다."""
        sig = _sig(path)
        if sig is None:
            return {"status": "failed", "reason": "missing"}
        key = kind[:2] + "-" + cache_key(*sig)
        out = os.path.join(self.dir, key + "." + self.KINDS[kind][0])
        with self._lock:
            if key in self._jobs:
                return {"status": "queued"}
            if key in self._failed:
                return {"status": "failed", "reason": self._failed[key]}
        if self.ffmpeg is None:
            return {"status": "no_ffmpeg"}
        if os.path.isfile(out):
            return {"status": "ready", "file": out}
        return {"status": "none"}

    def is_cache_file(self, path):
        try:
            p = os.path.normcase(os.path.abspath(path))
            d = os.path.normcase(os.path.abspath(self.dir))
            return os.path.dirname(p) == d
        except (TypeError, ValueError):
            return False

    # -- 일꾼
    def _start_workers(self):
        self._threads = [t for t in self._threads if t.is_alive()]
        while len(self._threads) < self.nworkers:
            t = threading.Thread(target=self._worker, name="preview", daemon=True)
            self._threads.append(t)
            t.start()

    def _worker(self):
        while True:
            with self._lock:
                while not self._closed and (self._paused or not self._stack):
                    self._lock.wait(1.0)
                if self._closed:
                    return
                key = self._stack.pop()
                job = self._jobs[key]
                job["state"] = "running"
            ok, reason = False, "error"
            try:
                ok, reason = self._run(job)
            except Exception as e:      # 일꾼은 죽지 않는다
                reason = "error: %s" % e.__class__.__name__
            with self._lock:
                self._jobs.pop(key, None)
                self._procs.pop(key, None)
                if ok:
                    self._done.append({"kind": job["kind"], "path": job["path"],
                                       "t": time.time()})
                else:
                    self._failed[key] = reason
                self.version += 1
                self._lock.notify_all()
            if ok:
                self.evict(keep=job["out"])

    def _run(self, job):
        info = probe(job["path"], self.ffprobe) if self.ffprobe else None
        if self.ffprobe and info is not None and not info.get("vcodec"):
            return False, "no_video"
        tmp = "%s.tmp-%s.%s" % (job["out"][:-4], secrets.token_hex(4), job["out"][-3:])
        build = poster_cmd if job["kind"] == "poster" else proxy_cmd
        timeout = self.KINDS[job["kind"]][1]
        try:
            ok, reason = self._ffmpeg(job["key"], build(self.ffmpeg, job["path"], tmp, info),
                                      timeout, tmp)
            if not ok and job["kind"] == "poster" and reason == "empty":
                # 길이 정보가 틀려 시킹이 끝을 넘은 경우: 첫 프레임으로 한 번 더
                ok, reason = self._ffmpeg(job["key"], build(self.ffmpeg, job["path"], tmp,
                                                            dict(info or {}, duration=None)),
                                          timeout, tmp)
            if ok:
                os.replace(tmp, job["out"])
            return ok, reason
        finally:
            _unlink(tmp)

    def _ffmpeg(self, key, cmd, timeout, tmp):
        _unlink(tmp)
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 **_popen_kw())
        except OSError:
            return False, "spawn"
        with self._lock:
            self._procs[key] = p
            closed = self._closed
        if closed:
            _kill(p)
            return False, "closed"
        try:
            _, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(p)
            return False, "timeout"
        if self._closed:
            return False, "closed"
        if p.returncode != 0:
            return False, "ffmpeg: " + (err or b"").decode("utf-8", "replace").strip()[-200:]
        try:
            if os.path.getsize(tmp) <= 0:
                return False, "empty"
        except OSError:
            return False, "empty"
        return True, None

    # -- 캐시
    def _touch(self, path):
        try:
            os.utime(path, None)         # 우리 파일의 mtime 을 「마지막 사용」으로 쓴다
        except OSError:
            pass

    def _entries(self):
        out = []
        try:
            it = os.scandir(self.dir)
        except OSError:
            return out
        with it:
            for e in it:
                if ".tmp-" in e.name or not e.is_file(follow_symlinks=False):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                out.append((st.st_mtime, st.st_size, e.path))
        return out

    def evict(self, keep=None):
        """상한을 넘으면 오래 안 쓴 것부터 지운다. 지운 바이트 수."""
        ents = sorted(self._entries())
        total = sum(s for _, s, _ in ents)
        freed = 0
        keep_n = os.path.normcase(keep) if keep else None
        for _, s, p in ents:
            if total <= self.limit:
                break
            if keep_n and os.path.normcase(p) == keep_n:
                continue
            try:
                os.remove(p)
            except OSError:
                continue                 # 서빙 중(Windows 잠금) 등 — 다음에
            total -= s
            freed += s
        return freed

    def clear(self):
        """「미리보기 캐시 비우기」. 대기 작업도 비운다. (지운 개수, 바이트)."""
        with self._lock:
            for k in self._stack:
                self._jobs.pop(k, None)
            self._stack.clear()
            self._failed.clear()
            self._done.clear()
            self.version += 1
        n = b = 0
        for _, s, p in self._entries():
            try:
                os.remove(p)
                n += 1
                b += s
            except OSError:
                pass
        return n, b

    def _cleanup_tmp(self):
        try:
            for e in os.scandir(self.dir):
                if ".tmp-" in e.name:
                    _unlink(e.path)
        except OSError:
            pass

    def stats(self):
        ents = self._entries()
        with self._lock:
            return {"ffmpeg": bool(self.ffmpeg), "count": len(ents),
                    "bytes": sum(s for _, s, _ in ents), "limit_bytes": self.limit,
                    "queued": len(self._stack),
                    "running": sum(1 for j in self._jobs.values() if j["state"] == "running"),
                    "failed": len(self._failed), "version": self.version,
                    "done": list(self._done)[-50:]}

    def wait_idle(self, timeout=None):
        """큐가 빌 때까지 기다린다(시험·종료용). 비었으면 True."""
        end = None if timeout is None else time.time() + timeout
        with self._lock:
            while self._jobs:
                left = None if end is None else end - time.time()
                if left is not None and left <= 0:
                    return False
                self._lock.wait(left if left is not None else 1.0)
            return True

    def close(self):
        with self._lock:
            self._closed = True
            procs = list(self._procs.values())
            self._lock.notify_all()
        for p in procs:
            _kill(p)
        for t in self._threads:
            t.join(timeout=5)


def _unlink(p):
    try:
        os.remove(p)
    except OSError:
        pass


# ------------------------------------------------------------------ <media>

def media_object(path, registry, queue=None, info=None, cat=None, ffprobe=None):
    """<media> 객체(SPEC-2 B.5). 포스터는 준비된 경우에만 url 을 준다."""
    path = os.path.abspath(path)
    name = os.path.basename(path)
    ext = _ext(path)
    try:
        st = os.stat(path)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = None, None
    if cat is None:
        if ext in cats.VIDEO_EXT:
            cat = cats.VIDEO
        elif ext in cats.PROXY_VIDEO_EXT:
            cat = cats.PROXY
        elif ext in cats.IMAGE_EXT:
            cat = cats.IMAGE
        else:
            cat = cats.OTHER
    is_video = ext in cats.VIDEO_EXT or ext in cats.PROXY_VIDEO_EXT
    if info is None and is_video and size is not None:
        fp = ffprobe if ffprobe is not None else (queue.ffprobe if queue else None)
        if fp:
            info = probe(path, fp)
    obj = {"path": path, "name": name, "size": size, "mtime": mtime, "cat": cat, "ext": ext,
           "playable": "none", "poster_url": None, "video_url": None}
    if size is None:
        return obj
    if ext in WEB_IMAGE_EXT:
        obj["poster_url"] = registry.url(path)    # 이미지는 자기 자신이 포스터
        return obj
    if not is_video:
        return obj
    pl = playable(path, info)
    obj["playable"] = pl
    if pl == "direct":
        obj["video_url"] = registry.url(path)
    elif pl == "proxy" and queue is not None:
        st_ = queue.status("proxy", path)
        if st_.get("status") == "ready":
            obj["video_url"] = registry.url(st_["file"])
    if queue is not None and pl != "none":
        st_ = queue.status("poster", path)
        if st_.get("status") == "ready":
            obj["poster_url"] = registry.url(st_["file"])
    if info:
        for k in ("duration", "width", "height"):
            if info.get(k):
                obj[k] = info[k]
    return obj

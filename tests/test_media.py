"""core.media 테스트 — 실제 작은 영상(ffmpeg 로 생성)으로 판정·포스터·프록시·Range 서빙을 검사한다."""
import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import media  # noqa: E402

FF, FP = media.find_ffmpeg()
HAVE_FF = bool(FF and FP)
need_ff = unittest.skipUnless(HAVE_FF, "ffmpeg/ffprobe 없음")


def ff(*args):
    subprocess.run([FF, "-v", "error", "-y", "-nostdin"] + list(args), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class Clips:
    """클래스 단위로 한 번만 만드는 시험 영상들."""
    dir = None

    @classmethod
    def make(cls):
        if cls.dir:
            return cls
        cls.dir = tempfile.mkdtemp(prefix="dbmedia-clips-")
        d = cls.dir
        cls.h264 = os.path.join(d, "h264.mp4")
        ff("-f", "lavfi", "-i", "testsrc=s=320x180:d=4:r=25", "-f", "lavfi",
           "-i", "sine=f=440:d=4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt",
           "yuv420p", "-c:a", "aac", "-shortest", cls.h264)
        cls.noaudio = os.path.join(d, "noaudio.mp4")
        ff("-f", "lavfi", "-i", "testsrc=s=320x180:d=2:r=25", "-c:v", "libx264", "-preset",
           "ultrafast", "-pix_fmt", "yuv420p", cls.noaudio)
        cls.prores = os.path.join(d, "prores.mov")
        ff("-f", "lavfi", "-i", "testsrc=s=320x180:d=2:r=25", "-f", "lavfi",
           "-i", "sine=f=440:d=2", "-c:v", "prores_ks", "-profile:v", "2", "-c:a", "pcm_s16le",
           "-shortest", cls.prores)
        cls.alpha = os.path.join(d, "alpha.mov")      # 왼쪽 절반이 투명한 ProRes 4444
        ff("-f", "lavfi", "-i", "testsrc=s=320x180:d=2:r=25,format=yuva444p10le,"
           "geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='if(lt(X,160),0,1023)'",
           "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", cls.alpha)
        cls.h264_10 = os.path.join(d, "h264_10bit.mp4")
        ff("-f", "lavfi", "-i", "testsrc=s=320x180:d=1:r=25", "-c:v", "libx264", "-preset",
           "ultrafast", "-pix_fmt", "yuv422p", cls.h264_10)
        cls.long = os.path.join(d, "long_wide.mp4")    # 1280 폭 · 95초 → 프록시 960 · 90초
        ff("-f", "lavfi", "-i", "testsrc=s=1280x720:d=95:r=2", "-c:v", "libx264", "-preset",
           "ultrafast", "-pix_fmt", "yuv420p", cls.long)
        cls.audio = os.path.join(d, "song.mp4")         # 영상 스트림 없는 mp4
        ff("-f", "lavfi", "-i", "sine=f=440:d=1", "-c:a", "aac", cls.audio)
        return cls

    @classmethod
    def cleanup(cls):
        if cls.dir:
            shutil.rmtree(cls.dir, ignore_errors=True)
            cls.dir = None


def tearDownModule():
    Clips.cleanup()


def vprobe(path):
    out = subprocess.run([FP, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,pix_fmt,width,height:format=duration",
                          "-of", "default=nw=1", path], capture_output=True, text=True).stdout
    d = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        d[k] = v
    return d


# ------------------------------------------------------------------ 순수 로직

class TestPure(unittest.TestCase):
    def test_parse_range(self):
        pr = media.parse_range
        self.assertIsNone(pr(None, 100))
        self.assertEqual(pr("bytes=0-9", 100), (0, 9))
        self.assertEqual(pr("bytes=90-", 100), (90, 99))
        self.assertEqual(pr("bytes=-10", 100), (90, 99))
        self.assertEqual(pr("bytes=-500", 100), (0, 99))
        self.assertEqual(pr("bytes=50-5000", 100), (50, 99))
        self.assertEqual(pr("bytes=100-", 100), "bad")
        self.assertEqual(pr("bytes=9-3", 100), "bad")
        self.assertEqual(pr("bytes=-0", 100), "bad")
        self.assertIsNone(pr("bytes=0-1,5-6", 100))     # 다중 구간 → 전체
        self.assertIsNone(pr("items=0-1", 100))         # 모르는 단위 → 무시

    def test_content_type(self):
        self.assertEqual(media.content_type_for("mp4"), "video/mp4")
        self.assertEqual(media.content_type_for(".MOV"), "video/quicktime")
        self.assertEqual(media.content_type_for("jpg"), "image/jpeg")
        self.assertEqual(media.content_type_for("xyz"), "application/octet-stream")

    def test_playable_without_probe(self):
        pl = media.playable
        self.assertEqual(pl("a.mp4"), "direct")
        self.assertEqual(pl("a.MOV"), "direct")
        self.assertEqual(pl("a.webm"), "direct")
        self.assertEqual(pl("a.mxf"), "proxy")
        self.assertEqual(pl("a.avi"), "proxy")
        self.assertEqual(pl("a.r3d"), "none")
        self.assertEqual(pl("a.jpg"), "none")
        self.assertEqual(pl("a.aep"), "none")

    def test_playable_rules(self):
        pl = media.playable
        base = {"vcodec": "h264", "acodec": "aac", "pix_fmt": "yuv420p", "has_alpha": False}
        self.assertEqual(pl("a.mp4", base), "direct")
        self.assertEqual(pl("a.mov", dict(base, acodec=None)), "direct")
        self.assertEqual(pl("a.mov", dict(base, vcodec="hevc")), "proxy")
        self.assertEqual(pl("a.mov", dict(base, vcodec="prores", acodec="pcm_s16le")), "proxy")
        self.assertEqual(pl("a.mp4", dict(base, pix_fmt="yuv420p10le")), "proxy")
        self.assertEqual(pl("a.mov", dict(base, acodec="pcm_s24le")), "proxy")
        self.assertEqual(pl("a.webm", dict(base, vcodec="vp9", acodec="opus")), "direct")
        self.assertEqual(pl("a.mkv", base), "proxy")
        self.assertEqual(pl("a.mp4", dict(base, vcodec=None)), "none")

    def test_parse_probe_skips_cover_art(self):
        info = media.parse_probe({"streams": [
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "3.5"}})
        self.assertIsNone(info["vcodec"])
        self.assertEqual(info["acodec"], "aac")
        self.assertEqual(info["duration"], 3.5)

    def test_find_ffmpeg_order(self):
        with tempfile.TemporaryDirectory() as d:
            envd, appd = os.path.join(d, "env"), os.path.join(d, "app")
            os.makedirs(envd)
            os.makedirs(os.path.join(appd, "bin"))
            for p in (os.path.join(envd, "ffmpeg"), os.path.join(envd, "ffprobe"),
                      os.path.join(appd, "bin", "ffmpeg"), os.path.join(appd, "bin", "ffprobe")):
                open(p, "w").close()
            with mock.patch.dict(os.environ, {"DRIVEBOARD_FFMPEG": envd}), \
                    mock.patch.object(media.os, "name", "posix"):
                self.assertEqual(media.find_ffmpeg(appd),
                                 (os.path.join(envd, "ffmpeg"), os.path.join(envd, "ffprobe")))
            with mock.patch.dict(os.environ, {"DRIVEBOARD_FFMPEG": os.path.join(envd, "ffmpeg")}):
                self.assertEqual(media.find_ffmpeg(appd)[0], os.path.join(envd, "ffmpeg"))
            env = dict(os.environ)
            env.pop("DRIVEBOARD_FFMPEG", None)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(media.find_ffmpeg(appd),
                                 (os.path.join(appd, "bin", "ffmpeg"),
                                  os.path.join(appd, "bin", "ffprobe")))
                with mock.patch.object(media.shutil, "which", return_value=None):
                    self.assertEqual(media.find_ffmpeg(os.path.join(d, "none")), (None, None))


class TestRegistry(unittest.TestCase):
    def test_reuse_and_resolve(self):
        r = media.MediaRegistry()
        t1 = r.issue("/x/a.mp4")
        self.assertEqual(r.issue("/x/a.mp4"), t1)
        self.assertEqual(r.issue("/x/./a.mp4"), t1)
        self.assertNotEqual(r.issue("/x/b.mp4"), t1)
        self.assertEqual(r.resolve(t1), os.path.abspath("/x/a.mp4"))
        self.assertIsNone(r.resolve("nope"))
        self.assertIsNone(r.resolve(None))
        self.assertGreaterEqual(len(t1), 20)
        self.assertTrue(r.url("/x/a.mp4").endswith(t1))

    def test_lru_expiry(self):
        r = media.MediaRegistry(max_items=3)
        ta, tb, tc = r.issue("/a"), r.issue("/b"), r.issue("/c")
        r.resolve(ta)                    # a 를 최근으로
        td = r.issue("/d")               # b 가 밀려난다
        self.assertIsNone(r.resolve(tb))
        self.assertEqual(r.resolve(ta), os.path.abspath("/a"))
        self.assertEqual(r.resolve(tc), os.path.abspath("/c"))
        self.assertEqual(r.resolve(td), os.path.abspath("/d"))
        self.assertEqual(len(r), 3)
        tb2 = r.issue("/b")              # 밀려난 경로는 새 토큰
        self.assertNotEqual(tb2, tb)
        self.assertIsNone(r.resolve(tb))


# ------------------------------------------------------------------ Range 서빙

class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    path_map = {}
    errors = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path_map.get(self.path)
        if p is None:
            self.send_error(404)
            return
        media.serve_file(self, p)

    do_HEAD = do_GET


class _Srv(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        _H.errors.append(sys.exc_info()[1])


class TestServe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dbmedia-serve-")
        cls.data = bytes(range(256)) * (4096 * 5 + 7)      # ~5MB, 청크 여러 개
        cls.file = os.path.join(cls.tmp, "v.mp4")
        with open(cls.file, "wb") as f:
            f.write(cls.data)
        cls.empty = os.path.join(cls.tmp, "e.mp4")
        open(cls.empty, "wb").close()
        _H.path_map = {"/v": cls.file, "/e": cls.empty, "/gone": os.path.join(cls.tmp, "x.mp4")}
        cls.srv = _Srv(("127.0.0.1", 0), _H)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, rng=None, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Range": rng} if rng else {}
        c.request(method, path, headers=h)
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_full(self):
        r, b = self.get("/v")
        self.assertEqual(r.status, 200)
        self.assertEqual(b, self.data)
        self.assertEqual(r.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(int(r.getheader("Content-Length")), len(self.data))
        self.assertEqual(r.getheader("Content-Type"), "video/mp4")
        self.assertIn("private", r.getheader("Cache-Control"))

    def test_ranges(self):
        n = len(self.data)
        for rng, (a, z) in (("bytes=0-99", (0, 99)), ("bytes=1048570-2097160", (1048570, 2097160)),
                            ("bytes=%d-" % (n - 5), (n - 5, n - 1)), ("bytes=-1000", (n - 1000, n - 1)),
                            ("bytes=10-%d" % (n * 2), (10, n - 1))):
            r, b = self.get("/v", rng)
            self.assertEqual(r.status, 206, rng)
            self.assertEqual(b, self.data[a:z + 1], rng)
            self.assertEqual(r.getheader("Content-Range"), "bytes %d-%d/%d" % (a, z, n))
            self.assertEqual(int(r.getheader("Content-Length")), z - a + 1)

    def test_416_and_misc(self):
        n = len(self.data)
        r, b = self.get("/v", "bytes=%d-" % n)
        self.assertEqual(r.status, 416)
        self.assertEqual(r.getheader("Content-Range"), "bytes */%d" % n)
        r, b = self.get("/v", "bytes=0-1,4-5")
        self.assertEqual(r.status, 200)
        r, b = self.get("/e")
        self.assertEqual((r.status, b), (200, b""))
        r, b = self.get("/e", "bytes=0-")
        self.assertEqual(r.status, 416)
        r, b = self.get("/gone")
        self.assertEqual(r.status, 404)
        r, b = self.get("/v", "bytes=0-9", method="HEAD")
        self.assertEqual((r.status, b), (206, b""))

    def test_abort_mid_stream(self):
        _H.errors.clear()
        for _ in range(3):
            s = socket.create_connection(("127.0.0.1", self.port))
            s.sendall(b"GET /v HTTP/1.1\r\nHost: x\r\n\r\n")
            s.recv(4096)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
            s.close()                    # RST 로 끊는다
        time.sleep(0.3)
        r, b = self.get("/v", "bytes=0-9")       # 서버는 멀쩡해야 한다
        self.assertEqual((r.status, b), (206, self.data[:10]))
        self.assertEqual(_H.errors, [])


# ------------------------------------------------------------------ ffprobe · 큐 (실제 ffmpeg)

@need_ff
class TestProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Clips.make()

    def test_classify(self):
        c = Clips
        i = media.probe(c.h264, FP)
        self.assertEqual((i["vcodec"], i["acodec"], i["width"], i["height"]),
                         ("h264", "aac", 320, 180))
        self.assertAlmostEqual(i["duration"], 4.0, delta=0.2)
        self.assertEqual(media.playable(c.h264, i), "direct")
        self.assertEqual(media.playable(c.noaudio, media.probe(c.noaudio, FP)), "direct")
        ip = media.probe(c.prores, FP)
        self.assertEqual(ip["vcodec"], "prores")
        self.assertFalse(ip["has_alpha"])
        self.assertEqual(media.playable(c.prores, ip), "proxy")
        ia = media.probe(c.alpha, FP)
        self.assertTrue(ia["has_alpha"], ia["pix_fmt"])
        self.assertEqual(media.playable(c.alpha, ia), "proxy")
        self.assertEqual(media.playable(c.h264_10, media.probe(c.h264_10, FP)), "proxy")
        self.assertEqual(media.playable(c.audio, media.probe(c.audio, FP)), "none")

    def test_cache_and_failures(self):
        with mock.patch.object(media, "_run_probe", wraps=media._run_probe) as rp:
            media._probe_cache.clear()
            media.probe(Clips.h264, FP)
            media.probe(Clips.h264, FP)
            self.assertEqual(rp.call_count, 1)
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.mp4")
            with open(bad, "wb") as f:
                f.write(b"not a video" * 100)
            self.assertIsNone(media.probe(bad, FP))
            self.assertEqual(media.playable(bad, None), "direct")   # 추정 → UI 오류 시 프록시
            self.assertIsNone(media.probe(os.path.join(d, "missing.mp4"), FP))
        self.assertIsNone(media.probe(Clips.h264, ffprobe=""))


class QueueBase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="dbmedia-home-")
        self.q = None

    def tearDown(self):
        if self.q:
            self.q.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def no_partials(self):
        left = [n for n in os.listdir(os.path.join(self.home, "previews")) if ".tmp-" in n]
        self.assertEqual(left, [])


class TestQueueNoFfmpeg(QueueBase):
    def test_no_ffmpeg(self):
        self.q = media.PreviewQueue(self.home, None)
        f = os.path.join(self.home, "a.mp4")
        open(f, "wb").close()
        self.assertEqual(self.q.poster(f), {"status": "no_ffmpeg"})
        self.assertEqual(self.q.proxy(f), {"status": "no_ffmpeg"})
        self.assertFalse(self.q.stats()["ffmpeg"])


@need_ff
class TestQueue(QueueBase):
    @classmethod
    def setUpClass(cls):
        Clips.make()

    def wait(self, kind, path, timeout=120):
        self.assertTrue(self.q.wait_idle(timeout))
        return self.q.request(kind, path)

    def test_poster(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        self.assertEqual(self.q.poster(Clips.h264)["status"], "queued")
        r = self.wait("poster", Clips.h264)
        self.assertEqual(r["status"], "ready")
        self.assertTrue(r["file"].endswith(".jpg"))
        with open(r["file"], "rb") as f:
            self.assertEqual(f.read(2), b"\xff\xd8")
        self.assertTrue(self.q.is_cache_file(r["file"]))
        self.assertEqual(vprobe(r["file"])["width"], "320")       # 축소만, 확대 안 함
        self.no_partials()

    def test_poster_alpha_flattened_on_black(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        self.q.poster(Clips.alpha)
        r = self.wait("poster", Clips.alpha)
        self.assertEqual(r["status"], "ready")
        px = subprocess.run([FF, "-v", "error", "-i", r["file"], "-vf", "crop=20:20:20:80",
                             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            capture_output=True).stdout
        self.assertTrue(px and max(px) < 30, "투명 영역이 검정이 아니다")

    def test_proxy_h264_limits(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        self.q.proxy(Clips.long)
        self.q.proxy(Clips.alpha)
        self.q.proxy(Clips.prores)
        for src, w in ((Clips.long, 960), (Clips.alpha, 320), (Clips.prores, 320)):
            r = self.wait("proxy", src, 300)
            self.assertEqual(r["status"], "ready", src)
            v = vprobe(r["file"])
            self.assertEqual((v["codec_name"], v["pix_fmt"]), ("h264", "yuv420p"), src)
            self.assertEqual(int(v["width"]), w, src)
            self.assertLessEqual(float(v["duration"]), 90.5, src)
            self.assertEqual(media.playable(r["file"], media.probe(r["file"], FP)), "direct")
        self.assertGreater(float(vprobe(self.q.proxy(Clips.long)["file"])["duration"]), 85)
        self.no_partials()

    def test_failed_and_missing(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        bad = os.path.join(self.home, "bad.mov")
        with open(bad, "wb") as f:
            f.write(b"\0" * 5000)
        self.q.proxy(bad)
        r = self.wait("proxy", bad)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(self.q.stats()["failed"], 1)
        self.assertEqual(self.q.poster(os.path.join(self.home, "nope.mp4"))["status"], "failed")
        self.no_partials()
        self.assertEqual([n for n in os.listdir(os.path.join(self.home, "previews"))], [])

    def test_timeout_kills_and_cleans(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        with mock.patch.dict(media.PreviewQueue.KINDS, {"proxy": ("mp4", 0.05)}):
            self.q.proxy(Clips.long)
            r = self.wait("proxy", Clips.long)
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["reason"], "timeout")
        self.no_partials()

    def test_file_change_new_key(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        src = os.path.join(self.home, "clip.mp4")
        shutil.copy(Clips.noaudio, src)
        self.q.poster(src)
        r1 = self.wait("poster", src)
        os.utime(src, (time.time() - 100, time.time() - 100))
        self.assertEqual(self.q.poster(src)["status"], "queued")
        r2 = self.wait("poster", src)
        self.assertNotEqual(r1["file"], r2["file"])

    def test_dedup_and_lifo(self):
        self.q = media.PreviewQueue(self.home, FF, workers=1, ffprobe=FP)
        self.q._paused = True
        srcs = [Clips.h264, Clips.noaudio, Clips.prores, Clips.h264_10]
        for s in srcs:
            self.assertEqual(self.q.poster(s)["status"], "queued")
        self.assertEqual(self.q.poster(Clips.h264)["status"], "queued")   # 중복 → 맨 위로
        self.assertEqual(self.q.stats()["queued"], 4)
        with mock.patch.object(media.subprocess, "Popen", wraps=media.subprocess.Popen) as po:
            with self.q._lock:
                self.q._paused = False
                self.q._lock.notify_all()
            self.assertTrue(self.q.wait_idle(120))
            ff_calls = [c for c in po.call_args_list if c.args[0][0] == FF]
            self.assertEqual(len(ff_calls), 4)
        order = [d["path"] for d in self.q.stats()["done"]]
        self.assertEqual(order, [os.path.abspath(p) for p in
                                 (Clips.h264, Clips.h264_10, Clips.prores, Clips.noaudio)])

    def test_lru_eviction_and_clear(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        d = self.q.dir
        now = time.time()
        for i in range(5):
            p = os.path.join(d, "f%d.jpg" % i)
            with open(p, "wb") as f:
                f.write(b"x" * 1000)
            os.utime(p, (now - 1000 + i * 10, now - 1000 + i * 10))
        self.q._touch(os.path.join(d, "f0.jpg"))       # f0 을 방금 쓴 것으로
        self.q.limit = 3000
        self.assertEqual(self.q.evict(), 2000)
        self.assertEqual(sorted(os.listdir(d)), ["f0.jpg", "f3.jpg", "f4.jpg"])
        self.assertEqual(self.q.stats()["bytes"], 3000)
        self.q.set_cache_gb(0.01)                       # 하한 0.5GB 로 고정
        self.assertEqual(self.q.limit, int(0.5 * media.GiB))
        self.assertEqual(self.q.clear(), (3, 3000))
        self.assertEqual(os.listdir(d), [])

    def test_ready_hit_reuses_and_touches(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        self.q.poster(Clips.h264)
        f = self.wait("poster", Clips.h264)["file"]
        os.utime(f, (1, 1))
        self.assertEqual(self.q.poster(Clips.h264)["file"], f)
        self.assertGreater(os.path.getmtime(f), 1000)

    def test_media_object(self):
        self.q = media.PreviewQueue(self.home, FF, ffprobe=FP)
        reg = media.MediaRegistry()
        o = media.media_object(Clips.h264, reg, self.q)
        self.assertEqual(o["playable"], "direct")
        self.assertIsNone(o["poster_url"])
        self.assertTrue(o["video_url"].startswith("/media/"))
        self.assertEqual(reg.resolve(o["video_url"][7:]), os.path.abspath(Clips.h264))
        self.assertEqual((o["width"], o["height"]), (320, 180))
        for k in ("path", "name", "size", "mtime", "cat", "ext"):
            self.assertIn(k, o)
        self.q.poster(Clips.h264)
        self.q.wait_idle(60)
        o = media.media_object(Clips.h264, reg, self.q)
        self.assertTrue(self.q.is_cache_file(reg.resolve(o["poster_url"][7:])))
        p = media.media_object(Clips.prores, reg, self.q)
        self.assertEqual((p["playable"], p["video_url"]), ("proxy", None))
        self.q.proxy(Clips.prores)
        self.q.wait_idle(120)
        p = media.media_object(Clips.prores, reg, self.q)
        self.assertTrue(reg.resolve(p["video_url"][7:]).endswith(".mp4"))
        n = media.media_object(os.path.join(self.home, "missing.mp4"), reg, self.q)
        self.assertEqual((n["playable"], n["size"]), ("none", None))


if __name__ == "__main__":
    unittest.main()

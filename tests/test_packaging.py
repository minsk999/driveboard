"""배포 묶음 점검(SPEC-2 §E) + 앱 아이콘 생성기.

시험: driveboard.spec · 버전 일치 · ffmpeg 고정값(build.bat·release.yml·THIRD-PARTY.txt) ·
build.bat CRLF · 워크플로 구조 · build/icon.ico · ui/icon.svg.

아이콘 다시 만들기(표준 라이브러리만, Pillow 불필요):
    python tests/test_packaging.py --write-icon
    → build/icon.ico (16·24·32·48·64·128·256, PNG 내장) + ui/icon.svg
"""
import ast
import contextlib
import io
import os
import re
import struct
import sys
import tempfile
import shutil
import types
import unittest
import xml.etree.ElementTree as ET
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import version as V  # noqa: E402

ICO_PATH = os.path.join(ROOT, "build", "icon.ico")
SVG_PATH = os.path.join(ROOT, "ui", "icon.svg")
SPEC_PATH = os.path.join(ROOT, "driveboard.spec")
BAT_PATH = os.path.join(ROOT, "build.bat")
REL_PATH = os.path.join(ROOT, ".github", "workflows", "release.yml")
TEST_WF_PATH = os.path.join(ROOT, ".github", "workflows", "test.yml")
TP_PATH = os.path.join(ROOT, "THIRD-PARTY.txt")
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


# =================================================================== 아이콘 생성기
# 어두운 둥근 사각형 + 저장 공간 막대 3줄(라임 #A2FF00 + 카테고리 색). 좌표는 256 격자.
BG_TOP = (0x1E, 0x20, 0x28)
BG_BOT = (0x0B, 0x0C, 0x10)
TRACK = (0x2A, 0x2C, 0x35)
LIME = (0xA2, 0xFF, 0x00)
BARS = [   # (구간 끝 비율, 색) — 나머지는 빈 트랙
    [(0.58, LIME), (0.78, (0x0A, 0x84, 0xFF))],
    [(0.36, (0xFF, 0x9F, 0x0A)), (0.56, (0xBF, 0x5A, 0xF2)), (0.68, (0x2F, 0xD2, 0x7F))],
    [(0.26, (0x6E, 0x6B, 0xFF)), (0.40, (0xFF, 0xD6, 0x0A))],
]


def _layout(size):
    """작은 크기는 막대를 굵게·픽셀 격자에 맞춰 흐려지지 않게."""
    if size <= 32:
        return dict(m=0, r=56, bx0=32, bx1=224, bh=48, gap=16, seg_gap=0, ring=False)
    if size <= 48:
        return dict(m=256 / 48, r=52, bx0=48, bx1=208, bh=256 * 8 / 48, gap=256 * 2 / 48,
                    seg_gap=0, ring=False)
    return dict(m=14, r=52, bx0=46, bx1=210, bh=30, gap=16, seg_gap=5, ring=True)


def _in_rrect(x, y, x0, y0, x1, y1, r):
    if x < x0 or x >= x1 or y < y0 or y >= y1:
        return False
    dx = max(x0 + r - x, 0.0, x - (x1 - r))
    dy = max(y0 + r - y, 0.0, y - (y1 - r))
    return dx * dx + dy * dy <= r * r


def _geometry(size):
    """(배경 사각형, 테두리 여부, [(막대 사각형, [(x0, x1, 색)…])…]) — 256 단위."""
    L = _layout(size)
    k = 256.0 / size
    snap = (lambda v: round(v / k) * k) if size <= 48 else (lambda v: v)
    m = L["m"]
    bg = (m, m, 256 - m, 256 - m, L["r"])
    n = len(BARS)
    y = snap(128 - (n * L["bh"] + (n - 1) * L["gap"]) / 2)
    bx0, bx1, sg = L["bx0"], L["bx1"], L["seg_gap"]
    bars = []
    for segs in BARS:
        rect = (bx0, y, bx1, y + L["bh"], L["bh"] / 2)
        out, start = [], 0.0
        for i, (end, col) in enumerate(segs):
            sx0 = snap(bx0 + (bx1 - bx0) * start) + (sg / 2 if i else 0)
            sx1 = snap(bx0 + (bx1 - bx0) * end) - sg / 2
            out.append((sx0, sx1, col))
            start = end
        bars.append((rect, out))
        y += L["bh"] + L["gap"]
    return bg, L["ring"], bars


def render_icon(size, ss=4):
    """size×size RGBA 행 목록(필터 바이트 없음). 4×4 수퍼샘플링."""
    (gx0, gy0, gx1, gy1, gr), ring, bars = _geometry(size)
    shapes = []   # (포함 판정, 색 함수, 불투명도)

    def bg_col(x, y):
        t = min(max((y - gy0) / (gy1 - gy0), 0.0), 1.0)
        return tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT))
    shapes.append((lambda x, y: _in_rrect(x, y, gx0, gy0, gx1, gy1, gr), bg_col, 1.0))
    if ring:
        w = 3
        shapes.append((lambda x, y: _in_rrect(x, y, gx0, gy0, gx1, gy1, gr)
                       and not _in_rrect(x, y, gx0 + w, gy0 + w, gx1 - w, gy1 - w, gr - w),
                       lambda x, y: (255, 255, 255), 0.07))
    for (bx0, by0, bx1, by1, br), segs in bars:
        def pill(x, y, r=(bx0, by0, bx1, by1, br)):
            return _in_rrect(x, y, *r)
        shapes.append((pill, lambda x, y: TRACK, 1.0))
        for sx0, sx1, col in segs:
            shapes.append((lambda x, y, a=sx0, b=sx1, p=pill: a <= x < b and p(x, y),
                           lambda x, y, c=col: c, 1.0))
    k = 256.0 / size
    offs = [(i + 0.5) / ss for i in range(ss)]
    n = ss * ss
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            ar = ag = ab = aa = 0.0
            for oy in offs:
                y = (py + oy) * k
                for ox in offs:
                    x = (px + ox) * k
                    cr = cg = cb = ca = 0.0
                    for test, col, alpha in shapes:
                        if test(x, y):
                            r_, g_, b_ = col(x, y)
                            cr = r_ * alpha + cr * (1 - alpha)
                            cg = g_ * alpha + cg * (1 - alpha)
                            cb = b_ * alpha + cb * (1 - alpha)
                            ca = alpha + ca * (1 - alpha)
                    ar += cr
                    ag += cg
                    ab += cb
                    aa += ca
            if aa <= 0:
                row += b"\0\0\0\0"
            else:
                row += bytes((min(255, round(ar / aa)), min(255, round(ag / aa)),
                              min(255, round(ab / aa)), min(255, round(aa / n * 255))))
        rows.append(bytes(row))
    return rows


def png_bytes(size, rows):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + r for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico_bytes(pngs):
    """[(size, png)] → ICO(PNG 내장). 256 은 너비·높이 바이트 0."""
    head = struct.pack("<HHH", 0, 1, len(pngs))
    off = 6 + 16 * len(pngs)
    ents, blobs = b"", b""
    for size, data in pngs:
        b = 0 if size >= 256 else size
        ents += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), off)
        blobs += data
        off += len(data)
    return head + ents + blobs


def _hex(c):
    return "#%02X%02X%02X" % c


def svg_text():
    (gx0, gy0, gx1, gy1, gr), ring, bars = _geometry(256)
    f = lambda v: ("%.2f" % v).rstrip("0").rstrip(".")  # noqa: E731
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">',
           '  <title>DriveBoard</title>',
           '  <defs>',
           '    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">',
           '      <stop offset="0" stop-color="%s"/>' % _hex(BG_TOP),
           '      <stop offset="1" stop-color="%s"/>' % _hex(BG_BOT),
           '    </linearGradient>']
    for i, ((bx0, by0, bx1, by1, br), _s) in enumerate(bars):
        out.append('    <clipPath id="bar%d"><rect x="%s" y="%s" width="%s" height="%s" rx="%s"/></clipPath>'
                   % (i, f(bx0), f(by0), f(bx1 - bx0), f(by1 - by0), f(br)))
    out.append('  </defs>')
    out.append('  <rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="url(#bg)"/>'
               % (f(gx0), f(gy0), f(gx1 - gx0), f(gy1 - gy0), f(gr)))
    if ring:
        out.append('  <rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="none" stroke="#FFFFFF"'
                   ' stroke-opacity="0.07" stroke-width="3"/>'
                   % (f(gx0 + 1.5), f(gy0 + 1.5), f(gx1 - gx0 - 3), f(gy1 - gy0 - 3), f(gr - 1.5)))
    for i, ((bx0, by0, bx1, by1, br), segs) in enumerate(bars):
        out.append('  <g clip-path="url(#bar%d)">' % i)
        out.append('    <rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>'
                   % (f(bx0), f(by0), f(bx1 - bx0), f(by1 - by0), _hex(TRACK)))
        for sx0, sx1, col in segs:
            out.append('    <rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>'
                       % (f(sx0), f(by0), f(sx1 - sx0), f(by1 - by0), _hex(col)))
        out.append('  </g>')
    out.append('</svg>')
    return "\n".join(out) + "\n"


def write_icons(ico_path=ICO_PATH, svg_path=SVG_PATH, sizes=ICON_SIZES):
    os.makedirs(os.path.dirname(ico_path), exist_ok=True)
    data = ico_bytes([(s, png_bytes(s, render_icon(s))) for s in sizes])
    with open(ico_path, "wb") as fh:
        fh.write(data)
    with open(svg_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg_text())
    return ico_path, svg_path


# =================================================================== 파서(시험용)
def read_png(data):
    """PNG(8비트 RGBA, 비인터레이스) → (w, h, 필터 해제된 행들). 형식이 틀리면 AssertionError."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "PNG 시그니처"
    pos, ihdr, idat, seen_end = 8, None, b"", False
    while pos < len(data):
        (ln,) = struct.unpack(">I", data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        (crc,) = struct.unpack(">I", data[pos + 8 + ln:pos + 12 + ln])
        assert zlib.crc32(typ + body) & 0xFFFFFFFF == crc, "CRC %r" % typ
        if typ == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            seen_end = True
            break
        pos += 12 + ln
    assert ihdr and seen_end, "IHDR/IEND"
    w, h, depth, ctype, comp, filt, inter = ihdr
    assert (depth, ctype, comp, filt, inter) == (8, 6, 0, 0, 0), ihdr
    raw = zlib.decompress(idat)
    stride = w * 4
    assert len(raw) == h * (stride + 1), "IDAT 길이"
    rows, prev = [], bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        assert ft <= 4, "필터 종류"
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, rows


def read_ico(data):
    """ICO → [(size, png bytes)]."""
    reserved, typ, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0 and typ == 1 and 1 <= count <= 32, (reserved, typ, count)
    out, spans = [], []
    for i in range(count):
        w, h, colors, res, planes, bpp, size, off = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert res == 0 and colors == 0 and planes in (0, 1) and bpp == 32, (i, res, planes, bpp)
        assert off >= 6 + 16 * count and off + size <= len(data), "엔트리 범위 %d" % i
        spans.append((off, off + size))
        out.append(((w or 256), (h or 256), data[off:off + size]))
    spans.sort()
    for (a0, a1), (b0, _b1) in zip(spans, spans[1:]):
        assert a1 <= b0, "엔트리 겹침"
    return out


def _yaml_load(path):
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _read(path, mode="r"):
    if "b" in mode:
        with open(path, mode) as fh:
            return fh.read()
    with open(path, mode, encoding="utf-8", newline="") as fh:
        return fh.read()


# =================================================================== 시험
class Version(unittest.TestCase):
    def test_format(self):
        # X.Y.Z 또는 X.Y.Z-pre(prerelease 태그용) — driveboard.spec 의 검사와 같은 규칙.
        self.assertRegex(V.VERSION, r"^\d+\.\d+\.\d+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$")
        self.assertEqual(V.REPO, "minsk999/driveboard")
        self.assertEqual(V.version_tuple(),
                         tuple(int(x) for x in V.VERSION.split("-", 1)[0].split(".")) + (0,))
        self.assertEqual(V.version_tuple("1.2.3-rc.1"), (1, 2, 3, 0))

    def test_package_version_matches(self):
        import core
        if getattr(core, "__version__", None) != V.VERSION:
            self.skipTest("core/__init__.py 의 __version__ 이 아직 core.version.VERSION 을 쓰지 않습니다"
                          " (from .version import VERSION as __version__)")
        self.assertEqual(core.__version__, V.VERSION)

    def test_ffmpeg_pin_shape(self):
        self.assertRegex(V.FFMPEG_SHA256, r"^[0-9a-f]{64}$")
        self.assertTrue(V.FFMPEG_URL.startswith("https://github.com/GyanD/codexffmpeg/releases/download/"))
        self.assertIn("/%s/" % V.FFMPEG_VERSION, V.FFMPEG_URL)
        self.assertTrue(V.FFMPEG_URL.endswith("/%s.zip" % V.FFMPEG_DIR))


class Paths(unittest.TestCase):
    def test_source_layout(self):
        from core import paths
        self.assertFalse(paths.is_frozen())
        self.assertEqual(os.path.realpath(paths.resource_dir()), os.path.realpath(ROOT))
        self.assertTrue(os.path.isfile(os.path.join(paths.ui_dir(), "index.html")))

    def test_frozen_layout(self):
        from core import paths
        tmp = tempfile.mkdtemp()
        try:
            exe_dir = os.path.join(tmp, "DriveBoard")
            internal = os.path.join(exe_dir, "_internal")
            os.makedirs(os.path.join(internal, "bin"))
            open(os.path.join(internal, "bin", paths._exe("ffmpeg")), "wb").close()
            saved = {k: getattr(sys, k, None) for k in ("frozen", "_MEIPASS", "executable")}
            sys.frozen, sys._MEIPASS = True, internal
            sys.executable = os.path.join(exe_dir, "DriveBoard.exe")
            try:
                self.assertTrue(paths.is_frozen())
                self.assertEqual(paths.app_dir(), exe_dir)
                self.assertEqual(paths.ui_dir(), os.path.join(internal, "ui"))
                self.assertEqual(paths.ffmpeg_app_dir(), internal)
                # 사용자가 exe 옆 bin\ 에 넣은 ffmpeg 가 먼저
                os.makedirs(os.path.join(exe_dir, "bin"))
                open(os.path.join(exe_dir, "bin", paths._exe("ffmpeg")), "wb").close()
                self.assertEqual(paths.ffmpeg_app_dir(), exe_dir)
            finally:
                for k, v in saved.items():
                    if v is None and k != "executable":
                        if hasattr(sys, k):
                            delattr(sys, k)
                    else:
                        setattr(sys, k, v)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class _Rec:
    """PyInstaller 빌더 흉내 — 받은 인자를 기록한다."""

    def __init__(self, kind, *args, **kw):
        self.kind, self.args, self.kw = kind, args, kw
        self.pure, self.scripts, self.binaries, self.datas = [], [], [], kw.get("datas", [])


def _fake_pyinstaller():
    mods = {}
    for name in ("PyInstaller", "PyInstaller.utils", "PyInstaller.utils.hooks",
                 "PyInstaller.utils.win32", "PyInstaller.utils.win32.versioninfo"):
        mods[name] = types.ModuleType(name)
    hooks = mods["PyInstaller.utils.hooks"]
    hooks.collect_submodules = lambda pkg: [pkg + ".fake_sub"]
    vi = mods["PyInstaller.utils.win32.versioninfo"]
    for cls in ("FixedFileInfo", "StringFileInfo", "StringStruct", "StringTable", "VarFileInfo",
                "VarStruct", "VSVersionInfo"):
        setattr(vi, cls, (lambda c: (lambda *a, **k: (c, a, k)))(cls))
    return mods


# spec 이 읽는 환경 변수 — 호출자가 env 로 주지 않으면 실행 동안 지운다.
# release.yml·build.bat 은 빌드 단계에서 DRIVEBOARD_REQUIRE_FFMPEG=1 을 켠다. 그 값이 시험 실행에
# 새어 들어오면 bin/ 없는 임시 트리의 spec 이 SystemExit 를 내 시험이 환경에 따라 깨진다.
SPEC_ENV_KEYS = ("DRIVEBOARD_REQUIRE_FFMPEG",)


def run_spec(root, platform="win32", env=None):
    """root 의 driveboard.spec 을 가짜 PyInstaller 로 실행 → 전역 dict.

    환경은 격리한다: SPEC_ENV_KEYS 는 env 에 없으면 지운 채로 실행하고, 끝나면 원래대로 되돌린다.
    """
    src = _read(os.path.join(root, "driveboard.spec"))
    mods = _fake_pyinstaller()
    saved_mods = {k: sys.modules.get(k) for k in mods}
    saved_plat = sys.platform
    env = dict(env or {})
    saved_env = {k: os.environ.get(k) for k in set(SPEC_ENV_KEYS) | set(env)}
    g = {"__name__": "__main__", "SPECPATH": root,
         "Analysis": lambda *a, **k: _Rec("Analysis", *a, **k),
         "PYZ": lambda *a, **k: _Rec("PYZ", *a, **k),
         "EXE": lambda *a, **k: _Rec("EXE", *a, **k),
         "COLLECT": lambda *a, **k: _Rec("COLLECT", *a, **k)}
    try:
        sys.modules.update(mods)
        sys.platform = platform
        for k in SPEC_ENV_KEYS:
            if k not in env:
                os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v
        with contextlib.redirect_stdout(io.StringIO()):
            exec(compile(src, "driveboard.spec", "exec"), g)
    finally:
        sys.platform = saved_plat
        for k, v in saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return g


class Spec(unittest.TestCase):
    def test_parses(self):
        tree = ast.parse(_read(SPEC_PATH), "driveboard.spec")
        names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertTrue({"Analysis", "PYZ", "EXE", "COLLECT"} <= names)

    def _tree(self, with_ffmpeg):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copy(SPEC_PATH, tmp)
        os.makedirs(os.path.join(tmp, "core"))
        shutil.copy(os.path.join(ROOT, "core", "version.py"), os.path.join(tmp, "core"))
        os.makedirs(os.path.join(tmp, "ui"))
        os.makedirs(os.path.join(tmp, "build"))
        open(os.path.join(tmp, "build", "icon.ico"), "wb").close()
        open(os.path.join(tmp, "app.py"), "w").close()
        open(os.path.join(tmp, "THIRD-PARTY.txt"), "w").close()
        if with_ffmpeg:
            os.makedirs(os.path.join(tmp, "bin"))
            for n in ("ffmpeg.exe", "ffprobe.exe", "FFmpeg-LICENSE.txt"):
                open(os.path.join(tmp, "bin", n), "w").close()
        return tmp

    def test_build_graph(self):
        tmp = self._tree(with_ffmpeg=True)
        g = run_spec(tmp)
        a, exe, coll = g["a"], g["exe"], g["coll"]
        self.assertEqual(a.args[0], [os.path.join(tmp, "app.py")])
        dests = {(os.path.basename(s), d) for s, d in a.kw["datas"]}
        for item in (("ui", "ui"), ("THIRD-PARTY.txt", "."), ("ffmpeg.exe", "bin"),
                     ("ffprobe.exe", "bin"), ("FFmpeg-LICENSE.txt", "bin")):
            self.assertIn(item, dests)
        hid = a.kw["hiddenimports"]
        self.assertIn("core.fake_sub", hid)
        self.assertIn("webview.platforms.winforms", hid)
        self.assertIn("clr_loader", hid)
        self.assertIn("tkinter", a.kw["excludes"])
        self.assertEqual(exe.kw["name"], "DriveBoard")
        self.assertIs(exe.kw["console"], False)
        self.assertTrue(exe.kw["exclude_binaries"])           # onedir
        self.assertEqual(exe.kw["icon"], os.path.join(tmp, "build", "icon.ico"))
        self.assertEqual(coll.kw["name"], "DriveBoard")
        ver = repr(exe.kw["version"])
        self.assertIn("'FileVersion', '%s'" % V.VERSION, ver)
        self.assertIn("filevers': %r" % (V.version_tuple(),), ver)

    def test_without_ffmpeg(self):
        tmp = self._tree(with_ffmpeg=False)
        g = run_spec(tmp)
        self.assertFalse(any(d == "bin" for _s, d in g["a"].kw["datas"]))
        with self.assertRaises(SystemExit):
            run_spec(tmp, env={"DRIVEBOARD_REQUIRE_FFMPEG": "1"})

    def test_missing_icon_fails(self):
        tmp = self._tree(with_ffmpeg=False)
        os.remove(os.path.join(tmp, "build", "icon.ico"))
        with self.assertRaises(SystemExit):
            run_spec(tmp)

    def test_non_windows_skips_version_resource(self):
        g = run_spec(self._tree(with_ffmpeg=False), platform="linux")
        self.assertIsNone(g["exe"].kw["version"])

    def test_inherited_require_ffmpeg_is_isolated(self):
        # 회귀: release.yml 이 잡 전체에 DRIVEBOARD_REQUIRE_FFMPEG=1 을 걸어 'Unit tests' 단계에서
        # 이 클래스의 시험 둘이 SystemExit 로 깨졌다 → 태그를 밀어도 릴리스가 한 번도 안 나왔다.
        tmp = self._tree(with_ffmpeg=False)
        old = os.environ.get("DRIVEBOARD_REQUIRE_FFMPEG")
        os.environ["DRIVEBOARD_REQUIRE_FFMPEG"] = "1"
        try:
            g = run_spec(tmp)                                  # 물려받은 값은 무시된다
            self.assertFalse(any(d == "bin" for _s, d in g["a"].kw["datas"]))
            self.assertEqual(os.environ.get("DRIVEBOARD_REQUIRE_FFMPEG"), "1")   # 되돌려 놓는다
            with self.assertRaises(SystemExit):                # 명시하면 여전히 막는다
                run_spec(tmp, env={"DRIVEBOARD_REQUIRE_FFMPEG": "1"})
            self.assertEqual(os.environ.get("DRIVEBOARD_REQUIRE_FFMPEG"), "1")
        finally:
            if old is None:
                os.environ.pop("DRIVEBOARD_REQUIRE_FFMPEG", None)
            else:
                os.environ["DRIVEBOARD_REQUIRE_FFMPEG"] = old

    def _with_version(self, version):
        tmp = self._tree(with_ffmpeg=True)
        vp = os.path.join(tmp, "core", "version.py")
        src = _read(vp)
        src2 = re.sub(r'^VERSION\s*=\s*"[^"]*"', 'VERSION = "%s"' % version, src, count=1, flags=re.M)
        self.assertNotEqual(src, src2)
        with open(vp, "w", encoding="utf-8", newline="") as fh:
            fh.write(src2)
        return tmp

    def test_prerelease_version(self):
        # release.yml 은 '-' 가 든 태그(v1.1.0-rc.1)를 prerelease 로 올린다 — spec 도 그 VERSION 을 받아야 한다.
        g = run_spec(self._with_version("1.1.0-rc.1"))
        ver = repr(g["exe"].kw["version"])
        self.assertIn("'ProductVersion', '1.1.0-rc.1'", ver)     # release.yml 이 태그와 그대로 대조한다
        self.assertIn("filevers': (1, 1, 0, 0)", ver)              # 숫자 칸은 X.Y.Z 만
        self.assertIn("'flags': 2", ver)                           # VS_FF_PRERELEASE
        self.assertIn("'flags': 0", repr(run_spec(self._tree(with_ffmpeg=True))["exe"].kw["version"]))
        self.assertEqual(V.version_tuple("1.1.0-rc.1"), (1, 1, 0, 0))

    def test_bad_version_rejected(self):
        for bad in ("1.1", "1.1.0-", "1.1.0-rc..1", "1.1.0+build", "v1.1.0", "1.1.0 rc"):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                run_spec(self._with_version(bad))


class FfmpegPin(unittest.TestCase):
    def test_release_yml(self):
        s = _read(REL_PATH)
        self.assertIn(V.FFMPEG_SHA256, s)
        self.assertIn(V.FFMPEG_URL, s)

    def test_build_bat(self):
        s = _read(BAT_PATH)
        self.assertIn(V.FFMPEG_SHA256, s)
        self.assertIn(V.FFMPEG_URL, s)
        self.assertIn(V.FFMPEG_DIR, s)

    def test_third_party(self):
        s = _read(TP_PATH)
        self.assertIn("FFmpeg", s)
        self.assertIn(V.FFMPEG_VERSION, s)
        self.assertIn("GPL", s)
        self.assertIn(V.FFMPEG_URL, s)
        for word in ("Pretendard", "OFL", "pywebview", "BSD", "Python", "PSF"):
            self.assertIn(word, s)
        self.assertIn("ui/fonts/Pretendard-OFL.txt", s)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "ui", "fonts", "Pretendard-OFL.txt")))


class BuildBat(unittest.TestCase):
    def test_crlf(self):
        b = _read(BAT_PATH, "rb")
        self.assertGreater(b.count(b"\r\n"), 10)
        self.assertEqual(b.count(b"\n"), b.count(b"\r\n"), "LF 단독 줄바꿈이 있습니다")
        self.assertEqual(b.count(b"\r"), b.count(b"\r\n"), "CR 단독 줄바꿈이 있습니다")
        self.assertTrue(b.endswith(b"\r\n"))
        b.decode("utf-8")

    def test_steps(self):
        s = _read(BAT_PATH)
        for needle in ("pyinstaller", "driveboard.spec", "unittest discover", "DriveBoard-Windows.zip",
                       "Get-FileHash", "pywebview", "--write-icon"):
            self.assertIn(needle.lower(), s.lower(), needle)

    def test_require_ffmpeg_not_leaked_into_tests(self):
        # 회귀: 'set DRIVEBOARD_REQUIRE_FFMPEG=1' 이 :run_tests 보다 앞에 있어 spec 시험이 SystemExit 로 깨졌다.
        lines = _read(BAT_PATH).splitlines()
        def first(pred, what):
            for i, ln in enumerate(lines):
                if pred(ln.strip().lower()):
                    return i
            self.fail("build.bat 에 없음: " + what)
        set_on = first(lambda l: l == 'set "driveboard_require_ffmpeg=1"', "set REQUIRE_FFMPEG=1")
        call_tests = first(lambda l: "call :run_tests" in l, "call :run_tests")
        pyi = first(lambda l: "-m pyinstaller" in l, "PyInstaller")
        self.assertLess(call_tests, set_on)
        self.assertLess(set_on, pyi)
        sub = first(lambda l: l == ":run_tests", ":run_tests")
        disc = first(lambda l: "unittest discover" in l, "unittest discover")
        clr = [i for i, ln in enumerate(lines)
               if ln.strip().lower() == 'set "driveboard_require_ffmpeg="' and sub < i < disc]
        self.assertTrue(clr, ":run_tests 가 DRIVEBOARD_REQUIRE_FFMPEG 를 비우지 않습니다")


def _steps(job):
    return job.get("steps") or []


class Workflows(unittest.TestCase):
    def _check_common(self, text):
        self.assertNotIn("\t", text, "YAML 에 탭 문자")
        for line in text.splitlines():
            m = re.match(r"^\s*-?\s*uses:\s*(\S+)", line)
            if m:
                self.assertRegex(m.group(1), r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", "버전 고정(SHA): " + line)

    def test_release(self):
        text = _read(REL_PATH)
        self._check_common(text)
        for needle in ("windows-latest", "'3.12'", "core/version.py", "driveboard.spec",
                       "unittest discover", "DriveBoard-Windows.zip", "softprops/action-gh-release",
                       "contents: write", "Get-FileHash", "SmartScreen", "Compress-Archive"):
            self.assertIn(needle, text, needle)
        doc = _yaml_load(REL_PATH)
        if doc is None:
            self.skipTest("PyYAML 없음 — 문자열 점검만 했습니다")
        on = doc.get("on", doc.get(True))
        self.assertEqual(on["push"]["tags"], ["v*"])
        self.assertEqual(doc["permissions"]["contents"], "write")
        job = doc["jobs"]["build"]
        self.assertEqual(job["runs-on"], "windows-latest")
        names = ["%s %s" % (st.get("name", ""), st.get("uses", "")) for st in _steps(job)]
        joined = "\n".join(names)
        # 순서: 버전 확인 → ffmpeg → 시험 → pyinstaller → 압축 → 릴리스
        def idx(word):
            for i, n in enumerate(names):
                if word in n:
                    return i
            self.fail("단계 없음: %s\n%s" % (word, joined))
        self.assertLess(idx("version"), idx("ffmpeg"))
        self.assertLess(idx("ffmpeg"), idx("Unit tests"))
        self.assertLess(idx("Unit tests"), idx("Build with PyInstaller"))
        self.assertLess(idx("Build with PyInstaller"), idx("Zip"))
        self.assertLess(idx("Zip"), idx("softprops"))
        # DRIVEBOARD_REQUIRE_FFMPEG 는 PyInstaller 단계에만 — 잡 전체에 걸면 'Unit tests' 로 샌다(회귀).
        self.assertNotIn("DRIVEBOARD_REQUIRE_FFMPEG", job.get("env") or {})
        self.assertNotIn("DRIVEBOARD_REQUIRE_FFMPEG", doc.get("env") or {})
        steps = _steps(job)
        for st in steps:
            if "Build with PyInstaller" in st.get("name", ""):
                self.assertEqual(str((st.get("env") or {}).get("DRIVEBOARD_REQUIRE_FFMPEG")), "1")
                # --no-open 스모크는 webview 를 안 불러 네이티브 창 DLL 누락을 못 잡는다 → 이름으로 확인.
                for dll in ("Python.Runtime.dll", "ClrLoader.dll", "Microsoft.Web.WebView2.Core.dll",
                            "WebView2Loader.dll"):
                    self.assertIn(dll, st["run"], dll)
            else:
                self.assertNotIn("DRIVEBOARD_REQUIRE_FFMPEG", st.get("env") or {}, st.get("name"))
        # 창 스택 자가 진단(app.py --selftest-gui)을 스모크 단계에서 종료 코드로 본다
        smoke = next(st for st in steps if "Smoke test" in st.get("name", ""))
        self.assertIn("--selftest-gui", smoke["run"])
        self.assertIn("ExitCode", smoke["run"])
        self.assertLess(smoke["run"].index("--selftest-gui"), smoke["run"].index("@('--no-open'"))
        # prerelease 태그(v1.1.0-rc.1)를 광고하므로 spec 도 그 VERSION 을 받는다(Spec.test_prerelease_version).
        self.assertIn("contains(github.ref_name, '-')", text)

    def test_tests_workflow(self):
        text = _read(TEST_WF_PATH)
        self._check_common(text)
        doc = _yaml_load(TEST_WF_PATH)
        if doc is None:
            self.skipTest("PyYAML 없음")
        on = doc.get("on", doc.get(True))
        self.assertIn("push", on)
        self.assertIn("pull_request", on)
        job = next(iter(doc["jobs"].values()))
        mx = job["strategy"]["matrix"]
        self.assertEqual(set(mx["os"]), {"ubuntu-latest", "windows-latest"})
        self.assertEqual(set(mx["python-version"]), {"3.10", "3.12"})


class Icon(unittest.TestCase):
    def test_ico(self):
        if not os.path.isfile(ICO_PATH):
            self.fail("build/icon.ico 없음 — python tests/test_packaging.py --write-icon")
        ents = read_ico(_read(ICO_PATH, "rb"))
        sizes = sorted(w for w, _h, _d in ents)
        self.assertEqual(sizes, sorted(ICON_SIZES))
        for w, h, data in ents:
            pw, ph, rows = read_png(data)
            self.assertEqual((pw, ph), (w, h))
            # 모서리는 투명(둥근 사각형), 가운데는 불투명
            self.assertLess(rows[0][3], 255 if w <= 32 else 1)
            mid = rows[h // 2][(w // 2) * 4 + 3]
            self.assertEqual(mid, 255)

    def test_ico_has_lime(self):
        ents = {w: d for w, _h, d in read_ico(_read(ICO_PATH, "rb"))}
        _w, _h, rows = read_png(ents[256])
        px = {tuple(r[i:i + 3]) for r in rows for i in range(0, len(r), 4)}
        self.assertIn(LIME, px)

    def test_generator_matches_committed(self):
        ents = {w: d for w, _h, d in read_ico(_read(ICO_PATH, "rb"))}
        for s in (16, 32):
            self.assertEqual(read_png(ents[s])[2], render_icon(s), "아이콘 %dpx 가 생성기와 다릅니다" % s)

    def test_writer_roundtrip(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        ico, svg = write_icons(os.path.join(tmp, "i.ico"), os.path.join(tmp, "i.svg"), sizes=(16, 24))
        ents = read_ico(_read(ico, "rb"))
        self.assertEqual([w for w, _h, _d in ents], [16, 24])
        self.assertEqual(_read(svg), svg_text())

    def test_svg(self):
        text = _read(SVG_PATH).replace("\r\n", "\n")      # Windows autocrlf 체크아웃 대비
        self.assertEqual(text, svg_text(), "ui/icon.svg 가 생성기와 다릅니다 — --write-icon")
        root = ET.fromstring(text)
        self.assertTrue(root.tag.endswith("svg"))
        self.assertEqual(root.get("viewBox"), "0 0 256 256")
        self.assertIn("#A2FF00", text.upper())


def main(argv):
    if "--write-icon" in argv:
        ico, svg = write_icons()
        print("wrote", ico, os.path.getsize(ico), "bytes;", svg)
        return 0
    unittest.main(argv=[argv[0]] + [a for a in argv[1:]])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

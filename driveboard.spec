# -*- mode: python ; coding: utf-8 -*-
# DriveBoard — PyInstaller onedir 빌드 (SPEC-2 §E)
#   pyinstaller --noconfirm --clean --workpath build\pyi driveboard.spec
#   → dist\DriveBoard\DriveBoard.exe (+ _internal\ui, _internal\bin\ffmpeg.exe)
#
# - 앱은 core/paths.py 로 ui/·bin/ 을 찾는다(소스·배포본 공통).
# - bin\ffmpeg.exe·ffprobe.exe 가 있으면 함께 넣는다(GPL — THIRD-PARTY.txt 동봉).
#   DRIVEBOARD_REQUIRE_FFMPEG=1 이면 없을 때 빌드를 멈춘다(release.yml 이 켠다).
# - 버전 리소스는 core/version.py 에서 만든다(임포트하지 않고 정규식으로 읽는다).
import os
import re
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(SPECPATH)  # noqa: F821  (PyInstaller 가 넣어 주는 전역)
IS_WIN = sys.platform.startswith("win")


def _read_version():
    with open(os.path.join(ROOT, "core", "version.py"), encoding="utf-8") as f:
        src = f.read()
    vals = {}
    for key in ("VERSION", "APP_NAME", "COMPANY", "COPYRIGHT", "DESCRIPTION"):
        m = re.search(r'^%s\s*=\s*"([^"]*)"' % key, src, re.M)
        if not m:
            raise SystemExit("driveboard.spec: core/version.py 에 %s 가 없습니다" % key)
        vals[key] = m.group(1)
    # X.Y.Z 또는 X.Y.Z-pre(점으로 나눈 영숫자 식별자) — release.yml 은 '-' 가 든 태그를 prerelease 로 올린다.
    # '+빌드' 꼬리는 받지 않는다(태그·ProductVersion 대조가 문자열 그대로라 의미가 없다).
    if not re.match(r"^\d+\.\d+\.\d+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$", vals["VERSION"]):
        raise SystemExit("driveboard.spec: VERSION 형식이 X.Y.Z(-pre) 가 아닙니다: %r" % vals["VERSION"])
    return vals


META = _read_version()
# Windows 버전 리소스의 숫자 칸은 X.Y.Z.0 — prerelease 꼬리는 문자열(FileVersion·ProductVersion)에만 남는다.
VER4 = tuple(int(x) for x in META["VERSION"].split("-", 1)[0].split(".")) + (0,)
APP = META["APP_NAME"]
VS_FF_FLAGS = 0x2 if "-" in META["VERSION"] else 0x0   # VS_FF_PRERELEASE


def _version_resource():
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)
    v = META["VERSION"]
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=VER4, prodvers=VER4, mask=0x3F, flags=VS_FF_FLAGS, OS=0x40004,
                          fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("041204B0", [
                StringStruct("CompanyName", META["COMPANY"]),
                StringStruct("FileDescription", META["DESCRIPTION"]),
                StringStruct("FileVersion", v),
                StringStruct("InternalName", APP),
                StringStruct("LegalCopyright", META["COPYRIGHT"]),
                StringStruct("OriginalFilename", APP + ".exe"),
                StringStruct("ProductName", APP),
                StringStruct("ProductVersion", v),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0412, 1200])]),
        ])


# ---------------------------------------------------------------- 데이터 파일
datas = [(os.path.join(ROOT, "ui"), "ui")]
for name in ("THIRD-PARTY.txt",):
    p = os.path.join(ROOT, name)
    if os.path.isfile(p):
        datas.append((p, "."))

ffmpeg_files = [os.path.join(ROOT, "bin", n) for n in ("ffmpeg.exe", "ffprobe.exe")]
have_ffmpeg = all(os.path.isfile(p) for p in ffmpeg_files)
if have_ffmpeg:
    # binaries 가 아니라 datas 로 — 정적 exe 라 의존성 분석이 필요 없고, 이름·위치를 그대로 둔다.
    datas += [(p, "bin") for p in ffmpeg_files]
    for extra in ("FFmpeg-LICENSE.txt", "FFmpeg-README.txt"):
        p = os.path.join(ROOT, "bin", extra)
        if os.path.isfile(p):
            datas.append((p, "bin"))
elif os.environ.get("DRIVEBOARD_REQUIRE_FFMPEG", "").strip() not in ("", "0"):
    raise SystemExit("driveboard.spec: bin\\ffmpeg.exe·ffprobe.exe 가 없습니다(DRIVEBOARD_REQUIRE_FFMPEG=1)")
else:
    print("driveboard.spec: bin\\ffmpeg.exe 없음 — ffmpeg 없이 빌드합니다(미리보기는 PATH 의 ffmpeg 에 의존)")

# ---------------------------------------------------------------- 숨은 임포트
# core 모듈은 app.py 가 지연 임포트하는 것도 있어 통째로 넣는다.
hiddenimports = collect_submodules("core")
# pywebview 5.x — Windows 백엔드는 guilib 가 실행 중에 고른다(정적 분석이 못 본다).
# 데이터(WebView2 dll·js)는 pywebview 자체 훅과 pyinstaller-hooks-contrib(clr·clr_loader)가 모은다.
hiddenimports += [
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "webview.platforms.mshtml",
    "clr",
    "clr_loader",
    "pythonnet",
    "proxy_tools",
    "bottle",
]

excludes = [
    "tkinter", "_tkinter",
    "webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cocoa",
    "webview.platforms.cef", "webview.platforms.android",
    "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "qtpy",
]

icon_path = os.path.join(ROOT, "build", "icon.ico")
if not os.path.isfile(icon_path):
    raise SystemExit("driveboard.spec: build\\icon.ico 가 없습니다 — python tests/test_packaging.py --write-icon")

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "app.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon_path,
    version=_version_resource() if IS_WIN else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP,
)

"""리소스 경로 — 소스 실행과 PyInstaller 배포본(onedir) 양쪽에서 같은 답을 준다.

- 소스:   <저장소>/ui, <저장소>/bin
- 배포본: <exe 폴더>/_internal/ui (sys._MEIPASS), ffmpeg 는 _internal/bin 또는 <exe 폴더>/bin
  (<exe 폴더>/bin 이 먼저 — 사용자가 ffmpeg 를 직접 바꿔 넣을 수 있게).
"""
from __future__ import annotations

import os
import sys

_SRC_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """사용자가 보는 앱 폴더: 배포본이면 DriveBoard.exe 가 있는 폴더, 소스면 저장소 루트."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _SRC_ROOT


def resource_dir() -> str:
    """번들 리소스(ui/ 등)가 풀린 폴더: 배포본이면 sys._MEIPASS, 소스면 저장소 루트."""
    if is_frozen():
        mp = getattr(sys, "_MEIPASS", None)
        if mp:
            return os.path.abspath(mp)
        return app_dir()
    return _SRC_ROOT


def ui_dir() -> str:
    return os.path.join(resource_dir(), "ui")


def _exe(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def bin_dirs() -> list:
    """ffmpeg 를 찾아볼 bin 폴더 후보(우선순위 순, 중복 제거)."""
    out = []
    for base in (app_dir(), resource_dir()):
        d = os.path.join(base, "bin")
        if d not in out:
            out.append(d)
    return out


def ffmpeg_app_dir() -> str:
    """media.find_ffmpeg(app_dir=...) 에 넘길 폴더 — 그 아래 bin/ffmpeg(.exe) 가 있는 곳.

    후보 중 실제로 ffmpeg 가 있는 첫 곳, 없으면 resource_dir().
    """
    for d in bin_dirs():
        if os.path.isfile(os.path.join(d, _exe("ffmpeg"))):
            return os.path.dirname(d)
    return resource_dir()


_UNBLOCK_EXT = (".dll", ".pyd", ".exe")


def unblock_bundle(root=None, force=False) -> int:
    """인터넷에서 받은 zip 을 탐색기로 풀면 모든 파일에 「인터넷에서 받음」 표식(Zone.Identifier 스트림)이 붙는다.
    .NET 은 그 표식이 있는 DLL 을 거부해 pythonnet 이 「Failed to resolve Python.Runtime.Loader.Initialize」로
    죽고 앱 창이 안 뜬다(시험판 첫 실행에서 실제로 밟음 — CI 러너 파일엔 표식이 없어 자가 점검은 통과했다).
    배포본 폴더의 실행 파일·DLL 에서 그 표식만 지운다 = PowerShell Unblock-File 과 같다. 지운 개수를 돌려준다.
    Windows 배포본에서만 동작한다(force 는 시험용). 실패는 조용히 넘긴다 — 표식 제거는 최선 노력일 뿐이다."""
    if os.name != "nt" or not (force or is_frozen()):
        return 0
    removed = 0
    base = root or app_dir()
    for dp, _dns, fns in os.walk(base):
        for fn in fns:
            if not fn.lower().endswith(_UNBLOCK_EXT):
                continue
            try:
                os.remove(os.path.join(dp, fn) + ":Zone.Identifier")
                removed += 1
            except OSError:
                pass
    return removed

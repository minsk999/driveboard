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

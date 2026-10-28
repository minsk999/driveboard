"""DriveBoard 버전 · 저장소 · 배포 고정값 (SPEC-2 §E).

릴리스 태그 `v<VERSION>` 은 이 값과 같아야 한다(release.yml 이 검사한다).
표준 라이브러리만 쓰고 아무것도 임포트하지 않는다 — driveboard.spec 이 정규식으로도 읽는다.
"""

VERSION = "1.0.0-rc.2"
REPO = "minsk999/driveboard"

APP_NAME = "DriveBoard"
COMPANY = "minsk999"
COPYRIGHT = "Copyright (c) minsk999"
DESCRIPTION = "DriveBoard — 드라이브 정리 도구"

RELEASES_URL = "https://github.com/%s/releases" % REPO
LATEST_RELEASE_URL = RELEASES_URL + "/latest"

# 배포본에 동봉하는 ffmpeg — build.bat · release.yml 과 반드시 같은 값(tests/test_packaging.py 가 대조).
# GPL v3 빌드(gyan.dev essentials). 소스 커밋은 THIRD-PARTY.txt 참고.
FFMPEG_VERSION = "8.0.1"
FFMPEG_URL = ("https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/"
              "ffmpeg-8.0.1-essentials_build.zip")
FFMPEG_SHA256 = "e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673"
FFMPEG_DIR = "ffmpeg-8.0.1-essentials_build"      # zip 안의 최상위 폴더


def version_tuple(v=VERSION):
    """'1.2.3' → (1, 2, 3, 0) — Windows 버전 리소스용(4칸)."""
    parts = []
    for p in str(v).split("-", 1)[0].split("+", 1)[0].split("."):
        parts.append(int(p) if p.isdigit() else 0)
    return tuple((parts + [0, 0, 0, 0])[:4])

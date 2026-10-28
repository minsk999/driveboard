# DriveBoard — Claude Code 작업 안내 (이어받기용)

> 다른 세션이 이 파일부터 읽고 바로 이어 갈 수 있게 쓴 문서다. 세부 계약은 `SPEC.md`(1차)·`SPEC-2.md`(2차, 충돌하면 이쪽이 이긴다),
> 사용법·Windows 점검 목록은 `README.md`, 변경 기록은 `CHANGELOG.md`.

## 무엇인가
영상팀용 Windows 드라이브 정리 앱. 아이폰 「저장 공간」처럼 드라이브별 용량을 카테고리(캐시·렌더·촬영 소스·프록시·프로젝트…)로 보여 주고,
그 자리에서 **휴지통 · 다른 드라이브/외장/NAS 로 이동 · 복사 · (휴지통 없는 곳은) 영구 삭제**를 한다.
**프로젝트 보기**: 팀 파일명 규칙(BRC `날짜_광고주_캠페인_…_작업자`)으로 프로젝트 폴더를 광고주별·시간순·연월별로 묶고,
폴더 안 최신 렌더 영상을 호버 재생 미리보기로 보여 준다(ProRes 등은 동봉 ffmpeg 로 프록시).

구조: Python 표준 라이브러리 HTTP 서버(`app.py`, 127.0.0.1) + pywebview(WebView2) 창 + 바닐라 JS UI(`ui/`).
`core/`: categories·model·scanner·store·settings·projects·brc·media·dupes·recommend·safety·winapi·ops·paths·update·version.

사용자: 영상팀 팀장(After Effects). 한국어, 두괄식·간결, 픽셀·모션에 민감. **UI 문구는 존댓말.**
팀원 PC 는 보통 C(SSD) 하나 또는 C+D(HDD). 팀장 PC 는 내장 7개 + 외장 + ORICO NAS(SMB).

## 현재 상태 (2026-10-28)
- 저장소 `minsk999/driveboard`(**공개**), 브랜치 `main`. 단위 시험 ~410개 Ubuntu·Windows × Py3.10·3.12 통과.
- **시험판 `v1.0.0-rc.2` 릴리스됨**(prerelease → 앱 업데이트 알림에는 안 뜬다). 팀장이 아직 실제 PC 에서 처음 실행 확인 중.
  - rc.1 은 받은 zip 을 탐색기로 풀면 DLL 의 Mark-of-the-Web 때문에 pythonnet 이 죽었다 → rc.2 에서 시작 시 표식 제거
    (`core/paths.unblock_bundle`) + `DriveBoard.exe.config`(loadFromRemoteSources) + 창 실패 시 브라우저로 폴백.
- 개발은 지금까지 **Linux 클라우드 컨테이너**에서 했다 → **Windows API 코드는 사람이 실제로 눌러 본 적이 없다.** 이 세션의 첫 목표가 그 확인이다.

## 다음 할 일 (순서대로)
1. **`python tests\win_selftest.py` 실행** → PASS/FAIL 표를 사용자와 같이 본다(자기가 만든 임시 파일만 다룬다).
   NAS 까지: `--nas <장치이름 또는 IP>`.
2. `README.md` 「Windows 에서 처음 확인할 것」 23개를 사용자와 하나씩 확인. 특히: 드라이브 목록·SSD/HDD·외장 판정, 휴지통 보내기·비우기,
   이동/복사/되돌리기, NAS 공유 찾기, 영상 호버 재생(H.264 와 ProRes 프록시 — 컨테이너의 헤드리스 Chromium 은 H.264 를 못 풀어 webm 으로만 확인했다),
   한 번만 실행, 「폴더 선택…」, 팀 설정 내보내기 다운로드.
3. 실제 사용 데이터로 **분류·프로젝트 판정 품질** 점검(사용자의 실제 폴더 이름 규칙에 맞는지). 결과는 사용자에게 보여 주고 규칙을 다듬는다.
4. 문제가 없으면 `core/version.py` 를 `1.0.0` 으로 → 정식 릴리스 → 팀 공유.

## ★ 데이터 안전 — 이 세션은 실제 드라이브 위에서 돈다
- 휴지통·이동·복사·삭제 시험은 **지금 이 세션에서 직접 만든 테스트 폴더로만** 한다(예: `D:\_DriveBoard_test\`). 사용자 파일·프로젝트 폴더에 손대지 말 것.
- 사용자가 앱에서 직접 누르는 것은 사용자 판단이지만, 처음 몇 번은 테스트 폴더로 하자고 권한다.
- 휴지통 **비우기**는 그 드라이브 휴지통 전체를 영구 삭제한다 — 사용자 확인 없이 절대 실행하지 않는다.
- NAS·USB 메모리에는 휴지통이 없다(삭제 = 영구 삭제).

## 실행·시험 (Windows)
```
pip install pywebview                 # 창(WebView2). 없으면 브라우저로 뜬다
python app.py                         # 실제 드라이브로 실행
python app.py --no-open --port 8790   # 서버만 → 브라우저로 http://127.0.0.1:8790/
python tests\make_fake_drives.py %TEMP%\dbfake --scale demo   # 가짜 드라이브(희소 파일)
python app.py --fake %TEMP%\dbfake --multi                     # 가짜로 실행(실제 드라이브 안전)
python -m unittest discover -s tests
python tests\win_selftest.py
build.bat                             # ffmpeg 받기(sha256) → 시험 → PyInstaller → dist\DriveBoard-Windows.zip
```
- 개발용 창을 띄우는 명령은 프로세스가 안 끝난다 — 백그라운드로 띄우거나 사용자가 직접 실행하게 할 것.
- 앱이 이미 떠 있으면 두 번째 실행은 기존 창을 부르고 끝난다(`--multi` 로 우회).
- 로그·데이터: `%LOCALAPPDATA%\DriveBoard\`(driveboard.log, window-error.log, selftest-gui.log, 인덱스, settings.json, jobs.json).

## 릴리스
- `core/version.py` 의 `VERSION` 을 올리고 커밋·푸시 → GitHub Actions **release → [Run workflow]** 에 `v<VERSION>` 입력
  (또는 로컬에서 `git tag v<VERSION>` + 태그 push — 클라우드에선 태그 push 가 막혀 이 버튼을 만들었다). `-` 가 들어가면 시험판.
- `release.yml` 이 Windows 에서: 버전 대조 → ffmpeg(버전·sha256 고정) → 단위 시험 → PyInstaller → DLL 존재 확인 →
  **Mark-of-the-Web 흉내 후 `--selftest-gui`** → 서버 기동 확인 → zip·sha256 → 릴리스.

## 규칙
- **공개 저장소다.** 실제 광고주·프로젝트명·팀원 실명·회사명·사내 경로를 코드·시험 데이터·커밋에 넣지 않는다
  (시험 데이터는 가상 이름: 소담코스·한결증권·한빛은행 등). 작업자 이니셜↔실명은 사용자 설정(팀 설정 파일)에만.
- 커밋 신원: `git config user.name minsk999` / `user.email minsk999@users.noreply.github.com`. 커밋 메시지 한국어.
- 런타임 의존성은 표준 라이브러리 + (선택) pywebview 만. Python 3.10+.
- ctypes 는 `argtypes/restype` 를 항상 명시.
- UI: 네이티브 `alert/confirm/prompt`·`<select>`·`title` 툴팁·`<video controls>` 금지(자체 컴포넌트), 색은 CSS 변수, 외부 네트워크(CDN) 금지.
- 시험 기대값은 손으로 계산해 적는다(함수 출력으로 만들지 않는다). BRC 파서는 `tests/brc_reference.js`(BannerNode 원본)와 node 로 차등 비교.

## 실제로 밟은 함정 (되풀이 금지)
- **Mark-of-the-Web**: 받은 zip 을 탐색기로 풀면 DLL 에 Zone.Identifier → .NET 이 pythonnet DLL 거부. CI 파일엔 표식이 없어 통과했었다.
- **최상위 exe 에 표식** → `Start-Process` 가 보안 경고 창을 띄워 CI 가 영원히 멈춘다(표식 흉내는 `_internal` 만).
- PowerShell: 네이티브 명령을 안 돌렸으면 `$LASTEXITCODE` 는 `$null` → `-ne 0` 이 참. `| Select-Object -First 1` 은 파이프를 끊어 ffmpeg 가 실패 코드로 끝난다.
  `Start-Process -PassThru` 는 `$p.Handle` 을 먼저 잡아야 `ExitCode` 가 비지 않는다.
- NTFS 에선 `truncate` 가 희소 파일을 만들지 않고 0 을 실제로 쓴다 → 시험용 가짜 드라이브가 CI 디스크를 채웠다(`set_sparse_size` 사용).
- 8.3 짧은 이름(`RUNNER~1`)은 거부하지 말고 `GetLongPathNameW` 로 펼쳐서 검사한다.
- 여유 공간 캐시는 작업 「끝」이 아니라 항목마다 비운다(끝남이 보인 뒤 on_job_done 이 불려 2초간 옛 값이 보였다).
- 희소 파일을 이동·복사하면 실제로 0 을 써서 디스크를 채운다 — 이동 시험은 작은 실제 파일로.

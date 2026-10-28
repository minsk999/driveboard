# DriveBoard — 설계 스펙 (정본)

> 영상팀 PC(SSD·HDD 7개, 약 25TB)의 용량을 **아이폰 「저장 공간」처럼** 카테고리별로 보여 주고,
> 그 자리에서 **휴지통으로 보내거나 다른 드라이브로 옮기는** Windows 데스크톱 앱.
> 이 문서가 모듈 간 계약의 정본이다. 구현이 이 문서와 다르면 문서를 고치고 이유를 남긴다.

## 0. 사용자와 환경

- 사용자: 영상팀 팀장(After Effects 중심). 한국어, 두괄식·간결, **픽셀 디테일·모션에 민감**.
- PC: Windows 10/11, NTFS 고정 드라이브 7개(C: 3.63TB SSD … H:/I: 7.27TB HDD). Python 3.10+ 설치됨(다른 사내 앱 개발용).
- 실행: `run.cmd` 더블클릭 → `pythonw app.py`. `pywebview` 가 있으면 네이티브 창(WebView2), 없으면 기본 브라우저.
- 개발·검증 환경은 **Linux 컨테이너**다. Windows API 는 여기서 실행할 수 없다 →
  모든 Windows 전용 코드는 `core/winapi.py` 한 곳에 두고, 비Windows 에서는 **가짜 드라이브(fake) 백엔드**로 같은 인터페이스를 구현한다.
- 런타임 의존성: 표준 라이브러리 + (선택) `pywebview`. **그 밖의 서드파티 금지**(send2trash·psutil·numpy 등 쓰지 않는다).

## 1. 폴더 구조

```
driveboard/
  app.py               진입점: HTTP 서버(127.0.0.1) + pywebview 창 + API 라우팅
  run.cmd / run-dev.cmd
  requirements.txt     pywebview (선택)
  core/
    __init__.py
    categories.py      카테고리 정의 + 분류 규칙(순수 함수)
    model.py           DriveIndex (디렉터리 트리 + 큰 파일 표) + 질의·변경 함수
    scanner.py         드라이브 스캔(드라이브당 스레드 1개), 진행률, 취소
    store.py           인덱스 저장/로드(원자적 쓰기, pickle 금지)
    dupes.py           중복 파일 탐지
    recommend.py       추천 계산
    settings.py        설정 저장/로드
    safety.py          보호 경로 판정
    winapi.py          Windows ctypes + fake 백엔드
    ops.py             작업 큐: 휴지통 / 이동(복사→검증→원본 삭제) / 휴지통 비우기 / 되돌리기 / 기록
  ui/
    index.html  app.css  app.js  treemap.js
    fonts/PretendardVariable.woff2  fonts/Pretendard-OFL.txt
  tests/
    make_fake_drives.py  가짜 드라이브 생성기(희소 파일로 수 TB 흉내)
    test_*.py            unittest(표준 라이브러리만)
```

## 2. 크기·날짜 표기 (탐색기와 같은 숫자여야 한다)

- **1024 진법**, 단위 표기는 탐색기처럼 `KB/MB/GB/TB`. 4TB 드라이브 = `3.63TB`.
  - `< 1KB` → `512바이트` 대신 `0.5KB` 로 통일(`0KB` 는 0 일 때만). 유효숫자 3자리: `3.63TB`, `406GB`, `48.2GB`, `9.81MB`.
- 상대 시간: `방금`, `5분 전`, `3시간 전`, `어제`, `3일 전`, `2주 전`, `8개월 전`, `2년 전`. 툴팁엔 절대 시각 `2025. 3. 12. 14:05`.
- 숫자는 `font-variant-numeric: tabular-nums`.
- 「마지막 수정」은 **mtime** 이다. Windows 는 큰 볼륨에서 마지막 접근 시각(atime) 갱신을 끄므로 atime 을 쓰지 않는다. UI 문구도 「수정한 지」로 쓴다(「사용한 지」 금지).

## 3. 카테고리 (core/categories.py)

`CATEGORIES` = 아래 순서의 리스트. **id 는 리스트 인덱스(0~10)이며 저장 포맷에 들어가므로 바꾸지 않는다.**

| id | key | 이름 | 색(다크 배경용) | deletable | 설명 |
|---|---|---|---|---|---|
| 0 | cache   | 캐시·임시      | `#FF9F0A` | true  | AE 디스크 캐시·미디어 캐시·Temp. 지워도 다시 만들어진다 |
| 1 | render  | 렌더·출력      | `#2FD27F` | true  | 렌더·익스포트·납품본 |
| 2 | source  | 촬영·소스      | `#0A84FF` | true  | 카메라 원본·소스 푸티지 |
| 3 | proxy   | 프록시         | `#30D5C8` | true  | 프록시·저해상도 사본 |
| 4 | video   | 기타 영상      | `#6E6BFF` | true  | 위로 분류되지 않은 영상 |
| 5 | project | 프로젝트       | `#BF5AF2` | true  | aep·prproj·psd·ai·c4d·blend … |
| 6 | image   | 이미지·시퀀스  | `#FF4F8B` | true  | 사진·이미지·이미지 시퀀스 |
| 7 | audio   | 오디오         | `#FFD60A` | true  | wav·mp3·aiff … |
| 8 | archive | 압축·백업      | `#C0A37A` | true  | zip·rar·7z·iso·Auto-Save |
| 9 | app     | 앱·시스템      | `#8E8E93` | **false** | Windows·Program Files·실행 파일 |
| 10| other   | 기타           | `#5C5C63` | true  | 나머지 |

가상 세그먼트(카테고리 아님, UI 막대에만): `bin` 휴지통(`#FF453A`), `unscanned` 「시스템·확인 불가」(사용량 − 스캔 합계 − 휴지통, `#3A3A40` + 사선 해치), `free` 남은 공간(트랙색).

### 3.1 디렉터리 문맥(context)
디렉터리마다 `ctx`(카테고리 id 또는 None)를 부모에게서 물려받고, 자기 이름으로 덮어쓴다. 비교는 **소문자·앞뒤 공백 제거**.

우선순위(높은 것부터):
1. **사용자 규칙** `settings.rules = [{path, cat}]` — 경로가 규칙 경로와 같거나 그 아래면 그 cat. 가장 긴(깊은) 규칙이 이긴다. **사용자 규칙은 절대적**: 그 하위의 모든 파일이 그 카테고리가 된다(확장자 무시).
2. `settings.cache_paths` 의 경로 아래 → cache(절대적, 확장자 무시).
3. 이름 기반 문맥:
   - cache: `media cache files`, `media cache`, `peak files`, `disk cache`, `disk cache - *`, `aecache`, `conformed audio files`, `temp`(부모가 `appdata\local` 일 때만), `$windows.~ws` 제외.
   - archive(백업): `adobe after effects auto-save`, `adobe premiere pro auto-save`, `auto-save`, `autosave`, `backup`, `backups`, `백업`.
   - proxy: `proxy`, `proxies`, `프록시`.
   - render: 정규식 `^(render|renders|rendered|output|outputs|export|exports|deliver|delivery|deliverables|final|finals|out|출력|렌더|납품|완성본|최종)$` 또는 이름이 `render`·`렌더`·`출력`·`납품` 을 **포함**.
   - source: `^(footage|footages|source|sources|src|raw|rush|rushes|원본|소스|촬영|촬영본|camera|cam|dcim|private|m4root|xdroot|avchd|contents|clip|clips)$` 또는 `촬영`·`원본`·`footage` 포함.
   - app(시스템): 드라이브 루트 바로 아래의 `windows`, `program files`, `program files (x86)`, `programdata`, `$windows.~bt`, `windows.old`, `recovery`, `$winreagent`, `boot`, `efi`, 그리고 `users\<이름>\appdata` 아래(단, 위 cache 문맥이 먼저 걸린 곳은 cache).
4. 그 밖: 부모 문맥 유지.

### 3.2 파일 분류 `classify(name, ctx) -> cat_id`
`ext` = 마지막 `.` 뒤 소문자(없으면 ""). 순서대로 첫 일치:
1. ctx 가 사용자 규칙/cache_paths 로 온 **절대 문맥**이면 그 cat.
2. 캐시 확장자 `cfa pek ims mcdb prmdc prfpset? 제외 … ` → 정확히: `{cfa, pek, ims, mcdb, prmdc, aecache, mcache}` → cache.
3. ctx == cache → cache. ctx == app → app.
4. 루트 시스템 파일 `pagefile.sys hiberfil.sys swapfile.sys dumpstack.log.tmp` → app.
5. 영상 확장자 `VIDEO = {mp4, mov, mxf, avi, mkv, m4v, wmv, mts, m2ts, r3d, braw, ari, mpg, mpeg, webm, 3gp, insv, dv, vob, flv, f4v, hevc, h264, h265, prores}`, 그리고 `lrv`(GoPro 저해상도) → proxy:
   - ctx == proxy, 또는 이름(확장자 제외)이 `_proxy`/`-proxy`/` proxy` 로 끝나거나 `_proxy` 포함 → proxy
   - ctx == render → render
   - ctx == source, 또는 RAW 확장자 `{r3d, braw, ari, mxf}`, 또는 카메라 파일명 패턴 → source
     카메라 패턴(대소문자 무시, 확장자 제외한 이름에 전체 일치): `C\d{4}`(Sony), `[A-Z]\d{3}_?C\d{3}.*`(ARRI/RED/BMD), `G[HX]\d{6}`, `GOPR\d{4}`, `DJI_\d{4,}.*`, `MVI_\d{4}`, `IMG_\d{4}`, `PXL_\d{8}_\d+.*`, `VID_\d{8}_\d+.*`, `\d{8}_\d{6}`
   - ctx == archive → archive
   - 이름에 `comp`, `render`, `final`, `export`, `렌더`, `최종`, `완성`, 또는 `_v\d+`/` v\d+` 버전 꼬리 → render
   - 그 밖 → video
6. 이미지 `IMAGE = {jpg, jpeg, png, tif, tiff, exr, dpx, tga, bmp, gif, webp, heic, heif, cr2, cr3, arw, nef, dng, raf, orf, rw2, hdr, psb?는 project}`:
   ctx == render → render, ctx == proxy → proxy, ctx == source → source(사진 원본), 그 밖 → image
7. 프로젝트 `PROJECT = {aep, aepx, aet, prproj, psd, psb, ai, indd, c4d, blend, ma, mb, max, hip, nk, drp, fcpxml, veg, sesx, mogrt, ffx, aex? 아니다(플러그인=app)}` → project
8. 오디오 `AUDIO = {wav, mp3, aac, m4a, aif, aiff, flac, ogg, opus, wma, bwf}` → audio
9. 압축·백업 `ARCHIVE = {zip, rar, 7z, tar, gz, tgz, bz2, xz, iso, dmg, img, bak, cab? 아니다}` → archive; 그리고 ctx == archive → archive
10. 실행·시스템 `APP = {exe, dll, sys, msi, msp, cab, appx, msix, aex, plugin, ocx, drv, cpl}` → app
11. → other

★ 구현은 위 표를 **데이터(세트·정규식 상수)** 로 두고, `?`·「아니다」가 붙은 항목은 무시한다(문서용 메모). 최종 세트는 `categories.py` 에 상수로 박고 이 문서의 표와 일치시킨다.

### 3.3 이미지 시퀀스 묶기
같은 디렉터리에서 이름이 `접두사 + 숫자(3~8자리) + .확장자` 이고 (접두사, 확장자, 숫자 자릿수)가 같은 파일이 **5개 이상**이면 **하나의 항목**으로 묶는다.
- 표시 이름: `접두사[####].ext` (#는 자릿수만큼), `count` = 프레임 수, 범위 `first..last`, 크기 합, mtime 최댓값.
- 카테고리는 첫 프레임 분류값.
- 영상·이미지·기타 어떤 확장자든 적용(EXR·PNG·DPX 렌더 시퀀스가 주 대상). 단 `cfa/pek`(캐시)는 묶지 않는다.

## 4. 인덱스 모델 (core/model.py)

드라이브 하나 = `DriveIndex`. 수백만 파일을 다 들고 있을 수 없으므로:
- **디렉터리 표**(모든 디렉터리): 병렬 배열.
- **큰 항목 표**: `size >= settings.big_file_mb`(기본 **50MB**) 인 파일과, 합계가 그 이상인 **시퀀스 묶음**만 개별 저장. 작은 파일은 디렉터리 합계에만 들어간다.

```python
NCAT = 11
class DriveIndex:
    # 메타
    letter: str          # "D"
    root: str            # "D:\\"  (fake 백엔드에선 실제 디렉터리 경로)
    label: str; serial: str; fs: str
    scanned_at: float; scan_seconds: float
    errors: int          # 접근 불가 디렉터리 수
    error_samples: list[str]   # 최대 20개
    # 디렉터리 표 (id = 인덱스, 0 = 루트)
    d_name:   list[str]            # 루트는 ""
    d_parent: array('i')           # 루트 -1
    d_children: list[list[int]|None]
    d_size:   array('q')           # 재귀 합계(바이트)
    d_files:  array('q')           # 재귀 파일 수(시퀀스 프레임 각각 1로 셈)
    d_newest: array('d')           # 하위 전체 최신 mtime (빈 폴더는 자기 mtime)
    d_cat:    array('q')           # 길이 ndirs*NCAT, 재귀 카테고리별 바이트
    d_alive:  bytearray            # 1=유효, 0=삭제됨(톰스톤)
    # 큰 항목 표
    f_name: list[str]; f_dir: array('i'); f_size: array('q'); f_mtime: array('d')
    f_cat: array('b'); f_count: array('i')   # 1=일반 파일, >1=시퀀스 프레임 수
    f_alive: bytearray
```

필수 함수(이름·시그니처 고정 — app.py 가 이걸 부른다):
- `path_of_dir(idx, did) -> str`, `path_of_file(idx, fid) -> str` (시퀀스는 디렉터리 경로 + 표시 이름; 실제 조작은 `frames_of(idx,fid)`)
- `frames_of(idx, fid) -> list[str]` 시퀀스의 실제 파일 경로 목록(디스크에서 패턴으로 다시 찾음).
- `find_dir(idx, path) -> int|None`, `find_item(idx, path) -> ('dir'|'file', id)|None`
- `cat_totals(idx, did=0) -> list[int]`(길이 NCAT)
- `list_children(idx, did, cat=None, age_days=0, now=None) -> list[dict]` 폴더 보기용. 각 dict: `{kind:'dir'|'file', id, name, path, size, cat_bytes(list), files, newest, cat}`. `cat` 이 주어지면 `size` 대신 정렬·표시 기준이 그 카테고리 바이트(`metric`)가 되고, 그 카테고리가 0인 항목은 빠진다. 디렉터리의 **직속 작은 파일 합계**는 `{kind:'loose', name:'(이 폴더의 작은 파일들)', size, files}` 한 줄로.
- `list_files(idx, did=0, cat=None, age_days=0, q='', sort='size', order='desc', offset=0, limit=200, now=None) -> (items, total_count, total_bytes)` 큰 항목 표에서 서브트리(did) 필터.
- `small_remainder(idx, did, cat) -> (bytes, files)` 목록에 안 나오는 작은 파일 몫(안내 줄용).
- `detach(idx, path) -> int` 파일/디렉터리/시퀀스를 인덱스에서 제거하고 조상 합계를 깎는다. 제거한 바이트 반환.
- `graft(idx, sub: DriveIndex, at_path) -> None` 다른 스캔 결과(서브트리)를 `at_path` 위치에 붙인다(중간 디렉터리 생성, 조상 합계 가산).
- `apply_rule(idx, path, cat) -> None` 서브트리 전체를 cat 으로 재분류(크기 이동만, 재스캔 없음).
- `summary(idx) -> dict` (§7 drive summary 의 스캔 부분)
- **불변식**(테스트로 강제): 어떤 변경 뒤에도 `sum(cat_totals(idx,0)) == d_size[0]`, 각 디렉터리 `d_size == 자기 직속 + 자식 합`.

경로 문자열은 **OS 네이티브**(`D:\Projects\A`). 내부 비교는 `os.path.normcase(os.path.normpath())`.

## 5. 스캔 (core/scanner.py)

- `start_scan(drive_info, settings, progress_cb) -> ScanHandle` / `handle.cancel()` / `handle.result -> DriveIndex|None`.
  드라이브당 스레드 1개(서로 다른 물리 디스크라 병렬이 이득). 같은 드라이브 중복 스캔은 거부.
- `scan_subtree(path, ...) -> DriveIndex` 이동 후 대상 쪽 갱신용(동기).
- `os.scandir` 반복 DFS(명시적 스택, 재귀 금지). `entry.stat(follow_symlinks=False)` 로 크기·mtime(Windows 에선 추가 시스템 콜 없음).
- 건너뛰기: `$Recycle.Bin`, `System Volume Information`, `settings.excluded`, **재분석 지점**(심볼릭 링크·정션: `is_symlink()` 또는 Windows `st_file_attributes & 0x400`) — 이중 집계 방지.
- `PermissionError`/`OSError` → `errors += 1`, 샘플 20개까지.
- Windows 긴 경로: 스캔 루트를 `\\?\D:\` 로 시작하고, 저장하는 이름에는 접두사를 남기지 않는다.
- 진행률 콜백(약 0.25초 간격): `{phase:'scan'|'finalize'|'done'|'error'|'canceled', files, dirs, bytes, current, cats(list NCAT 진행 중 합계), started_at}` — UI 가 스캔 중에도 막대가 차오르게 한다.
- 마무리: 자식 → 부모 순으로 재귀 합계 계산(디렉터리 id 가 발견 순이므로 역순 순회로 충분).
- 성능 목표: 컨테이너에서 합성 트리 **100만 파일 / 10만 디렉터리 를 60초 안**에, 메모리 1GB 미만.

## 6. 저장 (core/store.py)

- 위치: Windows `%LOCALAPPDATA%\DriveBoard\`, 그 밖 `~/.driveboard/`. 환경변수 `DRIVEBOARD_HOME` 가 있으면 그곳(테스트용).
- 파일: `index-<serial 또는 letter>.dbi`, `settings.json`, `jobs.json`.
- 인덱스 포맷: **pickle 금지**. 헤더 JSON(버전·메타) + `array.tobytes()` 블록 + 이름은 `\0` 결합 UTF-8, 전체 `zlib` 압축. 버전 불일치·손상 → None(재스캔 유도), 예외로 앱을 죽이지 않는다.
- **원자적 쓰기**: 같은 폴더 임시 파일 → `flush` + `os.fsync` → `os.replace`.

## 7. HTTP API (app.py ↔ ui/app.js 계약)

- 서버는 **127.0.0.1** 에만 바인딩, 포트 8790부터 빈 포트 탐색.
- 모든 `/api/*` 는 JSON. 오류는 `{ok:false, error:'<코드>', message:'<한국어 문장>'}` + 적절한 상태 코드.
- **보안 관문(필수, do_GET/do_POST 맨 앞 한 곳)**:
  1. `Host` 가 `127.0.0.1:<port>` 또는 `localhost:<port>` 가 아니면 403(DNS 리바인딩).
  2. `/api/*` 는 헤더 `X-DB-Token` == 프로세스 토큰(상수 시간 비교) 아니면 403. 토큰은 `/` 를 서빙할 때 HTML `<meta name="db-token" content="…">` 로만 주입.
  3. `Sec-Fetch-Site` 헤더가 있고 `same-origin` 이 아니면 `/api/*` 403(**`none` 도 거부** — 주소창·링크 클릭).
  4. `Origin` 헤더가 있으면 자기 origin 과 같아야 한다.
  5. 정적 파일은 `ui/` 아래 화이트리스트(확장자 html/css/js/woff2/txt/svg/png)만, `..`·절대경로 거부.
  6. 요청 본문 상한 1MB.

### GET /api/meta
`{ok, version, platform:'win'|'fake', categories:[{id,key,name,color,deletable,desc}], virtual:{bin:{name,color}, unscanned:{name,color}}, settings}`

### GET /api/state  (UI 가 0.4초(작업 중) / 3초(평시)마다 폴링)
```
{ok, now,
 drives:[{letter, root, label, serial, fs, kind:'ssd'|'hdd'|'removable'|'unknown',
          total, free, used,
          scanned:bool, scanned_at, scan_seconds,
          cats:[NCAT], scanned_bytes, files, dirs, errors,
          bin_bytes, bin_count, unscanned_bytes,
          stale:bool}],          # stale = 스캔 후 used 가 5% 또는 20GB 넘게 달라짐
 scans:{ "D": {phase, files, dirs, bytes, current, cats, started_at} },
 jobs:[job...],                  # 진행 중 + 최근 끝난 것(최근 10개)
 busy:bool}
```
`unscanned_bytes = max(0, used - scanned_bytes - bin_bytes)`. 스캔 전이면 `cats` 는 0 배열, `unscanned_bytes = used - bin_bytes`.

### POST /api/scan  `{drives:['D','E'] | 'all'}` → `{ok, started:['D','E']}`
### POST /api/scan/cancel `{drive:'D'}`

### GET /api/items
쿼리: `drive=D|*`, `view=files|folders`, `cat=<id>|''`, `dir=<did>`(folders/files 의 기준 디렉터리, 기본 0), `age=0|90|180|365`, `q=`, `sort=size|mtime|name`, `order=desc|asc`, `offset`, `limit(≤500)`.
- `view=folders`, `drive=*` 이면 각 드라이브 루트를 항목으로 돌려준다.
응답:
```
{ok, total, total_bytes, offset,
 crumbs:[{dir, name, path}],             # folders 일 때 루트→현재
 remainder:{bytes, files},               # files 일 때 목록에 안 나오는 작은 파일 몫
 items:[{key, drive, kind:'file'|'seq'|'dir'|'loose', id, name, path, size, metric,
         cat, cat_bytes:[NCAT]|null, files, mtime, count?, first?, last?,
         protected:bool, protected_reason:''}]}
```
`key` = `"<drive>:<kind>:<id>"` (UI 선택 상태 키). `metric` = 정렬·막대 기준(카테고리 필터 시 그 카테고리 바이트).

### GET /api/tree?drive=D&dir=0&depth=2  (트리맵)
`{ok, node:{id,name,path,size,cat,cat_bytes, children:[node...]}}` — 레벨마다 상위 200개, 나머지는 `{name:'기타 N개', size, cat:10, other:true}` 로 묶음. 파일·시퀀스도 자식으로(kind 포함).

### POST /api/inspect `{paths:[...]}`
액션 확인창용. `{ok, items:[{path, kind, size, files, cat_bytes, protected, protected_reason, exists}], total, total_files, cat_bytes}`

### POST /api/ops/recycle `{paths:[...]}` → `{ok, job_id}` / 보호 경로 포함 시 400 `{error:'protected', blocked:[{path,reason}]}`
### POST /api/ops/move `{paths:[...], target:'H', dest_dir?:str|null, verify:'size'|'quick'|'full'}`
→ `{ok, job_id}` / 400 `{error:'space', need, free}` / 400 `protected` / 400 `same_drive`
### POST /api/ops/preview_move `{paths, target, dest_dir?}`
→ `{ok, bytes, files, target_free, target_free_after, enough:bool, blocked:[…], dest_examples:['H:\\Projects\\A', …(최대 3)], conflicts:n}`
### POST /api/ops/empty_bin `{drives:['D']}` → `{ok, job_id}`
### POST /api/ops/cancel `{job_id}` · POST /api/ops/undo `{job_id}` → `{ok, job_id(새 작업)}`
### GET /api/jobs → `{ok, jobs:[…최근 200]}`
job:
```
{id, type:'recycle'|'move'|'empty_bin'|'undo'|'verify_dupes', status:'queued'|'running'|'done'|'partial'|'failed'|'canceled',
 title, created_at, started_at, finished_at,
 bytes_total, bytes_done, files_total, files_done, current, speed_bps, eta_s,
 freed:{D: bytes}, added:{H: bytes},
 results:[{path, ok, dest?, error?}], undoable:bool, undo_of?:id, message}
```

### POST /api/reveal `{path}` 탐색기에서 선택(Windows `explorer /select,`). fake 에선 no-op.
### GET /api/recommendations → `{ok, items:[rec…]}` (§9)
### GET /api/dupes?min_mb=50 → `{ok, groups:[{size, name, wasted, verified:bool|null, items:[{drive,path,mtime,key}]}], wasted_total}`
### POST /api/dupes/verify `{group_ids?}` → job(빠른 해시: 처음·가운데·끝 1MB + 크기)
### GET /api/settings · POST /api/settings `{…부분}` → 저장 후 전체 반환
### POST /api/rules `{path, cat}` 규칙 추가 → 즉시 재분류 · POST /api/rules/delete `{path}` → 해당 서브트리 재스캔 작업

## 8. 작업(ops) 규칙 — **데이터 안전이 최우선**

공통:
- 작업은 **단일 워커 스레드 큐**에서 순서대로(디스크 경합 방지). 각 작업은 취소 가능.
- 실행 직전 매 경로에 `safety.check(path)` 재검사. 존재하지 않으면 그 항목만 실패 처리.
- 작업 기록은 `jobs.json` 에 최근 200개 보관(원자적 쓰기).
- 끝나면 인덱스 갱신: 원본 쪽 `detach`, 대상 쪽 `scan_subtree` 후 `graft`, 그리고 인덱스 저장.

**휴지통(recycle)**
- Windows: `SHFileOperationW(FO_DELETE)` 플래그 `FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING | FOF_SILENT | FOF_NOERRORUI`.
  ★`FOF_WANTNUKEWARNING` 필수 — 없으면 휴지통 용량을 넘는 큰 파일이 **경고 없이 영구 삭제**된다.
  경로 버퍼는 이중 NUL 종료. 경로 하나씩 호출해 항목별 결과를 얻는다. `fAnyOperationsAborted` 가 참이면 「취소됨」.
- fake: `<드라이브 루트>/.fakebin/<타임스탬프>_<이름>` 으로 이동.
- UI 는 반드시 안내: **「휴지통으로 보낸 용량은 휴지통을 비워야 그 드라이브에서 확보됩니다」**(드라이브마다 휴지통이 따로다).
- 되돌리기: 자동 복원은 하지 않는다(`undoable:false`, 메시지 「휴지통에서 복원하세요」).

**이동(move)** — 파일·폴더·시퀀스
1. 대상 경로: `dest_dir` 가 있으면 `dest_dir\<이름>`, 없으면 **같은 상대 경로** `<대상 루트>\<원본 루트 기준 상대 경로>`(폴더 구조 유지 → AE 상대 경로 재연결이 된다). 이미 있으면 맨 위 항목 이름에 ` (2)`, ` (3)` …
2. 같은 드라이브면 400 `same_drive`(UI 가 막는다).
3. 공간 검사: 필요 바이트 + 1GB 여유 ≤ 대상 free. 부족하면 시작하지 않는다.
4. 복사: 파일마다 `<dest>.dbpart` 로 8MB 청크 복사(취소 확인은 청크마다) → `flush`+`fsync` → 검증 → `os.replace(.dbpart, dest)` → `shutil.copystat`.
   검증 수준: `size`(크기 일치) / `quick`(**기본**, 크기 + 처음·가운데·끝 1MB 바이트 비교) / `full`(복사 중 계산한 blake2b 와 대상 재독 blake2b 비교).
5. **원본 삭제는 그 항목의 모든 파일이 검증된 뒤에만**, 복사한 파일을 하나씩 지우고 빈 디렉터리를 아래에서 위로 제거. 복사 못 한 파일(잠김 등)이 있으면 그 파일과 그 상위 폴더는 남기고 `partial`.
6. 취소: 진행 중 파일의 `.dbpart` 삭제, 이미 끝난 항목은 이동된 채로 두고 결과에 기록.
7. 실패한 `.dbpart` 는 반드시 정리(finally).
8. 되돌리기: 완료된 항목만, 대상 → 원래 경로로 같은 알고리즘으로 이동(원래 경로가 다시 생겼으면 ` (2)`).
9. 실행 중인 Adobe 앱(AfterFX.exe, Adobe Premiere Pro.exe, Adobe Media Encoder.exe)이 있으면 캐시 카테고리 항목 작업 전에 경고 문구를 결과 message 에 담는다(차단은 안 함). 프로세스 목록은 `tasklist /FO CSV /NH`(CREATE_NO_WINDOW).

**휴지통 비우기(empty_bin)**: `SHEmptyRecycleBinW(None, "D:\\", SHERB_NOCONFIRMATION|SHERB_NOPROGRESSUI|SHERB_NOSOUND)`. UI 에서 확인창 필수(영구 삭제). 크기는 `SHQueryRecycleBinW`.

## 9. 보호 경로 (core/safety.py) `check(path, drives) -> (ok, reason)`

거부(한국어 이유와 함께):
- 알려진 드라이브(스캔 대상 고정 드라이브)에 속하지 않는 경로, 정규화 후 `..` 이 남는 경로, 상대 경로.
- **드라이브 루트 자체**.
- 루트 바로 아래 `Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`, `$Recycle.Bin`, `System Volume Information`, `Recovery`, `Boot`, `EFI`, `$WinREAgent`, `$WINDOWS.~BT` 와 그 하위 전부.
- 루트의 `pagefile.sys`, `hiberfil.sys`, `swapfile.sys`, `bootmgr`, `BOOTNT`, `DumpStack.log.tmp`.
- `Users` 폴더 자체, `Users\<이름>` 자체, `Users\<이름>\AppData` 와 `AppData\Local|Roaming|LocalLow` 자체(그 **하위의 캐시 폴더는 허용**).
- `Users\<이름>\` 바로 아래 표준 폴더 자체(`Desktop`, `Documents`, `Downloads`, `Pictures`, `Videos`, `Music`, `OneDrive`) — 안의 내용물은 허용.
- 카테고리 app(9) 으로 분류된 항목.
- 앱 자신의 폴더와 데이터 폴더(`DRIVEBOARD_HOME`).
fake 백엔드에서도 같은 규칙을 fake 루트 기준으로 적용한다.

## 10. 추천 (core/recommend.py) `build(indexes, drives, settings, dupes) -> list[rec]`
rec: `{id, kind, title, desc, bytes, count, tone:'safe'|'review'|'info', action:{type:'view', drive:'*'|'D', view, cat, age, dir?} | {type:'empty_bin', drives:[…]} | {type:'dupes'}}`
1. `cache` 캐시·임시 합계(≥1GB) — tone safe, 「AE·프리미어 캐시는 지워도 다시 만들어져요」
2. `bin` 드라이브별 휴지통 합계(≥100MB) — action empty_bin
3. `old_render` 렌더·출력 중 `settings.old_days`(기본 180) 넘게 수정 안 한 것
4. `dupes` 다른 위치의 같은 파일(이름+크기) 낭비 바이트
5. `big` 10GB 이상 파일
6. `old_project` 1년 넘게 손 안 댄 폴더(깊이 1~3 중 크기 상위, newest 기준) → 「여유 많은 드라이브로 옮기기」
7. `balance` 여유 < 10% 인 드라이브마다: 가장 여유 많은 드라이브 이름과 함께 info
정렬: bytes 내림차순, 단 tone safe 먼저.

## 11. UI (ui/)

**톤**: 다크, 차분, 고급. 사용자의 기존 앱(BannerNode/TeamTimer) 계열 — 배경 `#0B0C10`, 포인트 **라임 `#A2FF00`**(주 버튼·선택 체크·포커스·진행만, 아껴 쓴다). 글꼴 Pretendard Variable(로컬 `fonts/`).
**색은 전부 CSS 변수**. 하드코딩 금지.

화면:
1. **왼쪽 레일(240px)**: 로고 「DriveBoard」, 「전체」 + 드라이브 목록(문자·라벨·SSD/HDD 아이콘·미니 누적 막대·남은 공간). 드래그 중엔 **드롭 대상**이 된다. 아래쪽에 **휴지통 드롭존**.
2. **홈(전체)**: 히어로 = 전체 드라이브 합산 누적 막대 + 큰 숫자(「25.5TB 중 16.4TB 사용」) + 범례 칩. **범례 칩에 호버하면 모든 막대에서 그 카테고리만 밝고 나머지는 흐려진다**(연동 하이라이트). 그 아래 **추천 카드 줄**, 그 아래 **드라이브 카드 그리드**(카드마다 누적 막대·남은 공간·마지막 스캔).
3. **드라이브 상세**: 큰 헤더(「SSD2 (E:)」 + 「1.81TB 중 1.40TB 사용 · 406GB 남음」), 큰 누적 막대, 탭 `카테고리 | 폴더 | 트리맵`.
   - 카테고리 탭: 아이폰식 행 목록(색 아이콘, 이름, 크기, 파일 수, 쉐브론) → 누르면 그 카테고리 목록.
   - 폴더 탭: 브레드크럼 + 자식 폴더 목록(행마다 구성 미니 막대). 카테고리 필터 칩.
   - 트리맵 탭: `treemap.js`.
4. **목록 화면**(카테고리/전체 드라이브 검색 결과): 툴바(파일/폴더 토글, 정렬, 기간 칩 `전체·3개월+·6개월+·1년+`, 검색), 행 = 체크 · 색 아이콘 · 이름(시퀀스는 「1,500프레임」 배지) · 흐린 경로 · 크기 + 최댓값 대비 얇은 막대 · 「8개월 전」. 행 호버 시 빠른 동작(폴더 열기·휴지통·이동). 작은 파일 몫 안내 줄. 무한 스크롤(200개씩).
   - **폴더를 고를 때는 그 폴더의 전체 구성**(다른 카테고리 포함)을 보여 준다 — 「렌더」 필터에서 폴더를 지우면 안의 소스도 같이 지워지기 때문.
5. **선택 트레이**: 선택이 생기면 아래에서 스프링으로 올라오는 바 — 「3개 · 412GB」, 구성 미니 막대, `[휴지통으로] [다른 드라이브로 이동 ▾] [선택 해제]`. 이동 팝오버 = 드라이브 목록 + 각 드라이브의 **이동 후 막대 미리보기**(들어올 몫이 라임 사선), 공간 부족은 비활성 + 이유.
6. **드래그 앤 드롭**: 행(여러 개)을 끌면 「3개 · 412GB」 칩이 커서를 따라온다. 레일의 드라이브에 올리면 그 항목이 살짝 커지며 「이동 후 여유 1.2TB」 를 보여 주고, 공간이 부족하면 붉게 흔들린다. 놓으면 확인 시트.
7. **확인 시트**(모달): 무엇을, 얼마나, 구성, 원본 → 대상 예시 경로, 검증 수준. 휴지통은 「휴지통을 비워야 공간이 생긴다」 안내 + 그 드라이브 휴지통 현재 크기.
8. **작업 표시**: 헤더 오른쪽 알약(원형 진행 + 「이동 중 42%」). 누르면 작업 패널(진행·속도·남은 시간·취소·되돌리기·기록).
9. **토스트**: 아래 가운데, 이동 완료엔 [되돌리기].
10. **설정**: 큰 파일 기준(MB), 검증 수준, 오래된 기준(일), 제외 폴더, 캐시 폴더 추가, 분류 규칙 목록(삭제 가능).
11. 행 우클릭 메뉴: 탐색기에서 보기 · 경로 복사 · **「이 폴더를 ○○로 분류」**(규칙 추가) · 휴지통 · 이동.

**모션**(전부 `prefers-reduced-motion` 존중):
- 첫 표시: 누적 막대가 0 에서 왼→오로 차오름(세그먼트 순차 지연, `cubic-bezier(.2,.8,.2,1)` 700ms), 큰 숫자 카운트업.
- 스캔 중: 카드에 은은한 쉬머 + 실시간 숫자, 막대가 스캔 진행에 따라 자란다.
- 삭제 후: 해당 행이 높이 0 으로 접히고, 막대의 그 세그먼트가 줄어들며 남은 공간이 늘어난다(만족감의 순간).
- 이동 후: 선택 칩이 대상 드라이브(레일)로 날아가는 FLIP 애니메이션, 대상 막대가 자란다.
- 화면 전환 220ms 페이드+슬라이드, 목록 첫 20행 스태거.

**컴포넌트 규칙**: 네이티브 `alert/confirm/prompt`·`<select>`·`title` 툴팁 금지 → 자체 모달·드롭다운·`data-tip` 툴팁. 문구는 **존댓말**. 아이콘은 인라인 SVG(외부 아이콘 폰트 금지). 외부 네트워크 요청 0(CDN 금지 — 오프라인 동작).

**키보드**: `Delete` 휴지통 확인 · `Esc` 닫기/선택 해제 · `Ctrl+A` 전체 선택 · `/` 검색 · `Backspace` 상위 폴더 · `F5` 새로고침(데이터).

## 12. 가짜 드라이브(개발·테스트)

- `DRIVEBOARD_FAKE=<디렉터리>` 또는 `app.py --fake <디렉터리>`: 그 아래 하위 디렉터리 하나 = 드라이브 하나.
  디렉터리 이름 `C`, `D` … 가 드라이브 문자. 각 디렉터리의 `_drive.json` = `{"label":"SSD1","kind":"ssd","total":3990000000000}`. `free = total − (실제 파일 크기 합 + .fakebin 합)`.
  ★`_drive.json` 과 `.fakebin` 은 스캔에서 제외.
- `tests/make_fake_drives.py <out> [--scale small|demo]`: 희소 파일(`truncate`)로 **실제 디스크는 거의 안 쓰고** 수 TB 를 흉내 낸다. 7개 드라이브(사용자 PC 와 같은 라벨·용량), 영상팀 같은 구조(프로젝트/footage/render/aep, `Media Cache Files`, EXR 시퀀스, DCIM, Auto-Save, 중복 사본, 오래된 mtime, Program Files 흉내).
  ★희소 파일을 **이동 테스트**에 쓰면 실제로 0 을 써서 디스크를 채운다 → 이동 테스트는 작은 파일로.

## 13. 검증 기준

- `python -m unittest discover -s tests` 전부 통과(표준 라이브러리만).
- `node --check ui/app.js ui/treemap.js`.
- `python app.py --no-open --fake <dir>` 로 띄워 헤드리스 Chromium(Playwright)으로 주요 흐름 + 스크린샷, 콘솔 오류 0.
- Windows 전용 코드는 여기서 실행할 수 없다 → ctypes `argtypes/restype` 를 모두 명시하고, 실기 확인 목록을 `README.md` 「Windows 에서 처음 확인할 것」에 적는다.

## 14. 1.0.0 구현 메모 (1차 스펙과 달라진 점)

2차(`SPEC-2.md`)를 함께 구현하면서 1차 계약이 바뀐 곳. 자세한 2차 쪽 차이는 `SPEC-2.md` §G.
- **드라이브 키**: 필드 이름은 계속 `letter` 지만 값이 드라이브 문자만은 아니다 — NAS 는 `N-` + sha1(소문자 UNC 루트) 앞 6자리,
  연결 안 된 장치는 마지막 문자(그 문자를 다른 장치가 쓰고 있으면 `~<볼륨 시리얼>`). `drive=` 를 받는 모든 API 가 이 모양을 받는다.
- **레일**: 「전체」 줄이 없어지고 맨 위 내비 「저장 공간」(홈)·「프로젝트」가 생겼다. 드라이브는 내장 / 외장·USB / 네트워크(NAS) 로 묶인다.
- **`/api/items?drive=*`·추천·중복**은 연결된 드라이브만 본다. 연결 안 된 장치는 키를 직접 주면 볼 수 있고 항목은 전부 보호(`연결되지 않은 장치라 다룰 수 없어요`).
- **보호 경로**(§9)·**분류의 시스템 문맥**(§3 의 최상위 `Windows`·`Program Files`…, `AppData`)은 내장 드라이브에만 적용한다. 외장·USB·NAS 는 루트 자체와 휴지통·시스템 볼륨 폴더·NAS 시스템 폴더만 막는다.
- **스캔 끝**: 인덱스를 저장할 때까지 phase 는 `finalize` 이고, 저장한 뒤 `done` 이다.
- **추천 「오래 손대지 않은 폴더」**: 외장·NAS 에 있는 폴더는 대상에서 뺀다(이미 보관 장소다). 문장에는 NAS 내부 키 대신 라벨을 쓴다.
- **검증 기준(§13)**: `node --check` 는 `ui/*.js`·`tests/*.js` 전부, Playwright 종단 시험은 `tests/e2e.js`(팀장 구성 v2 흐름)·`ui_shell.js`·`ui_projects.js`·`ui_smoke.js`.

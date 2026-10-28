@echo off
rem DriveBoard - local Windows build (SPEC-2 E)
rem   build.bat            : system Python
rem   build.bat --venv     : isolated .venv-build (recommended)
rem   build.bat --skip-tests
rem Output: dist\DriveBoard\DriveBoard.exe + dist\DriveBoard-Windows.zip
rem ffmpeg pin must match core\version.py and .github\workflows\release.yml (tests\test_packaging.py checks).
setlocal EnableExtensions
chcp 65001 > nul
cd /d "%~dp0"

set "FFMPEG_VERSION=8.0.1"
set "FFMPEG_URL=https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip"
set "FFMPEG_SHA256=e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673"
set "FFMPEG_DIR=ffmpeg-8.0.1-essentials_build"
set "PYWEBVIEW_VERSION=5.4"
set "PYINSTALLER_VERSION=6.16.0"
set "PYTHONUTF8=1"
set "DRIVEBOARD_NO_UPDATE_CHECK=1"
set "DRIVEBOARD_REQUIRE_FFMPEG=1"

set "USE_VENV="
set "SKIP_TESTS="
:args
if "%~1"=="" goto args_done
if /i "%~1"=="--venv" set "USE_VENV=1"
if /i "%~1"=="--skip-tests" set "SKIP_TESTS=1"
shift
goto args
:args_done

rem ---- Python
set "PY="
where py > nul 2> nul && set "PY=py -3"
if not defined PY where python > nul 2> nul && set "PY=python"
if not defined PY (
  echo   [X] Python 을 찾지 못했어요. Python 3.10 이상을 설치해 주세요.
  goto fail
)
if defined USE_VENV (
  if not exist ".venv-build\Scripts\python.exe" (
    echo   - 가상 환경 .venv-build 를 만듭니다...
    %PY% -m venv .venv-build || goto fail
  )
  set "PY=.venv-build\Scripts\python.exe"
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" || (
  echo   [X] Python 3.10 이상이 필요해요.
  goto fail
)

echo.
echo   [1/6] 빌드 도구 설치 (pywebview %PYWEBVIEW_VERSION%, PyInstaller %PYINSTALLER_VERSION%)
%PY% -m pip install --disable-pip-version-check -q "pywebview==%PYWEBVIEW_VERSION%" "pyinstaller==%PYINSTALLER_VERSION%" || goto fail

echo   [2/6] ffmpeg %FFMPEG_VERSION%
if exist "bin\ffmpeg.exe" if exist "bin\ffprobe.exe" (
  echo         bin\ffmpeg.exe 가 이미 있어요 - 내려받기를 건너뜁니다.
  goto ffmpeg_done
)
call :fetch_ffmpeg || goto fail
:ffmpeg_done

echo   [3/6] 앱 아이콘
if not exist "build\icon.ico" (
  %PY% tests\test_packaging.py --write-icon || goto fail
)

if defined SKIP_TESTS (
  echo   [4/6] 단위 시험 - 건너뜀 --skip-tests
) else (
  echo   [4/6] 단위 시험
  call :run_tests || goto fail
)

echo   [5/6] PyInstaller
if exist "dist\DriveBoard" rmdir /s /q "dist\DriveBoard"
if exist "dist\DriveBoard" (
  echo   [X] dist\DriveBoard 를 지우지 못했어요. DriveBoard.exe 가 실행 중이면 트레이까지 종료해 주세요.
  goto fail
)
%PY% -m PyInstaller --noconfirm --clean --workpath build\pyi --distpath dist driveboard.spec || goto fail
if not exist "dist\DriveBoard\DriveBoard.exe" (
  echo   [X] dist\DriveBoard\DriveBoard.exe 가 만들어지지 않았어요.
  goto fail
)
if not exist "dist\DriveBoard\_internal\ui\index.html" (
  echo   [X] 배포본에 ui\index.html 이 빠졌어요.
  goto fail
)
copy /y "THIRD-PARTY.txt" "dist\DriveBoard\THIRD-PARTY.txt" > nul || goto fail

echo   [6/6] dist\DriveBoard-Windows.zip
if exist "dist\DriveBoard-Windows.zip" del /q "dist\DriveBoard-Windows.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Compress-Archive -Path 'dist\DriveBoard' -DestinationPath 'dist\DriveBoard-Windows.zip' -CompressionLevel Optimal; $h=(Get-FileHash -Algorithm SHA256 'dist\DriveBoard-Windows.zip').Hash.ToLowerInvariant(); Write-Host ('        sha256 ' + $h)" || goto fail

echo.
echo   완료: dist\DriveBoard\DriveBoard.exe
echo         dist\DriveBoard-Windows.zip
endlocal
exit /b 0

:run_tests
setlocal
set "DRIVEBOARD_HOME=%TEMP%\driveboard-build-tests-%RANDOM%"
%PY% -m unittest discover -s tests
set "RC=%ERRORLEVEL%"
if exist "%DRIVEBOARD_HOME%" rmdir /s /q "%DRIVEBOARD_HOME%"
endlocal & exit /b %RC%

:fetch_ffmpeg
rem Download the pinned zip, verify sha256, extract only ffmpeg.exe / ffprobe.exe (+ license, readme).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $tmp=Join-Path $env:TEMP ('driveboard-ffmpeg-'+[guid]::NewGuid()); New-Item -ItemType Directory -Path $tmp | Out-Null; try { $zip=Join-Path $tmp 'ffmpeg.zip'; Write-Host ('        ' + $env:FFMPEG_URL); Invoke-WebRequest -Uri $env:FFMPEG_URL -OutFile $zip -UseBasicParsing; $h=(Get-FileHash -Algorithm SHA256 $zip).Hash.ToLowerInvariant(); if ($h -ne $env:FFMPEG_SHA256) { throw ('sha256 mismatch: ' + $h) }; Write-Host '        sha256 OK'; Expand-Archive -Path $zip -DestinationPath $tmp -Force; $src=Join-Path $tmp $env:FFMPEG_DIR; New-Item -ItemType Directory -Force -Path 'bin' | Out-Null; Copy-Item (Join-Path $src 'bin\ffmpeg.exe') 'bin\ffmpeg.exe' -Force; Copy-Item (Join-Path $src 'bin\ffprobe.exe') 'bin\ffprobe.exe' -Force; Copy-Item (Join-Path $src 'LICENSE') 'bin\FFmpeg-LICENSE.txt' -Force; Copy-Item (Join-Path $src 'README.txt') 'bin\FFmpeg-README.txt' -Force } finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }"
if errorlevel 1 (
  echo   [X] ffmpeg 를 받지 못했어요. 네트워크를 확인하거나 bin\ffmpeg.exe, bin\ffprobe.exe 를 직접 넣어 주세요.
  exit /b 1
)
exit /b 0

:fail
echo.
echo   [X] 빌드에 실패했어요. 위 메시지를 확인해 주세요.
endlocal
exit /b 1

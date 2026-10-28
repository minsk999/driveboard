@echo off
rem DriveBoard dev launcher (console shows logs; closing it stops the app)
chcp 65001 > nul
title DriveBoard - dev
cd /d "%~dp0"
echo.
echo   DriveBoard (개발 모드)
echo   - 이 창을 닫으면 앱도 함께 종료돼요.
echo   - 가짜 드라이브로 띄우려면: run-dev.cmd --fake C:\경로\fake
echo.
set "PYEXE="
where python > nul 2> nul && set "PYEXE=python"
if not defined PYEXE where py > nul 2> nul && set "PYEXE=py -3"
if not defined PYEXE goto nopy
%PYEXE% "%~dp0app.py" --verbose %*
if errorlevel 1 pause
exit /b
:nopy
echo   [X] Python 을 찾지 못했어요. Python 3.10 이상을 설치해 주세요.
pause
exit /b 1

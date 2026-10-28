@echo off
rem DriveBoard launcher (double-click; no console window)
chcp 65001 > nul
cd /d "%~dp0"
where pythonw > nul 2> nul && goto pythonw
where pyw > nul 2> nul && goto pyw
where python > nul 2> nul && goto python
echo.
echo   Python 을 찾지 못했어요. Python 3.10 이상을 설치한 뒤 다시 실행해 주세요.
echo.
pause
exit /b 1
:pythonw
start "" pythonw "%~dp0app.py" %*
exit /b 0
:pyw
start "" pyw -3 "%~dp0app.py" %*
exit /b 0
:python
start "DriveBoard" /min python "%~dp0app.py" %*
exit /b 0

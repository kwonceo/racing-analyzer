@echo off
setlocal
REM ============================================================
REM  서버 감시 - 작업 스케줄러가 5분마다 부른다.
REM  판정과 상한은 tools\watchdog.py 안에 있다. 이 파일은 호출만 한다.
REM ============================================================
cd /d "%~dp0.."

set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py"
set "PYTHONIOENCODING=utf-8"

"%PY%" tools\watchdog.py
exit /b %errorlevel%

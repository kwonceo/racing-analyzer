@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  서버만 띄운다. 브라우저는 열지 않는다.
REM  2026-08-14 신설 - 윈도우 업데이트 재부팅으로 2시간 다운된 사고 대응.
REM
REM  기존 경마서버_자동시작.bat 과의 차이:
REM    . 브라우저를 열지 않는다 (로그인 없는 세션에서는 못 띄운다)
REM    . git pull 을 하지 않는다 (무인 실행 중 코드가 바뀌면 위험)
REM    . pause 가 없다 (무인 실행이라 멈추면 안 된다)
REM  기존 파일은 지우지 않았다. 사람이 켤 때는 그대로 쓴다.
REM ============================================================
cd /d "%~dp0.."

set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py"
set "PYTHONIOENCODING=utf-8"
set "LOGDIR=%CD%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

REM ---- 중복 기동 방지: 8011 이 LISTENING 이면 아무것도 안 한다 ----
REM  ESTABLISHED/TIME_WAIT 는 세지 않는다. LISTENING 만 본다.
set "SRV_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do set "SRV_PID=%%P"
if defined SRV_PID (
  echo [%date% %time%] SKIP - already listening PID !SRV_PID! >> "%LOGDIR%\autostart.log"
  exit /b 0
)

REM ---- 로그 회전 (50MB 초과 시 backups 로) ----
if exist "%~dp0rotate_logs.bat" call "%~dp0rotate_logs.bat" >nul 2>&1

echo [%date% %time%] START attempt >> "%LOGDIR%\autostart.log"

REM ---- detached 기동 ----
REM  Start-Process 를 쓰는 이유: 부모(작업 스케줄러)가 끝나도 자식이 살아남는다.
REM  cmd 의 start /b 는 콘솔을 공유해 부모 종료 시 함께 죽을 수 있다.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$env:PYTHONIOENCODING='utf-8'; Start-Process -FilePath '%PY%' -ArgumentList 'app.py' -WorkingDirectory '%CD%' -RedirectStandardOutput '%LOGDIR%\server_stdout.log' -RedirectStandardError '%LOGDIR%\server_stdout.log.err' -WindowStyle Hidden"

REM ---- 기동 확인 (최대 40초) ----
set "OK="
for /l %%i in (1,1,8) do (
  timeout /t 5 /nobreak >nul
  if not defined OK (
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do set "OK=%%P"
  )
)

if defined OK (
  echo [%date% %time%] OK - listening PID !OK! >> "%LOGDIR%\autostart.log"
  exit /b 0
) else (
  echo [%date% %time%] FAIL - not listening after 40s >> "%LOGDIR%\autostart.log"
  exit /b 1
)

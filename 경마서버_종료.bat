@echo off
REM =======================================================================
REM  경마서버 안전 종료  (2026-08-03)
REM
REM  [!] 이 파일은 반드시 cp949(ANSI)로 저장한다.
REM      CMD 가 배치 파일을 읽는 인코딩은 시스템 ANSI 로 고정이고
REM      chcp 65001 로도 바뀌지 않는다. UTF-8 로 저장하면 한글이 깨져
REM      명령이 하나도 실행되지 않는다(2026-08-03 실사고).
REM
REM  왜 필요한가:
REM    서버는 detached(Start-Process -WindowStyle Hidden)로 띄운다.
REM    창과 프로세스가 분리돼 창을 닫아도 서버가 계속 살아 있다.
REM    [주의] 기동 방식은 그대로 둔다 - 창을 실수로 닫아도 수집이 안 끊긴다.
REM          대신 끄는 수단을 준다.
REM
REM  안전장치:
REM    - 종료 전에 무엇을 죽이는지 화면에 보여주고 확인을 받는다
REM    - 8011 LISTEN PID 를 그 순간 netstat 으로 다시 확인한다
REM    - 리로더 부모/자식을 함께 잡는다
REM    - 종료 후 정말 죽었는지 다시 확인하고 결과를 표시한다
REM    - [!] 중간에 실패하면 거기서 멈추고 알린다(끝까지 가지 않는다)
REM =======================================================================
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo.
echo ============================================================
echo   경마서버 종료
echo ============================================================
echo.

REM --- [0/2] 파이썬이 있는지 먼저 확인. 없으면 여기서 멈춘다 ---
where py >nul 2>nul
if errorlevel 1 (
  echo [X] py 명령을 찾을 수 없습니다. 파이썬이 설치되어 있는지 확인하세요.
  goto :failed
)

echo [1/2] 지금 무엇이 떠 있는지 확인합니다...
echo.
py tools\kill_safe.py --server
if errorlevel 1 (
  echo.
  echo [X] 확인 단계에서 실패했습니다. 아래 메시지를 보세요.
  goto :failed
)

echo.
echo ============================================================
set "ANS="
set /p ANS="위 프로세스를 종료합니까?  (y = 종료 / 그 외 = 취소) : "
if /i not "%ANS%"=="y" goto :cancelled

echo.
echo [2/2] 종료합니다...
echo.
py tools\kill_safe.py --server --apply --yes
if errorlevel 1 (
  echo.
  echo [X] 종료에 실패했습니다. 8011 포트를 아직 잡고 있을 수 있습니다.
  goto :failed
)

echo.
echo [OK] 종료가 끝났습니다. 이 창을 닫아도 됩니다.
goto :end

:cancelled
echo.
echo 취소했습니다. 아무것도 종료하지 않았습니다. 서버는 계속 돌고 있습니다.
goto :end

:failed
echo.
echo ------------------------------------------------------------
echo [X] 작업이 완료되지 않았습니다. 서버 상태를 직접 확인하세요:
echo     netstat -ano ^| findstr :8011
echo ------------------------------------------------------------

:end
echo.
pause

@echo off
chcp 65001 >nul
REM ═══════════════════════════════════════════════════════════════════════
REM  경마서버 안전 종료  (2026-08-03 승인 ⓐ)
REM
REM  왜 필요한가:
REM    서버는 2026-07-30 부터 `Start-Process -WindowStyle Hidden`(detached)로 띄운다.
REM    그러면 **창과 프로세스가 분리**돼 창을 닫아도 서버가 계속 살아 있다.
REM    대표가 "창을 닫아서 내렸다"고 생각했는데 실제로는 돌고 있던 이유가 이것이다.
REM    ⚠ detached 를 없애면 실수로 창을 닫을 때 수집이 끊긴다 —
REM      그래서 기동 방식은 그대로 두고 **끄는 수단**을 만든다.
REM
REM  안전장치:
REM    · 종료 전에 **무엇을 죽이는지 화면에 보여주고** 확인을 받는다
REM    · 8011 LISTEN PID 를 **그 순간 netstat 으로 다시** 확인한다(캐시하지 않는다)
REM    · 리로더 부모·자식을 함께 잡는다(2026-08-03 에 이걸 몰라 2벌로 오판했다)
REM    · 종료 후 **정말 죽었는지 다시 확인**하고 결과를 표시한다
REM ═══════════════════════════════════════════════════════════════════════
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo.
echo ============================================================
echo   경마서버 종료
echo ============================================================
echo.
echo [1/2] 지금 무엇이 떠 있는지 확인합니다...
echo.
python tools\kill_safe.py --server
if errorlevel 1 goto :failed

echo.
echo ============================================================
set /p ANS="위 프로세스를 종료합니까?  (y = 종료 / 그 외 = 취소) : "
if /i not "%ANS%"=="y" goto :cancelled

echo.
echo [2/2] 종료합니다...
echo.
python tools\kill_safe.py --server --apply --yes
if errorlevel 1 goto :failed

echo.
echo 🟢 종료가 끝났습니다. 이 창을 닫아도 됩니다.
goto :end

:cancelled
echo.
echo 취소했습니다. 아무것도 종료하지 않았습니다. 서버는 계속 돌고 있습니다.
goto :end

:failed
echo.
echo 🔴 종료에 실패했습니다. 위 메시지를 확인하세요.
echo    (서버가 안 떠 있었거나, 권한 문제일 수 있습니다)

:end
echo.
pause

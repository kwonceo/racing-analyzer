@echo off
REM ============================================================
REM  자동 기동 등록을 되돌린다 - 관리자 권한 필요
REM  등록한 작업 2개만 지운다. 다른 것은 건드리지 않는다.
REM  시작프로그램 바로가기와 경마서버_자동시작.bat 은 그대로 남는다.
REM ============================================================
net session >nul 2>&1
if errorlevel 1 (
  echo [중단] 관리자 권한이 없습니다. 오른쪽 버튼 - 관리자 권한으로 실행.
  pause
  exit /b 1
)
schtasks /delete /tn "KeibaServerAutoStart" /f
schtasks /delete /tn "KeibaServerWatchdog" /f
schtasks /delete /tn "KeibaSystemProbe" /f >nul 2>&1
echo.
echo 되돌리기 완료. 서버는 계속 돌고 있습니다.
echo 앞으로는 예전처럼 로그인해야 서버가 뜹니다.
pause

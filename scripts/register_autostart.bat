@echo off
setlocal
REM ============================================================
REM  서버 자동 기동 등록 - 대표가 1회만 실행한다 (관리자 권한 필요)
REM
REM  2026-08-14 신설. 윈도우 업데이트가 새벽 2시에 컴퓨터를 재시작했고
REM  서버가 다시 안 올라와 두 시간 동안 없었다.
REM
REM  왜 작업 스케줄러인가
REM    시작프로그램은 사람이 로그인해야 실행된다. 잠금화면에 멈춰 있으면 안 뜬다.
REM    작업 스케줄러는 로그인 없이도 실행된다.
REM    윈도우 서비스로 만들려면 NSSM 같은 외부 도구를 깔아야 하고
REM    되돌리려면 그 도구까지 지워야 한다. 스케줄러는 삭제 한 줄이다.
REM
REM  등록되는 작업 2개
REM    KeibaServerAutoStart : 컴퓨터가 켜지면 서버를 띄운다
REM    KeibaServerWatchdog  : 5분마다 살아 있는지 보고 죽었으면 다시 띄운다
REM
REM  되돌리기
REM    scripts\unregister_autostart.bat 실행 (또는 아래 두 줄)
REM      schtasks /delete /tn "KeibaServerAutoStart" /f
REM      schtasks /delete /tn "KeibaServerWatchdog" /f
REM ============================================================
cd /d "%~dp0.."
set "ROOT=%CD%"

echo.
echo ============================================================
echo   경마 분석 서버 - 자동 기동 등록
echo ============================================================
echo.

REM ---- 관리자 권한 확인 ----
net session >nul 2>&1
if errorlevel 1 (
  echo [중단] 관리자 권한이 없습니다.
  echo.
  echo   이 파일을 마우스 오른쪽 버튼으로 누르고
  echo   [관리자 권한으로 실행] 을 골라 주세요.
  echo.
  pause
  exit /b 1
)
echo [1/4] 관리자 권한 확인      : OK
echo.

REM ---- SYSTEM 계정 환경 점검 (서버를 띄우지 않는다) ----
echo [2/4] SYSTEM 계정에서 서버가 뜰 수 있는지 확인합니다...
echo       (지금 도는 서버는 건드리지 않습니다. 30초쯤 걸립니다)
echo.

schtasks /delete /tn "KeibaSystemProbe" /f >nul 2>&1
schtasks /create /tn "KeibaSystemProbe" ^
  /tr "cmd /c cd /d \"%ROOT%\" && set PYTHONIOENCODING=utf-8 && py tools\system_probe.py" ^
  /sc ONCE /st 00:00 /ru SYSTEM /rl HIGHEST /f >nul 2>&1
if errorlevel 1 (
  echo    [!] 점검 작업 등록 실패. 계속 진행합니다.
) else (
  if exist "%ROOT%\logs\system_probe.json" del /q "%ROOT%\logs\system_probe.json" >nul 2>&1
  schtasks /run /tn "KeibaSystemProbe" >nul 2>&1
  timeout /t 30 /nobreak >nul
  schtasks /delete /tn "KeibaSystemProbe" /f >nul 2>&1
)

if exist "%ROOT%\logs\system_probe.json" (
  echo    점검 결과 ^(logs\system_probe.json^):
  type "%ROOT%\logs\system_probe.json"
  echo.
  findstr /c:"\"ok\": true" "%ROOT%\logs\system_probe.json" >nul 2>&1
  if errorlevel 1 (
    echo.
    echo    [!] SYSTEM 계정 점검에서 실패한 항목이 있습니다.
    echo    [!] 위 FAIL 줄을 클로드에게 보여 주세요.
    echo    [!] 그래도 등록은 진행합니다. 5분 감시가 받쳐 줍니다.
  ) else (
    echo    SYSTEM 계정 점검      : 전부 통과
  )
) else (
  echo    [!] 점검 결과 파일이 안 생겼습니다. SYSTEM 계정에서 파이썬 실행이 막혔을 수 있습니다.
  echo    [!] 이 줄을 클로드에게 보여 주세요. 등록은 계속 진행합니다.
)
echo.

REM ---- 작업 1: 부팅 시 자동 기동 ----
echo [3/4] 부팅 시 자동 기동 등록...
schtasks /delete /tn "KeibaServerAutoStart" /f >nul 2>&1
schtasks /create /tn "KeibaServerAutoStart" ^
  /tr "\"%ROOT%\scripts\start_server_only.bat\"" ^
  /sc ONSTART /ru SYSTEM /rl HIGHEST /f
if errorlevel 1 (
  echo    [실패] 등록되지 않았습니다.
) else (
  echo    KeibaServerAutoStart  : 등록 완료
)
echo.

REM ---- 작업 2: 5분 감시 ----
echo [4/4] 5분 감시 등록...
schtasks /delete /tn "KeibaServerWatchdog" /f >nul 2>&1
schtasks /create /tn "KeibaServerWatchdog" ^
  /tr "\"%ROOT%\scripts\watchdog.bat\"" ^
  /sc MINUTE /mo 5 /ru SYSTEM /rl HIGHEST /f
if errorlevel 1 (
  echo    [실패] 등록되지 않았습니다.
) else (
  echo    KeibaServerWatchdog   : 등록 완료
)
echo.

echo ============================================================
echo   등록된 작업
echo ============================================================
schtasks /query /tn "KeibaServerAutoStart" /fo LIST 2>nul | findstr /c:"TaskName" /c:"Status" /c:"Next Run" /c:"다음 실행" /c:"상태"
schtasks /query /tn "KeibaServerWatchdog"  /fo LIST 2>nul | findstr /c:"TaskName" /c:"Status" /c:"Next Run" /c:"다음 실행" /c:"상태"
echo.
echo ------------------------------------------------------------
echo  확인하는 방법
echo    1) 컴퓨터를 다시 시작합니다
echo    2) 로그인하지 말고 잠금화면에서 3분 기다립니다
echo    3) 로그인한 뒤 브라우저에서 http://127.0.0.1:8011 을 엽니다
echo       화면이 뜨면 성공입니다
echo    4) 안 뜨면 logs\autostart.log 파일을 열어 마지막 줄을 봅니다
echo ------------------------------------------------------------
echo.
pause

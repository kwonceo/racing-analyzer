@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  매일 자정 데이터 자동 백업(GitHub 커밋) 예약 (1회만 실행)
REM   - Windows 작업 스케줄러에 매일 00:00 backup_checkpoint.bat 등록
REM   - 작업 이름: 경마서버_자정백업
REM  해제하려면: schtasks /Delete /TN "경마서버_자정백업" /F
REM ============================================================
set ROOT=%~dp0..
set BACKUP=%ROOT%\scripts\backup_checkpoint.bat
set TASK=경마서버_자정백업

echo 대상 배치 : %BACKUP%
echo 작업 이름 : %TASK%
echo 실행 시각 : 매일 00:00
echo.

if not exist "%BACKUP%" (
  echo ❌ backup_checkpoint.bat 을 찾을 수 없습니다. 프로젝트 루트 확인.
  goto :done
)

REM 기존 동일 작업이 있으면 갱신(먼저 삭제 시도 후 재등록)
schtasks /Delete /TN "%TASK%" /F >nul 2>&1

REM 무인 실행: backup_checkpoint 는 마지막에 pause 가 있으나 스케줄러는 창 없이 종료됨.
schtasks /Create /SC DAILY /ST 00:00 /TN "%TASK%" ^
  /TR "cmd /c \"%BACKUP%\"" /F

if %errorlevel%==0 (
  echo ✅ 등록 완료. 매일 자정 data/ + 문서가 GitHub 에 자동 커밋됩니다.
  echo    확인: schtasks /Query /TN "%TASK%"
  echo    해제: schtasks /Delete /TN "%TASK%" /F
) else (
  echo ⚠️ 등록 실패 - 관리자 권한으로 다시 실행하세요(우클릭 → 관리자 권한).
)

:done
echo.
endlocal
pause

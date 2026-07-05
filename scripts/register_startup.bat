@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  PC 부팅 시 경마서버 자동 실행 등록 (1회만 실행)
REM   - 현재 사용자 로그온 시 경마서버_자동시작.bat 을 자동 실행
REM   - 방식: 시작프로그램 폴더에 바로가기(.lnk) 생성
REM  해제하려면 시작프로그램 폴더에서 '경마서버자동시작.lnk' 삭제.
REM ============================================================
set ROOT=%~dp0..
set TARGET=%ROOT%\경마서버_자동시작.bat
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set LNK=%STARTUP%\경마서버자동시작.lnk

echo 대상 배치 : %TARGET%
echo 등록 위치 : %LNK%
echo.

if not exist "%TARGET%" (
  echo ❌ 경마서버_자동시작.bat 을 찾을 수 없습니다. 프로젝트 루트 확인.
  goto :done
)

REM PowerShell 로 시작프로그램 바로가기 생성(무인 실행: /auto 인자로 pause 생략)
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%LNK%');" ^
  "$s.TargetPath='%TARGET%';" ^
  "$s.Arguments='/auto';" ^
  "$s.WorkingDirectory='%ROOT%';" ^
  "$s.WindowStyle=7;" ^
  "$s.Description='경마 BMED 분석기 자동 시작';" ^
  "$s.Save()"

if exist "%LNK%" (
  echo ✅ 등록 완료. 다음 부팅부터 자동 실행됩니다.
  echo    해제: 위 등록 위치의 .lnk 파일을 삭제하세요.
) else (
  echo ⚠️ 등록 실패 - 관리자 권한 또는 PowerShell 정책 확인.
)

:done
echo.
endlocal
pause

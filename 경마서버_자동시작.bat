@echo off
setlocal
REM ============================================================
REM  경마 BMED 분석기 - 홈서버 자동 시작
REM   1) 최신 코드 확인(git pull)
REM   2) 서버 자동 실행(포트 8011) - 이미 떠 있으면 재실행 안 함
REM   3) 분석기 웹 + 배당판 자동 열기
REM   4) 확장 [전체 자동 수집] ON 안내
REM  ※ PC 켜지면 자동 실행하려면 scripts\register_startup.bat 을 1회 실행.
REM  기존 기능 삭제 없음. 프로젝트 루트에 위치.
REM ============================================================
cd /d "%~dp0"

set SERVER_URL=http://127.0.0.1:8011
set BOARD_URL=https://www.keiba.go.jp/

echo [1/4] 최신 코드 확인(git pull)...
git pull origin master 2>nul

echo.
echo [2/4] 서버 실행(포트 8011)...
REM ----------- [중복 기동 가드 · 2026-07-30 신설] -----------
REM  배경(실사고): 세션이 끊긴 뒤 "서버가 죽은 줄 알고" 두 번째 인스턴스를 띄웠다(7/30 13:30:59).
REM   Windows 는 SO_REUSEADDR 때문에 나중에 뜬 쪽이 LISTEN 을 가로채고,
REM   양쪽 트리가 각자 _multi_bg_loop 를 돌려 같은 파일에 동시 쓰기를 한다
REM   -> WinError 32/5 로 [복기저장]·[분석로그] 저장이 실패하고 스냅샷이 유실된다.
REM  종전 검사(findstr :8011)는 ESTABLISHED/TIME_WAIT 까지 잡아 판정이 불명확했으므로
REM   LISTENING 상태만 정확히 보고 PID 까지 표시하도록 강화한다.
REM  [주의] 강제 종료 로직은 넣지 않는다  실행 중인 서버를 죽이는 쪽이 더 위험하다.
set "SRV_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8011" ^| findstr "LISTENING"') do set "SRV_PID=%%P"
if defined SRV_PID (
  echo    [!] 서버가 이미 실행 중입니다 ^(PID %SRV_PID% · 포트 8011 LISTENING^).
  echo    [!] 중복 기동은 데이터 동시 쓰기 사고를 일으키므로 새로 띄우지 않습니다.
  echo    [!] 재시작이 필요하면 기존 프로세스를 먼저 종료한 뒤 이 스크립트를 다시 실행하세요.
) else (
  start "경마분석서버" cmd /c "py app.py"
  echo    서버 새 창에서 기동 중... (5초 대기)
  timeout /t 5 /nobreak >nul
)

echo.
echo [3/4] 분석기 웹 + 배당판 열기...
start "" "%SERVER_URL%"
start "" "%BOARD_URL%"

echo.
echo [4/4] 자동수집 시작 안내
echo ------------------------------------------------------------
echo  Chrome 확장 팝업에서 [전체 자동 수집 (복승·쌍승)] 을 ON 하세요.
echo   - 발주시각 기반 백그라운드 자동수집(30초 하트비트) 시작
echo   - 경주 전환은 확장이 배당판 변경을 감지 -> 이전 경주 스냅샷은
REM     서버 odds_history 에 영구 보존(baselineReset), 새 경주 자동 시작
echo   - 결과는 [일괄 결과 등록] 또는 복기 탭에서 입력 -> 완전 저장(AI 학습)
echo ------------------------------------------------------------
echo.
echo  분석기: %SERVER_URL%
echo  배당판: %BOARD_URL%
echo.
echo ===== 자동 시작 준비 완료 =====
endlocal
REM PC 부팅 자동실행(무인)일 땐 pause 생략되도록: 인자 /auto 주면 대기 안 함
if /I "%~1"=="/auto" goto :eof
pause

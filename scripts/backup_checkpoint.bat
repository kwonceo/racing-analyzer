@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  경마 BMED 분석기 - 전체 데이터 백업 체크포인트
REM  data/ 전체 커밋 + GitHub 푸시 + 백업 날짜 기록
REM  기존 기능 삭제 없음(추가만). 프로젝트 루트에서 실행.
REM ============================================================
cd /d "%~dp0.."

echo [1/4] 현재 상태 확인...
git status --short

echo.
echo [2/4] data/ 및 문서 스테이징...
git add data/analysis_log data/race_results data/ai_training data/daily_summary data/prerace data/korea_history data/korea_session.json data/discovered_patterns.json data/pattern_learning.json 2>nul
git add CHANGELOG.md CLAUDE.md README.md RECOVERY.md 2>nul

echo.
echo [3/4] 커밋...
for /f "tokens=1-3 delims=/- " %%a in ("%date%") do set TODAY=%%a-%%b-%%c
set STAMP=%date% %time%
git commit -m "체크포인트 백업 (%STAMP%)" -m "data/ 학습·AI 코퍼스 + 문서 백업" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
if errorlevel 1 (
  echo    변경 사항 없음 또는 커밋 스킵.
)

echo.
echo [4/4] GitHub 푸시...
git push origin master
if errorlevel 1 (
  echo    ⚠️ 푸시 실패 - 네트워크/인증 확인 후 재시도: git push origin master
) else (
  echo    ✅ 푸시 완료.
)

echo.
echo %STAMP% - 백업 실행>> data\backup_log.txt
echo 백업 날짜 기록: data\backup_log.txt
echo.
echo ===== 백업 체크포인트 완료 =====
endlocal
pause

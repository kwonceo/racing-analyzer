@echo off
REM ============================================================
REM  Racing Analyzer - safe git reset (data protection)
REM   Physically backs up data/ BEFORE running the given git command,
REM   so a bad "git reset --hard" can never lose learning data.
REM
REM   Usage (from anywhere):
REM     scripts\safe_reset.bat reset --hard HEAD~1
REM     scripts\safe_reset.bat reset --hard origin/master
REM     scripts\safe_reset.bat checkout -- .
REM
REM   IMPORTANT: do NOT run "git reset --hard" directly; use this wrapper.
REM   Recovery: restore from backups\data_<timestamp>\data\ or "git reflog".
REM   Additive only (no existing feature removed).
REM ============================================================
cd /d "%~dp0.."

if "%~1"=="" (
  echo Usage: scripts\safe_reset.bat ^<git command^>
  echo   e.g. scripts\safe_reset.bat reset --hard HEAD~1
  goto :eof
)

echo [safe-reset] 1/2 backing up data\ ...
call "%~dp0backup_data.bat"

echo.
echo [safe-reset] 2/2 git %*
git %*
if errorlevel 1 (
  echo [safe-reset] git command failed. Data is safe in backups\ .
) else (
  echo [safe-reset] done. Recover via backups\data_* or "git reflog" if needed.
)

@echo off
setlocal
REM ============================================================
REM  Racing Analyzer - physical snapshot backup of data/
REM   Copies the whole data/ folder to backups\data_<timestamp>\
REM   (local safety net). Call before risky ops (git reset --hard).
REM   safe_reset.bat calls this automatically. backups/ is gitignored.
REM   Additive only (no existing feature removed). Run from repo root.
REM ============================================================
cd /d "%~dp0.."

REM Reliable, locale-independent timestamp via PowerShell
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set BK=backups\data_%TS%

if not exist data (
  echo [data-backup] WARN: data folder not found. Skipping.
  goto :done
)
if not exist backups mkdir backups

echo [data-backup] snapshot data\ -^> %BK%
xcopy /E /I /Y /Q data "%BK%\data" >nul
if errorlevel 1 (
  echo [data-backup] ERROR: copy failed - check disk/permissions.
) else (
  echo [data-backup] OK: %BK%
  echo %date% %time% - physical backup %BK%>> data\backup_log.txt
)

REM Retention: keep only the newest 20 snapshots (auto-remove older)
for /f "skip=20 delims=" %%d in ('dir /b /ad /o-d backups\data_* 2^>nul') do (
  echo [data-backup] prune old snapshot: %%d
  rmdir /s /q "backups\%%d"
)

:done
endlocal

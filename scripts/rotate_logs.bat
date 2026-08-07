@echo off
REM ============================================================
REM  Server console log rotation  (2026-08-08)
REM   Over 50MB -> RENAME AND KEEP (do NOT truncate).
REM   Truncating would erase the day's incident evidence.
REM   Kept at: backups\logs\   (inside gitignore)
REM  Note: app.py unchanged. Call this before starting the server.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set LIMIT=52428800
if not exist "backups\logs" mkdir "backups\logs"
set TS=%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set TS=!TS: =0!
set MOVED=0
for %%F in (logs\server*) do (
  if %%~zF GTR %LIMIT% (
    move /y "%%F" "backups\logs\%%~nxF.!TS!" >/dev/null 2>&1
    REM verify by absence of the original - a locked file reports success but does not move
    if exist "%%F" (
      echo [rotate] SKIP %%~nxF - file is locked ^(server running?^). Nothing was truncated.
    ) else (
      echo [rotate] %%~nxF  %%~zF bytes  -^> backups\logs\%%~nxF.!TS!
      set /a MOVED+=1
    )
  )
)
echo [rotate] done. moved=!MOVED!  limit=%LIMIT% bytes
endlocal

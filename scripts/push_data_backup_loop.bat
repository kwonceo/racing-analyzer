@echo off
REM [2026-10-11] user-session loop: push data commits every 30 minutes (server runs as SYSTEM without GitHub credential)
cd /d "%~dp0.."
:loop
call scripts\push_data_backup.bat
timeout /t 1800 /nobreak >nul
goto loop

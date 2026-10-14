@echo off
REM [2026-10-11] Server runs as SYSTEM (no GitHub credential) -> push data commits from the user account.
REM Pushes local HEAD to the data-backup branch (force) so no code is pulled and the server is not restarted.
cd /d "%~dp0.."
echo [%date% %time%] push start>> logs\push_data_backup.log
git push origin HEAD:refs/heads/data-backup --force>> logs\push_data_backup.log 2>&1
echo [%date% %time%] rc=%errorlevel%>> logs\push_data_backup.log

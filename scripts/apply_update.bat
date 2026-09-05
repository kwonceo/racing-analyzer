@echo off
REM Apply committed updates to the production folder (git pull).
REM Refuses to run while a race is in the collection window.
setlocal
cd /d "%~dp0.."
python toolspply_update.py %*
echo.
pause
endlocal

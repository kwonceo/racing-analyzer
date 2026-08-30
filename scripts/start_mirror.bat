@echo off
rem [read-only mirror] view live analysis from another PC. app.py is NOT touched.
rem rollback: close this window and delete tools\mirror_server.py
cd /d "%~dp0.."
set PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe
set PYTHONIOENCODING=utf-8
netstat -ano | findstr ":8014" | findstr LISTENING >nul 2>&1
if %errorlevel%==0 goto show
start "" /b "%PY%" tools\mirror_server.py
timeout /t 4 /nobreak >nul
:show
echo.
echo   Open this URL on the other PC  (Tailscale required)
echo   ---------------------------------------------------
for /f "delims=" %%T in (data\_mirror_token.txt) do echo    http://100.80.114.84:8014/m?t=%%T
echo.
echo   NOTE: a few seconds ~ tens of seconds behind the board. For watching only.
echo   NOTE: anyone who knows this URL can view it.
echo.
pause

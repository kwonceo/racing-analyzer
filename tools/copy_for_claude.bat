@echo off
copy /Y "%~dp0..\app.py" "%~dp0_sync_app3.py" >nul
if exist "%~dp0_sync_app3.py" (echo [OK] app.py 복사 완료) else (echo [실패] 복사 안됨)
pause

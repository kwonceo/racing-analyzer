@echo off
cd /d C:\Users\USER\Desktop\경마분석서버\chrome-extension
powershell Compress-Archive -Path * -DestinationPath ..\chrome-extension.zip -Force
echo.
echo [OK] ZIP 재빌드 완료: chrome-extension.zip
pause

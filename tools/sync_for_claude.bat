@echo off
rem [Claude 동기화용] app.py / app.js 를 새 이름으로 복사 → 클라우드 세션이 최신본을 읽을 수 있게 함
cd /d %~dp0..
copy /Y app.py tools\_sync_app.py >nul
copy /Y static\js\app.js tools\_sync_app.js >nul
echo.
echo [OK] 복사 완료: tools\_sync_app.py , tools\_sync_app.js
echo 이제 채팅에서 "복사 완료" 라고 알려주세요.
pause

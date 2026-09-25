@echo off
setlocal
REM ============================================================
REM  보조 프로세스 4개를 띄운다 (8011 서버와 별개 · 없을 때만).
REM   . 8012 tools\forecast_ui.py        전적표 분석 화면
REM   . 8013 tools\review_ui.py          대표 복기 화면(3층)
REM   .      tools\form_forecast.py --daemon   경마 자동 예측(대표 승인 2026-09-25)
REM   .      tools\keirin_forecast.py --daemon 경륜 자동 예측(대표 승인 2026-09-25)
REM  2026-09-25 신설 - 재부팅·다운 뒤 넷 다 꺼진 채 남아 있던 것(9/09 이후 예측 12건뿐).
REM  start_server_only.bat 이 부팅 때 이것을 부른다. 사람이 직접 실행해도 된다.
REM  중복 기동 방지: 포트(8012·8013)는 LISTENING, 데몬은 명령줄로 확인한다.
REM ============================================================
cd /d "%~dp0.."
set "PY=C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=py"
set "LOGDIR=%CD%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$env:PYTHONIOENCODING='utf-8'; $root='%CD%'; $py='%PY%'; $log='%LOGDIR%\autostart.log';" ^
  "$procs = Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | ForEach-Object { $_.CommandLine };" ^
  "$jobs = @(" ^
  "  @{tag='8012'; args='-u tools/forecast_ui.py';           key='forecast_ui.py';   out='forecast_ui'}," ^
  "  @{tag='8013'; args='-u tools/review_ui.py';             key='review_ui.py';     out='review_ui'}," ^
  "  @{tag='form'; args='-u tools/form_forecast.py --daemon'; key='form_forecast.py'; out='form_forecast_daemon'}," ^
  "  @{tag='keirin'; args='-u tools/keirin_forecast.py --daemon'; key='keirin_forecast.py'; out='keirin_forecast_daemon'}" ^
  ");" ^
  "foreach ($j in $jobs) {" ^
  "  $running = $procs | Where-Object { $_ -and $_ -match [regex]::Escape($j.key) };" ^
  "  if ($running) { Add-Content $log ('[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] AUX SKIP ' + $j.tag + ' already running'); continue }" ^
  "  Start-Process -FilePath $py -ArgumentList $j.args -WorkingDirectory $root -RedirectStandardOutput ($root + '\logs\' + $j.out + '.out') -RedirectStandardError ($root + '\logs\' + $j.out + '.err') -WindowStyle Hidden;" ^
  "  Add-Content $log ('[' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + '] AUX START ' + $j.tag);" ^
  "}"
exit /b 0

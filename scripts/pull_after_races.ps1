# [2026-10-11] One-shot: wait until today's races are over, then pull origin/master into the live folder.
#   Condition: local time >= 21:30 AND no data/odds_history file modified in the last 20 minutes.
#   After pull: syntax check of app.py, push local master (user account has GitHub credentials), log result. Exits once.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$log = Join-Path $root 'logs\pull_after_races.log'
function W($m) { Add-Content -Path $log -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding UTF8 }
W 'waiting'
while ($true) {
  $now = Get-Date
  if ($now.Hour -gt 21 -or ($now.Hour -eq 21 -and $now.Minute -ge 30)) {
    $recent = Get-ChildItem -Path (Join-Path $root 'data\odds_history') -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $now.AddMinutes(-20) }
    if (-not $recent) { break }
  }
  Start-Sleep -Seconds 300
}
W 'pull start'
$out = & git pull --no-rebase origin master 2>&1 | Out-String
W $out.Trim()
$syn = & python -c "import ast;ast.parse(open('app.py',encoding='utf-8').read());print('syntax ok')" 2>&1 | Out-String
W $syn.Trim()
Start-Sleep -Seconds 40
try { $h = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 'http://127.0.0.1:8011/api/snapshot/health').StatusCode } catch { $h = 'ERR ' + $_.Exception.Message }
W ("health " + $h)
$p = & git push origin master 2>&1 | Out-String
W $p.Trim()
W 'done'

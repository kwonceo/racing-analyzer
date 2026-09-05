@echo off
REM Install SessionStart hook for CLAUDE.md size warning. Run once per PC.
REM .claude\ is gitignored, so it does not sync via git.
setlocal
cd /d "%~dp0.."
python tools\install_claude_hook.py
endlocal
pause

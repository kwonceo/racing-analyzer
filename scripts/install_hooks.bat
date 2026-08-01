@echo off
REM ==========================================================================
REM  Install commit-gate hooks   (created 2026-08-01)
REM
REM  WHY THIS SCRIPT EXISTS
REM    .git/hooks/ is NOT tracked by git. Committing a hook file does not carry
REM    it to another machine or a fresh clone. So the source of truth lives in
REM    scripts/ and this script copies it into place.
REM    (2026-08-01 incident: no pre-commit hook existed, so the whole commit
REM     gate was decoration - run_precommit.py returned rc=1 and the commit
REM     went through anyway.)
REM
REM  NOTE: comments are ASCII on purpose. Korean text in a .bat breaks cmd
REM        parsing under the default codepage. Korean explanations live in
REM        scripts/pre-commit and scripts/post-commit (shell, UTF-8 safe).
REM
REM  Usage: scripts\install_hooks.bat
REM ==========================================================================
cd /d "%~dp0.."
if not exist ".git\hooks" (
  echo [ERROR] .git\hooks not found. Run from the git repository root.
  exit /b 1
)
copy /Y "scripts\pre-commit"  ".git\hooks\pre-commit"  >nul
copy /Y "scripts\post-commit" ".git\hooks\post-commit" >nul
echo [OK] installed
echo      .git\hooks\pre-commit   ^<- scripts\pre-commit    (blocks bad commits)
echo      .git\hooks\post-commit  ^<- scripts\post-commit   (logs --no-verify bypass)
echo.
echo [VERIFY] Inject a violation on purpose and try to commit.
echo          If it is not blocked, the hook is decoration again.
exit /b 0

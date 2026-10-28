@echo off
setlocal
cd /d "%~dp0"
set "DV_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%DV_PYTHON%" goto run
set "DV_PYTHON=python"
:run
"%DV_PYTHON%" -X utf8 "%~dp0scripts\share_team.py"
pause

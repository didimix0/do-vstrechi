@echo off
setlocal
set "DV_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%DV_PYTHON%" goto run
set "DV_PYTHON=python"
:run
"%DV_PYTHON%" "%~dp0run.py" --menu
pause

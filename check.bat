@echo off
setlocal
title Hitless recorder - check
cd /d "%~dp0"

set "PY="
py -3 -c "print(1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :got_python
python -c "print(1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :got_python
echo(
echo   Python is missing. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
echo(
pause
exit /b 1

:got_python
REM Check for updates first. cmd reads this whole block before running it,
REM so the update may safely replace this very file; the fresh copy then
REM starts again from the top with --updated and skips this step.
if not "%~1"=="--updated" (
  %PY% "%~dp0update.py"
  call "%~f0" --updated %*
  exit /b
)
set "ARGS=%*"
set "ARGS=%ARGS:--updated=%"

%PY% -m hitless check %ARGS%
pause

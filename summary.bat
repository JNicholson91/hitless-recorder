@echo off
setlocal
title Hitless recorder - summary
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

%PY% -m hitless summary %*
pause

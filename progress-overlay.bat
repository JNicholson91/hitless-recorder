@echo off
setlocal EnableDelayedExpansion
title Hitless progress overlay
cd /d "%~dp0"

REM Nothing to keep running: the overlay page talks to AutoHitCounter itself.
REM This just works out the link for OBS, copies it, and opens a preview.

set "PAGE=%~dp0overlay\progress.html"
if not exist "%PAGE%" goto :missing
set "URL=file:///%PAGE:\=/%"
set "URL=!URL: =%%20!"
echo !URL!| clip

echo(
echo   Your overlay link (already copied - just paste it):
echo(
echo     !URL!
echo(
echo   In OBS: + under Sources - Browser - untick "Local file" -
echo   paste the link into URL - width 600, height 110 - OK.
echo(
echo   It fills in by itself whenever AutoHitCounter is open.
echo   Settings go on the end of the link, e.g.  ?scale=1.3  or  ?igt=0
echo   (SETUP.md lists them all).
echo(
start "" "!URL!"
pause
exit /b 0

:missing
echo   The overlay file is missing. Run record.bat once to update, then try again.
pause
exit /b 1

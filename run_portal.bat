@echo off
cd /d "%~dp0"
title Business Scrying Portal
where py >nul 2>nul
if %errorlevel%==0 (py -3 business_scrying_portal.py %* & goto done)
where python >nul 2>nul
if %errorlevel%==0 (python business_scrying_portal.py %* & goto done)
echo Python 3.9 or newer was not found.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.
:done
pause

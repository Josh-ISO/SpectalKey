@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0launch.ps1"
pause

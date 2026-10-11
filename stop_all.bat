@echo off
echo Stopping all cgrep services...
powershell -ExecutionPolicy Bypass -File "c:\projects\CGrep-v3\stop_all.ps1"
pause

@echo off
setlocal DisableDelayedExpansion
echo Starting Keshi with the desktop window preferred.
"%~dp0Keshi.exe" --desktop
if errorlevel 1 pause

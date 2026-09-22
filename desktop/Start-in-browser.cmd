@echo off
setlocal DisableDelayedExpansion
echo Starting Keshi in the local browser. Keep the service control window open.
"%~dp0Keshi.exe" --browser
if errorlevel 1 pause

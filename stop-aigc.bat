@echo off
setlocal
title AIGC Studio Shutdown

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-studio.ps1" %*
set "STOP_EXIT_CODE=%ERRORLEVEL%"

if not "%STOP_EXIT_CODE%"=="0" (
    echo.
    echo AIGC Studio could not be stopped. Review the message above.
    pause
)

exit /b %STOP_EXIT_CODE%

@echo off
setlocal
title AIGC Studio Launcher

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-studio.ps1" %*
set "START_EXIT_CODE=%ERRORLEVEL%"

if not "%START_EXIT_CODE%"=="0" (
    echo.
    echo AIGC Studio failed to start. Review the message above.
    pause
)

exit /b %START_EXIT_CODE%

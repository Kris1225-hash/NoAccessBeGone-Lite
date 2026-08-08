@echo off
REM nab-peer setup — Windows
REM Sets up the peer with a donated account token and registers autostart via Task Scheduler.
setlocal enabledelayedexpansion

set "DIR=%~dp0"
set "CONF=%USERPROFILE%\.nab\peer.env"
set "SHARE_KEY=a436975c7eb45eadac09659e4dce92f9f2207c8be40bfadc"

echo == nab peer setup ==
echo This machine will run a scanning peer for the nab name database.
echo It needs a Discord account token (use a dedicated alt, not your main).
echo.
set /p TOKEN=Discord token: 
if "%TOKEN%"=="" (
    echo no token given, aborting
    exit /b 1
)

if not exist "%USERPROFILE%\.nab" mkdir "%USERPROFILE%\.nab"
(
    echo # nab peer config
    echo TOKEN=%TOKEN%
    echo SHARE_KEY=%SHARE_KEY%
    echo SCAN=1
    echo DAILY_CAP=100
    echo JOIN_INTERVAL=60
) > "%CONF%"
echo config written to %CONF%

schtasks /create /tn "nab-peer" /tr "\"%DIR%nab_peer.exe\"" /sc onlogon /rl limited /f >nul
echo autostart registered (Task Scheduler: nab-peer)

echo.
echo watch it live at:  http://localhost:8092
echo done. start it now with:  "%DIR%nab_peer.exe"
echo stop/disable anytime with:  schtasks /delete /tn "nab-peer" /f
endlocal

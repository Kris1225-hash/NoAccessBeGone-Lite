@echo off
REM NoAccessBeGoneLite installer for Windows
setlocal

set "PLUGIN_NAME=noAccessBeGoneLite"
set "REPO_DIR=%~dp0"
if "%VENCORD_DIR%"=="" set "VENCORD_DIR=%USERPROFILE%\Vencord"

where pnpm >nul 2>nul
if errorlevel 1 (
    echo [NoAccessBeGoneLite] pnpm not found. Install it first:
    echo     npm install -g pnpm
    exit /b 1
)

if not exist "%VENCORD_DIR%" (
    echo [NoAccessBeGoneLite] cloning Vencord into %VENCORD_DIR% ...
    git clone --depth 1 https://github.com/Vendicated/Vencord.git "%VENCORD_DIR%"
)

echo [NoAccessBeGoneLite] copying plugin ...
if exist "%VENCORD_DIR%\src\plugins\%PLUGIN_NAME%" rmdir /s /q "%VENCORD_DIR%\src\plugins\%PLUGIN_NAME%"
mkdir "%VENCORD_DIR%\src\plugins\%PLUGIN_NAME%\components"
copy /y "%REPO_DIR%plugin-lite\index.tsx" "%VENCORD_DIR%\src\plugins\%PLUGIN_NAME%\index.tsx" >nul
copy /y "%REPO_DIR%plugin-lite\style.css" "%VENCORD_DIR%\src\plugins\%PLUGIN_NAME%\style.css" >nul
copy /y "%REPO_DIR%plugin-lite\components\LockScreen.tsx" "%VENCORD_DIR%\src\plugins\%PLUGIN_NAME%\components\LockScreen.tsx" >nul

echo [NoAccessBeGoneLite] patching Vencord CSP allowlist (nab.enby.fish) ...
findstr /c:"nab.enby.fish" "%VENCORD_DIR%\src\main\csp\index.ts" >nul 2>nul
if errorlevel 1 (
    powershell -NoProfile -Command "(Get-Content '%VENCORD_DIR%\src\main\csp\index.ts' -Raw) -replace [regex]::Escape('\"icons.duckduckgo.com\": ImageSrc, // DuckDuckGo Favicon API (Reverse Image Search)'), ('\"icons.duckduckgo.com\": ImageSrc, // DuckDuckGo Favicon API (Reverse Image Search)' + [char]10 + '    \"nab.enby.fish\": ConnectSrc, // NoAccessBeGone name database') | Set-Content '%VENCORD_DIR%\src\main\csp\index.ts' -NoNewline"
)

cd /d "%VENCORD_DIR%"
call pnpm install --no-frozen-lockfile
call pnpm build

echo.
echo [NoAccessBeGoneLite] done! Load it via pnpm inject (Discord) or the Vesktop
echo Vencord Location setting, then enable NoAccessBeGoneLite in Vencord settings.

endlocal

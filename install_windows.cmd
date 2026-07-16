@echo off
setlocal enabledelayedexpansion

rem Installs the Kitsu Krita Plugin into Krita's pykrita resource folder.
rem
rem Usage: install_windows.cmd [custom pykrita folder]
rem   Run with no arguments to install to the standard location
rem   (%%APPDATA%%\krita\pykrita). If your Krita install uses a different
rem   resource folder (e.g. a portable install), find it via Krita's
rem   Settings > Manage Resources > Open Resource Folder, then pass its
rem   "pykrita" subfolder as an argument to this script.

set "SCRIPT_DIR=%~dp0"
set "SOURCE=%SCRIPT_DIR%pykrita"

if "%~1"=="" (
    set "TARGET=%APPDATA%\krita\pykrita"
) else (
    set "TARGET=%~1"
)

echo Installing Kitsu Krita Plugin
echo   From: %SOURCE%
echo   To:   %TARGET%
echo.

if not exist "%SOURCE%\kitsu_krita_plugin.desktop" (
    echo ERROR: %SOURCE%\kitsu_krita_plugin.desktop not found.
    echo Run this script from the repository root, or check your checkout.
    exit /b 1
)

if not exist "%TARGET%" (
    mkdir "%TARGET%"
    if errorlevel 1 (
        echo ERROR: Could not create %TARGET%
        exit /b 1
    )
)

rem Remove any previous install of this plugin's own folder first, for a
rem clean, idempotent install that never leaves stale files from an older
rem version behind.
if exist "%TARGET%\kitsu_krita_plugin" (
    rmdir /s /q "%TARGET%\kitsu_krita_plugin"
)

copy /y "%SOURCE%\kitsu_krita_plugin.desktop" "%TARGET%\kitsu_krita_plugin.desktop" >nul
if errorlevel 1 (
    echo ERROR: Could not copy the .desktop file.
    exit /b 1
)

robocopy "%SOURCE%\kitsu_krita_plugin" "%TARGET%\kitsu_krita_plugin" /E /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
    echo ERROR: Could not copy the plugin folder.
    exit /b 1
)

echo.
echo Done. Restart Krita, then enable "Kitsu Krita Plugin" in
echo Settings ^> Configure Krita ^> Python Plugin Manager.
pause
endlocal

@echo off
setlocal enabledelayedexpansion
title Zen Mouse Jiggler - Uninstall

rem Safety: only run from inside the Zen Mouse Jiggler app folder.
if not exist "%~dp0ZenMouseJiggler.exe" (
    echo This uninstaller must be run from the Zen Mouse Jiggler folder
    echo (the folder that contains ZenMouseJiggler.exe^).
    echo.
    pause
    exit /b 1
)

echo ============================================
echo    Zen Mouse Jiggler - Uninstaller
echo ============================================
echo.
echo This will:
echo    * Close Zen Mouse Jiggler if it is running
echo    * Remove the "Launch at Windows startup" entry
echo    * Delete saved settings in:
echo        %LOCALAPPDATA%\ZenMouseJiggler
echo    * Delete this application folder:
echo        %~dp0
echo.

choice /c YN /n /m "Are you sure you want to uninstall? [Y/N] "
if errorlevel 2 goto cancel

echo.
echo Closing the app if it is running...
taskkill /IM ZenMouseJiggler.exe /F >nul 2>&1

echo Removing the startup entry...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ZenMouseJiggler" /f >nul 2>&1

echo Removing saved settings...
if exist "%LOCALAPPDATA%\ZenMouseJiggler" rmdir /s /q "%LOCALAPPDATA%\ZenMouseJiggler"

rem Strip the trailing backslash from this folder's path.
set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"

rem A running batch cannot delete its own folder, so write a small cleaner to
rem %TEMP% that waits for us to exit, removes the folder (retrying a few times
rem in case the exe is still releasing files), then deletes itself.
set "CLEAN=%TEMP%\zmj_uninstall_%RANDOM%.bat"
>"%CLEAN%" echo @echo off
>>"%CLEAN%" echo cd /d "%TEMP%"
>>"%CLEAN%" echo set /a n=0
>>"%CLEAN%" echo :retry
>>"%CLEAN%" echo timeout /t 1 /nobreak ^>nul
>>"%CLEAN%" echo rmdir /s /q "%APPDIR%" 2^>nul
>>"%CLEAN%" echo if not exist "%APPDIR%" goto done
>>"%CLEAN%" echo set /a n+=1
>>"%CLEAN%" echo if %%n%% lss 5 goto retry
>>"%CLEAN%" echo :done
>>"%CLEAN%" echo del "%%~f0"

echo Removing the application folder...
start "" /min "%CLEAN%"

echo.
echo Uninstall complete. This window will close.
ping -n 3 127.0.0.1 >nul 2>&1
exit /b 0

:cancel
echo.
echo Uninstall cancelled. Nothing was changed.
echo.
pause
exit /b 0

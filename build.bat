@echo off
REM Rebuilds CookieRunAutoMenuBot.exe from the current source.
REM Run this any time cookierun_bot.py or cookierun_gui.py changes.
REM config.json / templates/ / debug_shots/ are never touched by this --
REM they live next to the exe and are picked up at runtime, not baked in.

cd /d "%~dp0"

py -m PyInstaller --onefile --windowed --name CookieRunAutoMenuBot ^
    --distpath . --workpath build --specpath build ^
    cookierun_gui.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED - see errors above.
    pause
    exit /b 1
)

py make_release_zip.py

echo.
echo Build OK: CookieRunAutoMenuBot.exe updated in this folder.
echo A CookieRunAutoMenuBot-vX.Y.Z.zip was also created -- that's what
echo you attach to the GitHub release (see RELEASING.md).
pause

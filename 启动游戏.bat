@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PYEXE=%~dp0neon_protocol\venv\Scripts\python.exe

if not exist "%PYEXE%" (
    echo [ERROR] Python venv not found:
    echo   %PYEXE%
    echo Please create the venv in neon_protocol\venv first.
    pause
    exit /b 1
)

echo ============================================================
echo  NEON PROTOCOL
echo ============================================================
echo  WASD move ^| Mouse aim ^| Left click fire ^| Shift dash
echo  1/2/3 weapon ^| Tab map ^| ESC pause
echo ============================================================
echo.

"%PYEXE%" -m neon_protocol.main --scale 1

echo.
echo [EXIT] Game closed.
pause

@echo off
cd /d "%~dp0"

set "PY="
call :try "C:\Python314\pythonw.exe"
call :try "C:\Python313\pythonw.exe"
call :try "C:\Python312\pythonw.exe"
call :try "C:\Python311\pythonw.exe"
call :try "C:\Python310\pythonw.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
call :try "%ProgramFiles%\Python314\pythonw.exe"
call :try "%ProgramFiles%\Python313\pythonw.exe"
if not defined PY (
  for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do call :try "%%i"
)
if not defined PY (
  for /f "delims=" %%i in ('where python.exe 2^>nul') do call :try "%%i"
)

if not defined PY (
  echo.
  echo [ERROR] pythonw.exe / python.exe not found.
  echo Install Python 3.8+ from https://www.python.org/downloads/ and retry.
  echo.
  pause
  exit /b 1
)

start "" "%PY%" "%~dp0src\pointer_zoom.py" --config "%~dp0pointer-zoom.ini"

echo Pointer Zoom (no GUI) started in the background.
echo   Ctrl+Alt+P  toggle on / off
echo   Ctrl+Alt+R  reset pointer to the original size
echo   Ctrl+Alt+Q  quit
echo.
echo (hotkeys are configurable in pointer-zoom.ini)
timeout /t 6 >nul
exit /b 0

:try
if defined PY exit /b 0
if not exist "%~1" exit /b 0
set "PY=%~1"
exit /b 0

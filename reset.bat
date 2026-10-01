@echo off
cd /d "%~dp0"

set "PY="
call :try "C:\Python314\python.exe"
call :try "C:\Python313\python.exe"
call :try "C:\Python312\python.exe"
call :try "C:\Python311\python.exe"
call :try "C:\Python310\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try "%ProgramFiles%\Python314\python.exe"
call :try "%ProgramFiles%\Python313\python.exe"
if not defined PY (
  for /f "delims=" %%i in ('where python.exe 2^>nul') do call :try "%%i"
)

if not defined PY (
  echo.
  echo [ERROR] python.exe not found.
  echo Instead: open Settings - Accessibility - Mouse pointer and touch
  echo and drag the size slider back to 1.
  echo.
  pause
  exit /b 1
)

"%PY%" "%~dp0src\pointer_zoom.py" --reset
echo.
echo Done. If the pointer is still oversized, open
echo Settings - Accessibility - Mouse pointer and touch - Size and set it to 1.
pause
exit /b 0

:try
if defined PY exit /b 0
if not exist "%~1" exit /b 0
set "PY=%~1"
exit /b 0
